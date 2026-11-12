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


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
_URL_CRED = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")
_KV = re.compile(r"""(?P<k>[A-Za-z_$][\w$.\-]*)(?P<q0>['"]?)\s*(?P<op>=>|:=|==|=|:)\s*(?P<q>['"])(?P<v>[^'"\n]*)(?P=q)""")
_PLAIN_KV = re.compile(r"\b(\w{0,40}(?:pass|pwd|secret|token|api_?key)\w{0,20}\s*[=:]\s*)(?![*?'\"\s])[^\s'\",;&)]+", re.IGNORECASE)
_LONG_TOKEN = re.compile(r"(?<![\w/.-])(?=[A-Za-z0-9_\-+/=]*\d)(?=[A-Za-z0-9_\-+/=]*[A-Za-z])[A-Za-z0-9_\-+/=]{32,}(?![\w/.-])")


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name.replace("-", "_")).lower()


def mask_secrets(text: str) -> str:
    """Mask secret-looking values in one line of code: emails, credentials in URLs, values assigned to keys whose
    name looks personal or secret (the same name rule as value profiles: password, token, api_key, email ...) and
    long opaque tokens."""
    text = _URL_CRED.sub(r"\1***@", text)
    text = _EMAIL.sub("***@***", text)

    def kv(m: re.Match) -> str:
        key = m.group("k").rsplit(".", 1)[-1].lstrip("$")
        if not m.group("v") or not is_pii_name(_snake(key)):
            return m.group(0)
        whole, base = m.group(0), m.start()
        return whole[:m.start("v") - base] + "***" + whole[m.end("v") - base:]

    text = _KV.sub(kv, text)
    text = _PLAIN_KV.sub(r"\1***", text)
    return _LONG_TOKEN.sub("***", text)


def snippet(line_text: str) -> str:
    """The matched line, trimmed to 160 characters, with secret-looking values masked."""
    s = mask_secrets(line_text.strip())
    return s if len(s) <= SNIPPET_MAX else s[:SNIPPET_MAX - 1] + "…"
