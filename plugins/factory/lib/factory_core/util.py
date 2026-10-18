"""Small, dependency-free helpers shared by the engine, hooks and server."""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX only; the factory targets macOS, Linux and WSL.
    import fcntl
except ImportError:  # pragma: no cover - Windows without WSL
    fcntl = None  # type: ignore[assignment]


class FactoryError(Exception):
    """An error the CLI reports to the user with a non-zero exit code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def now_iso() -> str:
    """UTC timestamp with millisecond precision, e.g. 2026-10-18T16:23:24.496Z."""
    t = _dt.datetime.now(_dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def parse_iso(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    try:
        return _dt.datetime.strptime(value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        try:
            return _dt.datetime.strptime(value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None


def seconds_between(start: str | None, end: str | None) -> float | None:
    a, b = parse_iso(start), parse_iso(end)
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds())


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON so readers never see a half-written file (tmp file + rename)."""
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def append_jsonl(path: Path, record: dict) -> None:
    """Append one JSON line. A single write with O_APPEND keeps concurrent lines whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line)
    finally:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, skipping a truncated or corrupt line instead of failing."""
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    out.append(value)
    except FileNotFoundError:
        pass
    return out


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Exclusive advisory lock held for the duration of the block."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def real(path: str | os.PathLike) -> Path:
    """Resolve symlinks so /var and /private/var compare equal on macOS."""
    return Path(os.path.realpath(os.fspath(path)))


def is_within(child: str | os.PathLike, parent: str | os.PathLike) -> bool:
    c, p = real(child), real(parent)
    return c == p or p in c.parents


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_words: int = 4) -> str:
    words = [w for w in _SLUG_RE.split(text.lower()) if w]
    stop = {"a", "an", "the", "to", "of", "for", "and", "on", "in", "with", "add", "make"}
    picked = [w for w in words if w not in stop] or words
    slug = "-".join(picked[:max_words])[:40].strip("-")
    return slug or "job"


def first_line(text: str) -> str:
    """The first non-empty line, normalised (BOM, surrounding whitespace, bold markers)."""
    for line in text.lstrip("﻿").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.replace("**", "").strip()
    return ""


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
