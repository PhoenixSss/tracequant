#!/usr/bin/env python3
"""Audit canonical LCK Skills and their mirrored provider-specific Skills.

Canonical procedures live only under ``.agents/skills``. Each Claude provider
Skill under ``.claude/skills/<name>`` is a self-contained mirror of the whole
canonical Skill package (``SKILL.md`` plus ``references/*.md``) with exactly one
declared provider-specific difference:

- subtractive Skills omit the Codex-only ``## Execution route contract`` section
  from their ``SKILL.md``;
- the additive Skill appends one Claude-only ``## Execution model`` section.

The comparison is package-to-package: a provider package that misses a canonical
file, carries an undeclared extra file, rewrites a mirrored file, or leaves a
relative link dangling inside the package fails the audit. Provider execution
mechanics never appear in the shared policy layer, so a Claude Skill must not
contain any provider vocabulary.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
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
SKILL_FILE: Final = "SKILL.md"
ROUTE_SECTION: Final = "## Execution route contract"
CLAUDE_SECTION: Final = "## Execution model"
CANONICAL_ROOT: Final = Path(".agents/skills")
PROVIDER_ROOT: Final = Path(".claude/skills")
CANONICAL_PREFIX: Final = CANONICAL_ROOT.as_posix()
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
_MARKDOWN_LINK: Final = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_EXTERNAL_LINK_PREFIXES: Final = ("#", "http://", "https://", "mailto:")


class SkillMirrorError(ValueError):
    """A provider Skill is not the declared mirror of its canonical package."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_skill_package(skill_root: Path) -> dict[str, str]:
    """Return one Skill package as ``{relative posix path: text}``."""

    return {
        path.relative_to(skill_root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(skill_root.rglob("*.md"))
    }


def package_text(package: Mapping[str, str]) -> str:
    """Concatenate a package deterministically, ordered by relative path."""

    return "".join(package[path] for path in sorted(package))


def package_sha256(package: Mapping[str, str]) -> str:
    return _sha256(package_text(package))


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


def dangling_links(path: Path, repo_root: Path) -> list[str]:
    """Return the relative link targets of ``path`` that resolve nowhere.

    A provider Skill is a self-contained copy, so a relative link must resolve
    inside its own package or against the repository root; anything else would
    send a Claude session to a path that only exists in the canonical tree.
    """

    unresolved: list[str] = []
    for target in _MARKDOWN_LINK.findall(path.read_text(encoding="utf-8")):
        if target.startswith(_EXTERNAL_LINK_PREFIXES):
            continue
        if (path.parent / target).exists() or (repo_root / target).exists():
            continue
        unresolved.append(target)
    return unresolved


def canonical_package(repo_root: Path, name: str) -> dict[str, str]:
    return read_skill_package(repo_root / CANONICAL_ROOT / name)


def provider_package(repo_root: Path, name: str) -> dict[str, str]:
    return read_skill_package(repo_root / PROVIDER_ROOT / name)


def expected_provider_package(repo_root: Path, name: str) -> tuple[dict[str, str], str]:
    """Return ``(expected package, declared_difference)`` for one provider Skill."""

    package = canonical_package(repo_root, name)
    if name in SUBTRACTIVE_SKILLS:
        return {
            relative: (strip_route_section(text) if relative == SKILL_FILE else text)
            for relative, text in package.items()
        }, ROUTE_SECTION
    if name in ADDITIVE_SKILLS:
        expected = dict(package)
        expected[SKILL_FILE] = f"{expected[SKILL_FILE]}\n{CLAUDE_MODEL_SECTION}"
        return expected, CLAUDE_SECTION
    raise SkillMirrorError(f"unknown provider Skill mode for {name}")


def _provider_scan_text(
    package: Mapping[str, str], canonical: Mapping[str, str], mode: str
) -> str:
    """Return the provider content that must stay free of provider vocabulary.

    The declared Claude-only section is the single place allowed to name the
    Codex execution model, because it exists to deny it.
    """

    parts: list[str] = []
    for relative in sorted(package):
        text = package[relative]
        if mode == "additive" and relative in canonical:
            text = text[: len(canonical[relative])]
        parts.append(text)
    return "".join(parts)


def audit(repo_root: Path) -> tuple[dict[str, object], int]:
    canonical_results: dict[str, object] = {}
    adapter_results: dict[str, object] = {}
    violations: list[str] = []

    for name in SKILLS:
        canonical_relative = CANONICAL_ROOT / name
        adapter_relative = PROVIDER_ROOT / name
        canonical_pkg = canonical_package(repo_root, name)
        if SKILL_FILE not in canonical_pkg:
            violations.append(
                f"missing canonical Skill {(canonical_relative / SKILL_FILE).as_posix()}"
            )
            continue
        adapter_pkg = provider_package(repo_root, name)
        if SKILL_FILE not in adapter_pkg:
            violations.append(
                f"missing provider Skill {(adapter_relative / SKILL_FILE).as_posix()}"
            )
            continue

        canonical_text = package_text(canonical_pkg)
        missing = [token for token in REQUIRED[name] if token not in canonical_text]
        forbidden = [token for token in FORBIDDEN if token in canonical_text]
        if "uv run --frozen python -m tools.lck" not in canonical_text:
            missing.append("uv run --frozen python -m tools.lck")
        if missing:
            violations.append(
                f"{canonical_relative.as_posix()}: missing {', '.join(missing)}"
            )
        if forbidden:
            violations.append(
                f"{canonical_relative.as_posix()}: forbidden {', '.join(forbidden)}"
            )

        mode = "subtractive" if name in SUBTRACTIVE_SKILLS else "additive"
        try:
            expected, difference = expected_provider_package(repo_root, name)
        except SkillMirrorError as exc:
            violations.append(f"{canonical_relative.as_posix()}: {exc}")
            continue

        missing_files = sorted(set(expected) - set(adapter_pkg))
        extra_files = sorted(set(adapter_pkg) - set(expected))
        differing_files = sorted(
            relative
            for relative in set(expected) & set(adapter_pkg)
            if expected[relative] != adapter_pkg[relative]
        )
        mirrored = not missing_files and not extra_files
        exact = mirrored and not differing_files
        if not exact:
            differences = [
                f"missing {', '.join(missing_files)}" if missing_files else "",
                f"undeclared {', '.join(extra_files)}" if extra_files else "",
                f"differs {', '.join(differing_files)}" if differing_files else "",
            ]
            violations.append(
                f"{adapter_relative.as_posix()}: not a mirror of its canonical package "
                f"(declared difference: {difference}; "
                f"{'; '.join(part for part in differences if part)})"
            )

        dangling = {
            relative: unresolved
            for relative in sorted(adapter_pkg)
            if (
                unresolved := dangling_links(
                    repo_root / adapter_relative / relative, repo_root
                )
            )
        }
        if dangling:
            targets = "; ".join(
                f"{relative} -> {target}"
                for relative, unresolved in dangling.items()
                for target in unresolved
            )
            violations.append(
                f"{adapter_relative.as_posix()}: dangling provider-package links "
                f"({targets})"
            )

        scan_text = _provider_scan_text(adapter_pkg, canonical_pkg, mode)
        leaked = [token for token in PROVIDER_VOCABULARY if token in scan_text]
        if CANONICAL_PREFIX in package_text(adapter_pkg):
            leaked.append(CANONICAL_PREFIX)
        if leaked:
            violations.append(
                f"{adapter_relative.as_posix()}: provider execution mechanics leaked "
                f"({', '.join(leaked)})"
            )

        canonical_results[name] = {
            "path": canonical_relative.as_posix(),
            "files": sorted(canonical_pkg),
            "package_sha256": package_sha256(canonical_pkg),
            "missing_contract_tokens": missing,
            "forbidden_paths": forbidden,
        }
        adapter_results[name] = {
            "path": adapter_relative.as_posix(),
            "files": sorted(adapter_pkg),
            "package_sha256": package_sha256(adapter_pkg),
            "canonical_package_sha256": package_sha256(canonical_pkg),
            "mode": mode,
            "declared_difference": difference,
            "mirrored": mirrored,
            "exact": exact,
            "missing_files": missing_files,
            "extra_files": extra_files,
            "differing_files": differing_files,
            "dangling_links": dangling,
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
