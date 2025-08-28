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

# --- Windows: usar o event loop compatível
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
    except Exception:
        pass

# ---- Módulos do projeto
from app.db import init_db, upsert_ticket, get_ticket_rec
from app.movidesk_client import (
    MovideskError,
    get_ticket_by_id,
    get_ticket_text_bundle,
    get_latest_ticket_for_email_account_multi,
    sample_email_channel,
)
from app.classifier import classify_from_subject
from app.llm import classify_ticket_with_llm
from app.teams_graph import (
    TeamsGraphError,
    notify_user_for_ticket,
    diag_token_info,
    diag_resolve_app,
    diag_user,
)

load_dotenv()

# -----------------------------------------------------------------------------#
# App + Logs
# -----------------------------------------------------------------------------#
app = FastAPI(title="Assistente N1 - Tecnogera (BOT single-tenant fix)")
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

@app.get("/healthz")
def healthz():
    return {"ok": True, "file": __file__}

@app.get("/debug/routes")
def _debug_routes():
    return [getattr(r, "path", str(r)) for r in app.router.routes]

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

@app.get("/debug/bot-info")
def debug_bot_info():
    return {
        "ENABLE_TEAMS_BOT": os.getenv("ENABLE_TEAMS_BOT"),
        "MS_CLIENT_ID_present": bool(BOT_APP_ID),
        "MS_CLIENT_SECRET_present": bool(BOT_APP_PASSWORD),
        "MS_TENANT_ID_present": bool(MS_TENANT_ID),
        "BOT_LOADED": _bot_loaded_ok,
    }

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

        # >>> FIX CRÍTICO PARA SINGLE-TENANT <<<
        # Canal de autenticação deve usar SEU tenant quando o App é single-tenant
        adapter_settings = BotFrameworkAdapterSettings(
            app_id=BOT_APP_ID,
            app_password=BOT_APP_PASSWORD,
            channel_auth_tenant=MS_TENANT_ID or None,  # se vazio, cai no default botframework.com
        )

        bot_adapter = BotFrameworkAdapter(adapter_settings)
        memory = MemoryStorage()
        conversation_state = ConversationState(memory)
        bot = N1Bot(conversation_state)

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
                # timeout para evitar pendurar se metadata/JWKS estiver bloqueado
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
async def ingest_movidesk(request: Request, t: str = Query(..., description="segredo do webhook")):
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
                n1_candidate = bool(llm.n1_candidate and not llm.admin_required and (llm.confidence or 0) >= 0.5)
                n1_reason = f"LLM: {llm.rationale or 'sem rationale'}"
                suggested_service = llm.suggested_service
                suggested_category = llm.suggested_category
                suggested_urgency = llm.suggested_urgency or "Média"
                llm_conf = llm.confidence
                llm_admin = bool(llm.admin_required)
            else:
                cls = classify_from_subject(subj)
                n1_candidate = cls.n1_candidate
                n1_reason = f"Fallback regras: {cls.n1_reason}"
                suggested_service = cls.suggested_service
                suggested_category = cls.suggested_category
                suggested_urgency = cls.suggested_urgency
        except Exception as e:
            logger.error(f"[INGEST] LLM/regras falhou ({ticket_id}): {e}\n{traceback.format_exc()}")
            cls = classify_from_subject(subj)
            n1_candidate = cls.n1_candidate
            n1_reason = f"Fallback regras (erro LLM): {cls.n1_reason}"
            suggested_service = cls.suggested_service
            suggested_category = cls.suggested_category
            suggested_urgency = cls.suggested_urgency

    upsert_ticket(
        ticket_id=ticket_id,
        allowed=allowed,
        subject=subj,
        requester_email=requester_email,
        origin_email_account=origin_email_account,
        n1_candidate=n1_candidate,
        n1_reason=n1_reason,
        suggested_service=suggested_service,
        suggested_category=suggested_category,
        suggested_urgency=suggested_urgency,
        llm_json=llm_obj,
        llm_confidence=llm_conf,
        llm_admin_required=llm_admin,
    )

    return {
        "ok": True,
        "ticketId": ticket_id,
        "allowed": allowed,
        "origin": {"code": origin_code, "name": origin_name},
        "originEmailAccount": origin_email_account,
        "requesterEmail": requester_email,
        "subject": subj,
        "classification": {
            "n1_candidate": n1_candidate,
            "n1_reason": n1_reason,
            "suggested_service": suggested_service,
            "suggested_category": suggested_category,
            "suggested_urgency": suggested_urgency,
            "llm_confidence": llm_conf,
            "llm_admin_required": llm_admin,
            "llm_raw": llm_obj,
        },
    }

# -----------------------------------------------------------------------------#
# Debug Movidesk / Base
# -----------------------------------------------------------------------------#
@app.get("/debug/ticket-text")
def debug_ticket_text(id: int):
    try:
        return get_ticket_text_bundle(id)
    except (RetryError, MovideskError) as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/debug/check")
def debug_check(id: int):
    try:
        ticket = get_ticket_by_id(id)
    except (RetryError, MovideskError) as e:
        raise HTTPException(status_code=502, detail=str(e))
    origin_code = ticket.get("origin")
    origin_name = ORIGIN_MAP.get(origin_code, str(origin_code))
    origin_email_account = ticket.get("originEmailAccount") or ""
    is_email = _is_email_channel(ticket)
    matches_account = _email_to_matches(ticket)
    allowed = bool(is_email and matches_account)
    return {
        "id": ticket.get("id"),
        "subject": ticket.get("subject"),
        "origin": {"code": origin_code, "name": origin_name},
        "originEmailAccount": origin_email_account,
        "allowEmailToEnv": ALLOW_EMAIL_TO_LIST,
        "allowed": allowed,
        "checks": {"isEmailChannel": is_email, "emailAccountMatches": matches_account},
    }

@app.get("/debug/latest-ti")
def debug_latest_ti():
    try:
        ticket = get_latest_ticket_for_email_account_multi(ALLOW_EMAIL_TO_LIST)
    except (RetryError, MovideskError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    origin_code = ticket.get("origin")
    origin_name = ORIGIN_MAP.get(origin_code, str(origin_code))
    origin_email_account = ticket.get("originEmailAccount") or ""
    is_email = _is_email_channel(ticket)
    matches_account = _email_to_matches(ticket)
    allowed = bool(is_email and matches_account)
    return {
        "id": ticket.get("id"),
        "subject": ticket.get("subject"),
        "origin": {"code": origin_code, "name": origin_name},
        "originEmailAccount": origin_email_account,
        "allowEmailToEnv": ALLOW_EMAIL_TO_LIST,
        "allowed": allowed,
        "checks": {"isEmailChannel": is_email, "emailAccountMatches": matches_account},
    }

@app.get("/debug/peek-accounts")
def debug_peek_accounts(max: int = 300):
    try:
        sample = sample_email_channel(max_items=max)
        counts = {}
        for s in sample:
            acc = (s.get("originEmailAccount") or "").strip()
            if not acc:
                continue
            counts[acc] = counts.get(acc, 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        return {"foundAccounts": [{"originEmailAccount": k, "count": v} for k, v in ordered], "totalSample": len(sample)}
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

    # não está no banco
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

@app.get("/debug/graph/user-installed-apps")
def debug_graph_user_installed_apps(email: str):
    from app.teams_graph import diag_user_installed_apps
    try:
        return diag_user_installed_apps(email)
    except TeamsGraphError as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/debug/bot-auth-check")
def debug_bot_auth_check():
    import httpx
    urls = [
        "https://login.botframework.com/v1/.well-known/openidconfiguration",
        "https://login.botframework.com/v1/.well-known/keys",
        "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
    ]
    out = []
    for u in urls:
        try:
            r = httpx.get(u, timeout=8)
            out.append({"url": u, "status": r.status_code, "ok": r.status_code == 200, "len": len(r.text)})
        except Exception as e:
            out.append({"url": u, "error": str(e)})
    return {"checks": out}
