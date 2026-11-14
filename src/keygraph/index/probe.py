"""Read-only checks of the claims an indexing answer makes, run inside keygraph so the agent never sees a row.

`probe_items` takes the accepted items of `validate` and returns one verdict per item:
`{"kind", "index", "verified": true | false | null, "check", "rate", "n"}` (plus `"error": <type name>` when a query
failed and `"skipped": "query_cap"` when the per-submit cap ran out). `null` means unverified. Only rates and counts leave
this module, never values. Every statement goes through `SelectOnlyRunner`, which applies `assert_select`. Filter text
is built only from validated column names and literal values that parse with a strict grammar; anything else stays
unverified."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from decimal import Decimal
from typing import NamedTuple

from .. import devlog
from ..probe import SelectOnlyRunner, _lit

QUERY_CAP = 40
REL_SAMPLE, CODE_SAMPLE, GRAIN_SAMPLE, FILTER_SAMPLE = 200, 200, 1000, 1000
REL_PASS = 0.8


def quote_for(kind: str | None) -> str:
    """The identifier quote of a source kind: backticks for mysql, mariadb and clickhouse, `[]` for sqlserver, else `"`."""
    k = (kind or "").lower()
    return "`" if k in ("mysql", "mariadb", "clickhouse") else "[]" if k in ("sqlserver", "mssql", "tsql") else '"'


def quote_ident(name: str, quote: str) -> str:
    """Quote an identifier, doubling any quote character inside it (`]` for `[]`)."""
    o, c = quote[0], quote[-1]
    return f"{o}{name.replace(c, c * 2)}{c}"


def dialect_of(g) -> str | None:
    """The first source kind in the map's build.json, or None when it has none."""
    for s in (g.build or {}).get("sources") or []:
        if isinstance(s, dict) and s.get("kind"):
            return str(s["kind"])
    return None


class Source(NamedTuple):
    runner: SelectOnlyRunner
    quote: str
    bare: bool  # table ids lose their schema (sqlite has none)


def runner_from_env(url_env: str = "", cmd_env: str = "", environ=None, dialect: str | None = None) -> Source | None:
    """The probe source named by the job's environment variables, or None when neither is set (items stay unverified).
    `dialect` (a source kind such as mysql or postgres) picks the identifier quote for a command runner; a URL gives its own."""
    env = os.environ if environ is None else environ
    url, cmd = (env.get(url_env, "") if url_env else ""), (env.get(cmd_env, "") if cmd_env else "")
    if url:
        lower = url.lower()
        if not lower.startswith("sqlite:"):
            try:
                import sqlalchemy  # noqa: F401
            except ImportError:
                raise RuntimeError("probing a database URL needs the live extra: pip install 'keygraph[live]'") from None
        return Source(SelectOnlyRunner.from_url(url), quote_for(lower.split(":", 1)[0].split("+", 1)[0]), lower.startswith("sqlite:"))
    if cmd:
        from ..adapters.fetch import command_runner
        run = command_runner(cmd)
        return Source(SelectOnlyRunner(lambda sql: [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in run(sql)]),
                      quote_for(dialect), False)
    return None


class _Cap:
    def __init__(self, n: int):
        self.left = n

    def take(self, n: int) -> bool:
        if n > self.left:
            return False
        self.left -= n
        return True


def _norm(v) -> str:
    if isinstance(v, Decimal) and v == v.to_integral_value():
        return str(int(v))
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def _int(rows, i: int = 0) -> int:
    v = rows[0][i] if rows and rows[0] else 0
    return int(v) if v is not None else 0


_TOKEN = re.compile(r"\s*(?:('(?:[^']|'')*')|(-?\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(<>|!=|=|\(|\)|,))")


def parse_filter(text: str, columns: set[str], q: Callable[[str], str]) -> tuple[str, set[str]] | None:
    """SQL for a simple filter (`col IS [NOT] NULL`, `col =|<>|!= literal`, `col [NOT] IN (literals)`, joined by AND),
    built only from known column names and re-quoted literals; None when the text is anything else."""
    toks, pos = [], 0
    while pos < len(text.rstrip()):
        m = _TOKEN.match(text, pos)
        if not m:
            return None
        toks.append(next((k, v) for k, v in zip("snwp", m.groups()) if v is not None))
        pos = m.end()
    i, parts, used = 0, [], set()

    def lit(k, v):
        if k == "n":
            return v
        if k == "s" and "\\" not in v and "\x00" not in v:
            return v
        return None

    def kw(j, word):
        return j < len(toks) and toks[j][0] == "w" and toks[j][1].upper() == word

    while i < len(toks):
        if toks[i][0] != "w" or toks[i][1] not in columns:
            return None
        col = toks[i][1]
        i += 1
        used.add(col)
        if kw(i, "IS"):
            neg = kw(i + 1, "NOT")
            if not kw(i + 1 + neg, "NULL"):
                return None
            parts.append(f"{q(col)} IS {'NOT ' if neg else ''}NULL")
            i += 2 + neg
        elif i < len(toks) and toks[i] == ("p", "=") or i < len(toks) and toks[i][1] in ("<>", "!="):
            op = toks[i][1]
            if i + 1 >= len(toks) or lit(*toks[i + 1]) is None:
                return None
            parts.append(f"{q(col)} {op} {lit(*toks[i + 1])}")
            i += 2
        else:
            neg = kw(i, "NOT")
            i += neg
            if not kw(i, "IN") or i + 1 >= len(toks) or toks[i + 1][1] != "(":
                return None
            i += 2
            vals = []
            while True:
                if i >= len(toks) or lit(*toks[i]) is None:
                    return None
                vals.append(lit(*toks[i]))
                i += 1
                if i < len(toks) and toks[i][1] == ",":
                    i += 1
                    continue
                if i < len(toks) and toks[i][1] == ")":
                    i += 1
                    break
                return None
            parts.append(f"{q(col)} {'NOT ' if neg else ''}IN ({','.join(vals)})")
        if i < len(toks):
            if not kw(i, "AND"):
                return None
            i += 1
            if i >= len(toks):
                return None
    return (" AND ".join(parts), used) if parts else None


class _Prober:
    def __init__(self, g, runner: SelectOnlyRunner, quote: str, bare: bool, cap: int):
        self.g, self.run, self.quote, self.bare, self.cap = g, runner, quote, bare, _Cap(cap)

    def q(self, name: str) -> str:
        return quote_ident(name, self.quote)

    def t(self, tid: str) -> str:
        n = self.g.nodes[tid]
        return self.q(n["table"]) if self.bare else f"{self.q(n['schema'])}.{self.q(n['table'])}"

    def verdict(self, check: str, verified, rate=None, n: int = 0, **extra) -> dict:
        return {"verified": verified, "check": check, "rate": None if rate is None else round(rate, 3), "n": n, **extra}

    def relation(self, it: dict) -> dict:
        c = "relation_overlap"
        if len(it["from_columns"]) != 1:
            return self.verdict("relation_overlap_multi_column", None)
        if not self.cap.take(2):
            return self.verdict(c, None, skipped="query_cap")
        fc, tc, q = it["from_columns"][0], it["to_columns"][0], self.q
        vals = [r[0] for r in self.run.run(f"SELECT DISTINCT {q(fc)} FROM {self.t(it['from_table'])} "
                                           f"WHERE {q(fc)} IS NOT NULL LIMIT {REL_SAMPLE}")]
        lits = sorted({x for x in map(_lit, vals) if x is not None})
        if not lits:
            return self.verdict(c, None)
        found = self.run.run(f"SELECT DISTINCT {q(tc)} FROM {self.t(it['to_table'])} WHERE {q(tc)} IN ({','.join(lits)}) "
                             f"LIMIT {len(lits)}")
        hit = {x for x in map(_lit, [r[0] for r in found]) if x in set(lits)}
        rate = len(hit) / len(lits)
        return self.verdict(c, rate >= REL_PASS, rate, len(lits))

    def codes(self, it: dict) -> dict:
        c = "codes_present"
        if not it.get("codes"):
            return self.verdict("none", None)
        if not self.cap.take(1):
            return self.verdict(c, None, skipped="query_cap")
        col, q = it["column"], self.q
        rows = self.run.run(f"SELECT DISTINCT {q(col)} FROM {self.t(it['table'])} WHERE {q(col)} IS NOT NULL LIMIT {CODE_SAMPLE + 1}")
        have, claimed = {_norm(r[0]) for r in rows}, {str(k).strip() for k in it["codes"]}
        rate = len(claimed & have) / len(claimed)
        return self.verdict(c, True if rate == 1 else (False if len(rows) <= CODE_SAMPLE else None), rate, len(claimed))

    def grain(self, it: dict) -> dict:
        """Sample-based: the first rows of the table only, so a pass is a signal and never a proof. Claimed columns that
        are the primary key (or a unique key), or missing, are unique by definition and stay unverified."""
        node = self.g.nodes[it["table"]]
        key = it.get("grain_columns") or []
        trivial = [node.get("primary_key") or [], *[u.get("columns") or [] for u in node.get("unique_keys") or []]]
        if not key or any(k and set(k) == set(key) for k in trivial):
            return self.verdict("grain_pk_trivial", None)
        c = "grain_sample_unique"
        if not self.cap.take(2):
            return self.verdict(c, None, skipped="query_cap")
        cols = ",".join(self.q(k) for k in key)
        inner = f"(SELECT {cols} FROM {self.t(it['table'])} LIMIT {GRAIN_SAMPLE}) s"
        total = _int(self.run.run(f"SELECT COUNT(*) FROM {inner}"))
        if not total:
            return self.verdict(c, None)
        distinct = _int(self.run.run(f"SELECT COUNT(*) FROM (SELECT DISTINCT {cols} FROM {inner}) d"))
        return self.verdict(c, distinct == total, distinct / total, total)

    def filters(self, it: dict) -> dict:
        c = "default_filter_selectivity"
        texts = it.get("default_filters") or []
        if not texts:
            return self.verdict("none", None)
        cols = {x["name"] for x in self.g.nodes[it["table"]]["columns"]}
        built = [parse_filter(t, cols, self.q) for t in texts]
        ok = [b for b in built if b]
        if not ok:
            return self.verdict(c + "_unparsed", None)
        rates, total, fails = [], 0, 0
        for sql, used in ok:
            if not self.cap.take(1):
                return self.verdict(c, None, skipped="query_cap")
            inner = f"(SELECT {','.join(self.q(x) for x in sorted(used))} FROM {self.t(it['table'])} LIMIT {FILTER_SAMPLE}) s"
            rows = self.run.run(f"SELECT COUNT(*), SUM(CASE WHEN {sql} THEN 1 ELSE 0 END) FROM {inner}")
            total, kept = _int(rows), _int(rows, 1)
            if not total:
                return self.verdict(c, None)
            rates.append(kept / total)
            fails += kept == 0
        return self.verdict(c, False if fails else (True if len(ok) == len(built) else None), min(rates), total)


def probe_items(items: list[dict], g, runner: SelectOnlyRunner | None, quote: str | None = None, *, bare: bool = False,
                cap: int = QUERY_CAP) -> list[dict]:
    """One verdict per accepted item (`{"kind", "index", "item"}` as `validate` returns them), in the same order.
    No runner means every item stays unverified. `quote` defaults to the map's source dialect. Grain verdicts are samples."""
    pr = _Prober(g, runner, quote or quote_for(dialect_of(g)), bare, cap) if runner is not None else None
    out = []
    for ent in items:
        kind, it = ent["kind"], ent["item"]
        checks = {"relations": ["relation"], "columns": ["codes"], "tables": ["grain", "filters"]}.get(kind, [])
        v = {"verified": None, "check": "none", "rate": None, "n": 0}
        if pr is None and checks:
            v = {"verified": None, "check": "no_source", "rate": None, "n": 0}
        elif checks:
            vs = []
            for name in checks:
                try:
                    if name == "grain" and not (it.get("grain") or it.get("grain_columns")):
                        continue
                    vs.append(getattr(pr, name)(it))
                except Exception as exc:  # noqa: BLE001  (the message can hold values; keep the type only)
                    vs.append(pr.verdict(name, None, error=type(exc).__name__))
            vs = [x for x in vs if x["check"] != "none"] or [v]
            # several checks on one item: false if any fails, true only if every check passed
            v = vs[0] if len(vs) == 1 else {
                "verified": False if any(x["verified"] is False for x in vs) else (True if all(x["verified"] for x in vs) else None),
                "check": "+".join(x["check"] for x in vs), "rate": min((x["rate"] for x in vs if x["rate"] is not None), default=None),
                "n": min(x["n"] for x in vs), **({"error": next(x["error"] for x in vs if "error" in x)} if any("error" in x for x in vs) else {})}
        out.append({"kind": kind, "index": ent["index"], **v})
        devlog.trace("index.probe", task=kind, index=ent["index"], check=v["check"], verified=v["verified"], rate=v["rate"], n=v["n"])
    return out
