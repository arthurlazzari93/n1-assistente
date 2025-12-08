# app/db.py
import os
import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone

DB_PATH = os.getenv("DB_PATH", "n1agent.db")

# ---------- Constantes de telemetria (usadas futuramente em /debug/metrics) ----------
# Sources padronizados para eventos de ingestão.
INGEST_SOURCE_MOVIDESK_WEBHOOK = "movidesk_webhook"
INGEST_SOURCE_MOVIDESK_MANUAL = "movidesk_manual"
INGEST_SOURCE_TEAMS_BOT = "teams_bot"

# Ações padronizadas (ação = etapa dentro do fluxo). Expanda com cautela.
INGEST_ACTION_PAYLOAD_RECEIVED = "payload_received"
INGEST_ACTION_FETCH_TICKET = "fetch_ticket"
INGEST_ACTION_CLASSIFY_TICKET_LLM = "classify_ticket_llm"
INGEST_ACTION_CLASSIFY_TICKET_FALLBACK = "classify_ticket_fallback"
INGEST_ACTION_UPSERT_TICKET = "upsert_ticket"
INGEST_ACTION_SCHEDULE_FOLLOWUP = "schedule_followup"
INGEST_ACTION_NOTIFY_TEAMS = "notify_user_teams"
INGEST_ACTION_SKIP_FLOW = "skip_flow"
INGEST_ACTION_PROACTIVE_FOLLOWUP = "proactive_followup"

# SessÇõÇæes de atendimento (tempo limite em minutos, override via env se precisar)
SESSION_REMINDER_MINUTES = int(os.getenv("SESSION_REMINDER_MINUTES", "15"))
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "60"))
FOLLOWUP_NUDGE1_MINUTES = int(os.getenv("FOLLOWUP_NUDGE1_MINUTES", "10"))
FOLLOWUP_NUDGE2_MINUTES = int(os.getenv("FOLLOWUP_NUDGE2_MINUTES", "25"))
FOLLOWUP_FINAL_CLOSE_MINUTES = int(os.getenv("FOLLOWUP_FINAL_CLOSE_MINUTES", "85"))

SESSION_ACTIVE_STATUSES = ("aguardando_resposta_usuario", "em_andamento")
SESSION_FINAL_STATUSES = ("encerrada_resolvido", "encerrada_timeout", "encerrada_escalado")


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


def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


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


def _ensure_user_context_table(cur: sqlite3.Cursor):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_ticket_context (
            user_email TEXT PRIMARY KEY,
            current_ticket_id INTEGER,
            teams_user_id TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_ctx_teams ON user_ticket_context(teams_user_id);")


def _ensure_ingest_events_table(cur: sqlite3.Cursor):
    """
    Tabela leve para registrar eventos da ingestão Movidesk.
    Servirá como base para o endpoint /debug/metrics em tarefa futura.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,      -- success | error
            ticket_id TEXT,
            error_message TEXT,
            context TEXT
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ingest_events_source_action ON ingest_events(source, action);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ingest_events_ts ON ingest_events(ts);")


def _ensure_sessions_table(cur: sqlite3.Cursor):
    """
    Estrutura de sessÇõÇœes de atendimento (bot x usuÇ­rio). MantÇ¸m timestamps para lembretes e timeout.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teams_user_id TEXT,
            user_email TEXT,
            ticket_id INTEGER,
            movidesk_ticket_id TEXT,
            type TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            last_user_message_at TEXT,
            last_bot_message_at TEXT,
            ended_at TEXT
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_teams ON sessions(teams_user_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_ticket ON sessions(ticket_id);")


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
        _ensure_user_context_table(cur)
        _ensure_ingest_events_table(cur)
        _ensure_sessions_table(cur)
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
    Agenda os lembretes do fluxo proativo usando os intervalos configuráveis (env):
      FOLLOWUP_NUDGE1_MINUTES
      FOLLOWUP_NUDGE2_MINUTES
      FOLLOWUP_FINAL_CLOSE_MINUTES
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    wait_window = max(FOLLOWUP_FINAL_CLOSE_MINUTES - FOLLOWUP_NUDGE2_MINUTES, 0)
    wait_text = f" por até {wait_window} minutos" if wait_window else ""
    plan = [
        ("nudge1", FOLLOWUP_NUDGE1_MINUTES,
         f"Estou à disposição para te ajudar com o chamado #{ticket_id}. Podemos iniciar agora?"),
        ("nudge2", FOLLOWUP_NUDGE2_MINUTES,
         f"Vou aguardar seu retorno{wait_text}. Se não houver resposta, atualizo o chamado #{ticket_id} para 'Aguardando retorno do usuário'."),
        ("final_close", FOLLOWUP_FINAL_CLOSE_MINUTES,
         f"Encerrando a triagem automática do chamado #{ticket_id}. Um analista dará continuidade. Se preferir, podemos retomar por aqui com `iniciar {ticket_id}`."),
    ]
    with connect() as conn:
        cur = conn.cursor()
        for step, minutes_offset, msg in plan:
            minutes_offset = max(0, int(minutes_offset))
            when_dt = now + timedelta(minutes=minutes_offset)
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


# ---------------- Telemetria de ingestão (base para /debug/metrics) ----------------

def log_ingest_event(
    source: str,
    action: str,
    status: str,
    ticket_id: str | int | None = None,
    error_message: str | None = None,
    context: dict | list | str | None = None,
):
    """
    Registra um evento simples relacionado à ingestão Movidesk.
    Não lança exceção para o caller; quem usa deve tratar erros externos.
    """
    ts = _utc_now()
    ctx_serialized: str | None = None
    if isinstance(context, (dict, list)):
        try:
            ctx_serialized = json.dumps(context, ensure_ascii=False)
        except Exception:
            ctx_serialized = str(context)
    elif context is not None:
        ctx_serialized = str(context)

    ticket_str = str(ticket_id) if ticket_id is not None else None
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO ingest_events (ts, source, action, status, ticket_id, error_message, context)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                ts,
                source,
                action,
                status,
                ticket_str,
                error_message,
                ctx_serialized,
            ),
        )
        conn.commit()


def _decode_context(raw: str | None):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def get_ingest_metrics(window_hours: int = 24, recent_limit: int = 100, error_limit: int = 20) -> dict:
    """
    Retorna métricas simples da tabela ingest_events.
    Útil para o endpoint /debug/metrics.
    """
    window_start = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    data: dict = {
        "recent_events": [],
        "window": {
            "since": window_start,
            "total_events": 0,
            "by_status": {},
            "by_action": {},
            "recent_errors": [],
        },
    }
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ts, source, action, status, ticket_id, error_message, context
              FROM ingest_events
             ORDER BY id DESC
             LIMIT ?;
            """,
            (recent_limit,),
        )
        for row in cur.fetchall():
            data["recent_events"].append(
                {
                    "ts": row[0],
                    "source": row[1],
                    "action": row[2],
                    "status": row[3],
                    "ticket_id": row[4],
                    "error_message": row[5],
                    "context": _decode_context(row[6]),
                }
            )

        cur.execute(
            "SELECT status, COUNT(1) FROM ingest_events WHERE ts >= ? GROUP BY status;",
            (window_start,),
        )
        for status, count in cur.fetchall():
            data["window"]["by_status"][status] = count
            data["window"]["total_events"] += count

        cur.execute(
            """
            SELECT action, status, COUNT(1)
              FROM ingest_events
             WHERE ts >= ?
          GROUP BY action, status;
            """,
            (window_start,),
        )
        for action, status, count in cur.fetchall():
            per_action = data["window"]["by_action"].setdefault(action, {"success": 0, "error": 0})
            per_action[status] = count

        cur.execute(
            """
            SELECT ts, source, action, ticket_id, error_message
              FROM ingest_events
             WHERE status='error'
             ORDER BY id DESC
             LIMIT ?;
            """,
            (error_limit,),
        )
        for row in cur.fetchall():
            data["window"]["recent_errors"].append(
                {
                    "ts": row[0],
                    "source": row[1],
                    "action": row[2],
                    "ticket_id": row[3],
                    "error_message": row[4],
                }
            )
    return data


def get_followup_metrics() -> dict:
    """
    Resumo do estado dos follow-ups (pendentes/enviados/cancelados) + próximo vencimento.
    """
    metrics = {"by_status": {}, "pending_total": 0, "next_due": None}
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT state, COUNT(1) FROM ticket_followups GROUP BY state;")
        for state, count in cur.fetchall():
            metrics["by_status"][state] = count
            if state == "pending":
                metrics["pending_total"] = count
        cur.execute(
            """
            SELECT next_run_at
              FROM ticket_followups
             WHERE state='pending'
             ORDER BY next_run_at ASC
             LIMIT 1;
            """
        )
        row = cur.fetchone()
        if row:
            metrics["next_due"] = row[0]
    return metrics


def get_recent_tickets(limit: int = 20) -> list[dict]:
    """
    Retorna tickets mais recentes registrados em tickets_ingestion.
    """
    items: list[dict] = []
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ticket_id, first_seen_at, last_seen_at, allowed, requester_email,
                   subject, origin_email_account, teams_notified, n1_candidate, n1_reason
              FROM tickets_ingestion
          ORDER BY last_seen_at DESC
             LIMIT ?;
            """,
            (limit,),
        )
        rows = cur.fetchall()
        for row in rows:
            items.append(
                {
                    "ticket_id": row[0],
                    "first_seen_at": row[1],
                    "last_seen_at": row[2],
                    "allowed": bool(row[3]),
                    "requester_email": row[4],
                    "subject": row[5],
                    "origin_email_account": row[6],
                    "teams_notified": bool(row[7]),
                    "n1_candidate": bool(row[8]) if row[8] is not None else None,
                    "n1_reason": row[9],
                }
            )
    return items


def set_user_current_ticket(user_email: str, ticket_id: int | None, teams_user_id: str | None = None) -> None:
    email = _normalize_email(user_email)
    if not email:
        return
    now = _utc_now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_email FROM user_ticket_context WHERE user_email=?;", (email,))
        exists = cur.fetchone() is not None
        if exists:
            if teams_user_id is not None:
                cur.execute(
                    """
                    UPDATE user_ticket_context
                       SET current_ticket_id=?,
                           teams_user_id=?,
                           updated_at=?
                     WHERE user_email=?;
                    """,
                    (ticket_id, teams_user_id, now, email),
                )
            else:
                cur.execute(
                    """
                    UPDATE user_ticket_context
                       SET current_ticket_id=?,
                           updated_at=?
                     WHERE user_email=?;
                    """,
                    (ticket_id, now, email),
                )
        else:
            cur.execute(
                """
                INSERT INTO user_ticket_context (user_email, current_ticket_id, teams_user_id, updated_at)
                VALUES (?, ?, ?, ?);
                """,
                (email, ticket_id, teams_user_id, now),
            )
        conn.commit()


def get_user_context(user_email: str) -> dict | None:
    email = _normalize_email(user_email)
    if not email:
        return None
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_email, current_ticket_id, teams_user_id, updated_at FROM user_ticket_context WHERE user_email=?;",
            (email,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "user_email": row[0],
            "current_ticket_id": row[1],
            "teams_user_id": row[2],
            "updated_at": row[3],
        }


def get_user_context_by_teams_id(teams_user_id: str) -> dict | None:
    if not teams_user_id:
        return None
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_email, current_ticket_id, teams_user_id, updated_at FROM user_ticket_context WHERE teams_user_id=?;",
            (teams_user_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "user_email": row[0],
            "current_ticket_id": row[1],
            "teams_user_id": row[2],
            "updated_at": row[3],
        }


def list_tickets_for_requester(user_email: str, limit: int = 5) -> list[dict]:
    email = _normalize_email(user_email)
    if not email:
        return []
    items: list[dict] = []
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ticket_id, subject, last_seen_at, n1_reason, teams_notified, allowed
              FROM tickets_ingestion
             WHERE LOWER(COALESCE(requester_email, '')) = ?
          ORDER BY last_seen_at DESC
             LIMIT ?;
            """,
            (email, limit),
        )
        rows = cur.fetchall()
        for row in rows:
            items.append(
                {
                    "ticket_id": row[0],
                    "subject": row[1],
                    "last_seen_at": row[2],
                    "n1_reason": row[3],
                    "teams_notified": bool(row[4]),
                    "allowed": bool(row[5]),
                }
            )
    return items


# ---------------- SessÃµes de atendimento (bot / Teams) ----------------
# MantÃ©m estado de conversas de atendimento para aplicar lembretes e timeout
# (integraÃ§Ã£o com o bot e workers virÃ¡ em tarefas futuras).
_SESSION_COLUMNS = [
    "id",
    "teams_user_id",
    "user_email",
    "ticket_id",
    "movidesk_ticket_id",
    "type",
    "status",
    "started_at",
    "last_user_message_at",
    "last_bot_message_at",
    "ended_at",
]


def _session_row_to_dict(row: tuple | None) -> dict | None:
    if not row:
        return None
    return dict(zip(_SESSION_COLUMNS, row))


def create_session(
    teams_user_id: str | None,
    user_email: str | None,
    ticket_id: int | None,
    movidesk_ticket_id: str | None,
    session_type: str,
    initial_status: str = "em_andamento",
) -> int:
    """
    Cria uma nova sessÃ£o de atendimento e retorna o ID gerado.
    """
    now = _utc_now()
    email = _normalize_email(user_email) if user_email else None
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO sessions (
                teams_user_id, user_email, ticket_id, movidesk_ticket_id,
                type, status, started_at, last_user_message_at,
                last_bot_message_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                teams_user_id,
                email,
                ticket_id,
                movidesk_ticket_id,
                session_type,
                initial_status,
                now,
                None,
                now,
                None,
            ),
        )
        session_id = cur.lastrowid
        conn.commit()
    return int(session_id or 0)


def get_active_session_for_user(teams_user_id: str | None) -> dict | None:
    """
    Retorna a sessÃ£o mais recente do usuÃ¡rio em andamento/aguardando.
    """
    if not teams_user_id:
        return None
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT {", ".join(_SESSION_COLUMNS)}
              FROM sessions
             WHERE teams_user_id=?
               AND status IN ({", ".join("?" for _ in SESSION_ACTIVE_STATUSES)})
             ORDER BY id DESC
             LIMIT 1;
            """,
            (teams_user_id, *SESSION_ACTIVE_STATUSES),
        )
        row = cur.fetchone()
    return _session_row_to_dict(row)


def update_session_on_bot_message(session_id: int) -> None:
    """
    Atualiza timestamp da ?ltima mensagem enviada pelo bot e marca que estamos aguardando o usu?rio quando aplic?vel.
    """
    now = _utc_now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE sessions
               SET last_bot_message_at=?,
                   status=CASE
                       WHEN status IN ('em_andamento', 'aguardando_resposta_usuario')
                           THEN 'aguardando_resposta_usuario'
                       ELSE status
                   END
             WHERE id=?;
            """,
            (now, session_id),
        )
        conn.commit()


def update_session_on_user_message(session_id: int) -> None:
    """
    Atualiza timestamp da Ãºltima mensagem do usuÃ¡rio e muda status para 'em_andamento' se aguardando.
    """
    now = _utc_now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE sessions
               SET last_user_message_at=?,
                   status=CASE
                       WHEN status='aguardando_resposta_usuario' THEN 'em_andamento'
                       ELSE status
                   END
             WHERE id=?;
            """,
            (now, session_id),
        )
        conn.commit()


def close_session(session_id: int, new_status: str) -> None:
    """
    Finaliza a sessÃ£o com o status informado (ex.: encerrada_timeout).
    """
    now = _utc_now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE sessions SET status=?, ended_at=? WHERE id=?;",
            (new_status, now, session_id),
        )
        conn.commit()


def find_sessions_pending_timeout(
    bot_cutoff_iso: str | None = None,
    user_cutoff_iso: str | None = None,
) -> list[dict]:
    """
    Busca sessÃµes em andamento/aguardando que ultrapassaram limites de tempo
    (last_bot_message_at e/ou last_user_message_at). Usado por lembretes e timeout.
    """
    if not bot_cutoff_iso and not user_cutoff_iso:
        return []
    clauses: list[str] = []
    params: list[str] = []
    if bot_cutoff_iso:
        clauses.append("(last_bot_message_at IS NOT NULL AND last_bot_message_at <= ?)")
        params.append(bot_cutoff_iso)
    if user_cutoff_iso:
        clauses.append("(last_user_message_at IS NOT NULL AND last_user_message_at <= ?)")
        params.append(user_cutoff_iso)
    time_filter = " OR ".join(clauses)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT {", ".join(_SESSION_COLUMNS)}
              FROM sessions
             WHERE status IN ({", ".join("?" for _ in SESSION_ACTIVE_STATUSES)})
               AND ({time_filter})
          ORDER BY COALESCE(last_user_message_at, last_bot_message_at, started_at) ASC;
            """,
            (*SESSION_ACTIVE_STATUSES, *params),
        )
        rows = cur.fetchall()
    return [_session_row_to_dict(row) for row in rows if row]


def get_sessions_for_reminder(now: datetime | None = None) -> list[dict]:
    """
    SessÃµes elegÃ­veis para lembrete (status aguardando ou em andamento, sem resposta recente).
    """
    now_dt = now or datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(minutes=SESSION_REMINDER_MINUTES)).isoformat()
    sessions = find_sessions_pending_timeout(bot_cutoff_iso=cutoff, user_cutoff_iso=cutoff)
    return [
        s
        for s in sessions
        if s.get("status") == "aguardando_resposta_usuario"
    ]


def get_sessions_for_timeout(now: datetime | None = None) -> list[dict]:
    """
    SessÃµes elegÃ­veis para encerramento automÃ¡tico por timeout.
    """
    now_dt = now or datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(minutes=SESSION_TIMEOUT_MINUTES)).isoformat()
    return find_sessions_pending_timeout(bot_cutoff_iso=cutoff, user_cutoff_iso=cutoff)


def get_session_by_id(session_id: int) -> dict | None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {', '.join(_SESSION_COLUMNS)} FROM sessions WHERE id=?;",
            (session_id,),
        )
        row = cur.fetchone()
    return _session_row_to_dict(row)


def set_session_movidesk_ticket(session_id: int, movidesk_ticket_id: str) -> None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE sessions SET movidesk_ticket_id=? WHERE id=?;",
            (movidesk_ticket_id, session_id),
        )
        conn.commit()
