"""C/D FILE IDENTITY REPORT for task-65-round-2-v2.

Verifies the C/D identical-generation file identity invariant between the
independently generated C and D run-locked manifests:

- complete generation source-path set identical;
- per-path blob ID identical;
- per-path sha256 identical;
- allowed differences limited to: agent identity, discovery adapter,
  invocation identity, permission/runtime environment identity,
  session/evidence identity;
- no per-agent-pruned closure (a per-agent-pruned file set is a violation).

Any unexpected file-set / blob / hash difference -> ``human_gate: true`` and
the formal C vs D comparison must not continue.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from benchmark_common import BenchmarkError, load_json

# Fields allowed to differ between C and D (identity-only differences).
ALLOWED_IDENTITY_FIELDS: frozenset[str] = frozenset(
    {
        "generation_id",
        "agent_identity",
        "invocation_contract",
        "permission_discovery_identity",
    }
)


def _path_entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["path"]: entry for entry in manifest["closure"]["paths"]}


def file_identity_report(
    c_manifest: dict[str, Any], d_manifest: dict[str, Any]
) -> dict[str, Any]:
    """Compare C and D run-locked manifests; returns the FILE IDENTITY REPORT."""
    c_paths = _path_entries(c_manifest)
    d_paths = _path_entries(d_manifest)

    c_only = sorted(set(c_paths) - set(d_paths))
    d_only = sorted(set(d_paths) - set(c_paths))
    common = sorted(set(c_paths) & set(d_paths))

    blob_diffs: list[str] = []
    sha_diffs: list[str] = []
    for path in common:
        c_entry, d_entry = c_paths[path], d_paths[path]
        if c_entry["blob_id"] != d_entry["blob_id"]:
            blob_diffs.append(path)
        if c_entry["sha256"] != d_entry["sha256"]:
            sha_diffs.append(path)

    path_set_identical = not c_only and not d_only
    blob_identical = not blob_diffs
    sha_identical = not sha_diffs

    identity_diffs: dict[str, Any] = {}
    for field in ALLOWED_IDENTITY_FIELDS:
        c_value, d_value = c_manifest.get(field), d_manifest.get(field)
        if c_value != d_value:
            identity_diffs[field] = {"c": c_value, "d": d_value}

    unexpected_field_diffs: list[str] = []
    for key in sorted(set(c_manifest) | set(d_manifest)):
        if key in ALLOWED_IDENTITY_FIELDS:
            continue
        if c_manifest.get(key) != d_manifest.get(key):
            unexpected_field_diffs.append(key)

    human_gate = not (path_set_identical and blob_identical and sha_identical)

    report: dict[str, Any] = {
        "protocol_identity": "task-65-round-2-v2",
        "report": "C/D FILE IDENTITY REPORT",
        "c_generation_id": c_manifest.get("generation_id"),
        "d_generation_id": d_manifest.get("generation_id"),
        "c_benchmark_base_sha": c_manifest.get("benchmark_base_sha"),
        "d_benchmark_base_sha": d_manifest.get("benchmark_base_sha"),
        "path_set_identical": path_set_identical,
        "per_path_blob_identical": blob_identical,
        "per_path_sha256_identical": sha_identical,
        "c_only_paths": c_only,
        "d_only_paths": d_only,
        "blob_differences": blob_diffs,
        "sha256_differences": sha_diffs,
        "allowed_identity_differences": identity_diffs,
        "unexpected_field_differences": unexpected_field_diffs,
        "no_per_agent_pruned_closure": not c_only and not d_only,
        "human_gate": human_gate,
        "disposition": "HUMAN GATE — do not continue formal C vs D comparison"
        if human_gate
        else "pass",
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the C/D FILE IDENTITY REPORT from run-locked manifests."
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
