"""Build the self-contained keygraph.html explorer (graph + context embedded, cytoscape.js inlined)."""

from __future__ import annotations

import base64
import gzip
import json
from pathlib import Path

from .graph import Graph

STATIC = Path(__file__).parent / "static"
KIND = {"declared": 0, "inferred": 1}
STATUS = {"declared": 0, "inferred": 1, "confirmed": 2, "added": 3, "probed": 4}
CARD = {"many-to-one": 0, "one-to-one": 1, "many-to-many": 2, "one-to-many": 3}


def compact(graph: Graph, ctx_dump: dict | None = None) -> dict:
    schemas = sorted({n["schema"] for n in graph.nodes.values()})
    sidx = {s: i for i, s in enumerate(schemas)}
    ids = sorted(graph.nodes)
    tidx = {t: i for i, t in enumerate(ids)}
    tables = []
    for t in ids:
        n = graph.nodes[t]
        ci = {c["name"]: i for i, c in enumerate(n["columns"])}
        tables.append([
            n["table"], sidx[n["schema"]], [[c["name"], c["type"]] for c in n["columns"]],
            [ci[c] for c in n["primary_key"] if c in ci],
            [[i["name"], int(i["unique"]), int(i["primary"]), [ci[c] for c in i["columns"] if c in ci]] for i in n["indexes"]],
            [[ci.get(p["id_column"], -1), ci.get(p["type_column"], -1)] for p in n["polymorphic"]],
        ])
    edges = []
    for e in graph.edges:
        fi, ti = tidx[e["from"]], tidx[e["to"]]
        fci = {c["name"]: i for i, c in enumerate(graph.nodes[e["from"]]["columns"])}
        tci = {c["name"]: i for i, c in enumerate(graph.nodes[e["to"]]["columns"])}
        edges.append([fi, ti, [fci[c] for c in e["from_columns"] if c in fci], [tci[c] for c in e["to_columns"] if c in tci],
                      int(round(e["confidence"] * 100)), STATUS.get(e["status"], 1), CARD.get(e["cardinality"], 0),
                      int(e["ambiguous"]), e["evidence"]])
    c = {"concepts": [], "rels": [], "cols": [], "metrics": [], "tn": []}
    if ctx_dump:
        for x in ctx_dump["concepts"]:
            c["concepts"].append({"n": x["name"], "d": x["definition"], "dm": x["domain"], "sy": x["synonyms"], "v": int(x["verified"]),
                                  "l": [[tidx[l["table"]], l["column"], l["role"], l["note"]] for l in x["links"] if l["table"] in tidx]})
        c["rels"] = [[r["from"], r["rel"], r["to"], r["note"], int(r["verified"])] for r in ctx_dump["relations"]]
        c["cols"] = [{"t": tidx[x["table"]], "c": x["column"], "m": x["meaning"], "u": x["unit"], "k": x["codes"], "nm": x["null_meaning"],
                      "pii": int(x["pii"]), "f": x["typical_filters"], "ih": x["index_hint"], "v": int(x["verified"]), "s": x["source"]}
                     for x in ctx_dump["columns"] if x["table"] in tidx]
        c["metrics"] = [{"n": x["name"], "d": x["definition"], "e": x["expression"], "g": x["grain"], "t": [tidx[t] for t in x["tables"] if t in tidx],
                         "f": x["filters"], "cv": x["caveats"], "v": int(x["verified"])} for x in ctx_dump["metrics"]]
        c["tn"] = [{"t": tidx[x["table"]], "g": x["grain"], "fr": x["freshness"], "sd": x["soft_delete"], "dc": x["date_column"],
                    "ri": x["recommended_indexes"], "p": x["pitfalls"], "sm": x["summary"], "v": int(x["verified"]), "s": x["source"]}
                   for x in ctx_dump["tables"] if x["table"] in tidx]
    return {"v": 1, "s": schemas, "t": tables, "e": edges, "c": c, "st": graph.data["stats"]}


def build_html(graph: Graph, ctx_dump: dict | None = None, gzip_over: int = 300_000) -> str:
    payload = json.dumps(compact(graph, ctx_dump), ensure_ascii=False, separators=(",", ":"))
    if len(payload) > gzip_over:
        data = "GZ:" + base64.b64encode(gzip.compress(payload.encode("utf-8"), 9)).decode("ascii")
    else:
        data = payload.replace("</", "<\\/")
    tpl = (STATIC / "explorer.html").read_text(encoding="utf-8")
    cy = (STATIC / "cytoscape.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    lic = (STATIC / "CYTOSCAPE_LICENSE").read_text(encoding="utf-8").replace("--", "- -")
    return (tpl.replace("/*__LICENSE__*/", lic).replace("/*__CYTOSCAPE__*/", cy).replace("__DATA__", data))
