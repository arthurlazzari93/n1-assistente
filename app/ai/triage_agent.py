# triage_agent.py
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

# LLM (usamos direto o SDK; se preferir, pode trocar pelo seu wrapper em llm.py)
try:
    from openai import OpenAI  # pip install openai
except Exception:
    OpenAI = None  # type: ignore

from ..kb import search as kb_search, kb_try_answer
from ..summarizer import extract_steps
from ..learning import get_priors

# ---- Config (com defaults seguros) ----
try:
    from ..config import KB_MIN_SCORE as _KB_MIN_SCORE_DEFAULT  # type: ignore
except Exception:
    _KB_MIN_SCORE_DEFAULT = 2.0

try:
    from ..config import KB_TOP_K as _KB_TOP_K_DEFAULT  # type: ignore
except Exception:
    _KB_TOP_K_DEFAULT = 2

_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
_LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
_CLIENT = OpenAI(api_key=_OPENAI_API_KEY) if (_OPENAI_API_KEY and OpenAI) else None

# ---- Intents suportadas (podemos expandir sem quebrar) ----
INTENT_DESCRIPTIONS = {
    "signature.generate": "Gerar/baixar imagem da assinatura corporativa.",
    "signature.configure": "Configurar assinatura no Outlook (novo ou classico).",
    "password.reset": "Resetar senha de AD/Windows/E-mail ou conta bloqueada por senha incorreta.",
    "outlook.issue": "Problemas gerais no Outlook (UI, travamentos, sem relacao com entrega).",
    "email.delivery_issue": "Conta nao envia/recebe, mensagens ficam na caixa de saida ou retornam.",
    "email.access_blocked": "Usuario com senha correta mas caixa bloqueada/licenca desativada/MFA pendente.",
    "vpn.access": "Conexao/autenticacao VPN (GlobalProtect, FortiClient, etc.).",
    "onedrive.sync_issue": "Sincronizacao OneDrive/SharePoint presa ou arquivos com status amarelo.",
    "sharepoint.permission_issue": "Sem acesso/permissao em sites ou bibliotecas SharePoint.",
    "printer.install_driver": "Implantacao de impressora, mapeamento e drivers.",
    "printer.queue_stuck": "Fila/spool travados, trabalhos presos ou duplicados.",
    "internal_system.access": "Acesso/perfil em sistemas internos (ERP, CRM, SISLOC, SAP, TOTVS...).",
    "other": "Fallback genérico para itens fora da taxonomia.",
}
INTENT_LABELS = list(INTENT_DESCRIPTIONS.keys())
# Resumo da evolucao da taxonomia/KB:
# - Novas intents para OneDrive/SharePoint, impressoras, sistemas internos e e-mail corporativo.
# - KB expandida com: onedrive_sincronizacao.md, sharepoint_permissoes.md,
#   impressora_instalacao_driver.md, impressora_fila_travada.md,
#   sistema_erp_acesso.md, email_envio_recebimento.md e email_acesso_bloqueado.md.

SYSTEM_PROMPT = """
Você é um Agente de Suporte N1 da Tecnogera.
Objetivo: resolver com o MENOR número de interações, usando a Base de Conhecimento (KB).

Diretrizes:
- Faça no MÁXIMO 1 pergunta por vez, apenas quando faltar contexto.
- Seja simpática, acolhedora e confiante: agradeça ou valide o relato antes de orientar o próximo passo.
- Use frases em tom positivo ("vamos resolver juntos", "fico aqui aguardando") e evite jargões frios.
- Sempre que fornecer passo-a-passo, finalize com: "Funcionou? Responda Sim ou Não."
- Se envolver permissões/AD/servidores → action="escalate" e explique em "reason".
- Respostas curtas, PT-BR. Use KB/caminhos exatos quando houver.

Formato de resposta (JSON):
{
  "action": "answer" | "ask" | "resolve" | "escalate",
  "message": "texto curto em PT-BR",
  "checklist": ["passo 1", "passo 2"],
  "confidence": 0.0,
  "reason": "motivo (se action=escalate)"
}
""".strip()


# --------------------------------------------------------------------------------------
# Utilitários de contexto
# --------------------------------------------------------------------------------------

def _ticket_context(ticket: Dict[str, Any]) -> str:
    subj = (ticket.get("subject") or "").strip()
    body = (
        ticket.get("first_action_text")
        or ticket.get("first_action_html")
        or ticket.get("description")
        or ""
    ).strip()
    body = body.replace("\r", " ").replace("\n", " ")
    if len(body) > 1500:
        body = body[:1500] + "..."
    return f"Ticket #{ticket.get('id')}\nAssunto: {subj or '(sem assunto)'}\nPrimeira mensagem: {body or '(sem corpo)'}"


def _history_as_msgs(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []
    for m in history or []:
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


def _safe_json_loads(s: str) -> Dict[str, Any]:
    try:
        return json.loads(s)
    except Exception:
        return {
            "action": "ask",
            "message": "Quero te ajudar certinho! Em qual tela ou etapa você está agora?",
            "checklist": [],
            "confidence": 0.4,
        }


_GREETINGS = ("oi", "olá", "ola", "bom dia", "boa tarde", "boa noite", "hey", "hi", "hello")


def _is_greeting(text: str) -> bool:
    """
    Detecta saudações curtas (sem contexto adicional).
    """
    if not text:
        return False
    normalized = text.strip().lower()
    if len(normalized.split()) > 7:
        return False
    return any(normalized.startswith(greet) for greet in _GREETINGS)


# --------------------------------------------------------------------------------------
# Classificação de intenção
# --------------------------------------------------------------------------------------

def _classify_intent_heuristic(text: str) -> Dict[str, Any]:
    """Fallback leve em PT-BR quando não houver LLM."""
    t = (text or "").lower()

    def has_any(*needles: str) -> bool:
        return any(n in t for n in needles if n)

    email_context = has_any("e-mail", "email", "mailbox", "outlook")
    email_blocked = email_context and has_any("bloque", "suspens", "licenc", "mfa", "autenticação", "conta desativada")

    if "assinatura" in t and has_any("criar", "gerar", "png", "imagem"):
        return {"intent": "signature.generate", "confidence": 0.65}
    if "assinatura" in t and has_any("configurar", "outlook", "opções", "options", "novo", "clássico"):
        return {"intent": "signature.configure", "confidence": 0.6}
    if has_any("reset", "redefinir", "esqueci", "senha", "password"):
        if email_blocked:
            return {"intent": "email.access_blocked", "confidence": 0.6}
        return {"intent": "password.reset", "confidence": 0.6}

    if has_any("onedrive", "one drive", "sharepoint"):
        if has_any("sincron", "sync", "pendente", "travado", "ícone amarelo", "status amarelo", "upload", "arquivos em espera"):
            return {"intent": "onedrive.sync_issue", "confidence": 0.62}
        if has_any("permiss", "acesso", "compartilh", "liberar", "site", "biblioteca"):
            return {"intent": "sharepoint.permission_issue", "confidence": 0.58}

    if has_any("impressora", "printer", "multifuncional"):
        if has_any("instal", "driver", "deploy", "mapear", "adicionar", "instalar impressora"):
            return {"intent": "printer.install_driver", "confidence": 0.6}
        if has_any("fila", "spool", "trav", "spooler", "limpar fila", "cancelar fila", "fila presa", "reiniciar spool"):
            return {"intent": "printer.queue_stuck", "confidence": 0.58}

    if has_any("erp", "crm", "protheus", "sap", "totvs", "sisloc", "salesforce", "dynamics", "sistema interno", "sistema corporativo"):
        return {"intent": "internal_system.access", "confidence": 0.6}

    if has_any("vpn"):
        return {"intent": "vpn.access", "confidence": 0.55}

    if email_context:
        if email_blocked:
            return {"intent": "email.access_blocked", "confidence": 0.58}
        if has_any("não envia", "nao envia", "não recebe", "nao recebe", "retorna", "bounce", "caixa de saída", "caixa de saida", "outbox", "fila"):
            return {"intent": "email.delivery_issue", "confidence": 0.55}
        return {"intent": "outlook.issue", "confidence": 0.5}

    return {"intent": "other", "confidence": 0.4}


def classify_intent(text: str) -> Dict[str, Any]:
    """
    Classifica o texto em uma das INTENT_LABELS.
    Usa LLM (json) quando disponível; caso contrário, heurística leve.
    Retorna: {"intent": str, "confidence": float}
    """
    if not _CLIENT:
        return _classify_intent_heuristic(text)

    intent_lines = "\n".join(f"- {label}: {desc}" for label, desc in INTENT_DESCRIPTIONS.items())
    user_prompt = (
        "Classifique o pedido abaixo na intent mais específica disponível.\n"
        "Use 'other' apenas quando nenhuma descrição abaixo fizer sentido.\n"
        f"Intents disponíveis:\n{intent_lines}\n\n"
        "Responda em JSON: {\"intent\": \"<label>\", \"confidence\": 0.0..1.0}\n\n"
        f"Texto:\n{text}"
    )
    try:
        resp = _CLIENT.chat.completions.create(
            model=_LLM_MODEL,
            messages=[
                {"role": "system", "content": "Você é um classificador de intenção conciso."},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=200,
        )
        data = _safe_json_loads(resp.choices[0].message.content or "{}")
        intent = data.get("intent") or "other"
        if intent not in INTENT_LABELS:
            intent = "other"
        conf = float(data.get("confidence") or 0.5)
        return {"intent": intent, "confidence": conf}
    except Exception:
        return _classify_intent_heuristic(text)


# --------------------------------------------------------------------------------------
# Reranking com LLM
# --------------------------------------------------------------------------------------

def rerank_with_llm(query: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Recebe os Top-K da KB e pede ao LLM para escolher o MELHOR (ou ranquear).
    Retorna hits reordenados (melhor primeiro). Fallback = lista original.
    """
    if not _CLIENT or not hits:
        return hits

    # resumimos cada hit (título + até 3 passos) para reduzir custo e enviesamento
    cand_lines = []
    for i, h in enumerate(hits):
        title = h.get("doc_title") or "Documento"
        chunk = (h.get("chunk_text") or "").strip()
        steps = extract_steps(chunk, max_steps=3)
        short = " | ".join(steps) if steps else (chunk[:220] + ("..." if len(chunk) > 220 else ""))
        cand_lines.append(f"[{i}] {title}: {short}")

    prompt = (
        "Selecione o candidato MAIS útil para responder ao pedido (0-index).\n"
        "Considere correspondência exata ao objetivo do usuário e clareza dos passos.\n"
        "Responda apenas em JSON: {\"best_index\": <int>, \"scores\": [0..100], \"reason\": \"...\"}\n\n"
        f"Pedido:\n{query}\n\n"
        "Candidatos:\n" + "\n".join(cand_lines)
    )

    try:
        resp = _CLIENT.chat.completions.create(
            model=_LLM_MODEL,
            messages=[
                {"role": "system", "content": "Você é um reranker objetivo em PT-BR."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=300,
        )
        data = _safe_json_loads(resp.choices[0].message.content or "{}")
        idx = int(data.get("best_index", 0))
        idx = min(max(idx, 0), len(hits) - 1)
        best = hits[idx]
        others = [h for j, h in enumerate(hits) if j != idx]
        return [best] + others
    except Exception:
        return hits


# --------------------------------------------------------------------------------------
# KB Contexto curto para o LLM
# --------------------------------------------------------------------------------------

def _kb_context_from_hits(hits: List[Dict[str, Any]], k: int) -> str:
    """
    Monta um contexto compacto: apenas título + até 3 passos por hit.
    """
    if not hits:
        return "KB: (sem resultados relevantes)"
    lines = ["KB (resumos curtos):"]
    for i, h in enumerate(hits[:k], start=1):
        title = h.get("doc_title") or "Documento"
        chunk = (h.get("chunk_text") or "").strip()
        steps = extract_steps(chunk, max_steps=3)
        short = " | ".join(steps) if steps else (chunk[:280] + ("..." if len(chunk) > 280 else ""))
        lines.append(f"[{i}] {title}: {short}")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Função principal do agente
# --------------------------------------------------------------------------------------

def triage_next(history: List[Dict[str, Any]], ticket: Dict[str, Any]) -> Dict[str, Any]:
    """
    Decide o próximo passo usando: Classificação de intenção → KB (BM25 + priors) → Reranker LLM → LLM para resposta.
    Retorno mantém contrato atual e adiciona best_doc_path para feedback posterior.
    """
    # 1) Query canônica com contexto do ticket
    last_user = next((m.get("text") for m in reversed(history or []) if m.get("role") == "user"), "")
    query = f"{ticket.get('subject','')}\n{ticket.get('first_action_text') or ticket.get('description') or ''}\n{last_user}".strip()

    # 2) Classificar intenção
    intent_data = classify_intent(query)
    intent = intent_data.get("intent") or "other"

    # 3) Priors (aprendizado com feedback)
    priors = get_priors(intent=intent)

    # 4) Buscar na KB com priors (Top-K maior para reranking)
    topk = max(3, int(_KB_TOP_K_DEFAULT) * 3)
    threshold = float(_KB_MIN_SCORE_DEFAULT or 2.0)
    hits = kb_search(query, k=topk, threshold=threshold, priors=priors)

    # 5) Se nada relevante → perguntar 1 detalhe
    if not hits:
        last_user_msg = (last_user or "").strip()
        if _is_greeting(last_user_msg):
            message = "Oi! Que bom falar com você. Me conta rapidinho o que está acontecendo e eu te ajudo."
        else:
            detail = ""
            if ticket.get("subject"):
                detail = f" sobre \"{ticket['subject'][:80]}\""
            message = f"Quero te ajudar com isso{detail}. Pode me explicar melhor o que está acontecendo?"
        return {
            "action": "ask",
            "message": message,
            "checklist": [],
            "confidence": 0.45,
        }

    # 6) Reranking com LLM (melhor primeiro)
    hits = rerank_with_llm(query, hits)

    # 7) Preparar contexto de KB enxuto (título + steps)
    kb_ctx = _kb_context_from_hits(hits, k=max(2, _KB_TOP_K_DEFAULT))

    # 8) Sem LLM → fallback curto usando a própria KB
    if not _CLIENT:
        best = hits[0]
        fallback = kb_try_answer(query, threshold=threshold, priors=priors)
        if fallback:
            return {
                "action": "answer",
                "message": fallback["reply"],
                "checklist": [s["title"] for s in fallback.get("sources", [])],
                "confidence": 0.55,
                "best_doc_path": best.get("doc_path"),
                "intent": intent,
            }
        # improvável (já temos hits), mas por segurança:
        return {
            "action": "answer",
            "message": kb_ctx + "\n\nMe diga em qual tela você está agora que eu te guio o próximo passo.",
            "checklist": [],
            "confidence": 0.5,
            "best_doc_path": hits[0].get("doc_path"),
            "intent": intent,
        }

    # 9) Gerar resposta curta com LLM usando o contexto + histórico
    msgs: List[Dict[str, str]] = []
    msgs.append({"role": "system", "content": SYSTEM_PROMPT})
    msgs.append({"role": "user", "content": _ticket_context(ticket)})
    msgs.append({"role": "user", "content": f"Intent detectada: {intent} (use este objetivo)."})
    msgs.append({"role": "user", "content": kb_ctx})
    msgs.extend(_history_as_msgs(history))

    resp = _CLIENT.chat.completions.create(
        model=_LLM_MODEL,
        messages=msgs,
        temperature=0.2,
        max_tokens=600,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    data = _safe_json_loads(content)

    # saneamento e metadados para feedback
    data.setdefault("action", "answer")
    data.setdefault("message", "Certo! Vou te guiar. Em qual tela/opção você está agora?")
    data.setdefault("checklist", [])
    data.setdefault("confidence", 0.5)
    data["best_doc_path"] = hits[0].get("doc_path")
    data["intent"] = intent
    return data


def ia_generate_message(prompt: str, temperature: float = 0.4, max_tokens: int = 240) -> str:
    """
    Mensagem auxiliar para gerar a primeira fala do bot via LLM.
    Retorna string vazia se não houver modelo configurado ou em caso de erro.
    """
    text = (prompt or "").strip()
    if not text or not _CLIENT:
        return ""
    try:
        resp = _CLIENT.chat.completions.create(
            model=_LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "Você cria mensagens iniciais curtas, simpáticas e profissionais em PT-BR.",
                },
                {"role": "user", "content": text},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        msg = resp.choices[0].message.content or ""
        return msg.strip()
    except Exception:
        return ""
