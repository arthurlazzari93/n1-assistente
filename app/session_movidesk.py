from __future__ import annotations

import os
from typing import Dict, Any, Optional

from loguru import logger

from .movidesk_client import httpx, MOVIDESK_BASE, _get_token, add_public_note, MovideskError


DEFAULT_CHAT_CATEGORY = os.getenv("MOVIDESK_CHAT_CATEGORY", "Atendimento Automático/FAQ TI")
DEFAULT_CHAT_SUBJECT = "[Atendimento automático] Dúvida rápida de TI"
DEFAULT_FALLBACK_EMAIL = os.getenv("MOVIDESK_DEFAULT_REQUESTER_EMAIL", "ti@exemplo.com.br")


def build_chat_session_summary(session: Dict[str, Any], conversation: Optional[str] = None) -> str:
    base = "Usuário entrou em contato via chat (atendimento automático)."
    subject_hint = session.get("subject") or session.get("last_intent") or ""
    if subject_hint:
        base += f" Tema principal: {subject_hint}."
    if conversation:
        base += f"\n\nResumo da conversa:\n{conversation}"
    base += "\n\nO usuário confirmou que a orientação resolveu o problema."
    return base.strip()


def create_resolved_movidesk_ticket_from_session(session: Dict[str, Any], summary: str) -> Optional[str]:
    """
    Cria um ticket no Movidesk representando o atendimento automático já resolvido.
    Retorna o ID do ticket criado ou None em caso de falha.
    """
    token_email = session.get("user_email") or DEFAULT_FALLBACK_EMAIL
    token_email = (token_email or "").strip()
    if not token_email:
        logger.warning("[sessions] sem e-mail para criar ticket Movidesk (sessão %s)", session.get("id"))
        return None

    subject = f"{DEFAULT_CHAT_SUBJECT}"
    if session.get("subject"):
        subject += f" - {session.get('subject')}"

    payload = {
        "subject": subject[:200],
        "description": summary,
        "clients": [{"email": token_email}],
        "category": DEFAULT_CHAT_CATEGORY,
        "status": "Resolvido",
        "origin": 9,  # Web/API
    }

    token = _get_token()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    params = {"token": token}

    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            resp = client.post(f"{MOVIDESK_BASE}/tickets", params=params, json=payload)
            if resp.status_code not in (200, 201):
                logger.warning("[sessions] falha ao criar ticket Movidesk: %s %s", resp.status_code, resp.text[:400])
                return None
            data = resp.json()
    except MovideskError as e:
        logger.warning(f"[sessions] erro Movidesk ao criar ticket: {e}")
        return None
    except Exception as e:
        logger.warning(f"[sessions] erro inesperado ao criar ticket Movidesk: {e}")
        return None

    ticket_id = str(data.get("id") or "")
    if not ticket_id:
        logger.warning("[sessions] ticket criado mas sem ID retornado: %s", data)
        return None

    note_text = "Ticket registrado automaticamente após atendimento resolvido no chat virtual."
    try:
        add_public_note(int(ticket_id), note_text)
    except Exception as e:
        logger.warning(f"[sessions] falha ao adicionar nota no ticket {ticket_id}: {e}")

    return ticket_id
