import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import db

try:
    from app.bot import N1Bot  # type: ignore
except Exception:  # pragma: no cover
    N1Bot = None  # type: ignore


class DummyAccessor:
    def __init__(self, storage: dict, name: str) -> None:
        self.storage = storage
        self.name = name

    def _bucket(self):
        return self.storage.setdefault(self.name, {})

    async def get(self, turn_context, default=None):  # type: ignore
        user_id = getattr(turn_context.activity.from_property, "id", "default")  # type: ignore[attr-defined]
        return self._bucket().get(user_id, default)

    async def set(self, turn_context, value):  # type: ignore
        user_id = getattr(turn_context.activity.from_property, "id", "default")
        self._bucket()[user_id] = value


class DummyConversationState:
    def __init__(self) -> None:
        self.storage: dict = {}

    def create_property(self, name: str) -> DummyAccessor:
        return DummyAccessor(self.storage, name)

    async def save_changes(self, turn_context):  # type: ignore
        return None


class FakeTurnContext:
    def __init__(self, text: str, user_id: str = "user-1", teams_id: str = "teams-user-1", email: str = "tester@example.com") -> None:
        self.activity = SimpleNamespace(
            text=text,
            from_property=SimpleNamespace(
                id=user_id,
                aad_object_id=teams_id,
                email=email,
                name="Tester",
                additional_properties={},
            ),
            recipient=SimpleNamespace(id="bot"),
            channel_data={},
        )
        self.sent_messages: list[str] = []

    async def send_activity(self, message: str):
        self.sent_messages.append(message)


@unittest.skipIf(N1Bot is None, "Dependências do Bot Framework indisponíveis")
class BotSessionIntegrationTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(prefix="bot_session_", suffix=".db")
        os.close(fd)
        self.temp_db_path = path
        self.old_db_path = db.DB_PATH
        db.DB_PATH = self.temp_db_path
        db.init_db()
        self.conv_state = DummyConversationState()
        patcher_create = patch("app.bot.create_resolved_movidesk_ticket_from_session", return_value="T-9000")
        patcher_summary = patch("app.bot.build_chat_session_summary", return_value="Resumo teste")
        self.mock_create_ticket = patcher_create.start()
        self.mock_build_summary = patcher_summary.start()
        self.addCleanup(patcher_create.stop)
        self.addCleanup(patcher_summary.stop)
        self.bot = N1Bot(conversation_state=self.conv_state)  # type: ignore

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    def _run_bot(self, ctx: FakeTurnContext):
        asyncio.run(self.bot.on_message_activity(ctx))  # type: ignore

    def test_chat_session_creation_and_updates(self):
        ctx1 = FakeTurnContext("Preciso de ajuda com assinatura", user_id="user-chat")
        self._run_bot(ctx1)

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, type, status, last_user_message_at FROM sessions")
            row = cur.fetchone()
        self.assertIsNotNone(row)
        session_id, sess_type, status, first_user_ts = row
        self.assertEqual(sess_type, "chat_driven")
        self.assertEqual(status, "em_andamento")
        self.assertIsNotNone(first_user_ts)

        ctx2 = FakeTurnContext("Obrigado!", user_id="user-chat")
        self._run_bot(ctx2)
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT last_user_message_at FROM sessions WHERE id=?", (session_id,))
            updated_ts = cur.fetchone()[0]
        self.assertNotEqual(updated_ts, first_user_ts)

    def test_chat_session_closes_after_positive_feedback(self):
        user_id = "user-finish"
        ctx1 = FakeTurnContext("Ajude com VPN", user_id=user_id, teams_id="teams-user-2")
        self._run_bot(ctx1)
        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM sessions WHERE teams_user_id=?", ("teams-user-2",))
            session_id = cur.fetchone()[0]

        bucket = self.conv_state.storage.get("conv", {})
        conv = bucket.get(user_id)
        self.assertIsNotNone(conv)
        conv["awaiting_ok"] = True
        conv["session_type"] = "chat_driven"
        conv["session_id"] = session_id

        ctx2 = FakeTurnContext("Sim", user_id=user_id, teams_id="teams-user-2")
        self._run_bot(ctx2)

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT status, ended_at, movidesk_ticket_id FROM sessions WHERE id=?", (session_id,))
            status, ended_at, mov_ticket = cur.fetchone()
        self.assertEqual(status, "encerrada_resolvido")
        self.assertIsNotNone(ended_at)
        self.assertEqual(mov_ticket, "T-9000")
        self.assertTrue(self.mock_create_ticket.called)


if __name__ == "__main__":
    unittest.main()
