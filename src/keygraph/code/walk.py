"""File walk for the code index: which files to read, their language, hash and line positions."""

from __future__ import annotations

import bisect
import fnmatch
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = frozenset({".git", "node_modules", "vendor", "dist", "build", "target", ".venv", "venv", "__pycache__",
                       ".next", "coverage"})
LANGS = {".php": "php", ".py": "py", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "ts", ".tsx": "ts",
         ".java": "java", ".kt": "kt", ".go": "go", ".rb": "rb", ".cs": "cs", ".sql": "sql", ".prisma": "prisma"}
LOCK_NAMES = frozenset({"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock", "poetry.lock", "uv.lock",
                        "Gemfile.lock", "Cargo.lock", "go.sum"})
BINARY_PROBE = 8192


@dataclass(frozen=True)
class SourceFile:
    path: str  # relative to the repo root, POSIX separators
    abs: Path
    lang: str


def _excluded(rel: str, name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(name, g) for g in patterns)


def walk(root: str | Path, exclude: tuple[str, ...] | list[str] = (), max_file_kb: int = 1024) -> list[SourceFile]:
    """Every indexable file under root, sorted by path. Symlinks are never followed."""
    root = Path(root)
    patterns = tuple(exclude)
    out: list[SourceFile] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        rel_dir = here.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not (here / d).is_symlink()
                             and not _excluded(rel_dir + d, d, patterns))
        for name in sorted(filenames):
            p = here / name
            lang = LANGS.get(p.suffix.lower())
            if not lang or name in LOCK_NAMES or name.endswith(".min.js") or p.is_symlink():
                continue
            rel = rel_dir + name
            if _excluded(rel, name, patterns):
                continue
            try:
                if p.stat().st_size > max_file_kb * 1024:
                    continue
            except OSError:
                continue
            out.append(SourceFile(rel, p, lang))
    out.sort(key=lambda f: f.path)
    return out


def read(f: SourceFile) -> tuple[str, str] | None:
    """(sha1, text) of a file, or None for a binary file (a NUL byte in the first 8 KB) or an unreadable one."""
    try:
        data = f.abs.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:BINARY_PROBE]:
        return None
    return hashlib.sha1(data).hexdigest(), data.decode("utf-8", errors="replace")


class Lines:
    """Offset -> 1-based line number, and line number -> line text, for one file's text."""

    def __init__(self, text: str):
        self.text = text
        self.starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)

    def get(self, line: int) -> str:
        start = self.starts[line - 1]
        end = self.starts[line] - 1 if line < len(self.starts) else len(self.text)
        return self.text[start:end]
