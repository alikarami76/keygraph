"""Query fingerprints: one normalised text and a short hash per query shape.

The fingerprint always comes from these regex rules, with or without sqlglot, so the same query logged from two
machines gets the same fingerprint. Rules: strip comments, replace string, number and date literals and
placeholders with ?, collapse IN (?, ?, ...) to IN (?) and VALUES (...), (...) to one tuple, lowercase everything
outside quoted identifiers, and
write the tokens back with single spaces (none after '(' or before ')' and ',')."""

from __future__ import annotations

import hashlib
import re

from ..code.refs import mask_secrets
from ..code.sql import neutralize

_STRING = re.compile(r"'(?:''|\\.|[^'\\])*'")
# strings and comments in ONE pass so whichever starts first wins ('--' inside a string is not a comment)
_STR_OR_COMMENT = re.compile(r"(?P<s>'(?:''|\\.|[^'\\])*')|--[^\n]*|/\*[\s\S]*?\*/|^[ \t]*#[^\n]*", re.M)
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])")
_TOKENS = re.compile(r"\"(?:\"\"|[^\"])*\"|`[^`]*`|\[[^\]]*\]|\?|[A-Za-z_][\w$]*(?:\.(?:[A-Za-z_][\w$]*|\*))*"
                     r"|\d+|<>|!=|<=|>=|::|\|\||\S")
_IN_LIST = re.compile(r"\bin \(\?(?:, \?)*\)")
_VALUES = re.compile(r"\bvalues (\([^()]*\))(?:, \([^()]*\))+")


def normalize(sql: str) -> str:
    s = _STR_OR_COMMENT.sub(lambda m: "?" if m.group("s") else " ", sql)
    s = neutralize(s)  # placeholders become 0, then a number
    s = _NUMBER.sub("?", s)
    toks = [t if t[0] in "\"`[" else t.lower() for t in _TOKENS.findall(s)]
    while toks and toks[-1] == ";":
        toks.pop()
    s = " ".join(toks).replace("( ", "(").replace(" )", ")").replace(" ,", ",")
    s = _IN_LIST.sub("in (?)", s)
    return _VALUES.sub(r"values \1", s)


def fingerprint(sql: str) -> tuple[str, str]:
    """(16-hex fingerprint, normalised SQL)."""
    n = normalize(sql)
    return hashlib.sha1(n.encode("utf-8")).hexdigest()[:16], n


def mask_sql(sql: str, keep_literals: bool = False) -> str:
    """The SQL as stored: literals replaced by ? unless keep_literals; secret-looking values are always masked."""
    s = sql if keep_literals else _NUMBER.sub("?", _STRING.sub("?", sql))
    return "\n".join(mask_secrets(line) for line in s.splitlines()).strip()
