"""C/D FILE IDENTITY REPORT for task-65-round-2-v2.

Compares the independently generated C and D run-locked manifests.

Formal comparison validity is decided by the mandatory shared identities
alone (SAME BUSINESS / SAME TASK / SAME EVALUATION):

- BUSINESS_SNAPSHOT_ID — carried by ``benchmark_base_sha``;
- TASK_SPEC_ID — carried by ``protocol_identity``;
- EVALUATION_ID — carried by the optional ``evaluation_id`` field when it
  has been assigned (run-lock ``--evaluation-id`` at freeze).

Any mismatch -> ``human_gate: true``; the formal C vs D comparison is
invalid and must not continue.

Control-plane file identity — complete generation source-path set,
per-path blob ID, per-path sha256, file mode, generation identity digest —
is DIAGNOSTIC / PROVENANCE. Every difference is recorded in this report,
but a control-plane file identity difference alone never invalidates an
Arm, never sets a mandatory Human Gate, and never prohibits formal C vs D
comparison. Per-arm identities (workflow / runner / agent runtime /
environment) are carried by the allowed identity fields
(``ALLOWED_IDENTITY_FIELDS``) and are traceable without requiring
cross-Arm equality.

Allowed identity differences remain limited to: agent identity, discovery
adapter, invocation identity, permission/runtime environment identity,
session/evidence identity; no per-agent-pruned closure (a per-agent-pruned
file set is a violation).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from benchmark_common import BenchmarkError, load_json, validate_basic
from generation_materializer import RUN_LOCKED_SCHEMA

# Fields allowed to differ between C and D (identity-only differences).
ALLOWED_IDENTITY_FIELDS: frozenset[str] = frozenset(
    {
        "generation_id",
        "agent_identity",
        "generated_by",
        "generated_at_utc",
        "invocation_contract",
        "permission_discovery_identity",
    }
)

# The mandatory shared identities that decide formal comparison validity.
# BUSINESS_SNAPSHOT_ID and TASK_SPEC_ID are carried by required run-locked
# manifest fields; EVALUATION_ID is carried by the optional ``evaluation_id``
# field (assigned at freeze via run-lock ``--evaluation-id``) and is only
# gated when at least one manifest carries it.
SHARED_IDENTITY_FIELDS: tuple[str, ...] = (
    "business_snapshot_id",
    "task_spec_id",
    "evaluation_id",
)

_SHARED_IDENTITY_FIELD_MAP: dict[str, str] = {
    "business_snapshot_id": "benchmark_base_sha",
    "task_spec_id": "protocol_identity",
    "evaluation_id": "evaluation_id",
}

DIAGNOSTIC_DISPOSITION = (
    "DIAGNOSTIC — control-plane file identity differences recorded; "
    "formal comparison permitted if experiment definition permits"
)
HUMAN_GATE_DISPOSITION = (
    "HUMAN GATE — mandatory shared identity mismatch; formal C vs D comparison invalid"
)


def _shared_identity_value(manifest: dict[str, Any], identity: str) -> Any:
    return manifest.get(_SHARED_IDENTITY_FIELD_MAP[identity])


def _path_entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["path"]: entry for entry in manifest["closure"]["paths"]}


def file_identity_report(
    c_manifest: dict[str, Any], d_manifest: dict[str, Any]
) -> dict[str, Any]:
    """Compare C and D run-locked manifests; returns the FILE IDENTITY REPORT."""
    validate_basic(c_manifest, RUN_LOCKED_SCHEMA, "C run-locked manifest")
    validate_basic(d_manifest, RUN_LOCKED_SCHEMA, "D run-locked manifest")
    if c_manifest.get("generation_id") != "C":
        raise BenchmarkError("C report input must have generation_id C")
    if d_manifest.get("generation_id") != "D":
        raise BenchmarkError("D report input must have generation_id D")
    c_paths = _path_entries(c_manifest)
    d_paths = _path_entries(d_manifest)

    c_only = sorted(set(c_paths) - set(d_paths))
    d_only = sorted(set(d_paths) - set(c_paths))
    common = sorted(set(c_paths) & set(d_paths))

    blob_diffs: list[str] = []
    sha_diffs: list[str] = []
    mode_diffs: list[str] = []
    for path in common:
        c_entry, d_entry = c_paths[path], d_paths[path]
        if c_entry["blob_id"] != d_entry["blob_id"]:
            blob_diffs.append(path)
        if c_entry["sha256"] != d_entry["sha256"]:
            sha_diffs.append(path)
        if c_entry["file_mode"] != d_entry["file_mode"]:
            mode_diffs.append(path)

    path_set_identical = not c_only and not d_only
    blob_identical = not blob_diffs
    sha_identical = not sha_diffs
    mode_identical = not mode_diffs
    identity_digest_identical = c_manifest.get(
        "generation_identity_digest"
    ) == d_manifest.get("generation_identity_digest")

    identity_diffs: dict[str, Any] = {}
    for field in ALLOWED_IDENTITY_FIELDS:
        c_value, d_value = c_manifest.get(field), d_manifest.get(field)
        if c_value != d_value:
            identity_diffs[field] = {"c": c_value, "d": d_value}

    unexpected_field_diffs: list[str] = []
    for key in sorted(set(c_manifest) | set(d_manifest)):
        if key in ALLOWED_IDENTITY_FIELDS:
            continue
        if key == "evaluation_id":
            # Owned by the mandatory shared-identity block below.
            continue
        if c_manifest.get(key) != d_manifest.get(key):
            unexpected_field_diffs.append(key)

    # Mandatory shared identities decide formal comparison validity.  A
    # control-plane file identity difference never gates formal comparison.
    shared_identities: dict[str, Any] = {}
    shared_identities_identical = True
    for identity in SHARED_IDENTITY_FIELDS:
        c_value = _shared_identity_value(c_manifest, identity)
        d_value = _shared_identity_value(d_manifest, identity)
        if identity == "evaluation_id":
            carried = c_value is not None or d_value is not None
            # Not carried -> no gate; carried -> both present and equal.
            identical = c_value == d_value if carried else True
        else:
            carried = True
            identical = c_value == d_value
        if not identical:
            shared_identities_identical = False
        shared_identities[identity] = {
            "carried": carried,
            "identical": identical,
            "c": c_value,
            "d": d_value,
        }

    human_gate = not shared_identities_identical
    file_identity_consistent = (
        path_set_identical
        and blob_identical
        and sha_identical
        and mode_identical
        and identity_digest_identical
    )
    if not shared_identities_identical:
        disposition = HUMAN_GATE_DISPOSITION
    elif file_identity_consistent:
        disposition = "pass"
    else:
        disposition = DIAGNOSTIC_DISPOSITION

    report: dict[str, Any] = {
        "protocol_identity": "task-65-round-2-v2",
        "report": "C/D FILE IDENTITY REPORT",
        "c_generation_id": c_manifest.get("generation_id"),
        "d_generation_id": d_manifest.get("generation_id"),
        "c_benchmark_base_sha": c_manifest.get("benchmark_base_sha"),
        "d_benchmark_base_sha": d_manifest.get("benchmark_base_sha"),
        "mandatory_shared_identities": shared_identities,
        "shared_identities_identical": shared_identities_identical,
        "path_set_identical": path_set_identical,
        "per_path_blob_identical": blob_identical,
        "per_path_sha256_identical": sha_identical,
        "per_path_file_mode_identical": mode_identical,
        "file_mode_differences": mode_diffs,
        "generation_identity_digest_identical": identity_digest_identical,
        "c_only_paths": c_only,
        "d_only_paths": d_only,
        "blob_differences": blob_diffs,
        "sha256_differences": sha_diffs,
        "allowed_identity_differences": identity_diffs,
        "unexpected_field_differences": unexpected_field_diffs,
        "no_per_agent_pruned_closure": not c_only and not d_only,
        "human_gate": human_gate,
        "disposition": disposition,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the C/D FILE IDENTITY REPORT from run-locked manifests; "
            "control-plane file identity differences are diagnostic/provenance, "
            "human_gate fires only on mandatory shared identity mismatch."
        )
    )
    parser.add_argument("--c", required=True, help="C run-locked manifest")
    parser.add_argument("--d", required=True, help="D run-locked manifest")
    parser.add_argument("--out", help="optional output path")
    args = parser.parse_args(argv)

    try:
        report = file_identity_report(load_json(Path(args.c)), load_json(Path(args.d)))
    except BenchmarkError as exc:
        print(f"FILE IDENTITY REPORT FAIL CLOSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0 if not report["human_gate"] else 2


if __name__ == "__main__":
    sys.exit(main())
