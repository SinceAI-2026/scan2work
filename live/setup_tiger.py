"""Create the Arduino fault panel's Tiger Data schema. Safe to re-run.

  board_heartbeats   hypertable   one row per board heartbeat: the fault it shows, its status, its A0 touch pad

Run from the repo root:  .venv/bin/python -m live.setup_tiger   (needs TIGER_DATABASE_URL in live/.env)
"""
import os

import psycopg

from . import stores  # noqa: F401  (loads live/.env)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS board_heartbeats (
        ts timestamptz NOT NULL, tag text, status text, a0_swing double precision, touched boolean)""",
    "SELECT create_hypertable('board_heartbeats', 'ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)",
]


def main():
    with psycopg.connect(os.environ["TIGER_DATABASE_URL"], autocommit=True) as conn:
        for sql in SCHEMA:
            conn.execute(sql)
    print("Tiger ready: board_heartbeats.")


if __name__ == "__main__":
    main()
