# app/triage_agent.py
from __future__ import annotations
import os, json, textwrap
from typing import List, Dict, Any

from openai import OpenAI

# RAG: buscamos na KB internamente
from .kb import search as kb_search, kb_try_answer

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

_CLIENT = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

SYSTEM_PROMPT = """
Você é um Agente de Suporte N1 da Tecnogera.
Objetivo: resolver o chamado com o MENOR número de interações, usando a Base de Conhecimento (KB) fornecida.

Diretrizes:
- Use o contexto do ticket (assunto/corpo) para já começar focado.
- Se a KB já traz o procedimento, vá direto ao passo-a-passo. Evite perguntas desnecessárias.
- Faça no MÁXIMO 1 pergunta por vez, curta e objetiva, apenas quando for necessário para escolher o próximo passo.
- Inclua links ou caminhos exatos se estiverem na KB.
- Se o usuário “fugir do assunto”, puxe educadamente de volta para o tema do ticket.
- Se exigir acesso/permissão de administrador, políticas do AD/servidores, ou outra ação não N1 → ESCALONE.
- Mantenha as respostas curtas e claras, em PT-BR. Não invente informações fora da KB.

Formato de saída: responda APENAS este JSON (sem nenhum texto fora do JSON):
{
  "action": "answer" | "ask" | "resolve" | "escalate",
  "message": "texto curto em PT-BR para o usuário",
  "checklist": ["passo 1", "passo 2", "..."],
  "confidence": 0.0,
  "reason": "motivo da escalada (se action=escalate)"
}
"""

def _ticket_context(ticket: Dict[str, Any]) -> str:
    subj = (ticket.get("subject") or "").strip()
    body = (ticket.get("first_action_text") or ticket.get("first_action_html") or ticket.get("description") or "").strip()
    body = body.replace("\r", " ").replace("\n", " ")
    if len(body) > 1500:
        body = body[:1500] + "..."
    return f"Ticket #{ticket.get('id')}\nAssunto: {subj or '(sem assunto)'}\nPrimeira mensagem: {body or '(sem corpo)'}"

def _history_as_msgs(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    # history esperado: [{"role":"user"|"assistant","text": "..."}]
    out: List[Dict[str, str]] = []
    for m in history:
        role = m.get("role") or "user"
        content = m.get("text") or ""
        # limitamos um pouco o tamanho por segurança
        if len(content) > 1200:
            content = content[:1200] + "..."
        out.append({"role": "assistant" if role == "assistant" else "user", "content": content})
    return out

def _kb_context(query: str, k: int = 6) -> str:
    """
    Monta um bloco de contexto com os melhores trechos da KB.
    """
    hits = kb_search(query, k=k) or []
    if not hits:
        return "KB: (sem resultados relevantes)"
    lines = ["KB (trechos relevantes):"]
    for i, h in enumerate(hits[:6], start=1):
        title = h["doc_title"]
        chunk = h["chunk_text"].strip()
        # encurta cada trecho pra caber no prompt
        if len(chunk) > 700:
            chunk = chunk[:700] + "..."
        lines.append(f"[{i}] Título: {title}\n{chunk}")
    return "\n\n".join(lines)

def _safe_json(s: str) -> Dict[str, Any]:
    try:
        return json.loads(s)
    except Exception:
        # fallback tolerante
        return {
            "action": "answer",
            "message": s.strip()[:800],
            "checklist": [],
            "confidence": 0.3,
        }

def triage_next(history: List[Dict[str, str]], ticket: Dict[str, Any]) -> Dict[str, Any]:
    """
    Decide o próximo passo da conversa usando KB + LLM.
    history: [{"role":"user"|"assistant", "text":"..."}]
    ticket: {"id","subject","first_action_text"/"first_action_html"/"description"}
    """
    # último texto do usuário (se houver) melhora a busca
    last_user = next((m["text"] for m in reversed(history) if m.get("role") == "user"), "")
    query = f"{ticket.get('subject','')}\n{ticket.get('first_action_text') or ticket.get('description') or ''}\n{last_user}".strip()
    kb_ctx = _kb_context(query)

    # Se não há chave da OpenAI, faça um fallback usando apenas KB
    if _CLIENT is None:
        hit = kb_try_answer(query)
        if hit:
            return {
                "action": "answer",
                "message": hit["reply"],
                "checklist": [s["title"] for s in hit["sources"]],
                "confidence": 0.55,
            }
        # nenhum resultado — faça uma pergunta mínima
        return {
            "action": "ask",
            "message": "Me diga em qual tela/opção você está agora que eu te guio o próximo passo.",
            "checklist": [],
            "confidence": 0.35,
        }

    msgs: List[Dict[str, str]] = []
    msgs.append({"role": "system", "content": SYSTEM_PROMPT})
    msgs.append({"role": "user", "content": f"Contexto do ticket:\n{_ticket_context(ticket)}"})
    msgs.append({"role": "user", "content": kb_ctx})
    msgs.extend(_history_as_msgs(history))

    resp = _CLIENT.chat.completions.create(
        model=MODEL,
        messages=msgs,
        temperature=0.2,
        max_tokens=600,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or ""
    data = _safe_json(content)

    # saneamento de campos
    data.setdefault("action", "answer")
    data.setdefault("message", "Certo! Vou te guiar. Em qual tela/opção você está agora?")
    data.setdefault("checklist", [])
    data.setdefault("confidence", 0.5)

    return data
