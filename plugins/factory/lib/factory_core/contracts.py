"""Validate agent artifacts against the contract registry (header line + required sections)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import registry
from .util import first_line

MAX_ARTIFACT_BYTES = 2_000_000


@dataclass
class ArtifactCheck:
    path: Path
    artifact: str
    exists: bool = False
    header: str = ""
    value: str | None = None  # e.g. "PASS", "READY", "APPROVE"
    tone: str | None = None  # good | warn | stop
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exists and not self.problems


def header_spec(artifact: str) -> dict:
    spec = registry.contracts()["artifacts"].get(artifact)
    if spec is None:
        raise KeyError(artifact)
    return registry.contracts()["headers"][spec["header"]]


def parse_header(line: str) -> tuple[str | None, str | None]:
    """Recognise any known header line; returns (value, tone) or (None, None)."""
    for spec in registry.contracts()["headers"].values():
        m = re.match(spec["pattern"], line)
        if m:
            value = m.group(1)
            for tone in ("good", "warn", "stop"):
                if value in spec[tone]:
                    return value, tone
            return value, None
    return None, None


def _headings(text: str) -> list[str]:
    return [m.group(1).strip().lower() for m in re.finditer(r"^#{1,4}\s+(.+?)\s*#*\s*$", text, re.M)]


def check(path: Path, artifact: str) -> ArtifactCheck:
    result = ArtifactCheck(path=path, artifact=artifact)
    spec = registry.contracts()["artifacts"].get(artifact)
    if spec is None:
        result.problems.append(f"unknown artifact type {artifact!r}")
        return result
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        result.problems.append(f"{path.name} was not written")
        return result
    result.exists = True
    if size > MAX_ARTIFACT_BYTES:
        result.problems.append(f"{path.name} is larger than {MAX_ARTIFACT_BYTES // 1000} kB")
        return result
    text = path.read_text(encoding="utf-8", errors="replace")
    result.header = first_line(text)
    hspec = registry.contracts()["headers"][spec["header"]]
    m = re.match(hspec["pattern"], result.header)
    if not m:
        expected = hspec["pattern"].strip("^$").replace("\\", "")
        result.problems.append(
            f"{path.name}: first line must match {expected!r}, found {result.header[:80]!r}"
        )
    else:
        result.value = m.group(1)
        for tone in ("good", "warn", "stop"):
            if result.value in hspec[tone]:
                result.tone = tone
    # A BLOCKED document only needs to explain the block.
    if result.tone == "stop" and spec["header"] in ("status", "build"):
        if "blocked" not in _headings(text):
            result.problems.append(f"{path.name}: a BLOCKED document needs a '## Blocked' section")
        return result
    headings = _headings(text)
    for section in spec.get("sections", []):
        if not any(section.lower() in h for h in headings):
            result.problems.append(f"{path.name}: missing section '## {section}'")
    return result


def section_text(text: str, name: str) -> str:
    """Body of the first heading containing `name`, up to the next heading of the same or higher level."""
    lines = text.splitlines()
    out: list[str] = []
    level = None
    for line in lines:
        m = re.match(r"^(#{1,4})\s+(.+)$", line)
        if m:
            if level is not None and len(m.group(1)) <= level:
                break
            if level is None and name.lower() in m.group(2).lower():
                level = len(m.group(1))
                continue
        if level is not None:
            out.append(line)
    return "\n".join(out).strip()
