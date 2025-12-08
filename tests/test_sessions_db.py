import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from app import db


class SessionDbTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(prefix="sess_", suffix=".db")
        os.close(fd)
        self.temp_db_path = path
        self.old_db_path = db.DB_PATH
        db.DB_PATH = self.temp_db_path
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        try:
            os.remove(self.temp_db_path)
        except OSError:
            pass

    def test_create_and_get_active_session(self):
        session_id = db.create_session(
            teams_user_id="teams-user-123",
            user_email="Colaborador@Test.com",
            ticket_id=999,
            movidesk_ticket_id="T-999",
            session_type="ticket_driven",
            initial_status="em_andamento",
        )
        self.assertGreater(session_id, 0)
        session = db.get_active_session_for_user("teams-user-123")
        self.assertIsNotNone(session)
        self.assertEqual(session["id"], session_id)
        self.assertEqual(session["user_email"], "colaborador@test.com")
        self.assertEqual(session["status"], "em_andamento")
        self.assertIsNone(session["last_user_message_at"])
        self.assertIsNotNone(session["last_bot_message_at"])

    def test_update_and_close_session(self):
        session_id = db.create_session(
            teams_user_id="teams-user-xyz",
            user_email="agent@company.com",
            ticket_id=10,
            movidesk_ticket_id="10",
            session_type="ticket_driven",
            initial_status="aguardando_resposta_usuario",
        )
        session_before = db.get_active_session_for_user("teams-user-xyz")
        prev_bot_ts = session_before["last_bot_message_at"]

        db.update_session_on_bot_message(session_id)
        db.update_session_on_user_message(session_id)
        updated = db.get_active_session_for_user("teams-user-xyz")
        self.assertEqual(updated["status"], "em_andamento")
        self.assertIsNotNone(updated["last_user_message_at"])
        self.assertNotEqual(updated["last_bot_message_at"], prev_bot_ts)

        db.close_session(session_id, "encerrada_timeout")
        closed = db.get_active_session_for_user("teams-user-xyz")
        self.assertIsNone(closed)

    def test_session_reminder_and_timeout_queries(self):
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(minutes=90)).isoformat()

        remind_session = db.create_session(
            teams_user_id="reminder-user",
            user_email=None,
            ticket_id=None,
            movidesk_ticket_id=None,
            session_type="ticket_driven",
            initial_status="aguardando_resposta_usuario",
        )
        timeout_session = db.create_session(
            teams_user_id="timeout-user",
            user_email="timeout@test.com",
            ticket_id=None,
            movidesk_ticket_id=None,
            session_type="chat_driven",
            initial_status="em_andamento",
        )

        with db.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE sessions
                   SET last_bot_message_at=?, status='aguardando_resposta_usuario'
                 WHERE id=?;
                """,
                (old_ts, remind_session),
            )
            cur.execute(
                """
                UPDATE sessions
                   SET last_bot_message_at=?, last_user_message_at=?
                 WHERE id=?;
                """,
                (old_ts, old_ts, timeout_session),
            )
            conn.commit()

        reminders = db.get_sessions_for_reminder(now=now)
        reminder_ids = [s["id"] for s in reminders]
        self.assertIn(remind_session, reminder_ids)
        self.assertNotIn(timeout_session, reminder_ids)

        timeouts = db.get_sessions_for_timeout(now=now)
        timeout_ids = [s["id"] for s in timeouts]
        self.assertIn(remind_session, timeout_ids)
        self.assertIn(timeout_session, timeout_ids)


if __name__ == "__main__":
    unittest.main()
