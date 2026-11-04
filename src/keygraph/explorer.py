"""Build the self-contained keygraph.html explorer.

The page is one file: the keygraph design system CSS (fonts embedded), dagre, the theme helpers, the diagram
renderer and the app script are inlined from static/ (copied there from design/ by `design/build.py --export-static`),
and the graph plus context is embedded as compact JSON (gzip + base64 when large). Nothing is fetched at runtime.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
from pathlib import Path

from .context import safe_source
from .graph import Graph
from .query import _is_log, table_info_line

STATIC = Path(__file__).parent / "static"
STATUS = {"declared": 0, "inferred": 1, "confirmed": 2, "added": 3, "probed": 4}
CARD = {"many-to-one": 0, "one-to-one": 1, "many-to-many": 2, "one-to-many": 3}
NOTE_KEYS = ("extra", "generated", "charset", "collation", "comment")


def _col(c: dict) -> list:
    """[name, type, nullable, default|None, enum/set members|None, other details]."""
    note = "; ".join(f"{k}: {c[k]}" for k in NOTE_KEYS if c.get(k))
    return [c["name"], c["type"], int(c.get("nullable", True)), c.get("default"), c.get("values") or None, note]


KIND_IDX = {"declare_table": 0, "declare_column": 1, "sql_write": 2, "sql_read": 3, "use": 4}


def _code(graph: Graph, tidx: dict, code: dict) -> dict:
    """Code refs per table for the "Used in code" sections: {"r": repos, "t": {table index: [counts, refs, columns]}}.
    counts are per kind in KIND_IDX order; a ref is [repo index, path, line, kind index, confidence, column index or -1]
    plus the snippet when the summary carries one; columns is {column index: [counts, refs]}."""
    repos = sorted({r["repo"] for t in code.values() for r in t["refs"]} |
                   {r["repo"] for t in code.values() for c in t["columns"].values() for r in c["refs"]})
    ridx = {r: i for i, r in enumerate(repos)}
    out: dict = {}
    for tid in sorted(code):
        if tid not in tidx:
            continue
        cols = {c["name"]: i for i, c in enumerate(graph.nodes[tid]["columns"])}

        def ref(r: dict) -> list:
            x = [ridx[r["repo"]], r["path"], r["line"], KIND_IDX[r["kind"]], r["confidence"], cols.get(r["column"], -1)]
            return x + [r["snippet"]] if "snippet" in r else x

        t = code[tid]
        counts = lambda n: [n.get(k, 0) for k in KIND_IDX]  # noqa: E731
        out[str(tidx[tid])] = [counts(t["n"]), [ref(r) for r in t["refs"]],
                               {str(cols[c]): [counts(v["n"]), [ref(r) for r in v["refs"]]]
                                for c, v in sorted(t["columns"].items()) if c in cols}]
    return {"r": repos, "t": out}


def compact(graph: Graph, ctx_dump: dict | None = None, code: dict | None = None, queries: dict | None = None) -> dict:
    schemas = sorted({n["schema"] for n in graph.nodes.values()})
    sidx = {s: i for i, s in enumerate(schemas)}
    ids = sorted(graph.nodes)
    tidx = {t: i for i, t in enumerate(ids)}
    hubs = set(graph.data.get("hubs") or [])
    tables = []
    for t in ids:
        n = graph.nodes[t]
        ci = {c["name"]: i for i, c in enumerate(n["columns"])}
        info = n.get("info") or {}
        tables.append([
            n["table"], sidx[n["schema"]], [_col(c) for c in n["columns"]],
            [ci[c] for c in n["primary_key"] if c in ci],
            [[i["name"], int(i["unique"]), int(i["primary"]), [ci[c] for c in i["columns"] if c in ci]] for i in n["indexes"]],
            [[ci.get(p["id_column"], -1), ci.get(p["type_column"], -1)] for p in n["polymorphic"]],
            table_info_line(n),
            int(_is_log(graph, t)) | (2 if t in hubs else 0) | (4 if n.get("mirror_of") else 0),
            info.get("rows_estimate"),
        ])
    edges = []
    for e in graph.edges:
        fi, ti = tidx[e["from"]], tidx[e["to"]]
        fci = {c["name"]: i for i, c in enumerate(graph.nodes[e["from"]]["columns"])}
        tci = {c["name"]: i for i, c in enumerate(graph.nodes[e["to"]]["columns"])}
        edges.append([fi, ti, [fci[c] for c in e["from_columns"] if c in fci], [tci[c] for c in e["to_columns"] if c in tci],
                      e["confidence"], STATUS.get(e["status"], 1), CARD.get(e["cardinality"], 0),
                      int(e["ambiguous"]), e["evidence"], (1 if e.get("hub") else 0) | (2 if e.get("role") else 0)])
    # area flags: 1 unconnected (no relations left after hubs), 2 mirror schema
    areas = [[a["id"], a["name"], [tidx[t] for t in a["tables"] if t in tidx], int(bool(a.get("auto", True))),
              (1 if a.get("unconnected") else 0) | (2 if a.get("mirror") else 0)]
             for a in graph.data.get("areas") or []]
    aidx = {a[0]: i for i, a in enumerate(areas)}
    links = [[aidx[l["a"]], aidx[l["b"]], l["edges"]] for l in graph.data.get("area_links") or []
             if l["a"] in aidx and l["b"] in aidx]
    c = {"concepts": [], "rels": [], "cols": [], "metrics": [], "tn": []}
    if ctx_dump:
        for x in ctx_dump["concepts"]:
            c["concepts"].append({"n": x["name"], "d": x["definition"], "dm": x["domain"], "sy": x["synonyms"], "v": int(x["verified"]),
                                  "l": [[tidx[l["table"]], l["column"], l["role"], l["note"]] for l in x["links"] if l["table"] in tidx]})
        c["rels"] = [[r["from"], r["rel"], r["to"], r["note"], int(r["verified"])] for r in ctx_dump["relations"]]
        c["cols"] = [{"t": tidx[x["table"]], "c": x["column"], "m": x["meaning"], "u": x["unit"], "k": x["codes"], "nm": x["null_meaning"],
                      "pii": int(x["pii"]), "f": x["typical_filters"], "ih": x["index_hint"], "v": int(x["verified"]), "s": safe_source(x["source"])}
                     for x in ctx_dump["columns"] if x["table"] in tidx]
        c["metrics"] = [{"n": x["name"], "d": x["definition"], "e": x["expression"], "g": x["grain"], "t": [tidx[t] for t in x["tables"] if t in tidx],
                         "f": x["filters"], "cv": x["caveats"], "v": int(x["verified"])} for x in ctx_dump["metrics"]]
        c["tn"] = [{"t": tidx[x["table"]], "g": x["grain"], "fr": x["freshness"], "sd": x["soft_delete"], "dc": x["date_column"],
                    "ri": x["recommended_indexes"], "p": x["pitfalls"], "sm": x["summary"], "v": int(x["verified"]), "s": safe_source(x["source"])}
                   for x in ctx_dump["tables"] if x["table"] in tidx]
    prof = {}
    for key, p in (graph.data.get("profiles") or {}).items():
        tid, col = key.rsplit(".", 1)
        if tid in tidx:
            names = [c["name"] for c in graph.nodes[tid]["columns"]]
            if col in names:
                if p.get("pii"):  # PII: never embed raw values, whatever an older profile stored
                    p = {k: v for k, v in p.items() if k not in ("top_values", "numeric", "dates", "length")}
                prof[f"{tidx[tid]}:{names.index(col)}"] = p
    c["p"] = prof
    out = {"v": 2, "s": schemas, "t": tables, "e": edges, "a": areas, "al": links, "mi": graph.data.get("mirrors") or {},
           "c": c, "st": graph.data["stats"]}
    if code:
        out["k"] = _code(graph, tidx, code)
    if queries:
        out["q"] = {str(tidx[t]): q for t, q in sorted(queries.items()) if t in tidx}
    return out


def _svg(name: str) -> str:
    s = (STATIC / name).read_text(encoding="utf-8")
    s = re.sub(r"<title>.*?</title>", "", s, flags=re.S)
    return s.replace("#17181A", "var(--kg-text)").replace("#E2A317", "var(--kg-accent)")


def _js(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8").replace("</script", "<\\/script")


def build_html(graph: Graph, ctx_dump: dict | None = None, gzip_over: int = 300_000, code: dict | None = None,
               queries: dict | None = None) -> str:
    payload = json.dumps(compact(graph, ctx_dump, code, queries), ensure_ascii=False, separators=(",", ":"))
    if len(payload) > gzip_over:
        data = "GZ:" + base64.b64encode(gzip.compress(payload.encode("utf-8"), 9, mtime=0)).decode("ascii")
    else:
        data = payload.replace("<", "\\u003c")  # JSON-safe; no "</script" or "<!--" can end or alter the block
    lic = "\n\n".join((STATIC / f).read_text(encoding="utf-8") for f in ("DAGRE_LICENSE", "OFL-Inter.txt", "OFL-JetBrainsMono.txt"))
    fav = (STATIC / "favicon.svg").read_text(encoding="utf-8").replace("#", "%23").replace('"', "'").replace("\n", "")
    logo = _svg("keygraph-logo.svg").replace("<svg ", '<svg style="fill:var(--kg-text)" ', 1)
    mark = _svg("keygraph-mark.svg").replace("<svg ", '<svg width="24" height="24" ', 1)
    parts = {
        "LICENSES": lic.replace("--", "- -"),
        "FAVICON": fav,
        "CSS": (STATIC / "keygraph.inline.css").read_text(encoding="utf-8").replace("</style", "<\\/style"),
        "LOGOS": json.dumps({"logo": logo, "mark": mark}).replace("</", "<\\/"),
        "DAGRE": _js("dagre-1.1.4.min.js"),
        "THEME": _js("kg-theme.js"),
        "DIAGRAM": _js("kg-diagram.js"),
        "APP": _js("explorer.js"),
        "DATA": data,
    }
    tpl = (STATIC / "explorer.html").read_text(encoding="utf-8")
    # one pass over the template only, so text inside the data or the scripts is never treated as a marker
    return re.sub(r"/\*__([A-Z]+)__\*/", lambda m: parts[m.group(1)], tpl)
