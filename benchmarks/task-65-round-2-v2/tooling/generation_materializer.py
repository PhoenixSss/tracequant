"""Deterministic generation-fixture materializer for task-65-round-2-v2.

Materializes a pinned generation manifest into a conductor-local Git-ignored
fixture store by extracting git blobs verbatim (byte-for-byte, no rewriting).

Hermetic / fail-closed contract (Task #125 Materializer section):

- verbatim blob extraction (no rewriting of any kind);
- deterministic (the same manifest input always yields the same tree/hash);
- idempotent (re-running against the same store changes nothing);
- no synthetic repair (stale references are never fixed or spliced);
- no current-generation fallback (missing historical files are never replaced
  by current files);
- no cross-generation shared runtime file (each generation materializes into
  its own isolated bundle directory);
- no symlinks (only regular files with validated git file modes);
- validate git file mode (destination mode matches the source blob mode).

Fail-closed conditions (any trigger -> HUMAN GATE): source path missing at
the pinned source commit; blob mismatch; sha256 mismatch; manifest/source
identity mismatch.

The fixture store is never committed.  Fixture bundles are not Formal Arm
runtime trees; the runtime projection is a separate, mechanically distinct
concept (see the runtime-projection contract).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmark_common import (
    DEFAULT_FIXTURE_STORE,
    MANIFEST_KINDS,
    PROJECTION_ACTIONS,
    ROLES,
    BenchmarkError,
    git_cat_blob,
    git_ls_tree_blob,
    load_json,
    sha256_bytes,
    sha256_file,
    tree_identity,
    validate_basic,
    write_bytes_verbatim,
)


@dataclass(frozen=True)
class ManifestPath:
    """One closure entry of a pinned generation manifest."""

    path: str
    role: str
    blob_id: str
    sha256: str
    file_mode: str
    projection_action: str
    projection_reason: str | None


@dataclass
class PinnedManifest:
    """Parsed pinned manifest."""

    generation_id: str
    agent_identity: str
    workflow_source_sha: str
    source_label: str
    paths: list[ManifestPath]
    known_limitations: list[str]
    raw: dict[str, Any] = field(default_factory=dict)


PINNED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema_version",
        "protocol_identity",
        "kind",
        "generation_id",
        "agent_identity",
        "workflow_source",
        "closure",
        "invocation",
        "permission_profile",
        "known_limitations",
    ],
    "properties": {
        "schema_version": {"type": "integer"},
        "protocol_identity": {"type": "string"},
        "kind": {"type": "string", "enum": sorted(MANIFEST_KINDS)},
        "generation_id": {"type": "string"},
        "agent_identity": {"type": "string"},
        "workflow_source": {
            "type": "object",
            "required": ["sha", "label"],
            "properties": {
                "sha": {"type": "string", "pattern": r"[0-9a-f]{40}"},
                "label": {"type": "string"},
            },
        },
        "closure": {
            "type": "object",
            "required": ["definition", "paths"],
            "properties": {
                "definition": {"type": "string"},
                "paths": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "path",
                            "role",
                            "projection_action",
                            "exists_at_source",
                        ],
                        "properties": {
                            "path": {"type": "string"},
                            "role": {"type": "string", "enum": sorted(ROLES)},
                            "projection_action": {
                                "type": "string",
                                "enum": sorted(PROJECTION_ACTIONS),
                            },
                            "projection_reason": {"type": ["string", "null"]},
                            "exists_at_source": {"type": "boolean"},
                            "blob_id": {"type": "string", "pattern": r"[0-9a-f]{40}"},
                            "sha256": {"type": "string", "pattern": r"[0-9a-f]{64}"},
                            "file_mode": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
        },
        "invocation": {"type": "object"},
        "permission_profile": {"type": "object"},
        "known_limitations": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "additionalProperties": True,
}


def parse_pinned_manifest(manifest_path: Path) -> PinnedManifest:
    """Load and structurally validate a pinned manifest; fail closed."""
    raw = load_json(manifest_path)
    validate_basic(raw, PINNED_SCHEMA, "manifest")
    if raw.get("protocol_identity") != "task-65-round-2-v2":
        raise BenchmarkError(
            f"protocol identity mismatch: {raw.get('protocol_identity')!r}"
        )
    if raw.get("kind") != "pinned":
        raise BenchmarkError(f"expected pinned manifest, got kind {raw.get('kind')!r}")
    closure = raw["closure"]
    paths: list[ManifestPath] = []
    for entry in closure["paths"]:
        if not entry["exists_at_source"]:
            if entry["projection_action"] != "ENSURE_ABSENT":
                raise BenchmarkError(
                    f"{entry['path']}: absent-at-source entry must use "
                    f"projection_action ENSURE_ABSENT"
                )
            paths.append(
                ManifestPath(
                    path=entry["path"],
                    role=entry["role"],
                    blob_id="",
                    sha256="",
                    file_mode="",
                    projection_action=entry["projection_action"],
                    projection_reason=entry.get("projection_reason"),
                )
            )
            continue
        for key in ("blob_id", "sha256", "file_mode"):
            if not entry.get(key):
                raise BenchmarkError(
                    f"{entry['path']}: missing {key} for existing file"
                )
        paths.append(
            ManifestPath(
                path=entry["path"],
                role=entry["role"],
                blob_id=entry["blob_id"],
                sha256=entry["sha256"],
                file_mode=entry["file_mode"],
                projection_action=entry["projection_action"],
                projection_reason=entry.get("projection_reason"),
            )
        )
    return PinnedManifest(
        generation_id=raw["generation_id"],
        agent_identity=raw["agent_identity"],
        workflow_source_sha=raw["workflow_source"]["sha"],
        source_label=raw["workflow_source"]["label"],
        paths=paths,
        known_limitations=list(raw.get("known_limitations", [])),
        raw=raw,
    )


def materialize(
    manifest: PinnedManifest,
    repo_root: Path,
    store: Path,
) -> dict[str, Any]:
    """Extract the pinned generation fixture into ``store``; fail closed.

    Returns the deterministic bundle record.  The bundle tree identity covers
    all materialized (path, sha256, file mode) entries.
    """
    gates: list[dict[str, Any]] = []
    written: list[dict[str, Any]] = []

    for entry in manifest.paths:
        path = entry.path
        if not entry.blob_id:
            continue  # absent-at-source declaration, nothing to materialize
        source_entry = git_ls_tree_blob(repo_root, manifest.workflow_source_sha, path)
        if source_entry is None:
            gates.append(
                {
                    "name": "source_path_missing",
                    "status": "fail",
                    "detail": f"path {path} absent at {manifest.workflow_source_sha}",
                }
            )
            continue
        source_mode, source_blob = source_entry
        if source_blob != entry.blob_id:
            gates.append(
                {
                    "name": "manifest_source_identity_mismatch",
                    "status": "fail",
                    "detail": (
                        f"path {path}: manifest blob {entry.blob_id}, "
                        f"source blob {source_blob}"
                    ),
                }
            )
            continue
        data = git_cat_blob(repo_root, entry.blob_id)
        actual_sha256 = sha256_bytes(data)
        if actual_sha256 != entry.sha256:
            gates.append(
                {
                    "name": "sha256_mismatch",
                    "status": "fail",
                    "detail": (
                        f"path {path}: manifest sha256 {entry.sha256}, "
                        f"actual {actual_sha256}"
                    ),
                }
            )
            continue
        if source_mode != entry.file_mode:
            gates.append(
                {
                    "name": "file_mode_mismatch",
                    "status": "fail",
                    "detail": (
                        f"path {path}: manifest mode {entry.file_mode}, "
                        f"source mode {source_mode}"
                    ),
                }
            )
            continue
        destination = store / path
        _write_idempotent(destination, data, entry.file_mode)
        written.append(
            {
                "path": path,
                "role": entry.role,
                "blob_id": entry.blob_id,
                "sha256": entry.sha256,
                "file_mode": entry.file_mode,
            }
        )

    if any(gate["status"] == "fail" for gate in gates):
        raise BenchmarkError(
            "materializer fail-closed: " + "; ".join(g["detail"] for g in gates)
        )

    identity = tree_identity(
        (item["path"], item["sha256"], item["file_mode"]) for item in written
    )
    bundle: dict[str, Any] = {
        "protocol_identity": "task-65-round-2-v2",
        "schema_version": 1,
        "kind": "fixture_bundle",
        "generation_id": manifest.generation_id,
        "agent_identity": manifest.agent_identity,
        "workflow_source_sha": manifest.workflow_source_sha,
        "source_label": manifest.source_label,
        "manifest_sha256": sha256_bytes(
            json.dumps(manifest.raw, sort_keys=True).encode("utf-8")
        ),
        "tree_identity": identity,
        "file_count": len(written),
        "files": written,
    }
    return bundle


def _write_idempotent(destination: Path, data: bytes, mode: str) -> None:
    """Write verbatim; a pre-existing identical file is an idempotent no-op."""
    if destination.exists():
        if sha256_file(destination) != sha256_bytes(data):
            raise BenchmarkError(
                f"fixture store collision at {destination}: existing content "
                f"differs from manifest (refusing to overwrite)"
            )
        return
    write_bytes_verbatim(destination, data, mode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialize a pinned generation manifest into the fixture store."
    )
    parser.add_argument(
        "--manifest", required=True, help="path to pinned manifest JSON"
    )
    parser.add_argument(
        "--repo-root", default=".", help="repository root (default cwd)"
    )
    parser.add_argument(
        "--store", default=DEFAULT_FIXTURE_STORE, help="conductor-local fixture store"
    )
    parser.add_argument(
        "--out-record", help="optional output path for the bundle record"
    )
    args = parser.parse_args(argv)

    try:
        repo_root = Path(args.repo_root).resolve()
        manifest = parse_pinned_manifest(Path(args.manifest))
        store = Path(args.store).resolve()
        bundle = materialize(manifest, repo_root, store)
    except BenchmarkError as exc:
        print(f"MATERIALIZER FAIL CLOSED: {exc}", file=sys.stderr)
        return 1

    digest = {
        "status": "pass",
        "generation_id": bundle["generation_id"],
        "tree_identity": bundle["tree_identity"],
        "file_count": bundle["file_count"],
    }
    print(json.dumps(digest, sort_keys=True))
    if args.out_record:
        Path(args.out_record).write_text(
            json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
