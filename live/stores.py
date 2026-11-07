"""The two online stores of the Arduino fault panel. Only live/ uses them; the rest of Field Atlas never imports
this module and works the same without them.

- Tiger Data (Postgres + TimescaleDB): the board's heartbeats (which fault it shows, its status, its A0 touch
  pad) go into the `board_heartbeats` hypertable (live/setup_tiger.py).
- Snowflake: the panel's event log (claimed -> acknowledged -> done) in `DEVICE_EVENTS` (live/setup_snowflake.py).

Writes to both are queued and never block a request. Settings come from the environment or live/.env
(git-ignored, see live/.env.example). A store that is not configured, has no driver installed, or fails is
skipped; a failed store is retried after RETRY_S.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path

RETRY_S = 30
_WRITES: queue.Queue = queue.Queue()   # (function, args): store writes, run one by one off the request thread


def _writer():
    while True:
        fn, args = _WRITES.get()
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001 - a store write must never kill the writer
            print(f"[live] write failed: {e}")


threading.Thread(target=_writer, daemon=True).start()


def _load_env(path: Path):
    """KEY=value lines; already-set environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#") and not os.environ.get(key.strip()):
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


_load_env(Path(__file__).with_name(".env"))


class _Store:
    name = "store"

    def __init__(self):
        self.conn = None
        self.failed_at = 0.0
        self.last_error = ""
        self.lock = threading.Lock()

    def configured(self) -> bool:
        raise NotImplementedError

    def _connect(self):
        raise NotImplementedError

    def _get(self):
        if not self.configured():
            return None
        if self.conn is None and time.time() - self.failed_at > RETRY_S:
            try:
                self.conn = self._connect()
                self.last_error = ""
                print(f"[live:{self.name}] connected")
            except Exception as e:  # noqa: BLE001 - a missing driver or any connection error means "use memory"
                self._fail(e)
        return self.conn

    def _fail(self, e: Exception):
        self.last_error = f"{type(e).__name__}: {e}"[:300]
        print(f"[live:{self.name}] {self.last_error}")
        self.failed_at = time.time()
        try:
            if self.conn:
                self.conn.close()
        except Exception:  # noqa: BLE001
            pass
        self.conn = None

    def status(self) -> dict:
        return {"configured": self.configured(), "connected": self.conn is not None, "error": self.last_error or None}


class Tiger(_Store):
    name = "tiger"

    def configured(self):
        return bool(os.getenv("TIGER_DATABASE_URL"))

    def _connect(self):
        import psycopg

        return psycopg.connect(os.environ["TIGER_DATABASE_URL"], autocommit=True, connect_timeout=5)

    def _run(self, sql: str, args=()):
        with self.lock:
            conn = self._get()
            if not conn:
                return None
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, args)
                    return cur.fetchall() if cur.description else []
            except Exception as e:  # noqa: BLE001
                self._fail(e)
                return None

    def insert_heartbeat(self, tag: str | None, status: str, a0_swing, touched):
        if self.configured():
            _WRITES.put((self._run, ("INSERT INTO board_heartbeats (ts, tag, status, a0_swing, touched) "
                                     "VALUES (now(), %s, %s, %s, %s)", (tag, status, a0_swing, touched))))


def snowflake_auth() -> dict:
    """Key-pair login (SNOWFLAKE_PRIVATE_KEY_PATH) or a programmatic access token (SNOWFLAKE_TOKEN)."""
    key = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH")
    if key:
        return {"private_key_file": os.path.expanduser(key)}
    return {"authenticator": "PROGRAMMATIC_ACCESS_TOKEN", "token": os.environ["SNOWFLAKE_TOKEN"]}


def snowflake_connect(**extra):
    import snowflake.connector

    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"], user=os.environ["SNOWFLAKE_USER"], **snowflake_auth(),
        role=os.getenv("SNOWFLAKE_ROLE") or None, warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "VALMET_WH"),
        login_timeout=10, network_timeout=15, **extra)


class Snowflake(_Store):
    name = "snowflake"

    def configured(self):
        return (all(os.getenv(k) for k in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER"))
                and bool(os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH") or os.getenv("SNOWFLAKE_TOKEN")))

    def _connect(self):
        return snowflake_connect(database=os.getenv("SNOWFLAKE_DATABASE", "VALMET_TWIN"),
                                 schema=os.getenv("SNOWFLAKE_SCHEMA", "PLANT"))

    def query(self, sql: str, args=()) -> list[dict] | None:
        with self.lock:
            conn = self._get()
            if not conn:
                return None
            try:
                cur = conn.cursor()
                cur.execute(sql, args)
                cols = [c[0].lower() for c in cur.description or []]
                return [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
            except Exception as e:  # noqa: BLE001
                self._fail(e)
                return None

    def write_later(self, sql: str, args=()):
        """Fire-and-forget write: a request never waits on the warehouse."""
        if self.configured():
            _WRITES.put((self.query, (sql, args)))


tiger = Tiger()
snowflake = Snowflake()
