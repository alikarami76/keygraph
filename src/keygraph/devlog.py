"""Dev mode: an opt-in, local, append-only log of how an agent used keygraph (stdlib only).

On with `KEYGRAPH_DEV=1` or `keygraph dev on` (a marker file); `KEYGRAPH_DEV=0` forces it off. Events go to
`<out>/dev/events.jsonl`, build traces to `<out>/dev/traces/`. Arguments, errors and profile values are masked.
Nothing here may change a command's result: every failure is swallowed with one short stderr warning.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

V = 1
MAX_BYTES = 20 * 1024 * 1024      # events.jsonl rotation cap
KEEP_ROTATED = 5                  # events.1..5.jsonl
INLINE_MAX = 16 * 1024            # responses up to this many bytes are stored whole
TRACE_MAX_BYTES = 50 * 1024 * 1024
TRACE_MAX_EVENTS = 200_000
KEEP_TRACES = 20
DECISIONS_MAX = 200
_ON, _OFF = {"1", "true", "on", "yes"}, {"0", "false", "off", "no"}
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\"')\]>]*")
_LONG = re.compile(r"\S{513,}")  # mask_secrets is quadratic on one long unbroken token
# phone-like numbers: 7+ digit runs or grouped digits (dates such as 2026-11-12 stay)
_PHONE = re.compile(r"(?<![\w.])(?:\+?\d{7,}|\+?\d{2,4}[\s-]\d{3}[\s-]\d{3,4}(?:[\s-]\d{2,4})?)(?![\w.])")
MSG_MAX = 300
# token shapes that no key name gives away: AWS access key ids, GitHub tokens, JWTs (eyJ header . payload . signature)
_TOKENS = re.compile(r"\bAKIA[0-9A-Z]{16}\b|\b(?:gh[pousr]_(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{20,}|github_pat_(?=[A-Za-z0-9_]*\d)[A-Za-z0-9_]{20,})"
                     r"|\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
# keys that hold sampled column values in a profile (counts, null rates, lengths and patterns stay)
_VALUE_KEYS = {"top_values", "numeric", "dates", "examples", "sample_values", "samples", "reconcile",
               "undocumented_codes", "unseen_codes"}
# a profile object (it has "sample_size") keeps only these keys; any other key is replaced, so a new value-bearing key fails closed
_PROFILE_KEEP = {"sample_size", "null_rate", "method", "profiled_at", "distinct_in_sample", "distinct_ratio", "pattern",
                 "length", "pii", "note", "values_complete"}
_warned = False


def _warn(exc: BaseException) -> None:
    global _warned
    if not _warned:
        _warned = True
        print(f"keygraph: dev log skipped ({type(exc).__name__})", file=sys.stderr)


def dev_dir(out) -> Path:
    return Path(out) / "dev"


def source(out) -> str:
    """Why dev mode is on or off: env, marker or off."""
    v = os.environ.get("KEYGRAPH_DEV", "").strip().lower()
    if v in _ON:
        return "env"
    if v in _OFF:
        return "off"
    return "marker" if (dev_dir(out) / "enabled").exists() else "off"


def enabled(out) -> bool:
    try:
        return source(out) in ("env", "marker")
    except Exception:  # noqa: BLE001
        return False


# ---- masking ----------------------------------------------------------------------------------------------

def _long(m: re.Match) -> str:
    return f"<long-run:{len(m.group(0))}>"


def mask_text(s: str) -> str:
    """Secrets, phone-like numbers and emails masked and any scheme://... redacted (log text only: also AWS access key
    ids, GitHub tokens and JWTs)."""
    from .code.refs import mask_secrets
    s = _TOKENS.sub("<token>", _LONG.sub(_long, s))
    s = _URL.sub(lambda m: m.group(0).split("://", 1)[0] + "://***", mask_secrets(s))
    return _PHONE.sub("<phone>", s)


def mask_message(s: str) -> str:
    """An error message: secrets, URLs, then string and number literals masked, cut to 300 characters."""
    from .queries.fingerprint import mask_sql
    return mask_text(mask_sql(_LONG.sub(_long, s)))[:MSG_MAX]


def mask_args(v, key: str = ""):
    """Recursively mask arguments: `sql` through mask_sql, every string through mask_secrets and URL redaction."""
    if isinstance(v, dict):
        return {str(k): mask_args(x, str(k)) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [mask_args(x, key) for x in v]
    if isinstance(v, str):
        if key == "cmd":  # command templates hold credentials (-pSECRET, Bearer ...): keep only the program
            return (v.split() or [""])[0][:60] + " <redacted-cmd>"
        if key == "uri" and v.startswith("keygraph://"):  # keygraph's own resource ids, not a connection URL
            return "keygraph://" + mask_text(v[11:])
        if key == "sql":
            from .queries.fingerprint import mask_sql
            return mask_text(mask_sql(_LONG.sub(_long, v)))
        return mask_text(v)
    return v if v is None or isinstance(v, (bool, int, float)) else mask_text(str(v))


def mask_response(tool: str, obj):
    """Replace sampled profile values anywhere in a response with "<masked>" (counts and patterns stay)."""
    if isinstance(obj, dict):
        profile = "sample_size" in obj
        return {k: "<masked>" if (k in _VALUE_KEYS or (profile and k not in _PROFILE_KEEP)) and x not in (None, [], {})
                else mask_response(tool, x) for k, x in obj.items()}
    if isinstance(obj, list):
        return [mask_response(tool, x) for x in obj]
    return mask_text(obj) if isinstance(obj, str) else obj


# ---- response policy --------------------------------------------------------------------------------------

# Tools that return database rows. Their events keep the shape of the answer, never the rows, whatever the size.
# "run_query" is the MCP tool and "run" the CLI command planned for ENG-084 (neither exists yet); the registry is
# here so the first row-returning tool cannot ship without this policy.
ROW_TOOLS: set[str] = {"run_query", "run"}
_ROW_SCALARS = (bool, int, float)


def register_row_tool(name: str) -> None:
    ROW_TOOLS.add(name)


def is_row_tool(name: str) -> bool:
    return name in ROW_TOOLS


# `keygraph index ...` commands and the MCP index_* tools carry packets (profile values, SQL), agent answers and rejection
# reasons. Their events keep ids, counts and statuses only: the answer argument becomes list lengths, a response keeps
# numbers, a few fixed words and ids, lists become lengths, and any other text is dropped.
_INDEX_WORDS = {"status", "unit_id", "job_id", "next", "finish", "resume", "probe_source"}
_INDEX_SIZED = {"packet", "answer_schema", "instructions"}
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_index_tool(name: str) -> bool:
    return isinstance(name, str) and (name.startswith(("index ", "index_")))


def index_shape(obj, key: str = ""):
    """The loggable shape of an index command's JSON result (never text from an agent, a packet or a reason)."""
    if key in _INDEX_SIZED:
        return {"bytes": len(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8", "replace"))}
    if isinstance(obj, dict):
        return {str(k): index_shape(v, str(k)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return {"len": len(obj)}
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    return mask_text(str(obj))[:200] if key in _INDEX_WORDS else "<text>"


def index_args(args):
    """Arguments of an index call with the answer (agent text) reduced to list lengths per kind."""
    if not isinstance(args, dict):
        return args
    out = dict(args)
    for k in ("url_env", "cmd_env"):  # a value typed here (a URL, a command) must never be logged: keep a valid name only
        v = out.get(k)
        if v:
            out[k] = v if isinstance(v, str) and _ENV_NAME.match(v) else "<invalid>"
    ans = out.get("answer")
    if ans is not None:
        out["answer"] = ({str(k): (len(v) if isinstance(v, list) else "<text>") for k, v in ans.items()}
                         if isinstance(ans, dict) else "<text>")
    return out


def _scalar(v):
    return v if v is None or isinstance(v, _ROW_SCALARS) else mask_text(str(v))[:200]


def _msg(v):
    """A warning or check text: secrets, URLs and SQL string/number literals masked, cut to 300 characters."""
    return v if v is None or isinstance(v, _ROW_SCALARS) else mask_message(str(v))

def rows_shape(obj) -> dict:
    """The only thing logged for a row-returning tool: row_count, column names and types, truncated, warnings, checks.
    Built from an allow-list, so a key it does not know (rows, data, sample, ...) is never copied."""
    if not isinstance(obj, dict):
        return {"row_count": len(obj) if isinstance(obj, list) else None}
    rows = obj.get("rows") if isinstance(obj.get("rows"), list) else []
    cols = obj.get("columns")
    types = obj.get("types") or obj.get("column_types")
    out_cols: list = []
    if isinstance(cols, list):
        for i, c in enumerate(cols):
            if isinstance(c, dict):
                out_cols.append({"name": _scalar(c.get("name")), "type": _scalar(c.get("type"))})
            else:
                ty = types[i] if isinstance(types, list) and i < len(types) else types.get(c) if isinstance(types, dict) else None
                out_cols.append({"name": _scalar(c), "type": _scalar(ty)})
    elif rows and isinstance(rows[0], dict):
        out_cols = [{"name": _scalar(k), "type": None} for k in rows[0]]
    n = obj.get("row_count")
    shape: dict = {"row_count": n if isinstance(n, int) and not isinstance(n, bool) else len(rows), "columns": out_cols,
                   "truncated": bool(obj.get("truncated", False))}
    for k in ("warnings", "checks"):
        v = obj.get(k)
        if isinstance(v, list):
            shape[k] = [_msg(x) for x in v][:50]
        elif isinstance(v, dict):
            shape[k] = {str(a): _msg(b) for a, b in list(v.items())[:50]}
    return shape


def _shape(tool: str, obj) -> dict:
    if not isinstance(obj, dict):
        return {"type": type(obj).__name__, "len": len(obj)} if isinstance(obj, (list, str)) else {"type": type(obj).__name__}
    s: dict = {"keys": sorted(obj)[:30]}
    lists = {k: len(x) for k, x in obj.items() if isinstance(x, list)}
    if lists:
        s["lists"] = dict(sorted(lists.items()))
    if tool == "search" and isinstance(obj.get("tables"), list):
        s["tables"] = [t.get("table", t.get("id")) if isinstance(t, dict) else t for t in obj["tables"][:20]]
    if tool == "path" and isinstance(obj.get("paths"), list):
        s["paths"] = len(obj["paths"])
        sc = [p.get("score") for p in obj["paths"] if isinstance(p, dict) and isinstance(p.get("score"), (int, float))]
        if sc:
            s["top_score"] = max(sc)
    return s


def _ref(text: str, shape: dict) -> dict:
    b = text.encode("utf-8", "replace")
    return {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b), "tokens_approx": len(text) // 4, "shape": shape}


def response_fields(tool: str, obj) -> dict:
    """{"response": obj} when its JSON is <= 16 KB, else {"response_ref": {sha256, bytes, tokens_approx, shape}}."""
    if tool in ROW_TOOLS:
        return {"response": rows_shape(obj)}
    if is_index_tool(tool):
        return {"response": index_shape(obj)}
    obj = mask_response(tool, obj)
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    if len(text.encode("utf-8", "replace")) <= INLINE_MAX:
        return {"response": obj}
    return {"response_ref": _ref(text, _shape(tool, obj))}


def stdout_fields(tool: str, text: str, nbytes: int, digest: str, tokens: int, inline: bool = True, lines: int = 0) -> dict:
    """Same policy for a command's captured stdout. JSON stdout goes through mask_response; text stays text.
    A row-returning command keeps its shape only (never text, whatever the size)."""
    if tool in ROW_TOOLS:
        with contextlib.suppress(ValueError, RecursionError):
            if text.lstrip()[:1] == "{" and nbytes <= INLINE_MAX:
                return {"response": rows_shape(json.loads(text))}
        return {"response_ref": {"sha256": digest, "bytes": nbytes, "tokens_approx": tokens, "shape": {"lines": lines or text.count("\n")}}}
    if is_index_tool(tool):
        with contextlib.suppress(ValueError, RecursionError):
            if text.lstrip()[:1] == "{" and nbytes <= INLINE_MAX:
                return {"response": index_shape(json.loads(text))}
        return {"response_ref": {"sha256": digest, "bytes": nbytes, "tokens_approx": tokens, "shape": {"lines": lines or text.count("\n")}}}
    if nbytes <= INLINE_MAX and inline:
        with contextlib.suppress(ValueError, RecursionError):
            if text.lstrip()[:1] in "{[":
                return response_fields(tool, json.loads(text))
        return {"response": mask_text(text)}
    shape: dict = {"lines": lines or text.count("\n")}
    if nbytes <= INLINE_MAX:
        with contextlib.suppress(ValueError, RecursionError):
            shape = _shape(tool, json.loads(text))
    return {"response_ref": {"sha256": digest, "bytes": nbytes, "tokens_approx": tokens, "shape": shape}}


def _private_dir(d: Path) -> None:
    """Create the dev dir (and parents below it) with mode 0700; best effort where modes mean little (Windows)."""
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        if d.name == "traces":
            d.parent.chmod(0o700)
        d.chmod(0o700)


# ---- sessions and recorder --------------------------------------------------------------------------------

_PROCESS = uuid.uuid4().hex[:12]


def session_id(surface: str, out=None) -> str:
    """MCP: one id per process. CLI: KEYGRAPH_DEV_SESSION if set; otherwise an id derived from the parent process
    (the agent's shell) and the dev dir, so the commands one agent shell runs group together."""
    if surface == "mcp":
        return _PROCESS
    env = os.environ.get("KEYGRAPH_DEV_SESSION")
    if env:
        return env
    if out is None:
        return uuid.uuid4().hex[:12]
    return hashlib.sha1(f"{os.getppid()}:{dev_dir(out).resolve()}".encode()).hexdigest()[:12]


def session_source(surface: str) -> str:
    """Where session_id gets its value: "mcp" (the server process), "env" (KEYGRAPH_DEV_SESSION) or "ppid" (the fallback).
    The report regroups "ppid" CLI events by idle time, since every agent tool call is usually a new shell."""
    if surface == "mcp":
        return "mcp"
    return "env" if os.environ.get("KEYGRAPH_DEV_SESSION") else "ppid"


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


class Recorder:
    """Appends one JSON line per event to <out>/dev/events.jsonl, rotating at MAX_BYTES."""

    def __init__(self, out, surface: str = "cli", client: dict | None = None, session: str | None = None):
        self.path = dev_dir(out) / "events.jsonl"
        self.surface, self.client = surface, client or {}
        self.session = session or session_id(surface, out)
        self.seq = 0

    def _rotate(self) -> None:
        """Shift events.N -> N+1 and the live file -> 1 once it is full. Rename failures (another process got there
        first) are ignored; the size is re-checked so a second process does not rotate again."""
        try:
            if self.path.stat().st_size < MAX_BYTES:
                return
        except FileNotFoundError:
            return
        d = self.path.parent
        for i in range(KEEP_ROTATED, 0, -1):
            src = self.path if i == 1 else d / f"events.{i - 1}.jsonl"
            with contextlib.suppress(OSError):
                os.replace(src, d / f"events.{i}.jsonl")

    def record(self, kind: str, tool: str, args=None, ms: float = 0.0, ok: bool = True, error: dict | None = None,
               response: dict | None = None, decisions: list | None = None, trace: str | None = None) -> bool:
        """Append one event; False when it could not be written. Never raises."""
        try:
            self.seq += 1
            ev: dict = {"v": V, "ts": now(), "session": self.session, "session_source": session_source(self.surface), "seq": self.seq, "surface": self.surface,
                        "client": self.client, "kind": kind, "tool": tool,
                        "args": mask_args(index_args(args or {}) if is_index_tool(tool) else args or {}),
                        "ms": round(ms, 2), "ok": ok}
            if error:
                ev["error"] = {"type": error.get("type", ""), "message": mask_message(str(error.get("message", "")))}
            ev.update(response or {})
            if decisions:
                ds = list(decisions)
                marked = len(ds) == DECISIONS_MAX + 1 and isinstance(ds[-1], dict) and ds[-1].get("kind") == "truncated"
                if len(ds) > DECISIONS_MAX and not marked:
                    ds = ds[:DECISIONS_MAX] + [{"kind": "truncated", "dropped": len(ds) - DECISIONS_MAX}]
                ev["decisions"] = mask_args(ds)
            if trace:
                ev["trace"] = trace
            _private_dir(self.path.parent)
            self._rotate()
            data = (json.dumps(ev, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8", "replace")
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                view = memoryview(data)  # normally one syscall on an O_APPEND fd, so writers do not interleave lines
                while view:
                    view = view[os.write(fd, view):]
            finally:
                os.close(fd)
            return True
        except Exception as exc:  # noqa: BLE001  (logging must never change a result)
            _warn(exc)
            return False


# ---- tracer and decisions ---------------------------------------------------------------------------------

_TRACER: contextvars.ContextVar = contextvars.ContextVar("keygraph_tracer", default=None)
_DECISIONS: contextvars.ContextVar = contextvars.ContextVar("keygraph_decisions", default=None)


class Tracer:
    """Writes events to one trace file; after the cap it writes a single {"kind":"truncated"} and goes quiet."""

    def __init__(self, path: Path):
        self.path, self.n, self.size, self.done = path, 0, 0, False
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        self.f = open(fd, "a", encoding="utf-8", errors="replace", closefd=True)  # noqa: SIM115  (held open for the length of a build)

    def emit(self, kind: str, fields: dict) -> None:
        if self.done:
            return
        try:
            line = None
            if self.n >= TRACE_MAX_EVENTS or self.size >= TRACE_MAX_BYTES:
                self.done = True
                line = json.dumps({"kind": "truncated", "events": self.n}) + "\n"
            else:
                line = json.dumps({"kind": kind, **{k: mask_args(v, k) for k, v in fields.items()}},
                                  ensure_ascii=False, sort_keys=True, default=str) + "\n"
            self.f.write(line)
            self.n += 1
            self.size += len(line.encode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001  (skip this event; give up after a few failures)
            self.fails = getattr(self, "fails", 0) + 1
            self.done = self.done or self.fails > 3
            _warn(exc)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.f.close()


def active() -> bool:
    """True when a build tracer or a decisions collector is listening. Guard loops with this so dev off costs ~nothing."""
    return _TRACER.get() is not None or _DECISIONS.get() is not None


def trace(kind: str, **fields) -> None:
    """Record a build-time decision; one contextvar lookup and nothing else when no tracer is active."""
    t = _TRACER.get()
    if t is not None:
        t.emit(kind, fields)


def _prune(d: Path) -> None:
    files = sorted(d.glob("*.jsonl"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    for p in files[KEEP_TRACES:]:
        p.unlink(missing_ok=True)


@contextlib.contextmanager
def tracing(out, name: str):
    """Open <out>/dev/traces/<run-id>.jsonl for the block when dev mode is on; otherwise a no-op. Yields the path or None.
    Nested in an open trace (probe calling render) it joins that trace and yields its path."""
    cur = _TRACER.get()
    if cur is not None:
        yield cur.path
        return
    t = None
    try:
        if enabled(out):
            d = dev_dir(out) / "traces"
            _private_dir(d)
            path = d / f"{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S%f}-{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.jsonl"
            t = Tracer(path)
            _prune(d)
    except Exception as exc:  # noqa: BLE001
        _warn(exc)
        if t:
            t.close()
        t = None
    tok = _TRACER.set(t)
    try:
        yield t.path if t else None
    finally:
        _TRACER.reset(tok)
        if t:
            t.close()


@contextlib.contextmanager
def collecting():
    """Collect query-time decisions (capped at 200) to attach to a call event. Yields the list."""
    lst: list = []
    tok = _DECISIONS.set(lst)
    try:
        yield lst
    finally:
        _DECISIONS.reset(tok)


def _typed_name(name: str) -> str:
    """A table name the user typed, as logged: through mask_text (secrets, token shapes, 512+ character runs)."""
    return mask_text(name)


def decide(kind: str, **fields) -> None:
    """A decision: goes on the call's `decisions` when a collector is active, and into the build trace when one is."""
    if kind == "resolve" and isinstance(fields.get("name"), str):
        fields["name"] = _typed_name(fields["name"])
    lst = _DECISIONS.get()
    if lst is not None:
        if len(lst) < DECISIONS_MAX:
            lst.append({"kind": kind, **fields})
        elif lst[-1].get("kind") == "truncated":
            lst[-1]["dropped"] += 1
        else:
            lst.append({"kind": "truncated", "dropped": 1})
    t = _TRACER.get()
    if t is not None:
        t.emit(kind, fields)


class _Phase:
    """`with devlog.phase("render.docs") as p: ...; p["files"] = n` writes {"kind": "phase", "name", "ms", **p} on exit."""
    __slots__ = ("info", "name", "t0")

    def __init__(self, name: str, info: dict):
        self.name, self.info = name, info

    def __enter__(self) -> dict:
        self.t0 = time.perf_counter()
        return self.info

    def __exit__(self, *exc) -> None:
        t = _TRACER.get()
        if t is not None:
            t.emit("phase", {"name": self.name, "ms": round((time.perf_counter() - self.t0) * 1000, 2), **self.info})


def phase(name: str, **fields) -> _Phase:
    """Time a block as a phase event in the active build trace (a bare timer and nothing written when none is active)."""
    return _Phase(name, dict(fields))


def note(out, text: str, surface: str = "cli", session: str | None = None, client: dict | None = None,
         recorder: Recorder | None = None) -> dict:
    """Record the question an agent is about to answer (dev mode only). The one function behind `keygraph dev note
    --json` and the MCP `dev_note` tool, so both return {"noted", "session", "question", "hint"} (plus "reason"
    when nothing was recorded: "dev mode is off" or "write failed")."""
    q = mask_text(text)
    sess = recorder.session if recorder else session or session_id(surface, out)
    on = enabled(out)
    r: dict = {"noted": False, "session": sess, "question": q}
    if on:
        r["noted"] = (recorder or Recorder(out, surface, client, sess)).record("note", "dev_note", {"question": q})
        if not r["noted"]:
            r["reason"] = "write failed"
    else:
        r["reason"] = "dev mode is off"
    if surface == "cli":
        r["hint"] = "export KEYGRAPH_DEV_SESSION=" + sess + " to group the following commands with this note"
    elif r["noted"]:
        r["hint"] = "this session is the MCP server's: its calls are grouped with this note automatically"
    else:
        r["hint"] = "nothing was recorded, so there is nothing to group; do not retry"
    return r


# ---- housekeeping used by `keygraph dev` ------------------------------------------------------------------

def events_bytes(out) -> int:
    d = dev_dir(out)
    return sum(p.stat().st_size for p in d.glob("events*.jsonl")) if d.is_dir() else 0


def status(out) -> dict:
    d = dev_dir(out)
    src = source(out)
    return {"enabled": src != "off", "source": src, "dir": str(d), "events_bytes": events_bytes(out),
            "trace_files": len(list((d / "traces").glob("*.jsonl"))) if (d / "traces").is_dir() else 0}


def clear(out, everything: bool = False) -> None:
    import shutil
    d = dev_dir(out)
    for p in d.glob("events*.jsonl"):
        p.unlink(missing_ok=True)
    shutil.rmtree(d / "traces", ignore_errors=True)
    if everything:
        (d / "enabled").unlink(missing_ok=True)
