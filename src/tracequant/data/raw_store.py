"""Immutable local persistence for parsed public-history source objects.

The store deliberately accepts an already parsed Polars frame.  It does not
download, normalize, repair, aggregate, or otherwise reinterpret source data.
Filesystem work occurs only when a public method is called.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Self

import polars as pl

from tracequant.core.time import format_utc, parse_utc, to_utc
from tracequant.data.public_history import (
    BinancePublicHistoryRequest,
    BinancePublicHistorySourceIdentity,
    BinancePublicHistorySourceKind,
)
from tracequant.domain import TimeRange

__all__ = [
    "RawArtifact",
    "RawArtifactConflictError",
    "RawArtifactNotFoundError",
    "RawArtifactValidationError",
    "RawManifest",
    "RawObjectIdentity",
    "RawSourceObject",
    "RawStore",
    "RawStoreError",
]

_LAYOUT_VERSION: Final = "v1"
_MANIFEST_VERSION: Final = 1
_DATA_FILENAME: Final = "data.parquet"
_MANIFEST_FILENAME: Final = "manifest.json"


class RawStoreError(Exception):
    """Base error for Raw persistence operations."""


class RawArtifactConflictError(RawStoreError):
    """Raised when an immutable identity already has different content."""


class RawArtifactNotFoundError(RawStoreError):
    """Raised when a final Raw artifact is incomplete or absent."""


class RawArtifactValidationError(RawStoreError):
    """Raised when a completed Raw artifact fails integrity validation."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _exact_mapping(
    value: object, *, fields: frozenset[str], model: str
) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise RawArtifactValidationError(f"{model} must be a JSON object")
    missing = fields - set(value)
    extra = set(value) - fields
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing fields: {', '.join(sorted(missing))}")
        if extra:
            details.append(f"extra fields: {', '.join(sorted(extra))}")
        raise RawArtifactValidationError(
            f"invalid {model} fields ({'; '.join(details)})"
        )
    return value


def _require_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RawArtifactValidationError(f"{field} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class RawObjectIdentity:
    """Stable local identity for one upstream public-history object.

    Archive requests already carry a day/month source boundary.  REST has no
    archive boundary, so its caller UTC range is the object boundary instead.
    """

    source: BinancePublicHistorySourceIdentity
    rest_request_range: TimeRange | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, BinancePublicHistorySourceIdentity):
            raise TypeError("source must be a BinancePublicHistorySourceIdentity")
        is_rest = self.source.source_kind is BinancePublicHistorySourceKind.REST
        if is_rest != (self.rest_request_range is not None):
            raise ValueError(
                "REST Raw identity requires a request range and archive identity forbids it"
            )
        if self.rest_request_range is not None and not isinstance(
            self.rest_request_range, TimeRange
        ):
            raise TypeError("rest_request_range must be a TimeRange")

    @classmethod
    def from_request(cls, request: BinancePublicHistoryRequest) -> Self:
        """Derive object identity without reinterpreting source semantics."""
        if not isinstance(request, BinancePublicHistoryRequest):
            raise TypeError("request must be a BinancePublicHistoryRequest")
        return cls(
            source=request.source_identity,
            rest_request_range=(
                request.request_range
                if request.source_kind is BinancePublicHistorySourceKind.REST
                else None
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source.to_dict(),
            "rest_request_range": (
                self.rest_request_range.to_dict()
                if self.rest_request_range is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _exact_mapping(
            value,
            fields=frozenset({"source", "rest_request_range"}),
            model="RawObjectIdentity",
        )
        range_value = fields["rest_request_range"]
        return cls(
            source=BinancePublicHistorySourceIdentity.from_dict(fields["source"]),
            rest_request_range=(
                None if range_value is None else TimeRange.from_dict(range_value)
            ),
        )

    @property
    def object_id(self) -> str:
        """Return a platform-independent identifier from canonical fields."""
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("ascii")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class RawSourceObject:
    """An already parsed source frame plus its acquisition evidence."""

    request: BinancePublicHistoryRequest
    rows: pl.DataFrame
    actual_record_range: TimeRange
    raw_schema_identifier: str
    producer_version: str
    upstream_checksum: str | None = None
    upstream_revision: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, BinancePublicHistoryRequest):
            raise TypeError("request must be a BinancePublicHistoryRequest")
        if not isinstance(self.rows, pl.DataFrame):
            raise TypeError("rows must be a polars DataFrame")
        if not isinstance(self.actual_record_range, TimeRange):
            raise TypeError("actual_record_range must be a TimeRange")
        for field in ("raw_schema_identifier", "producer_version"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field} must be a non-empty string")
        for field in ("upstream_checksum", "upstream_revision"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{field} must be None or a non-empty string")

    @property
    def identity(self) -> RawObjectIdentity:
        return RawObjectIdentity.from_request(self.request)


@dataclass(frozen=True, slots=True)
class RawManifest:
    """Versioned integrity and provenance record for one Raw object."""

    manifest_schema_version: int
    completed: bool
    object_identity: RawObjectIdentity
    caller_request_range: TimeRange
    actual_record_range: TimeRange
    record_count: int
    parquet_file_size: int
    project_sha256: str
    upstream_checksum: str | None
    upstream_revision: str | None
    raw_schema_identifier: str
    producer_version: str
    created_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_schema_version": self.manifest_schema_version,
            "completed": self.completed,
            "object_identity": self.object_identity.to_dict(),
            "caller_request_range": self.caller_request_range.to_dict(),
            "actual_record_range": self.actual_record_range.to_dict(),
            "record_count": self.record_count,
            "parquet_file_size": self.parquet_file_size,
            "project_sha256": self.project_sha256,
            "upstream_checksum": self.upstream_checksum,
            "upstream_revision": self.upstream_revision,
            "raw_schema_identifier": self.raw_schema_identifier,
            "producer_version": self.producer_version,
            "created_at": format_utc(self.created_at),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        names = frozenset(
            {
                "manifest_schema_version",
                "completed",
                "object_identity",
                "caller_request_range",
                "actual_record_range",
                "record_count",
                "parquet_file_size",
                "project_sha256",
                "upstream_checksum",
                "upstream_revision",
                "raw_schema_identifier",
                "producer_version",
                "created_at",
            }
        )
        fields = _exact_mapping(value, fields=names, model="RawManifest")
        version = fields["manifest_schema_version"]
        if type(version) is not int or version != _MANIFEST_VERSION:
            raise RawArtifactValidationError(
                f"unsupported manifest schema version {version!r}"
            )
        if fields["completed"] is not True:
            raise RawArtifactValidationError("manifest is not completed")
        record_count = fields["record_count"]
        file_size = fields["parquet_file_size"]
        if type(record_count) is not int or record_count < 0:
            raise RawArtifactValidationError(
                "record_count must be a non-negative integer"
            )
        if type(file_size) is not int or file_size < 0:
            raise RawArtifactValidationError(
                "parquet_file_size must be a non-negative integer"
            )
        nullable_strings: dict[str, str | None] = {}
        for name in ("upstream_checksum", "upstream_revision"):
            item = fields[name]
            if item is not None and (not isinstance(item, str) or not item):
                raise RawArtifactValidationError(
                    f"{name} must be null or a non-empty string"
                )
            nullable_strings[name] = item
        try:
            created_at = parse_utc(
                _require_string(fields["created_at"], field="created_at")
            )
        except ValueError as error:
            raise RawArtifactValidationError(
                "created_at must be a timezone-aware UTC datetime"
            ) from error
        checksum = _require_string(fields["project_sha256"], field="project_sha256")
        if len(checksum) != 64 or any(
            character not in "0123456789abcdef" for character in checksum
        ):
            raise RawArtifactValidationError(
                "project_sha256 must be a lowercase SHA-256 digest"
            )
        try:
            object_identity = RawObjectIdentity.from_dict(fields["object_identity"])
            caller_request_range = TimeRange.from_dict(fields["caller_request_range"])
            actual_record_range = TimeRange.from_dict(fields["actual_record_range"])
        except (TypeError, ValueError) as error:
            raise RawArtifactValidationError(
                "manifest contains invalid typed identity or UTC range fields"
            ) from error
        return cls(
            manifest_schema_version=version,
            completed=True,
            object_identity=object_identity,
            caller_request_range=caller_request_range,
            actual_record_range=actual_record_range,
            record_count=record_count,
            parquet_file_size=file_size,
            project_sha256=checksum,
            upstream_checksum=nullable_strings["upstream_checksum"],
            upstream_revision=nullable_strings["upstream_revision"],
            raw_schema_identifier=_require_string(
                fields["raw_schema_identifier"], field="raw_schema_identifier"
            ),
            producer_version=_require_string(
                fields["producer_version"], field="producer_version"
            ),
            created_at=created_at,
        )


@dataclass(frozen=True, slots=True)
class RawArtifact:
    """A verified completed Raw artifact."""

    path: Path
    frame: pl.DataFrame
    manifest: RawManifest

    @property
    def data_path(self) -> Path:
        return self.path / _DATA_FILENAME

    @property
    def manifest_path(self) -> Path:
        return self.path / _MANIFEST_FILENAME


class RawStore:
    """Filesystem-backed immutable Raw Parquet object store."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._root = Path(root)
        self._clock = clock or (lambda: datetime.now(UTC))

    def relative_path(self, identity: RawObjectIdentity) -> Path:
        """Return the controlled, platform-neutral path for an identity."""
        if not isinstance(identity, RawObjectIdentity):
            raise TypeError("identity must be a RawObjectIdentity")
        source = identity.source
        interval = source.interval.value if source.interval is not None else "none"
        boundary = source.archive_object_boundary
        boundary_kind = (
            boundary.granularity.value if boundary is not None else "request"
        )
        boundary_value = (
            boundary.period_start.isoformat() if boundary is not None else "utc-range"
        )
        return Path(
            "raw",
            _LAYOUT_VERSION,
            "binance",
            source.market.value,
            str(source.instrument),
            source.data_type.value,
            interval,
            source.source_kind.value,
            boundary_kind,
            boundary_value,
            identity.object_id,
        )

    def path_for(self, identity: RawObjectIdentity) -> Path:
        return self._root / self.relative_path(identity)

    def write(self, source_object: RawSourceObject) -> RawArtifact:
        """Atomically publish or idempotently return one verified Raw object."""
        if not isinstance(source_object, RawSourceObject):
            raise TypeError("source_object must be a RawSourceObject")
        identity = source_object.identity
        final_path = self.path_for(identity)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = Path(
            tempfile.mkdtemp(
                prefix=f".{identity.object_id}.tmp-", dir=final_path.parent
            )
        )
        try:
            data_path = temporary_path / _DATA_FILENAME
            source_object.rows.write_parquet(data_path)
            checksum = _sha256(data_path)
            created_at = to_utc(self._clock())
            manifest = RawManifest(
                manifest_schema_version=_MANIFEST_VERSION,
                completed=True,
                object_identity=identity,
                caller_request_range=source_object.request.request_range,
                actual_record_range=source_object.actual_record_range,
                record_count=source_object.rows.height,
                parquet_file_size=data_path.stat().st_size,
                project_sha256=checksum,
                upstream_checksum=source_object.upstream_checksum,
                upstream_revision=source_object.upstream_revision,
                raw_schema_identifier=source_object.raw_schema_identifier,
                producer_version=source_object.producer_version,
                created_at=created_at,
            )
            manifest_path = temporary_path / _MANIFEST_FILENAME
            manifest_path.write_text(
                _canonical_json(manifest.to_dict()) + "\n", encoding="utf-8"
            )
            self._sync_file(data_path)
            self._sync_file(manifest_path)
            self._validate_path(temporary_path, expected_identity=identity)
            self._sync_directory(temporary_path)

            if final_path.exists():
                return self._resolve_existing(final_path, manifest)
            try:
                self._publish_directory(temporary_path, final_path)
            except OSError:
                if not final_path.exists():
                    raise
                return self._resolve_existing(final_path, manifest)
            self._sync_directory(final_path.parent)
            return self.read(identity)
        finally:
            if temporary_path.exists():
                shutil.rmtree(temporary_path)

    def read(self, identity: RawObjectIdentity) -> RawArtifact:
        """Open a completed artifact and revalidate all persisted components."""
        if not isinstance(identity, RawObjectIdentity):
            raise TypeError("identity must be a RawObjectIdentity")
        path = self.path_for(identity)
        if not path.is_dir():
            raise RawArtifactNotFoundError(f"Raw artifact is not complete: {path}")
        return self._validate_path(path, expected_identity=identity)

    def read_request(self, request: BinancePublicHistoryRequest) -> RawArtifact:
        return self.read(RawObjectIdentity.from_request(request))

    def _resolve_existing(
        self, final_path: Path, candidate_manifest: RawManifest
    ) -> RawArtifact:
        existing = self._validate_path(
            final_path, expected_identity=candidate_manifest.object_identity
        )
        comparable = (
            "actual_record_range",
            "record_count",
            "parquet_file_size",
            "project_sha256",
            "upstream_checksum",
            "upstream_revision",
            "raw_schema_identifier",
            "producer_version",
        )
        if any(
            getattr(existing.manifest, field) != getattr(candidate_manifest, field)
            for field in comparable
        ):
            raise RawArtifactConflictError(
                f"Raw identity {candidate_manifest.object_identity.object_id} "
                "already exists with different content or provenance"
            )
        return existing

    def _validate_path(
        self, path: Path, *, expected_identity: RawObjectIdentity
    ) -> RawArtifact:
        data_path = path / _DATA_FILENAME
        manifest_path = path / _MANIFEST_FILENAME
        if not data_path.is_file() or not manifest_path.is_file():
            raise RawArtifactNotFoundError(
                "Raw artifact requires both data.parquet and manifest.json"
            )
        try:
            manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RawArtifactValidationError(
                "manifest is not valid UTF-8 JSON"
            ) from error
        manifest = RawManifest.from_dict(manifest_value)
        if manifest.object_identity != expected_identity:
            raise RawArtifactValidationError(
                "manifest object identity does not match path"
            )
        size = data_path.stat().st_size
        if size != manifest.parquet_file_size:
            raise RawArtifactValidationError(
                "Parquet file size does not match manifest"
            )
        if _sha256(data_path) != manifest.project_sha256:
            raise RawArtifactValidationError("Parquet checksum does not match manifest")
        try:
            frame = pl.read_parquet(data_path)
        except Exception as error:
            raise RawArtifactValidationError("Parquet data cannot be read") from error
        if frame.height != manifest.record_count:
            raise RawArtifactValidationError(
                "Parquet record count does not match manifest"
            )
        return RawArtifact(path=path, frame=frame, manifest=manifest)

    @staticmethod
    def _sync_file(path: Path) -> None:
        with path.open("rb") as stream:
            os.fsync(stream.fileno())

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name != "nt":
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _publish_directory(self, temporary_path: Path, final_path: Path) -> None:
        os.rename(temporary_path, final_path)
