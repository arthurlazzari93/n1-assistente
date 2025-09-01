# app/bot.py
from __future__ import annotations
import re
from typing import Dict, Any, List

from botbuilder.core import ActivityHandler, TurnContext, ConversationState
from botbuilder.schema import ChannelAccount
from loguru import logger

from app.triage_agent import TriageAgent


def _new_state() -> Dict[str, Any]:
    return {
        "stage": "idle",        # idle | triage
        "ticket_id": None,      # int | None
        "history": [],          # [{role, content}]
    }


class N1Bot(ActivityHandler):
    def __init__(self, conversation_state: ConversationState) -> None:
        super().__init__()
        self.conversation_state = conversation_state
        self.conv_accessor = self.conversation_state.create_property("N1ConvState")
        self.agent = TriageAgent()

    async def on_members_added_activity(
        self, members_added: List[ChannelAccount], turn_context: TurnContext
    ):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity(
                    "✅ Online! Use `iniciar <ticket>` para começar."
                )

    async def on_message_activity(self, turn_context: TurnContext):
        conv: Dict[str, Any] = await self.conv_accessor.get(turn_context, _new_state())

        text_raw: str = (turn_context.activity.text or "").strip()
        text = text_raw.lower()

        logger.info(f"[BOT] msg='{text_raw}' stage={conv.get('stage')} ticket={conv.get('ticket_id')}")

        # Comandos utilitários
        if text in ("status", "/status"):
            await turn_context.send_activity("✅ Online! Use `iniciar <ticket>` para começar.")
            return

        if text in ("reset", "/reset"):
            conv = _new_state()
            await self.conv_accessor.set(turn_context, conv)
            await self.conversation_state.save_changes(turn_context)
            await turn_context.send_activity("♻️ Estado limpo. Pronto para recomeçar.")
            return

        # Iniciar fluxo: "iniciar 12345"
        m = re.match(r"^(iniciar|comecar|começar)\s+(\d+)$", text)
        if m:
            ticket_id = int(m.group(2))
            conv["stage"] = "triage"
            conv["ticket_id"] = ticket_id
            conv["history"] = []
            await self.conv_accessor.set(turn_context, conv)
            await self.conversation_state.save_changes(turn_context)

            await turn_context.send_activity(f"🚀 Iniciando fluxo para o ticket #{ticket_id}.")
            await turn_context.send_activity(
                "Em uma frase, descreva o problema que você está enfrentando."
            )
            return

        # Fluxo de triagem (agora com IA)
        if conv.get("stage") == "triage":
            # registra mensagem do usuário no histórico
            conv["history"].append({"role": "user", "content": text_raw})

            result = self.agent.next(
                history=conv["history"],
                user_message=text_raw,
                ticket_id=conv.get("ticket_id"),
            )

            # registra resposta do assistente
            conv["history"].append({"role": "assistant", "content": result.reply})
            await turn_context.send_activity(result.reply)

            if result.done:
                await turn_context.send_activity("✅ Fechando a triagem. Se precisar, digite `iniciar <ticket>` novamente.")
                conv = _new_state()

            await self.conv_accessor.set(turn_context, conv)
            await self.conversation_state.save_changes(turn_context)
            return

        # Default
        await turn_context.send_activity(
            "Oi! Use `status` ou `iniciar <ticket>`. Você também pode digitar `reset`."
        )
