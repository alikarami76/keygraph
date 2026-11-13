"""The records every code extractor yields, plus the shared constants (kinds, confidences, snippet masking)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..profile import is_pii_name

KINDS = ("declare_table", "declare_column", "sql_write", "sql_read", "use")
KIND_ORDER = {k: i for i, k in enumerate(KINDS)}

CONF_DECLARE = 1.0
CONF_SQLGLOT = 0.95
CONF_SQLGLOT_UNQUALIFIED = 0.85
CONF_SQL_TOKENS = 0.7
CONF_USE_NAMED = 0.8
CONF_USE_ELSEWHERE = 0.5
CONF_PATTERN_DEFAULT = 0.9
SNIPPET_MAX = 160


@dataclass(frozen=True)
class CodeRef:
    repo: str
    path: str
    line: int
    kind: str
    table_id: str
    column: str | None
    symbol: str | None
    rule: str
    confidence: float
    snippet: str

    def key(self) -> tuple:
        """Identity for de-duplication: the first ref (in rule order) for a place, kind, table and column wins."""
        return (self.path, self.line, self.kind, self.table_id, self.column or "")

    def sort_key(self) -> tuple:
        return (self.repo, self.path, self.line, KIND_ORDER[self.kind], self.table_id, self.column or "",
                self.symbol or "", self.rule)


@dataclass(frozen=True)
class Unresolved:
    repo: str
    path: str
    line: int
    kind: str
    name: str
    rule: str

    def sort_key(self) -> tuple:
        return (self.repo, self.path, self.line, self.kind, self.name, self.rule)


_URL_CRED = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")
_LOCAL = re.compile(r"[\w.+-]+")
_DOMAIN = re.compile(r"[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
_KEYRUN = re.compile(r"[\w$.\-]+")
_KV_TAIL = re.compile(r"""(?P<q0>['"]?)\s*(?P<op>=>|:=|==|=|:)\s*(?P<q>['"])(?P<v>[^'"\n]*)(?P=q)""")
# unquoted `key = value` / `key: value`; the key must name a secret (see _secret_key)
_PLAIN_KV = re.compile(r"(?<![\w.$])(?P<k>[A-Za-z_]\w{0,60})(?P<sep>\s*[=:]\s*)(?![*?'\"<:$@\s])(?P<v>[^\s'\",;&)]+)")
_KEY_START = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$")
_SECRET_SUBSTR = ("password", "passwd", "secret", "token", "api_key", "apikey", "access_key", "accesskey")
_SECRET_WORDS = {"pass", "pwd", "auth", "authorization"}
_LONG_TOKEN = re.compile(r"(?<![\w/.-])(?=[A-Za-z0-9_\-+/=]*\d)(?=[A-Za-z0-9_\-+/=]*[A-Za-z])[A-Za-z0-9_\-+/=]{32,}(?![\w/.-])")


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name.replace("-", "_")).lower()


def _secret_key(key: str) -> bool:
    """A key that names a secret (db_password, client_secret, auth_token, DB_PASS ...) and not a reference to one
    (token_id, token_ids)."""
    k = _snake(key)
    words = k.split("_")
    if words[-1] in ("id", "ids"):
        return False
    return any(w in k for w in _SECRET_SUBSTR) or bool(_SECRET_WORDS & set(words))


def _mask_emails(text: str) -> str:
    """local@domain.tld -> ***@***. Same matches as one regex over the whole text, but linear: it only tries the run of
    local-part characters that ends right before an "@" (a regex tried every start inside a long run)."""
    if "@" not in text:
        return text
    out, pos, n = [], 0, len(text)
    while pos < n:
        m = _LOCAL.search(text, pos)
        if not m:
            break
        if m.end() < n and text[m.end()] == "@" and (d := _DOMAIN.match(text, m.end() + 1)):
            out += [text[pos:m.start()], "***@***"]
            pos = d.end()
        else:
            out.append(text[pos:m.end()])
            pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def _mask_quoted_kv(text: str) -> str:
    """`key = 'value'` / `key: "value"` with a personal or secret key name: the value becomes ***. Same matches as one
    regex with an unbounded key, but linear: the key of a match is the whole run of key characters (from its first
    letter, `_` or `$`) that sits right before the operator."""
    out, pos, n = [], 0, len(text)
    while pos < n:
        run = _KEYRUN.search(text, pos)
        if not run:
            break
        first = next((i for i in range(run.start(), run.end()) if text[i] in _KEY_START), None)
        tail = _KV_TAIL.match(text, run.end()) if first is not None else None
        if not tail:
            out.append(text[pos:run.end()])
            pos = run.end()
            continue
        key = text[first:run.end()].rsplit(".", 1)[-1].lstrip("$")
        out.append(text[pos:first])
        if tail.group("v") and is_pii_name(_snake(key)):
            out.append(text[first:tail.start("v")] + "***" + text[tail.end("v"):tail.end()])
        else:
            out.append(text[first:tail.end()])
        pos = tail.end()
    out.append(text[pos:])
    return "".join(out)


def mask_secrets(text: str) -> str:
    """Mask secret-looking values in one line of code: emails, credentials in URLs, values assigned to keys whose
    name looks personal or secret (the same name rule as value profiles: password, token, api_key, email ...) and
    long opaque tokens."""
    text = _URL_CRED.sub(r"\1***@", text)
    text = _mask_emails(text)
    text = _mask_quoted_kv(text)

    def plain(m: re.Match) -> str:  # a dotted value is a column reference (a.token = b.token), not a secret
        if not _secret_key(m.group("k")) or re.match(r"[\w$]+\.[\w$]", m.group("v")):
            return m.group(0)
        return m.group("k") + m.group("sep") + "***"

    text = _PLAIN_KV.sub(plain, text)
    return _LONG_TOKEN.sub("***", text)


def snippet(line_text: str) -> str:
    """The matched line, trimmed to 160 characters, with secret-looking values masked."""
    s = mask_secrets(line_text.strip())
    return s if len(s) <= SNIPPET_MAX else s[:SNIPPET_MAX - 1] + "…"
