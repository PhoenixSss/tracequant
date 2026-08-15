"""Run-lock program for the task-65-round-2-v2 benchmark (#86 freeze).

At #86 freeze time the run-lock program converts a CURRENT GENERATION
TEMPLATE MANIFEST (C or D) plus the freshly locked ``BENCHMARK_BASE_SHA`` into
a concrete ``RUN-LOCKED MANIFEST``: full 40-char SHAs, complete source paths,
blob IDs, sha256 digests, and role classification resolved from the actual
tree at ``BENCHMARK_BASE_SHA``.

C and D run-locked manifests are generated independently from their own
templates, then compared by ``file_identity_report``.  Formal comparison
validity is decided by the mandatory shared identities alone —
BUSINESS_SNAPSHOT_ID (``benchmark_base_sha``), TASK_SPEC_ID
(``protocol_identity``), EVALUATION_ID (optional ``evaluation_id``,
assigned via ``--evaluation-id`` at freeze).  Any shared identity mismatch
-> HUMAN GATE; the formal C vs D comparison must not continue.

Control-plane file identity (complete generation source-path set, per-path
blob ID, per-path sha256, file mode) is DIAGNOSTIC / PROVENANCE: differences
are recorded by the report but never alone gate the formal C vs D
comparison.  Allowed identity differences remain limited to agent identity /
discovery adapter / invocation identity / permission-runtime environment
identity / session-evidence identity; no per-agent-pruned closure, no
symlinks, no shared runtime files.

Run-locked manifests are #86 evidence (conductor-local archive); this Task
does not pre-generate formal run-locked manifests.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmark_common import (
    BenchmarkError,
    generation_identity_digest,
    git_cat_blob,
    git_ls_tree_blob,
    git_rev_parse,
    load_json,
    run_git,
    sha256_bytes,
    validate_basic,
)
from generation_materializer import RUN_LOCKED_SCHEMA
from runtime_control_plane import (
    is_invalid_control_plane_inherit,
    runtime_control_plane_paths,
)

TEMPLATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema_version",
        "protocol_identity",
        "kind",
        "generation_id",
        "agent_identity",
        "source_selector",
        "closure_derivation_rules",
        "expected_workflow_path_classes",
        "role_classification_rules",
        "materializer_contract",
        "validation_contract",
        "invocation_contract",
        "permission_discovery_identity",
        "file_identity_rule",
    ],
    "properties": {
        "schema_version": {"type": "integer"},
        "protocol_identity": {"type": "string"},
        "kind": {"type": "string", "enum": ["template"]},
        "generation_id": {"type": "string", "enum": ["C", "D"]},
        "agent_identity": {"type": "object"},
        "source_selector": {
            "type": "object",
            "required": ["kind", "ref"],
            "properties": {
                "kind": {"type": "string", "enum": ["fixed-commit"]},
                "ref": {"type": "string"},
            },
        },
        "closure_derivation_rules": {
            "type": "object",
            "required": ["path_classes"],
            "properties": {
                "path_classes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["glob", "role"],
                        "properties": {
                            "glob": {"type": "string"},
                            "role": {"type": "string"},
                        },
                    },
                },
                "excluded_paths": {"type": "array", "items": {"type": "string"}},
            },
        },
        "expected_workflow_path_classes": {
            "type": "array",
            "items": {"type": "string"},
        },
        "role_classification_rules": {
            "type": "object",
            "required": ["projection_defaults", "identity_required_must_be_explicit"],
            "properties": {
                "projection_defaults": {"type": "object"},
                "identity_required_must_be_explicit": {"type": "boolean"},
                "optional_historical_limitation_default": {"type": "string"},
                "runtime_install_false_is_not_a_decision": {"type": "boolean"},
            },
        },
        "materializer_contract": {"type": "object"},
        "validation_contract": {"type": "object"},
        "invocation_contract": {"type": "object"},
        "permission_discovery_identity": {"type": "object"},
        "file_identity_rule": {"type": "object"},
    },
    "additionalProperties": True,
}


@dataclass(frozen=True)
class RunLockedPath:
    path: str
    role: str
    blob_id: str
    sha256: str
    file_mode: str


def _list_paths(repo_root: Path, base_sha: str) -> list[str]:
    """List all tree paths at ``base_sha`` (repository-contained inputs only)."""
    result = run_git(repo_root, "ls-tree", "-r", "--name-only", base_sha)
    if result.returncode != 0:
        raise BenchmarkError(f"git ls-tree failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def generate_run_locked(
    template_path: Path,
    repo_root: Path,
    benchmark_base_sha: str,
    generated_by: str,
    generated_at_utc: str | None = None,
    evaluation_id: str | None = None,
) -> dict[str, Any]:
    """Generate one run-locked manifest from a template and the base SHA."""
    raw = load_json(template_path)
    validate_basic(raw, TEMPLATE_SCHEMA, "template")
    if raw.get("kind") != "template":
        raise BenchmarkError(f"expected template manifest, got {raw.get('kind')!r}")
    if raw.get("protocol_identity") != "task-65-round-2-v2":
        raise BenchmarkError("template protocol identity mismatch")
    if raw.get("source_selector") != {
        "kind": "fixed-commit",
        "ref": "BENCHMARK_BASE_SHA",
    }:
        raise BenchmarkError(
            "template source selector must be fixed-commit/BENCHMARK_BASE_SHA"
        )
    if generated_at_utc is None:
        generated_at_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
        generated_at_utc = generated_at_utc.replace("+00:00", "Z")
    if len(generated_at_utc) != 20 or not generated_at_utc.endswith("Z"):
        raise BenchmarkError("generated_at_utc must be a UTC second timestamp")
    if evaluation_id is not None and not evaluation_id.strip():
        raise BenchmarkError("evaluation_id must not be empty when assigned")

    resolved_base = git_rev_parse(repo_root, benchmark_base_sha)
    all_paths = _list_paths(repo_root, resolved_base)
    excluded = tuple(raw["closure_derivation_rules"].get("excluded_paths", []))

    def _excluded(path: str) -> bool:
        return any(fnmatch.fnmatch(path, pattern) for pattern in excluded)

    role_rules = raw["role_classification_rules"]
    projection_defaults = role_rules["projection_defaults"]
    if not role_rules.get("identity_required_must_be_explicit"):
        raise BenchmarkError(
            "identity_required_must_be_explicit must be true (no implicit default)"
        )
    if not role_rules.get("runtime_install_false_is_not_a_decision"):
        raise BenchmarkError(
            "runtime_install_false_is_not_a_decision must be true (no guessing)"
        )

    entries: list[RunLockedPath] = []
    matched: set[str] = set()
    for path_class in raw["closure_derivation_rules"]["path_classes"]:
        pattern = path_class["glob"]
        role = path_class["role"]
        # First-match-wins ordering: a more specific class listed earlier
        # overrides a later general class for the same path (deterministic).
        for path in all_paths:
            if _excluded(path):
                continue
            if path in matched:
                continue
            if not fnmatch.fnmatch(path, pattern):
                continue
            matched.add(path)
            blob_entry = git_ls_tree_blob(repo_root, resolved_base, path)
            if blob_entry is None:
                raise BenchmarkError(f"path {path} missing at base (fail closed)")
            mode, blob_id = blob_entry
            data = git_cat_blob(repo_root, blob_id)
            projection_action = projection_defaults.get(role)
            if projection_action not in {
                "INSTALL_GENERATION_VERSION",
                "INHERIT_BUSINESS_BASE",
            }:
                raise BenchmarkError(
                    f"role {role}: no explicit projection default (fail closed)"
                )
            if is_invalid_control_plane_inherit(path, projection_action):
                raise BenchmarkError(
                    f"{path}: GENERATION_CONTROL_PLANE cannot use "
                    "INHERIT_BUSINESS_BASE (fail closed)"
                )
            entries.append(
                RunLockedPath(
                    path=path,
                    role=role,
                    blob_id=blob_id,
                    sha256=sha256_bytes(data),
                    file_mode=mode,
                )
            )

    # The template's path classes are a projection declaration, not a best
    # effort filter.  Every mechanically derived current control-plane path
    # must be selected by exactly one class; otherwise a current workflow file
    # can silently disappear from the run-locked closure.
    control_plane_paths = runtime_control_plane_paths(repo_root, resolved_base)
    unmatched = sorted(control_plane_paths - matched)
    if unmatched:
        raise BenchmarkError(
            "runtime-control-plane path class coverage incomplete: "
            + ", ".join(unmatched)
        )

    # No path may be claimed by more than one class (deterministic ordering).
    entries_sorted = sorted(entries, key=lambda item: item.path)

    locked_path_dicts = [
        {
            "path": entry.path,
            "role": entry.role,
            "blob_id": entry.blob_id,
            "sha256": entry.sha256,
            "file_mode": entry.file_mode,
            "projection_action": projection_defaults.get(entry.role),
            "exists_at_source": True,
        }
        for entry in entries_sorted
    ]
    run_locked: dict[str, Any] = {
        "schema_version": 1,
        "protocol_identity": "task-65-round-2-v2",
        "kind": "run_locked",
        "generation_id": raw["generation_id"],
        "agent_identity": raw["agent_identity"],
        "source_selector": {"kind": "fixed-commit", "ref": "BENCHMARK_BASE_SHA"},
        "benchmark_base_sha": resolved_base,
        "generated_by": generated_by,
        "generated_at_utc": generated_at_utc,
        "closure": {
            "definition": raw["closure_derivation_rules"],
            "paths": locked_path_dicts,
        },
        "validation_contract": raw["validation_contract"],
        "invocation_contract": raw["invocation_contract"],
    }
    if evaluation_id is not None:
        # EVALUATION_ID (mandatory shared identity): assigned by the freeze
        # operator, identical for C and D, gated by file_identity_report.
        run_locked["evaluation_id"] = evaluation_id
    run_locked["generation_identity_digest"] = generation_identity_digest(
        resolved_base, locked_path_dicts
    )
    validate_basic(run_locked, RUN_LOCKED_SCHEMA, "run-locked manifest")
    return run_locked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate C/D RUN-LOCKED MANIFESTS from templates at freeze."
    )
    parser.add_argument("--template", action="append", required=True)
    parser.add_argument("--benchmark-base-sha", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--generated-by", default="task-65-round-2-v2 run-lock", help="freeze operator"
    )
    parser.add_argument(
        "--evaluation-id",
        help=(
            "EVALUATION_ID (mandatory shared identity) assigned at freeze; "
            "identical for C and D; required for formal freeze"
        ),
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated: dict[str, Path] = {}
    generated_at_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    generated_at_utc = generated_at_utc.replace("+00:00", "Z")
    try:
        for template in args.template:
            raw = load_json(Path(template))
            generation_id = raw["generation_id"]
            run_locked = generate_run_locked(
                Path(template),
                repo_root,
                args.benchmark_base_sha,
                args.generated_by,
                generated_at_utc,
                evaluation_id=args.evaluation_id,
            )
            destination = (
                out_dir / f"generation-{generation_id.lower()}-run-locked-manifest.json"
            )
            destination.write_text(
                json.dumps(run_locked, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            generated[generation_id] = destination
            print(
                f"generated run-locked manifest for {generation_id}: {destination} "
                f"({len(run_locked['closure']['paths'])} paths)"
            )
    except BenchmarkError as exc:
        print(f"RUN LOCK FAIL CLOSED: {exc}", file=sys.stderr)
        return 1

    # C/D FILE IDENTITY REPORT (independent bundle comparison; control-plane
    # file identity differences are diagnostic, shared identities gate).
    try:
        from file_identity_report import file_identity_report

        if "C" in generated and "D" in generated:
            report = file_identity_report(
                load_json(generated["C"]), load_json(generated["D"])
            )
            report_path = out_dir / "cd-file-identity-report.json"
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(json.dumps(report, sort_keys=True))
    except ImportError:
        print(
            "warning: file_identity_report unavailable; C/D report not generated",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
