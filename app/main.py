# app/main.py
from __future__ import annotations

import os
import sys
import traceback
import asyncio

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel
from tenacity import RetryError

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
    send_proactive_message,
    diag_token_info,
    diag_resolve_app,
    diag_user,
    diag_user_installed_apps,
)
from app import kb

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

@app.get("/healthz")
def healthz():
    return {"ok": True, "file": __file__}

@app.get("/debug/routes")
def _debug_routes():
    return [getattr(r, "path", str(r)) for r in app.router.routes]

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
    due = fetch_due_followups(limit=50)
    sent = 0
    for fu in due:
        ok = False
        try:
            ok = send_proactive_message(fu["requester_email"], fu["message"])
        except Exception as e:
            logger.warning(f"[followups] erro ao enviar para {fu['requester_email']}: {e}")
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

# -----------------------------------------------------------------------------#
# BOT: carregamento seguro (sempre registra /api/messages)
# -----------------------------------------------------------------------------#
_bot_loaded_ok = False
if ENABLE_TEAMS_BOT:
    try:
        from botbuilder.core import (
            BotFrameworkAdapterSettings,
            BotFrameworkAdapter,
            ConversationState,
            MemoryStorage,
        )
        from botbuilder.schema import Activity
        from app.bot import N1Bot  # precisa de app/bot.py e app/__init__.py

        adapter_settings = BotFrameworkAdapterSettings(
            app_id=BOT_APP_ID,
            app_password=BOT_APP_PASSWORD,
            channel_auth_tenant=MS_TENANT_ID or None,
        )

        bot_adapter = BotFrameworkAdapter(adapter_settings)
        memory = MemoryStorage()
        conversation_state = ConversationState(memory)
        bot = N1Bot(conversation_state)

        @app.post("/debug/kb/reindex")
        def _kb_reindex():
            return kb.reindex()

        @app.get("/debug/kb/search")
        def _kb_search(q: str, k: int = 5):
            return {"results": kb.search(q, k)}

        @app.post("/api/messages")
        async def messages(request: Request):
            logger.info("[BOT] /api/messages called")
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Payload inválido (JSON esperado)")

            activity = Activity().deserialize(body)
            auth_header = request.headers.get("Authorization", "")

            async def aux(turn_context):
                await bot.on_turn(turn_context)
                await conversation_state.save_changes(turn_context)

            try:
                await asyncio.wait_for(
                    bot_adapter.process_activity(activity, auth_header, aux),
                    timeout=15,
                )
                return Response(status_code=201)
            except asyncio.TimeoutError:
                logger.error("[BOT] process_activity TIMEOUT (checar firewall para *.botframework.com e login.botframework.com)")
                raise HTTPException(status_code=504, detail="Bot timeout durante validação.")
            except Exception:
                logger.exception("[BOT] erro no process_activity")
                raise HTTPException(status_code=500, detail="Erro interno no bot.")

        _bot_loaded_ok = True
        logger.info("[BOOT] Bot do Teams carregado com sucesso (/api/messages).")
    except Exception as e:
        logger.error(f"[BOOT] Falha ao inicializar Bot do Teams: {e}\n{traceback.format_exc()}")

# Stub se o bot não carregou (rota existe, mas retorna 503)
if not _bot_loaded_ok:
    @app.post("/api/messages")
    async def messages_stub(_: Request):
        raise HTTPException(status_code=503, detail="Bot indisponível (stub). Verifique dependências/credenciais.")

# -----------------------------------------------------------------------------#
# Ingestão Movidesk + Classificação
# -----------------------------------------------------------------------------#
@app.post("/ingest/movidesk")
async def ingest_movidesk(
    request: Request,
    t: str = Query(..., description="segredo do webhook"),
):
    if not WEBHOOK_SHARED_SECRET or t != WEBHOOK_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Segredo inválido")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload inválido (JSON esperado)")

    ticket_id = int(str(payload.get("id") or payload.get("ticketId") or "0"))
    if not ticket_id:
        raise HTTPException(status_code=400, detail="ID do ticket ausente no payload")

    logger.info(f"[INGEST] payload recebido para ticket {ticket_id}: {payload}")

    try:
        ticket = get_ticket_by_id(ticket_id)
    except (RetryError, MovideskError) as e:
        raise HTTPException(status_code=502, detail=f"Falha ao buscar ticket {ticket_id} na API Movidesk: {e}")

    origin_code = ticket.get("origin")
    origin_name = ORIGIN_MAP.get(origin_code, str(origin_code))
    origin_email_account = ticket.get("originEmailAccount") or ""
    subject = ticket.get("subject") or ""
    requester_email = _pick_requester_email(ticket)

    is_email = _is_email_channel(ticket)
    matches_account = _email_to_matches(ticket)
    allowed = bool(is_email and matches_account)

    # tenta obter texto do primeiro e-mail (ajuda na classificação)
    try:
        bundle = get_ticket_text_bundle(ticket_id)
    except Exception as e:
        logger.warning(f"[INGEST] get_ticket_text_bundle falhou para {ticket_id}: {e}")
        bundle = {"subject": subject, "first_action_text": "", "first_action_html": ""}

    subj = (bundle.get("subject") or subject or "").strip()
    body_text = (bundle.get("first_action_text") or "").strip()
    body_for_llm = body_text if body_text else f"(sem corpo; classificar pelo assunto) {subj}"

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
                llm_obj = llm.model_dump()
                n1_candidate = bool(llm.n1_candidate and not llm.admin_required and (llm.confidence or 0) >= 0.55)
                n1_reason = llm.reason or "—"
                llm_conf = llm.confidence
                llm_admin = bool(llm.admin_required)
                suggested_service = llm.suggested_service
                suggested_category = llm.suggested_category
                suggested_urgency = llm.suggested_urgency or "Média"
            else:
                clf = classify_from_subject(subj)
                n1_candidate = bool(clf.auto_solve)
                n1_reason = f"[fallback] {clf.reason}"
                suggested_service = clf.service
                suggested_category = clf.category
                suggested_urgency = clf.urgency or "Média"
        except Exception as e:
            logger.warning(f"[INGEST] falha na classificação com LLM: {e}")
            clf = classify_from_subject(subj)
            n1_candidate = bool(clf.auto_solve)
            n1_reason = f"[fallback] {clf.reason}"
            suggested_service = clf.service
            suggested_category = clf.category
            suggested_urgency = clf.urgency or "Média"

    # upsert no banco para debug/consulta futura
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

    # Notifica proativamente no Teams e agenda lembretes
    notified = False
    if ENABLE_TEAMS_BOT and allowed and requester_email:
        try:
            preview = f"Olá! Recebemos seu chamado #{ticket_id} sobre \"{subj or subject}\". Podemos iniciar o atendimento agora?"
            notify_user_for_ticket(requester_email, ticket_id, subj or f"Ticket #{ticket_id}", preview_text=preview)
            notified = True

            try:
                mark_teams_notified(ticket_id)
            except Exception as e:
                logger.warning(f"[INGEST] não consegui marcar teams_notified: {e}")

            try:
                if not _followups_already_scheduled(ticket_id):
                    schedule_proactive_flow(ticket_id, requester_email, subj or subject or f"Ticket #{ticket_id}")
            except Exception as e:
                logger.warning(f"[INGEST] não consegui agendar followups do ticket {ticket_id}: {e}")

        except TeamsGraphError as e:
            logger.warning(f"[TEAMS] falha ao notificar usuário {requester_email} para ticket {ticket_id}: {e}")

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
    }

# -----------------------------------------------------------------------------#
# Amostragem / utilitários Movidesk
# -----------------------------------------------------------------------------#
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
            notify_user_for_ticket(user_email, rec["ticket_id"], subject)
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
        notify_user_for_ticket(user_email, body.id, subject)
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
