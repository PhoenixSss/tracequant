"""Immutable local persistence for parsed public-history source objects.

The store deliberately accepts an already parsed Polars frame and, when
provided, the exact upstream response bodies that produced it.  It does not
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
from types import MappingProxyType
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
    "RawArtifactIncompleteError",
    "RawArtifactNotFoundError",
    "RawArtifactValidationError",
    "RawAcquisitionManifest",
    "RawAcquisitionResponse",
    "RawManifest",
    "RawObjectIdentity",
    "RawSourceProvenance",
    "RawSourceObject",
    "RawStore",
    "RawStoreError",
]

_LAYOUT_VERSION: Final = "v1"
_LEGACY_MANIFEST_VERSION: Final = 1
_MANIFEST_VERSION: Final = 3
_DATA_FILENAME: Final = "data.parquet"
_MANIFEST_FILENAME: Final = "manifest.json"
_ARCHIVE_FILENAME: Final = "source.zip"
_CHECKSUM_FILENAME: Final = "source.CHECKSUM"
_ACQUISITION_DIRNAME: Final = "acquisition"
_ACQUISITION_MANIFEST_VERSION: Final = 1


class RawStoreError(Exception):
    """Base error for Raw persistence operations."""


class RawArtifactConflictError(RawStoreError):
    """Raised when an immutable identity already has different content."""


class RawArtifactNotFoundError(RawStoreError):
    """Raised when a final Raw artifact is absent."""


class RawArtifactIncompleteError(RawArtifactNotFoundError):
    """Raised when a final Raw artifact path exists but is incomplete."""


class RawArtifactValidationError(RawStoreError):
    """Raised when a completed Raw artifact fails integrity validation."""


@dataclass(frozen=True, slots=True)
class RawAcquisitionResponse:
    """One bounded HTTP response retained with an acquisition outcome."""

    status: int
    headers: Mapping[str, str]
    body: bytes | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not int or not 100 <= self.status <= 599:
            raise ValueError("status must be an HTTP status code")
        object.__setattr__(
            self, "headers", _normalize_headers(self.headers, field="headers")
        )
        if self.body is not None and not isinstance(self.body, bytes):
            raise TypeError("body must be bytes or None")

    @property
    def body_sha256(self) -> str | None:
        return None if self.body is None else hashlib.sha256(self.body).hexdigest()


def _optional_sha256(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, field=field)


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


def _optional_string(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field=field)


def _require_sha256(value: object, *, field: str) -> str:
    digest = _require_string(value, field=field)
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise RawArtifactValidationError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def _normalize_headers(value: object, *, field: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    headers: dict[str, str] = {}
    for name, header_value in value.items():
        if not isinstance(name, str) or not name:
            raise TypeError(f"{field} names must be non-empty strings")
        if not isinstance(header_value, str):
            raise TypeError(f"{field} values must be strings")
        headers[name] = header_value
    return MappingProxyType(headers)


def _require_http_status(value: object, *, field: str) -> int:
    if type(value) is not int or not 100 <= value <= 599:
        raise RawArtifactValidationError(f"{field} must be an HTTP status code")
    return value


def _optional_http_status(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    return _require_http_status(value, field=field)


@dataclass(frozen=True, slots=True)
class RawSourceProvenance:
    """Immutable acquisition evidence retained with one Raw source object."""

    object_key: str
    source_url: str
    acquired_at: datetime
    source_http_status: int
    source_http_headers: Mapping[str, str]
    checksum_url: str
    checksum_http_status: int
    checksum_http_headers: Mapping[str, str]
    checksum_response_sha256: str
    archive_sha256: str
    csv_member: str
    validation_evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in (
            "object_key",
            "source_url",
            "checksum_url",
            "csv_member",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field} must be a non-empty string")
        if not isinstance(self.acquired_at, datetime):
            raise TypeError("acquired_at must be a datetime")
        try:
            acquired_at = to_utc(self.acquired_at)
        except ValueError as error:
            raise ValueError("acquired_at must be timezone-aware") from error
        object.__setattr__(self, "acquired_at", acquired_at)
        for field in ("source_http_status", "checksum_http_status"):
            status = getattr(self, field)
            if type(status) is not int or not 100 <= status <= 599:
                raise ValueError(f"{field} must be an HTTP status code")
        for field in ("source_http_headers", "checksum_http_headers"):
            object.__setattr__(
                self,
                field,
                _normalize_headers(getattr(self, field), field=field),
            )
        for field in ("checksum_response_sha256", "archive_sha256"):
            digest = getattr(self, field)
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError(f"{field} must be a lowercase SHA-256 digest")
        if isinstance(self.validation_evidence, str):
            raise TypeError("validation_evidence must be a sequence of strings")
        try:
            evidence = tuple(self.validation_evidence)
        except TypeError as error:
            raise TypeError(
                "validation_evidence must be a sequence of strings"
            ) from error
        if not evidence or any(
            not isinstance(item, str) or not item for item in evidence
        ):
            raise ValueError("validation_evidence must contain non-empty strings")
        if len(set(evidence)) != len(evidence):
            raise ValueError("validation_evidence must not contain duplicates")
        object.__setattr__(self, "validation_evidence", evidence)

    def to_dict(self) -> dict[str, object]:
        return {
            "object_key": self.object_key,
            "source_url": self.source_url,
            "acquired_at": format_utc(self.acquired_at),
            "source_http_status": self.source_http_status,
            "source_http_headers": dict(self.source_http_headers),
            "checksum_url": self.checksum_url,
            "checksum_http_status": self.checksum_http_status,
            "checksum_http_headers": dict(self.checksum_http_headers),
            "checksum_response_sha256": self.checksum_response_sha256,
            "archive_sha256": self.archive_sha256,
            "csv_member": self.csv_member,
            "validation_evidence": list(self.validation_evidence),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _exact_mapping(
            value,
            fields=frozenset(
                {
                    "object_key",
                    "source_url",
                    "acquired_at",
                    "source_http_status",
                    "source_http_headers",
                    "checksum_url",
                    "checksum_http_status",
                    "checksum_http_headers",
                    "checksum_response_sha256",
                    "archive_sha256",
                    "csv_member",
                    "validation_evidence",
                }
            ),
            model="RawSourceProvenance",
        )
        try:
            acquired_at = parse_utc(
                _require_string(fields["acquired_at"], field="acquired_at")
            )
            source_http_headers = _normalize_headers(
                fields["source_http_headers"], field="source_http_headers"
            )
            checksum_http_headers = _normalize_headers(
                fields["checksum_http_headers"], field="checksum_http_headers"
            )
            raw_evidence = fields["validation_evidence"]
            if isinstance(raw_evidence, str) or not isinstance(raw_evidence, list):
                raise TypeError("validation_evidence must be a list of strings")
            if any(not isinstance(item, str) for item in raw_evidence):
                raise TypeError("validation_evidence must be a list of strings")
            validation_evidence: tuple[str, ...] = tuple(raw_evidence)
        except (TypeError, ValueError) as error:
            raise RawArtifactValidationError(
                "provenance contains invalid timestamp, headers, or validation evidence"
            ) from error
        try:
            return cls(
                object_key=_require_string(fields["object_key"], field="object_key"),
                source_url=_require_string(fields["source_url"], field="source_url"),
                acquired_at=acquired_at,
                source_http_status=_require_http_status(
                    fields["source_http_status"], field="source_http_status"
                ),
                source_http_headers=source_http_headers,
                checksum_url=_require_string(
                    fields["checksum_url"], field="checksum_url"
                ),
                checksum_http_status=_require_http_status(
                    fields["checksum_http_status"], field="checksum_http_status"
                ),
                checksum_http_headers=checksum_http_headers,
                checksum_response_sha256=_require_sha256(
                    fields["checksum_response_sha256"],
                    field="checksum_response_sha256",
                ),
                archive_sha256=_require_sha256(
                    fields["archive_sha256"], field="archive_sha256"
                ),
                csv_member=_require_string(fields["csv_member"], field="csv_member"),
                validation_evidence=validation_evidence,
            )
        except (TypeError, ValueError) as error:
            raise RawArtifactValidationError(
                "provenance contains invalid acquisition evidence"
            ) from error

    def matches_except_acquired_at(self, other: object) -> bool:
        """Compare stable evidence while ignoring acquisition metadata.

        HTTP headers are retained for auditability but are transport metadata;
        values such as ``Date`` may change between identical acquisitions and
        must not turn an immutable-content retry into a conflict.
        """
        if not isinstance(other, RawSourceProvenance):
            return False
        volatile_fields = {
            "acquired_at",
            "source_http_headers",
            "checksum_http_headers",
        }
        left = {
            key: value
            for key, value in self.to_dict().items()
            if key not in volatile_fields
        }
        right = {
            key: value
            for key, value in other.to_dict().items()
            if key not in volatile_fields
        }
        return left == right


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
class RawAcquisitionManifest:
    """Durable, non-completed state for one attempted Raw acquisition."""

    manifest_schema_version: int
    completed: bool
    object_identity: RawObjectIdentity
    caller_request_range: TimeRange
    status: str
    detail: str
    recorded_at: datetime
    source_url: str | None
    checksum_url: str | None
    source_http_status: int | None
    source_http_headers: Mapping[str, str]
    source_body_sha256: str | None
    checksum_http_status: int | None
    checksum_http_headers: Mapping[str, str]
    checksum_response_sha256: str | None

    def __post_init__(self) -> None:
        if self.manifest_schema_version != _ACQUISITION_MANIFEST_VERSION:
            raise ValueError("unsupported acquisition manifest schema version")
        if self.completed is not False:
            raise ValueError("acquisition manifest must not be completed")
        if not isinstance(self.object_identity, RawObjectIdentity):
            raise TypeError("object_identity must be a RawObjectIdentity")
        if not isinstance(self.caller_request_range, TimeRange):
            raise TypeError("caller_request_range must be a TimeRange")
        for field in ("status", "detail"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field} must be a non-empty string")
        if not isinstance(self.recorded_at, datetime):
            raise TypeError("recorded_at must be a datetime")
        try:
            recorded_at = to_utc(self.recorded_at)
        except ValueError as error:
            raise ValueError("recorded_at must be timezone-aware") from error
        object.__setattr__(self, "recorded_at", recorded_at)
        for field in ("source_url", "checksum_url"):
            value = getattr(self, field)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{field} must be None or a non-empty string")
        for field in ("source_http_status", "checksum_http_status"):
            status = getattr(self, field)
            if status is not None and (
                type(status) is not int or not 100 <= status <= 599
            ):
                raise ValueError(f"{field} must be None or an HTTP status code")
        for field in ("source_http_headers", "checksum_http_headers"):
            object.__setattr__(
                self,
                field,
                _normalize_headers(getattr(self, field), field=field),
            )
        for field in ("source_body_sha256", "checksum_response_sha256"):
            object.__setattr__(
                self,
                field,
                _optional_sha256(getattr(self, field), field=field),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_schema_version": self.manifest_schema_version,
            "completed": self.completed,
            "object_identity": self.object_identity.to_dict(),
            "caller_request_range": self.caller_request_range.to_dict(),
            "status": self.status,
            "detail": self.detail,
            "recorded_at": format_utc(self.recorded_at),
            "source_url": self.source_url,
            "checksum_url": self.checksum_url,
            "source_http_status": self.source_http_status,
            "source_http_headers": dict(self.source_http_headers),
            "source_body_sha256": self.source_body_sha256,
            "checksum_http_status": self.checksum_http_status,
            "checksum_http_headers": dict(self.checksum_http_headers),
            "checksum_response_sha256": self.checksum_response_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        fields = _exact_mapping(
            value,
            fields=frozenset(
                {
                    "manifest_schema_version",
                    "completed",
                    "object_identity",
                    "caller_request_range",
                    "status",
                    "detail",
                    "recorded_at",
                    "source_url",
                    "checksum_url",
                    "source_http_status",
                    "source_http_headers",
                    "source_body_sha256",
                    "checksum_http_status",
                    "checksum_http_headers",
                    "checksum_response_sha256",
                }
            ),
            model="RawAcquisitionManifest",
        )
        try:
            recorded_at = parse_utc(
                _require_string(fields["recorded_at"], field="recorded_at")
            )
            object_identity = RawObjectIdentity.from_dict(fields["object_identity"])
            caller_request_range = TimeRange.from_dict(fields["caller_request_range"])
        except (TypeError, ValueError) as error:
            raise RawArtifactValidationError(
                "acquisition manifest contains invalid identity, range, or timestamp"
            ) from error
        try:
            version = fields["manifest_schema_version"]
            if type(version) is not int:
                raise TypeError("manifest_schema_version must be an integer")
            completed = fields["completed"]
            if type(completed) is not bool:
                raise TypeError("completed must be a boolean")
            return cls(
                manifest_schema_version=version,
                completed=completed,
                object_identity=object_identity,
                caller_request_range=caller_request_range,
                status=_require_string(fields["status"], field="status"),
                detail=_require_string(fields["detail"], field="detail"),
                recorded_at=recorded_at,
                source_url=_optional_string(fields["source_url"], field="source_url"),
                checksum_url=_optional_string(
                    fields["checksum_url"], field="checksum_url"
                ),
                source_http_status=_optional_http_status(
                    fields["source_http_status"], field="source_http_status"
                ),
                source_http_headers=_normalize_headers(
                    fields["source_http_headers"], field="source_http_headers"
                ),
                source_body_sha256=_optional_sha256(
                    fields["source_body_sha256"], field="source_body_sha256"
                ),
                checksum_http_status=_optional_http_status(
                    fields["checksum_http_status"], field="checksum_http_status"
                ),
                checksum_http_headers=_normalize_headers(
                    fields["checksum_http_headers"], field="checksum_http_headers"
                ),
                checksum_response_sha256=_optional_sha256(
                    fields["checksum_response_sha256"],
                    field="checksum_response_sha256",
                ),
            )
        except (TypeError, ValueError) as error:
            raise RawArtifactValidationError(
                "acquisition manifest contains invalid outcome evidence"
            ) from error

    @property
    def record_id(self) -> str:
        """Return the deterministic identifier for this acquisition attempt."""
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("ascii")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class RawSourceObject:
    """An already parsed source frame plus its acquisition evidence.

    A source object with provenance must carry the exact checksum response and
    archive bodies.  The Raw store persists those bytes beside the parsed
    frame, while the provenance digests in the manifest bind the files to the
    acquisition that produced the frame.
    """

    request: BinancePublicHistoryRequest
    rows: pl.DataFrame
    actual_record_range: TimeRange
    raw_schema_identifier: str
    producer_version: str
    upstream_checksum: str | None = None
    upstream_revision: str | None = None
    provenance: RawSourceProvenance | None = None
    archive_payload: bytes | None = None
    checksum_response_body: bytes | None = None

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
        if self.provenance is not None and not isinstance(
            self.provenance, RawSourceProvenance
        ):
            raise TypeError("provenance must be a RawSourceProvenance or None")
        if self.provenance is None:
            if (
                self.archive_payload is not None
                or self.checksum_response_body is not None
            ):
                raise ValueError(
                    "source response bodies require acquisition provenance"
                )
        else:
            if not isinstance(self.archive_payload, bytes):
                raise TypeError("archive_payload must be bytes with provenance")
            if not isinstance(self.checksum_response_body, bytes):
                raise TypeError("checksum_response_body must be bytes with provenance")
            if (
                hashlib.sha256(self.archive_payload).hexdigest()
                != self.provenance.archive_sha256
            ):
                raise ValueError("archive_payload does not match provenance digest")
            if (
                hashlib.sha256(self.checksum_response_body).hexdigest()
                != self.provenance.checksum_response_sha256
            ):
                raise ValueError(
                    "checksum_response_body does not match provenance digest"
                )

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
    provenance: RawSourceProvenance | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
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
        if self.manifest_schema_version == _MANIFEST_VERSION:
            payload["provenance"] = (
                self.provenance.to_dict() if self.provenance is not None else None
            )
        return payload

    @classmethod
    def from_dict(cls, value: object) -> Self:
        common_names = frozenset(
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
        if not isinstance(value, dict):
            raise RawArtifactValidationError("RawManifest must be a JSON object")
        version = value.get("manifest_schema_version")
        if type(version) is not int or version not in {
            _LEGACY_MANIFEST_VERSION,
            _MANIFEST_VERSION,
        }:
            raise RawArtifactValidationError(
                f"unsupported manifest schema version {version!r}"
            )
        names = (
            common_names
            if version == _LEGACY_MANIFEST_VERSION
            else common_names | {"provenance"}
        )
        fields = _exact_mapping(value, fields=names, model="RawManifest")
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
            provenance_value = fields.get("provenance")
            provenance = (
                None
                if provenance_value is None
                else RawSourceProvenance.from_dict(provenance_value)
            )
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
            provenance=provenance,
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

    @property
    def archive_path(self) -> Path:
        """Return the persisted upstream archive body."""
        return self.path / _ARCHIVE_FILENAME

    @property
    def checksum_response_path(self) -> Path:
        """Return the persisted upstream checksum response body."""
        return self.path / _CHECKSUM_FILENAME


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

    def acquisition_path_for(self, identity: RawObjectIdentity) -> Path:
        """Return the root for durable non-completed acquisition attempts."""
        return self._root / _ACQUISITION_DIRNAME / self.relative_path(identity)

    def write_acquisition_manifest(
        self,
        manifest: RawAcquisitionManifest,
        *,
        source_response: RawAcquisitionResponse | None = None,
        checksum_response: RawAcquisitionResponse | None = None,
    ) -> RawAcquisitionManifest:
        """Persist one failed, gap, or quarantined acquisition attempt.

        Acquisition manifests are stored outside the completed Raw object path.
        Optional response bodies are retained beside the manifest as quarantine
        evidence, so this method never makes an incomplete attempt readable as
        a completed Raw artifact.
        """
        if not isinstance(manifest, RawAcquisitionManifest):
            raise TypeError("manifest must be a RawAcquisitionManifest")
        self._validate_acquisition_response(
            manifest,
            source_response,
            prefix="source",
        )
        self._validate_acquisition_response(
            manifest,
            checksum_response,
            prefix="checksum",
        )

        parent_path = self.acquisition_path_for(manifest.object_identity)
        parent_path.mkdir(parents=True, exist_ok=True)
        final_path = parent_path / manifest.record_id
        temporary_path = Path(
            tempfile.mkdtemp(prefix=f".{manifest.record_id}.tmp-", dir=parent_path)
        )
        try:
            if source_response is not None and source_response.body is not None:
                (temporary_path / _ARCHIVE_FILENAME).write_bytes(source_response.body)
            if checksum_response is not None and checksum_response.body is not None:
                (temporary_path / _CHECKSUM_FILENAME).write_bytes(
                    checksum_response.body
                )
            manifest_path = temporary_path / _MANIFEST_FILENAME
            manifest_path.write_text(
                _canonical_json(manifest.to_dict()) + "\n", encoding="utf-8"
            )
            if source_response is not None and source_response.body is not None:
                self._sync_file(temporary_path / _ARCHIVE_FILENAME)
            if checksum_response is not None and checksum_response.body is not None:
                self._sync_file(temporary_path / _CHECKSUM_FILENAME)
            self._sync_file(manifest_path)
            self._sync_directory(temporary_path)

            if final_path.exists():
                return self._read_acquisition_manifest(
                    final_path, expected_identity=manifest.object_identity
                )
            try:
                self._publish_directory(temporary_path, final_path)
            except OSError:
                if not final_path.exists():
                    raise
                return self._read_acquisition_manifest(
                    final_path, expected_identity=manifest.object_identity
                )
            self._sync_directory(parent_path)
            return self._read_acquisition_manifest(
                final_path, expected_identity=manifest.object_identity
            )
        finally:
            if temporary_path.exists():
                shutil.rmtree(temporary_path)

    def list_acquisition_manifests(
        self, identity: RawObjectIdentity
    ) -> tuple[RawAcquisitionManifest, ...]:
        """Read all durable acquisition outcomes for one Raw identity."""
        if not isinstance(identity, RawObjectIdentity):
            raise TypeError("identity must be a RawObjectIdentity")
        root = self.acquisition_path_for(identity)
        if not root.is_dir():
            return ()
        records = []
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            if not path.is_dir():
                raise RawArtifactValidationError(
                    "acquisition evidence contains an unexpected file"
                )
            records.append(
                self._read_acquisition_manifest(path, expected_identity=identity)
            )
        return tuple(records)

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
            if source_object.provenance is not None:
                archive_path = temporary_path / _ARCHIVE_FILENAME
                checksum_response_path = temporary_path / _CHECKSUM_FILENAME
                assert isinstance(source_object.archive_payload, bytes)
                assert isinstance(source_object.checksum_response_body, bytes)
                archive_path.write_bytes(source_object.archive_payload)
                checksum_response_path.write_bytes(source_object.checksum_response_body)
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
                provenance=source_object.provenance,
            )
            manifest_path = temporary_path / _MANIFEST_FILENAME
            manifest_path.write_text(
                _canonical_json(manifest.to_dict()) + "\n", encoding="utf-8"
            )
            self._sync_file(data_path)
            if source_object.provenance is not None:
                self._sync_file(archive_path)
                self._sync_file(checksum_response_path)
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
            if path.exists():
                raise RawArtifactIncompleteError(
                    f"Raw artifact is not a directory: {path}"
                )
            raise RawArtifactNotFoundError(f"Raw artifact does not exist: {path}")
        return self._validate_path(path, expected_identity=identity)

    def read_request(self, request: BinancePublicHistoryRequest) -> RawArtifact:
        return self.read(RawObjectIdentity.from_request(request))

    @staticmethod
    def _validate_acquisition_response(
        manifest: RawAcquisitionManifest,
        response: RawAcquisitionResponse | None,
        *,
        prefix: str,
    ) -> None:
        status_field = f"{prefix}_http_status"
        headers_field = f"{prefix}_http_headers"
        digest_field = (
            "source_body_sha256" if prefix == "source" else "checksum_response_sha256"
        )
        if response is None:
            if (
                getattr(manifest, status_field) is not None
                or getattr(manifest, headers_field)
                or getattr(manifest, digest_field) is not None
            ):
                raise ValueError(f"{prefix} evidence is missing its response")
            return
        if getattr(manifest, status_field) != response.status:
            raise ValueError(f"{prefix} response status does not match manifest")
        if dict(getattr(manifest, headers_field)) != dict(response.headers):
            raise ValueError(f"{prefix} response headers do not match manifest")
        if getattr(manifest, digest_field) != response.body_sha256:
            raise ValueError(f"{prefix} response digest does not match manifest")

    def _read_acquisition_manifest(
        self, path: Path, *, expected_identity: RawObjectIdentity
    ) -> RawAcquisitionManifest:
        manifest_path = path / _MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise RawArtifactNotFoundError(
                "acquisition evidence requires manifest.json"
            )
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RawArtifactValidationError(
                "acquisition manifest is not valid UTF-8 JSON"
            ) from error
        manifest = RawAcquisitionManifest.from_dict(value)
        if manifest.object_identity != expected_identity:
            raise RawArtifactValidationError(
                "acquisition manifest object identity does not match path"
            )
        for filename, digest in (
            (_ARCHIVE_FILENAME, manifest.source_body_sha256),
            (_CHECKSUM_FILENAME, manifest.checksum_response_sha256),
        ):
            body_path = path / filename
            if digest is None:
                if body_path.exists():
                    raise RawArtifactValidationError(
                        f"acquisition evidence has unexpected {filename}"
                    )
            elif not body_path.is_file():
                raise RawArtifactNotFoundError(
                    f"acquisition evidence requires {filename}"
                )
            elif _sha256(body_path) != digest:
                raise RawArtifactValidationError(
                    f"acquisition evidence {filename} checksum does not match manifest"
                )
        return manifest

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
        existing_provenance = existing.manifest.provenance
        candidate_provenance = candidate_manifest.provenance
        if existing_provenance is None or candidate_provenance is None:
            provenance_matches = existing_provenance is candidate_provenance
        else:
            provenance_matches = existing_provenance.matches_except_acquired_at(
                candidate_provenance
            )
        if not provenance_matches:
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
            raise RawArtifactIncompleteError(
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
        if manifest.provenance is not None:
            archive_path = path / _ARCHIVE_FILENAME
            checksum_response_path = path / _CHECKSUM_FILENAME
            if not archive_path.is_file() or not checksum_response_path.is_file():
                raise RawArtifactIncompleteError(
                    "Raw artifact with provenance requires source.zip and "
                    "source.CHECKSUM"
                )
            if _sha256(archive_path) != manifest.provenance.archive_sha256:
                raise RawArtifactValidationError(
                    "upstream archive checksum does not match provenance"
                )
            if (
                _sha256(checksum_response_path)
                != manifest.provenance.checksum_response_sha256
            ):
                raise RawArtifactValidationError(
                    "checksum response digest does not match provenance"
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
