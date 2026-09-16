"""Bounded, read-only identity for canonical workflow instruction packages.

Inventories bind instruction bytes; they never grant lifecycle authority or ask
the agent to load all routes. Shared policy documents are hashed as whole owner
files, not recursively treated as a reading list.
"""

from __future__ import annotations

import hashlib
import json
import re
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any, Final

SKILLS: Final = (
    "task-delivery-runner",
    "task-pr-review-runner",
    "task-closeout",
    "feature-completion-audit",
)
SHARED_FILES: Final = frozenset(
    {
        "AGENTS.md",
        ".agents/policies/command-execution.md",
        ".agents/policies/context-retrieval.md",
        ".agents/policies/workflow-evidence.md",
        "docs/workflows/lck/lifecycle.md",
        "docs/workflows/lck/review-and-remediation.md",
    }
)
MAX_FILES: Final = 32
MAX_FILE_BYTES: Final = 256 * 1024
MAX_PACKAGE_BYTES: Final = 1024 * 1024
SHA256: Final = re.compile(r"[0-9a-f]{64}")
# Plain paths, inline links and reference-style links share the same inventory
# semantics. A different Markdown presentation must not hide a normative file.
REFERENCES: Final = re.compile(r"[^\s`\[\]()<>\"']+\.md\b")
MARKDOWN_LINKS: Final = re.compile(
    r"\]\(([^\s)]+)|^\s*\[[^\]]+\]:\s*(\S+)", re.MULTILINE
)


class SkillPackageError(ValueError):
    """Missing, malformed, unbounded or inconsistent instruction identity."""


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise SkillPackageError("instruction path must be repo-relative POSIX text")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(char in value for char in "%:#?\x00")
    ):
        raise SkillPackageError(f"unsafe instruction path: {value!r}")
    return value


def _read(repo_root: Path, relative: str) -> bytes:
    relative = _path(relative)
    root = repo_root.resolve()
    current = root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise SkillPackageError(f"symlink instruction path: {relative}")
    if not current.is_relative_to(root) or not current.is_file():
        raise SkillPackageError(f"missing instruction file: {relative}")
    with current.open("rb") as stream:
        payload = stream.read(MAX_FILE_BYTES + 1)
    if len(payload) > MAX_FILE_BYTES:
        raise SkillPackageError(f"instruction file exceeds byte limit: {relative}")
    return payload


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SkillPackageError(f"duplicate inventory key: {key}")
        result[key] = value
    return result


def instruction_references(text: str, source: str) -> set[str]:
    """Resolve local normative references regardless of Markdown presentation."""
    found: set[str] = set()
    targets = {match.group() for match in REFERENCES.finditer(text)}
    targets.update(m.group(1) or m.group(2) for m in MARKDOWN_LINKS.finditer(text))
    for value in targets:
        target = _path(value.split("#", 1)[0])
        if target not in SHARED_FILES and not target.startswith(
            (".agents/", ".claude/", "docs/")
        ):
            target = (PurePosixPath(source).parent / target).as_posix()
        found.add(target)
    return found


def resolve_skill_package(
    repo_root: Path,
    skill_path: str,
    *,
    route: str | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify the full package and optionally select one context-loading route.

    ``expected_sha256`` verifies evidence continuity, never lifecycle freshness.
    The complete inventory is hashed even when only one route is selected.
    """
    skill_path = _path(skill_path)
    parts = PurePosixPath(skill_path).parts
    if (
        len(parts) != 4
        or parts[0] not in {".agents", ".claude"}
        or parts[1] != "skills"
        or parts[2] not in SKILLS
        or parts[3] != "SKILL.md"
    ):
        raise SkillPackageError("unsupported Skill entrypoint")
    name = parts[2]
    prefix = f".agents/skills/{name}/"
    canonical = prefix + "SKILL.md"
    manifest_path = prefix + "package.json"
    manifest_bytes = _read(repo_root, manifest_path)
    try:
        manifest = json.loads(manifest_bytes, object_pairs_hook=_object)
    except (ValueError, UnicodeError) as exc:
        raise SkillPackageError(f"invalid instruction inventory: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema_version", "entrypoint", "files", "routes"}
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or manifest["entrypoint"] != canonical
    ):
        raise SkillPackageError("invalid instruction inventory schema/entrypoint")
    files = manifest["files"]
    routes = manifest["routes"]
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise SkillPackageError("instruction inventory file count is invalid")
    if canonical not in files or not isinstance(routes, dict) or not routes:
        raise SkillPackageError("instruction inventory lacks root/routes")
    if len(routes) > 8:
        raise SkillPackageError("too many instruction routes")

    texts: dict[str, str] = {}
    total = len(manifest_bytes)
    inventory: dict[str, str] = {}
    for relative, expected in sorted(files.items()):
        relative = _path(relative)
        if relative not in SHARED_FILES and not (
            relative.startswith(prefix) and relative.endswith(".md")
        ):
            raise SkillPackageError(f"non-normative inventory file: {relative}")
        if not isinstance(expected, str) or not SHA256.fullmatch(expected):
            raise SkillPackageError(f"invalid instruction digest: {relative}")
        payload = _read(repo_root, relative)
        total += len(payload)
        if total > MAX_PACKAGE_BYTES:
            raise SkillPackageError("instruction package exceeds byte limit")
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            raise SkillPackageError(f"instruction digest mismatch: {relative}")
        inventory[relative] = actual
        try:
            texts[relative] = payload.decode("utf-8")
        except UnicodeError as exc:
            raise SkillPackageError(f"non-UTF8 instruction: {relative}") from exc

    # Only Skill procedure links select instructions. Shared owners may contain
    # contextual bibliography and other workflow routes; hashing them binds that
    # content without eagerly traversing every related business document.
    edges: dict[str, set[str]] = {}
    for relative, text in texts.items():
        if relative.startswith(prefix):
            edges[relative] = instruction_references(text, relative)
    referenced: set[str] = set()
    pending = [canonical]
    while pending:
        relative = pending.pop()
        if relative not in referenced:
            referenced.add(relative)
            pending.extend(edges.get(relative, set()))
    if referenced != set(files):
        raise SkillPackageError("undeclared reference or unreferenced inventory file")
    local_files = {
        p.relative_to(repo_root).as_posix()
        for p in islice((repo_root / prefix).rglob("*.md"), MAX_FILES + 1)
    }
    if local_files != {p for p in files if p.startswith(prefix)}:
        raise SkillPackageError("undeclared supporting instruction file")

    supporting: set[str] = set()
    for key, selected in routes.items():
        if not re.fullmatch(r"[a-z0-9-]{1,40}", key) or not isinstance(selected, list):
            raise SkillPackageError("invalid instruction route")
        if len(selected) > MAX_FILES or any(not isinstance(p, str) for p in selected):
            raise SkillPackageError("invalid instruction route files")
        if len(selected) != len(set(selected)):
            raise SkillPackageError("duplicate route instruction")
        for relative in selected:
            if (
                relative not in files
                or not relative.startswith(prefix)
                or relative == canonical
            ):
                raise SkillPackageError(
                    "route must select declared supporting instructions"
                )
            supporting.add(relative)
            if any(
                p.startswith(prefix) and p != canonical and p not in selected
                for p in edges[relative]
            ):
                raise SkillPackageError(
                    "route omits a referenced supporting instruction"
                )
    if supporting != {p for p in files if p.startswith(prefix) and p != canonical}:
        raise SkillPackageError("supporting instructions must have a route")
    if route is not None and route not in routes:
        raise SkillPackageError(f"unknown instruction route: {route}")

    inventory[manifest_path] = hashlib.sha256(manifest_bytes).hexdigest()
    canonical_digest = _digest(inventory)
    if skill_path != canonical:
        adapter = _read(repo_root, skill_path)
        try:
            adapter_text = adapter.decode("utf-8")
        except UnicodeError as exc:
            raise SkillPackageError("non-UTF8 adapter") from exc
        if (
            instruction_references(adapter_text, skill_path) != {canonical}
            or f"Follow `{canonical}` as the canonical procedure." not in adapter_text
            or "```" in adapter_text
            or any(token in adapter_text for token in ("git ", "gh ", "uv "))
        ):
            raise SkillPackageError("adapter cannot resolve a thin canonical chain")
        inventory[skill_path] = hashlib.sha256(adapter).hexdigest()
        permissions = ".claude/settings.json"
        permission_bytes = _read(repo_root, permissions)
        total += len(adapter) + len(permission_bytes)
        inventory[permissions] = hashlib.sha256(permission_bytes).hexdigest()
    if total > MAX_PACKAGE_BYTES:
        raise SkillPackageError("effective instruction package exceeds byte limit")
    package_digest = _digest(inventory)
    if expected_sha256 is not None and package_digest != expected_sha256:
        raise SkillPackageError("effective instruction package digest mismatch")
    return {
        "path": skill_path,
        "sha256": inventory[skill_path],
        "canonical_path": canonical,
        "canonical_package_sha256": canonical_digest,
        "package_sha256": package_digest,
        "inventory": dict(sorted(inventory.items())),
        "routes": routes,
        "selected_instructions": [skill_path]
        + ([canonical] if skill_path != canonical else [])
        + (routes[route] if route is not None else []),
        "route": route,
    }
