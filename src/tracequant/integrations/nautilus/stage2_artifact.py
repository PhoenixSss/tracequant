from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, cast

import zstandard

from tracequant.integrations.nautilus.stage2_btceth import (
    require_stage2_catalog_identity,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_COVERAGE_FILENAME,
    STAGE2_DATASET_ID,
    STAGE2_DIGEST_FILENAME,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    STAGE2_MANIFEST_FILENAME,
    Stage2DataError,
)

ARTIFACT_LOCK_SCHEMA: Final = "tracequant-stage2-artifact-lock-v1"
FILE_MANIFEST_SCHEMA: Final = "tracequant-stage2-artifact-files-v1"
ARCHIVE_FORMAT: Final = "tar+zstd"
RELEASE_REPOSITORY: Final = "PhoenixSss/tracequant"
RELEASE_TAG: Final = "stage2-binance-usdm-btceth-202001-202608-r1"
ARCHIVE_ASSET_NAME: Final = f"{STAGE2_DATASET_ID}.catalog.tar.zst"
FILE_MANIFEST_ASSET_NAME: Final = f"{STAGE2_DATASET_ID}.catalog.files.json"
EXPECTED_EVIDENCE_FILES: Final = (
    STAGE2_COVERAGE_FILENAME,
    STAGE2_DIGEST_FILENAME,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    STAGE2_MANIFEST_FILENAME,
)
LOCKED_IDENTITIES: Mapping[str, object] = {
    "acceptance_digest": (
        "5909c878a81f0cdea85a8b8f86efd36d9c9bad5b3f3fb4c0f9960bb0551609cd"
    ),
    "dataset_digest": (
        "e17c6294e0a0e6714e56a44624ade37cff46125c46d8b0ee81ede6093711579c"
    ),
    "instrument_snapshot_checksum": (
        "dd7fab59448a3b530ab70871ec57c375f6758e409004f9673d9d0cac7ee630bd"
    ),
    "market_data_manifest_digest": (
        "a0d9a36bb65ec7c2ec41f47cdf6ba7d20f57ad28d8d3494a69624c60d6d0a110"
    ),
    "runtime_identity": "2.0.0rc4+a0400251110653b6d8ae6a9b5b89c4543fa85a2d",
    "source_manifest_digest": (
        "de86d44c73117e17af2bbcb655cf1c8d4290043fa8854636cc0e4e592a1dc790"
    ),
}
_SHA256_LENGTH: Final = 64
_CHUNK_SIZE: Final = 1024 * 1024


class Stage2ArtifactError(Stage2DataError):
    """Raised when a Stage 2 release artifact fails closed."""


@dataclass(frozen=True)
class LockedFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    sha256: str
    size: int
    format: str | None = None


@dataclass(frozen=True)
class Stage2ArtifactLock:
    path: Path
    dataset_id: str
    archive_root: str
    repository: str
    tag: str
    archive: ReleaseAsset
    file_manifest: ReleaseAsset
    files: tuple[LockedFile, ...]
    identities: Mapping[str, object]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_artifact_lock(path: Path) -> Stage2ArtifactLock:
    lock_path = path.resolve()
    payload = _load_json_object(lock_path, label="artifact lock")
    _require_keys(
        payload,
        {
            "archive_root",
            "dataset_id",
            "files",
            "identities",
            "release",
            "schema",
        },
        label="artifact lock",
    )
    if payload["schema"] != ARTIFACT_LOCK_SCHEMA:
        raise Stage2ArtifactError("artifact lock schema does not match")
    dataset_id = _require_text(payload, "dataset_id", label="artifact lock")
    archive_root = _require_text(payload, "archive_root", label="artifact lock")
    if dataset_id != STAGE2_DATASET_ID or archive_root != dataset_id:
        raise Stage2ArtifactError("artifact lock dataset identity does not match")
    release = _require_mapping(payload, "release", label="artifact lock")
    _require_keys(
        release,
        {"archive_asset", "file_manifest_asset", "repository", "tag"},
        label="artifact lock release",
    )
    repository = _require_text(release, "repository", label="artifact lock release")
    tag = _require_text(release, "tag", label="artifact lock release")
    if repository != RELEASE_REPOSITORY or tag != RELEASE_TAG:
        raise Stage2ArtifactError("artifact lock release identity does not match")
    archive = _parse_asset(
        _require_mapping(release, "archive_asset", label="artifact lock release"),
        label="archive asset",
        require_format=True,
    )
    file_manifest = _parse_asset(
        _require_mapping(release, "file_manifest_asset", label="artifact lock release"),
        label="file manifest asset",
        require_format=False,
    )
    if (
        archive.name != ARCHIVE_ASSET_NAME
        or file_manifest.name != FILE_MANIFEST_ASSET_NAME
    ):
        raise Stage2ArtifactError("artifact lock asset identity does not match")
    expected_prefix = f"https://github.com/{repository}/releases/download/{tag}/"
    if archive.url != f"{expected_prefix}{archive.name}" or file_manifest.url != (
        f"{expected_prefix}{file_manifest.name}"
    ):
        raise Stage2ArtifactError("artifact lock release URL is not immutable")
    if archive.format != ARCHIVE_FORMAT:
        raise Stage2ArtifactError("artifact archive format does not match")
    raw_files = payload["files"]
    if not isinstance(raw_files, list) or not raw_files:
        raise Stage2ArtifactError("artifact lock files are missing")
    files = tuple(_parse_locked_file(item) for item in raw_files)
    paths = tuple(item.path for item in files)
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise Stage2ArtifactError("artifact lock files are not sorted and unique")
    for evidence in EXPECTED_EVIDENCE_FILES:
        if evidence not in paths:
            raise Stage2ArtifactError("artifact lock is missing catalog evidence")
    identities = _require_mapping(payload, "identities", label="artifact lock")
    _validate_identities(identities)
    if dict(identities) != dict(LOCKED_IDENTITIES):
        raise Stage2ArtifactError(
            "artifact lock does not bind the accepted r1 identity"
        )
    return Stage2ArtifactLock(
        path=lock_path,
        dataset_id=dataset_id,
        archive_root=archive_root,
        repository=repository,
        tag=tag,
        archive=archive,
        file_manifest=file_manifest,
        files=files,
        identities=dict(identities),
    )


def verify_catalog(lock: Stage2ArtifactLock, catalog_path: Path) -> None:
    try:
        catalog_mode = catalog_path.lstat().st_mode
    except OSError as exc:
        raise Stage2ArtifactError("catalog path must be a real directory") from exc
    if not stat.S_ISDIR(catalog_mode):
        raise Stage2ArtifactError("catalog path must be a real directory")
    catalog = catalog_path.resolve()
    expected_files = {item.path for item in lock.files}
    expected_directories: set[str] = set()
    for name in expected_files:
        parent = PurePosixPath(name).parent
        while parent.as_posix() != ".":
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    try:
        for item in catalog.rglob("*"):
            name = item.relative_to(catalog).as_posix()
            item_mode = item.lstat().st_mode
            if stat.S_ISREG(item_mode):
                actual_files.add(name)
            elif stat.S_ISDIR(item_mode):
                actual_directories.add(name)
            else:
                raise Stage2ArtifactError(
                    "catalog file inventory does not match artifact lock"
                )
    except OSError as exc:
        raise Stage2ArtifactError(
            "catalog file inventory does not match artifact lock"
        ) from exc
    if actual_files != expected_files or actual_directories != expected_directories:
        raise Stage2ArtifactError("catalog file inventory does not match artifact lock")
    for locked in lock.files:
        candidate = catalog / locked.path
        candidate_stat = candidate.lstat()
        if not stat.S_ISREG(candidate_stat.st_mode):
            raise Stage2ArtifactError(
                "catalog file inventory does not match artifact lock"
            )
        if candidate_stat.st_size != locked.size:
            raise Stage2ArtifactError("catalog file size does not match artifact lock")
        if sha256_file(candidate) != locked.sha256:
            raise Stage2ArtifactError("catalog file hash does not match artifact lock")
    _verify_sidecar_identities(lock, catalog)
    try:
        require_stage2_catalog_identity(catalog)
    except (OSError, Stage2DataError, ValueError) as exc:
        raise Stage2ArtifactError("catalog Nautilus identity does not match") from exc


def materialize_catalog(
    lock_path: Path,
    staging_root: Path,
    catalog_path: Path,
    *,
    archive_path: Path | None = None,
) -> Path:
    lock = load_artifact_lock(lock_path)
    staging = _require_external_absolute(staging_root, label="staging root")
    target = _require_external_absolute(catalog_path, label="catalog path")
    if staging == target or staging in target.parents or target in staging.parents:
        raise Stage2ArtifactError("staging root and catalog path must not overlap")
    _require_safe_target(target)
    staging.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="stage2-materialize-", dir=staging))
    publish: Path | None = None
    try:
        publish = Path(tempfile.mkdtemp(prefix=".stage2-publish-", dir=target.parent))
        archive = work / lock.archive.name
        if archive_path is None:
            _download(lock.archive.url, archive)
        else:
            supplied = archive_path.resolve()
            if not supplied.is_file() or supplied.is_symlink():
                raise Stage2ArtifactError("supplied archive is not a regular file")
            shutil.copyfile(supplied, archive)
        _verify_asset(archive, lock.archive)
        extracted = publish / "extracted"
        extracted.mkdir()
        _extract_locked_archive(archive, extracted, lock)
        candidate = extracted / lock.archive_root
        verify_catalog(lock, candidate)
        os.replace(candidate, target)
        return target
    except Stage2ArtifactError:
        raise
    except (OSError, tarfile.TarError, zstandard.ZstdError) as exc:
        raise Stage2ArtifactError("artifact materialization failed") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if publish is not None:
            shutil.rmtree(publish, ignore_errors=True)


def build_release_assets(
    catalog_path: Path,
    output_root: Path,
    *,
    repository: str,
    tag: str,
) -> tuple[Path, Path, dict[str, object]]:
    catalog = catalog_path.resolve()
    output = output_root.resolve()
    if not catalog.is_dir() or catalog.is_symlink():
        raise Stage2ArtifactError("catalog path must be a real directory")
    if output == catalog or output in catalog.parents or catalog in output.parents:
        raise Stage2ArtifactError("publication output and catalog must not overlap")
    if repository != RELEASE_REPOSITORY or tag != RELEASE_TAG:
        raise Stage2ArtifactError("release identity does not match")
    output.mkdir(parents=True, exist_ok=True)
    if any(item.is_symlink() for item in catalog.rglob("*")):
        raise Stage2ArtifactError("catalog must not contain symbolic links")
    files = tuple(
        LockedFile(
            path=item.relative_to(catalog).as_posix(),
            sha256=sha256_file(item),
            size=item.stat().st_size,
        )
        for item in sorted(catalog.rglob("*"))
        if item.is_file() and not item.is_symlink()
    )
    if not files or tuple(item.path for item in files) != tuple(
        sorted(item.path for item in files)
    ):
        raise Stage2ArtifactError("catalog files are not sorted and unique")
    archive_name = ARCHIVE_ASSET_NAME
    manifest_name = FILE_MANIFEST_ASSET_NAME
    archive_path = output / archive_name
    manifest_path = output / manifest_name
    _write_deterministic_archive(catalog, archive_path, files)
    identities = _catalog_identities(catalog)
    if identities != dict(LOCKED_IDENTITIES):
        raise Stage2ArtifactError("catalog does not match the accepted r1 identity")
    manifest_payload: dict[str, object] = {
        "archive": {
            "format": ARCHIVE_FORMAT,
            "name": archive_name,
            "sha256": sha256_file(archive_path),
            "size": archive_path.stat().st_size,
        },
        "archive_root": STAGE2_DATASET_ID,
        "dataset_id": STAGE2_DATASET_ID,
        "files": [_locked_file_json(item) for item in files],
        "identities": identities,
        "schema": FILE_MANIFEST_SCHEMA,
    }
    _write_json(manifest_path, manifest_payload)
    base_url = f"https://github.com/{repository}/releases/download/{tag}"
    lock_payload: dict[str, object] = {
        "archive_root": STAGE2_DATASET_ID,
        "dataset_id": STAGE2_DATASET_ID,
        "files": [_locked_file_json(item) for item in files],
        "identities": identities,
        "release": {
            "archive_asset": {
                "format": ARCHIVE_FORMAT,
                "name": archive_name,
                "sha256": manifest_payload["archive"]["sha256"],  # type: ignore[index]
                "size": manifest_payload["archive"]["size"],  # type: ignore[index]
                "url": f"{base_url}/{archive_name}",
            },
            "file_manifest_asset": {
                "name": manifest_name,
                "sha256": sha256_file(manifest_path),
                "size": manifest_path.stat().st_size,
                "url": f"{base_url}/{manifest_name}",
            },
            "repository": repository,
            "tag": tag,
        },
        "schema": ARTIFACT_LOCK_SCHEMA,
    }
    return archive_path, manifest_path, lock_payload


def _extract_locked_archive(
    archive: Path,
    destination: Path,
    lock: Stage2ArtifactLock,
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
                        raise Stage2ArtifactError("archive contains a duplicate member")
                    seen.add(name)
                    if member.isdir():
                        if name not in expected_dirs:
                            raise Stage2ArtifactError(
                                "archive contains an unknown member"
                            )
                        (destination / name).mkdir(parents=True, exist_ok=True)
                        continue
                    locked = expected_files.get(name)
                    if locked is None or not member.isfile():
                        raise Stage2ArtifactError("archive contains an unsafe member")
                    if member.size != locked.size:
                        raise Stage2ArtifactError("archive member size does not match")
                    source = bundle.extractfile(member)
                    if source is None:
                        raise Stage2ArtifactError("archive member cannot be read")
                    target = destination / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with target.open("xb") as output:
                        while chunk := source.read(_CHUNK_SIZE):
                            digest.update(chunk)
                            output.write(chunk)
                    if digest.hexdigest() != locked.sha256:
                        raise Stage2ArtifactError("archive member hash does not match")
    if set(expected_files) - seen:
        raise Stage2ArtifactError("archive is missing a locked member")


def _write_deterministic_archive(
    catalog: Path, archive: Path, files: Sequence[LockedFile]
) -> None:
    partial = archive.with_name(f".{archive.name}.partial")
    partial.unlink(missing_ok=True)
    try:
        with partial.open("xb") as compressed:
            with zstandard.ZstdCompressor(level=19, threads=0).stream_writer(
                compressed, closefd=False
            ) as writer:
                with tarfile.open(fileobj=writer, mode="w|") as bundle:
                    directories = {STAGE2_DATASET_ID}
                    for item in files:
                        parent = PurePosixPath(STAGE2_DATASET_ID, item.path).parent
                        while parent.as_posix() != ".":
                            directories.add(parent.as_posix())
                            parent = parent.parent
                    for directory in sorted(directories):
                        info = tarfile.TarInfo(directory)
                        info.type = tarfile.DIRTYPE
                        _normalize_tar_info(info, mode=0o755)
                        bundle.addfile(info)
                    for item in files:
                        source = catalog / item.path
                        info = tarfile.TarInfo(f"{STAGE2_DATASET_ID}/{item.path}")
                        info.size = item.size
                        _normalize_tar_info(info, mode=0o644)
                        with source.open("rb") as payload:
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


def _verify_sidecar_identities(lock: Stage2ArtifactLock, catalog: Path) -> None:
    actual = _catalog_identities(catalog)
    if actual != dict(lock.identities):
        raise Stage2ArtifactError(
            "catalog Stage 2 identities do not match artifact lock"
        )


def _catalog_identities(catalog: Path) -> dict[str, object]:
    digest = _load_json_object(catalog / STAGE2_DIGEST_FILENAME, label="dataset digest")
    manifest = _load_json_object(
        catalog / STAGE2_MANIFEST_FILENAME, label="source manifest"
    )
    snapshot = _load_json_object(
        catalog / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME, label="instrument snapshot"
    )
    required = {
        "dataset_id",
        "instrument_snapshot",
        "market_data_manifest_digest",
        "runtime_identity",
        "source_manifest_digest",
    }
    if not required.issubset(digest):
        raise Stage2ArtifactError("dataset digest identities are incomplete")
    snapshot_identity = digest["instrument_snapshot"]
    if not isinstance(snapshot_identity, Mapping):
        raise Stage2ArtifactError("dataset digest snapshot identity is invalid")
    if manifest.get("instrument_snapshot") != snapshot_identity:
        raise Stage2ArtifactError("source manifest snapshot identity does not match")
    if snapshot.get("checksum_sha256") != snapshot_identity.get("checksum_sha256"):
        raise Stage2ArtifactError("instrument snapshot checksum does not match")
    canonical = json.dumps(
        digest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return {
        "acceptance_digest": LOCKED_IDENTITIES["acceptance_digest"],
        "dataset_digest": hashlib.sha256(canonical).hexdigest(),
        "instrument_snapshot_checksum": snapshot_identity.get("checksum_sha256"),
        "market_data_manifest_digest": digest["market_data_manifest_digest"],
        "runtime_identity": digest["runtime_identity"],
        "source_manifest_digest": digest["source_manifest_digest"],
    }


def _validate_identities(identities: Mapping[str, object]) -> None:
    expected_keys = {
        "acceptance_digest",
        "dataset_digest",
        "instrument_snapshot_checksum",
        "market_data_manifest_digest",
        "runtime_identity",
        "source_manifest_digest",
    }
    _require_keys(identities, expected_keys, label="artifact identities")
    for key in expected_keys - {"runtime_identity"}:
        value = identities[key]
        if not isinstance(value, str) or not _is_sha256(value):
            raise Stage2ArtifactError("artifact identity digest is invalid")
    runtime = identities["runtime_identity"]
    if not isinstance(runtime, str) or not runtime:
        raise Stage2ArtifactError("artifact runtime identity is invalid")


def _parse_locked_file(value: object) -> LockedFile:
    if not isinstance(value, Mapping):
        raise Stage2ArtifactError("artifact lock file entry is invalid")
    _require_keys(value, {"path", "sha256", "size"}, label="artifact lock file")
    path = _require_text(value, "path", label="artifact lock file")
    _require_safe_relative(path)
    sha256 = _require_text(value, "sha256", label="artifact lock file")
    size = _require_size(value, "size", label="artifact lock file")
    if not _is_sha256(sha256):
        raise Stage2ArtifactError("artifact lock file hash is invalid")
    return LockedFile(path=path, sha256=sha256, size=size)


def _parse_asset(
    value: Mapping[str, object], *, label: str, require_format: bool
) -> ReleaseAsset:
    expected = {"name", "sha256", "size", "url"}
    if require_format:
        expected.add("format")
    _require_keys(value, expected, label=label)
    sha256 = _require_text(value, "sha256", label=label)
    if not _is_sha256(sha256):
        raise Stage2ArtifactError(f"{label} hash is invalid")
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
        raise Stage2ArtifactError("archive size does not match artifact lock")
    if sha256_file(path) != asset.sha256:
        raise Stage2ArtifactError("archive hash does not match artifact lock")


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "tracequant-stage2-artifact/1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            final_url = response.geturl()
            if not final_url.startswith("https://"):
                raise Stage2ArtifactError("artifact download left HTTPS")
            with target.open("xb") as output:
                shutil.copyfileobj(response, output, length=_CHUNK_SIZE)
    except Stage2ArtifactError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise Stage2ArtifactError("artifact download failed") from exc


def _require_safe_target(target: Path) -> None:
    if target.is_symlink():
        raise Stage2ArtifactError("catalog target must not be a symlink")
    if not target.exists():
        if not target.parent.is_dir():
            raise Stage2ArtifactError("catalog target parent must exist")
        return
    if not target.is_dir():
        raise Stage2ArtifactError("catalog target must be a directory")
    if any(target.iterdir()):
        raise Stage2ArtifactError("catalog target must be empty")


def _require_external_absolute(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise Stage2ArtifactError(f"{label} must be absolute")
    if path.is_symlink():
        raise Stage2ArtifactError(f"{label} must not be a symlink")
    resolved = path.resolve()
    repository = Path(__file__).resolve().parents[4]
    if resolved == repository or repository in resolved.parents:
        raise Stage2ArtifactError(f"{label} must be outside the repository")
    return resolved


def _require_safe_member(name: str) -> None:
    _require_safe_relative(name)
    if "\\" in name:
        raise Stage2ArtifactError("archive member path is unsafe")


def _require_safe_relative(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise Stage2ArtifactError("artifact member path is unsafe")


def _load_json_object(path: Path, *, label: str) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Stage2ArtifactError(f"{label} cannot be read") from exc
    if not isinstance(raw, dict):
        raise Stage2ArtifactError(f"{label} must be an object")
    return cast(dict[str, object], raw)


def _require_mapping(
    value: Mapping[str, object], key: str, *, label: str
) -> Mapping[str, object]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise Stage2ArtifactError(f"{label} {key} is invalid")
    return result


def _require_text(value: Mapping[str, object], key: str, *, label: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise Stage2ArtifactError(f"{label} {key} is invalid")
    return result


def _require_size(value: Mapping[str, object], key: str, *, label: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int) or result < 0:
        raise Stage2ArtifactError(f"{label} {key} is invalid")
    return result


def _require_keys(
    value: Mapping[str, object], expected: set[str], *, label: str
) -> None:
    if set(value) != expected:
        raise Stage2ArtifactError(f"{label} fields do not match schema")


def _is_sha256(value: str) -> bool:
    return len(value) == _SHA256_LENGTH and all(
        character in "0123456789abcdef" for character in value
    )


def _locked_file_json(item: LockedFile) -> dict[str, object]:
    return {"path": item.path, "sha256": item.sha256, "size": item.size}


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the fixed Stage 2 r1 artifact")
    subparsers = parser.add_subparsers(dest="command", required=True)
    materialize = subparsers.add_parser("materialize")
    materialize.add_argument("--lock", type=Path, required=True)
    materialize.add_argument("--staging-root", type=Path, required=True)
    materialize.add_argument("--catalog-path", type=Path, required=True)
    materialize.add_argument("--archive", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--lock", type=Path, required=True)
    verify.add_argument("--catalog-path", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "materialize":
        result = materialize_catalog(
            args.lock,
            args.staging_root,
            args.catalog_path,
            archive_path=args.archive,
        )
        print(result)
        return 0
    lock = load_artifact_lock(args.lock)
    verify_catalog(lock, args.catalog_path)
    print(args.catalog_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
