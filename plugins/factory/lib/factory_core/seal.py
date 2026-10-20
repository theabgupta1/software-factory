"""Tamper evidence for the files that steer the factory.

Agents run as you and can write any file you can, so the state that decides what happens next — job.json, the
messages you send to agents and their receipts, and the subagent → step map the hooks use — carries an HMAC made
with a per-repository key. The key lives in the shared git directory (`.git/factory/state.key`, mode 0600), outside
every worktree and outside `.factory/`. The engine refuses a job.json whose seal does not match, and the hooks and
the console ignore unsealed messages and map entries.

This raises the bar and makes tampering visible; it is not a sandbox. An agent that reads the key can still forge
state, which is why the guard also refuses agent commands that name the key or the engine's files.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from .paths import git_common_dir

KEY_FILE = "state.key"
_KEYS: dict[str, bytes] = {}


def key_path(root: Path) -> Path:
    """Where the key for the repository whose main checkout is `root` lives."""
    dot = Path(root) / ".git"
    base = dot if dot.is_dir() else (git_common_dir(Path(root)) or dot)
    return base / "factory" / KEY_FILE


def _load(path: Path) -> bytes | None:
    try:
        data = path.read_bytes().strip()
    except FileNotFoundError:
        return None
    return data if len(data) >= 32 else None


def key(root: Path) -> bytes:
    path = key_path(root)
    cached = _KEYS.get(str(path))
    if cached is not None:
        return cached
    value = _load(path)
    if value is None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_name(f".{KEY_FILE}.{os.getpid()}.{secrets.token_hex(4)}")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, secrets.token_hex(32).encode("ascii"))
        finally:
            os.close(fd)
        try:
            os.link(tmp, path)  # atomic: exactly one process creates the key, the others read it
        except FileExistsError:
            pass
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
        value = _load(path)
        if value is None:
            raise OSError(f"could not create the factory state key at {path}")
    _KEYS[str(path)] = value
    return value


def forget_keys() -> None:
    """Drop cached keys (tests that delete a repository and reuse its path)."""
    _KEYS.clear()


def mac(root: Path, *parts) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hmac.new(key(root), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def seal(root: Path, record: dict, *context) -> dict:
    """Set record["mac"] over the record (without its mac) and `context`; returns the record."""
    body = {k: v for k, v in record.items() if k != "mac"}
    record["mac"] = mac(root, *context, body)
    return record


def intact(root: Path, record, *context) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("mac"), str):
        return False
    body = {k: v for k, v in record.items() if k != "mac"}
    try:
        return hmac.compare_digest(record["mac"], mac(root, *context, body))
    except OSError:
        return False
