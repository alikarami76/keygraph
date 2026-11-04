"""Fetch information_schema STATISTICS and KEY_COLUMN_USAGE through any command or callable.

Read-only: only SELECT statements on information_schema are issued. `run_sql(sql) -> list[dict]` can be a
DB-API wrapper, a SQLAlchemy wrapper, or `command_runner("some-cli --sql {sql}")` for an external tool that
prints JSON (a list of rows, or an object with a "rows" list; leading log lines are skipped).
"""

from __future__ import annotations

import json
import shlex
import string
import subprocess
from typing import Callable

STATS_SQL = ("SELECT TABLE_SCHEMA,TABLE_NAME,INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME "
             "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA={schema} {extra} "
             "ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX LIMIT {limit}")
KCU_SQL = ("SELECT TABLE_SCHEMA,TABLE_NAME,CONSTRAINT_NAME,ORDINAL_POSITION,COLUMN_NAME,"
           "REFERENCED_TABLE_SCHEMA,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME "
           "FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA={schema} "
           "AND REFERENCED_TABLE_NAME IS NOT NULL {extra} ORDER BY TABLE_NAME,CONSTRAINT_NAME,ORDINAL_POSITION LIMIT {limit}")

Rows = list[dict]


def parse_rows(stdout: str) -> Rows:
    text = stdout.strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON in command output: " + text[:200])
    data = json.loads(text[min(starts):])
    if isinstance(data, dict):
        data = data.get("rows", [])
    return data


def command_runner(template: str, timeout: int = 120) -> Callable[[str], Rows]:
    """template like 'mytool --engine prod query --sql {sql}'; {sql} is passed as one argument."""
    parts = shlex.split(template)

    def run(sql: str) -> Rows:
        argv = [p.replace("{sql}", sql) if "{sql}" in p else p for p in parts]
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        if done.returncode != 0:
            raise RuntimeError(f"command failed ({done.returncode}): {done.stderr.strip()[-300:]}")
        return parse_rows(done.stdout)

    return run


def _chunked(run_sql, template: str, schema_expr: str, limit: int, name_col: str = "TABLE_NAME") -> Rows:
    """Run the query; when a result hits the row limit, split by table-name prefix until each part fits."""
    out: Rows = []

    def go(prefix: str):
        extra = f"AND {name_col} LIKE '{prefix}%'" if prefix else ""
        rows = run_sql(template.format(schema=schema_expr, extra=extra, limit=limit))
        if len(rows) < limit or len(prefix) >= 6:
            out.extend(rows)
            return
        alphabet = string.ascii_lowercase + string.digits + "_"
        for ch in alphabet:
            go(prefix + ch.replace("_", "\\_"))
        # names starting with an uppercase letter match LIKE case-insensitively on most collations

    go("")
    return out


def fetch_metadata(run_sql: Callable[[str], Rows], schema_expr: str = "DATABASE()", limit: int = 10000) -> tuple[Rows, Rows]:
    return (_chunked(run_sql, STATS_SQL, schema_expr, limit), _chunked(run_sql, KCU_SQL, schema_expr, limit))


COLUMNS_SQL = ("SELECT TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,COLUMN_TYPE,DATA_TYPE,IS_NULLABLE,COLUMN_KEY,"
               "COLUMN_DEFAULT,EXTRA,CHARACTER_SET_NAME,COLLATION_NAME,COLUMN_COMMENT,GENERATION_EXPRESSION "
               "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA={schema} {extra} ORDER BY TABLE_NAME,ORDINAL_POSITION LIMIT {limit}")
TABLES_SQL = ("SELECT TABLE_SCHEMA,TABLE_NAME,ENGINE,TABLE_ROWS,DATA_LENGTH,INDEX_LENGTH,TABLE_COMMENT,CREATE_OPTIONS "
              "FROM information_schema.TABLES WHERE TABLE_SCHEMA={schema} AND TABLE_TYPE='BASE TABLE' {extra} ORDER BY TABLE_NAME LIMIT {limit}")
CHECKS_SQL = ("SELECT tc.TABLE_SCHEMA,tc.TABLE_NAME,cc.CONSTRAINT_NAME,cc.CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS cc "
              "JOIN information_schema.TABLE_CONSTRAINTS tc ON tc.CONSTRAINT_SCHEMA=cc.CONSTRAINT_SCHEMA AND "
              "tc.CONSTRAINT_NAME=cc.CONSTRAINT_NAME WHERE tc.TABLE_SCHEMA={schema} AND tc.CONSTRAINT_TYPE='CHECK' LIMIT {limit}")


def fetch_mysql_all(run_sql, schema_expr: str = "DATABASE()", limit: int = 10000) -> dict:
    """COLUMNS, TABLES, STATISTICS, KEY_COLUMN_USAGE (chunked) and CHECK constraints (best effort)."""
    out = {"columns": _chunked(run_sql, COLUMNS_SQL, schema_expr, limit), "tables": _chunked(run_sql, TABLES_SQL, schema_expr, limit)}
    out["statistics"], out["key_columns"] = fetch_metadata(run_sql, schema_expr, limit)
    try:
        out["checks"] = run_sql(CHECKS_SQL.format(schema=schema_expr, limit=limit))
    except Exception:  # noqa: BLE001  (MariaDB / old MySQL / tool restrictions)
        out["checks"] = []
    return out
