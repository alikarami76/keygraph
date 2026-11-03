"""Generic importer: one markdown note per table -> table notes and column semantics.

Recognised (case-insensitive) heading words: grain|meaning -> grain; gotcha|caveat|pitfall|warning -> pitfalls;
column(s) -> bullets/rows like "- `col` - meaning" or "| col | meaning |"; code|status|enum -> "- `value` = label"
(optionally under a "### column" sub-heading); a verified section is read but does not mark the note verified (only a person or a checked run should). A "**Summary:**" line sets the
summary. Front matter keys: table (or the file stem), and the key named by schema_key for the schema.
"""

from __future__ import annotations

import re
from pathlib import Path


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("[]")
    return meta, text[m.end():]


def _sections(body: str) -> list[tuple[int, str, list[str]]]:
    out: list[tuple[int, str, list[str]]] = []
    for line in body.splitlines():
        h = re.match(r"^(#{1,4})\s+(.*)$", line)
        if h:
            out.append((len(h.group(1)), h.group(2).strip(), []))
        elif out:
            out[-1][2].append(line)
    return out


_BUL = re.compile(r"^\s*[-*]\s+`?([A-Za-z0-9_.\-]+)`?\s*(?:[:=—–-]+|\s)\s*(.+)$")


def _text(lines: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(l.strip() for l in lines if l.strip() and not l.strip().startswith("_None"))).strip()


def parse_note(path: str | Path, schema_key: str = "schema", default_schema: str = "main") -> dict | None:
    p = Path(path)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    table = meta.get("table") or p.stem
    schema = meta.get(schema_key) or default_schema
    if meta.get("kind") not in (None, "table"):
        return None
    note: dict = {"table": f"{schema}.{table}", "columns": [], "source": str(p), "verified": False}
    sm = re.search(r"\*\*Summary:\*\*\s*(.+)", body)
    if sm:
        note["summary"] = sm.group(1).strip()
    pit: list[str] = []
    cur_code_col = None
    for level, title, lines in _sections(body):
        tl = title.lower()
        if re.search(r"grain|meaning", tl) and "column" not in tl:
            note["grain"] = _text(lines)
        elif re.search(r"gotcha|caveat|pitfall|warning", tl):
            pit.extend(re.sub(r"^\s*[-*]\s+", "", l).strip() for l in lines if l.strip())
        elif re.search(r"verified", tl):
            note["verified_facts"] = _text(lines)  # kept out of the verified flag: a note is a claim, not a check
        elif re.search(r"column", tl):
            for l in lines:
                m = _BUL.match(l)
                r = re.match(r"^\|\s*`?([A-Za-z0-9_]+)`?\s*\|\s*([^|]+)\|", l)
                if (m or r) and not (r and set(r.group(1)) <= {"-"}):
                    c, mean = (m or r).group(1), (m or r).group(2).strip()
                    note["columns"].append({"column": c, "meaning": mean})
        elif re.search(r"code|status|enum", tl):
            cur_code_col = None
            for l in lines:
                m = _BUL.match(l)
                if m:
                    cur_code_col = cur_code_col or re.sub(r"[^\w]", "", title.split()[-1])
                    note.setdefault("_codes", {}).setdefault(cur_code_col, {})[m.group(1)] = m.group(2).strip()
        elif level >= 3 and note.get("_codes") is not None:
            pass
    if pit:
        note["pitfalls"] = " | ".join(pit)
    note["codes"] = note.pop("_codes", {})
    return note


def notes_to_context(root: str | Path, schema_key: str = "schema", default_schema: str = "main",
                     skip_names: tuple[str, ...] = ("memory.md", "readme.md")) -> dict:
    """Convert a folder of per-table markdown notes into a context structure (tables + columns).

    Entries carry source = the note path; they are always unverified.
    Column meanings and codes are always unverified: a note bullet is a claim, not a check.
    """
    out: dict = {"tables": [], "columns": []}
    for f in sorted(Path(root).rglob("*.md")):
        if f.name.lower() in skip_names:
            continue
        n = parse_note(f, schema_key, default_schema)
        if not n:
            continue
        cols, codes = n.pop("columns"), n.pop("codes", {})
        n.pop("verified_facts", None)
        out["tables"].append({k: v for k, v in n.items() if v not in ("", None)})
        seen = set()
        for c in cols:
            out["columns"].append({"table": n["table"], "column": c["column"], "meaning": c["meaning"],
                                   **({"codes": codes[c["column"]]} if c["column"] in codes else {}),
                                   "source": str(f), "verified": False})
            seen.add(c["column"])
        for col, cd in codes.items():
            if col not in seen:
                out["columns"].append({"table": n["table"], "column": col, "codes": cd, "source": str(f), "verified": False})
    return out
