"""Where things live: the plugin's own files, a repo's factory state, job folders, worktrees."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .util import FactoryError, real

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ".factory"
LEGACY_STATE_DIR = "factory"


def plugin_path(*parts: str) -> Path:
    return PLUGIN_ROOT.joinpath(*parts)


def git(args: list[str], cwd: Path, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    if not Path(cwd).is_dir():
        raise FactoryError(f"{cwd} does not exist (git {' '.join(args[:2])} was asked to run there)")
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise FactoryError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise FactoryError(f"git {' '.join(args)} timed out after {timeout}s") from exc
    if check and proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip()
        raise FactoryError(f"git {' '.join(args)} failed: {msg}")
    return proc


def git_common_dir(start: Path) -> Path | None:
    """Absolute path of the repository's shared .git directory (works on git < 2.31 too)."""
    proc = git(["rev-parse", "--git-common-dir"], start, check=False)
    if proc.returncode != 0:
        return None
    common = Path(proc.stdout.strip())
    if not common.is_absolute():
        common = Path(start) / common
    return real(common)


def find_repo_root(start: Path | None = None) -> Path:
    """The main checkout's top level, even when called from inside a linked worktree."""
    start = Path(start or os.getcwd())
    common = git_common_dir(start)
    if common is None:
        raise FactoryError(f"{start} is not inside a git repository")
    if common.name == ".git":
        return real(common.parent)
    # Bare repositories or unusual layouts: fall back to the current top level.
    top = git(["rev-parse", "--show-toplevel"], start).stdout.strip()
    return real(top)


@dataclass(frozen=True)
class RepoPaths:
    root: Path

    @property
    def state(self) -> Path:
        return self.root / STATE_DIR

    @property
    def jobs(self) -> Path:
        return self.state / "jobs"

    @property
    def legacy_jobs(self) -> Path:
        return self.root / LEGACY_STATE_DIR / "jobs"

    @property
    def config(self) -> Path:
        return self.state / "config.json"

    @property
    def backlog(self) -> Path:
        return self.state / "backlog.md"

    @property
    def legacy_backlog(self) -> Path:
        return self.root / LEGACY_STATE_DIR / "backlog.md"

    @property
    def index(self) -> Path:
        return self.state / "index.json"

    def job_dir(self, job_id: str) -> Path:
        return self.jobs / job_id

    def worktree_for(self, job_id: str) -> Path:
        return self.root.parent / f"{self.root.name}-factory" / job_id

    @classmethod
    def discover(cls, start: Path | None = None) -> "RepoPaths":
        return cls(find_repo_root(start))


def ensure_state_dir(paths: RepoPaths) -> None:
    """Create `.factory/` and keep it out of git without touching the user's .gitignore."""
    paths.jobs.mkdir(parents=True, exist_ok=True)
    common = git_common_dir(paths.root)
    if common is None:
        return
    exclude = common / "info" / "exclude"
    line = f"/{STATE_DIR}/"
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    except OSError:
        return
    if line not in existing.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with open(exclude, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(f"# Software Factory runtime state\n{line}\n")
