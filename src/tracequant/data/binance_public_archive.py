"""Shared, bounded acquisition mechanics for Binance official archives.

This module owns the mechanical part of an official Binance archive read:
checksum acquisition, bounded download, ZIP member framing, failure evidence,
and revision-aware Raw publication.  Dataset adapters supply the typed object
plan and parse the already-framed member bytes.
"""

from __future__ import annotations

import csv
import hashlib
import http.client
import io
import math
import re
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol

import polars as pl

from tracequant.data.public_history import (
    BinancePublicHistoryRequest,
    BinancePublicHistorySourceKind,
)
from tracequant.data.raw_store import (
    RawAcquisitionManifest,
    RawAcquisitionResponse,
    RawArtifact,
    RawArtifactAmbiguousError,
    RawArtifactConflictError,
    RawArtifactIncompleteError,
    RawArtifactNotFoundError,
    RawArtifactValidationError,
    RawObjectIdentity,
    RawSourceObject,
    RawSourceProvenance,
    RawStore,
)
from tracequant.domain import TimeRange

__all__ = [
    "ArchiveHttpGet",
    "ArchiveHttpBudgetExceeded",
    "ArchiveHttpResponse",
    "BinanceArchiveAcquisitionOutcome",
    "BinanceArchiveAcquisitionStatus",
    "BinanceArchiveDatasetAdapter",
    "BinanceArchiveObjectPlan",
    "BinanceArchiveParseResult",
    "BinancePublicArchiveAcquisition",
]

_MAX_ARCHIVE_BYTES: Final = 512 * 1024 * 1024
_CHECKSUM_PATTERN: Final = re.compile(
    r"\A([0-9A-Fa-f]{64})[ \t]+[*]?([^\r\n]+)[\r\n]*\Z"
)
_PRODUCER_VERSION: Final = "tracequant/0.1.0"


@dataclass(frozen=True, slots=True)
class ArchiveHttpResponse:
    """Bounded response returned by an injectable archive HTTP transport."""

    status: int
    body: bytes
    headers: Mapping[str, str]
    complete: bool = True


class ArchiveHttpGet(Protocol):
    def __call__(self, url: str, timeout: float) -> ArchiveHttpResponse: ...


class ArchiveHttpBudgetExceeded(RuntimeError):
    """A received archive response exhausted an enclosing shared budget."""

    def __init__(self, message: str, *, response: ArchiveHttpResponse) -> None:
        super().__init__(message)
        self.response = response


@dataclass(frozen=True, slots=True)
class BinanceArchiveObjectPlan:
    """Typed identity and framing contract for one official archive object."""

    request: BinancePublicHistoryRequest
    object_key: str
    url: str
    checksum_url: str
    member_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.request, BinancePublicHistoryRequest):
            raise TypeError("request must be a BinancePublicHistoryRequest")
        if self.request.source_kind not in {
            BinancePublicHistorySourceKind.ARCHIVE_DAILY,
            BinancePublicHistorySourceKind.ARCHIVE_MONTHLY,
        }:
            raise ValueError("archive plan request must use an archive source")
        for field_name in ("object_key", "url", "checksum_url", "member_name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class BinanceArchiveParseResult:
    """Dataset-specific rows and coverage evidence after member framing."""

    rows: pl.DataFrame
    actual_record_range: TimeRange
    validation_evidence: tuple[str, ...]


class BinanceArchiveDatasetAdapter(Protocol):
    """The small typed boundary a dataset parser exposes to shared acquisition."""

    raw_schema_identifier: str

    def parse_member(
        self, plan: BinanceArchiveObjectPlan, payload: bytes
    ) -> BinanceArchiveParseResult: ...

    def validate_complete_object_coverage(
        self, plan: BinanceArchiveObjectPlan, actual_range: TimeRange
    ) -> None: ...

    def validate_required_coverage(
        self, plan: BinanceArchiveObjectPlan, actual_range: TimeRange
    ) -> None: ...


class BinanceArchiveAcquisitionStatus(StrEnum):
    """Stable mechanical outcomes shared by Binance archive adapters."""

    PUBLISHED = "published"
    EXISTING = "existing"
    COVERAGE_GAP = "coverage_gap"
    NOT_FOUND = "not_found"
    CHECKSUM_NOT_FOUND = "checksum_not_found"
    RETRYABLE_FAILURE = "retryable_failure"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INVALID_CONTENT = "invalid_content"
    LOCAL_FAILURE = "local_failure"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class BinanceArchiveAcquisitionOutcome:
    """One shared acquisition outcome before an adapter adds its result type."""

    status: BinanceArchiveAcquisitionStatus
    artifact_path: Path | None = None
    detail: str | None = None
    actual_record_range: TimeRange | None = None


class _InvalidContentError(ValueError):
    def __init__(
        self, message: str, *, response: ArchiveHttpResponse | None = None
    ) -> None:
        super().__init__(message)
        self.response = response


class _CoverageGapError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        actual_record_range: TimeRange | None = None,
    ) -> None:
        super().__init__(message)
        self.actual_record_range = actual_record_range


class _RetryableDownloadError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        resource: str,
        response: ArchiveHttpResponse | None = None,
    ) -> None:
        super().__init__(message)
        self.resource = resource
        self.response = response


class _BudgetExhaustedDownloadError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        resource: str,
        response: ArchiveHttpResponse,
    ) -> None:
        super().__init__(message)
        self.resource = resource
        self.response = response


@dataclass(frozen=True, slots=True)
class _DownloadedResponse:
    body: bytes
    status: int
    headers: Mapping[str, str]


class _DownloadNotFoundError(FileNotFoundError):
    def __init__(
        self,
        url: str,
        *,
        resource: str,
        response: ArchiveHttpResponse | None = None,
    ) -> None:
        super().__init__(url)
        self.resource = resource
        self.response = response


def _default_http_get(
    url: str,
    timeout: float,
    *,
    maximum_response_bytes: int | None = None,
) -> ArchiveHttpResponse:
    request = urllib.request.Request(url, headers={"User-Agent": _PRODUCER_VERSION})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if maximum_response_bytes is None:
                body = response.read(_MAX_ARCHIVE_BYTES + 1)
                if len(body) > _MAX_ARCHIVE_BYTES:
                    raise _InvalidContentError(
                        "archive response exceeds the size limit"
                    )
                complete = True
            else:
                limit = min(maximum_response_bytes, _MAX_ARCHIVE_BYTES)
                body = response.read(limit)
                declared_length = response.headers.get("Content-Length")
                try:
                    complete = declared_length is not None and int(
                        declared_length
                    ) <= len(body)
                except ValueError:
                    complete = False
                if declared_length is None:
                    complete = len(body) < limit
            return ArchiveHttpResponse(
                status=response.status,
                body=body,
                headers=dict(response.headers.items()),
                complete=complete,
            )
    except urllib.error.HTTPError as error:
        limit = (
            4096
            if maximum_response_bytes is None
            else min(4096, maximum_response_bytes)
        )
        body = error.read(limit)
        declared_length = error.headers.get("Content-Length") if error.headers else None
        try:
            complete = (
                len(body) < limit
                if declared_length is None
                else int(declared_length) <= len(body)
            )
        except ValueError:
            complete = False
        return ArchiveHttpResponse(
            status=error.code,
            body=body,
            headers=dict(error.headers.items()) if error.headers is not None else {},
            complete=complete,
        )
    except (TimeoutError, urllib.error.URLError):
        raise


def _download(
    http_get: ArchiveHttpGet,
    url: str,
    timeout: float,
    *,
    resource: str,
) -> _DownloadedResponse:
    try:
        response = http_get(url, timeout)
    except ArchiveHttpBudgetExceeded as error:
        raise _BudgetExhaustedDownloadError(
            str(error), resource=resource, response=error.response
        ) from error
    except _InvalidContentError:
        raise
    except (
        TimeoutError,
        ConnectionError,
        OSError,
        http.client.HTTPException,
    ) as error:
        detail = str(error).strip() or (
            f"{type(error).__name__} while downloading {resource}"
        )
        raise _RetryableDownloadError(detail, resource=resource) from error
    if response.status == 404:
        raise _DownloadNotFoundError(url, resource=resource, response=response)
    if response.status == 429 or 500 <= response.status <= 599:
        raise _RetryableDownloadError(
            f"HTTP {response.status} for {url}",
            resource=resource,
            response=response,
        )
    if response.status < 200 or response.status >= 300:
        raise _InvalidContentError(
            f"unexpected HTTP {response.status} for {url}", response=response
        )
    if not response.body:
        raise _InvalidContentError(f"empty response for {url}", response=response)
    return _DownloadedResponse(
        body=response.body,
        status=response.status,
        headers=dict(response.headers),
    )


def _declared_checksum(payload: bytes, expected_filename: str) -> str:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise _InvalidContentError("checksum response is not ASCII") from error
    match = _CHECKSUM_PATTERN.fullmatch(text)
    if match is None or match.group(2) != expected_filename:
        raise _InvalidContentError(
            "checksum response has an unexpected format or filename"
        )
    return match.group(1).lower()


def _extract_expected_member(plan: BinanceArchiveObjectPlan, payload: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) != 1 or members[0].filename != plan.member_name:
                raise _InvalidContentError(
                    "ZIP must contain exactly the expected CSV member"
                )
            member = members[0]
            if member.flag_bits & 0x1:
                raise _InvalidContentError("encrypted ZIP members are not supported")
            if member.file_size > _MAX_ARCHIVE_BYTES:
                raise _InvalidContentError("CSV member exceeds the size limit")
            csv_payload = archive.read(member)
            if len(csv_payload) > _MAX_ARCHIVE_BYTES:
                raise _InvalidContentError("CSV member exceeds the size limit")
            return csv_payload
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as error:
        raise _InvalidContentError("archive is not a readable ZIP") from error


class BinancePublicArchiveAcquisition:
    """Acquire one typed official archive object and publish it through RawStore."""

    def __init__(
        self,
        store: RawStore,
        *,
        http_get: ArchiveHttpGet | None = None,
        timeout: float = 30.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(store, RawStore):
            raise TypeError("store must be a RawStore")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and greater than zero")
        self._store = store
        self._http_get = http_get or _default_http_get
        self._timeout = timeout
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _to_raw_response(
        response: ArchiveHttpResponse | _DownloadedResponse | None,
    ) -> RawAcquisitionResponse | None:
        if response is None:
            return None
        return RawAcquisitionResponse(
            status=response.status,
            headers=response.headers,
            body=response.body,
        )

    def record_failure(
        self,
        request: BinancePublicHistoryRequest,
        status: BinanceArchiveAcquisitionStatus,
        detail: str,
        *,
        source_url: str | None = None,
        checksum_url: str | None = None,
        artifact_path: Path | None = None,
        source_response: RawAcquisitionResponse | None = None,
        checksum_response: RawAcquisitionResponse | None = None,
        actual_record_range: TimeRange | None = None,
    ) -> BinanceArchiveAcquisitionOutcome:
        manifest = RawAcquisitionManifest(
            manifest_schema_version=1,
            completed=False,
            object_identity=RawObjectIdentity.from_request(request),
            caller_request_range=request.request_range,
            status=status.value,
            detail=detail,
            recorded_at=self._clock(),
            source_url=source_url,
            checksum_url=checksum_url,
            source_http_status=(
                source_response.status if source_response is not None else None
            ),
            source_http_headers=(
                source_response.headers if source_response is not None else {}
            ),
            source_body_sha256=(
                source_response.body_sha256 if source_response is not None else None
            ),
            checksum_http_status=(
                checksum_response.status if checksum_response is not None else None
            ),
            checksum_http_headers=(
                checksum_response.headers if checksum_response is not None else {}
            ),
            checksum_response_sha256=(
                checksum_response.body_sha256 if checksum_response is not None else None
            ),
        )
        self._store.write_acquisition_manifest(
            manifest,
            source_response=source_response,
            checksum_response=checksum_response,
        )
        return BinanceArchiveAcquisitionOutcome(
            status=status,
            artifact_path=artifact_path,
            detail=detail,
            actual_record_range=actual_record_range,
        )

    def _existing_artifact(
        self,
        plan: BinanceArchiveObjectPlan,
        adapter: BinanceArchiveDatasetAdapter,
    ) -> tuple[RawArtifact | None, BinanceArchiveAcquisitionOutcome | None]:
        identity = RawObjectIdentity.from_request(plan.request)
        try:
            revisions = self._store.list_verified_revisions(identity)
            if len(revisions) == 1:
                existing = revisions[0]
            elif not revisions:
                existing = self._store.read_request(plan.request)
            else:
                for revision in revisions:
                    try:
                        adapter.validate_complete_object_coverage(
                            plan, revision.manifest.actual_record_range
                        )
                    except _CoverageGapError as error:
                        return None, self.record_failure(
                            plan.request,
                            BinanceArchiveAcquisitionStatus.COVERAGE_GAP,
                            str(error),
                            source_url=plan.url,
                            checksum_url=plan.checksum_url,
                            artifact_path=revision.path,
                            actual_record_range=error.actual_record_range,
                        )
                existing = None
        except RawArtifactIncompleteError as error:
            return None, self.record_failure(
                plan.request,
                BinanceArchiveAcquisitionStatus.LOCAL_FAILURE,
                str(error),
                source_url=plan.url,
                checksum_url=plan.checksum_url,
            )
        except RawArtifactNotFoundError:
            return None, None
        except (
            RawArtifactAmbiguousError,
            RawArtifactValidationError,
            OSError,
        ) as error:
            return None, self.record_failure(
                plan.request,
                BinanceArchiveAcquisitionStatus.LOCAL_FAILURE,
                str(error),
                source_url=plan.url,
                checksum_url=plan.checksum_url,
            )
        else:
            if existing is not None:
                try:
                    adapter.validate_complete_object_coverage(
                        plan, existing.manifest.actual_record_range
                    )
                    adapter.validate_required_coverage(
                        plan, existing.manifest.actual_record_range
                    )
                except _CoverageGapError as error:
                    return None, self.record_failure(
                        plan.request,
                        BinanceArchiveAcquisitionStatus.COVERAGE_GAP,
                        str(error),
                        source_url=plan.url,
                        checksum_url=plan.checksum_url,
                        artifact_path=existing.path,
                        actual_record_range=error.actual_record_range,
                    )
        return existing, None

    def acquire(
        self,
        plan: BinanceArchiveObjectPlan,
        adapter: BinanceArchiveDatasetAdapter,
    ) -> BinanceArchiveAcquisitionOutcome:
        """Download, verify, parse, and revision-aware publish one object."""
        if not isinstance(plan, BinanceArchiveObjectPlan):
            raise TypeError("plan must be a BinanceArchiveObjectPlan")
        existing, early = self._existing_artifact(plan, adapter)
        if early is not None:
            return early

        checksum_payload: _DownloadedResponse | None = None
        archive_payload: _DownloadedResponse | None = None
        checksum_response: RawAcquisitionResponse | None = None
        archive_response: RawAcquisitionResponse | None = None
        resource = "checksum"
        try:
            checksum_payload = _download(
                self._http_get,
                plan.checksum_url,
                self._timeout,
                resource="checksum",
            )
            declared = _declared_checksum(
                checksum_payload.body, plan.url.rsplit("/", 1)[-1]
            )
            resource = "archive"
            archive_payload = _download(
                self._http_get,
                plan.url,
                self._timeout,
                resource="archive",
            )
            actual = hashlib.sha256(archive_payload.body).hexdigest()
            if actual != declared:
                raise _InvalidContentError(
                    "archive SHA-256 does not match upstream checksum"
                )
            member_payload = _extract_expected_member(plan, archive_payload.body)
            parsed = adapter.parse_member(plan, member_payload)
            source = RawSourceObject(
                request=plan.request,
                rows=parsed.rows,
                actual_record_range=parsed.actual_record_range,
                raw_schema_identifier=adapter.raw_schema_identifier,
                producer_version=_PRODUCER_VERSION,
                upstream_checksum=f"sha256:{declared}",
                upstream_revision=plan.url,
                provenance=RawSourceProvenance(
                    object_key=plan.object_key,
                    source_url=plan.url,
                    acquired_at=self._clock(),
                    source_http_status=archive_payload.status,
                    source_http_headers=archive_payload.headers,
                    checksum_url=plan.checksum_url,
                    checksum_http_status=checksum_payload.status,
                    checksum_http_headers=checksum_payload.headers,
                    checksum_response_sha256=hashlib.sha256(
                        checksum_payload.body
                    ).hexdigest(),
                    archive_sha256=actual,
                    csv_member=plan.member_name,
                    validation_evidence=(
                        "checksum_response_verified",
                        "archive_sha256_matches_checksum",
                        "zip_member_structure_verified",
                        *parsed.validation_evidence,
                        "source_object_coverage_verified",
                    ),
                ),
                archive_payload=archive_payload.body,
                checksum_response_body=checksum_payload.body,
            )
            identity = RawObjectIdentity.from_request(plan.request)
            candidate_revision = source.revision_identity
            matching_existing: RawArtifact | None = None
            if candidate_revision is not None:
                try:
                    matching_existing = self._store.read_revision(
                        identity, candidate_revision
                    )
                except RawArtifactNotFoundError:
                    pass
            else:
                matching_existing = existing
            artifact = self._store.write(source)
        except _DownloadNotFoundError as error:
            if error.resource == "checksum":
                checksum_response = self._to_raw_response(error.response)
            else:
                archive_response = self._to_raw_response(error.response)
            return self.record_failure(
                plan.request,
                (
                    BinanceArchiveAcquisitionStatus.CHECKSUM_NOT_FOUND
                    if error.resource == "checksum"
                    else BinanceArchiveAcquisitionStatus.NOT_FOUND
                ),
                str(error),
                source_url=plan.url,
                checksum_url=plan.checksum_url,
                source_response=self._to_raw_response(archive_payload)
                or archive_response,
                checksum_response=self._to_raw_response(checksum_payload)
                or checksum_response,
            )
        except _RetryableDownloadError as error:
            if error.resource == "checksum":
                checksum_response = self._to_raw_response(error.response)
            else:
                archive_response = self._to_raw_response(error.response)
            return self.record_failure(
                plan.request,
                BinanceArchiveAcquisitionStatus.RETRYABLE_FAILURE,
                str(error),
                source_url=plan.url,
                checksum_url=plan.checksum_url,
                source_response=self._to_raw_response(archive_payload)
                or archive_response,
                checksum_response=self._to_raw_response(checksum_payload)
                or checksum_response,
            )
        except _BudgetExhaustedDownloadError as error:
            if error.resource == "checksum":
                checksum_response = self._to_raw_response(error.response)
            else:
                archive_response = self._to_raw_response(error.response)
            return self.record_failure(
                plan.request,
                BinanceArchiveAcquisitionStatus.BUDGET_EXHAUSTED,
                str(error),
                source_url=plan.url,
                checksum_url=plan.checksum_url,
                source_response=self._to_raw_response(archive_payload)
                or archive_response,
                checksum_response=self._to_raw_response(checksum_payload)
                or checksum_response,
            )
        except _CoverageGapError as error:
            return self.record_failure(
                plan.request,
                BinanceArchiveAcquisitionStatus.COVERAGE_GAP,
                str(error),
                source_url=plan.url,
                checksum_url=plan.checksum_url,
                source_response=self._to_raw_response(archive_payload),
                checksum_response=self._to_raw_response(checksum_payload),
                actual_record_range=error.actual_record_range,
            )
        except (csv.Error, _InvalidContentError) as error:
            response = (
                error.response if isinstance(error, _InvalidContentError) else None
            )
            if response is not None:
                if resource == "checksum" and checksum_payload is None:
                    checksum_payload = _DownloadedResponse(
                        body=response.body,
                        status=response.status,
                        headers=response.headers,
                    )
                elif resource == "archive" and archive_payload is None:
                    archive_payload = _DownloadedResponse(
                        body=response.body,
                        status=response.status,
                        headers=response.headers,
                    )
            return self.record_failure(
                plan.request,
                BinanceArchiveAcquisitionStatus.INVALID_CONTENT,
                str(error),
                source_url=plan.url,
                checksum_url=plan.checksum_url,
                source_response=self._to_raw_response(archive_payload),
                checksum_response=self._to_raw_response(checksum_payload),
            )
        except RawArtifactConflictError as error:
            return self.record_failure(
                plan.request,
                BinanceArchiveAcquisitionStatus.CONFLICT,
                str(error),
                source_url=plan.url,
                checksum_url=plan.checksum_url,
                source_response=self._to_raw_response(archive_payload),
                checksum_response=self._to_raw_response(checksum_payload),
            )
        except (
            RawArtifactNotFoundError,
            RawArtifactValidationError,
            OSError,
        ) as error:
            return self.record_failure(
                plan.request,
                BinanceArchiveAcquisitionStatus.LOCAL_FAILURE,
                str(error),
                source_url=plan.url,
                checksum_url=plan.checksum_url,
                source_response=self._to_raw_response(archive_payload),
                checksum_response=self._to_raw_response(checksum_payload),
            )
        return BinanceArchiveAcquisitionOutcome(
            status=(
                BinanceArchiveAcquisitionStatus.EXISTING
                if matching_existing is not None
                else BinanceArchiveAcquisitionStatus.PUBLISHED
            ),
            artifact_path=artifact.path,
            actual_record_range=artifact.manifest.actual_record_range,
        )


def _coverage_gap(
    message: str,
    *,
    actual_record_range: TimeRange | None = None,
) -> _CoverageGapError:
    """Create the shared coverage exception for dataset adapters."""
    return _CoverageGapError(
        message,
        actual_record_range=actual_record_range,
    )


def _invalid_content(message: str) -> _InvalidContentError:
    """Create the shared invalid-content exception for dataset adapters."""
    return _InvalidContentError(message)
