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


_TOKENS = re.compile(r"""('(?:[^']|'')*'|"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]*\])|(/\*.*?\*/|--[^\n]*)""", re.DOTALL)


def assert_select(sql: str) -> str:
    """The statement with comments removed, or UnsafeSQL. One left-to-right pass reads quoted text ('..', "..", `..`, [..])
    and comments; comments are cut only outside quotes. The checks run on a copy where every quoted text is a placeholder, so
    keywords hidden after a quote or comment marker are still seen. Any backslash, dollar quote and E'..' string is
    refused too (engines disagree on where such a string ends). Any `;` (even inside a quote) and any unterminated
    quote are refused: keygraph's own SQL never needs them."""
    if "\\" in sql:  # MySQL reads a backslash as an escape, other engines do not: the two readings of a string can differ
        raise UnsafeSQL("backslash not allowed")
    if re.search(r"\$\w*\$", sql):
        raise UnsafeSQL("dollar-quoted strings are not allowed")
    out, masked, pos = [], [], 0
    for m in _TOKENS.finditer(sql):
        if m.group(1) and m.group(1)[0] == "'" and m.start() and sql[m.start() - 1] in "eE" \
                and (m.start() < 2 or not (sql[m.start() - 2].isalnum() or sql[m.start() - 2] in "_$")):
            raise UnsafeSQL("escape strings (E'..') are not allowed")
        out.append(sql[pos:m.start()])
        masked.append(sql[pos:m.start()])
        out.append(m.group(1) if m.group(1) is not None else " ")
        masked.append(" 0 " if m.group(1) is not None else " ")
        pos = m.end()
    out.append(sql[pos:])
    masked.append(sql[pos:])
    s, stripped = "".join(out).strip(), "".join(masked).strip()
    if s.endswith(";"):
        s, stripped = s[:-1].strip(), stripped[:-1].strip()
    if not s:
        raise UnsafeSQL("empty statement")
    if ";" in s or ";" in stripped:
        raise UnsafeSQL("only one statement is allowed")
    if re.search(r"['\"`]", stripped):
        raise UnsafeSQL("unterminated quote")
    if not re.match(r"select\b", stripped, re.IGNORECASE):
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


_DOLLAR = re.compile(r"\$\w*\$")  # assert_select refuses dollar quoting, so a value holding one (a `$2b$` hash) is not sampled


def _lit(v) -> str | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return str(v)
    if isinstance(v, Decimal) and v == v.to_integral_value():
        return str(int(v))
    if isinstance(v, str) and "\\" not in v and "\x00" not in v and not _DOLLAR.search(v):
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
