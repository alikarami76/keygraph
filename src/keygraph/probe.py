"""Read-only value-overlap probe for inferred edges. Refuses anything that is not a single SELECT."""

from __future__ import annotations

import re
import sqlite3
import urllib.parse
from decimal import Decimal
from pathlib import Path
from typing import Callable

from . import devlog
from .graph import Graph

_BAD = re.compile(r"\b(insert|update|delete|replace|drop|alter|create|truncate|grant|revoke|rename|call|handler|load|"
                  r"lock|unlock|merge|exec|execute|set|use|copy|vacuum|attach|pragma)\b|\binto\s+(outfile|dumpfile)\b|"
                  r"\bfor\s+update\b", re.I)


class UnsafeSQL(ValueError):
    pass


_TOKENS = re.compile(r"""('(?:[^']|'')*'|"(?:[^"]|"")*"|`[^`]*`)|/\*.*?\*/|--[^\n]*""", re.DOTALL)


def assert_select(sql: str) -> str:
    # string literals and quoted names are kept as they are, so `--` or `/*` inside one does not change the query
    s = _TOKENS.sub(lambda m: m.group(1) or " ", sql).strip()
    if s.endswith(";"):
        s = s[:-1].strip()
    if not s:
        raise UnsafeSQL("empty statement")
    stripped = re.sub(r"'(?:[^']|'')*'", "''", s)
    if ";" in stripped:
        raise UnsafeSQL("only one statement is allowed")
    if not re.match(r"select\b", s, re.I):
        raise UnsafeSQL("only SELECT statements are allowed")
    m = _BAD.search(stripped)
    if m:
        raise UnsafeSQL(f"forbidden keyword in query: {m.group(0)}")
    return s


def row_dict(labels: list[str], row) -> dict:
    """A row keyed by its column labels, keeping repeated labels (relname, relname_2, ...) instead of dropping values."""
    out: dict = {}
    for label, v in zip(labels, row):
        k, n = str(label), 1
        while k in out:
            n += 1
            k = f"{label}_{n}"
        out[k] = v
    return out


class SelectOnlyRunner:
    def __init__(self, execute: Callable[[str], list]):
        self._execute = execute

    def run(self, sql: str) -> list:
        return list(self._execute(assert_select(sql)))

    @classmethod
    def from_sqlite(cls, path: str, as_dicts: bool = False, timeout: float = 10.0) -> SelectOnlyRunner:
        """A stdlib sqlite3 runner, opened read-only (`file:...?mode=ro`), so it works without the live extra."""
        uri = "file:" + urllib.parse.quote(str(Path(path).resolve())) + "?mode=ro"

        def ex(sql):
            con = sqlite3.connect(uri, uri=True, timeout=timeout)
            try:
                cur = con.execute(sql)
                labels = [d[0] for d in cur.description or []]
                return [row_dict(labels, r) if as_dicts else tuple(r) for r in cur.fetchall()]
            finally:
                con.close()
        return cls(ex)

    @classmethod
    def from_url(cls, url: str, as_dicts: bool = False) -> SelectOnlyRunner:
        """as_dicts: rows keyed by column name (what the information_schema adapters read) instead of tuples.
        `sqlite:` URLs use the standard library (read-only); other URLs need SQLAlchemy (extra: live)."""
        if url.lower().startswith("sqlite:"):
            rest = url.split(":", 1)[1]
            path = rest[3:].split("?", 1)[0] if rest.startswith("///") else ""
            if not path or path == ":memory:":
                raise ValueError("a sqlite URL needs a file path: sqlite:///path/to/file.db")
            return cls.from_sqlite(urllib.parse.unquote(path), as_dicts)
        from sqlalchemy import create_engine, text
        eng = create_engine(url)

        def ex(sql):
            with eng.connect() as c:
                res = c.execute(text(sql))
                labels = list(res.keys())
                return [row_dict(labels, r) if as_dicts else tuple(r) for r in res]
        return cls(ex)

    @classmethod
    def from_dbapi(cls, conn) -> SelectOnlyRunner:
        def ex(sql):
            cur = conn.cursor()
            try:
                cur.execute(sql)
                return cur.fetchall()
            finally:
                cur.close()
        return cls(ex)


def _lit(v) -> str | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return str(v)
    if isinstance(v, Decimal) and v == v.to_integral_value():
        return str(int(v))
    if isinstance(v, str) and "\\" not in v and "\x00" not in v:
        return "'" + v.replace("'", "''") + "'"
    return None


def probe_graph(graph: Graph, runner: SelectOnlyRunner, sample: int = 500, quote: str = '"',
                max_edges: int = 200, min_conf: float = 0.0, statuses=("inferred",), profiles: dict | None = None) -> dict:
    q = lambda name: f"{quote}{name}{quote}"
    qt = lambda tid: ".".join(q(p) for p in tid.split(".", 1)) if not tid.startswith("main.") else q(tid.split(".", 1)[1])
    res = {"probed": 0, "skipped": 0, "errors": []}
    for e in graph.edges:
        if e["status"] not in statuses or e["confidence"] < min_conf or res["probed"] >= max_edges:
            continue
        if len(e["from_columns"]) != 1:
            res["skipped"] += 1
            continue
        fc, tc = e["from_columns"][0], e["to_columns"][0]
        pf, pt = (profiles or {}).get(f"{e['from']}.{fc}", {}).get("numeric"), (profiles or {}).get(f"{e['to']}.{tc}", {}).get("numeric")
        if pf and pt and (pf["min"] > pt["max"] or pf["max"] < pt["min"]):
            e["evidence"].append(f"profile: value ranges do not overlap ({pf['min']:g}..{pf['max']:g} vs {pt['min']:g}..{pt['max']:g})")
            e["confidence"] = round(e["confidence"] * 0.3, 2)
            e["status"] = "probed"
            res["probed"] += 1
            devlog.trace("probe.edge", edge=e["id"], outcome="profile ranges do not overlap", confidence=e["confidence"])
            continue
        try:
            vals = [r[0] for r in runner.run(
                f"SELECT {q(fc)} FROM {qt(e['from'])} WHERE {q(fc)} IS NOT NULL LIMIT {int(sample)}")]
            lits = sorted({l for l in map(_lit, vals) if l is not None})
            if not lits:
                res["skipped"] += 1
                continue
            found = runner.run(f"SELECT {q(tc)} FROM {qt(e['to'])} WHERE {q(tc)} IN ({','.join(lits)}) LIMIT {len(lits)}")
        except Exception as exc:  # noqa: BLE001
            res["errors"].append(f"{e['id']}: {exc}")
            devlog.trace("probe.edge", edge=e["id"], outcome="error", error_type=type(exc).__name__)  # never the message
            continue
        rate = len({r[0] for r in found}) / len(lits)
        e["match_rate"] = round(rate, 3)
        e["evidence"].append(f"probe: {rate:.0%} of {len(lits)} sampled distinct values of {e['from']}.{fc} exist in {e['to']}.{tc}")
        c = e["confidence"]
        c = min(0.99, c + 0.25) if rate >= 0.95 else min(0.99, c + 0.10) if rate >= 0.8 else c if rate >= 0.5 else \
            min(c, 0.1) if rate == 0 else c * 0.5
        e["confidence"] = round(c, 2)
        e["status"] = "probed"
        res["probed"] += 1
        devlog.trace("probe.edge", edge=e["id"], outcome="probed", sampled_distinct=len(lits), match_rate=e["match_rate"],
                     confidence=e["confidence"])
    devlog.trace("probe.summary", probed=res["probed"], skipped=res["skipped"], errors=len(res["errors"]))
    graph.refresh_stats()
    return res
