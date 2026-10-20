#!/usr/bin/env python3
"""Install, upgrade, inspect and uninstall the Software Factory in a Claude Code home (~/.claude).

The plugins are installed as skills-dir plugins (`<home>/skills/factory`, `<home>/skills/factory-live`), which
Claude Code loads on its own: no settings file is edited. Every install is staged and checked first, anything it
replaces is backed up to `<home>/backups/`, and `<home>/factory-install.json` records what was written (with
checksums) so uninstall removes exactly that and restores what was there before.

  scripts/install.sh [--dry-run] [--config-dir DIR] [--no-live | --with-live] [--replace-foreign] [--json]
  scripts/uninstall.sh [--dry-run] [--config-dir DIR] [--keep-backup-restore] [--purge] [--json]
  python3 scripts/install.py status [--config-dir DIR]

Exit codes: 0 done (or nothing to do), 1 error, 2 refused (a check failed), 3 conflict (would replace files the
factory did not install; pass --replace-foreign to back them up and replace them).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "factory-install.json"
SCHEMA = 1
COMPONENTS = {"factory": ROOT / "plugins" / "factory", "factory-live": ROOT / "plugins" / "factory-live"}
# Build inputs, tests and local artefacts never ship.
EXCLUDE_DIRS = {"tests", "evals", "agents-src", "protocol", "__pycache__", "node_modules", ".pytest_cache", "results"}
EXCLUDE_PATHS = {".claude-plugin/types"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".log")
EXCLUDE_NAMES = {".DS_Store", ".gitignore"}


class Refused(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


# ---- small helpers ----------------------------------------------------------------------------------
def claude_home(arg: str | None) -> Path:
    raw = arg or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return Path(raw).expanduser().resolve()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk(root: Path):
    """Files under `root` minus what never ships (build inputs, tests, caches Python writes at run time)."""
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(d for d in dirnames
                             if d not in EXCLUDE_DIRS and (rel_dir / d).as_posix() not in EXCLUDE_PATHS)
        for name in sorted(filenames):
            if name not in EXCLUDE_NAMES and not name.endswith(EXCLUDE_SUFFIXES):
                yield Path(dirpath) / name


def shipped_files(src: Path) -> list[Path]:
    out = []
    for p in _walk(src):
        if p.is_symlink():
            raise Refused(f"{p} is a symbolic link; the factory ships plain files only")
        out.append(p.relative_to(src))
    return out


def tree_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256(p) for p in _walk(root) if not p.is_symlink()}


def plugin_version(src: Path) -> str:
    return json.loads((src / ".claude-plugin" / "plugin.json").read_text())["version"]


def source_commit() -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def claude_cli() -> str | None:
    return shutil.which("claude")


def run(argv: list[str], env: dict | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})})


def load_manifest(home: Path) -> dict | None:
    p = home / MANIFEST
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except ValueError as exc:
        raise Refused(f"{p} is not valid JSON ({exc}); fix or remove it before installing", 1)
    if data.get("schema") != SCHEMA:
        raise Refused(f"{p} has schema {data.get('schema')!r}; this installer reads schema {SCHEMA}", 1)
    return data


def write_manifest(home: Path, data: dict) -> None:
    tmp = home / f".{MANIFEST}.tmp"
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, home / MANIFEST)


def backup(home: Path, paths: list[Path], label: str) -> Path:
    """A tar.gz of `paths` (relative names kept) under <home>/backups/."""
    dest_dir = home / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.tar.gz"
    with tarfile.open(dest, "w:gz") as tar:
        for p in paths:
            tar.add(p, arcname=p.relative_to(home).as_posix())
    return dest


def restore(home: Path, archive: Path) -> list[str]:
    restored = []
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            target = (home / m.name).resolve()
            if not str(target).startswith(str(home) + os.sep) or m.issym() or m.islnk():
                raise Refused(f"{archive} holds an entry outside {home} or a link ({m.name}); not restoring it", 1)
        try:
            tar.extractall(home, members=members, filter="data")  # names checked above; no special files
        except TypeError:  # Python without extraction filters
            tar.extractall(home, members=members)
        restored = sorted({"/".join(m.name.split("/")[:2]) for m in members})
    return restored


# ---- checks ----------------------------------------------------------------------------------------------
def preflight(with_live: bool | None) -> dict:
    report = {"python": sys.version.split()[0], "git": shutil.which("git") is not None, "claude": None,
              "live": False, "live_reason": ""}
    if sys.version_info < (3, 9):
        raise Refused(f"Python 3.9 or newer is needed (found {report['python']})")
    if not report["git"]:
        raise Refused("git was not found on PATH; the factory needs it for worktrees and merges")
    cli = claude_cli()
    if cli:
        v = run([cli, "--version"], timeout=30)
        report["claude"] = (v.stdout or v.stderr).strip().splitlines()[0] if v.returncode == 0 else None
    if with_live is False:
        report["live_reason"] = "skipped (--no-live)"
    elif with_live is True:
        report["live"], report["live_reason"] = True, "forced (--with-live)"
    elif not cli:
        report["live_reason"] = "skipped: no claude CLI to check that this Claude Code build supports mods"
    else:
        res = run([cli, "plugin", "validate", str(COMPONENTS["factory-live"])], timeout=120)
        report["live"] = res.returncode == 0
        if report["live"]:
            report["live_reason"] = "this Claude Code build loads mods"
        else:
            last = ((res.stdout + res.stderr).strip().splitlines() or ["no output"])[-1]
            report["live_reason"] = f"skipped: this Claude Code build does not validate the mod ({last})"
    return report


def other_installs(home: Path) -> list[str]:
    """Marketplace installs of the same plugins would load twice beside the skills-dir copies."""
    found = []
    for name in ("installed_plugins.json", "installed_plugins_v2.json"):
        p = home / "plugins" / name
        try:
            text = p.read_text()
        except OSError:
            continue
        for comp in COMPONENTS:
            if f'"{comp}@' in text:
                found.append(f"{comp} (in {p})")
    return found


def validate_stage(stage: Path, names: list[str], cli: str | None) -> list[str]:
    notes = []
    if "factory" in names:
        doc = run([sys.executable, str(stage / "factory" / "bin" / "factoryctl"), "doctor", "--json"], timeout=60,
                  env={"PYTHONDONTWRITEBYTECODE": "1"})
        if doc.returncode != 0:
            raise Refused(f"the staged factoryctl does not run: {(doc.stderr or doc.stdout).strip()[:400]}")
        notes.append("staged factoryctl runs (doctor)")
    if cli:
        for name in names:
            res = run([cli, "plugin", "validate", str(stage / name)], timeout=120)
            if res.returncode != 0:
                raise Refused(f"claude plugin validate failed for the staged {name}:\n{(res.stdout + res.stderr).strip()[-1500:]}")
            notes.append(f"claude plugin validate {name}: passed")
    else:
        notes.append("claude CLI not found: plugin validation skipped")
    return notes


# ---- install -----------------------------------------------------------------------------------------
def plan_install(home: Path, names: list[str], manifest: dict | None) -> list[dict]:
    steps = []
    for name in names:
        src = COMPONENTS[name]
        target = home / "skills" / name
        files = shipped_files(src)
        new_hashes = {f.as_posix(): sha256(src / f) for f in files}
        owned = (manifest or {}).get("components", {}).get(name)
        if not target.exists():
            action = "install"
            foreign = False
            modified: list[str] = []
        else:
            current = tree_hashes(target)
            foreign = owned is None
            recorded = (owned or {}).get("files", {})
            modified = sorted(k for k, v in current.items() if recorded.get(k) != v) if owned else []
            action = "unchanged" if current == new_hashes else ("replace-foreign" if foreign else "upgrade")
        steps.append({"name": name, "source": str(src), "target": str(target), "action": action, "foreign": foreign,
                      "locally_modified": modified, "files": len(files), "version": plugin_version(src),
                      "hashes": new_hashes})
    return steps


def do_install(args) -> dict:
    home = claude_home(args.config_dir)
    pre = preflight(True if args.with_live else False if args.no_live else None)
    names = ["factory"] + (["factory-live"] if pre["live"] else [])
    manifest = load_manifest(home)
    steps = plan_install(home, names, manifest)
    result = {"home": str(home), "preflight": pre, "steps": [{k: v for k, v in s.items() if k != "hashes"} for s in steps],
              "warnings": [], "dry_run": bool(args.dry_run)}
    dupes = other_installs(home)
    if dupes:
        result["warnings"].append("also installed from a marketplace (would load twice): " + ", ".join(dupes)
                                  + ". Remove one: /plugin uninstall, or scripts/uninstall.sh.")
    if manifest:
        stale = sorted(set(manifest.get("components", {})) - set(names))
        if stale:
            result["warnings"].append(f"previously installed and not part of this install: {', '.join(stale)} "
                                      "(left as is; scripts/uninstall.sh removes everything)")
    conflicts = [s for s in steps if s["action"] == "replace-foreign"]
    if conflicts and not args.replace_foreign:
        raise Refused("these paths exist and were not installed by this installer: "
                      + ", ".join(s["target"] for s in conflicts)
                      + ". Nothing was changed. Re-run with --replace-foreign to back them up and replace them.", 3)
    todo = [s for s in steps if s["action"] != "unchanged"]
    if args.dry_run or not todo:
        result["status"] = "dry-run" if args.dry_run else "up-to-date"
        return result

    skills = home / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".factory-stage-", dir=skills))  # same filesystem: renames are atomic
    moved: list[tuple[Path, Path]] = []
    try:
        for s in todo:
            src, dst = Path(s["source"]), stage / s["name"]
            for rel in shipped_files(src):
                (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / rel, dst / rel)
            if tree_hashes(dst) != s["hashes"]:
                raise Refused(f"the staged copy of {s['name']} does not match its source; nothing was changed", 1)
        result["checks"] = validate_stage(stage, [s["name"] for s in todo], claude_cli())

        # One archive per component, so uninstall restores exactly what each install replaced.
        archives = {s["name"]: backup(home, [Path(s["target"])], f"{s['name']}-preinstall")
                    for s in todo if Path(s["target"]).exists()}
        if archives:
            result["backup"] = ", ".join(str(a) for a in archives.values())
        for s in todo:
            target = Path(s["target"])
            if target.exists():
                aside = stage / f"{s['name']}.previous"
                os.replace(target, aside)
                moved.append((aside, target))
            os.replace(stage / s["name"], target)
        components = dict((manifest or {}).get("components", {}))
        for s in steps:
            prev = components.get(s["name"], {})
            components[s["name"]] = {
                "path": s["target"], "version": s["version"], "files": s["hashes"],
                # The backup that restores what was there before the factory's first install of this component.
                "restore": prev.get("restore") if s["action"] in ("upgrade", "unchanged") else (
                    str(archives[s["name"]]) if s["action"] == "replace-foreign" else None),
            }
        backups = list((manifest or {}).get("backups", [])) + [str(a) for a in archives.values()]
        write_manifest(home, {"schema": SCHEMA, "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                              "source": {"path": str(ROOT), "commit": source_commit()},
                              "components": components, "backups": backups})
        result["status"] = "installed"
    except BaseException:
        for aside, target in reversed(moved):  # roll back whatever was swapped in
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            os.replace(aside, target)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return result


# ---- uninstall ---------------------------------------------------------------------------------------
def do_uninstall(args) -> dict:
    home = claude_home(args.config_dir)
    manifest = load_manifest(home)
    if not manifest:
        return {"home": str(home), "status": "not-installed", "steps": []}
    steps = []
    for name, comp in manifest.get("components", {}).items():
        target = Path(comp["path"])
        if not target.exists():
            steps.append({"name": name, "action": "already-gone", "target": str(target)})
            continue
        if not str(target.resolve()).startswith(str(home / "skills") + os.sep):
            raise Refused(f"the manifest names {target}, which is outside {home / 'skills'}; not removing it", 1)
        current = tree_hashes(target)
        modified = sorted(k for k, v in current.items() if comp.get("files", {}).get(k) != v)
        steps.append({"name": name, "action": "remove", "target": str(target), "locally_modified": modified,
                      "restore": None if args.keep_backup_restore else comp.get("restore")})
    result = {"home": str(home), "steps": steps, "dry_run": bool(args.dry_run)}
    if args.dry_run:
        result["status"] = "dry-run"
        return result
    removing = [Path(s["target"]) for s in steps if s["action"] == "remove"]
    if any(s.get("locally_modified") for s in steps):
        result["backup"] = str(backup(home, removing, "factory-uninstall"))
    for p in removing:
        shutil.rmtree(p)
    restored = []
    for s in steps:
        if s.get("restore") and Path(s["restore"]).exists():
            restored += restore(home, Path(s["restore"]))
    result["restored"] = sorted(set(restored))
    (home / MANIFEST).unlink()
    if args.purge:
        shutil.rmtree(home / "factory", ignore_errors=True)  # the console's recent-projects registry
    result["status"] = "uninstalled"
    return result


def do_status(args) -> dict:
    home = claude_home(args.config_dir)
    manifest = load_manifest(home)
    out = {"home": str(home), "installed": bool(manifest), "components": {}}
    for name, comp in (manifest or {}).get("components", {}).items():
        target = Path(comp["path"])
        current = tree_hashes(target) if target.exists() else {}
        out["components"][name] = {"path": comp["path"], "version": comp.get("version"), "present": target.exists(),
                                   "modified": sorted(k for k, v in current.items() if comp["files"].get(k) != v),
                                   "missing": sorted(set(comp["files"]) - set(current))}
    if manifest:
        out["source"] = manifest.get("source")
        out["installed_at"] = manifest.get("installed_at")
    return out


# ---- output ------------------------------------------------------------------------------------------
def human(cmd: str, r: dict) -> str:
    lines = [f"Claude home: {r['home']}"]
    pre = r.get("preflight")
    if pre:
        lines.append(f"python {pre['python']} · git {'ok' if pre['git'] else 'missing'} · claude {pre['claude'] or 'not found'}")
        lines.append(f"factory-live: {pre['live_reason']}")
    for s in r.get("steps", []):
        extra = f" (locally modified: {', '.join(s['locally_modified'][:5])})" if s.get("locally_modified") else ""
        if cmd == "install":
            lines.append(f"  {s['name']} {s['version']}: {s['action']} → {s['target']} ({s['files']} files){extra}")
        else:
            lines.append(f"  {s['name']}: {s['action']} {s['target']}{extra}")
    for name, c in r.get("components", {}).items():
        state = "ok" if c["present"] and not c["modified"] and not c["missing"] else "changed since install"
        lines.append(f"  {name} {c['version']}: {state} ({c['path']})")
    for n in r.get("checks", []):
        lines.append(f"  check: {n}")
    for w in r.get("warnings", []):
        lines.append(f"  warning: {w}")
    if r.get("backup"):
        lines.append(f"  backup: {r['backup']}")
    if r.get("restored"):
        lines.append(f"  restored: {', '.join(r['restored'])}")
    status = r.get("status") or ("installed" if r.get("installed") else "not installed")
    lines.append(f"Result: {status}")
    if cmd == "install" and status == "installed":
        lines.append("Next: start Claude Code in a repository and run /factory init, then /factory <what you want>.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="install.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("install", "uninstall", "status"):
        p = sub.add_parser(name)
        p.add_argument("--config-dir", help="Claude Code home (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
        p.add_argument("--json", action="store_true")
        if name != "status":
            p.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
    sub.choices["install"].add_argument("--no-live", action="store_true", help="do not install factory-live")
    sub.choices["install"].add_argument("--with-live", action="store_true",
                                        help="install factory-live without checking the local Claude Code build")
    sub.choices["install"].add_argument("--replace-foreign", action="store_true",
                                        help="back up and replace a skills/factory* folder this installer did not write")
    sub.choices["uninstall"].add_argument("--keep-backup-restore", action="store_true",
                                          help="do not restore what the first install replaced")
    sub.choices["uninstall"].add_argument("--purge", action="store_true",
                                          help="also remove <home>/factory (the console's recent-projects list)")
    args = parser.parse_args(argv)
    try:
        result = {"install": do_install, "uninstall": do_uninstall, "status": do_status}[args.cmd](args)
        code = 0
    except Refused as exc:
        result, code = {"status": "refused", "error": str(exc)}, exc.code
    if args.json:
        print(json.dumps(result, indent=2))
    elif code:
        print(f"error: {result['error']}", file=sys.stderr)
    else:
        print(human(args.cmd, result))
    return code


if __name__ == "__main__":
    sys.exit(main())
