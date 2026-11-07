"""Create the Arduino fault panel's Snowflake table. Safe to re-run.

  DEVICE_EVENTS   the panel's event log: FAULT_DETECTED (claimed), ACKNOWLEDGED, RESTORED (done)

The database and schema are created if the role may; otherwise they must exist.
Run from the repo root:  .venv/bin/python -m live.setup_snowflake   (needs the SNOWFLAKE_* settings in live/.env)
"""
import os

from .stores import snowflake_connect

DB = os.getenv("SNOWFLAKE_DATABASE", "VALMET_TWIN")
SCHEMA = os.getenv("SNOWFLAKE_SCHEMA", "PLANT")


def main():
    conn = snowflake_connect()
    cur = conn.cursor()
    for sql in (f"CREATE DATABASE IF NOT EXISTS {DB}", f"CREATE SCHEMA IF NOT EXISTS {DB}.{SCHEMA}"):
        try:
            cur.execute(sql)
        except Exception as e:  # noqa: BLE001 - no create rights: the database and schema must already exist
            print("  (skipped:", str(e).splitlines()[0], ")")
    cur.execute(f"USE SCHEMA {DB}.{SCHEMA}")
    cur.execute("""CREATE TABLE IF NOT EXISTS DEVICE_EVENTS (EVENT_ID STRING, TS TIMESTAMP_NTZ, TAG STRING,
        EVENT_TYPE STRING, SOURCE STRING, ACTOR STRING, DETAILS VARIANT)""")
    print(f"Snowflake ready: {DB}.{SCHEMA}.DEVICE_EVENTS")
    conn.close()


if __name__ == "__main__":
    main()
