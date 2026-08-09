"""Shared helpers for the task-65-round-2-v2 benchmark tooling.

The module is dependency-free (stdlib only) and deterministic: the same input
always yields the same output.  Git access is limited to read-only object
queries used to pin and verify generation fixtures.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

PROTOCOL_IDENTITY: str = "task-65-round-2-v2"
SCHEMA_VERSION: int = 1

# Role classification values defined by the Task #125 generation model.
ROLES: frozenset[str] = frozenset(
    {
        "EXECUTION_REQUIRED",
        "VALIDATION_PRESENCE_REQUIRED",
        "IDENTITY_REQUIRED",
        "OPTIONAL_HISTORICAL_LIMITATION",
    }
)

# Three-state runtime projection actions defined by the Runtime Projection
# Model.  ``runtime_install = false`` alone is not a decision; keep-vs-delete
# is always given explicitly by INHERIT_BUSINESS_BASE / ENSURE_ABSENT.
PROJECTION_ACTIONS: frozenset[str] = frozenset(
    {
        "INSTALL_GENERATION_VERSION",
        "INHERIT_BUSINESS_BASE",
        "ENSURE_ABSENT",
    }
)

MANIFEST_KINDS: frozenset[str] = frozenset({"pinned", "template", "run_locked"})

# Default conductor-local fixture store (Git-ignored, never committed).
DEFAULT_FIXTURE_STORE: str = ".agents/benchmark-fixtures.local"

SHA256_HEX: int = 64
GIT_SHA_HEX: int = 40


class BenchmarkError(Exception):
    """Raised on a deterministic tooling failure (fail-closed)."""


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of the file at ``path``."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    """Load and parse a JSON document; fail closed on any error."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"cannot load JSON from {path}: {exc}") from exc


def run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one read-only git command against ``repo_root``."""
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def git_ls_tree_blob(repo_root: Path, commit: str, path: str) -> tuple[str, str] | None:
    """Return (mode, blob_id) of ``path`` at ``commit`` or None when absent."""
    result = run_git(repo_root, "ls-tree", commit, "--", path)
    if result.returncode != 0:
        raise BenchmarkError(f"git ls-tree failed for {path}: {result.stderr.strip()}")
    line = result.stdout.strip()
    if not line:
        return None
    # Format: <mode> <type> <object>\t<path>
    parts = line.split("\t", 1)[0].split()
    if len(parts) != 3 or parts[1] != "blob":
        raise BenchmarkError(f"unexpected ls-tree entry for {path}: {line!r}")
    return parts[0], parts[2]


def git_cat_blob(repo_root: Path, blob_id: str) -> bytes:
    """Return the verbatim bytes of git blob ``blob_id``."""
    result = subprocess.run(
        ["git", "cat-file", "blob", blob_id],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise BenchmarkError(f"git cat-file failed for blob {blob_id}: {detail}")
    return result.stdout


def git_rev_parse(repo_root: Path, rev: str) -> str:
    """Resolve ``rev`` to a full object SHA; fail closed when unresolvable."""
    result = run_git(repo_root, "rev-parse", f"{rev}^{{commit}}")
    if result.returncode != 0:
        raise BenchmarkError(f"cannot resolve {rev!r}: {result.stderr.strip()}")
    value = result.stdout.strip()
    if len(value) != GIT_SHA_HEX:
        raise BenchmarkError(f"unexpected rev-parse result for {rev!r}: {value!r}")
    return value


def git_object_exists(repo_root: Path, object_id: str) -> bool:
    """Return whether ``object_id`` is present in the local object database."""
    result = subprocess.run(
        ["git", "cat-file", "-e", object_id],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def write_bytes_verbatim(dest: Path, data: bytes, mode: str) -> None:
    """Write ``data`` to ``dest`` byte-for-byte with the given git file mode.

    The destination file mode is validated against the source blob mode
    (``100644`` -> 0o644, ``100755`` -> 0o755); anything else fails closed.
    """
    file_mode = _octal_mode(mode)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    dest.chmod(file_mode)


def _octal_mode(mode: str) -> int:
    if mode == "100644":
        return 0o644
    if mode == "100755":
        return 0o755
    raise BenchmarkError(f"unsupported git file mode {mode!r} (fail closed)")


def tree_identity(entries: Iterable[tuple[str, str, str]]) -> str:
    """Deterministic tree identity over sorted (path, sha256, mode) entries."""
    lines = [f"{path}\t{digest}\t{mode}" for path, digest, mode in entries]
    return sha256_bytes("\n".join(sorted(lines)).encode("utf-8"))


def validate_basic(data: Any, schema: Mapping[str, Any], label: str) -> None:
    """Minimal dependency-free structural validation against our schemas.

    Supports ``type``, ``required``, ``properties``, ``items``, ``enum`` and
    ``additionalProperties``.  This is intentionally small: the benchmark
    schemas are flat and deterministic.  Fail closed on any violation.
    """
    if isinstance(schema.get("enum"), list):
        if data not in schema["enum"]:
            _schema_fail(label, f"value {data!r} not in enum {schema['enum']}")
        return
    expected = schema.get("type")
    if isinstance(expected, list):
        # e.g. ["string", "null"]: match any of the listed types.
        if not any(_matches_type(data, candidate) for candidate in expected):
            _schema_fail(
                label,
                f"expected one of {expected}, got {type(data).__name__}",
            )
        return
    if expected == "object":
        if not isinstance(data, Mapping):
            _schema_fail(label, f"expected object, got {type(data).__name__}")
        mapping = data
        for prop in schema.get("required", []):
            if prop not in mapping:
                _schema_fail(label, f"missing required property {prop!r}")
        properties = schema.get("properties", {})
        for prop, subschema in properties.items():
            if prop in mapping:
                validate_basic(mapping[prop], subschema, f"{label}.{prop}")
        if not schema.get("additionalProperties", True):
            # Keys not declared in properties are allowed when they are
            # explicitly listed as optional extension keys.
            allowed_extra = set(schema.get("x-allowed-extra", []))
            unexpected = sorted(set(mapping) - set(properties) - set(allowed_extra))
            if unexpected:
                _schema_fail(label, f"unexpected properties: {unexpected}")
    elif expected == "array":
        if not isinstance(data, list):
            _schema_fail(label, f"expected array, got {type(data).__name__}")
        items_schema = schema.get("items")
        if items_schema:
            for index, item in enumerate(data):
                validate_basic(item, items_schema, f"{label}[{index}]")
    elif expected == "string":
        if not isinstance(data, str):
            _schema_fail(label, f"expected string, got {type(data).__name__}")
        if "pattern" in schema:
            import re

            if re.fullmatch(schema["pattern"], data) is None:
                _schema_fail(
                    label, f"value {data!r} does not match {schema['pattern']}"
                )
        if "minLength" in schema and len(data) < schema["minLength"]:
            _schema_fail(label, f"value shorter than {schema['minLength']}")
    elif expected == "integer":
        if not isinstance(data, int) or isinstance(data, bool):
            _schema_fail(label, f"expected integer, got {type(data).__name__}")
    elif expected == "boolean":
        if not isinstance(data, bool):
            _schema_fail(label, f"expected boolean, got {type(data).__name__}")
    elif expected == "null":
        if data is not None:
            _schema_fail(label, f"expected null, got {type(data).__name__}")
    elif expected is None:
        return
    else:
        _schema_fail(label, f"unsupported schema type {expected!r}")


def _matches_type(data: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(data, Mapping)
    if expected == "array":
        return isinstance(data, list)
    if expected == "string":
        return isinstance(data, str)
    if expected == "integer":
        return isinstance(data, int) and not isinstance(data, bool)
    if expected == "boolean":
        return isinstance(data, bool)
    if expected == "null":
        return data is None
    return False


def _schema_fail(label: str, message: str) -> None:
    raise BenchmarkError(f"schema violation at {label}: {message}")


def bounded_text(value: str, limit: int = 512) -> str:
    """Bound a string for normalized audit events (bounded output contract)."""
    if len(value) <= limit:
        return value
    return value[:limit] + f"...<truncated:{len(value) - limit} bytes>"


def gate(name: str, status: str, detail: str | None = None) -> dict[str, Any]:
    """Build a normalized gate result record."""
    if status not in {"pass", "fail", "unknown"}:
        raise BenchmarkError(f"invalid gate status {status!r}")
    result: dict[str, Any] = {"name": name, "status": status}
    if detail is not None:
        result["detail"] = detail
    return result


def sequence_json_digest(sequence: Sequence[Mapping[str, Any]]) -> str:
    """Deterministic digest over a sequence of normalized records."""
    return sha256_bytes(
        json.dumps(list(sequence), sort_keys=True, ensure_ascii=False).encode("utf-8")
    )
