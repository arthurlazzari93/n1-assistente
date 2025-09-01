# app/ai/triage_agent.py
from __future__ import annotations
import os, json, textwrap
from typing import List, Dict, Any
from openai import OpenAI

_CLIENT = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

SYSTEM_PROMPT = """
Você é um agente de Suporte N1 da Tecnogera. Seu objetivo é
ENTENDER o problema do usuário e GUIÁ-LO até a solução, perguntando
uma coisa por vez de forma clara, educada e objetiva.

Regras:
- Pergunte UMA coisa por vez (mensagem curta).
- Use o contexto do ticket (assunto/corpo) para já começar focado.
- Quando já houver passos viáveis: proponha um checklist curto (3-7 passos).
- Se perceber que precisa de acesso/credencial/permissão administrativa
  ou o caso não é N1 → ESCALONE.
- Não invente dados do ambiente; confirme com o usuário quando necessário.
- Responda em PT-BR.

Formato de saída (JSON estrito, sem texto fora do JSON):
{
  "action": "ask" | "resolve" | "escalate",
  "message": "<o que dizer ao usuário nesta rodada>",
  "checklist": ["passo 1", "passo 2", "..."],   // opcional
  "reason": "<motivo para escalar, se houver>",  // opcional
  "confidence": 0.0-1.0
}
"""

def _build_context(ticket: Dict[str, Any]) -> str:
    subj = (ticket.get("subject") or "").strip()
    body = (ticket.get("first_action_text") or ticket.get("first_action_html") or "").strip()
    body = body.replace("\n", " ").replace("\r", " ")
    if len(body) > 1200:
        body = body[:1200] + "..."
    return textwrap.dedent(f"""
        Ticket #{ticket.get("id")}
        Assunto: {subj or "(sem assunto)"}
        Primeira mensagem: {body or "(sem corpo)"}
    """).strip()

def _safe_json_loads(s: str) -> Dict[str, Any]:
    try:
        return json.loads(s)
    except Exception:
        # fallback robusto: embrulhar como "ask"
        return {"action": "ask", "message": s.strip(), "checklist": [], "confidence": 0.3}

def triage_next(history: List[Dict[str, str]], ticket: Dict[str, Any]) -> Dict[str, Any]:
    """
    history: lista de mensagens no formato [{"role":"user|assistant", "content":"..."}]
    ticket: {id, subject, first_action_text/first_action_html}
    """
    ctx = _build_context(ticket)
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Contexto do ticket:\n{ctx}"},
    ]
    msgs.extend(history)

    resp = _CLIENT.chat.completions.create(
        model=_MODEL,
        messages=msgs,
        temperature=0.2,
        max_tokens=500,
        response_format={"type": "text"},  # garantimos texto; o JSON vem no conteúdo
    )
    content = resp.choices[0].message.content or ""
    data = _safe_json_loads(content)
    # saneamento mínimo
    data.setdefault("action", "ask")
    data.setdefault("message", "Pode me contar um pouco mais sobre o que está acontecendo?")
    data.setdefault("checklist", [])
    data.setdefault("confidence", 0.4)
    return data
