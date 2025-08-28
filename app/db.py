# app/db.py
import os, sqlite3, json
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", "n1agent.db")

def _utc_now():
    return datetime.now(timezone.utc).isoformat()

def _ensure_columns(cur):
    cur.execute("PRAGMA table_info(tickets_ingestion);")
    cols = {row[1] for row in cur.fetchall()}
    add = []
    if "n1_candidate" not in cols:
        add.append("ALTER TABLE tickets_ingestion ADD COLUMN n1_candidate INTEGER DEFAULT 0;")
    if "n1_reason" not in cols:
        add.append("ALTER TABLE tickets_ingestion ADD COLUMN n1_reason TEXT;")
    if "suggested_service" not in cols:
        add.append("ALTER TABLE tickets_ingestion ADD COLUMN suggested_service TEXT;")
    if "suggested_category" not in cols:
        add.append("ALTER TABLE tickets_ingestion ADD COLUMN suggested_category TEXT;")
    if "suggested_urgency" not in cols:
        add.append("ALTER TABLE tickets_ingestion ADD COLUMN suggested_urgency TEXT;")
    if "llm_json" not in cols:
        add.append("ALTER TABLE tickets_ingestion ADD COLUMN llm_json TEXT;")
    if "llm_confidence" not in cols:
        add.append("ALTER TABLE tickets_ingestion ADD COLUMN llm_confidence REAL;")
    if "llm_admin_required" not in cols:
        add.append("ALTER TABLE tickets_ingestion ADD COLUMN llm_admin_required INTEGER DEFAULT 0;")
    for sql in add:
        cur.execute(sql)

def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tickets_ingestion (
            ticket_id INTEGER PRIMARY KEY,
            first_seen_at TEXT NOT NULL,
            last_seen_at  TEXT NOT NULL,
            allowed       INTEGER NOT NULL,
            requester_email TEXT,
            subject         TEXT,
            origin_email_account TEXT,
            teams_notified  INTEGER NOT NULL DEFAULT 0
        );
        """)
        _ensure_columns(cur)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_allowed ON tickets_ingestion(allowed);")
        conn.commit()

@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()

def upsert_ticket(ticket_id: int, allowed: bool, subject: str, requester_email: str, origin_email_account: str,
                  n1_candidate: bool | None = None, n1_reason: str | None = None,
                  suggested_service: str | None = None, suggested_category: str | None = None,
                  suggested_urgency: str | None = None,
                  llm_json: dict | None = None, llm_confidence: float | None = None, llm_admin_required: bool | None = None):
    now = _utc_now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ticket_id FROM tickets_ingestion WHERE ticket_id = ?", (ticket_id,))
        row = cur.fetchone()
        if row:
            cur.execute("""
            UPDATE tickets_ingestion
               SET last_seen_at=?,
                   allowed=?,
                   subject=?,
                   requester_email=?,
                   origin_email_account=?,
                   n1_candidate=COALESCE(?, n1_candidate),
                   n1_reason=COALESCE(?, n1_reason),
                   suggested_service=COALESCE(?, suggested_service),
                   suggested_category=COALESCE(?, suggested_category),
                   suggested_urgency=COALESCE(?, suggested_urgency),
                   llm_json=COALESCE(?, llm_json),
                   llm_confidence=COALESCE(?, llm_confidence),
                   llm_admin_required=COALESCE(?, llm_admin_required)
             WHERE ticket_id=?;
            """, (now, int(allowed), subject, requester_email, origin_email_account,
                  int(n1_candidate) if n1_candidate is not None else None,
                  n1_reason, suggested_service, suggested_category, suggested_urgency,
                  json.dumps(llm_json) if isinstance(llm_json, dict) else llm_json,
                  llm_confidence,
                  int(llm_admin_required) if llm_admin_required is not None else None,
                  ticket_id))
        else:
            cur.execute("""
            INSERT INTO tickets_ingestion
                (ticket_id, first_seen_at, last_seen_at, allowed, requester_email, subject, origin_email_account,
                 teams_notified, n1_candidate, n1_reason, suggested_service, suggested_category, suggested_urgency,
                 llm_json, llm_confidence, llm_admin_required)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (ticket_id, now, now, int(allowed), requester_email, subject, origin_email_account,
                  int(n1_candidate) if n1_candidate is not None else 0,
                  n1_reason, suggested_service, suggested_category, suggested_urgency,
                  json.dumps(llm_json) if isinstance(llm_json, dict) else llm_json,
                  llm_confidence,
                  int(llm_admin_required) if llm_admin_required is not None else 0))
        conn.commit()

def get_ticket_rec(ticket_id: int):
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("""
        SELECT ticket_id, first_seen_at, last_seen_at, allowed, requester_email, subject, origin_email_account,
               teams_notified, n1_candidate, n1_reason, suggested_service, suggested_category, suggested_urgency,
               llm_json, llm_confidence, llm_admin_required
          FROM tickets_ingestion WHERE ticket_id = ?;
        """, (ticket_id,))
        row = cur.fetchone()
        if not row:
            return None
        keys = ["ticket_id","first_seen_at","last_seen_at","allowed","requester_email","subject","origin_email_account",
                "teams_notified","n1_candidate","n1_reason","suggested_service","suggested_category","suggested_urgency",
                "llm_json","llm_confidence","llm_admin_required"]
        rec = dict(zip(keys, row))
        try:
            if isinstance(rec.get("llm_json"), str) and rec["llm_json"]:
                import orjson
                rec["llm_json"] = orjson.loads(rec["llm_json"])
        except Exception:
            pass
        return rec
