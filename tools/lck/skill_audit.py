#!/usr/bin/env python3
"""Audit canonical LCK Skills and their mirrored provider-specific Skills.

Canonical procedures live only under ``.agents/skills``. The Claude provider
Skills under ``.claude/skills`` are self-contained mirrors of the canonical
Skill package (``SKILL.md`` plus ``references/*.md``) with exactly one declared
provider-specific difference:

- subtractive Skills omit the Codex-only ``## Execution route contract`` section;
- the additive Skill appends one Claude-only ``## Execution model`` section.

Provider execution mechanics never appear in the shared policy layer, so a
Claude Skill must not contain any provider vocabulary.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Final

SCHEMA_VERSION: Final = 10
SKILLS: Final = (
    "task-delivery-runner",
    "task-pr-review-runner",
    "task-closeout",
    "feature-completion-audit",
)
SUBTRACTIVE_SKILLS: Final = (
    "task-delivery-runner",
    "task-pr-review-runner",
    "task-closeout",
)
ADDITIVE_SKILLS: Final = ("feature-completion-audit",)
ROUTE_SECTION: Final = "## Execution route contract"
CLAUDE_SECTION: Final = "## Execution model"
CANONICAL_PREFIX: Final = ".agents/skills"
# The single declared Claude-only addition. It is the one place allowed to name
# the Codex execution model, because it exists to state that it does not apply.
CLAUDE_MODEL_SECTION: Final = """## Execution model

Claude Code executes commands directly in the user's shell environment — there is
no sandbox isolation layer. Git, `gh`, Python, subprocess, network, and filesystem
access all work natively. The Codex Guardian sandbox/elevated routing model does
not apply to this Skill's procedures.

Command permissions are governed by `.claude/settings.json`, not by `.codex/rules/`.
Runner commands can fail, but not because of sandbox restrictions; read the Runner's
own output to classify the result, and never retry a real command failure with
broader permissions or fall back to an equivalent direct command chain.
"""
PROVIDER_VOCABULARY: Final = (
    "elevated-first",
    "sandbox-first",
    "sandbox",
    "30-second",
    "still-running poll",
    "execution-profile",
    # Covers route/routed/rerouted/routes. A dangling reference to the Codex-only
    # route contract is the same class of leak as a route label.
    "route",
)
REQUIRED: Final = {
    "task-delivery-runner": (
        "delivery prepare",
        "delivery complete",
        "refresh <TASK>",
    ),
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
_BLANK_RUN: Final = re.compile(r"\n{3,}")


class SkillMirrorError(ValueError):
    """A provider Skill is not the declared mirror of its canonical package."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_skill_package(skill_root: Path) -> str:
    """Return the concatenated canonical package for one Skill."""

    return "".join(
        path.read_text(encoding="utf-8") for path in sorted(skill_root.rglob("*.md"))
    )


def strip_route_section(text: str) -> str:
    """Remove the single Codex-only ``## Execution route contract`` section."""

    lines = text.splitlines(keepends=True)
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.rstrip("\n") == ROUTE_SECTION
        ),
        None,
    )
    if start is None:
        raise SkillMirrorError(f"missing {ROUTE_SECTION} section")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    del lines[start:end]
    return _BLANK_RUN.sub("\n\n", "".join(lines)).rstrip("\n") + "\n"


def canonical_package(repo_root: Path, name: str) -> str:
    return read_skill_package(repo_root / ".agents" / "skills" / name)


def provider_skill_text(repo_root: Path, name: str) -> str:
    relative = Path(".claude/skills") / name / "SKILL.md"
    return (repo_root / relative).read_text(encoding="utf-8")


def expected_provider_skill(repo_root: Path, name: str) -> tuple[str, str]:
    """Return ``(expected_text, declared_difference)`` for one provider Skill."""

    package = canonical_package(repo_root, name)
    if name in SUBTRACTIVE_SKILLS:
        return strip_route_section(package), ROUTE_SECTION
    if name in ADDITIVE_SKILLS:
        return package + "\n" + CLAUDE_MODEL_SECTION, CLAUDE_SECTION
    raise SkillMirrorError(f"unknown provider Skill mode for {name}")


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
            violations.append(f"missing provider Skill {adapter_relative}")
            continue

        package = canonical_package(repo_root, name)
        adapter = provider_skill_text(repo_root, name)
        canonical = canonical_path.read_text(encoding="utf-8")
        missing = [token for token in REQUIRED[name] if token not in canonical]
        forbidden = [token for token in FORBIDDEN if token in canonical]
        if "uv run --frozen python -m tools.lck" not in canonical:
            missing.append("uv run --frozen python -m tools.lck")
        if missing:
            violations.append(f"{canonical_relative}: missing {', '.join(missing)}")
        if forbidden:
            violations.append(f"{canonical_relative}: forbidden {', '.join(forbidden)}")

        mode = "subtractive" if name in SUBTRACTIVE_SKILLS else "additive"
        try:
            expected, difference = expected_provider_skill(repo_root, name)
        except SkillMirrorError as exc:
            violations.append(f"{canonical_relative.as_posix()}: {exc}")
            continue
        mirrored = adapter == expected
        if not mirrored:
            violations.append(
                f"{adapter_relative}: not a mirror of its canonical package "
                f"(declared difference: {difference})"
            )

        # The declared Claude-only section is the single place allowed to name
        # the Codex execution model, because it exists to deny it. Scan the
        # mirrored canonical content only.
        scan_text = adapter if mode == "subtractive" else adapter[: len(package)]
        leaked = [token for token in PROVIDER_VOCABULARY if token in scan_text]
        if CANONICAL_PREFIX in adapter:
            leaked.append(CANONICAL_PREFIX)
        if leaked:
            violations.append(
                f"{adapter_relative}: provider execution mechanics leaked "
                f"({', '.join(leaked)})"
            )

        canonical_results[name] = {
            "path": canonical_relative.as_posix(),
            "package_sha256": _sha256(package),
            "missing_contract_tokens": missing,
            "forbidden_paths": forbidden,
        }
        adapter_results[name] = {
            "path": adapter_relative.as_posix(),
            "sha256": _sha256(adapter),
            "canonical_package_sha256": _sha256(package),
            "mode": mode,
            "declared_difference": difference,
            "mirrored": mirrored,
            "provider_vocabulary": leaked,
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
