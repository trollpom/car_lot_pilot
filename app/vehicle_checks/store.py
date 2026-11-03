"""Small isolated ledger: cache, idempotency and no concurrent paid checks."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.config import DB_PATH
from app.vehicle_checks.client import CheckError

SCHEMA = """
CREATE TABLE IF NOT EXISTS lot_vehicle_checks (
 id TEXT PRIMARY KEY, lot_id INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
 request_id TEXT NOT NULL, input_hash TEXT NOT NULL, mode TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT,
 report_json TEXT, error_code TEXT, message TEXT,
 UNIQUE(lot_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_vehicle_checks_lot ON lot_vehicle_checks(lot_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vehicle_check_running ON lot_vehicle_checks((1)) WHERE status='running';
CREATE TABLE IF NOT EXISTS lot_vehicle_check_requests (
 lot_id INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
 request_id TEXT NOT NULL, input_hash TEXT NOT NULL,
 check_id TEXT NOT NULL REFERENCES lot_vehicle_checks(id) ON DELETE CASCADE,
 PRIMARY KEY(lot_id, request_id)
);
"""


def utcnow():
    return datetime.now(timezone.utc)


@contextmanager
def connection():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def purge(conn, now):
    # Retain a short non-report ledger for idempotency/budgets; erase expired report contents.
    conn.execute("UPDATE lot_vehicle_checks SET report_json=NULL,status='expired',message='Срок отчёта истёк; нужна новая проверка' "
                 "WHERE expires_at IS NOT NULL AND expires_at<=? AND status='done'", (now.isoformat(),))
    conn.execute("DELETE FROM lot_vehicle_checks WHERE status!='running' AND created_at<?", ((now-timedelta(days=30)).isoformat(),))


def init_store(recover=False):
    with connection() as conn:
        conn.executescript(SCHEMA)
        if recover:
            conn.execute("UPDATE lot_vehicle_checks SET status='error',error_code='interrupted',"
                         "message='Сервис перезапущен во время проверки. Автоматический повтор не выполняется' WHERE status='running'")
        purge(conn, utcnow())


def public(row):
    if not row:
        return None
    result = dict(row)
    result.pop("input_hash", None)
    raw = result.pop("report_json", None)
    result["report"] = json.loads(raw) if raw else None
    return result


def latest(lot_id):
    with connection() as conn:
        purge(conn, utcnow())
        return public(conn.execute("SELECT * FROM lot_vehicle_checks WHERE lot_id=? ORDER BY created_at DESC LIMIT 1", (lot_id,)).fetchone())


def reserve(lot_id, request_id, input_hash, mode, daily_limit):
    now = utcnow()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        purge(conn, now)
        previous = conn.execute("SELECT c.* FROM lot_vehicle_check_requests r JOIN lot_vehicle_checks c ON c.id=r.check_id "
                                "WHERE r.lot_id=? AND r.request_id=?", (lot_id, request_id)).fetchone()
        if previous:
            if previous["input_hash"] != input_hash:
                raise CheckError("idempotency_conflict", "Идентификатор запроса уже использован с другими данными", 409)
            return public(previous), "idempotent"
        cached = conn.execute("SELECT * FROM lot_vehicle_checks WHERE lot_id=? AND input_hash=? AND status='done' "
                              "AND expires_at>? ORDER BY created_at DESC LIMIT 1", (lot_id, input_hash, now.isoformat())).fetchone()
        if cached:
            conn.execute("INSERT INTO lot_vehicle_check_requests VALUES(?,?,?,?)", (lot_id, request_id, input_hash, cached["id"]))
            return public(cached), "cache"
        if conn.execute("SELECT 1 FROM lot_vehicle_checks WHERE status='running'").fetchone():
            raise CheckError("busy", "Предыдущая проверка ещё выполняется", 409)
        if mode == "live":
            count = conn.execute("SELECT COUNT(*) FROM lot_vehicle_checks WHERE mode='live' AND created_at>=?",
                                 ((now-timedelta(hours=24)).isoformat(),)).fetchone()[0]
            if count >= daily_limit:
                raise CheckError("daily_limit", "Достигнут лимит новых live-проверок за 24 часа", 429)
        check_id = uuid.uuid4().hex
        conn.execute("INSERT INTO lot_vehicle_checks(id,lot_id,request_id,input_hash,mode,status,created_at) VALUES(?,?,?,?,?,'running',?)",
                     (check_id, lot_id, request_id, input_hash, mode, now.isoformat()))
        conn.execute("INSERT INTO lot_vehicle_check_requests VALUES(?,?,?,?)", (lot_id, request_id, input_hash, check_id))
        return public(conn.execute("SELECT * FROM lot_vehicle_checks WHERE id=?", (check_id,)).fetchone()), "new"


def finish(check_id, report):
    expires = min(report.expires_at, report.created_at+timedelta(hours=24 if report.demo else 6))
    with connection() as conn:
        conn.execute("UPDATE lot_vehicle_checks SET status='done',report_json=?,expires_at=?,message='Проверка завершена' WHERE id=?",
                     (report.model_dump_json(), expires.astimezone(timezone.utc).isoformat(), check_id))
        return public(conn.execute("SELECT * FROM lot_vehicle_checks WHERE id=?", (check_id,)).fetchone())


def fail(check_id, error):
    with connection() as conn:
        conn.execute("UPDATE lot_vehicle_checks SET status='error',error_code=?,message=? WHERE id=?",
                     (error.code, error.public_message, check_id))
