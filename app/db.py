# app/db.py
import os
import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone

DB_PATH = os.getenv("DB_PATH", "n1agent.db")


# ---------------- utils ----------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


# ---------------- bootstrap ----------------

def _ensure_columns_tickets(cur: sqlite3.Cursor):
    """Garante colunas novas sem quebrar instalações que já existiam."""
    cur.execute("PRAGMA table_info(tickets_ingestion);")
    cols = {row[1] for row in cur.fetchall()}
    add_sql = []
    if "teams_notified" not in cols:
        add_sql.append("ALTER TABLE tickets_ingestion ADD COLUMN teams_notified INTEGER DEFAULT 0;")
    if "n1_candidate" not in cols:
        add_sql.append("ALTER TABLE tickets_ingestion ADD COLUMN n1_candidate INTEGER DEFAULT 0;")
    if "n1_reason" not in cols:
        add_sql.append("ALTER TABLE tickets_ingestion ADD COLUMN n1_reason TEXT;")
    if "suggested_service" not in cols:
        add_sql.append("ALTER TABLE tickets_ingestion ADD COLUMN suggested_service TEXT;")
    if "suggested_category" not in cols:
        add_sql.append("ALTER TABLE tickets_ingestion ADD COLUMN suggested_category TEXT;")
    if "suggested_urgency" not in cols:
        add_sql.append("ALTER TABLE tickets_ingestion ADD COLUMN suggested_urgency TEXT;")
    if "llm_json" not in cols:
        add_sql.append("ALTER TABLE tickets_ingestion ADD COLUMN llm_json TEXT;")
    if "llm_confidence" not in cols:
        add_sql.append("ALTER TABLE tickets_ingestion ADD COLUMN llm_confidence REAL;")
    if "llm_admin_required" not in cols:
        add_sql.append("ALTER TABLE tickets_ingestion ADD COLUMN llm_admin_required INTEGER DEFAULT 0;")
    for sql in add_sql:
        cur.execute(sql)


def _ensure_followups_table(cur: sqlite3.Cursor):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            requester_email TEXT NOT NULL,
            subject TEXT,
            step TEXT NOT NULL,            -- nudge1 | nudge2 | final_close
            message TEXT NOT NULL,
            next_run_at TEXT NOT NULL,     -- ISO UTC
            state TEXT NOT NULL DEFAULT 'pending', -- pending|sent|cancelled
            last_sent_at TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_followups_state_next ON ticket_followups(state, next_run_at);")


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tickets_ingestion (
                ticket_id INTEGER PRIMARY KEY,
                first_seen_at TEXT NOT NULL,
                last_seen_at  TEXT NOT NULL,
                allowed       INTEGER NOT NULL,
                requester_email TEXT,
                subject TEXT,
                origin_email_account TEXT,
                teams_notified INTEGER NOT NULL DEFAULT 0,
                n1_candidate INTEGER DEFAULT 0,
                n1_reason TEXT,
                suggested_service TEXT,
                suggested_category TEXT,
                suggested_urgency TEXT,
                llm_json TEXT,
                llm_confidence REAL,
                llm_admin_required INTEGER DEFAULT 0
            );
            """
        )
        _ensure_columns_tickets(cur)
        _ensure_followups_table(cur)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_allowed ON tickets_ingestion(allowed);")
        conn.commit()


# ---------------- tickets ----------------

def upsert_ticket(
    ticket_id: int,
    allowed: bool,
    subject: str,
    requester_email: str,
    origin_email_account: str,
    n1_candidate: bool | None = None,
    n1_reason: str | None = None,
    suggested_service: str | None = None,
    suggested_category: str | None = None,
    suggested_urgency: str | None = None,
    llm_json: dict | None = None,
    llm_confidence: float | None = None,
    llm_admin_required: bool | None = None,
):
    now = _utc_now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ticket_id FROM tickets_ingestion WHERE ticket_id = ?;", (ticket_id,))
        exists = cur.fetchone() is not None
        if exists:
            cur.execute(
                """
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
                """,
                (
                    now,
                    int(allowed),
                    subject,
                    requester_email,
                    origin_email_account,
                    int(n1_candidate) if n1_candidate is not None else None,
                    n1_reason,
                    suggested_service,
                    suggested_category,
                    suggested_urgency,
                    json.dumps(llm_json) if isinstance(llm_json, dict) else llm_json,
                    llm_confidence,
                    int(llm_admin_required) if llm_admin_required is not None else None,
                    ticket_id,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO tickets_ingestion
                    (ticket_id, first_seen_at, last_seen_at, allowed, requester_email, subject, origin_email_account,
                     teams_notified, n1_candidate, n1_reason, suggested_service, suggested_category, suggested_urgency,
                     llm_json, llm_confidence, llm_admin_required)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    ticket_id,
                    now,
                    now,
                    int(allowed),
                    requester_email,
                    subject,
                    origin_email_account,
                    int(n1_candidate) if n1_candidate is not None else 0,
                    n1_reason,
                    suggested_service,
                    suggested_category,
                    suggested_urgency,
                    json.dumps(llm_json) if isinstance(llm_json, dict) else llm_json,
                    llm_confidence,
                    int(llm_admin_required) if llm_admin_required is not None else 0,
                ),
            )
        conn.commit()


def get_ticket_rec(ticket_id: int) -> dict | None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ticket_id, first_seen_at, last_seen_at, allowed, requester_email, subject, origin_email_account,
                   teams_notified, n1_candidate, n1_reason, suggested_service, suggested_category, suggested_urgency,
                   llm_json, llm_confidence, llm_admin_required
              FROM tickets_ingestion
             WHERE ticket_id = ?;
            """,
            (ticket_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        keys = [
            "ticket_id",
            "first_seen_at",
            "last_seen_at",
            "allowed",
            "requester_email",
            "subject",
            "origin_email_account",
            "teams_notified",
            "n1_candidate",
            "n1_reason",
            "suggested_service",
            "suggested_category",
            "suggested_urgency",
            "llm_json",
            "llm_confidence",
            "llm_admin_required",
        ]
        rec = dict(zip(keys, row))
        # tenta decodificar llm_json
        try:
            if isinstance(rec.get("llm_json"), str) and rec["llm_json"]:
                rec["llm_json"] = json.loads(rec["llm_json"])
        except Exception:
            pass
        return rec


def mark_teams_notified(ticket_id: int):
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE tickets_ingestion SET teams_notified=1, last_seen_at=? WHERE ticket_id=?;",
            (_utc_now(), ticket_id),
        )
        conn.commit()


# ---------------- follow-ups (lembretes) ----------------

def schedule_proactive_flow(ticket_id: int, requester_email: str, subject: str):
    """
    Agenda os lembretes do fluxo proativo:
      +10 min  -> nudge1
      +25 min  -> nudge2  (10 + 15)
      +85 min  -> final_close (25 + 60)
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    plan = [
        ("nudge1", now + timedelta(minutes=10),
         f"Estou à disposição para te ajudar com o chamado #{ticket_id}. Podemos iniciar agora?"),
        ("nudge2", now + timedelta(minutes=25),
         f"Vou aguardar seu retorno por até 1 hora. Se não houver resposta, atualizo o chamado #{ticket_id} para 'Aguardando retorno do usuário'."),
        ("final_close", now + timedelta(minutes=85),
         f"Encerrando a triagem automática do chamado #{ticket_id}. Um analista dará continuidade. Se preferir, podemos retomar por aqui com `iniciar {ticket_id}`."),
    ]
    with connect() as conn:
        cur = conn.cursor()
        for step, when_dt, msg in plan:
            cur.execute(
                """
                INSERT INTO ticket_followups
                    (ticket_id, requester_email, subject, step, message, next_run_at, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?);
                """,
                (ticket_id, requester_email, subject, step, msg, when_dt.isoformat(), _utc_now()),
            )
        conn.commit()


def cancel_followups(ticket_id: int):
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ticket_followups SET state='cancelled' WHERE ticket_id=? AND state='pending';",
            (ticket_id,),
        )
        conn.commit()


def fetch_due_followups(limit: int = 20) -> list[dict]:
    now_iso = _utc_now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, ticket_id, requester_email, subject, step, message, next_run_at
              FROM ticket_followups
             WHERE state='pending' AND next_run_at <= ?
             ORDER BY next_run_at ASC
             LIMIT ?;
            """,
            (now_iso, limit),
        )
        rows = cur.fetchall()
        keys = ["id", "ticket_id", "requester_email", "subject", "step", "message", "next_run_at"]
        return [dict(zip(keys, r)) for r in rows]


def mark_followup_sent(fu_id: int):
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE ticket_followups SET state='sent', last_sent_at=? WHERE id=?;",
            (_utc_now(), fu_id),
        )
        conn.commit()
