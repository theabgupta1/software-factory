"""Git operations the engine performs itself: worktrees, inspection, and the local merge."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .paths import git
from .util import FactoryError

DIFF_FILE_LIMIT = 400


def shared_fingerprint(repo: Path) -> str:
    """Hash of the repository's shared git configuration and hooks. Agents must never change these: a planted hook
    or alias runs on the user's machine long after the job, so the engine stops a job when they change."""
    import hashlib

    common = Path(repo) / ".git"
    if not common.is_dir():
        from .paths import git_common_dir

        common = git_common_dir(Path(repo)) or common
    h = hashlib.sha256()
    for rel in ("config", "info/attributes"):
        p = common / rel
        h.update(rel.encode() + b"\0" + (p.read_bytes() if p.is_file() else b"-") + b"\0")
    hooks = common / "hooks"
    if hooks.is_dir():
        for p in sorted(hooks.iterdir()):
            if not p.name.endswith(".sample"):
                h.update(b"hook:" + p.name.encode() + b"\0" + (p.read_bytes() if p.is_file() else b"dir") + b"\0")
    return h.hexdigest()


def branch_exists(repo: Path, branch: str) -> bool:
    return git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo, check=False).returncode == 0


def rev(repo: Path, ref: str) -> str:
    return git(["rev-parse", ref], repo).stdout.strip()


def create_worktree(repo: Path, worktree: Path, branch: str, base: str) -> str:
    """Create (or adopt, when resuming) the job's worktree. Returns the base commit sha."""
    if not branch_exists(repo, base):
        raise FactoryError(
            f"base branch {base!r} does not exist; set base_branch in .factory/config.json"
        )
    base_sha = rev(repo, base)
    if worktree.exists() and (worktree / ".git").exists():
        current = git(["branch", "--show-current"], worktree, check=False).stdout.strip()
        if current != branch:
            raise FactoryError(f"{worktree} exists but is on {current!r}, not {branch!r}")
        return base_sha
    worktree.parent.mkdir(parents=True, exist_ok=True)
    if branch_exists(repo, branch):
        git(["worktree", "add", str(worktree), branch], repo)
    else:
        git(["worktree", "add", "-b", branch, str(worktree), base], repo)
    return base_sha


def head(worktree: Path) -> str:
    return rev(worktree, "HEAD")


def is_clean(worktree: Path) -> bool:
    """Tracked files only: build outputs and caches the repo doesn't ignore must not count as dirt."""
    out = git(["status", "--porcelain", "--untracked-files=no"], worktree).stdout
    return not out.strip()


def changed_paths(repo: Path, base: str, ref: str) -> list[str]:
    proc = git(["diff", "--name-only", f"{base}...{ref}"], repo, check=False)
    if proc.returncode != 0:
        return []
    return [p for p in proc.stdout.splitlines() if p.strip()][:DIFF_FILE_LIMIT]


def paths_between(worktree: Path, old: str, new: str) -> list[str]:
    proc = git(["diff", "--name-only", f"{old}..{new}"], worktree, check=False)
    return [p for p in proc.stdout.splitlines() if p.strip()] if proc.returncode == 0 else []


def commits_with_trailer(worktree: Path, base: str, trailer: str) -> list[str]:
    proc = git(
        ["log", "--format=%H", f"--grep=^{re.escape(trailer)}$", f"{base}..HEAD"], worktree, check=False
    )
    return [c for c in proc.stdout.split() if c] if proc.returncode == 0 else []


def commit_count(repo: Path, base: str, ref: str) -> int:
    proc = git(["rev-list", "--count", f"{base}..{ref}"], repo, check=False)
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return 0


def log(repo: Path, base: str, ref: str, limit: int = 100) -> list[dict]:
    proc = git(
        ["log", f"--max-count={limit}", "--format=%H%x1f%h%x1f%an%x1f%aI%x1f%s", f"{base}..{ref}"],
        repo,
        check=False,
    )
    out = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 5:
            out.append({"sha": parts[0], "short": parts[1], "author": parts[2], "date": parts[3], "subject": parts[4]})
    return out


def diff(repo: Path, base: str, ref: str, max_bytes: int = 1_500_000) -> dict:
    stat = git(["diff", "--numstat", f"{base}...{ref}"], repo, check=False).stdout
    files = []
    for line in stat.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            add, rem, path = parts
            files.append({
                "path": path,
                "added": None if add == "-" else int(add),
                "removed": None if rem == "-" else int(rem),
                "binary": add == "-",
            })
    patch = git(["diff", "--no-color", "--no-ext-diff", f"{base}...{ref}"], repo, check=False).stdout
    truncated = len(patch.encode("utf-8")) > max_bytes
    if truncated:
        patch = patch.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return {"files": files[:DIFF_FILE_LIMIT], "patch": patch, "truncated": truncated}


def diffstat(repo: Path, base: str, ref: str, max_lines: int = 60) -> str:
    proc = git(["diff", "--stat=120", f"{base}...{ref}"], repo, check=False)
    lines = proc.stdout.splitlines() if proc.returncode == 0 else []
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [f" … {len(lines) - max_lines + 1} more lines", lines[-1]]
    return "\n".join(lines)


def copy_into_worktree(repo: Path, worktree: Path, paths: list[str], timeout: int = 300) -> list[str]:
    """Copy untracked files (e.g. .env, node_modules) from the main checkout; copy-on-write when possible.

    Missing sources and already-present destinations are skipped. Returns the paths copied.
    """
    import shutil
    import subprocess
    import sys

    copied = []
    cow = ["-cR"] if sys.platform == "darwin" else ["-R", "--reflink=auto"]
    for rel in paths:
        src = (repo / rel)
        dest = (worktree / rel)
        if not src.exists() or dest.exists():
            continue
        if repo.resolve() not in src.resolve().parents:
            raise FactoryError(f"worktree.copy entry {rel!r} points outside the repository")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(["cp", *cow, str(src), str(dest)], capture_output=True, text=True, timeout=timeout)
            if proc.returncode != 0:
                proc = subprocess.run(["cp", "-R", str(src), str(dest)], capture_output=True, text=True, timeout=timeout)
            if proc.returncode != 0:
                raise FactoryError(f"could not copy {rel} into the worktree: {proc.stderr.strip()[:300]}")
        except FileNotFoundError:  # no cp (Windows without WSL)
            if src.is_dir():
                shutil.copytree(src, dest, symlinks=True)
            else:
                shutil.copy2(src, dest)
        except subprocess.TimeoutExpired as exc:
            raise FactoryError(f"copying {rel} into the worktree took longer than {timeout}s") from exc
        copied.append(rel)
    return copied


def supports_merge_tree(repo: Path) -> bool:
    proc = git(["merge-tree", "-h"], repo, check=False)
    return "--write-tree" in (proc.stdout + proc.stderr)


def conflicts(repo: Path, base: str, branch: str) -> list[str] | None:
    """Files that would conflict, [] when clean, None when this git can't tell (< 2.38)."""
    if not supports_merge_tree(repo):
        return None
    proc = git(["merge-tree", "--write-tree", "--name-only", base, branch], repo, check=False)
    if proc.returncode == 0:
        return []
    lines = proc.stdout.splitlines()[1:]
    files = []
    for line in lines:
        if not line.strip():
            break
        files.append(line.strip())
    return files or ["(unknown files)"]


@dataclass
class MergeResult:
    status: str  # merged | busy | conflict | failed
    message: str
    sha: str | None = None
    cleanup: str | None = None


def merge(repo: Path, base: str, branch: str, worktree: Path | None, title: str) -> MergeResult:
    current = git(["branch", "--show-current"], repo, check=False).stdout.strip()
    if current != base:
        return MergeResult("busy", f"the main checkout is on {current or 'a detached HEAD'!r}, not {base!r}")
    if not is_clean(repo):
        return MergeResult("busy", "the main checkout has uncommitted changes")
    found = conflicts(repo, base, branch)
    if found:
        return MergeResult("conflict", "merging would conflict in: " + ", ".join(found[:10]))
    message = f"Merge {branch}: {title}"[:200]
    proc = git(["merge", "--no-ff", "--no-edit", "-m", message, branch], repo, check=False, timeout=300)
    if proc.returncode != 0:
        if Path(repo, ".git", "MERGE_HEAD").exists():
            git(["merge", "--abort"], repo, check=False)
        return MergeResult("failed", (proc.stderr or proc.stdout).strip()[:500] or "git merge failed")
    sha = rev(repo, "HEAD")
    cleanup = None
    if worktree is not None and worktree.exists():
        rm = git(["worktree", "remove", str(worktree)], repo, check=False)
        if rm.returncode != 0:
            cleanup = f"git worktree remove failed: {(rm.stderr or rm.stdout).strip()[:300]}"
    if cleanup is None:
        git(["branch", "-d", branch], repo, check=False)
    return MergeResult("merged", f"merged {branch} into {base}", sha=sha, cleanup=cleanup)


def remove_unused_worktree(repo: Path, worktree: Path, branch: str, base: str) -> str | None:
    """Drop a worktree whose branch has no commits (artifact-only workflows). Returns a note or None."""
    if not worktree.exists():
        return None
    if commit_count(repo, base, branch) > 0:
        return f"kept {worktree} because {branch} has commits"
    rm = git(["worktree", "remove", "--force", str(worktree)], repo, check=False)
    if rm.returncode != 0:
        return f"could not remove {worktree}: {(rm.stderr or rm.stdout).strip()[:200]}"
    git(["branch", "-D", branch], repo, check=False)
    return None
