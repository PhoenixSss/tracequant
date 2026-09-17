from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, cast

import zstandard

from tracequant.integrations.nautilus.stage2_artifact import (
    LOCKED_IDENTITIES,
    _catalog_identities,
)
from tracequant.integrations.nautilus.stage2_btceth import (
    fetch_stage2_instruments,
    instrument_snapshot_payload,
    prepare_stage2_combined_catalog,
    require_stage2_catalog_identity,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_DAILY_MARK_ROOT,
    STAGE2_DATASET_ID,
    STAGE2_EXPECTED_SOURCE_COUNT,
    STAGE2_EXPECTED_SUPPLEMENTAL_SOURCE_COUNT,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    STAGE2_INSTRUMENT_SYMBOLS,
    STAGE2_MARK_GAP_FILL_DATES,
    STAGE2_MARK_INTERVAL,
    STAGE2_NAUTILUS_VERSION,
    STAGE2_PUBLIC_DATA_ORIGIN,
    STAGE2_SOURCE_SCHEMA,
    Stage2DataError,
    expected_stage2_source_specs,
    load_stage2_config,
    parse_utc,
)

SOURCE_ARTIFACT_LOCK_SCHEMA: Final = "tracequant-stage2-source-artifact-lock-v1"
SOURCE_FILE_MANIFEST_SCHEMA: Final = "tracequant-stage2-source-artifact-files-v1"
ARCHIVE_FORMAT: Final = "tar+zstd"
RELEASE_REPOSITORY: Final = "PhoenixSss/tracequant"
RELEASE_TAG: Final = "stage2-binance-usdm-btceth-202001-202608-r1-sources"
ARCHIVE_ROOT: Final = f"{STAGE2_DATASET_ID}.sources"
ARCHIVE_ASSET_NAME: Final = f"{STAGE2_DATASET_ID}.sources.tar.zst"
FILE_MANIFEST_ASSET_NAME: Final = f"{STAGE2_DATASET_ID}.sources.files.json"
LOCKED_INSTRUMENT_FETCHED_AT: Final = "2026-09-15T03:08:10.006744Z"
_SHA256_LENGTH: Final = 64
_CHUNK_SIZE: Final = 1024 * 1024


class Stage2SourceArtifactError(Stage2DataError):
    """Raised when the fixed Stage 2 source escrow fails closed."""


@dataclass(frozen=True)
class LockedSourceFile:
    path: str
    sha256: str
    size: int
    source_url: str


@dataclass(frozen=True)
class LockedSource:
    path: str
    source_url: str
    checksum_path: str
    checksum_url: str
    official_checksum: str


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    sha256: str
    size: int
    format: str | None = None


@dataclass(frozen=True)
class Stage2SourceArtifactLock:
    path: Path
    dataset_id: str
    archive_root: str
    repository: str
    tag: str
    archive: ReleaseAsset
    file_manifest: ReleaseAsset
    files: tuple[LockedSourceFile, ...]
    sources: tuple[LockedSource, ...]
    identities: Mapping[str, object]
    source_inventory: Mapping[str, object]


@dataclass(frozen=True)
class _CatalogSource:
    path: str
    source_url: str
    checksum_url: str
    sha256: str
    supplemental: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_source_artifact_lock(path: Path) -> Stage2SourceArtifactLock:
    lock_path = path.resolve()
    payload = _load_json_object(lock_path, label="source artifact lock")
    _require_keys(
        payload,
        {
            "archive_root",
            "dataset_id",
            "files",
            "identities",
            "release",
            "schema",
            "source_inventory",
            "sources",
        },
        label="source artifact lock",
    )
    if payload["schema"] != SOURCE_ARTIFACT_LOCK_SCHEMA:
        raise Stage2SourceArtifactError("source artifact lock schema does not match")
    dataset_id = _require_text(payload, "dataset_id", label="source artifact lock")
    archive_root = _require_text(payload, "archive_root", label="source artifact lock")
    if dataset_id != STAGE2_DATASET_ID or archive_root != ARCHIVE_ROOT:
        raise Stage2SourceArtifactError(
            "source artifact dataset identity does not match"
        )
    release = _require_mapping(payload, "release", label="source artifact lock")
    _require_keys(
        release,
        {"archive_asset", "file_manifest_asset", "repository", "tag"},
        label="source artifact release",
    )
    repository = _require_text(release, "repository", label="source artifact release")
    tag = _require_text(release, "tag", label="source artifact release")
    if repository != RELEASE_REPOSITORY or tag != RELEASE_TAG:
        raise Stage2SourceArtifactError(
            "source artifact release identity does not match"
        )
    archive = _parse_asset(
        _require_mapping(release, "archive_asset", label="source artifact release"),
        label="source archive asset",
        require_format=True,
    )
    file_manifest = _parse_asset(
        _require_mapping(
            release, "file_manifest_asset", label="source artifact release"
        ),
        label="source file manifest asset",
        require_format=False,
    )
    if (
        archive.name != ARCHIVE_ASSET_NAME
        or file_manifest.name != FILE_MANIFEST_ASSET_NAME
        or archive.format != ARCHIVE_FORMAT
    ):
        raise Stage2SourceArtifactError("source artifact asset identity does not match")
    expected_prefix = f"https://github.com/{repository}/releases/download/{tag}/"
    if archive.url != f"{expected_prefix}{archive.name}" or file_manifest.url != (
        f"{expected_prefix}{file_manifest.name}"
    ):
        raise Stage2SourceArtifactError("source artifact release URL is not immutable")

    raw_files = payload["files"]
    raw_sources = payload["sources"]
    if not isinstance(raw_files, list) or not isinstance(raw_sources, list):
        raise Stage2SourceArtifactError("source artifact inventory is missing")
    files = tuple(_parse_locked_file(item) for item in raw_files)
    sources = tuple(_parse_locked_source(item) for item in raw_sources)
    file_paths = tuple(item.path for item in files)
    source_paths = tuple(item.path for item in sources)
    if file_paths != tuple(sorted(file_paths)) or len(file_paths) != len(
        set(file_paths)
    ):
        raise Stage2SourceArtifactError(
            "source artifact files are not sorted and unique"
        )
    if source_paths != tuple(sorted(source_paths)) or len(source_paths) != len(
        set(source_paths)
    ):
        raise Stage2SourceArtifactError(
            "source artifact sources are not sorted and unique"
        )
    _validate_locked_inventory(files, sources)

    identities = _require_mapping(payload, "identities", label="source artifact lock")
    if dict(identities) != dict(LOCKED_IDENTITIES):
        raise Stage2SourceArtifactError(
            "source artifact lock does not bind the accepted r1 identities"
        )
    source_inventory = _require_mapping(
        payload, "source_inventory", label="source artifact lock"
    )
    _validate_source_inventory(source_inventory, sources)
    return Stage2SourceArtifactLock(
        path=lock_path,
        dataset_id=dataset_id,
        archive_root=archive_root,
        repository=repository,
        tag=tag,
        archive=archive,
        file_manifest=file_manifest,
        files=files,
        sources=sources,
        identities=dict(identities),
        source_inventory=dict(source_inventory),
    )


def verify_source_root(lock: Stage2SourceArtifactLock, raw_root: Path) -> None:
    try:
        root_mode = raw_root.lstat().st_mode
    except OSError as exc:
        raise Stage2SourceArtifactError("source root must be a real directory") from exc
    if not stat.S_ISDIR(root_mode):
        raise Stage2SourceArtifactError("source root must be a real directory")
    root = raw_root.resolve()
    expected_files = {item.path for item in lock.files}
    expected_directories = _expected_directories(expected_files)
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    try:
        for item in root.rglob("*"):
            name = item.relative_to(root).as_posix()
            item_mode = item.lstat().st_mode
            if stat.S_ISREG(item_mode):
                actual_files.add(name)
            elif stat.S_ISDIR(item_mode):
                actual_directories.add(name)
            else:
                raise Stage2SourceArtifactError(
                    "source root inventory does not match the lock"
                )
    except OSError as exc:
        raise Stage2SourceArtifactError(
            "source root inventory does not match the lock"
        ) from exc
    if actual_files != expected_files or actual_directories != expected_directories:
        raise Stage2SourceArtifactError("source root inventory does not match the lock")
    file_by_path = {item.path: item for item in lock.files}
    for locked in lock.files:
        candidate = root / locked.path
        candidate_stat = candidate.lstat()
        if not stat.S_ISREG(candidate_stat.st_mode):
            raise Stage2SourceArtifactError(
                "source root inventory does not match the lock"
            )
        if candidate_stat.st_size != locked.size:
            raise Stage2SourceArtifactError("source file size does not match the lock")
        if sha256_file(candidate) != locked.sha256:
            raise Stage2SourceArtifactError("source file hash does not match the lock")
    for source in lock.sources:
        checksum = _official_checksum(root / source.checksum_path, source.path)
        if checksum != source.official_checksum:
            raise Stage2SourceArtifactError("official source checksum has drifted")
        if file_by_path[source.path].sha256 != checksum:
            raise Stage2SourceArtifactError(
                "source ZIP does not match official checksum"
            )


def materialize_source_artifact(
    lock_path: Path,
    staging_root: Path,
    raw_root: Path,
    *,
    archive_path: Path | None = None,
) -> Path:
    lock = load_source_artifact_lock(lock_path)
    staging = _require_external_absolute(staging_root, label="staging root")
    target = _require_external_absolute(raw_root, label="raw root")
    _require_nonoverlap(staging, target)
    _require_safe_target(target)
    staging.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="stage2-source-materialize-", dir=staging))
    publish: Path | None = None
    try:
        publish = Path(
            tempfile.mkdtemp(prefix=".stage2-source-publish-", dir=target.parent)
        )
        archive = work / lock.archive.name
        if archive_path is None:
            _download(lock.archive.url, archive)
        else:
            supplied = archive_path.resolve()
            if not supplied.is_file() or supplied.is_symlink():
                raise Stage2SourceArtifactError(
                    "supplied archive is not a regular file"
                )
            shutil.copyfile(supplied, archive)
        _verify_asset(archive, lock.archive)
        extracted = publish / "extracted"
        extracted.mkdir()
        _extract_locked_archive(archive, extracted, lock)
        candidate = extracted / lock.archive_root
        verify_source_root(lock, candidate)
        os.replace(candidate, target)
        return target
    except Stage2SourceArtifactError:
        raise
    except (OSError, tarfile.TarError, zstandard.ZstdError) as exc:
        raise Stage2SourceArtifactError(
            "source artifact materialization failed"
        ) from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if publish is not None:
            shutil.rmtree(publish, ignore_errors=True)


def fetch_official_sources(
    catalog_manifest_path: Path,
    staging_root: Path,
    raw_root: Path,
    *,
    workers: int = 8,
) -> Path:
    sources = _load_catalog_sources(catalog_manifest_path)
    staging = _require_external_absolute(staging_root, label="staging root")
    target = _require_external_absolute(raw_root, label="raw root")
    _require_nonoverlap(staging, target)
    _require_safe_target(target)
    if workers < 1 or workers > 32:
        raise Stage2SourceArtifactError("download worker count is invalid")
    staging.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="stage2-source-fetch-", dir=staging))
    publish: Path | None = None
    try:
        publish = Path(
            tempfile.mkdtemp(prefix=".stage2-source-fetch-", dir=target.parent)
        )
        candidate = publish / ARCHIVE_ROOT
        candidate.mkdir()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_fetch_catalog_source, source, candidate): source
                for source in sources
            }
            for future in as_completed(futures):
                future.result()
        os.replace(candidate, target)
        return target
    except Stage2SourceArtifactError:
        raise
    except OSError as exc:
        raise Stage2SourceArtifactError("official source fetch failed") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if publish is not None:
            shutil.rmtree(publish, ignore_errors=True)


def build_source_release_assets(
    raw_root: Path,
    catalog_manifest_path: Path,
    output_root: Path,
    *,
    repository: str,
    tag: str,
) -> tuple[Path, Path, dict[str, object]]:
    root = raw_root.resolve()
    output = output_root.resolve()
    sources = _load_catalog_sources(catalog_manifest_path)
    if not root.is_dir() or root.is_symlink():
        raise Stage2SourceArtifactError("source root must be a real directory")
    if output == root or output in root.parents or root in output.parents:
        raise Stage2SourceArtifactError(
            "publication output and source root must not overlap"
        )
    if repository != RELEASE_REPOSITORY or tag != RELEASE_TAG:
        raise Stage2SourceArtifactError("source release identity does not match")
    output.mkdir(parents=True, exist_ok=True)
    locked_sources: list[LockedSource] = []
    locked_files: list[LockedSourceFile] = []
    for source in sources:
        zip_path = root / source.path
        checksum_relative = f"{source.path}.CHECKSUM"
        checksum_path = root / checksum_relative
        official = _official_checksum(checksum_path, source.path)
        if official != source.sha256 or sha256_file(zip_path) != official:
            raise Stage2SourceArtifactError(
                "source ZIP does not match the locked official checksum"
            )
        locked_sources.append(
            LockedSource(
                path=source.path,
                source_url=source.source_url,
                checksum_path=checksum_relative,
                checksum_url=source.checksum_url,
                official_checksum=official,
            )
        )
        for path, url in (
            (source.path, source.source_url),
            (checksum_relative, source.checksum_url),
        ):
            candidate = root / path
            if not candidate.is_file() or candidate.is_symlink():
                raise Stage2SourceArtifactError("source artifact file is missing")
            locked_files.append(
                LockedSourceFile(
                    path=path,
                    sha256=sha256_file(candidate),
                    size=candidate.stat().st_size,
                    source_url=url,
                )
            )
    source_tuple = tuple(sorted(locked_sources, key=lambda item: item.path))
    file_tuple = tuple(sorted(locked_files, key=lambda item: item.path))
    _validate_locked_inventory(file_tuple, source_tuple)
    _verify_exact_filesystem_inventory(root, {item.path for item in file_tuple})

    archive_path = output / ARCHIVE_ASSET_NAME
    manifest_path = output / FILE_MANIFEST_ASSET_NAME
    _write_deterministic_archive(root, archive_path, file_tuple)
    inventory = _source_inventory_payload(source_tuple)
    manifest_payload: dict[str, object] = {
        "archive": {
            "format": ARCHIVE_FORMAT,
            "name": ARCHIVE_ASSET_NAME,
            "sha256": sha256_file(archive_path),
            "size": archive_path.stat().st_size,
        },
        "archive_root": ARCHIVE_ROOT,
        "dataset_id": STAGE2_DATASET_ID,
        "files": [_locked_file_json(item) for item in file_tuple],
        "identities": dict(LOCKED_IDENTITIES),
        "schema": SOURCE_FILE_MANIFEST_SCHEMA,
        "source_inventory": inventory,
        "sources": [_locked_source_json(item) for item in source_tuple],
    }
    _write_json(manifest_path, manifest_payload)
    base_url = f"https://github.com/{repository}/releases/download/{tag}"
    lock_payload: dict[str, object] = {
        "archive_root": ARCHIVE_ROOT,
        "dataset_id": STAGE2_DATASET_ID,
        "files": [_locked_file_json(item) for item in file_tuple],
        "identities": dict(LOCKED_IDENTITIES),
        "release": {
            "archive_asset": {
                "format": ARCHIVE_FORMAT,
                "name": ARCHIVE_ASSET_NAME,
                "sha256": sha256_file(archive_path),
                "size": archive_path.stat().st_size,
                "url": f"{base_url}/{ARCHIVE_ASSET_NAME}",
            },
            "file_manifest_asset": {
                "name": FILE_MANIFEST_ASSET_NAME,
                "sha256": sha256_file(manifest_path),
                "size": manifest_path.stat().st_size,
                "url": f"{base_url}/{FILE_MANIFEST_ASSET_NAME}",
            },
            "repository": repository,
            "tag": tag,
        },
        "schema": SOURCE_ARTIFACT_LOCK_SCHEMA,
        "source_inventory": inventory,
        "sources": [_locked_source_json(item) for item in source_tuple],
    }
    return archive_path, manifest_path, lock_payload


def rebuild_and_verify_catalog(
    lock_path: Path,
    config_path: Path,
    *,
    instrument_snapshot_path: Path | None = None,
) -> Path:
    lock = load_source_artifact_lock(lock_path)
    repository_root = Path(__file__).resolve().parents[4]
    config = load_stage2_config(config_path, repository_root=repository_root)
    verify_source_root(lock, config.raw_root)
    instruments: Sequence[object] | None = None
    fetched_at = None
    if instrument_snapshot_path is None:
        instruments, _observed_at = fetch_stage2_instruments()
        fetched_at = parse_utc(LOCKED_INSTRUMENT_FETCHED_AT)
        snapshot = instrument_snapshot_payload(instruments, fetched_at=fetched_at)
        if (
            snapshot.get("checksum_sha256")
            != lock.identities["instrument_snapshot_checksum"]
        ):
            raise Stage2SourceArtifactError(
                "refetched instrument snapshot does not match the source lock"
            )
    generated_snapshot = config.raw_root / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME
    try:
        _manifest, _coverage, catalog = prepare_stage2_combined_catalog(
            config_path,
            repository_root=repository_root,
            instruments=instruments,
            instrument_snapshot_path=instrument_snapshot_path,
            fetched_at=fetched_at,
        )
    finally:
        generated_snapshot.unlink(missing_ok=True)
    verify_source_root(lock, config.raw_root)
    require_stage2_catalog_identity(catalog)
    if _catalog_identities(catalog) != dict(lock.identities):
        raise Stage2SourceArtifactError(
            "rebuilt catalog does not match the locked r1 identities"
        )
    return catalog


def _load_catalog_sources(path: Path) -> tuple[_CatalogSource, ...]:
    payload = _load_json_object(path.resolve(), label="catalog source manifest")
    if (
        payload.get("schema") != STAGE2_SOURCE_SCHEMA
        or payload.get("dataset_id") != STAGE2_DATASET_ID
        or payload.get("nautilus_version") != STAGE2_NAUTILUS_VERSION
    ):
        raise Stage2SourceArtifactError(
            "catalog source manifest identity does not match"
        )
    instrument_snapshot = payload.get("instrument_snapshot")
    if not isinstance(instrument_snapshot, Mapping) or (
        instrument_snapshot.get("checksum_sha256")
        != LOCKED_IDENTITIES["instrument_snapshot_checksum"]
    ):
        raise Stage2SourceArtifactError("catalog source manifest snapshot has drifted")
    primary = payload.get("sources")
    supplemental = payload.get("supplemental_sources")
    if not isinstance(primary, list) or not isinstance(supplemental, list):
        raise Stage2SourceArtifactError("catalog source manifest inventory is missing")
    if len(primary) != STAGE2_EXPECTED_SOURCE_COUNT or len(supplemental) != (
        STAGE2_EXPECTED_SUPPLEMENTAL_SOURCE_COUNT
    ):
        raise Stage2SourceArtifactError(
            "catalog source manifest inventory is incomplete"
        )
    sources = tuple(
        _parse_catalog_source(item, supplemental=False) for item in primary
    ) + tuple(_parse_catalog_source(item, supplemental=True) for item in supplemental)
    expected = _expected_source_urls()
    observed = tuple(
        sorted((item.path, item.source_url, item.checksum_url) for item in sources)
    )
    if observed != expected:
        raise Stage2SourceArtifactError("catalog source manifest inventory has drifted")
    if len({item.path for item in sources}) != len(sources):
        raise Stage2SourceArtifactError(
            "catalog source manifest contains duplicate paths"
        )
    _verify_catalog_manifest_identities(payload)
    return tuple(sorted(sources, key=lambda item: item.path))


def _parse_catalog_source(value: object, *, supplemental: bool) -> _CatalogSource:
    if not isinstance(value, Mapping):
        raise Stage2SourceArtifactError("catalog source manifest entry is invalid")
    path = _require_text(value, "path", label="catalog source")
    source_url = _require_text(value, "source_url", label="catalog source")
    checksum_url = _require_text(value, "checksum_url", label="catalog source")
    sha256 = _require_text(value, "sha256", label="catalog source")
    _require_safe_relative(path)
    if not _is_sha256(sha256):
        raise Stage2SourceArtifactError("catalog source hash is invalid")
    if source_url != f"{STAGE2_PUBLIC_DATA_ORIGIN}/{path}" or checksum_url != (
        f"{source_url}.CHECKSUM"
    ):
        raise Stage2SourceArtifactError("catalog source URL identity has drifted")
    return _CatalogSource(
        path=path,
        source_url=source_url,
        checksum_url=checksum_url,
        sha256=sha256,
        supplemental=supplemental,
    )


def _verify_catalog_manifest_identities(payload: Mapping[str, object]) -> None:
    primary = cast(list[object], payload["sources"])
    supplemental = cast(list[object], payload["supplemental_sources"])

    def identity(item: object) -> dict[str, object]:
        if not isinstance(item, Mapping):
            raise Stage2SourceArtifactError("catalog source manifest entry is invalid")
        keys = (
            "checksum_url",
            "data_type",
            "end_ns",
            "instrument_id",
            "rows",
            "sha256",
            "source_url",
            "start_ns",
        )
        if any(key not in item for key in keys):
            raise Stage2SourceArtifactError("catalog source identity is incomplete")
        return {key: item[key] for key in keys}

    market_digest = _canonical_digest(
        {
            "sources": [identity(item) for item in primary],
            "supplemental_sources": [identity(item) for item in supplemental],
        }
    )
    source_digest = _canonical_digest(
        {
            "instrument_snapshot": payload["instrument_snapshot"],
            "market_data_manifest_digest": market_digest,
        }
    )
    if market_digest != LOCKED_IDENTITIES["market_data_manifest_digest"] or (
        source_digest != LOCKED_IDENTITIES["source_manifest_digest"]
    ):
        raise Stage2SourceArtifactError("catalog source manifest identity has drifted")


def _expected_source_urls() -> tuple[tuple[str, str, str], ...]:
    expected = [
        (spec.relative_path, spec.source_url, spec.checksum_url)
        for spec in expected_stage2_source_specs()
    ]
    for instrument_id, dates in STAGE2_MARK_GAP_FILL_DATES.items():
        symbol = STAGE2_INSTRUMENT_SYMBOLS[instrument_id]
        for date_text in dates:
            name = f"{symbol}-{STAGE2_MARK_INTERVAL}-{date_text}.zip"
            relative = (
                f"{STAGE2_DAILY_MARK_ROOT}/{symbol}/{STAGE2_MARK_INTERVAL}/{name}"
            )
            url = f"{STAGE2_PUBLIC_DATA_ORIGIN}/{relative}"
            expected.append((relative, url, f"{url}.CHECKSUM"))
    return tuple(sorted(expected))


def _fetch_catalog_source(source: _CatalogSource, target: Path) -> None:
    zip_path = target / source.path
    checksum_path = target / f"{source.path}.CHECKSUM"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    _download(source.checksum_url, checksum_path)
    official = _official_checksum(checksum_path, source.path)
    if official != source.sha256:
        raise Stage2SourceArtifactError(
            "official checksum does not match catalog manifest"
        )
    _download(source.source_url, zip_path)
    if sha256_file(zip_path) != official:
        raise Stage2SourceArtifactError("downloaded source ZIP does not match checksum")


def _validate_locked_inventory(
    files: Sequence[LockedSourceFile], sources: Sequence[LockedSource]
) -> None:
    expected_paths: set[str] = set()
    file_by_path = {item.path: item for item in files}
    for source in sources:
        expected_paths.update((source.path, source.checksum_path))
        if source.checksum_path != f"{source.path}.CHECKSUM":
            raise Stage2SourceArtifactError("source checksum path does not match")
        if source.source_url != f"{STAGE2_PUBLIC_DATA_ORIGIN}/{source.path}" or (
            source.checksum_url != f"{source.source_url}.CHECKSUM"
        ):
            raise Stage2SourceArtifactError("source URL identity does not match")
        if not _is_sha256(source.official_checksum):
            raise Stage2SourceArtifactError("official source checksum is invalid")
        zip_file = file_by_path.get(source.path)
        checksum_file = file_by_path.get(source.checksum_path)
        if zip_file is None or checksum_file is None:
            raise Stage2SourceArtifactError("source artifact file pair is incomplete")
        if (
            zip_file.source_url != source.source_url
            or checksum_file.source_url != source.checksum_url
            or zip_file.sha256 != source.official_checksum
        ):
            raise Stage2SourceArtifactError(
                "source artifact file identity does not match"
            )
    if set(file_by_path) != expected_paths or len(files) != len(sources) * 2:
        raise Stage2SourceArtifactError("source artifact inventory is incomplete")
    observed = tuple(
        sorted((item.path, item.source_url, item.checksum_url) for item in sources)
    )
    if observed != _expected_source_urls():
        raise Stage2SourceArtifactError("source artifact inventory has drifted")


def _validate_source_inventory(
    inventory: Mapping[str, object], sources: Sequence[LockedSource]
) -> None:
    _require_keys(
        inventory,
        {
            "file_count",
            "primary_source_count",
            "source_count",
            "source_inventory_digest",
            "supplemental_source_count",
        },
        label="source inventory",
    )
    expected = _source_inventory_payload(sources)
    if dict(inventory) != expected:
        raise Stage2SourceArtifactError("source inventory identity does not match")


def _source_inventory_payload(sources: Sequence[LockedSource]) -> dict[str, object]:
    source_count = len(sources)
    return {
        "file_count": source_count * 2,
        "primary_source_count": STAGE2_EXPECTED_SOURCE_COUNT,
        "source_count": source_count,
        "source_inventory_digest": _canonical_digest(
            [_locked_source_json(item) for item in sources]
        ),
        "supplemental_source_count": STAGE2_EXPECTED_SUPPLEMENTAL_SOURCE_COUNT,
    }


def _extract_locked_archive(
    archive: Path, destination: Path, lock: Stage2SourceArtifactLock
) -> None:
    expected_files = {f"{lock.archive_root}/{item.path}": item for item in lock.files}
    expected_dirs = {lock.archive_root}
    for name in expected_files:
        parent = PurePosixPath(name).parent
        while parent.as_posix() != ".":
            expected_dirs.add(parent.as_posix())
            parent = parent.parent
    seen: set[str] = set()
    with archive.open("rb") as compressed:
        with zstandard.ZstdDecompressor().stream_reader(compressed) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as bundle:
                for member in bundle:
                    name = member.name
                    _require_safe_member(name)
                    if name in seen:
                        raise Stage2SourceArtifactError(
                            "source archive contains a duplicate member"
                        )
                    seen.add(name)
                    if member.isdir():
                        if name not in expected_dirs:
                            raise Stage2SourceArtifactError(
                                "source archive contains an unknown member"
                            )
                        (destination / name).mkdir(parents=True, exist_ok=True)
                        continue
                    locked = expected_files.get(name)
                    if locked is None or not member.isfile():
                        raise Stage2SourceArtifactError(
                            "source archive contains an unsafe member"
                        )
                    if member.size != locked.size:
                        raise Stage2SourceArtifactError(
                            "source archive member size does not match"
                        )
                    source = bundle.extractfile(member)
                    if source is None:
                        raise Stage2SourceArtifactError(
                            "source archive member cannot be read"
                        )
                    target = destination / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with target.open("xb") as output:
                        while chunk := source.read(_CHUNK_SIZE):
                            digest.update(chunk)
                            output.write(chunk)
                    if digest.hexdigest() != locked.sha256:
                        raise Stage2SourceArtifactError(
                            "source archive member hash does not match"
                        )
    if set(expected_files) - seen:
        raise Stage2SourceArtifactError("source archive is missing a locked member")


def _write_deterministic_archive(
    raw_root: Path, archive: Path, files: Sequence[LockedSourceFile]
) -> None:
    partial = archive.with_name(f".{archive.name}.partial")
    partial.unlink(missing_ok=True)
    try:
        with partial.open("xb") as compressed:
            with zstandard.ZstdCompressor(level=9, threads=1).stream_writer(
                compressed, closefd=False
            ) as writer:
                with tarfile.open(fileobj=writer, mode="w|") as bundle:
                    directories = {ARCHIVE_ROOT}
                    for item in files:
                        parent = PurePosixPath(ARCHIVE_ROOT, item.path).parent
                        while parent.as_posix() != ".":
                            directories.add(parent.as_posix())
                            parent = parent.parent
                    for directory in sorted(directories):
                        info = tarfile.TarInfo(directory)
                        info.type = tarfile.DIRTYPE
                        _normalize_tar_info(info, mode=0o755)
                        bundle.addfile(info)
                    for item in files:
                        info = tarfile.TarInfo(f"{ARCHIVE_ROOT}/{item.path}")
                        info.size = item.size
                        _normalize_tar_info(info, mode=0o644)
                        with (raw_root / item.path).open("rb") as payload:
                            bundle.addfile(info, payload)
        os.replace(partial, archive)
    finally:
        partial.unlink(missing_ok=True)


def _normalize_tar_info(info: tarfile.TarInfo, *, mode: int) -> None:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode = mode


def _verify_exact_filesystem_inventory(root: Path, expected: set[str]) -> None:
    actual: set[str] = set()
    for item in root.rglob("*"):
        if item.is_symlink():
            raise Stage2SourceArtifactError("source root must not contain symlinks")
        if item.is_file():
            actual.add(item.relative_to(root).as_posix())
        elif not item.is_dir():
            raise Stage2SourceArtifactError("source root contains an unsafe entry")
    if actual != expected:
        raise Stage2SourceArtifactError("source root inventory does not match")


def _official_checksum(path: Path, source_path: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise Stage2SourceArtifactError("official checksum cannot be read") from exc
    lines = text.splitlines()
    if len(lines) != 1:
        raise Stage2SourceArtifactError("official checksum format is invalid")
    fields = lines[0].split()
    expected_name = PurePosixPath(source_path).name
    if (
        len(fields) != 2
        or not _is_sha256(fields[0])
        or fields[1].removeprefix("*") != expected_name
    ):
        raise Stage2SourceArtifactError("official checksum format is invalid")
    return fields[0]


def _parse_locked_file(value: object) -> LockedSourceFile:
    if not isinstance(value, Mapping):
        raise Stage2SourceArtifactError("source artifact file entry is invalid")
    _require_keys(value, {"path", "sha256", "size", "source_url"}, label="source file")
    path = _require_text(value, "path", label="source file")
    sha256 = _require_text(value, "sha256", label="source file")
    size = _require_size(value, "size", label="source file")
    source_url = _require_text(value, "source_url", label="source file")
    _require_safe_relative(path)
    if not _is_sha256(sha256):
        raise Stage2SourceArtifactError("source artifact file hash is invalid")
    return LockedSourceFile(path=path, sha256=sha256, size=size, source_url=source_url)


def _parse_locked_source(value: object) -> LockedSource:
    if not isinstance(value, Mapping):
        raise Stage2SourceArtifactError("source artifact source entry is invalid")
    _require_keys(
        value,
        {"checksum_path", "checksum_url", "official_checksum", "path", "source_url"},
        label="locked source",
    )
    path = _require_text(value, "path", label="locked source")
    checksum_path = _require_text(value, "checksum_path", label="locked source")
    _require_safe_relative(path)
    _require_safe_relative(checksum_path)
    return LockedSource(
        path=path,
        source_url=_require_text(value, "source_url", label="locked source"),
        checksum_path=checksum_path,
        checksum_url=_require_text(value, "checksum_url", label="locked source"),
        official_checksum=_require_text(
            value, "official_checksum", label="locked source"
        ),
    )


def _parse_asset(
    value: Mapping[str, object], *, label: str, require_format: bool
) -> ReleaseAsset:
    expected = {"name", "sha256", "size", "url"}
    if require_format:
        expected.add("format")
    _require_keys(value, expected, label=label)
    sha256 = _require_text(value, "sha256", label=label)
    if not _is_sha256(sha256):
        raise Stage2SourceArtifactError(f"{label} hash is invalid")
    return ReleaseAsset(
        name=_require_text(value, "name", label=label),
        url=_require_text(value, "url", label=label),
        sha256=sha256,
        size=_require_size(value, "size", label=label),
        format=(
            _require_text(value, "format", label=label) if require_format else None
        ),
    )


def _verify_asset(path: Path, asset: ReleaseAsset) -> None:
    if path.stat().st_size != asset.size:
        raise Stage2SourceArtifactError("source archive size does not match the lock")
    if sha256_file(path) != asset.sha256:
        raise Stage2SourceArtifactError("source archive hash does not match the lock")


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "tracequant-stage2-source-artifact/1"}
    )
    last_error: BaseException | None = None
    for attempt in range(5):
        target.unlink(missing_ok=True)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                if not response.geturl().startswith("https://"):
                    raise Stage2SourceArtifactError("source download left HTTPS")
                with target.open("xb") as output:
                    shutil.copyfileobj(response, output, length=_CHUNK_SIZE)
            return
        except Stage2SourceArtifactError:
            raise
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(2**attempt)
    target.unlink(missing_ok=True)
    raise Stage2SourceArtifactError("source download failed") from last_error


def _expected_directories(files: set[str]) -> set[str]:
    directories: set[str] = set()
    for name in files:
        parent = PurePosixPath(name).parent
        while parent.as_posix() != ".":
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _require_nonoverlap(first: Path, second: Path) -> None:
    if first == second or first in second.parents or second in first.parents:
        raise Stage2SourceArtifactError("staging root and raw root must not overlap")


def _require_safe_target(target: Path) -> None:
    if target.is_symlink():
        raise Stage2SourceArtifactError("raw target must not be a symlink")
    if not target.exists():
        if not target.parent.is_dir():
            raise Stage2SourceArtifactError("raw target parent must exist")
        return
    if not target.is_dir():
        raise Stage2SourceArtifactError("raw target must be a directory")
    if any(target.iterdir()):
        raise Stage2SourceArtifactError("raw target must be empty")


def _require_external_absolute(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise Stage2SourceArtifactError(f"{label} must be absolute")
    if path.is_symlink():
        raise Stage2SourceArtifactError(f"{label} must not be a symlink")
    resolved = path.resolve()
    repository = Path(__file__).resolve().parents[4]
    if resolved == repository or repository in resolved.parents:
        raise Stage2SourceArtifactError(f"{label} must be outside the repository")
    return resolved


def _require_safe_member(name: str) -> None:
    _require_safe_relative(name)
    if "\\" in name:
        raise Stage2SourceArtifactError("source archive member path is unsafe")


def _require_safe_relative(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise Stage2SourceArtifactError("source artifact member path is unsafe")


def _load_json_object(path: Path, *, label: str) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Stage2SourceArtifactError(f"{label} cannot be read") from exc
    if not isinstance(raw, dict):
        raise Stage2SourceArtifactError(f"{label} must be an object")
    return cast(dict[str, object], raw)


def _require_mapping(
    value: Mapping[str, object], key: str, *, label: str
) -> Mapping[str, object]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise Stage2SourceArtifactError(f"{label} {key} is invalid")
    return result


def _require_text(value: Mapping[str, object], key: str, *, label: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise Stage2SourceArtifactError(f"{label} {key} is invalid")
    return result


def _require_size(value: Mapping[str, object], key: str, *, label: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int) or result < 0:
        raise Stage2SourceArtifactError(f"{label} {key} is invalid")
    return result


def _require_keys(
    value: Mapping[str, object], expected: set[str], *, label: str
) -> None:
    if set(value) != expected:
        raise Stage2SourceArtifactError(f"{label} fields do not match schema")


def _is_sha256(value: str) -> bool:
    return len(value) == _SHA256_LENGTH and all(
        character in "0123456789abcdef" for character in value
    )


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _locked_file_json(item: LockedSourceFile) -> dict[str, object]:
    return {
        "path": item.path,
        "sha256": item.sha256,
        "size": item.size,
        "source_url": item.source_url,
    }


def _locked_source_json(item: LockedSource) -> dict[str, str]:
    return {
        "checksum_path": item.checksum_path,
        "checksum_url": item.checksum_url,
        "official_checksum": item.official_checksum,
        "path": item.path,
        "source_url": item.source_url,
    }


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the fixed Stage 2 r1 source escrow"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    materialize = subparsers.add_parser("materialize")
    materialize.add_argument("--lock", type=Path, required=True)
    materialize.add_argument("--staging-root", type=Path, required=True)
    materialize.add_argument("--raw-root", type=Path, required=True)
    materialize.add_argument("--archive", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--lock", type=Path, required=True)
    verify.add_argument("--raw-root", type=Path, required=True)
    fetch = subparsers.add_parser("fetch-official")
    fetch.add_argument("--catalog-manifest", type=Path, required=True)
    fetch.add_argument("--staging-root", type=Path, required=True)
    fetch.add_argument("--raw-root", type=Path, required=True)
    fetch.add_argument("--workers", type=int, default=8)
    build = subparsers.add_parser("build-release")
    build.add_argument("--raw-root", type=Path, required=True)
    build.add_argument("--catalog-manifest", type=Path, required=True)
    build.add_argument("--output-root", type=Path, required=True)
    build.add_argument("--lock-output", type=Path, required=True)
    rebuild = subparsers.add_parser("rebuild-verify")
    rebuild.add_argument("--lock", type=Path, required=True)
    rebuild.add_argument("--config", type=Path, required=True)
    rebuild.add_argument("--instrument-snapshot", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "materialize":
        result = materialize_source_artifact(
            args.lock,
            args.staging_root,
            args.raw_root,
            archive_path=args.archive,
        )
    elif args.command == "verify":
        lock = load_source_artifact_lock(args.lock)
        verify_source_root(lock, args.raw_root)
        result = args.raw_root.resolve()
    elif args.command == "fetch-official":
        result = fetch_official_sources(
            args.catalog_manifest,
            args.staging_root,
            args.raw_root,
            workers=args.workers,
        )
    elif args.command == "build-release":
        archive, manifest, lock_payload = build_source_release_assets(
            args.raw_root,
            args.catalog_manifest,
            args.output_root,
            repository=RELEASE_REPOSITORY,
            tag=RELEASE_TAG,
        )
        _write_json(args.lock_output, lock_payload)
        print(archive)
        print(manifest)
        print(args.lock_output.resolve())
        return 0
    else:
        result = rebuild_and_verify_catalog(
            args.lock,
            args.config,
            instrument_snapshot_path=args.instrument_snapshot,
        )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
