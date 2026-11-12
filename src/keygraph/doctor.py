"""`keygraph doctor`: check an install and a built output directory, and print the exact MCP launch command.

Agents run this at the end of setup (to prove the setup works) and before filing a bug (`--report` writes an
environment block with no table names, column names, SQL or home-directory paths). Read-only: it never opens a
database connection and never writes outside the report file it is asked for.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import importlib.util
import json
import platform
import shutil
import sys
from pathlib import Path

from . import __version__

MCP_TOOLS = {"search", "describe", "keys", "explain", "path", "neighbors", "areas", "area", "profile_summary",
             "code_where", "code_what", "query_for", "query_top", "query_search", "query_submit"}
EXTRAS = {"live": ("sqlalchemy", "sqlalchemy"), "mcp": ("mcp", "mcp"), "yaml": ("yaml", "pyyaml"), "code": ("sqlglot", "sqlglot")}


def _version(module: str, dist: str) -> str | None:
    if importlib.util.find_spec(module) is None:
        return None
    try:
        importlib.import_module(module)
    except Exception:  # noqa: BLE001  (an extra that is present but broken still counts as missing)
        return None
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "installed"


def _ephemeral() -> bool:
    """True when running from a throwaway uvx environment, whose interpreter path will not survive."""
    exe = sys.executable.replace("\\", "/")
    return "/uv/archive-" in exe or "/.cache/uv/" in exe or "/uv/cache/" in exe


def mcp_launch(out: Path) -> dict:
    """The command an MCP client should start, with absolute paths (GUI clients often lack the shell PATH)."""
    d = str(out.resolve())
    if _ephemeral():
        uvx = shutil.which("uvx") or "uvx"
        return {"command": uvx, "args": ["--from", "keygraph[mcp]", "keygraph", "mcp", "--dir", d]}
    return {"command": sys.executable, "args": ["-m", "keygraph", "mcp", "--dir", d]}


def _check(name: str, status: str, detail: str, fix: str | None = None) -> dict:
    c = {"name": name, "status": status, "detail": detail}
    if fix:
        c["fix"] = fix
    return c


def _mcp_tools(out: Path) -> set[str]:
    from .mcp_server import build_server
    return {t.name for t in asyncio.run(build_server(out).list_tools())}


def run(out: str | Path) -> dict:
    out = Path(out)
    checks: list[dict] = []
    py = platform.python_version()
    checks.append(_check("python", "ok" if sys.version_info >= (3, 11) else "fail", py,
                         None if sys.version_info >= (3, 11) else "install Python 3.11 or newer"))
    extras = {k: _version(m, d) for k, (m, d) in EXTRAS.items()}
    for k, v in extras.items():
        checks.append(_check(f"extra:{k}", "ok" if v else "warn", v or "not installed",
                             None if v else f"pip install 'keygraph[{k}]'"))
    graph_stats: dict = {}
    gp = out / "graph.json"
    if not gp.exists():
        checks.append(_check("graph", "fail", f"no graph.json in {out}",
                             "build one: keygraph scan --url \"$KEYGRAPH_DB_URL\" --dir <out>  (or keygraph load)"))
    else:
        from . import query as Q
        from .graph import Graph
        g = Graph.load(gp)
        s = g.data.get("stats") or {}
        graph_stats = {k: s.get(k, 0) for k in ("tables", "columns", "schemas", "edges")}
        graph_stats["edges_by_kind"] = s.get("edges_by_kind", {})
        status = "ok" if graph_stats["tables"] else "fail"
        checks.append(_check("graph", status, f"{graph_stats['tables']} tables, {graph_stats['edges']} relations, "
                             f"{graph_stats['schemas']} schemas", None if status == "ok" else "the build found no tables; check --schema"))
        edges = sorted((e for e in g.edges if e["from"] != e["to"]), key=lambda e: -e["confidence"])  # self-references have no path
        if edges:
            e = edges[0]
            found = Q.find_paths(g, e["from"], e["to"], min_conf=0.0)
            checks.append(_check("query", "ok" if found else "fail", "a join path query returns a route" if found
                                 else "path found no route for a known relation", None if found else "file a bug: keygraph doctor --report bug.md"))
        else:
            checks.append(_check("query", "warn", "no relations yet, so no join paths",
                                 "add declared keys, an overrides file, or run keygraph probe to confirm inferred ones"))
        db = out / "keygraph.db"
        if db.exists():
            from .context import ContextDB
            n = ContextDB(db, g).counts()
            graph_stats["context_entries"] = sum(n.values())
            checks.append(_check("context", "ok" if graph_stats["context_entries"] else "warn",
                                 f"{graph_stats['context_entries']} business context entries",
                                 None if graph_stats["context_entries"] else
                                 "add grain, status codes and metrics: keygraph context import <file.yaml>"))
        pages = out / "tables"
        checks.append(_check("pages", "ok" if pages.is_dir() else "warn",
                             "agent pages in tables/ and areas/" if pages.is_dir() else "no tables/ pages",
                             None if pages.is_dir() else "rebuild with keygraph render"))
        if extras["mcp"]:
            try:
                tools = _mcp_tools(out)
                missing = sorted(MCP_TOOLS - tools)
                checks.append(_check("mcp", "fail" if missing else "ok",
                                     f"server starts and lists {len(tools)} tools" + (f"; missing {missing}" if missing else ""),
                                     "file a bug: keygraph doctor --report bug.md" if missing else None))
            except Exception as exc:  # noqa: BLE001  (report, never crash the doctor)
                checks.append(_check("mcp", "fail", f"server failed to start: {type(exc).__name__}: {exc}",
                                     "file a bug: keygraph doctor --report bug.md"))
    worst = "fail" if any(c["status"] == "fail" for c in checks) else "warn" if any(c["status"] == "warn" for c in checks) else "ok"
    return {"status": worst, "keygraph": __version__, "python": py, "platform": platform.platform(terse=True),
            "out": str(out.resolve()), "extras": extras, "graph": graph_stats, "checks": checks,
            "mcp_launch": mcp_launch(out) if gp.exists() else None}


def format_text(r: dict) -> str:
    lines = [f"keygraph doctor: {r['status'].upper()}  (keygraph {r['keygraph']}, Python {r['python']}, {r['platform']})",
             f"output directory: {r['out']}"]
    for c in r["checks"]:
        lines.append(f"  {c['status']:<5} {c['name']:<11} {c['detail']}")
        if c.get("fix"):
            lines.append(f"        -> {c['fix']}")
    if r["mcp_launch"]:
        m = r["mcp_launch"]
        lines += ["", "MCP server launch (absolute paths, paste into your agent's MCP config):",
                  f"  command: {m['command']}", f"  args:    {json.dumps(m['args'])}"]
    return "\n".join(lines)


def _redact(text: str) -> str:
    home = str(Path.home())
    return text.replace(home, "~") if home and home != "/" else text


def report(r: dict) -> str:
    """Markdown environment block for a bug report: versions, counts and check results only."""
    g = r["graph"]
    rows = "\n".join(f"| {c['name']} | {c['status']} | {_redact(c['detail'])} |" for c in r["checks"]
                     if c["name"] != "graph")
    extras = ", ".join(f"{k}={v or 'no'}" for k, v in r["extras"].items())
    shape = (f"{g.get('tables', 0)} tables, {g.get('columns', 0)} columns, {g.get('schemas', 0)} schemas, "
             f"{g.get('edges', 0)} relations {json.dumps(g.get('edges_by_kind', {}))}") if g else "no graph built"
    return f"""## Environment (from `keygraph doctor --report`)

- keygraph: {r['keygraph']}
- Python: {r['python']}
- Platform: {r['platform']}
- Extras: {extras}
- Graph shape: {shape}
- Doctor status: {r['status']}

| Check | Status | Detail |
|---|---|---|
{rows}

<!-- No table names, column names, SQL, values or home-directory paths are included above. -->
"""
