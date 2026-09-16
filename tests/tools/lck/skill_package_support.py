"""Disposable instruction packages for validation subprocess tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


def copy_instruction_packages(destination: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    paths = {".claude/settings.json"}
    for manifest_path in (root / ".agents/skills").glob("*/package.json"):
        manifest = json.loads(manifest_path.read_text())
        paths.update(manifest["files"])
        paths.add(manifest_path.relative_to(root).as_posix())
        paths.add(manifest["entrypoint"].replace(".agents/", ".claude/", 1))
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
