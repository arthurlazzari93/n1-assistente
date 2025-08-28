# app/bot.py
import re
from botbuilder.core import ActivityHandler, TurnContext, ConversationState, MessageFactory

class N1Bot(ActivityHandler):
    def __init__(self, conversation_state: ConversationState):
        self.conversation_state = conversation_state

    async def on_message_activity(self, turn_context: TurnContext):
        text = (turn_context.activity.text or "" ).strip()
        low = text.lower()

        if low == "status":
            await turn_context.send_activity("✅ Online! Use `iniciar <ticket>` para começar.")
            return

        m = re.match(r"^iniciar\s+(\d+)$", low)
        if m:
            ticket_id = int(m.group(1))
            await turn_context.send_activity(f"🚀 Iniciando fluxo para o ticket **#{ticket_id}**.")
            return

        await turn_context.send_activity(
            "Não entendi. Comandos: `status` ou `iniciar <número>`."
        )
