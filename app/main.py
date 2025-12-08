# app/main.py
from __future__ import annotations

import os
import sys
import traceback
import asyncio
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel
from tenacity import RetryError
from app.teams_graph import diag_bot_token
from app.ai.triage_agent import triage_next
from app.schemas import (
    KBArticle,
    KBArticleCreate,
    KBArticleMetadata,
    KBArticleUpdate,
)
from app.kb_admin import (
    KBArticleAlreadyExistsError,
    KBArticleNotFoundError,
    create_kb_article,
    force_reindex,
    get_kb_article,
    list_kb_articles,
    update_kb_article,
)

# --- Windows: usar o event loop compatível (evita warnings/erros no BotBuilder)
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
    except Exception:
        pass

# ---- Módulos do projeto
from app.db import (
    init_db,
    upsert_ticket,
    get_ticket_rec,
    schedule_proactive_flow,
    fetch_due_followups,
    mark_followup_sent,
    cancel_followups,
    connect,
    mark_teams_notified,
    log_ingest_event,
    INGEST_SOURCE_MOVIDESK_WEBHOOK,
    INGEST_ACTION_PAYLOAD_RECEIVED,
    INGEST_ACTION_FETCH_TICKET,
    INGEST_ACTION_CLASSIFY_TICKET_LLM,
    INGEST_ACTION_CLASSIFY_TICKET_FALLBACK,
    INGEST_ACTION_UPSERT_TICKET,
    INGEST_ACTION_SCHEDULE_FOLLOWUP,
    INGEST_ACTION_NOTIFY_TEAMS,
    INGEST_ACTION_SKIP_FLOW,
    get_ingest_metrics,
    get_followup_metrics,
    get_recent_tickets,
    set_user_current_ticket,
    INGEST_SOURCE_TEAMS_BOT,
    INGEST_ACTION_PROACTIVE_FOLLOWUP,
    update_session_on_bot_message,
    close_session,
    get_sessions_for_reminder,
    get_sessions_for_timeout,
    SESSION_REMINDER_MINUTES,
    SESSION_TIMEOUT_MINUTES,
)

from app.movidesk_client import (
    MovideskError,
    get_ticket_by_id,
    get_ticket_text_bundle,
    get_latest_ticket_for_email_account_multi,
    sample_email_channel,
)

# imports de auditoria (opcionais – não derrubam o app se ausentes)
try:
    from app.movidesk_client import list_actions, list_notes  # type: ignore
except Exception:
    list_actions = None  # type: ignore
    list_notes = None  # type: ignore

from app.classifier import classify_from_subject
from app.llm import classify_ticket_with_llm
from app.teams_graph import (
    TeamsGraphError,
    notify_user_for_ticket,
    diag_token_info,
    diag_resolve_app,
    diag_user,
    diag_user_installed_apps,
)
from app import kb
from app.learning import get_feedback_metrics

# lazy import: função opcional; evita quebrar a inicialização se o módulo estiver parcial
try:
    from app.movidesk_client import add_public_note as _add_public_note  # type: ignore
except Exception:
    _add_public_note = None  # type: ignore

# importa de forma robusta (pacote absoluto)
try:
    from app.summarizer import summarize_conversation
except Exception:
    # fallback simples para não quebrar o endpoint se o módulo faltar
    def summarize_conversation(text: str) -> str:
        text = text or ""
        return (text[:800] + "…") if len(text) > 800 else text


load_dotenv()

# -----------------------------------------------------------------------------#
# App + Logs
# -----------------------------------------------------------------------------#
app = FastAPI(title="Assistente N1 - Tecnogera")
logger.remove()
logger.add("app.log", rotation="10 MB", retention=5, enqueue=True)

# Arquivos estáticos (front simples para testes)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# -----------------------------------------------------------------------------#
# ENV
# -----------------------------------------------------------------------------#
WEBHOOK_SHARED_SECRET = os.getenv("WEBHOOK_SHARED_SECRET", "")
ALLOW_EMAIL_TO_RAW = os.getenv("ALLOW_EMAIL_TO", "suporte@tecnogera.com.br")
ALLOW_EMAIL_TO_LIST = [s.strip() for s in ALLOW_EMAIL_TO_RAW.split(",") if s.strip()]
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Bot / Teams
MS_TENANT_ID = os.getenv("MS_TENANT_ID", "")
BOT_APP_ID = os.getenv("MS_CLIENT_ID", "")
BOT_APP_PASSWORD = os.getenv("MS_CLIENT_SECRET", "")
ENABLE_TEAMS_BOT = os.getenv("ENABLE_TEAMS_BOT", "1").lower() in ("1", "true", "yes", "on")
SESSION_REMINDER_MESSAGE = os.getenv("SESSION_REMINDER_MESSAGE", "").strip()
ENABLE_SESSION_WATCHDOG = os.getenv("ENABLE_SESSION_WATCHDOG", "1").lower() in ("1", "true", "yes", "on")
SESSION_WATCHDOG_POLL_SECONDS = int(os.getenv("SESSION_WATCHDOG_POLL_SECONDS", "60"))

# DB
init_db()

# -----------------------------------------------------------------------------#
# Util
# -----------------------------------------------------------------------------#
ORIGIN_MAP = {
    1: "Web (cliente)",
    2: "Web (agente)",
    3: "Recebido por e-mail",
    4: "Gatilho do sistema",
    5: "Chat (online)",
    7: "E-mail enviado pelo sistema",
    8: "Outro canal",
    9: "Web API",
}

_NOTIFIED_TICKETS = set()  # tickets já notificados (somente em memória)


def _log_ingest(action: str, status: str, ticket_id: int | None = None, error_message: str | None = None, context: dict | None = None):
    """
    Telemetria leve para monitorar ingestões Movidesk.
    Falhas ao gravar não podem derrubar o fluxo principal.
    """
    try:
        log_ingest_event(
            source=INGEST_SOURCE_MOVIDESK_WEBHOOK,
            action=action,
            status=status,
            ticket_id=str(ticket_id) if ticket_id is not None else None,
            error_message=error_message,
            context=context,
        )
    except Exception as exc:  # pragma: no cover - telemetria não deve falhar testes
        logger.warning(f"[telemetry] falha ao registrar {action}: {exc}")


def _log_bot(action: str, status: str, ticket_id: int | None = None, error_message: str | None = None, context: dict | None = None):
    try:
        log_ingest_event(
            source=INGEST_SOURCE_TEAMS_BOT,
            action=action,
            status=status,
            ticket_id=str(ticket_id) if ticket_id is not None else None,
            error_message=error_message,
            context=context,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning(f"[bot-telemetry] falha ao registrar {action}: {exc}")


def _format_followup_message(ticket_id: int, subject: str, step_message: str) -> str:
    subject_line = subject or f"Ticket #{ticket_id}"
    base = (
        f"Ticket #{ticket_id} • {subject_line}\n"
        f"{step_message}\n\n"
        "Se já resolveu, responda **Sim**.\n"
        "Se ainda precisa de ajuda, responda **Não** com o que aconteceu.\n"
        "Para ver todos os seus chamados, envie `listar`."
    )
    return base

def extract_core_fields(ticket_id: int) -> dict:
    """
    Extrai e padroniza os campos essenciais que o sistema usa:
    - requester_email (solicitante)
    - ticket_id
    - origin_email_account (e-mail de recebimento, ex.: suporte@tecnogera.com.br)
    - subject
    - first_action_text (descrição inicial do problema)
    """
    # Busca o ticket completo na API Movidesk
    ticket = get_ticket_by_id(ticket_id)  # pode levantar MovideskError/RetryError

    # Campos base
    origin_email_account = ticket.get("originEmailAccount") or ""
    subject = ticket.get("subject") or ""

    # Solicitante: dono/clients do ticket
    requester_email = _pick_requester_email(ticket) or ""

    # Primeira ação (descrição do usuário)
    try:
        bundle = get_ticket_text_bundle(ticket_id)  # {subject, first_action_text, first_action_html}
    except Exception:
        bundle = {"subject": subject, "first_action_text": "", "first_action_html": ""}

    first_action_text = (bundle.get("first_action_text") or "").strip()

    return {
        "ticket_id": int(ticket_id),
        "requester_email": requester_email,
        "origin_email_account": origin_email_account,
        "subject": subject,
        "first_action_text": first_action_text,
    }


def _is_email_channel(ticket: dict) -> bool:
    try:
        return int(ticket.get("origin", 0)) == 3
    except Exception:
        return False

def _email_to_matches(ticket: dict) -> bool:
    to_acc = (ticket.get("originEmailAccount") or "").lower().strip()
    for allowed in ALLOW_EMAIL_TO_LIST:
        a = allowed.lower().strip()
        if not a:
            continue
        if to_acc == a or a in to_acc:
            return True
    return False

def _pick_requester_email(ticket: dict) -> str:
    owner = ticket.get("owner") or {}
    cand = (owner.get("email") or owner.get("businessEmail") or "").strip()
    if cand:
        return cand
    for c in (ticket.get("clients") or []):
        e = (c.get("email") or c.get("businessEmail") or "").strip()
        if e:
            return e
    return ""

def _get_first(obj, *names, default=None):
    """Lê um atributo/chave com nomes alternativos, em BaseModel, dict ou objeto comum."""
    for n in names:
        # Pydantic v2: model_fields / model_dump
        if hasattr(obj, n):
            try:
                v = getattr(obj, n)
                if v is not None:
                    return v
            except Exception:
                pass
        if isinstance(obj, dict) and n in obj and obj[n] is not None:
            return obj[n]
    return default

def _to_dict_safe(obj):
    """Serializa qq objeto para dict (p/ logging/resposta)."""
    try:
        # Pydantic v2
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
    except Exception:
        pass
    try:
        # Pydantic v1
        if hasattr(obj, "dict"):
            return obj.dict()
    except Exception:
        pass
    if isinstance(obj, dict):
        return obj
    try:
        return {k: v for k, v in vars(obj).items() if not callable(v) and not k.startswith("_")}
    except Exception:
        return {"_repr": repr(obj)}


class ChatMessage(BaseModel):
    """Mensagem de chat simples para testar o agente via HTTP."""

    role: str  # "user" ou "assistant"
    text: str


class ChatTicket(BaseModel):
    """Contexto mínimo de ticket para simulação de atendimento."""

    subject: str = ""
    description: str = ""


class ChatRequest(BaseModel):
    """Payload do front-end de testes de chat."""

    ticket: ChatTicket
    history: list[ChatMessage]
    mode: str | None = "ticket"


class ChatResponse(BaseModel):
    reply: str
    action: str | None = None
    confidence: float | None = None
    intent: str | None = None


@app.get("/debug/bot/token")
def debug_bot_token():
    return diag_bot_token()

@app.get("/healthz")
def healthz():
    return {"ok": True, "file": __file__}

@app.get("/debug/routes")
def _debug_routes():
    return [getattr(r, "path", str(r)) for r in app.router.routes]


@app.get("/debug/metrics")
def debug_metrics():
    """
    Endpoint de observabilidade: consolida ingest_events, followups, tickets e feedback da KB.
    Apoiado pela telemetria criada em ingest_events e pelos helpers de banco/learning.py.
    """
    payload = {"generated_at": datetime.now(timezone.utc).isoformat()}
    errors: list[str] = []

    try:
        payload["ingest"] = get_ingest_metrics()
    except Exception as e:
        logger.exception("[metrics] falha ao ler ingest_metrics: {}", e)
        errors.append(f"ingest:{e}")

    try:
        payload["followups"] = get_followup_metrics()
    except Exception as e:
        logger.exception("[metrics] falha ao ler followups: {}", e)
        errors.append(f"followups:{e}")

    try:
        payload["tickets"] = {"recent": get_recent_tickets()}
    except Exception as e:
        logger.exception("[metrics] falha ao listar tickets recentes: {}", e)
        errors.append(f"tickets:{e}")

    try:
        payload["feedback"] = get_feedback_metrics()
    except Exception as e:
        logger.exception("[metrics] falha ao ler feedback da KB: {}", e)
        errors.append(f"feedback:{e}")

    if errors:
        payload["errors"] = errors
    return payload


@app.get("/debug/kb/articles", response_model=list[KBArticleMetadata])
def debug_kb_list_articles():
    """
    Lista artigos disponíveis na base de conhecimento.
    Uso administrativo apenas (sandbox/debug).
    """
    return list_kb_articles()


@app.get("/debug/kb/articles/{slug}", response_model=KBArticle)
def debug_kb_get_article(slug: str):
    try:
        return get_kb_article(slug)
    except KBArticleNotFoundError as exc:  # pragma: no cover - FastAPI converte em 404
        raise HTTPException(status_code=404, detail=f"Artigo '{slug}' não encontrado") from exc


@app.post("/debug/kb/articles", response_model=KBArticle, status_code=201)
def debug_kb_create_article(body: KBArticleCreate):
    try:
        article = create_kb_article(body)
    except KBArticleAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=f"Já existe artigo com slug '{body.slug}'") from exc
    stats = force_reindex()
    logger.info("[KB] artigo {} criado e índice atualizado: {}", body.slug, stats)
    return article


@app.put("/debug/kb/articles/{slug}", response_model=KBArticle)
def debug_kb_update_article(slug: str, body: KBArticleUpdate):
    try:
        article = update_kb_article(slug, body)
    except KBArticleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Artigo '{slug}' não encontrado") from exc
    stats = force_reindex()
    logger.info("[KB] artigo {} atualizado e índice reconstruído: {}", slug, stats)
    return article


@app.post("/debug/kb/reindex")
def debug_kb_reindex():
    """
    Força a reindexação da base de conhecimento (BM25 + priors).
    Rotas /debug/kb/* são administrativas e não devem ser expostas ao usuário final.
    """
    stats = force_reindex()
    return {"status": "ok", "stats": stats, "generated_at": datetime.now(timezone.utc).isoformat()}


@app.post("/debug/chat/triage", response_model=ChatResponse)
def debug_chat_triage(body: ChatRequest):
    """
    Endpoint simples para testar o agente N1 via HTTP.
    Recebe hist��rico de mensagens + contexto (assunto/descri��ǜo)
    e retorna a pr��xima resposta sugerida pelo triage_agent.
    """
    mode = (body.mode or "ticket").lower()
    subject = body.ticket.subject or ""
    description = body.ticket.description or ""
    history = [{"role": m.role, "text": m.text} for m in body.history]

    if mode == "chat":
        if not subject:
            subject = "[CHAT DIRETO] Sandbox Assistente N1"
        if not description:
            last_user_msg = next(
                (m["text"] for m in reversed(history) if m.get("role") == "user" and m.get("text")),
                "",
            )
            description = last_user_msg or "Conversa direta iniciada no sandbox (sem ticket Movidesk)."

    ticket = {
        "id": 0,
        "subject": subject,
        "first_action_text": description,
        "description": description,
    }

    out = triage_next(history, ticket)

    reply = out.get("message") or "Certo! Vou te guiar. Em qual tela/op��ǜo voc�� est�� agora?"
    checklist = out.get("checklist") or []
    if checklist:
        reply += "\n\n" + "\n".join(f"- {p}" for p in checklist)

    return ChatResponse(
        reply=reply,
        action=out.get("action"),
        confidence=float(out.get("confidence") or 0.0) if out.get("confidence") is not None else None,
        intent=out.get("intent"),
    )

@app.get("/debug/bot-info")
def debug_bot_info():
    return {
        "ENABLE_TEAMS_BOT": os.getenv("ENABLE_TEAMS_BOT"),
        "MS_CLIENT_ID_present": bool(BOT_APP_ID),
        "MS_CLIENT_SECRET_present": bool(BOT_APP_PASSWORD),
        "MS_TENANT_ID_present": bool(MS_TENANT_ID),
        "BOT_LOADED": '_bot_loaded_ok' in globals() and bool(_bot_loaded_ok),
    }

# -----------------------------------------------------------------------------#
# FOLLOW-UPS
# -----------------------------------------------------------------------------#
def _followups_already_scheduled(ticket_id: int) -> bool:
    try:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(1) FROM ticket_followups WHERE ticket_id=? AND state='pending';",
                (ticket_id,),
            )
            row = cur.fetchone()
            return bool(row and row[0] and int(row[0]) > 0)
    except Exception:
        return False

def _process_followups_once() -> int:
    """
    Worker responsável por consumir ticket_followups e enviar mensagens proativas no Teams.
    Quando um lembrete é disparado, a função inclui informações do ticket e atualiza o contexto
    multi-ticket do usuário (via set_user_current_ticket) para que respostas subsequentes "Sim/Não"
    retomem automaticamente o mesmo chamado.
    """
    due = fetch_due_followups(limit=50)
    sent = 0
    for fu in due:
        ok = False
        try:
            subject_line = fu.get("subject") or f"Ticket #{fu['ticket_id']}"
            message = _format_followup_message(int(fu["ticket_id"]), subject_line, fu["message"])
            graph_user_id = notify_user_for_ticket(
                fu["requester_email"],
                int(fu["ticket_id"]),
                subject_line,
                preview_text=message,
            )
            if graph_user_id:
                set_user_current_ticket(fu["requester_email"], int(fu["ticket_id"]), teams_user_id=graph_user_id)
            ok = True
            _log_bot(
                INGEST_ACTION_PROACTIVE_FOLLOWUP,
                "success",
                ticket_id=int(fu["ticket_id"]),
                context={"step": fu.get("step")},
            )
        except TeamsGraphError as e:
            logger.warning(f"[followups] Graph falhou para {fu['requester_email']}: {e}")
            _log_bot(
                INGEST_ACTION_PROACTIVE_FOLLOWUP,
                "error",
                ticket_id=int(fu["ticket_id"]),
                error_message=str(e),
                context={"step": fu.get("step")},
            )
        except Exception as e:
            logger.warning(f"[followups] erro ao enviar para {fu['requester_email']}: {e}")
            _log_bot(
                INGEST_ACTION_PROACTIVE_FOLLOWUP,
                "error",
                ticket_id=int(fu["ticket_id"]),
                error_message=str(e),
                context={"step": fu.get("step")},
            )
        if ok:
            try:
                mark_followup_sent(fu["id"])
                sent += 1
            except Exception as e:
                logger.warning(f"[followups] falha ao marcar enviado id={fu['id']}: {e}")
            # registra "rastro" no ticket se a função existir
            if callable(_add_public_note):
                try:
                    _add_public_note(int(fu["ticket_id"]), f"[N1 Bot] {fu['message']}")
                except Exception as e:
                    logger.warning(f"[followups] nota pública falhou no ticket {fu['ticket_id']}: {e}")
            else:
                logger.warning(f"[followups] add_public_note indisponível; ticket {fu['ticket_id']} ficou sem anotação.")
    return sent

@app.post("/cron/followups/run")
def run_followups_now():
    sent = _process_followups_once()
    return {"ok": True, "sent": sent}


def _build_session_reminder_text() -> str:
    if SESSION_REMINDER_MESSAGE:
        return SESSION_REMINDER_MESSAGE
    minutes_left = max(SESSION_TIMEOUT_MINUTES - SESSION_REMINDER_MINUTES, 1)
    if minutes_left == 1:
        window = "no pr?ximo minuto"
    else:
        window = f"nos pr?ximos {minutes_left} minutos"
    return (
        "Oi! Continuo com nossa conversa aberta. "
        f"Se n?o receber retorno {window}, vou encerrar automaticamente. "
        "Se ainda precisar de algo ? s? me mandar uma mensagem por aqui."
    )



def _send_chat_session_reminder(session: Dict[str, Any]) -> bool:
    email = (session.get("user_email") or "").strip()
    if not email:
        logger.warning(f"[sessions] sess?o {session.get('id')} sem user_email para lembrete.")
        return False
    try:
        notify_user_for_ticket(email, int(session.get("ticket_id") or 0), "Chat Assistente N1", preview_text=_build_session_reminder_text())
        update_session_on_bot_message(int(session["id"]))
        return True
    except TeamsGraphError as exc:
        logger.warning(f"[sessions] falha ao enviar lembrete da sess?o {session.get('id')}: {exc}")
        return False



_SESSION_TIMEOUT_HANDLER = None



def _trigger_session_timeout(session: Dict[str, Any]) -> None:
    global _SESSION_TIMEOUT_HANDLER
    if _SESSION_TIMEOUT_HANDLER is None:
        try:
            from app.bot import handle_session_timeout  # type: ignore
        except Exception as exc:
            logger.warning(f"[sessions] n?o consegui carregar handle_session_timeout: {exc}")
            _SESSION_TIMEOUT_HANDLER = False
        else:
            _SESSION_TIMEOUT_HANDLER = handle_session_timeout
    if callable(_SESSION_TIMEOUT_HANDLER):
        _SESSION_TIMEOUT_HANDLER(session)
    else:
        session_id = session.get("id")
        if session_id:
            try:
                close_session(int(session_id), "encerrada_timeout")
            except Exception as exc:
                logger.warning(f"[sessions] falha ao encerrar sess?o {session_id} no fallback: {exc}")



def _process_session_watchdog_once() -> Dict[str, int]:
    stats = {"reminders": 0, "timeouts": 0}
    try:
        reminders = get_sessions_for_reminder()
    except Exception as exc:
        logger.warning(f"[sessions] get_sessions_for_reminder falhou: {exc}")
        reminders = []
    try:
        timeouts = get_sessions_for_timeout()
    except Exception as exc:
        logger.warning(f"[sessions] get_sessions_for_timeout falhou: {exc}")
        timeouts = []

    timeout_ids = {s.get("id") for s in timeouts if s.get("type") == "chat_driven"}

    for session in reminders:
        if session.get("type") != "chat_driven":
            continue
        if session.get("id") in timeout_ids:
            continue
        if _send_chat_session_reminder(session):
            stats["reminders"] += 1

    for session in timeouts:
        if session.get("type") != "chat_driven":
            continue
        _trigger_session_timeout(session)
        stats["timeouts"] += 1
    return stats



@app.post("/cron/sessions/watchdog")
def run_session_watchdog_now():
    stats = _process_session_watchdog_once()
    return {"ok": True, **stats}

# Dispara o worker de lembretes quando a aplicação sobe (opcional)
@app.on_event("startup")
async def _boot_followups_loop():
    # garante estrutura do banco
    try:
        init_db()
    except Exception as e:
        logger.warning(f"[BOOT] init_db falhou (seguindo sem parar): {e}")

    if os.getenv("ENABLE_INPROC_FOLLOWUPS", "0") == "1":
        async def _loop():
            interval = int(os.getenv("FOLLOWUP_POLL_SECONDS", "60"))
            logger.info(f"[FOLLOWUPS] loop ativado (intervalo {interval}s)")
            while True:
                try:
                    await asyncio.get_running_loop().run_in_executor(None, _process_followups_once)
                except Exception as e:
                    logger.exception(f"[FOLLOWUPS] erro no loop: {e}")
                await asyncio.sleep(interval)
        asyncio.create_task(_loop())

    if ENABLE_SESSION_WATCHDOG:
        async def _session_loop():
            interval = SESSION_WATCHDOG_POLL_SECONDS
            logger.info(f"[SESSIONS] watchdog ativado (intervalo {interval}s)")
            while True:
                try:
                    await asyncio.get_running_loop().run_in_executor(None, _process_session_watchdog_once)
                except Exception as e:
                    logger.exception(f"[SESSIONS] erro no watchdog: {e}")
                await asyncio.sleep(interval)

        asyncio.create_task(_session_loop())

# -----------------------------------------------------------------------------#
# BOT: carregamento seguro (sempre registra /api/messages)
# -----------------------------------------------------------------------------#
_bot_loaded_ok = False
_bot_boot_error = None

if ENABLE_TEAMS_BOT:
    try:
        from botbuilder.core import (
            BotFrameworkAdapterSettings,
            BotFrameworkAdapter,
            ConversationState,
            MemoryStorage,
        )
        from botbuilder.schema import Activity
        from app.bot import N1Bot

        # Validação explícita de credenciais
        missing = []
        if not BOT_APP_ID: missing.append("BOT_APP_ID")
        if not BOT_APP_PASSWORD: missing.append("BOT_APP_PASSWORD")
        if missing:
            raise RuntimeError(f"Credenciais ausentes: {', '.join(missing)}")

        adapter_settings = BotFrameworkAdapterSettings(
            app_id=BOT_APP_ID,
            app_password=BOT_APP_PASSWORD,
            channel_auth_tenant=MS_TENANT_ID or None,
        )
        bot_adapter = BotFrameworkAdapter(adapter_settings)
        memory = MemoryStorage()
        conversation_state = ConversationState(memory)
        bot = N1Bot(conversation_state)

        @app.get("/debug/bot/health")
        def bot_health():
            return {
                "enabled": True,
                "_bot_loaded_ok": _bot_loaded_ok,
                "app_id_present": bool(BOT_APP_ID),
                "app_password_present": bool(bool(BOT_APP_PASSWORD)),
                "tenant_id_present": bool(MS_TENANT_ID),
                "boot_error": _bot_boot_error,
            }

        @app.post("/api/messages")
        async def messages(request: Request):
            logger.info("[BOT] /api/messages called")
            try:
                body = await request.json()
            except Exception:
                logger.warning("[BOT] payload inválido (não-JSON).")
                body = {}

            auth_header = request.headers.get("Authorization", "")

            # tenta desserializar Activity; se falhar, o adapter ainda valida
            try:
                activity = Activity().deserialize(body)
            except Exception:
                logger.warning("[BOT] falha ao desserializar Activity; passando body cru.")
                activity = body

            async def aux(turn_context):
                try:
                    await bot.on_turn(turn_context)
                    await conversation_state.save_changes(turn_context)
                except Exception as e:
                    logger.exception("[BOT] erro em on_turn: {}", e)

            try:
                await asyncio.wait_for(
                    bot_adapter.process_activity(activity, auth_header, aux),
                    timeout=25,
                )
                return Response(status_code=201)
            except asyncio.TimeoutError:
                logger.error("[BOT] process_activity TIMEOUT (cheque *.botframework.com / login.botframework.com)")
                return Response(status_code=200)
            except Exception as e:
                logger.exception("[BOT] erro no process_activity: {}", e)
                return Response(status_code=200)

        _bot_loaded_ok = True
        logger.info("[BOOT] Bot do Teams carregado com sucesso (/api/messages).")

    except Exception as e:
        _bot_boot_error = f"{e}"
        logger.error(f"[BOOT] Falha ao inicializar Bot do Teams: {e}\n{traceback.format_exc()}")

# Stub só se não carregou; nunca 5xx, mas agora loga o motivo
if not _bot_loaded_ok:
    @app.get("/debug/bot/health")
    def bot_health_stub():
        return {
            "enabled": ENABLE_TEAMS_BOT,
            "_bot_loaded_ok": False,
            "app_id_present": bool(BOT_APP_ID),
            "app_password_present": bool(bool(BOT_APP_PASSWORD)),
            "tenant_id_present": bool(MS_TENANT_ID),
            "boot_error": _bot_boot_error or "não inicializado",
        }

    @app.post("/api/messages")
    async def messages_stub(_: Request):
        logger.error("[BOOT] Bot indisponível (stub) — verifique dependências/credenciais. Motivo: {}", _bot_boot_error)
        return Response(status_code=200)



# -----------------------------------------------------------------------------#
# Ingestão Movidesk + Classificação
# -----------------------------------------------------------------------------#
@app.post("/ingest/movidesk")
async def ingest_movidesk(
    request: Request,
    t: str = Query(..., description="segredo do webhook"),
):
    # --- helpers locais (auto-contidos) ---
    def _get_first(obj, *names, default=None):
        """Lê um atributo/chave com nomes alternativos em BaseModel, dict ou objeto simples."""
        for n in names:
            if hasattr(obj, n):
                try:
                    v = getattr(obj, n)
                    if v is not None:
                        return v
                except Exception:
                    pass
            if isinstance(obj, dict) and n in obj and obj[n] is not None:
                return obj[n]
        return default

    def _to_dict_safe(obj):
        """Serializa qualquer objeto para dict (p/ logging/resposta)."""
        try:
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
        except Exception:
            pass
        try:
            if hasattr(obj, "dict"):
                return obj.dict()
        except Exception:
            pass
        if isinstance(obj, dict):
            return obj
        try:
            return {k: v for k, v in vars(obj).items() if not callable(v) and not k.startswith("_")}
        except Exception:
            return {"_repr": repr(obj)}

    # --- auth do webhook ---
    if not WEBHOOK_SHARED_SECRET or t != WEBHOOK_SHARED_SECRET:
        _log_ingest(INGEST_ACTION_PAYLOAD_RECEIVED, "error", error_message="invalid-shared-secret")
        raise HTTPException(status_code=401, detail="Segredo inválido")

    # --- payload ---
    try:
        payload = await request.json()
    except Exception:
        # Movidesk pode mandar form-encoded em alguns eventos
        try:
            form = await request.form()
            if "payload" in form:
                import json as _json
                payload = _json.loads(form["payload"])
            else:
                raw = (await request.body()).decode("utf-8", "ignore")
                import json as _json
                payload = _json.loads(raw) if raw.strip().startswith("{") else {}
        except Exception:
            payload = {}

    if not isinstance(payload, dict) or not payload:
        _log_ingest(INGEST_ACTION_PAYLOAD_RECEIVED, "error", error_message="invalid-payload")
        raise HTTPException(status_code=400, detail="Payload inválido (JSON esperado)")

    # aceita Id/id/ticketId/TicketId
    ticket_id = int(str(
        payload.get("id")
        or payload.get("Id")
        or payload.get("ticketId")
        or payload.get("TicketId")
        or "0"
    ))
    if not ticket_id:
        _log_ingest(INGEST_ACTION_PAYLOAD_RECEIVED, "error", error_message="ticket-id-missing")
        raise HTTPException(status_code=400, detail="ID do ticket ausente no payload")

    logger.info(f"[INGEST] payload recebido para ticket {ticket_id}: {payload}")
    _log_ingest(
        INGEST_ACTION_PAYLOAD_RECEIVED,
        "success",
        ticket_id=ticket_id,
        context={"status": status, "action_count": action_count},
    )

    # =========================
    # TRAVA NA RAIZ (simples)
    # =========================
    # Regra 1: só a primeira CRIAÇÃO do ticket (Status="Novo" e ActionCount=1)
    status = (payload.get("Status") or "").strip().lower()
    action_count = int(payload.get("ActionCount") or 0)
    is_first_creation = (status == "novo") and (action_count == 1)
    if not is_first_creation:
        # Ignora quaisquer outros eventos (novas ações, reentregas, etc.)
        _log_ingest(INGEST_ACTION_SKIP_FLOW, "success", ticket_id=ticket_id, context={"reason": "not-first-creation"})
        return {"ok": True, "skipped": "not-first-creation", "ticket": ticket_id}

    # Regra 2: só uma vez por ticket (não notificar novamente)
    global _NOTIFIED_TICKETS
    if ticket_id in _NOTIFIED_TICKETS:
        _log_ingest(INGEST_ACTION_SKIP_FLOW, "success", ticket_id=ticket_id, context={"reason": "already-notified"})
        return {"ok": True, "skipped": "already-notified", "ticket": ticket_id}

    # --- ticket base (a partir daqui só roda para a 1ª criação e ticket ainda não notificado) ---
    try:
        ticket = get_ticket_by_id(ticket_id)
        _log_ingest(INGEST_ACTION_FETCH_TICKET, "success", ticket_id=ticket_id, context={"origin": ticket.get("origin")})
    except (RetryError, MovideskError) as e:
        _log_ingest(INGEST_ACTION_FETCH_TICKET, "error", ticket_id=ticket_id, error_message=str(e))
        raise HTTPException(status_code=502, detail=f"Falha ao buscar ticket {ticket_id} na API Movidesk: {e}")

    origin_code = ticket.get("origin")
    origin_name = ORIGIN_MAP.get(origin_code, str(origin_code))
    origin_email_account = ticket.get("originEmailAccount") or ""
    subject = ticket.get("subject") or ""
    requester_email = _pick_requester_email(ticket)  # ← sempre usaremos o solicitante

    is_email = _is_email_channel(ticket)
    matches_account = _email_to_matches(ticket)
    allowed = bool(is_email and matches_account)
    if not allowed:
        _log_ingest(
            INGEST_ACTION_SKIP_FLOW,
            "success",
            ticket_id=ticket_id,
            context={"reason": "channel-not-allowed", "origin": origin_code},
        )

    # --- conteúdo para classificar ---
    try:
        bundle = get_ticket_text_bundle(ticket_id)
    except Exception as e:
        logger.warning(f"[INGEST] get_ticket_text_bundle falhou para {ticket_id}: {e}")
        bundle = {"subject": subject, "first_action_text": "", "first_action_html": ""}

    subj = (bundle.get("subject") or subject or "").strip()
    body_text = (bundle.get("first_action_text") or "").strip()
    body_for_llm = body_text if body_text else f"(sem corpo; classificar pelo assunto) {subj}"

    # --------------------------- Classificação (robusta a esquemas) ---------------------------
    llm_obj = None
    n1_candidate = False
    n1_reason = "Sem análise"
    suggested_service = suggested_category = None
    suggested_urgency = "Média"
    llm_conf = None
    llm_admin = False

    if allowed:
        try:
            if OPENAI_API_KEY:
                llm = classify_ticket_with_llm(subj, body_for_llm)

                llm_obj = _to_dict_safe(llm)
                llm_conf = _get_first(llm, "confidence", "score", default=None)
                llm_admin = bool(_get_first(llm, "admin_required", "needs_admin", "admin", default=False))
                raw_candidate = _get_first(llm, "n1_candidate", "auto_solve", "auto", "is_n1_candidate", default=False)
                n1_reason = _get_first(llm, "reason", "why", "explanation", "rationale", default="—")
                suggested_service = _get_first(llm, "suggested_service", "service", default=None)
                suggested_category = _get_first(llm, "suggested_category", "category", default=None)
                suggested_urgency = _get_first(llm, "suggested_urgency", "urgency", default="Média")

                # regra: só considera candidato se não exigir admin; se tiver confidence, aplica threshold
                if llm_conf is None:
                    n1_candidate = bool(raw_candidate and not llm_admin)
                else:
                    try:
                        n1_candidate = bool(raw_candidate and not llm_admin and float(llm_conf) >= 0.55)
                    except Exception:
                        n1_candidate = bool(raw_candidate and not llm_admin)
                _log_ingest(
                    INGEST_ACTION_CLASSIFY_TICKET_LLM,
                    "success",
                    ticket_id=ticket_id,
                    context={"confidence": llm_conf, "admin_required": llm_admin},
                )
            else:
                clf = classify_from_subject(subj)
                raw_candidate = _get_first(clf, "auto_solve", "auto", "n1_candidate", default=False)
                reason_fb = _get_first(clf, "reason", "why", "explanation", default="—")
                n1_candidate = bool(raw_candidate)
                n1_reason = f"[fallback] {reason_fb}"
                suggested_service = _get_first(clf, "service", "suggested_service", default=None)
                suggested_category = _get_first(clf, "category", "suggested_category", default=None)
                suggested_urgency = _get_first(clf, "urgency", "suggested_urgency", default="Média")
                _log_ingest(
                    INGEST_ACTION_CLASSIFY_TICKET_FALLBACK,
                    "success",
                    ticket_id=ticket_id,
                    context={"rule": "subject"},
                )
        except Exception as e:
            logger.warning(f"[INGEST] falha na classificação: {e}")
            _log_ingest(INGEST_ACTION_CLASSIFY_TICKET_LLM, "error", ticket_id=ticket_id, error_message=str(e))
            try:
                clf = classify_from_subject(subj)
                raw_candidate = _get_first(clf, "auto_solve", "auto", "n1_candidate", default=False)
                reason_fb = _get_first(clf, "reason", "why", "explanation", default="—")
                n1_candidate = bool(raw_candidate)
                n1_reason = f"[fallback] {reason_fb}"
                suggested_service = _get_first(clf, "service", "suggested_service", default=None)
                suggested_category = _get_first(clf, "category", "suggested_category", default=None)
                suggested_urgency = _get_first(clf, "urgency", "suggested_urgency", default="Média")
                _log_ingest(
                    INGEST_ACTION_CLASSIFY_TICKET_FALLBACK,
                    "success",
                    ticket_id=ticket_id,
                    context={"rule": "fallback-after-error"},
                )
            except Exception as e2:
                logger.warning(f"[INGEST] fallback simples também falhou: {e2}")
                n1_candidate = False
                n1_reason = "[erro] classificação indisponível"
                suggested_urgency = "Média"
                _log_ingest(
                    INGEST_ACTION_CLASSIFY_TICKET_FALLBACK,
                    "error",
                    ticket_id=ticket_id,
                    error_message=str(e2),
                )

    # --- persistência p/ auditoria ---
    try:
        upsert_ticket(
            ticket_id=ticket_id,
            allowed=allowed,
            subject=subj or subject or f"Ticket #{ticket_id}",
            requester_email=requester_email,
            origin_email_account=origin_email_account,
            n1_candidate=n1_candidate,
            n1_reason=n1_reason,
            suggested_service=suggested_service,
            suggested_category=suggested_category,
            suggested_urgency=suggested_urgency,
            llm_confidence=llm_conf,
            llm_admin_required=int(llm_admin),
        )
        _log_ingest(
            INGEST_ACTION_UPSERT_TICKET,
            "success",
            ticket_id=ticket_id,
            context={"allowed": allowed},
        )
    except Exception as e:
        _log_ingest(INGEST_ACTION_UPSERT_TICKET, "error", ticket_id=ticket_id, error_message=str(e))
        raise

    # --- notificação proativa no Teams + followups (somente o SOLICITANTE) ---
    notified = False
    if ENABLE_TEAMS_BOT and allowed and requester_email:
        try:
            preview = f"Olá! Recebemos seu chamado #{ticket_id} sobre \"{subj or subject}\". Podemos iniciar o atendimento agora?"
            # notifica EXCLUSIVAMENTE o e-mail do solicitante
            graph_user_id = notify_user_for_ticket(requester_email, ticket_id, subj or f"Ticket #{ticket_id}", preview_text=preview)
            notified = True
            if graph_user_id:
                set_user_current_ticket(requester_email, ticket_id, teams_user_id=graph_user_id)
            _log_ingest(
                INGEST_ACTION_NOTIFY_TEAMS,
                "success",
                ticket_id=ticket_id,
                context={"requester_email": requester_email},
            )

            # marca para não notificar novamente este ticket
            _NOTIFIED_TICKETS.add(ticket_id)

            try:
                mark_teams_notified(ticket_id)
            except Exception as e:
                logger.warning(f"[INGEST] não consegui marcar teams_notified: {e}")

            try:
                if not _followups_already_scheduled(ticket_id):
                    schedule_proactive_flow(ticket_id, requester_email, subj or subject or f"Ticket #{ticket_id}")
                    _log_ingest(INGEST_ACTION_SCHEDULE_FOLLOWUP, "success", ticket_id=ticket_id)
            except Exception as e:
                logger.warning(f"[INGEST] não consegui agendar followups do ticket {ticket_id}: {e}")
                _log_ingest(INGEST_ACTION_SCHEDULE_FOLLOWUP, "error", ticket_id=ticket_id, error_message=str(e))

        except TeamsGraphError as e:
            logger.warning(f"[TEAMS] falha ao notificar usuário {requester_email} para ticket {ticket_id}: {e}")
            _log_ingest(INGEST_ACTION_NOTIFY_TEAMS, "error", ticket_id=ticket_id, error_message=str(e))

    # --- resposta ---
    return {
        "ok": True,
        "ticket": {
            "id": ticket_id,
            "origin": origin_name,
            "originEmailAccount": origin_email_account,
            "subject": subj or subject,
            "requester_email": requester_email,
        },
        "allowed": allowed,
        "n1_candidate": n1_candidate,
        "n1_reason": n1_reason,
        "suggested": {
            "service": suggested_service,
            "category": suggested_category,
            "urgency": suggested_urgency,
        },
        "notified_on_teams": notified,
        "llm_used": bool(OPENAI_API_KEY),
        "llm_obj": llm_obj or {},
        "skipped": None if notified else "already-notified" if ticket_id in _NOTIFIED_TICKETS else None,
    }

# -----------------------------------------------------------------------------#
# Amostragem / utilitários Movidesk
# -----------------------------------------------------------------------------#

@app.get("/debug/extract-fields")
def debug_extract_fields(id: int):
    """
    Retorna os campos essenciais (solicitante, ticket, e-mail de recebimento, assunto, 1ª ação)
    para inspeção rápida em testes e troubleshooting.
    """
    try:
        core = extract_core_fields(int(id))
        return {"ok": True, **core}
    except (RetryError, MovideskError) as e:
        raise HTTPException(status_code=502, detail=f"Movidesk: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao extrair campos: {e}")


@app.get("/debug/check")
def debug_check(id: int):
    try:
        t = get_ticket_by_id(id)
    except (RetryError, MovideskError) as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "id": id,
        "origin": ORIGIN_MAP.get(t.get("origin"), t.get("origin")),
        "originEmailAccount": t.get("originEmailAccount"),
        "subject": t.get("subject"),
        "owner": t.get("owner"),
        "clients": t.get("clients"),
        "isEmail": _is_email_channel(t),
        "matchesAccount": _email_to_matches(t),
    }

@app.get("/debug/ticket-text")
def debug_ticket_text(id: int):
    try:
        b = get_ticket_text_bundle(id)
        return b
    except (RetryError, MovideskError) as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/debug/latest-ti")
def debug_latest_ti(max_take: int = 50):
    try:
        sample = get_latest_ticket_for_email_account_multi(ALLOW_EMAIL_TO_LIST, max_take=max_take)
        counts = {}
        for t in (sample or []):
            acc = (t.get("originEmailAccount") or "").lower().strip()
            counts[acc] = counts.get(acc, 0) + 1
        ordered = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        return {"byOriginEmailAccount": [{"account": k, "count": v} for k, v in ordered], "totalSample": len(sample)}
    except (RetryError, MovideskError) as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/debug/rec")
def debug_rec(id: int):
    rec = get_ticket_rec(id)
    if not rec:
        raise HTTPException(status_code=404, detail="Ticket não encontrado no banco")
    return rec

# -----------------------------------------------------------------------------#
# Notificação proativa no Teams + Diagnósticos Graph
# -----------------------------------------------------------------------------#
class PingBody(BaseModel):
    id: int

@app.post("/debug/ping-teams")
def debug_ping_teams(body: PingBody, dry: bool = Query(False, description="se true, não chama Graph; só simula")):
    logger.info(f"[PING] pedido para ticket {body.id} (dry={dry})")
    rec = get_ticket_rec(body.id)
    from_db = False
    if rec:
        from_db = True
        user_email = rec.get("requester_email") or ""
        subject = rec.get("subject") or f"Ticket #{body.id}"
        if not user_email:
            raise HTTPException(status_code=400, detail="Ticket no banco sem requester_email")
        if dry:
            return {"ok": True, "dry": True, "notified": user_email, "ticketId": rec["ticket_id"], "subject": subject, "fromDb": True}
        try:
            graph_user_id = notify_user_for_ticket(user_email, rec["ticket_id"], subject)
            if graph_user_id:
                set_user_current_ticket(user_email, rec["ticket_id"], teams_user_id=graph_user_id)
            return {"ok": True, "notified": user_email, "ticketId": rec["ticket_id"], "subject": subject, "fromDb": True}
        except TeamsGraphError as e:
            raise HTTPException(status_code=502, detail=str(e))

    try:
        ticket = get_ticket_by_id(body.id)
    except (RetryError, MovideskError) as e:
        raise HTTPException(status_code=502, detail=f"Falha ao buscar ticket no Movidesk: {e}")

    if not (_is_email_channel(ticket) and _email_to_matches(ticket)):
        raise HTTPException(status_code=403, detail="Ticket não é do canal de e-mail permitido (TI).")

    user_email = _pick_requester_email(ticket)
    subject = ticket.get("subject") or f"Ticket #{body.id}"
    origin_email_account = ticket.get("originEmailAccount") or ""
    if not user_email:
        raise HTTPException(status_code=400, detail="Requester_email não encontrado para este ticket")

    upsert_ticket(ticket_id=body.id, allowed=True, subject=subject, requester_email=user_email, origin_email_account=origin_email_account)

    if dry:
        return {"ok": True, "dry": True, "notified": user_email, "ticketId": body.id, "subject": subject, "fromDb": from_db}

    try:
        graph_user_id = notify_user_for_ticket(user_email, body.id, subject)
        if graph_user_id:
            set_user_current_ticket(user_email, body.id, teams_user_id=graph_user_id)
        return {"ok": True, "notified": user_email, "ticketId": body.id, "subject": subject, "fromDb": from_db}
    except TeamsGraphError as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/debug/graph/token-info")
def debug_graph_token():
    try:
        return diag_token_info()
    except TeamsGraphError as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/debug/graph/resolve-app")
def debug_graph_resolve_app():
    try:
        return diag_resolve_app()
    except TeamsGraphError as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/debug/graph/user")
def debug_graph_user(email: str):
    try:
        return diag_user(email)
    except TeamsGraphError as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/debug/graph/user-apps")
def debug_graph_user_apps(email: str):
    try:
        return diag_user_installed_apps(email)
    except TeamsGraphError as e:
        raise HTTPException(status_code=502, detail=str(e))

# -----------------------------------------------------------------------------#
# Resumo -> Movidesk (com verificação de gravação)
# -----------------------------------------------------------------------------#
class SummaryBody(BaseModel):
    id: int

@app.post("/tickets/{ticket_id}/resumo")
def post_ticket_summary(ticket_id: int, body: SummaryBody):
    """
    Sumariza uma conversa (placeholder) e publica nota no ticket.
    Depois, lê ações/notas para confirmar visualmente a inserção.
    """
    try:
        _ = get_ticket_by_id(ticket_id)
    except (RetryError, MovideskError) as e:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar ticket: {e}")

    transcript = f"Interação automática sobre o ticket #{ticket_id}."
    resumo = summarize_conversation(transcript)

    # garante função (lazy)
    global _add_public_note
    if not callable(_add_public_note):
        try:
            from app.movidesk_client import add_public_note as _add_public_note  # type: ignore
        except Exception:
            _add_public_note = None  # type: ignore

    published = False
    attempt = None
    err_detail = None

    if not callable(_add_public_note):
        logger.warning("[resumo] add_public_note indisponível; resumo NÃO publicado no Movidesk.")
    else:
        try:
            result = _add_public_note(ticket_id, resumo)  # type: ignore[misc]
            attempt = (result or {}).get("attempt")
            published = bool(result and result.get("ok"))
        except RetryError as e:
            inner = getattr(e, "last_attempt", None)
            inner_exc = inner.exception() if inner else None  # type: ignore[attr-defined]
            err_detail = str(inner_exc or e)
            logger.warning(f"[resumo] falha ao publicar nota (RetryError): {err_detail}")
        except MovideskError as e:
            err_detail = str(e)
            logger.warning(f"[resumo] falha ao publicar nota (MovideskError): {err_detail}")
        except Exception as e:
            err_detail = f"Erro inesperado: {e}"
            logger.exception("[resumo] falha inesperada ao publicar nota")

    # Auditoria pós-escrita (se helpers existirem)
    actions = []
    notes = []
    try:
        if callable(list_actions):
            actions = list_actions(ticket_id, top=5)  # type: ignore[misc]
        if callable(list_notes):
            notes = list_notes(ticket_id, top=5)  # type: ignore[misc]
    except Exception as e:
        logger.warning(f"[resumo] auditoria pós-escrita falhou: {e}")

    return {
        "ok": True,
        "ticket": ticket_id,
        "resumo": resumo,
        "published": published,
        "attempt": attempt,
        "error": err_detail,
        "after_api_check": {
            "actions_top5": actions,
            "notes_top5": notes,
        },
    }

# -----------------------------------------------------------------------------#
# Ferramentas de debug Movidesk (validação de escrita/leitura)
# -----------------------------------------------------------------------------#
class ActionTestBody(BaseModel):
    ticket_id: int
    text: str | None = None

@app.post("/debug/movidesk/action-test")
def movidesk_action_test(body: ActionTestBody):
    """
    Publica uma ação/nota com o texto informado e retorna leitura imediata
    das ações/notas para ver se persistiu de fato.
    """
    txt = (body.text or f"[N1 Bot] Teste automático no ticket #{body.ticket_id}").strip()

    # lazy import p/ garantir em modo --reload
    global _add_public_note
    if not callable(_add_public_note):
        try:
            from app.movidesk_client import add_public_note as _add_public_note  # type: ignore
        except Exception:
            _add_public_note = None  # type: ignore

    if not callable(_add_public_note):
        raise HTTPException(status_code=500, detail="add_public_note indisponível.")

    result = None
    try:
        result = _add_public_note(int(body.ticket_id), txt)  # type: ignore[misc]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao publicar ação/nota: {e}")

    a = []
    n = []
    try:
        if callable(list_actions):
            a = list_actions(body.ticket_id, top=5)  # type: ignore[misc]
        if callable(list_notes):
            n = list_notes(body.ticket_id, top=5)  # type: ignore[misc]
    except Exception as e:
        logger.warning(f"[action-test] auditoria falhou: {e}")

    return {
        "ok": True,
        "ticket": body.ticket_id,
        "publish_result": result,
        "after_api_check": {"actions_top5": a, "notes_top5": n},
    }

@app.get("/debug/movidesk/audit")
def movidesk_audit(id: int, top: int = 10):
    """
    Apenas lista ações/notas atuais do ticket, sem publicar nada.
    Útil para visualizar o que a API está retornando na sua base.
    """
    a = []
    n = []
    try:
        if callable(list_actions):
            a = list_actions(id, top=top)  # type: ignore[misc]
        if callable(list_notes):
            n = list_notes(id, top=top)  # type: ignore[misc]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha na leitura: {e}")
    return {"ok": True, "ticket": id, "actions": a, "notes": n}

# -----------------------------------------------------------------------------#
# Cancelamento de followups
# -----------------------------------------------------------------------------#
@app.post("/tickets/{ticket_id}/followups/cancel")
def cancel_followups_api(ticket_id: int):
    cancel_followups(int(ticket_id))
    return {"ok": True}
