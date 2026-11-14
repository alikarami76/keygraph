"""keygraph demo: build the bundled fictional webshop (35 tables) into a folder and open the explorer.

Runs the normal pipeline in this process: load from the packaged information_schema dumps and context, profile the
packaged SQLite sample with the standard library, index the packaged app sample, import the query log, render. No
extra, no database, no network. Where an extra would improve a step, a simpler built-in reader runs and a note names the extra.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import shlex
import sqlite3
import sys
import webbrowser
from pathlib import Path

DATA = Path(__file__).resolve().parent / "demo"
MARKER = ".keygraph-demo"
PROFILE_TABLES = ["shop.customers", "shop.orders", "shop.order_items", "shop.products", "billing.payments",
                  "shop.categories", "billing.vouchers"]


def prepare_dir(out: Path) -> None:
    """Use an empty or missing folder, or one a previous demo made. Never touch anything else."""
    if out.is_symlink() and not out.exists():
        raise ValueError(f"{out} is a broken link; pass an empty or new folder with --dir")
    if out.exists() and not out.is_dir():
        raise ValueError(f"{out} is a file, not a folder")
    if out.is_dir() and any(out.iterdir()) and not (out / MARKER).is_file():
        raise ValueError(f"{out} is not a keygraph demo folder and is not empty; pass an empty or new folder with --dir")
    if (out / MARKER).is_symlink():
        raise ValueError(f"{out / MARKER} is a link; remove it or pass another folder with --dir")
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER).write_text("created by `keygraph demo`; it is safe to rebuild this folder\n", encoding="utf-8")
    for name in ("keygraph.db", "keygraph.db-wal", "keygraph.db-shm"):  # start clean: the code index and log are not appended twice
        (out / name).unlink(missing_ok=True)


def _run(argv: list[str]) -> None:
    from .cli import main
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = main(argv)
    if rc != 0:
        raise ValueError(f"demo step `keygraph {' '.join(argv[:2])}` failed: {err.getvalue().strip() or buf.getvalue().strip()}")


def _profile(out: Path) -> None:
    """Profile the packaged sample through sqlite3 (stdlib), read-only and SELECT-only like every other source."""
    from .adapters.base import Source
    from .cli import _build_time, load_graph, render
    from .context import ContextDB
    from .probe import SelectOnlyRunner
    from .profile import run_profile

    conn = sqlite3.connect(f"{(DATA / 'shop.db').as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        class Src(Source):
            kind, dialect, quote, bare = "sqlite", "sqlite", '"', True
            run = staticmethod(SelectOnlyRunner.from_dbapi(conn).run)

        g = load_graph(out)
        ctx = ContextDB(out / "keygraph.db", g)
        now = dt.datetime.fromtimestamp(_build_time(), dt.UTC).replace(tzinfo=None)
        run_profile(g, Src(), ctx, tables=PROFILE_TABLES, log=lambda *a, **k: None, now=now)
        ctx.reindex()
        render(out, g, ctx)
        ctx.close()
    finally:
        conn.close()


def build(out: Path) -> list[str]:
    """Build the demo into `out`; returns the notes for steps that were skipped."""
    from .code import sql as code_sql
    skipped: list[str] = []
    prepare_dir(out)
    d = DATA / "dumps"
    _run(["load", "--dir", str(out), "--columns", str(d / "columns.json"), "--statistics", str(d / "statistics.json"),
          "--key-columns", str(d / "key_columns.json"), "--tables", str(d / "tables.json"),
          "--context", str(DATA / "context.json")])
    try:
        _profile(out)
    except ImportError as exc:
        skipped.append(f"value profiles skipped: {exc}")
    _run(["code", "index", str(DATA / "app"), "--name", "shop-app", "--dir", str(out)])
    if code_sql.sqlglot is None:
        skipped.append("code index and query log read SQL with the basic token reader; install keygraph[code] for parsed SQL")
    _run(["queries", "import", str(DATA / "queries.jsonl"), "--dir", str(out)])
    _run(["render", "--queries", "--dir", str(out)])
    return skipped


def next_commands(out: Path) -> list[str]:
    d = shlex.quote(str(out))
    return ["keygraph path customers shipments --dir " + d, "keygraph describe shop.orders --brief --dir " + d,
            "uvx --from 'keygraph[mcp]' keygraph mcp --dir " + d]


def should_open() -> bool:
    if os.environ.get("CI"):
        return False
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return True


def run(directory: str, open_browser: bool = True, as_json: bool = False) -> dict:
    out = Path(directory)
    skipped = build(out)
    from .graph import Graph
    g = Graph.load(out / "graph.json")
    explorer = Path(os.path.abspath(out / "keygraph.html"))
    result = {"dir": str(out), "explorer": str(explorer), "tables": len(g.nodes), "relations": len(g.edges),
              "skipped": skipped, "next": next_commands(out),
              "database": str(DATA / "shop.db")}
    if as_json:
        print(json.dumps(result, indent=1))
    else:
        print(f"keygraph demo: {result['tables']} tables, {result['relations']} relations -> {out}/")
        for s in skipped:
            print(f"  note: {s}")
        print(f"explorer: {explorer}\n\ntry next (if keygraph is not installed, prefix the first two with `uvx`; the mcp line needs keygraph[mcp]):")
        for c in result["next"]:
            print(f"  {c}")
    if open_browser and should_open():
        webbrowser.open(explorer.as_uri())
    return result
