# app/llm.py
import os, json
from typing import List, Optional
from pydantic import BaseModel, Field
from openai import OpenAI

MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "700"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

class LLMClassification(BaseModel):
    n1_candidate: bool = Field(default=False)
    confidence: float = Field(default=0.5, ge=0, le=1)
    rationale: str = Field(default="")
    suggested_service: Optional[str] = None
    suggested_category: Optional[str] = None
    suggested_urgency: Optional[str] = "Média"
    steps_to_resolve: List[str] = Field(default_factory=list)
    ask_user_questions: List[str] = Field(default_factory=list)
    admin_required: bool = Field(default=False)

def _build_prompt(subject: str, body: str) -> list[dict]:
    sys = (
        "Você é um analista de suporte N1 experiente. "
        "Decida se o usuário consegue resolver sozinho (N1) e gere um roteiro objetivo em PT-BR. "
        "Responda APENAS em JSON válido seguindo o schema: "
        "{n1_candidate: bool, confidence: number(0..1), rationale: string, "
        "suggested_service?: string, suggested_category?: string, suggested_urgency?: 'Baixa'|'Média'|'Alta', "
        "steps_to_resolve: string[], ask_user_questions: string[], admin_required: bool}."
    )
    user = f"""
TICKET:
Assunto: {subject or "(vazio)"}

Corpo:
{body or "(vazio)"}

INSTRUÇÕES:
- n1_candidate=true para problemas guiáveis (senha, Outlook, VPN, impressora, OneDrive/Teams básicos, etc.).
- Se exigir admin/AD/servidores/permissões, defina admin_required=true e n1_candidate=false.
- steps_to_resolve: lista de passos claros (imperativos e curtos).
- ask_user_questions: 2 a 5 perguntas triagem (curtas).
- confidence: 0..1.
- suggested_urgency: Baixa, Média ou Alta (padrão: Média).
- JSON estrito (sem comentários).
"""
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]

def _normalize_keys(data: dict) -> dict:
    """Aceita variações de nomes e preenche defaults."""
    def pick(*names, default=None):
        for n in names:
            if n in data:
                return data[n]
        return default

    norm = {}
    norm["n1_candidate"] = bool(pick("n1_candidate", "n1Candidate", "self_solve", default=False))
    norm["confidence"] = float(pick("confidence", "score", default=0.5))
    norm["rationale"] = pick("rationale", "reason", "justification", "rationale_text", default="")

    norm["suggested_service"] = pick("suggested_service", "service", "suggestedService", default=None)
    norm["suggested_category"] = pick("suggested_category", "category", "suggestedCategory", default=None)
    norm["suggested_urgency"] = pick("suggested_urgency", "urgency", "priority", "suggestedUrgency", default="Média")

    steps = pick("steps_to_resolve", "steps", "resolution_steps", default=[])
    if isinstance(steps, str):
        steps = [steps]
    norm["steps_to_resolve"] = steps or []

    qs = pick("ask_user_questions", "questions", "clarifying_questions", default=[])
    if isinstance(qs, str):
        qs = [qs]
    norm["ask_user_questions"] = qs or []

    norm["admin_required"] = bool(pick("admin_required", "adminRequired", "needs_admin", default=False))

    return norm

def classify_ticket_with_llm(subject: str, body: str) -> LLMClassification:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY ausente no .env")
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=_build_prompt(subject, body),
        temperature=0.2,
        max_tokens=MAX_TOKENS,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content

    # Parse tolerante
    try:
        data = json.loads(raw)
    except Exception:
        raw2 = raw.strip().strip("```").strip()
        data = json.loads(raw2)

    data = _normalize_keys(data)
    return LLMClassification(**data)
