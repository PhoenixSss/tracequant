"""Committed Arm Control Base validator for task-65-round-2-v2.

Mechanically validates the A/B synthetic benchmark-only control-base commit
and the C/D frozen control-base branch model against a pinned manifest and
``BENCHMARK_BASE_SHA``.

A/B gates (Task #125 Control-base Model):

- parent of the control-base commit == ``BENCHMARK_BASE_SHA``;
- the control-base diff contains exactly the manifest-declared runtime
  control-plane paths (no business source changes, no Task #65
  implementation, no benchmark result/evidence, no other-generation files);
- the diff is non-empty (a real synthetic commit exists);
- each projection action is verified mechanically: INSTALL_GENERATION_VERSION
  -> blob/sha256 at the control base equals the manifest entry;
  INHERIT_BUSINESS_BASE -> blob at the control base equals the blob at
  ``BENCHMARK_BASE_SHA``; ENSURE_ABSENT -> path physically absent;
- expected-absent paths are physically absent;
- the control-base worktree is clean (when a branch is provided).

C/D gates:

- control-base tip == ``BENCHMARK_BASE_SHA`` (frozen, no synthetic commit);
- worktree clean (when a branch is provided).

Any failed gate -> HUMAN GATE (the validator never mutates state).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark_common import (
    BenchmarkError,
    gate,
    git_ls_tree_blob,
    git_rev_parse,
    load_json,
    run_git,
    validate_basic,
)
from generation_materializer import PINNED_SCHEMA, ManifestPath, parse_pinned_manifest
from runtime_control_plane import (
    covering_entry_paths,
    is_invalid_control_plane_inherit,
    managed_runtime_control_plane_paths,
)

ARMS_AB = frozenset({"A", "B"})
ARMS_CD = frozenset({"C", "D"})


@dataclass
class ValidationResult:
    gates: list[dict[str, Any]]
    disposition: str


def _diff_name_status(repo_root: Path, base_sha: str, head_sha: str) -> list[str]:
    result = run_git(
        repo_root,
        "diff-tree",
        "-r",
        "--no-commit-id",
        "--name-status",
        base_sha,
        head_sha,
    )
    if result.returncode != 0:
        raise BenchmarkError(f"git diff-tree failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _full_tree_paths(repo_root: Path, commit: str) -> set[str]:
    """All blob paths in the tree of ``commit`` (deterministic, read-only)."""
    result = run_git(repo_root, "ls-tree", "-r", "--name-only", commit)
    if result.returncode != 0:
        raise BenchmarkError(f"git ls-tree failed: {result.stderr.strip()}")
    return {line for line in result.stdout.splitlines() if line.strip()}


def _worktree_clean(repo_root: Path) -> bool:
    result = run_git(repo_root, "status", "--porcelain")
    return result.returncode == 0 and result.stdout.strip() == ""


def validate_ab_from_file(
    manifest_path: Path,
    repo_root: Path,
    benchmark_base_sha: str,
    control_base_sha: str,
    branch: str | None,
) -> ValidationResult:
    raw = load_json(manifest_path)
    validate_basic(raw, PINNED_SCHEMA, "manifest")
    parsed = parse_pinned_manifest(manifest_path)
    return _validate_ab_parsed(
        parsed,
        repo_root,
        benchmark_base_sha,
        control_base_sha,
        branch,
    )


def _validate_ab_parsed(
    manifest: Any,
    repo_root: Path,
    benchmark_base_sha: str,
    control_base_sha: str,
    branch: str | None,
) -> ValidationResult:
    gates: list[dict[str, Any]] = []
    paths: list[ManifestPath] = manifest.paths

    invalid_control_plane_inherit = sorted(
        entry.path
        for entry in paths
        if is_invalid_control_plane_inherit(entry.path, entry.projection_action)
    )
    gates.append(
        gate(
            "generation_control_plane_must_not_inherit",
            "pass" if not invalid_control_plane_inherit else "fail",
            (
                None
                if not invalid_control_plane_inherit
                else "invalid INHERIT_BUSINESS_BASE paths: "
                + ", ".join(invalid_control_plane_inherit)
            ),
        )
    )

    managed_paths = managed_runtime_control_plane_paths(
        repo_root, benchmark_base_sha, manifest.workflow_source_sha
    )
    entries_by_path = {entry.path: entry for entry in paths}
    missing: list[str] = []
    duplicate_coverage: list[str] = []
    invalid_actions: list[str] = []
    for managed_path in sorted(managed_paths):
        covering = covering_entry_paths(entries_by_path, managed_path)
        if not covering:
            missing.append(managed_path)
            continue
        if len(covering) != 1:
            duplicate_coverage.append(f"{managed_path}: {', '.join(covering)}")
            continue
        entry = entries_by_path[covering[0]]
        source_exists = (
            git_ls_tree_blob(repo_root, manifest.workflow_source_sha, managed_path)
            is not None
        )
        if source_exists:
            # A source-existing file needs its own blob-bearing action.  A
            # directory absence sentinel is the one intentional exception:
            # it explicitly removes every source child under that directory.
            directory_absence = (
                entry.path != managed_path
                and entry.projection_action == "ENSURE_ABSENT"
                and not entry.exists_at_source
            )
            if not directory_absence and (
                entry.path != managed_path or entry.projection_action == "ENSURE_ABSENT"
            ):
                invalid_actions.append(
                    f"{managed_path}: source-existing path covered by "
                    f"{entry.path}={entry.projection_action}"
                )
        elif entry.projection_action == "INHERIT_BUSINESS_BASE":
            if managed_path not in manifest.source_absent_inherit_allowlist:
                invalid_actions.append(
                    f"{managed_path}: source-absent INHERIT is not allowlisted"
                )
        elif entry.projection_action != "ENSURE_ABSENT":
            invalid_actions.append(
                f"{managed_path}: source-absent path uses {entry.projection_action}"
            )
    for allowlisted in sorted(manifest.source_absent_inherit_allowlist):
        if allowlisted not in managed_paths:
            invalid_actions.append(
                f"{allowlisted}: source-absent INHERIT allowlist path is "
                "not in the managed universe"
            )
    completeness_failures = missing + duplicate_coverage + invalid_actions
    gates.append(
        gate(
            "runtime_control_plane_projection_complete",
            "pass" if not completeness_failures else "fail",
            (None if not completeness_failures else "; ".join(completeness_failures)),
        )
    )

    try:
        actual_parent = git_rev_parse(repo_root, f"{control_base_sha}^")
    except BenchmarkError as exc:
        return ValidationResult(
            [gate("parent_equals_benchmark_base", "fail", str(exc))], "HUMAN GATE"
        )
    gates.append(
        gate(
            "parent_equals_benchmark_base",
            "pass" if actual_parent == benchmark_base_sha else "fail",
            (
                None
                if actual_parent == benchmark_base_sha
                else f"expected {benchmark_base_sha}, observed {actual_parent}"
            ),
        )
    )

    diff_lines = _diff_name_status(repo_root, benchmark_base_sha, control_base_sha)
    if not diff_lines:
        gates.append(
            gate(
                "synthetic_commit_present",
                "fail",
                "diff between base and control base is empty",
            )
        )
    else:
        gates.append(gate("synthetic_commit_present", "pass"))

    changed_action_paths = {
        entry.path
        for entry in paths
        if entry.projection_action in {"INSTALL_GENERATION_VERSION", "ENSURE_ABSENT"}
    }
    absent_prefixes = [
        entry.path for entry in paths if entry.projection_action == "ENSURE_ABSENT"
    ]
    changed = {line.split("\t")[-1] for line in diff_lines}

    def _is_managed(changed_path: str) -> bool:
        if changed_path in changed_action_paths:
            return True
        return any(changed_path.startswith(prefix + "/") for prefix in absent_prefixes)

    unexpected = sorted(path for path in changed if not _is_managed(path))
    gates.append(
        gate(
            "diff_within_runtime_control_plane_paths",
            "pass" if not unexpected else "fail",
            (None if not unexpected else f"unexpected changed paths: {unexpected}"),
        )
    )

    head_tree = _full_tree_paths(repo_root, control_base_sha)

    def _present(path: str, tree: set[str]) -> bool:
        return path in tree or any(item.startswith(path + "/") for item in tree)

    projection_failures: list[str] = []
    for entry in paths:
        if entry.projection_action == "INSTALL_GENERATION_VERSION":
            head_entry = git_ls_tree_blob(repo_root, control_base_sha, entry.path)
            if head_entry is None or head_entry[1] != entry.blob_id:
                projection_failures.append(
                    f"{entry.path}: expected installed blob {entry.blob_id}"
                )
        elif entry.projection_action == "INHERIT_BUSINESS_BASE":
            base_entry = git_ls_tree_blob(repo_root, benchmark_base_sha, entry.path)
            head_entry = git_ls_tree_blob(repo_root, control_base_sha, entry.path)
            if (
                base_entry is None
                or head_entry is None
                or base_entry[1] != head_entry[1]
            ):
                projection_failures.append(
                    f"{entry.path}: expected inherited blob at {benchmark_base_sha}"
                )
        elif entry.projection_action == "ENSURE_ABSENT":
            if _present(entry.path, head_tree):
                projection_failures.append(
                    f"{entry.path}: expected absent, found in control-base tree"
                )
    gates.append(
        gate(
            "projection_actions_mechanically_verified",
            "pass" if not projection_failures else "fail",
            None if not projection_failures else "; ".join(projection_failures),
        )
    )

    absent_violations = [path for path in absent_prefixes if _present(path, head_tree)]
    gates.append(
        gate(
            "expected_absent_paths_absent",
            "pass" if not absent_violations else "fail",
            None if not absent_violations else f"present: {absent_violations}",
        )
    )

    if branch is not None:
        gates.append(
            gate(
                "control_base_worktree_clean",
                "pass" if _worktree_clean(repo_root) else "fail",
            )
        )

    failed = [g for g in gates if g["status"] == "fail"]
    disposition = "HUMAN GATE" if failed else "pass"
    return ValidationResult(gates, disposition)


def validate_cd(
    repo_root: Path,
    benchmark_base_sha: str,
    control_base_sha: str,
    branch: str | None,
) -> ValidationResult:
    gates: list[dict[str, Any]] = []
    actual_tip = git_rev_parse(repo_root, control_base_sha)
    gates.append(
        gate(
            "cd_tip_equals_benchmark_base",
            "pass" if actual_tip == benchmark_base_sha else "fail",
            (
                None
                if actual_tip == benchmark_base_sha
                else f"expected {benchmark_base_sha}, observed {actual_tip}"
            ),
        )
    )
    if branch is not None:
        gates.append(
            gate(
                "control_base_worktree_clean",
                "pass" if _worktree_clean(repo_root) else "fail",
            )
        )
    failed = [g for g in gates if g["status"] == "fail"]
    return ValidationResult(gates, "HUMAN GATE" if failed else "pass")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a committed Arm Control Base for task-65-round-2-v2."
    )
    parser.add_argument("--arm", required=True, choices=["A", "B", "C", "D"])
    parser.add_argument("--manifest", help="pinned manifest (required for A/B)")
    parser.add_argument("--benchmark-base-sha", required=True)
    parser.add_argument("--control-base-sha", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--branch", help="branch whose worktree must be clean")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    try:
        if args.arm in ARMS_AB:
            if not args.manifest:
                raise BenchmarkError("--manifest is required for arms A/B")
            result = validate_ab_from_file(
                Path(args.manifest),
                repo_root,
                args.benchmark_base_sha,
                args.control_base_sha,
                args.branch,
            )
        else:
            result = validate_cd(
                repo_root, args.benchmark_base_sha, args.control_base_sha, args.branch
            )
    except BenchmarkError as exc:
        print(f"CONTROL BASE VALIDATOR FAIL CLOSED: {exc}", file=sys.stderr)
        return 1

    output = {
        "protocol_identity": "task-65-round-2-v2",
        "arm": args.arm,
        "benchmark_base_sha": args.benchmark_base_sha,
        "control_base_sha": args.control_base_sha,
        "disposition": result.disposition,
        "gates": result.gates,
    }
    print(json.dumps(output, sort_keys=True))
    return 0 if result.disposition == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
