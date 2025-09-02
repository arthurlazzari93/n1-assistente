# app/bot.py
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from botbuilder.core import (
    ActivityHandler,
    TurnContext,
    ConversationState,
    StatePropertyAccessor,
    MemoryStorage,
)
from botbuilder.schema import ChannelAccount

from .movidesk_client import get_ticket_text_bundle
from .kb import kb_try_answer
from .ai.triage_agent import triage_next


class N1Bot(ActivityHandler):
    """
    Bot N1 com fluxo orientado por IA:
      - comando 'status'
      - comando 'iniciar <ticket>'
      - usa KB para uma resposta inicial rápida (se possível)
      - nas mensagens seguintes, a IA decide (RAG) e só cai no fallback da KB se o LLM não estiver disponível
      - histórico salvo em conversation_state, padronizado como {"role": "...", "text": "..."}
    """

    def __init__(self, conversation_state: Optional[ConversationState] = None) -> None:
        if conversation_state is None:
            # compat: se o main não injetar, criamos memória local
            conversation_state = ConversationState(MemoryStorage())
        self.conversation_state: ConversationState = conversation_state
        self.conv_accessor: StatePropertyAccessor = conversation_state.create_property("conv")

    # ---------------- util ----------------
    async def _save(self, turn_context: TurnContext, conv: Dict[str, Any]) -> None:
        await self.conv_accessor.set(turn_context, conv)  # type: ignore
        await self.conversation_state.save_changes(turn_context)

    def _is_stuck(self, text: str) -> bool:
        """Detecta sinais genéricos de frustração/trava (qualquer assunto)."""
        t = (text or "").lower()
        gatilhos = [
            "nao achei", "não achei", "nao encontro", "não encontro",
            "nao aparece", "não aparece", "nao funciona", "não funciona",
            "nao deu certo", "não deu certo", "nao estou achando", "não estou achando",
        ]
        return any(g in t for g in gatilhos)

    async def _triage_with_hint(self, conv: dict, ticket_ctx: dict, extra_hint: Optional[str]):
        """Chama o agente IA com histórico (+ dica genérica quando necessário)."""
        hist = list(conv.get("hist", []))
        if extra_hint:
            hist.append({"role": "user", "text": extra_hint})
        out = triage_next(hist, ticket_ctx)
        reply = out.get("message") or "Certo. Em qual tela/opção você está agora?"
        checklist = out.get("checklist") or []
        if checklist:
            reply += "\n\n" + "\n".join(f"- {p}" for p in checklist)
        return reply, out

    def _normalize_hist(self, conv: Dict[str, Any]) -> None:
        """Garante que todo item do histórico tem `text` string (evita null no LLM)."""
        conv.setdefault("hist", [])
        norm_hist: List[Dict[str, str]] = []
        for m in conv["hist"]:
            txt = m.get("text")
            if not isinstance(txt, str):
                txt = m.get("content") if isinstance(m.get("content"), str) else ""
            norm_hist.append({"role": (m.get("role") or "user"), "text": (txt or "")})
        conv["hist"] = norm_hist

    # ---------------- eventos ----------------
    async def on_members_added_activity(
        self, members_added: List[ChannelAccount], turn_context: TurnContext
    ):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity("✅ Online! Use `iniciar <ticket>` para começar.")

    # ---------------- mensagens ----------------
    async def on_message_activity(self, turn_context: TurnContext):
        text_raw: str = (turn_context.activity.text or "").strip()
        text = text_raw.lower()

        # carrega/normaliza estado
        conv: Dict[str, Any] = await self.conv_accessor.get(turn_context) or {}  # type: ignore
        conv.setdefault("flow", None)
        conv.setdefault("ticket", None)
        conv.setdefault("subject", "")
        conv.setdefault("ctx", "")
        self._normalize_hist(conv)

        # -------- comandos ----------
        if text == "status":
            await turn_context.send_activity("✅ Online! Use `iniciar <ticket>` para começar.")
            await self._save(turn_context, conv)
            return

        m = re.match(r"^iniciar\s+(\d+)$", text)
        if m:
            ticket_id = int(m.group(1))

            # pega assunto + primeira mensagem do ticket
            try:
                bundle = get_ticket_text_bundle(ticket_id)  # {subject, first_action_text/html}
            except Exception:
                bundle = {"subject": "", "first_action_text": "", "first_action_html": ""}

            conv.update({
                "flow": "triage",
                "ticket": ticket_id,
                "subject": bundle.get("subject") or "",
                "ctx": (bundle.get("first_action_text") or bundle.get("first_action_html") or "").strip(),
                "hist": [],  # zera histórico ao iniciar
            })

            await turn_context.send_activity(f"🚀 Iniciando fluxo para o ticket #{ticket_id}.")

            # 1) Tenta KB imediata com assunto + corpo inicial
            ctx_text = (f"{conv.get('subject')}\n{conv.get('ctx')}".strip())
            kb_hit = None
            try:
                if ctx_text:
                    kb_hit = kb_try_answer(ctx_text)
            except Exception:
                kb_hit = None

            if kb_hit:
                reply = (
                    f"Li seu chamado: **{conv.get('subject','')}**.\n\n"
                    f"{kb_hit['reply']}\n\n"
                    "Consegue tentar esses passos? Me diga se resolveu ou em qual etapa travou."
                )
                conv["hist"].append({"role": "assistant", "text": reply})
                await turn_context.send_activity(reply)
                await self._save(turn_context, conv)
                return

            # 2) Se a KB não cobrir, chama o agente IA já com o contexto do ticket
            ticket_ctx = {
                "id": ticket_id,
                "subject": conv.get("subject") or "",
                "first_action_text": conv.get("ctx") or "",
            }
            reply, out = await self._triage_with_hint(conv, ticket_ctx, extra_hint=None)
            conv["hist"].append({"role": "assistant", "text": reply})
            await turn_context.send_activity(reply)
            await self._save(turn_context, conv)
            return

        # -------- conversa em andamento ----------
        if conv.get("ticket"):
            ticket_ctx = {
                "id": conv.get("ticket"),
                "subject": conv.get("subject") or "",
                "first_action_text": conv.get("ctx") or "",
            }

            # registra a fala do usuário no histórico
            conv["hist"].append({"role": "user", "text": text_raw})

            # (A) Se o usuário sinalizou que travou, passamos um hint genérico para o agente
            hint = None
            if self._is_stuck(text_raw):
                hint = (
                    "O usuário relatou que não encontrou a opção/caminho ou que não deu certo. "
                    "Forneça um caminho alternativo SE existir OU faça UMA pergunta de desambiguação muito específica. "
                    "Não repita passos exatamente iguais. Use a KB para embasar a alternativa."
                )

            # 1) IA decide o próximo passo (responder/perguntar/pular/escalar)
            reply, out = await self._triage_with_hint(conv, ticket_ctx, hint)

            # 2) Proteção contra repetição: se a nova resposta for igual à anterior, peça ao agente uma alternativa
            last_assistant = next(
                (m["text"] for m in reversed(conv["hist"]) if m.get("role") == "assistant"),
                ""
            )
            if last_assistant.strip() == reply.strip():
                alt_hint = (
                    "A resposta anterior saiu igual. Agora NÃO repita. "
                    "Dê uma alternativa concreta (ex.: caminho diferente, tecla/menu alternativo) "
                    "OU faça uma pergunta de desambiguação específica e única. Curto e objetivo."
                )
                reply, out = await self._triage_with_hint(conv, ticket_ctx, alt_hint)

            conv["hist"].append({"role": "assistant", "text": reply})
            await turn_context.send_activity(reply)
            await self._save(turn_context, conv)
            return

        # sem ticket na conversa ainda
        await turn_context.send_activity("Para começar, me diga o número do chamado (ex.: `iniciar 12345`).")
        await self._save(turn_context, conv)
