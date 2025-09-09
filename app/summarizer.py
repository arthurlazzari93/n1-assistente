# app/summarizer.py
from __future__ import annotations

import os
import re
from typing import Optional

# OpenAI é opcional: só usamos se OPENAI_API_KEY estiver definido
_OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()
_CLIENT = None
if _OPENAI_KEY:
    try:
        from openai import OpenAI  # pip install openai
        _CLIENT = OpenAI(api_key=_OPENAI_KEY)
    except Exception:
        _CLIENT = None


def _clean_text(t: str) -> str:
    if not t:
        return ""
    # remove HTML básico e normaliza espaços
    t = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", t)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    return t.strip()


def _heuristic_summary(text: str, max_chars: int = 900) -> str:
    """
    Resumo leve quando não há LLM: pega primeiras frases e bullets do texto.
    """
    text = _clean_text(text)
    if not text:
        return "Resumo: (sem conteúdo para sumarizar)."

    # coleta bullets explícitos
    bullets = []
    for line in text.splitlines():
        ln = line.strip()
        if re.match(r"^[-*•]\s+", ln):
            bullets.append(ln)

    # coleta frases principais (primeiras 5–7)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    core = " ".join(sentences[:6]).strip()

    # monta
    parts = []
    if core:
        parts.append(core)
    if bullets:
        parts.append("\nPrincipais pontos:\n" + "\n".join(f"• {re.sub(r'^[-*•]\s+', '', b)}" for b in bullets[:6]))

    out = ("\n\n".join(parts)).strip()
    if len(out) > max_chars:
        out = out[: max_chars - 1].rstrip() + "…"
    return out or "Resumo: (conteúdo insuficiente)."


def summarize_conversation(transcript: str, max_words: int = 180) -> str:
    """
    Gera um resumo curto e objetivo da conversa para registrar no ticket.
    - Se OPENAI_API_KEY existir, usa LLM (chat.completions) com portugues.
    - Senão, aplica um resumo heurístico local.
    """
    transcript = (transcript or "").strip()
    if not transcript:
        return "Resumo: (sem conversação registrada)."

    # Caminho LLM (opcional)
    if _CLIENT:
        try:
            prompt_system = (
                "Você é um assistente de suporte N1. Gere um resumo curto, objetivo e em português do Brasil "
                "para ser registrado no histórico do chamado. "
                "Formato sugerido:\n"
                "Assunto (em 1 linha)\n"
                "O que foi verificado / tentado (bullets)\n"
                "Orientações / próximos passos (bullets)\n"
                "Se houver links ou caminhos de menu, inclua-os de forma clara. "
                f"Limite a {max_words} palavras."
            )
            messages = [
                {"role": "system", "content": prompt_system},
                {
                    "role": "user",
                    "content": (
                        "Transcrição/conteúdo a resumir (PT-BR):\n"
                        "---------------------------------------\n"
                        f"{transcript}\n"
                        "---------------------------------------"
                    ),
                },
            ]
            resp = _CLIENT.chat.completions.create(
                model=os.getenv("OPENAI_SUMMARY_MODEL", "gpt-4o-mini"),
                messages=messages,
                temperature=0.2,
                max_tokens=500,
            )
            text = (resp.choices[0].message.content or "").strip()
            return text or _heuristic_summary(transcript)
        except Exception:
            # Qualquer falha no provider → heurístico
            return _heuristic_summary(transcript)

    # Sem chave → heurístico
    return _heuristic_summary(transcript)


__all__ = ["summarize_conversation"]
