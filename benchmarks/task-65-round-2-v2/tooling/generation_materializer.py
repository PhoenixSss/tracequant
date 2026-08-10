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
import re
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmark_common import (
    DEFAULT_FIXTURE_STORE,
    PROJECTION_ACTIONS,
    ROLES,
    BenchmarkError,
    generation_identity_digest,
    git_cat_blob,
    git_ls_tree_blob,
    git_rev_parse,
    load_json,
    sha256_bytes,
    sha256_file,
    tree_identity,
    validate_basic,
    write_bytes_verbatim,
)

MATERIALIZATION_METADATA = ".tracequant-materialization.json"
UTC_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@dataclass(frozen=True)
class MaterializationRecord:
    """One explicit, immutable closure record shared by both manifest kinds."""

    path: str
    role: str
    blob_id: str
    sha256: str
    file_mode: str
    projection_action: str
    projection_reason: str | None
    exists_at_source: bool


ManifestPath = MaterializationRecord


@dataclass(frozen=True)
class PinnedManifest:
    """Parsed pinned manifest."""

    generation_id: str
    agent_identity: str
    workflow_source_sha: str
    source_label: str
    paths: tuple[MaterializationRecord, ...]
    source_absent_inherit_allowlist: frozenset[str]
    known_limitations: tuple[str, ...]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunLockedManifest:
    """A C/D manifest whose source and closure facts were already locked."""

    generation_id: str
    agent_identity: dict[str, Any]
    source_selector: dict[str, str]
    benchmark_base_sha: str
    generated_by: str
    generated_at_utc: str
    generation_identity_digest: str
    paths: tuple[MaterializationRecord, ...]
    validation_contract: dict[str, Any]
    invocation_contract: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def source_sha(self) -> str:
        return self.benchmark_base_sha


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
        "kind": {"type": "string", "enum": ["pinned"]},
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
                            "file_mode": {
                                "type": "string",
                                "enum": ["100644", "100755"],
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "source_absent_inherit_allowlist": {
                    "type": "array",
                    "items": {"type": "string"},
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


RUN_LOCKED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema_version",
        "protocol_identity",
        "kind",
        "generation_id",
        "agent_identity",
        "source_selector",
        "benchmark_base_sha",
        "generated_by",
        "generated_at_utc",
        "generation_identity_digest",
        "closure",
        "validation_contract",
        "invocation_contract",
    ],
    "properties": {
        "schema_version": {"type": "integer"},
        "protocol_identity": {"type": "string"},
        "kind": {"type": "string", "enum": ["run_locked"]},
        "generation_id": {"type": "string", "enum": ["C", "D"]},
        "agent_identity": {"type": "object"},
        "source_selector": {
            "type": "object",
            "required": ["kind", "ref"],
            "properties": {
                "kind": {"type": "string", "enum": ["fixed-commit"]},
                "ref": {"type": "string", "enum": ["BENCHMARK_BASE_SHA"]},
            },
            "additionalProperties": False,
        },
        "benchmark_base_sha": {
            "type": "string",
            "pattern": r"[0-9a-f]{40}",
        },
        "generated_by": {"type": "string", "minLength": 1},
        "generated_at_utc": {
            "type": "string",
            "pattern": UTC_TIMESTAMP_PATTERN.pattern,
        },
        "generation_identity_digest": {
            "type": "string",
            "pattern": r"[0-9a-f]{64}",
        },
        "closure": {
            "type": "object",
            "required": ["definition", "paths"],
            "properties": {
                "definition": {"type": ["object", "string"]},
                "paths": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "path",
                            "role",
                            "projection_action",
                            "exists_at_source",
                            "blob_id",
                            "sha256",
                            "file_mode",
                        ],
                        "properties": {
                            "path": {"type": "string", "minLength": 1},
                            "role": {"type": "string", "enum": sorted(ROLES)},
                            "projection_action": {
                                "type": "string",
                                "enum": sorted(PROJECTION_ACTIONS),
                            },
                            "exists_at_source": {"type": "boolean"},
                            "blob_id": {
                                "type": "string",
                                "pattern": r"[0-9a-f]{40}",
                            },
                            "sha256": {
                                "type": "string",
                                "pattern": r"[0-9a-f]{64}",
                            },
                            "file_mode": {
                                "type": "string",
                                "enum": ["100644", "100755"],
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
        "validation_contract": {"type": "object"},
        "invocation_contract": {"type": "object"},
    },
    "additionalProperties": False,
}


def _validate_path(path: str) -> None:
    """Reject paths that could escape the independent fixture directory."""
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or path == "."
        or path.startswith("../")
        or "/../" in path
        or path.endswith("/..")
    ):
        raise BenchmarkError(f"invalid manifest path {path!r} (fail closed)")


def _record_from_entry(entry: Mapping[str, Any]) -> MaterializationRecord:
    path = entry["path"]
    _validate_path(path)
    return MaterializationRecord(
        path=path,
        role=entry["role"],
        blob_id=entry.get("blob_id", ""),
        sha256=entry.get("sha256", ""),
        file_mode=entry.get("file_mode", ""),
        projection_action=entry["projection_action"],
        projection_reason=entry.get("projection_reason"),
        exists_at_source=entry["exists_at_source"],
    )


def _validate_timestamp(value: str) -> None:
    if UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise BenchmarkError(
            f"invalid generated_at_utc {value!r}; expected UTC second timestamp"
        )
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise BenchmarkError(f"invalid generated_at_utc {value!r}") from exc


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
    source_absent_inherit_allowlist = frozenset(
        closure.get("source_absent_inherit_allowlist", [])
    )
    paths: list[MaterializationRecord] = []
    seen_paths: set[str] = set()
    for entry in closure["paths"]:
        path = entry["path"]
        if path in seen_paths:
            raise BenchmarkError(f"duplicate projection entry for {path}")
        seen_paths.add(path)
        _validate_path(path)
        if not entry["exists_at_source"]:
            action = entry["projection_action"]
            if action == "INHERIT_BUSINESS_BASE":
                if path not in source_absent_inherit_allowlist:
                    raise BenchmarkError(
                        f"{path}: source-absent INHERIT requires explicit "
                        "source_absent_inherit_allowlist classification"
                    )
                if not entry.get("projection_reason"):
                    raise BenchmarkError(
                        f"{path}: source-absent INHERIT requires a reason"
                    )
            elif action != "ENSURE_ABSENT":
                raise BenchmarkError(
                    f"{path}: absent-at-source entry must use ENSURE_ABSENT "
                    "or an explicitly allowlisted INHERIT_BUSINESS_BASE"
                )
            paths.append(_record_from_entry(entry))
            continue
        if entry["projection_action"] == "ENSURE_ABSENT":
            raise BenchmarkError(
                f"{path}: source-existing entry cannot use ENSURE_ABSENT"
            )
        for key in ("blob_id", "sha256", "file_mode"):
            if not entry.get(key):
                raise BenchmarkError(f"{path}: missing {key} for existing file")
        paths.append(_record_from_entry(entry))
    return PinnedManifest(
        generation_id=raw["generation_id"],
        agent_identity=raw["agent_identity"],
        workflow_source_sha=raw["workflow_source"]["sha"],
        source_label=raw["workflow_source"]["label"],
        paths=tuple(paths),
        source_absent_inherit_allowlist=source_absent_inherit_allowlist,
        known_limitations=tuple(raw.get("known_limitations", [])),
        raw=raw,
    )


def parse_run_locked_manifest(manifest_path: Path) -> RunLockedManifest:
    """Load and validate a concrete C/D run-locked manifest."""
    raw = load_json(manifest_path)
    validate_basic(raw, RUN_LOCKED_SCHEMA, "run-locked manifest")
    if raw.get("protocol_identity") != "task-65-round-2-v2":
        raise BenchmarkError(
            f"protocol identity mismatch: {raw.get('protocol_identity')!r}"
        )
    if raw.get("kind") != "run_locked":
        raise BenchmarkError(f"expected run_locked manifest, got {raw.get('kind')!r}")
    source_selector = raw["source_selector"]
    if source_selector != {
        "kind": "fixed-commit",
        "ref": "BENCHMARK_BASE_SHA",
    }:
        raise BenchmarkError(
            "run-locked source selector must be fixed-commit/BENCHMARK_BASE_SHA"
        )
    source_sha = raw["benchmark_base_sha"]
    if len(source_sha) != 40 or any(
        char not in "0123456789abcdef" for char in source_sha
    ):
        raise BenchmarkError("run-locked benchmark_base_sha is not a full SHA")
    _validate_timestamp(raw["generated_at_utc"])
    agent_identity = raw["agent_identity"]
    expected_agent = {"C": "codex", "D": "claude"}[raw["generation_id"]]
    if agent_identity.get("agent") != expected_agent:
        raise BenchmarkError(
            f"generation {raw['generation_id']} has incompatible agent identity"
        )

    closure = raw["closure"]
    paths: list[MaterializationRecord] = []
    seen_paths: set[str] = set()
    for entry in closure["paths"]:
        path = entry["path"]
        if path in seen_paths:
            raise BenchmarkError(f"duplicate run-locked path {path}")
        seen_paths.add(path)
        if entry["exists_at_source"] is not True:
            raise BenchmarkError(
                f"run-locked path {path} must be explicitly present at source"
            )
        paths.append(_record_from_entry(entry))
    if not paths:
        raise BenchmarkError("run-locked closure must not be empty")

    calculated_digest = generation_identity_digest(
        source_sha,
        [
            {
                "path": entry.path,
                "role": entry.role,
                "projection_action": entry.projection_action,
                "projection_reason": entry.projection_reason,
                "exists_at_source": entry.exists_at_source,
                "blob_id": entry.blob_id,
                "sha256": entry.sha256,
                "file_mode": entry.file_mode,
            }
            for entry in paths
        ],
    )
    if raw["generation_identity_digest"] != calculated_digest:
        raise BenchmarkError(
            "run-locked generation identity digest does not match its closure"
        )
    return RunLockedManifest(
        generation_id=raw["generation_id"],
        agent_identity=agent_identity,
        source_selector=dict(source_selector),
        benchmark_base_sha=source_sha,
        generated_by=raw["generated_by"],
        generated_at_utc=raw["generated_at_utc"],
        generation_identity_digest=raw["generation_identity_digest"],
        paths=tuple(paths),
        validation_contract=raw["validation_contract"],
        invocation_contract=raw["invocation_contract"],
        raw=raw,
    )


def parse_manifest(manifest_path: Path) -> PinnedManifest | RunLockedManifest:
    """Parse by the explicit ``kind`` discriminator; never guess the schema."""
    raw = load_json(manifest_path)
    kind = raw.get("kind") if isinstance(raw, dict) else None
    if kind == "pinned":
        return parse_pinned_manifest(manifest_path)
    if kind == "run_locked":
        return parse_run_locked_manifest(manifest_path)
    raise BenchmarkError(f"unsupported manifest kind {kind!r} (fail closed)")


def materialize(
    manifest: PinnedManifest | RunLockedManifest,
    repo_root: Path,
    store: Path,
    *,
    expected_source_sha: str | None = None,
) -> dict[str, Any]:
    """Extract either validated manifest kind into an isolated fixture store.

    Returns the deterministic bundle record.  The bundle tree identity covers
    all materialized (path, sha256, file mode) entries.
    """
    gates: list[dict[str, Any]] = []
    source_sha = (
        manifest.workflow_source_sha
        if isinstance(manifest, PinnedManifest)
        else manifest.benchmark_base_sha
    )
    if expected_source_sha is not None and source_sha != expected_source_sha:
        gates.append(
            {
                "name": "source_sha_mismatch",
                "status": "fail",
                "detail": (
                    f"source SHA mismatch: manifest source {source_sha} does not match expected "
                    f"source {expected_source_sha}"
                ),
            }
        )

    # Resolve only the already-locked full source SHA.  In particular, this
    # never resolves BENCHMARK_BASE_SHA, HEAD, or another mutable ref.
    if not gates:
        try:
            resolved_source = git_rev_parse(repo_root, source_sha)
        except BenchmarkError as exc:
            gates.append(
                {
                    "name": "source_path_missing",
                    "status": "fail",
                    "detail": (
                        f"source_path_missing: locked source {source_sha} "
                        f"unavailable: {exc}"
                    ),
                }
            )
        else:
            if resolved_source != source_sha:
                gates.append(
                    {
                        "name": "manifest_source_identity_mismatch",
                        "status": "fail",
                        "detail": (
                            f"locked source {source_sha} resolves to {resolved_source}"
                        ),
                    }
                )

    if gates:
        raise BenchmarkError(
            "materializer fail-closed: " + "; ".join(gate["detail"] for gate in gates)
        )

    verified: list[MaterializationRecord] = []

    for entry in manifest.paths:
        path = entry.path
        if not entry.blob_id:
            continue  # absent-at-source declaration, nothing to materialize
        source_entry = git_ls_tree_blob(repo_root, source_sha, path)
        if source_entry is None:
            gates.append(
                {
                    "name": "source_path_missing",
                    "status": "fail",
                    "detail": f"path {path} absent at {source_sha}",
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
        verified.append(entry)

    if any(gate["status"] == "fail" for gate in gates):
        raise BenchmarkError(
            "materializer fail-closed: " + "; ".join(g["detail"] for g in gates)
        )

    written = [
        {
            "path": entry.path,
            "role": entry.role,
            "projection_action": entry.projection_action,
            "projection_reason": entry.projection_reason,
            "exists_at_source": entry.exists_at_source,
            "blob_id": entry.blob_id,
            "sha256": entry.sha256,
            "file_mode": entry.file_mode,
        }
        for entry in verified
    ]
    identity = tree_identity(
        (item["path"], item["sha256"], item["file_mode"]) for item in written
    )
    identity_digest = generation_identity_digest(source_sha, written)
    if isinstance(manifest, RunLockedManifest):
        if identity_digest != manifest.generation_identity_digest:
            raise BenchmarkError(
                "materializer fail-closed: run-locked identity changed during "
                "materialization"
            )

    bundle: dict[str, Any] = {
        "protocol_identity": "task-65-round-2-v2",
        "schema_version": 1,
        "kind": "fixture_bundle",
        "manifest_kind": "pinned"
        if isinstance(manifest, PinnedManifest)
        else "run_locked",
        "generation_id": manifest.generation_id,
        "agent_identity": manifest.agent_identity,
        "workflow_source_sha": source_sha,
        "source_sha": source_sha,
        "source_label": (
            manifest.source_label
            if isinstance(manifest, PinnedManifest)
            else "BENCHMARK_BASE_SHA"
        ),
        "manifest_sha256": sha256_bytes(
            json.dumps(manifest.raw, sort_keys=True).encode("utf-8")
        ),
        "tree_identity": identity,
        "generation_identity_digest": identity_digest,
        "file_count": len(written),
        "files": [
            {
                key: value
                for key, value in item.items()
                if key
                in {
                    "path",
                    "role",
                    "projection_action",
                    "projection_reason",
                    "exists_at_source",
                    "blob_id",
                    "sha256",
                    "file_mode",
                }
            }
            for item in written
        ],
    }
    if isinstance(manifest, RunLockedManifest):
        bundle["run_lock"] = {
            "source_selector": manifest.source_selector,
            "benchmark_base_sha": manifest.benchmark_base_sha,
            "generated_by": manifest.generated_by,
            "generated_at_utc": manifest.generated_at_utc,
        }

    _prepare_store(store, bundle, verified, repo_root)
    return bundle


def _mode_bits(mode: str) -> int:
    if mode == "100644":
        return 0o644
    if mode == "100755":
        return 0o755
    raise BenchmarkError(f"unsupported git file mode {mode!r} (fail closed)")


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise BenchmarkError(f"symlink is forbidden in fixture store: {path}")


def _validate_destination(
    destination: Path, store: Path, mode: str, data: bytes
) -> None:
    """Validate existing output and every parent before any write occurs."""
    current = destination
    while True:
        _reject_symlink(current)
        if current == store:
            break
        if current == current.parent:
            break
        current = current.parent
    if destination.exists():
        if not destination.is_file():
            raise BenchmarkError(
                f"fixture store collision at {destination}: not a regular file"
            )
        if sha256_file(destination) != sha256_bytes(data):
            raise BenchmarkError(
                f"fixture store collision at {destination}: existing content "
                "differs from manifest (refusing to overwrite)"
            )
        if stat.S_IMODE(destination.stat().st_mode) != _mode_bits(mode):
            raise BenchmarkError(
                f"fixture store collision at {destination}: file mode differs"
            )


def _prepare_store(
    store: Path,
    bundle: Mapping[str, Any],
    entries: list[MaterializationRecord],
    repo_root: Path,
) -> None:
    """Bind a store to one generation and then write only verified blobs."""
    _reject_symlink(store)
    if store.exists() and not store.is_dir():
        raise BenchmarkError(f"fixture store is not a directory: {store}")
    store.mkdir(parents=True, exist_ok=True)
    metadata = store / MATERIALIZATION_METADATA
    _reject_symlink(metadata)
    marker = {
        "protocol_identity": bundle["protocol_identity"],
        "manifest_kind": bundle["manifest_kind"],
        "generation_id": bundle["generation_id"],
        "source_sha": bundle["source_sha"],
        "generation_identity_digest": bundle["generation_identity_digest"],
        "tree_identity": bundle["tree_identity"],
    }
    if metadata.exists():
        try:
            existing = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BenchmarkError(f"invalid fixture store metadata: {metadata}") from exc
        if existing != marker:
            raise BenchmarkError(
                "fixture store is already bound to a different generation "
                "or locked identity (refusing cross-generation reuse)"
            )
    elif any(store.iterdir()):
        raise BenchmarkError(
            "fixture store contains files without materializer metadata "
            "(refusing ambiguous reuse)"
        )

    # All blob bytes were checked before this function.  Re-read only from the
    # locked object IDs when writing; no path or mutable tree is consulted.
    for entry in entries:
        destination = store / entry.path
        data = git_cat_blob(repo_root, entry.blob_id)
        _validate_destination(destination, store, entry.file_mode, data)

    for entry in entries:
        destination = store / entry.path
        data = git_cat_blob(repo_root, entry.blob_id)
        _write_idempotent(destination, data, entry.file_mode)
    if not metadata.exists():
        write_bytes_verbatim(
            metadata,
            (json.dumps(marker, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            "100644",
        )


def _write_idempotent(destination: Path, data: bytes, mode: str) -> None:
    """Write verbatim; a pre-existing identical file is an idempotent no-op."""
    if destination.exists():
        _reject_symlink(destination)
        if not destination.is_file():
            raise BenchmarkError(f"fixture store collision at {destination}")
        if sha256_file(destination) != sha256_bytes(data):
            raise BenchmarkError(f"fixture store collision at {destination}")
        if stat.S_IMODE(destination.stat().st_mode) != _mode_bits(mode):
            raise BenchmarkError(f"fixture store mode mismatch at {destination}")
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
        "--expected-source-sha",
        help="optional full SHA expected by the caller; mismatches fail closed",
    )
    parser.add_argument(
        "--out-record", help="optional output path for the bundle record"
    )
    args = parser.parse_args(argv)

    try:
        repo_root = Path(args.repo_root).resolve()
        manifest = parse_manifest(Path(args.manifest))
        store = Path(args.store).resolve()
        bundle = materialize(
            manifest,
            repo_root,
            store,
            expected_source_sha=args.expected_source_sha,
        )
    except BenchmarkError as exc:
        print(f"MATERIALIZER FAIL CLOSED: {exc}", file=sys.stderr)
        return 1

    digest = {
        "status": "pass",
        "generation_id": bundle["generation_id"],
        "tree_identity": bundle["tree_identity"],
        "generation_identity_digest": bundle["generation_identity_digest"],
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
