# app/triage_agent.py
from __future__ import annotations
import os, json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel

try:
    # SDK novo da OpenAI (v1.x)
    from openai import OpenAI
except Exception:
    OpenAI = None  # tipo: ignore


class AgentOutput(BaseModel):
    reply: str
    done: bool = False
    next_action: Optional[str] = None
    reason: Optional[str] = None


class TriageAgent:
    """
    Agente de triagem conversacional:
    - Se OPENAI_API_KEY existir, usa o modelo (default: gpt-4o-mini)
    - Senão, responde com heurística simples (fallback)
    """

    def __init__(self) -> None:
        self.enabled = bool(os.getenv("OPENAI_API_KEY")) and OpenAI is not None
        self.model = os.getenv("TRIAGE_MODEL", "gpt-4o-mini")
        self.client = OpenAI() if self.enabled else None

    def next(
        self,
        history: List[Dict[str, str]],
        user_message: str,
        ticket_id: Optional[int] = None,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> AgentOutput:
        if not self.enabled:
            # --------- Fallback sem IA (regras simples) ----------
            text = (user_message or "").lower()

            if "assinatura" in text and ("email" in text or "outlook" in text):
                steps = (
                    "Vamos configurar a assinatura no Outlook:\n"
                    "1) Abra o Outlook > Arquivo > Opções > Email > Assinaturas.\n"
                    "2) Clique em 'Novo', dê um nome e cole o conteúdo.\n"
                    "3) Em 'Escolher assinaturas padrão', selecione a conta e defina para 'Novas mensagens' e 'Respostas/encaminhamentos'.\n"
                    "Me avise se aparecer algo diferente."
                )
                return AgentOutput(reply=steps, done=False, next_action="outlook_signature_steps")

            # perguntas de diagnóstico padrão
            reply = (
                "Obrigado! Para entender melhor:\n"
                "• O que você esperava que acontecesse?\n"
                "• Quando começou o problema?\n"
                "• Houve alguma mudança recente (senha, PC, rede, instalação)?\n"
                "• Isso ocorre com você ou com mais pessoas?"
            )
            return AgentOutput(reply=reply, done=False)

        # --------- IA (OpenAI) ----------
        sys = (
            "Você é um agente de helpdesk de TI (português). "
            "Conduza triagem objetiva, com perguntas focadas e passos concretos. "
            "Responda curto (<=5 linhas). Quando puder, dê instruções práticas. "
            "Se concluir que já há orientação suficiente ou precisa escalar, marque done=true. "
            "Responda em JSON com chaves: reply (string), done (bool), next_action (string opcional)."
        )

        messages = [{"role": "system", "content": sys}]
        for m in history:
            # esperados: {"role": "user"|"assistant", "content": "..."}
            if m.get("role") in ("user", "assistant") and m.get("content"):
                messages.append({"role": m["role"], "content": m["content"]})
        # contexto opcional do ticket
        if ticket_id:
            messages.append({"role": "system", "content": f"Ticket atual: #{ticket_id}."})
        if extra_context:
            messages.append({"role": "system", "content": f"Contexto: {json.dumps(extra_context)[:1500]}"})

        messages.append({"role": "user", "content": user_message or ""})

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        txt = resp.choices[0].message.content or "{}"

        try:
            data = json.loads(txt)
            return AgentOutput(**data)
        except Exception:
            # Se o modelo não devolver JSON válido, devolve o texto bruto
            return AgentOutput(reply=txt, done=False)
