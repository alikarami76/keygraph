"""FK recovery benchmark: strip declared FKs from SQLite DBs and score keygraph's inferred edges.

usage: python3 -I bench/scripts/fk_recovery.py --db-root DIR [--glob '*/*.sqlite'] --work DIR
           --keygraph CMD --out results.json [--md results.md] [--label TEXT]

Stdlib only. Precision is a lower bound: a real relation that the DB never declared counts as an extra edge.
"""
import argparse
import datetime
import hashlib
import json
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

THRESHOLDS = (0.8, 0.6)


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def declared_and_strip(src: Path, dst: Path) -> tuple[set, int]:
    """Return (declared FK pairs, table count) and write a schema-only copy of src to dst without FKs."""
    con = sqlite3.connect(src)
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
    pk: dict[str, list[str]] = {}
    fks: set = set()
    out = sqlite3.connect(dst)
    for t in tables:
        cols = con.execute(f"pragma table_info({q(t)})").fetchall()
        pkc = [c[1] for c in sorted(cols, key=lambda c: c[5]) if c[5]]
        pk[t.lower()] = pkc
        defs = [f"{q(c[1])} {c[2] or ''}" for c in cols]
        if pkc:
            defs.append("PRIMARY KEY (" + ", ".join(q(c) for c in pkc) + ")")
        out.execute(f"create table {q(t)} ({', '.join(defs)})")
    for t in tables:
        rows = con.execute(f"pragma foreign_key_list({q(t)})").fetchall()
        for row in rows:
            ref, frm, to = row[2], row[3], row[4]
            if to is None:  # FK to the referenced table's PK; one pair per column for composite keys
                p = pk.get(ref.lower(), [])
                to = p[row[1]] if row[1] < len(p) else None
            if to is None or frm is None:
                continue
            fks.add(frozenset({(t.lower(), frm.lower()), (ref.lower(), to.lower())}))
    for t in tables:  # UNIQUE constraints (autoindexes have no sql in sqlite_master)
        for ix in con.execute(f"pragma index_list({q(t)})").fetchall():
            if ix[3] == "u":
                cols = [r[2] for r in con.execute(f"pragma index_info({q(ix[1])})").fetchall()]
                try:
                    out.execute(f"create unique index {q('uq_' + t + '_' + '_'.join(map(str, cols)))} on {q(t)} ({', '.join(q(c) for c in cols)})")
                except sqlite3.Error:
                    pass
    for (sql,) in con.execute("select sql from sqlite_master where type='index' and sql is not null"):
        try:
            out.execute(sql)
        except sqlite3.Error:
            pass
    out.commit()
    out.close()
    con.close()
    return fks, len(tables)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fmt_pair(p) -> str:
    return " <-> ".join(f"{t}.{c}" for t, c in sorted(p))


def inferred_edges(graph: dict) -> list[tuple[frozenset, float, str]]:
    """Single-column inferred edges as (pair, confidence, first evidence line)."""
    res = []
    for e in graph.get("edges", []):
        if e.get("kind") != "inferred" or len(e["from_columns"]) != 1 or len(e["to_columns"]) != 1:
            continue
        a = (e["from"].split(".", 1)[-1].lower(), e["from_columns"][0].lower())
        b = (e["to"].split(".", 1)[-1].lower(), e["to_columns"][0].lower())
        ev = e.get("evidence") or []
        res.append((frozenset({a, b}), float(e.get("confidence", 0)), str(ev[0]) if ev else ""))
    return res


def score(truth: set, edges: list, th: float) -> dict:
    got: dict = {}
    for pair, conf, ev in edges:
        if conf >= th and (pair not in got or conf > got[pair][0]):
            got[pair] = (conf, ev)
    hits = set(got) & truth
    return {
        "declared": len(truth), "hits": len(hits), "inferred": len(got),
        "misses": sorted(fmt_pair(p) for p in truth - set(got)),
        "extras": sorted(({"pair": fmt_pair(p), "confidence": got[p][0], "evidence": got[p][1]} for p in set(got) - truth),
                         key=lambda x: x["pair"]),
    }


def ratio(a: int, b: int):
    return round(a / b, 4) if b else None


def summarize(rows: list[dict], th: str) -> dict:
    d = sum(r["scores"][th]["declared"] for r in rows)
    h = sum(r["scores"][th]["hits"] for r in rows)
    n = sum(r["scores"][th]["inferred"] for r in rows)
    return {"declared": d, "hits": h, "inferred": n, "recall": ratio(h, d), "precision_lower_bound": ratio(h, n)}


def pct(x) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def markdown(res: dict) -> str:
    lines = [f"# FK recovery: {res['label'] or res['keygraph_version']}", "",
             f"keygraph {res['keygraph_version']}, git {res['git_sha']}, {res['date']}, {len(res['databases'])} databases.",
             "Precision is a lower bound: undeclared but real relations count as extra edges.", ""]
    for th in map(str, THRESHOLDS):
        lines += [f"## Confidence >= {th}", "", "| db | declared | hits | inferred | recall | precision (lower bound) |", "|---|---|---|---|---|---|"]
        for r in res["databases"]:
            s = r["scores"][th]
            lines.append(f"| {r['db']} | {s['declared']} | {s['hits']} | {s['inferred']} | {pct(ratio(s['hits'], s['declared']))} | {pct(ratio(s['hits'], s['inferred']))} |")
        t = res["total"][th]
        lines += [f"| **total** | {t['declared']} | {t['hits']} | {t['inferred']} | {pct(t['recall'])} | {pct(t['precision_lower_bound'])} |", ""]
    lines += ["## Misses and extra edges at 0.6", ""]
    for r in res["databases"]:
        s = r["scores"]["0.6"]
        if s["misses"] or s["extras"]:
            lines.append(f"### {r['db']}")
            lines += [f"- miss: {m}" for m in s["misses"]]
            lines += [f"- extra: {x['pair']} ({x['confidence']}) {x['evidence']}" for x in s["extras"]]
            lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db-root", required=True, type=Path)
    ap.add_argument("--glob", default="*/*.sqlite")
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--keygraph", required=True, help="command string, e.g. 'uv run keygraph'")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--md", type=Path)
    ap.add_argument("--label", default="")
    a = ap.parse_args(argv)
    kg = shlex.split(a.keygraph)
    a.work.mkdir(parents=True, exist_ok=True)
    dbs = sorted(a.db_root.glob(a.glob))
    if not dbs:
        print(f"no databases match {a.db_root}/{a.glob}", file=sys.stderr)
        return 2
    ver = ""
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=Path(__file__).resolve().parent)
    rows = []
    for db in dbs:
        name = db.stem
        d = a.work / name
        d.mkdir(parents=True, exist_ok=True)
        stripped = d / "stripped.sqlite"
        stripped.unlink(missing_ok=True)
        truth, ntab = declared_and_strip(db, stripped)
        subprocess.run([*kg, "scan", "--url", f"sqlite:///{stripped}", "--dir", str(d / "out")],
                       check=True, capture_output=True, shell=False)
        graph = json.loads((d / "out" / "graph.json").read_text())
        for bf in sorted((d / "out").glob("*build*.json")):  # the scan's build info records the version
            ver = ver or str(json.loads(bf.read_text()).get("keygraph_version") or "")
        edges = inferred_edges(graph)
        rows.append({"db": name, "sha256": sha256(db), "tables": ntab,
                     "scores": {str(th): score(truth, edges, th) for th in THRESHOLDS}})
    res = {"label": a.label, "keygraph_version": ver, "git_sha": sha.stdout.strip() if sha.returncode == 0 else None,
           "date": datetime.date.today().isoformat(), "databases": rows,
           "total": {str(th): summarize(rows, str(th)) for th in THRESHOLDS}}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    if a.md:
        a.md.write_text(markdown(res))
    for th in map(str, THRESHOLDS):
        t = res["total"][th]
        print(f"threshold {th}: recall {pct(t['recall'])}  precision (lower bound) {pct(t['precision_lower_bound'])}  inferred {t['inferred']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
