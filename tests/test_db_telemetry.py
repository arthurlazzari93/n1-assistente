import os
import sqlite3
import unittest
import tempfile

from app import db


class TelemetryDbTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(prefix="telemetry_", suffix=".db")
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

    def test_log_ingest_event_stores_row(self):
        db.log_ingest_event(
            source=db.INGEST_SOURCE_MOVIDESK_WEBHOOK,
            action=db.INGEST_ACTION_UPSERT_TICKET,
            status="success",
            ticket_id="12345",
            context={"step": "unit-test"},
        )
        with sqlite3.connect(db.DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT source, action, status, ticket_id, context FROM ingest_events;")
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], db.INGEST_SOURCE_MOVIDESK_WEBHOOK)
            self.assertEqual(row[1], db.INGEST_ACTION_UPSERT_TICKET)
            self.assertEqual(row[2], "success")
            self.assertEqual(row[3], "12345")
            self.assertIn("unit-test", row[4])

    def test_log_ingest_event_accepts_error_status(self):
        db.log_ingest_event(
            source=db.INGEST_SOURCE_MOVIDESK_WEBHOOK,
            action=db.INGEST_ACTION_CLASSIFY_TICKET_LLM,
            status="error",
            ticket_id=999,
            error_message="boom",
        )
        with sqlite3.connect(db.DB_PATH) as conn:
            cur = conn.cursor()
            cur.execute("SELECT status, error_message FROM ingest_events WHERE ticket_id='999';")
            row = cur.fetchone()
            self.assertEqual(row[0], "error")
            self.assertEqual(row[1], "boom")


if __name__ == "__main__":
    unittest.main()
