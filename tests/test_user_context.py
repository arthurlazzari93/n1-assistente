import os
import sqlite3
import tempfile
import unittest

from app import db


class UserContextTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(prefix="ctx_", suffix=".db")
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

    def test_set_and_get_user_context(self):
        db.set_user_current_ticket("User@Test.com", 42, teams_user_id="orgid-xyz")
        ctx = db.get_user_context("user@test.com")
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["current_ticket_id"], 42)
        self.assertEqual(ctx["teams_user_id"], "orgid-xyz")

        ctx_by_teams = db.get_user_context_by_teams_id("orgid-xyz")
        self.assertEqual(ctx_by_teams["user_email"], "user@test.com")

        db.set_user_current_ticket("user@test.com", None, teams_user_id="orgid-xyz")
        ctx_after = db.get_user_context("user@test.com")
        self.assertIsNone(ctx_after["current_ticket_id"])

    def test_list_tickets_for_requester(self):
        db.upsert_ticket(
            ticket_id=100,
            allowed=True,
            subject="OneDrive não sincroniza",
            requester_email="colaborador@empresa.com",
            origin_email_account="suporte@empresa.com",
            n1_candidate=True,
            n1_reason="teste",
            suggested_service=None,
            suggested_category=None,
            suggested_urgency="Média",
        )
        tickets = db.list_tickets_for_requester("Colaborador@Empresa.com", limit=3)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["ticket_id"], 100)
        self.assertEqual(tickets[0]["subject"], "OneDrive não sincroniza")


if __name__ == "__main__":
    unittest.main()
