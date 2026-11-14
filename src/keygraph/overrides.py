"""User overrides: confirm / reject / add edges, table aliases, role aliases, prefixes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import devlog


@dataclass
class Overrides:
    aliases: dict[str, str] = field(default_factory=dict)
    role_aliases: dict[str, list[str]] = field(default_factory=dict)
    prefixes: list[str] = field(default_factory=list)
    confirm: list[dict] = field(default_factory=list)
    reject: list[dict] = field(default_factory=list)
    add: list[dict] = field(default_factory=list)
    copy_schemas: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "Overrides":
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError as exc:  # pragma: no cover
                raise SystemExit("YAML needs PyYAML: pip install 'keygraph[yaml]' (or use a .json file)") from exc
            raw = yaml.safe_load(text) or {}
        else:
            raw = json.loads(text)
        ra = {k: ([v] if isinstance(v, str) else list(v)) for k, v in (raw.get("role_aliases") or {}).items()}
        return cls(aliases=dict(raw.get("aliases") or {}), role_aliases=ra, prefixes=list(raw.get("prefixes") or []),
                   confirm=list(raw.get("confirm") or []), reject=list(raw.get("reject") or []),
                   add=list(raw.get("add") or []),
                   copy_schemas=[str(x) for x in ([raw["copies"]] if isinstance(raw.get("copies"), str) else raw.get("copies") or [])])

    def configure(self, opts) -> None:
        devlog.trace("override.config", aliases=len(self.aliases), role_aliases=len(self.role_aliases),
                     prefixes=len(self.prefixes), copy_schemas=len(self.copy_schemas), confirm=len(self.confirm),
                     reject=len(self.reject), add=len(self.add))
        opts.aliases.update(self.aliases)
        opts.role_aliases.update(self.role_aliases)
        opts.prefixes.extend(p for p in self.prefixes if p not in opts.prefixes)
        opts.copy_schemas.extend(g for g in self.copy_schemas if g not in opts.copy_schemas)


def parse_ref(ref: str) -> tuple[str, list[str]]:
    """'shop.orders.customer_id' or 'orders.a,b' -> (table ref, columns)."""
    head, cols = ref.rsplit(".", 1)
    return head, [c.strip() for c in cols.split(",") if c.strip()]


def _resolve(nodes: dict, head: str) -> str | None:
    if head in nodes:
        return head
    hits = [n for n in nodes if n.split(".", 1)[1] == head]
    return hits[0] if len(hits) == 1 else None


def apply_overrides(data: dict, ov: Overrides) -> None:
    nodes = {n["id"]: n for n in data["nodes"]}
    applied = {"confirmed": 0, "rejected": 0, "added": 0}
    rejects: list[dict] = []
    notes = data["warnings"]

    def locate(item):
        fh, fc = parse_ref(item["from"])
        ft = _resolve(nodes, fh)
        if item.get("to"):
            th, tc = parse_ref(item["to"])
            tt = _resolve(nodes, th)
        else:
            tt, tc = None, []
        return ft, fc, tt, tc

    for item in ov.reject:
        ft, fc, tt, tc = locate(item)
        before = len(data["edges"])
        data["edges"] = [e for e in data["edges"] if not (
            e["kind"] == "inferred" and e["from"] == ft and e["from_columns"] == fc
            and (tt is None or (e["to"] == tt and (not tc or e["to_columns"] == tc))))]
        applied["rejected"] += before - len(data["edges"])
        if ft is not None:  # remembered so a later LLM relation cannot bring the pair back
            rejects.append({"from": ft, "from_columns": fc, "to": tt, "to_columns": tc if tt else []})
        if devlog.active():
            devlog.trace("override.reject", frm=item["from"], to=item.get("to"), removed=before - len(data["edges"]),
                         effect="removed inferred edges" if before != len(data["edges"]) else "matched no inferred edge")
        if ft is None:
            notes.append(f"override reject: unknown table in {item['from']}")
    for kind, items, conf in (("confirmed", ov.confirm, 0.98), ("added", ov.add, 0.9)):
        for item in items:
            ft, fc, tt, tc = locate(item)
            if not (ft and tt):
                notes.append(f"override {kind}: unknown table in {item}")
                devlog.trace(f"override.{kind}", frm=item.get("from"), to=item.get("to"), effect="ignored: unknown table")
                continue
            have = {c["name"] for c in nodes[ft]["columns"]}, {c["name"] for c in nodes[tt]["columns"]}
            if not (set(fc) <= have[0] and set(tc) <= have[1]) or not tc:
                notes.append(f"override {kind}: unknown column in {item}")
                devlog.trace(f"override.{kind}", frm=item.get("from"), to=item.get("to"), effect="ignored: unknown column")
                continue
            note = item.get("note", "")
            match = [e for e in data["edges"] if e["from"] == ft and e["from_columns"] == fc and e["kind"] == "inferred"]
            hit = [e for e in match if e["to"] == tt and e["to_columns"] == tc]
            if devlog.active():
                dropped = len(match) - 1 if hit else len(match) if kind == "confirmed" else 0
                devlog.trace(f"override.{kind}", frm=ft, to=tt, effect=(
                    "raised an inferred edge to confirmed" if kind == "confirmed" and hit else
                    "replaced the inferred edges of that column" if kind == "confirmed" and match else
                    f"{'confirmed' if kind == 'confirmed' else 'added'} a new edge"), replaced_inferred=dropped)
            if kind == "confirmed" and hit:
                data["edges"] = [e for e in data["edges"] if e not in match or e is hit[0]]
                e = hit[0]
                e["status"], e["confidence"], e["ambiguous"] = "confirmed", conf, False
                e["evidence"].append("confirmed by user override" + (f": {note}" if note else ""))
            else:
                if kind == "confirmed":
                    data["edges"] = [e for e in data["edges"] if e not in match]
                data["edges"] = [e for e in data["edges"] if not (
                    e["from"] == ft and e["from_columns"] == fc and e["to"] == tt and e["to_columns"] == tc)]
                fk = nodes[ft]
                from .infer import cardinality
                from .model import Table, Index
                t = Table(ft.split(".")[0], fk["table"], [], [Index(i["name"], i["columns"], i["unique"], i["primary"]) for i in fk["indexes"]])
                data["edges"].append({
                    "kind": "inferred", "status": kind, "from": ft, "from_columns": fc, "to": tt, "to_columns": tc,
                    "confidence": conf, "cardinality": cardinality(t, fc), "ambiguous": False,
                    "evidence": [f"{'confirmed' if kind == 'confirmed' else 'added'} by user override" + (f": {note}" if note else "")],
                })
            applied[kind] += 1
    data["overrides"] = applied
    if rejects:
        data["override_rejects"] = sorted(rejects, key=lambda r: json.dumps(r, sort_keys=True))
