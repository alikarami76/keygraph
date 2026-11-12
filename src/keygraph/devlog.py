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
# unquoted key=value secrets (password=x, api_key: x): the key is a whole word, never a *_id column, and the value
# is not a dotted name (a.token = b.token is a join, not a secret) or a placeholder
_PLAIN_KV = re.compile(r"(?<![\w.])((?:password|passwd|pwd|secret|token|api_?key|access_?key)\s*[=:]\s*)"
                       r"(?![*?'\"\s<:$@])(?![\w$]+\.[\w$])[^\s'\",;&)]+", re.IGNORECASE)
# phone-like numbers: 7+ digit runs or grouped digits (dates such as 2026-11-12 stay)
_PHONE = re.compile(r"(?<![\w.])(?:\+?\d{7,}|\+?\d{2,4}[\s-]\d{3}[\s-]\d{3,4}(?:[\s-]\d{2,4})?)(?![\w.])")
MSG_MAX = 300
# keys that hold sampled column values in a profile (counts, null rates, lengths and patterns stay)
_VALUE_KEYS = {"top_values", "numeric", "dates", "examples", "sample_values", "samples"}
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

def mask_text(s: str) -> str:
    """Secrets (also plain key=value), phone-like numbers and emails masked and any scheme://... redacted."""
    from .code.refs import mask_secrets
    s = _LONG.sub(lambda m: f"<long-run:{len(m.group(0))}>", s)
    s = _URL.sub(lambda m: m.group(0).split("://", 1)[0] + "://***", mask_secrets(s))
    return _PHONE.sub("<phone>", _PLAIN_KV.sub(r"\1***", s))


def mask_message(s: str) -> str:
    """An error message: secrets, URLs, then string and number literals masked, cut to 300 characters."""
    from .queries.fingerprint import mask_sql
    return mask_sql(mask_text(s))[:MSG_MAX]


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
            return mask_sql(mask_text(v))
        return mask_text(v)
    return v if v is None or isinstance(v, (bool, int, float)) else mask_text(str(v))


def mask_response(tool: str, obj):
    """Replace sampled profile values anywhere in a response with "<masked>" (counts and patterns stay)."""
    if isinstance(obj, dict):
        return {k: "<masked>" if k in _VALUE_KEYS and x not in (None, [], {}) else mask_response(tool, x) for k, x in obj.items()}
    if isinstance(obj, list):
        return [mask_response(tool, x) for x in obj]
    return mask_text(obj) if isinstance(obj, str) else obj


# ---- response policy --------------------------------------------------------------------------------------

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
    obj = mask_response(tool, obj)
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    if len(text.encode("utf-8", "replace")) <= INLINE_MAX:
        return {"response": obj}
    return {"response_ref": _ref(text, _shape(tool, obj))}


def stdout_fields(tool: str, text: str, nbytes: int, digest: str, tokens: int, inline: bool = True, lines: int = 0) -> dict:
    """Same policy for a command's captured stdout. JSON stdout goes through mask_response; text stays text."""
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


# ---- sessions and recorder --------------------------------------------------------------------------------

_PROCESS = uuid.uuid4().hex[:12]


def session_id(surface: str) -> str:
    """MCP: one id per process. CLI: KEYGRAPH_DEV_SESSION if set, else a fresh id per command."""
    if surface == "mcp":
        return _PROCESS
    return os.environ.get("KEYGRAPH_DEV_SESSION") or uuid.uuid4().hex[:12]


def now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


class Recorder:
    """Appends one JSON line per event to <out>/dev/events.jsonl, rotating at MAX_BYTES."""

    def __init__(self, out, surface: str = "cli", client: dict | None = None, session: str | None = None):
        self.path = dev_dir(out) / "events.jsonl"
        self.surface, self.client = surface, client or {}
        self.session = session or session_id(surface)
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
            ev: dict = {"v": V, "ts": now(), "session": self.session, "seq": self.seq, "surface": self.surface,
                        "client": self.client, "kind": kind, "tool": tool, "args": mask_args(args or {}),
                        "ms": round(ms, 2), "ok": ok}
            if error:
                ev["error"] = {"type": error.get("type", ""), "message": mask_message(str(error.get("message", "")))}
            ev.update(response or {})
            if decisions:
                ev["decisions"] = decisions[:DECISIONS_MAX]
            if trace:
                ev["trace"] = trace
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate()
            data = (json.dumps(ev, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8", "replace")
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
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
        self.f = open(path, "a", encoding="utf-8", errors="replace")  # noqa: SIM115  (held open for the length of a build)

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
    return _TRACER.get() is not None


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
    """Open <out>/dev/traces/<run-id>.jsonl for the block when dev mode is on; otherwise a no-op. Yields the path or None."""
    t = None
    try:
        if enabled(out):
            d = dev_dir(out) / "traces"
            d.mkdir(parents=True, exist_ok=True)
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


def decide(kind: str, **fields) -> None:
    lst = _DECISIONS.get()
    if lst is not None and len(lst) < DECISIONS_MAX:
        lst.append({"kind": kind, **fields})


def note(out, text: str, surface: str = "cli", session: str | None = None, client: dict | None = None,
         recorder: Recorder | None = None) -> dict:
    """Record the question an agent is about to answer (dev mode only). The one function behind `keygraph dev note
    --json` and the MCP `dev_note` tool, so both return {"noted", "session", "question"}."""
    q = mask_text(text)
    sess = recorder.session if recorder else session or session_id(surface)
    on = enabled(out)
    if on:
        on = (recorder or Recorder(out, surface, client, sess)).record("note", "dev_note", {"question": q})
    return {"noted": on, "session": sess, "question": q}


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
