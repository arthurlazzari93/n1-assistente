# app/ai/triage_agent.py
from __future__ import annotations
import os, json
from typing import List, Dict, Any

from openai import OpenAI

# RAG: buscamos na KB
from ..kb import search as kb_search, kb_try_answer

_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
_CLIENT = OpenAI(api_key=_OPENAI_API_KEY) if _OPENAI_API_KEY else None

SYSTEM_PROMPT = """
Você é um Agente de Suporte N1 da Tecnogera.
Objetivo: resolver o chamado com o MENOR número de interações, usando a Base de Conhecimento (KB) fornecida.

Diretrizes:
- Use o contexto do ticket (assunto/corpo) para já começar focado.
- Se a KB já traz o procedimento, vá direto ao passo-a-passo. Evite perguntas desnecessárias.
- Faça no MÁXIMO 1 pergunta por vez, curta e objetiva, apenas quando necessário.
- Inclua links/caminhos exatos se estiverem na KB.
- Se o usuário fugir do assunto, traga-o de volta educadamente.
- Se exigir acesso/permissão de administrador, políticas do AD/servidores, ou outra ação não N1 → ESCALONE.
- Respostas curtas e claras, PT-BR. Não invente informações fora da KB.

Responda APENAS como JSON:
{
  "action": "answer" | "ask" | "resolve" | "escalate",
  "message": "texto curto em PT-BR",
  "checklist": ["passo 1", "passo 2"],
  "confidence": 0.0,
  "reason": "motivo (se action=escalate)"
}
"""

def _ticket_context(ticket: Dict[str, Any]) -> str:
    subj = (ticket.get("subject") or "").strip()
    body = (ticket.get("first_action_text") or ticket.get("first_action_html") or ticket.get("description") or "").strip()
    body = body.replace("\r", " ").replace("\n", " ")
    if len(body) > 1500:
        body = body[:1500] + "..."
    return f"Ticket #{ticket.get('id')}\nAssunto: {subj or '(sem assunto)'}\nPrimeira mensagem: {body or '(sem corpo)'}"

def _history_as_msgs(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []
    for m in history:
        role = "assistant" if (m.get("role") == "assistant") else "user"
        txt = m.get("text")
        if not isinstance(txt, str):
            txt = m.get("content") if isinstance(m.get("content"), str) else ""
        txt = (txt or "").strip()
        if not txt:
            continue
        if len(txt) > 1200:
            txt = txt[:1200] + "..."
        msgs.append({"role": role, "content": txt})
    return msgs

def _kb_context(query: str, k: int = 6) -> str:
    hits = kb_search(query, k=k) or []
    if not hits:
        return "KB: (sem resultados relevantes)"
    lines = ["KB (trechos relevantes):"]
    for i, h in enumerate(hits[:6], start=1):
        title = h["doc_title"]
        chunk = (h["chunk_text"] or "").strip()
        if len(chunk) > 700:
            chunk = chunk[:700] + "..."
        lines.append(f"[{i}] Título: {title}\n{chunk}")
    return "\n\n".join(lines)

def _safe_json_loads(s: str) -> Dict[str, Any]:
    try:
        return json.loads(s)
    except Exception:
        return {
            "action": "ask",
            "message": "Para te guiar melhor: em qual tela/opção você está agora?",
            "checklist": [],
            "confidence": 0.4,
        }

def triage_next(history: List[Dict[str, Any]], ticket: Dict[str, Any]) -> Dict[str, Any]:
    """
    Decide o próximo passo usando KB + LLM.
    """
    # último texto do usuário (se houver) melhora a busca
    last_user = next((m.get("text") for m in reversed(history) if m.get("role") == "user"), "")
    query = f"{ticket.get('subject','')}\n{ticket.get('first_action_text') or ticket.get('description') or ''}\n{last_user}".strip()
    kb_ctx = _kb_context(query)

    # Sem chave → fallback de KB
    if _CLIENT is None:
        hit = kb_try_answer(query)
        if hit:
            return {
                "action": "answer",
                "message": hit["reply"],
                "checklist": [s["title"] for s in hit["sources"]],
                "confidence": 0.55,
            }
        return {
            "action": "ask",
            "message": "Me diga em qual tela/opção você está agora que eu te guio o próximo passo.",
            "checklist": [],
            "confidence": 0.35,
        }

    msgs: List[Dict[str, str]] = []
    msgs.append({"role": "system", "content": SYSTEM_PROMPT})
    msgs.append({"role": "user", "content": _ticket_context(ticket)})
    msgs.append({"role": "user", "content": kb_ctx})
    msgs.extend(_history_as_msgs(history))

    resp = _CLIENT.chat.completions.create(
        model=_MODEL,
        messages=msgs,
        temperature=0.2,
        max_tokens=600,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    data = _safe_json_loads(content)

    # saneamento
    data.setdefault("action", "answer")
    data.setdefault("message", "Certo! Vou te guiar. Em qual tela/opção você está agora?")
    data.setdefault("checklist", [])
    data.setdefault("confidence", 0.5)
    return data
