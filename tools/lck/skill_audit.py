#!/usr/bin/env python3
"""Audit canonical LCK Skills and their thin agent-specific adapters."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Final

from .skill_package import SKILLS as SKILLS
from .skill_package import SkillPackageError, resolve_skill_package

SCHEMA_VERSION: Final = 8
REQUIRED: Final = {
    "task-delivery-runner": ("delivery prepare", "delivery complete"),
    "task-pr-review-runner": ("review prepare", "review complete"),
    "task-closeout": ("merge preflight", "closeout"),
    "feature-completion-audit": ("feature-audit-snapshot", "feature-audit-recheck"),
}
SHARED_DOCS: Final = (
    Path("docs/workflows/lck/lifecycle.md"),
    Path("docs/workflows/lck/review-and-remediation.md"),
)
FORBIDDEN: Final = (
    "tools/agent_workflow",
    "git commit",
    "git push",
    "gh pr create",
    "gh pr merge",
    "write_actions_allowed",
    "snapshot_id",
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit(repo_root: Path) -> tuple[dict[str, object], int]:
    canonical_results: dict[str, object] = {}
    adapter_results: dict[str, object] = {}
    violations: list[str] = []

    for name in SKILLS:
        canonical_relative = Path(".agents/skills") / name / "SKILL.md"
        adapter_relative = Path(".claude/skills") / name / "SKILL.md"
        canonical_path = repo_root / canonical_relative
        adapter_path = repo_root / adapter_relative
        if not canonical_path.is_file():
            violations.append(f"missing canonical Skill {canonical_relative}")
            continue
        if not adapter_path.is_file():
            violations.append(f"missing adapter Skill {adapter_relative}")
            continue

        try:
            canonical_identity = resolve_skill_package(
                repo_root, canonical_relative.as_posix()
            )
            adapter_identity = resolve_skill_package(
                repo_root, adapter_relative.as_posix()
            )
        except (SkillPackageError, OSError) as exc:
            violations.append(f"{name}: {exc}")
            continue
        canonical = "\n".join(
            (repo_root / path).read_text(encoding="utf-8")
            for path in canonical_identity["inventory"]
            if path.startswith(f".agents/skills/{name}/") and path.endswith(".md")
        )
        adapter = adapter_path.read_text(encoding="utf-8")
        missing = [token for token in REQUIRED[name] if token not in canonical]
        forbidden = [token for token in FORBIDDEN if token in canonical]
        if "uv run --frozen python -m tools.lck" not in canonical:
            missing.append("uv run --frozen python -m tools.lck")
        if missing:
            violations.append(f"{canonical_relative}: missing {', '.join(missing)}")
        if forbidden:
            violations.append(f"{canonical_relative}: forbidden {', '.join(forbidden)}")

        canonical_ref = canonical_relative.as_posix()
        adapter_ok = (
            adapter_identity["canonical_package_sha256"]
            == canonical_identity["package_sha256"]
        )
        if not adapter_ok:
            violations.append(f"{adapter_relative}: not a thin canonical adapter")

        canonical_results[name] = {
            "path": canonical_ref,
            "sha256": canonical_identity["sha256"],
            "instruction_package": canonical_identity,
            "missing_contract_tokens": missing,
            "forbidden_paths": forbidden,
        }
        adapter_results[name] = {
            "path": adapter_relative.as_posix(),
            "sha256": _sha256(adapter),
            "instruction_package": adapter_identity,
            "canonical_reference": canonical_ref,
            "thin": adapter_ok,
        }

    shared_docs = {
        path.as_posix(): (repo_root / path).is_file() for path in SHARED_DOCS
    }
    for path, present in shared_docs.items():
        if not present:
            violations.append(f"missing shared doc {path}")

    output: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if not violations else "fail",
        "canonical_skills": canonical_results,
        "adapters": adapter_results,
        "shared_docs": shared_docs,
        "violations": violations,
    }
    return output, 0 if not violations else 1


def main() -> int:
    output, returncode = audit(Path.cwd().resolve())
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
