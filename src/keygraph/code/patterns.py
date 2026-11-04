"""Pattern rules (built-in and from code-patterns.yaml) and the engine that runs them over one file.

A rule is a regex with named groups; see orm.py for the group names. Declarations bind to tables in two ways:
scope "entity" (built-ins) binds a column to the table declared by the class, struct or model that contains it;
scope "file" (the default for user patterns) binds it to the nearest preceding table declaration in the file, else
to the one vocabulary table that has that column, else it is reported as unresolved."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import orm
from .refs import CONF_DECLARE, CONF_PATTERN_DEFAULT, CodeRef, Unresolved, snippet
from .vocab import Vocab
from .walk import LANGS, Lines

PATTERN_KINDS = ("declare_table", "declare_column", "use", "sql_read", "sql_write")
GROUPS = ("table", "column", "prop", "symbol", "entity")
KNOWN_LANGS = frozenset(LANGS.values())


class PatternError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    name: str
    kind: str
    regex: re.Pattern
    languages: tuple[str, ...] = ()
    confidence: float = CONF_PATTERN_DEFAULT
    scope: str = "file"
    fallback: bool = False
    builtin: bool = False

    def applies(self, lang: str) -> bool:
        return not self.languages or lang in self.languages

    def spec(self) -> dict:
        """A stable description, used to tell when an index was built with different rules."""
        return {"name": self.name, "kind": self.kind, "regex": self.regex.pattern, "languages": list(self.languages),
                "confidence": self.confidence, "scope": self.scope, "fallback": self.fallback}


def compile_rule(d: dict, builtin: bool = False) -> Rule:
    if not isinstance(d, dict):
        raise PatternError(f"pattern {d!r}: expected a mapping with name, kind and regex")
    name = str(d.get("name") or "").strip()
    if not name:
        raise PatternError(f"pattern {d!r}: missing name")
    kind = d.get("kind")
    if kind not in PATTERN_KINDS:
        raise PatternError(f"pattern {name}: kind must be one of {', '.join(PATTERN_KINDS)}, got {kind!r}")
    try:
        rx = re.compile(str(d.get("regex") or ""), re.M | (re.I if d.get("ignore_case") else 0))
    except re.error as exc:
        raise PatternError(f"pattern {name}: invalid regex: {exc}") from None
    groups = set(rx.groupindex)
    need = {"declare_table": {"table"}, "sql_read": {"table"}, "sql_write": {"table"}}.get(kind, {"column", "prop"})
    if not groups & need:
        raise PatternError(f"pattern {name}: a {kind} regex needs a named group {' or '.join(sorted(need))}")
    unknown = groups - set(GROUPS)
    if unknown:
        raise PatternError(f"pattern {name}: unknown named group(s) {', '.join(sorted(unknown))}; use {', '.join(GROUPS)}")
    langs = d.get("languages") or []
    if isinstance(langs, str):
        langs = [langs]
    bad = [l for l in langs if l not in KNOWN_LANGS]
    if bad:
        raise PatternError(f"pattern {name}: unknown language(s) {', '.join(bad)}; use {', '.join(sorted(KNOWN_LANGS))}")
    conf = d.get("confidence", CONF_DECLARE if builtin else CONF_PATTERN_DEFAULT)
    if not isinstance(conf, (int, float)) or not 0 < conf <= 1:
        raise PatternError(f"pattern {name}: confidence must be a number in (0, 1]")
    scope = d.get("scope", "entity" if builtin else "file")
    if scope not in ("entity", "file"):
        raise PatternError(f"pattern {name}: scope must be entity or file")
    return Rule(name, kind, rx, tuple(sorted(langs)), float(conf), scope, bool(d.get("fallback")), builtin)


def builtin_rules() -> list[Rule]:
    return [compile_rule(d, builtin=True) for d in orm.BUILTIN_RULES]


def load_patterns(paths) -> list[Rule]:
    """User rules from YAML (needs PyYAML) or JSON files, each holding {"patterns": [...]}. Names must be unique."""
    rules: list[Rule] = []
    for p in paths:
        p = Path(p)
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() == ".json":
            raw = json.loads(text)
        else:
            try:
                import yaml
            except ImportError:
                raise PatternError(f"{p.name}: reading YAML needs PyYAML (pip install 'keygraph[yaml]'), or write it as JSON") from None
            try:
                raw = yaml.safe_load(text) or {}
            except yaml.YAMLError as exc:
                raise PatternError(f"{p.name}: not valid YAML: {exc}") from None
        if not isinstance(raw, dict) or not isinstance(raw.get("patterns"), list):
            raise PatternError(f"{p.name}: expected a top-level 'patterns:' list")
        rules += [compile_rule(d) for d in raw["patterns"]]
    names = [r.name for r in rules] + [d["name"] for d in orm.BUILTIN_RULES]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise PatternError(f"duplicate pattern name(s): {', '.join(dup)}")
    return rules


# ---- blocks: the class / struct / model a declaration belongs to -------------------------------------------
@dataclass
class Block:
    name: str
    start: int
    end: int
    parent: int | None = None
    tables: list[str] = field(default_factory=list)  # resolved table ids
    declared: bool = False  # the block declares a table (resolved or not)


def _brace_end(text: str, i: int, stop: int) -> int | None:
    """End offset of the {...} that opens at paren depth 0 after i, or None if ';' or stop comes first."""
    depth, n = 0, len(text)
    while i < min(stop, n):
        ch = text[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif depth == 0 and ch == ";":
            return None
        elif depth == 0 and ch == "{":
            level = 0
            for j in range(i, n):
                if text[j] == "{":
                    level += 1
                elif text[j] == "}":
                    level -= 1
                    if level == 0:
                        return j + 1
            return n
        i += 1
    return None


def find_blocks(text: str, lang: str) -> list[Block]:
    blocks: list[Block] = []
    if lang in orm.INDENT_HEADERS:
        ms = list(re.finditer(orm.INDENT_HEADERS[lang], text, re.M))
        for m in ms:
            indent = len(m.group(1).expandtabs())
            end = len(text)
            pos = text.find("\n", m.end())
            while pos != -1:
                nxt = text.find("\n", pos + 1)
                line = text[pos + 1: nxt if nxt != -1 else len(text)]
                body = line.strip()
                if body and not body.startswith("#") and len(line) - len(line.lstrip()) <= indent:
                    end = pos + 1 + (len(line) if lang == "rb" and body == "end" else 0)
                    break
                pos = nxt
            blocks.append(Block(m.group(2), m.start(), end))
    elif lang in orm.BLOCK_HEADERS:
        ms = list(re.finditer(orm.BLOCK_HEADERS[lang], text, re.M))
        for k, m in enumerate(ms):
            stop = ms[k + 1].start() if k + 1 < len(ms) else len(text)
            end = _brace_end(text, m.end(), stop)
            blocks.append(Block(m.group(1), m.start(), end if end is not None else stop))
    for i, b in enumerate(blocks):
        inner = [j for j, o in enumerate(blocks) if j != i and o.start < b.start and b.end <= o.end]
        b.parent = max(inner, key=lambda j: blocks[j].start) if inner else None
    return blocks


def innermost(blocks: list[Block], offset: int) -> int | None:
    hits = [i for i, b in enumerate(blocks) if b.start <= offset < b.end]
    return max(hits, key=lambda i: blocks[i].start) if hits else None


# ---- the engine ---------------------------------------------------------------------------------------------
@dataclass
class FileResult:
    refs: list[CodeRef]
    unresolved: list[Unresolved]
    decl_lines: list[int]


def _group(m: re.Match, name: str) -> str | None:
    return m.group(name) if name in m.re.groupindex and m.group(name) else None


def _line_group(m: re.Match) -> int:
    for g in ("column", "table", "prop", "symbol"):
        if _group(m, g):
            return m.start(g)
    return m.start()


def run_rules(repo: str, path: str, lang: str, text: str, rules: list[Rule], vocab: Vocab,
              lines: Lines | None = None) -> FileResult:
    """Run every rule that applies to the language over one file's text."""
    L = lines or Lines(text)
    rules = [r for r in rules if r.applies(lang)]
    blocks = find_blocks(text, lang)
    refs: list[CodeRef] = []
    unres: list[Unresolved] = []
    decl_lines: set[int] = set()
    brace = lang in orm.BLOCK_HEADERS

    def mark(m: re.Match) -> None:
        decl_lines.update(range(L.line(m.start()), L.line(max(m.start(), m.end() - 1)) + 1))

    # pass 1: table declarations; each one finds its block
    decls = []  # (offset, rule, match, name, block index or None)
    for r in rules:
        if r.kind != "declare_table":
            continue
        for m in r.regex.finditer(text):
            name, ent = m.group("table"), _group(m, "entity")
            if ent:
                bi = next((i for i, b in enumerate(blocks) if b.name == ent), None)
            else:
                bi = innermost(blocks, m.start("table"))
                if bi is not None and blocks[bi].name == "Meta" and blocks[bi].parent is not None:
                    bi = blocks[bi].parent  # Django: db_table lives in the model's inner Meta class
                if bi is None and brace:  # an annotation in front of the class it describes
                    bi = next((i for i, b in enumerate(blocks) if b.start >= m.end()), None)
            decls.append((m.start("table"), r, m, name, bi))
    decls.sort(key=lambda d: (d[0], d[1].name))
    explicit = {d[4] for d in decls if d[4] is not None and not d[1].fallback}
    kept = [d for d in decls if not (d[1].fallback and d[4] in explicit)]
    file_decls: list[tuple[int, list[str]]] = []  # (offset, table ids) in file order
    for off, r, m, name, bi in kept:
        ids = vocab.resolve_table(name)
        mark(m)
        line = L.line(off)
        file_decls.append((off, ids))
        if bi is not None and not blocks[bi].declared:
            blocks[bi].declared, blocks[bi].tables = True, ids
        if not ids:
            unres.append(Unresolved(repo, path, line, "declare_table", name, r.name))
            continue
        sym = blocks[bi].name if bi is not None else _group(m, "symbol")
        for t in ids:
            refs.append(CodeRef(repo, path, line, "declare_table", t, None, sym, r.name,
                                round(r.confidence / len(ids), 4), snippet(L.get(line))))

    def entity_tables(offset: int) -> list[str] | None:
        bi = innermost(blocks, offset)
        inside = bi is not None
        while bi is not None:
            if blocks[bi].declared:
                return blocks[bi].tables
            bi = blocks[bi].parent
        if inside:
            return None
        return preceding(offset)

    def preceding(offset: int) -> list[str] | None:
        prev = [ids for off, ids in file_decls if off <= offset]
        return prev[-1] if prev else None

    # pass 2: columns, uses and SQL patterns
    for r in rules:
        if r.kind == "declare_table":
            continue
        for m in r.regex.finditer(text):
            tname, col, prop = _group(m, "table"), _group(m, "column"), _group(m, "prop")
            line = L.line(_line_group(m))
            name = col or prop
            mark(m)  # lines a rule explains are left out of the uses pass
            if tname:
                ids = vocab.resolve_table(tname)
                if not ids:
                    unres.append(Unresolved(repo, path, line, r.kind, tname, r.name))
                    continue
            elif r.scope == "entity":
                ids = entity_tables(m.start())
                if not ids:
                    continue
            else:
                ids = preceding(m.start())
                if ids is None:
                    ids = vocab.tables_with_column(name)
                    if len(ids) != 1:
                        unres.append(Unresolved(repo, path, line, r.kind, name, r.name))
                        continue
                if not ids:
                    continue
            hits = []
            for t in ids:
                real = vocab.column(t, name) if name else None
                if name and real is None:
                    continue
                hits.append((t, real))
            for t, real in hits:
                sym = prop or _group(m, "symbol")
                refs.append(CodeRef(repo, path, line, r.kind, t, real, sym, r.name,
                                    round(r.confidence / len(hits), 4), snippet(L.get(line))))
    seen: set = set()
    out = []
    for ref in refs:
        if ref.key() not in seen:
            seen.add(ref.key())
            out.append(ref)
    return FileResult(sorted(out, key=CodeRef.sort_key), sorted(set(unres), key=Unresolved.sort_key), sorted(decl_lines))
