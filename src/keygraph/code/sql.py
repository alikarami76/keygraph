"""SQL in code: string literals and .sql files, resolved to tables and columns of the vocabulary.

With sqlglot installed (pip install 'keygraph[code]') statements are parsed: aliases resolve, write targets and
their columns become sql_write, everything else sql_read. Without it, or when a statement does not parse, table
names after FROM / JOIN / UPDATE / INTO are matched as tokens with a lower confidence. The query log reuses
`analyze` to link a query to tables and columns."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .refs import CONF_SQL_TOKENS, CONF_SQLGLOT, CONF_SQLGLOT_UNQUALIFIED, CodeRef, Unresolved, snippet
from .vocab import Vocab
from .walk import Lines

try:  # optional extra keygraph[code]
    import sqlglot
    from sqlglot import exp
    logging.getLogger("sqlglot").setLevel(logging.ERROR)
except ImportError:  # pragma: no cover - exercised by the no-sqlglot test run
    sqlglot = None
    exp = None

DIALECTS = {"mysql": "mysql", "mariadb": "mysql", "postgres": "postgres", "postgresql": "postgres", "tsql": "tsql",
            "sqlserver": "tsql", "mssql": "tsql", "clickhouse": "clickhouse", "sqlite": "sqlite"}
SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|REPLACE|WITH|MERGE)\b", re.I)
SQL_INSIDE = re.compile(r"\b(?:FROM|JOIN)\s+[`\"\[]?\w")  # upper case only: "from here" in prose is not SQL
WRITE_START = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE|MERGE)\b", re.I)
_STRINGS = {
    "default": re.compile(
        r"//[^\n]*|/\*[\s\S]*?\*/"
        r"|(?P<b>`(?:\\[\s\S]|[^`\\])*`)"
        r"|(?P<d>\"(?:\\[\s\S]|[^\"\\])*\")|(?P<s>'(?:\\[\s\S]|[^'\\])*')"),
    "py": re.compile(
        r"#[^\n]*"
        r"|(?P<t>[rRbBuUfF]{0,2}(?:\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''))"
        r"|(?P<d>[rRbBuUfF]{0,2}\"(?:\\.|[^\"\\\n])*\")|(?P<s>[rRbBuUfF]{0,2}'(?:\\.|[^'\\\n])*')"),
    "php": re.compile(
        r"//[^\n]*|#(?!\[)[^\n]*|/\*[\s\S]*?\*/"
        r"|<<<[ \t]*(?P<hq>['\"]?)(?P<hid>[A-Za-z_]\w*)(?P=hq)\r?\n(?P<h>[\s\S]*?)\r?\n[ \t]*(?P=hid)\b"
        r"|(?P<d>\"(?:\\[\s\S]|[^\"\\])*\")|(?P<s>'(?:\\[\s\S]|[^'\\])*')"),
    "rb": re.compile(
        r"#[^\n]*|(?P<d>\"(?:\\[\s\S]|[^\"\\])*\")|(?P<s>'(?:\\[\s\S]|[^'\\])*')"),
}
_PLACEHOLDERS = [
    re.compile(r"\{\$[^{}]*\}|\$\{[^{}]*\}|#\{[^{}]*\}"),  # {$x} ${x} #{x}
    re.compile(r"%\(\w+\)s|%[sdifr]"),                   # %(name)s %s
    re.compile(r"\{\w*\}"),                               # {name} {} (format strings)
    re.compile(r"(?<![:\w]):[A-Za-z_]\w*"),               # :name (not ::cast)
    re.compile(r"\$[A-Za-z_]\w*(?:->\w+)*"),              # $var, $obj->field
    re.compile(r"\?"),
]
_TOKEN = re.compile(r"\b(FROM|JOIN|UPDATE|INTO)\s+((?:[`\"\[]?\w+[`\"\]]?\s*\.\s*)?[`\"\[]?\w+[`\"\]]?)", re.I)
_SPLIT = re.compile(r";[ \t]*(?:\r?\n|$)")


def strings(text: str, lang: str) -> list[tuple[int, str]]:
    """(offset of the literal body, body) for every string literal; comments are skipped."""
    rx = _STRINGS.get(lang, _STRINGS["default"])
    out = []
    for m in rx.finditer(text):
        for g in ("h", "t", "b", "d", "s"):
            if g in rx.groupindex and m.group(g) is not None:
                if g == "h":
                    out.append((m.start("h"), m.group("h")))
                else:
                    raw = m.group(g)
                    q = 3 if g == "t" else 1
                    pre = len(raw) - len(raw.lstrip("rRbBuUfF"))
                    out.append((m.start(g) + pre + q, raw[pre + q:len(raw) - q]))
                break
    return out


def is_sql(s: str) -> bool:
    return bool(SQL_START.match(s) or SQL_INSIDE.search(s))


def statements(text: str, lang: str) -> list[tuple[int, str]]:
    """(offset, sql) for every SQL candidate in a file: string literals, or a whole .sql file split on ';'."""
    if lang == "sql":
        out, pos = [], 0
        body = re.sub(r"--[^\n]*", lambda m: " " * len(m.group(0)), text)
        for m in list(_SPLIT.finditer(body)) + [None]:
            end = m.start() if m else len(body)
            chunk = body[pos:end]
            if chunk.strip():
                lead = len(chunk) - len(chunk.lstrip())
                out.append((pos + lead, chunk.strip()))
            pos = m.end() if m else end
        return out
    return [(off, s) for off, s in strings(text, lang) if is_sql(s)]


def neutralize(sql: str) -> str:
    """Replace interpolations and placeholders ({$x}, ${x}, %s, ?, :name, $var) with a literal 0."""
    for rx in _PLACEHOLDERS:
        sql = rx.sub("0", sql)
    return sql


@dataclass(frozen=True)
class SqlHit:
    kind: str  # sql_read | sql_write
    table: str  # name as written (schema.table or table)
    column: str | None
    role: str  # read | write | filter | join | group
    confidence: float
    via: str  # sqlglot | tokens


def _name(t) -> str:
    return f"{t.db}.{t.name}" if t.db else t.name


def _role(col) -> str:
    node = col.parent
    while node is not None:
        if isinstance(node, exp.Where):
            return "filter"
        if isinstance(node, exp.Join):
            return "join"
        if isinstance(node, exp.Group):
            return "group"
        if isinstance(node, (exp.Select, exp.Update, exp.Delete, exp.Insert)):
            return "read"
        node = node.parent
    return "read"


def _parse(sql: str, vocab: Vocab | None, dialect: str | None) -> list[SqlHit] | None:
    if sqlglot is None:
        return None
    try:
        trees = sqlglot.parse(sql, read=DIALECTS.get(dialect or "", None))
    except Exception:  # noqa: BLE001 - sqlglot raises several error types; any failure falls back to tokens
        return None
    hits: list[SqlHit] = []
    for tree in trees:
        if tree is None or isinstance(tree, exp.Command):
            return None
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        tables = [t for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in ctes]
        if not tables:
            continue
        alias = {}
        for t in tables:
            alias.setdefault(t.alias_or_name.lower(), _name(t))
            alias.setdefault(t.name.lower(), _name(t))
        target, written = None, set()
        if isinstance(tree, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
            node = tree.this
            if isinstance(node, exp.Schema):
                written |= {i.name.lower() for i in node.expressions if isinstance(i, exp.Identifier)}
                node = node.this
            if isinstance(node, exp.Table):
                target = _name(node)
            if isinstance(tree, exp.Update):
                for eq in tree.expressions:
                    if isinstance(eq, exp.EQ) and isinstance(eq.this, exp.Column):
                        written.add(eq.this.name.lower())
        names = sorted({_name(t) for t in tables})
        for n in names:
            hits.append(SqlHit("sql_write" if n == target else "sql_read", n, None, "write" if n == target else "read",
                               CONF_SQLGLOT, "sqlglot"))
        if target:
            for c in sorted(written):
                hits.append(SqlHit("sql_write", target, c, "write", CONF_SQLGLOT, "sqlglot"))
        for col in tree.find_all(exp.Column):
            cname = col.name
            if not cname or cname == "*":
                continue
            q = col.table.lower() if col.table else ""
            role = _role(col)
            if target and isinstance(tree, exp.Update) and cname.lower() in written and role == "read":
                continue
            if q:
                if q not in alias:
                    continue
                hits.append(SqlHit("sql_read", alias[q], cname, role, CONF_SQLGLOT, "sqlglot"))
            elif len(names) == 1:
                hits.append(SqlHit("sql_read", names[0], cname, role, CONF_SQLGLOT, "sqlglot"))
            elif vocab is not None:
                owners = [n for n in names if any(vocab.column(t, cname) for t in vocab.resolve_table(n))]
                if len(owners) == 1:
                    hits.append(SqlHit("sql_read", owners[0], cname, role, CONF_SQLGLOT_UNQUALIFIED, "sqlglot"))
    return hits


def _tokens(sql: str) -> list[SqlHit]:
    write = WRITE_START.match(sql)
    verb = write.group(1).upper() if write else ""
    hits, first_from = [], True
    for m in _TOKEN.finditer(sql):
        kw, name = m.group(1).upper(), re.sub(r"[`\"\[\]\s]", "", m.group(2))
        if name.isdigit():
            continue
        is_write = kw in ("UPDATE", "INTO") and verb in ("INSERT", "UPDATE", "REPLACE", "MERGE")
        if kw == "FROM" and verb == "DELETE" and first_from:
            is_write = True
        if kw == "FROM":
            first_from = False
        hits.append(SqlHit("sql_write" if is_write else "sql_read", name, None, "write" if is_write else "read",
                           CONF_SQL_TOKENS, "tokens"))
    return hits


def analyze(sql: str, vocab: Vocab | None = None, dialect: str | None = None) -> list[SqlHit]:
    """Tables and columns one SQL text touches (several statements allowed). Never raises."""
    clean = neutralize(sql)
    hits = _parse(clean, vocab, dialect)
    if hits is None:
        hits = _tokens(clean)
    return list(dict.fromkeys(hits))


def sql_refs(repo: str, path: str, lang: str, text: str, vocab: Vocab, dialect: str | None = None,
             lines: Lines | None = None) -> tuple[list[CodeRef], list[Unresolved], list[int]]:
    """SQL refs of one file, unresolved table names, and the lines SQL statements span (the uses pass skips them)."""
    L = lines or Lines(text)
    refs: list[CodeRef] = []
    unres: list[Unresolved] = []
    spans: set[int] = set()
    for off, sql in statements(text, lang):
        strong = bool(SQL_START.match(sql))
        spans.update(range(L.line(off), L.line(off + max(len(sql) - 1, 0)) + 1))
        for h in analyze(sql, vocab, dialect):
            ids = vocab.resolve_table(h.table)
            rule = f"sql:{h.via}"
            word = re.search(r"\b" + re.escape((h.column or h.table.rsplit(".", 1)[-1])) + r"\b", sql, re.I)
            line = L.line(off + (word.start() if word else 0))
            if not ids:
                if h.column is None and strong:
                    unres.append(Unresolved(repo, path, line, h.kind, h.table, rule))
                continue
            for t in ids:
                col = vocab.column(t, h.column) if h.column else None
                if h.column and col is None:
                    continue
                refs.append(CodeRef(repo, path, line, h.kind, t, col, None, rule, round(h.confidence / len(ids), 4),
                                    snippet(L.get(line))))
    seen, out = set(), []
    for r in refs:
        if r.key() not in seen:
            seen.add(r.key())
            out.append(r)
    return sorted(out, key=CodeRef.sort_key), sorted(set(unres), key=Unresolved.sort_key), sorted(spans)


def _resolved_alias(tree) -> dict[str, str]:
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    alias: dict[str, str] = {}
    for t in tree.find_all(exp.Table):
        if t.name and t.name.lower() not in ctes:
            alias.setdefault(t.alias_or_name.lower(), _name(t))
            alias.setdefault(t.name.lower(), _name(t))
    return alias


def _side(col, alias: dict[str, str], vocab: Vocab) -> tuple[str, str] | None:
    """(table id, real column) for one side of a comparison, or None when it does not resolve to one real column."""
    if not col.name or col.name == "*":
        return None
    if col.table:
        names = [alias[col.table.lower()]] if col.table.lower() in alias else []
    else:
        names = sorted(set(alias.values()))
    found = set()
    for n in names:
        ids = vocab.resolve_table(n)
        if len(ids) != 1:
            continue
        real = vocab.column(ids[0], col.name)
        if real:
            found.add((ids[0], real))
    return next(iter(found)) if len(found) == 1 else None


def sql_joins(sql: str, vocab: Vocab, dialect: str | None = None) -> list[tuple[str, str, str, str]]:
    """Equality joins `a.x = b.y` of one SQL text (JOIN ... ON, and WHERE of implicit joins) where both sides resolve
    to real columns: sorted, de-duplicated (left_table_id, left_col, right_table_id, right_col) with the smaller
    (table, column) first. Literals, other comparisons and unresolved columns are ignored; [] without sqlglot."""
    if sqlglot is None or vocab is None:
        return []
    try:
        trees = sqlglot.parse(neutralize(sql), read=DIALECTS.get(dialect or "", None))
    except Exception:  # noqa: BLE001 - any parse failure means no joins
        return []
    out: set[tuple[str, str, str, str]] = set()
    for tree in trees:
        if tree is None or isinstance(tree, exp.Command):
            continue
        alias = _resolved_alias(tree)
        if not alias:
            continue
        for eq in tree.find_all(exp.EQ):
            if not (isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column)):
                continue
            node = eq.parent
            while node is not None and not isinstance(node, (exp.Join, exp.Where, exp.Having)):
                node = node.parent
            if not isinstance(node, (exp.Join, exp.Where)):
                continue
            a, b = _side(eq.this, alias, vocab), _side(eq.expression, alias, vocab)
            if a and b and a != b:
                a, b = sorted([a, b])
                out.add((a[0], a[1], b[0], b[1]))
    return sorted(out)


def sql_join_refs(repo: str, path: str, lang: str, text: str, vocab: Vocab, dialect: str | None = None,
                  lines: Lines | None = None) -> list[tuple[str, str, int, str, str, str, str]]:
    """(repo, path, line, a_table, a_col, b_table, b_col) for every join in the SQL of one file, sorted."""
    if sqlglot is None:
        return []
    L = lines or Lines(text)
    out = set()
    for off, sql in statements(text, lang):
        for at, ac, bt, bc in sql_joins(sql, vocab, dialect):
            pair = rf"\b{re.escape(ac)}\b\s*=\s*[\w.`\"\[\]]*\b{re.escape(bc)}\b|\b{re.escape(bc)}\b\s*=\s*[\w.`\"\[\]]*\b{re.escape(ac)}\b"
            m = re.search(pair, sql, re.I) or re.search(r"\b" + re.escape(ac) + r"\b", sql, re.I)
            out.add((repo, path, L.line(off + (m.start() if m else 0)), at, ac, bt, bc))
    return sorted(out)
