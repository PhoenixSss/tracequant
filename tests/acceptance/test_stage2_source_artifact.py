from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path
from typing import cast

import pytest
import zstandard

import tracequant.integrations.nautilus.stage2_source_artifact as source_artifact
from tracequant.integrations.nautilus.stage2_source_artifact import (
    ARCHIVE_FORMAT,
    ARCHIVE_ROOT,
    RELEASE_TAG,
    SOURCE_ARTIFACT_LOCK_SCHEMA,
    Stage2SourceArtifactError,
    build_source_release_assets,
    load_source_artifact_lock,
    materialize_source_artifact,
    sha256_file,
    verify_source_root,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRACKED_LOCK = (
    REPOSITORY_ROOT
    / "config/datasets/binance-usdm-btceth-202001-202608-r1.sources.lock.json"
)
PUBLICATION_RECORD = (
    REPOSITORY_ROOT / "docs/product/stage2-btceth-source-publication.json"
)
CATALOG_LOCK = (
    REPOSITORY_ROOT / "config/datasets/binance-usdm-btceth-202001-202608-r1.lock.json"
)
CATALOG_PUBLICATION = (
    REPOSITORY_ROOT / "docs/product/stage2-btceth-dataset-publication.json"
)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _fixture_source(
    raw_root: Path,
    *,
    relative: str,
    data_type: str,
    instrument_id: str,
    ordinal: int,
) -> dict[str, object]:
    zip_path = raw_root / relative
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(f"fixed-source-{ordinal}\n".encode())
    digest = sha256_file(zip_path)
    checksum = Path(f"{zip_path}.CHECKSUM")
    checksum.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    source_url = f"https://data.binance.vision/{relative}"
    return {
        "checksum_url": f"{source_url}.CHECKSUM",
        "data_type": data_type,
        "end_ns": ordinal + 1,
        "instrument_id": instrument_id,
        "path": relative,
        "rows": 1,
        "sha256": digest,
        "source_kind": "binance_public_data",
        "source_url": source_url,
        "start_ns": ordinal,
    }


def _bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, dict[str, object]]:
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    primary = _fixture_source(
        raw_root,
        relative=("data/futures/um/monthly/klines/BTCUSDT/15m/BTCUSDT-15m-2020-01.zip"),
        data_type="bars",
        instrument_id="BTCUSDT-PERP.BINANCE",
        ordinal=1,
    )
    supplemental = _fixture_source(
        raw_root,
        relative=(
            "data/futures/um/daily/markPriceKlines/BTCUSDT/15m/"
            "BTCUSDT-15m-2019-12-31.zip"
        ),
        data_type="mark_price",
        instrument_id="BTCUSDT-PERP.BINANCE",
        ordinal=2,
    )
    snapshot = {
        "checksum_sha256": "a" * 64,
        "fetched_at": "2026-09-15T03:08:10.006744Z",
        "filename": "stage2_instrument_snapshot.json",
    }
    identity_keys = (
        "checksum_url",
        "data_type",
        "end_ns",
        "instrument_id",
        "rows",
        "sha256",
        "source_url",
        "start_ns",
    )
    market_digest = _digest(
        {
            "sources": [{key: primary[key] for key in identity_keys}],
            "supplemental_sources": [{key: supplemental[key] for key in identity_keys}],
        }
    )
    source_digest = _digest(
        {
            "instrument_snapshot": snapshot,
            "market_data_manifest_digest": market_digest,
        }
    )
    identities: dict[str, object] = {
        "acceptance_digest": "b" * 64,
        "dataset_digest": "c" * 64,
        "instrument_snapshot_checksum": "a" * 64,
        "market_data_manifest_digest": market_digest,
        "runtime_identity": "fixture-runtime",
        "source_manifest_digest": source_digest,
    }
    monkeypatch.setattr(source_artifact, "LOCKED_IDENTITIES", identities)
    monkeypatch.setattr(source_artifact, "STAGE2_EXPECTED_SOURCE_COUNT", 1)
    monkeypatch.setattr(source_artifact, "STAGE2_EXPECTED_SUPPLEMENTAL_SOURCE_COUNT", 1)
    expected_urls = tuple(
        sorted(
            (
                cast(str, item["path"]),
                cast(str, item["source_url"]),
                cast(str, item["checksum_url"]),
            )
            for item in (primary, supplemental)
        )
    )
    monkeypatch.setattr(source_artifact, "_expected_source_urls", lambda: expected_urls)
    catalog_manifest = tmp_path / "stage2_source_manifest.json"
    _write_json(
        catalog_manifest,
        {
            "dataset_id": "binance-usdm-btceth-202001-202608-r1",
            "instrument_snapshot": snapshot,
            "nautilus_version": "2.0.0rc4",
            "schema": "tracequant-stage2-source-v1",
            "sources": [primary],
            "supplemental_sources": [supplemental],
        },
    )
    archive, manifest, lock = build_source_release_assets(
        raw_root,
        catalog_manifest,
        tmp_path / "publication",
        repository="PhoenixSss/tracequant",
        tag=RELEASE_TAG,
    )
    lock_path = tmp_path / "source.lock.json"
    _write_json(lock_path, lock)
    return archive, manifest, lock_path, lock


def _malicious_archive(path: Path, case: str) -> None:
    with path.open("wb") as compressed:
        with zstandard.ZstdCompressor().stream_writer(
            compressed, closefd=False
        ) as writer:
            with tarfile.open(fileobj=writer, mode="w|") as bundle:
                if case == "duplicate":
                    for _ in range(2):
                        duplicate = tarfile.TarInfo(ARCHIVE_ROOT)
                        duplicate.type = tarfile.DIRTYPE
                        bundle.addfile(duplicate)
                    return
                names = {
                    "absolute": "/absolute",
                    "symlink": f"{ARCHIVE_ROOT}/escape",
                    "traversal": "../escape",
                    "unknown": f"{ARCHIVE_ROOT}/unknown",
                }
                info = tarfile.TarInfo(names[case])
                if case == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = "../../outside"
                    bundle.addfile(info)
                else:
                    info.size = 1
                    bundle.addfile(info, fileobj=io.BytesIO(b"x"))


def test_stage2_source_artifact_materializes_only_locked_raw_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, manifest, lock_path, lock_payload = _bundle(tmp_path, monkeypatch)
    lock = load_source_artifact_lock(lock_path)
    assert lock_payload["schema"] == SOURCE_ARTIFACT_LOCK_SCHEMA
    release = cast(dict[str, object], lock_payload["release"])
    archive_asset = cast(dict[str, object], release["archive_asset"])
    assert archive_asset["format"] == ARCHIVE_FORMAT
    assert archive_asset["sha256"] == sha256_file(archive)
    manifest_asset = cast(dict[str, object], release["file_manifest_asset"])
    assert manifest_asset["sha256"] == sha256_file(manifest)

    staging = tmp_path / "staging"
    staging.mkdir()
    first = tmp_path / "first" / ARCHIVE_ROOT
    first.parent.mkdir()
    materialize_source_artifact(lock_path, staging, first, archive_path=archive)
    verify_source_root(lock, first)
    assert len(tuple(first.rglob("*.zip"))) == 2
    assert len(tuple(first.rglob("*.CHECKSUM"))) == 2

    for item in staging.iterdir():
        if item.is_dir():
            os.rmdir(item)
        else:
            item.unlink()
    staging.rmdir()
    staging.mkdir()
    second = tmp_path / "second" / ARCHIVE_ROOT
    second.parent.mkdir()
    materialize_source_artifact(lock_path, staging, second, archive_path=archive)
    verify_source_root(lock, second)

    (second / lock.sources[0].path).unlink()
    with pytest.raises(Stage2SourceArtifactError, match="inventory"):
        verify_source_root(lock, second)

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "keep").write_text("preserve", encoding="utf-8")
    with pytest.raises(Stage2SourceArtifactError, match="empty"):
        materialize_source_artifact(lock_path, staging, nonempty, archive_path=archive)
    assert (nonempty / "keep").read_text(encoding="utf-8") == "preserve"

    corrupted = tmp_path / "corrupted.tar.zst"
    corrupted.write_bytes(archive.read_bytes() + b"corrupt")
    with pytest.raises(Stage2SourceArtifactError, match="size"):
        materialize_source_artifact(
            lock_path,
            staging,
            tmp_path / "corrupt-target",
            archive_path=corrupted,
        )
    assert not (tmp_path / "corrupt-target").exists()


def test_stage2_source_artifact_verify_is_offline_and_checks_official_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, _manifest, lock_path, _lock_payload = _bundle(tmp_path, monkeypatch)
    staging = tmp_path / "staging"
    staging.mkdir()
    installed = tmp_path / "installed"
    materialize_source_artifact(lock_path, staging, installed, archive_path=archive)
    lock = load_source_artifact_lock(lock_path)
    monkeypatch.setattr(
        source_artifact,
        "_download",
        lambda *_args, **_kwargs: pytest.fail("offline verify attempted a download"),
    )
    verify_source_root(lock, installed)
    checksum_path = installed / lock.sources[0].checksum_path
    checksum_path.write_text(f"{'0' * 64}  wrong.zip\n", encoding="utf-8")
    with pytest.raises(Stage2SourceArtifactError, match="size|hash|checksum"):
        verify_source_root(lock, installed)


def test_stage2_source_artifact_publishes_atomically_and_preserves_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, _manifest, lock_path, _lock_payload = _bundle(tmp_path, monkeypatch)
    staging = tmp_path / "staging"
    staging.mkdir()
    target_parent = tmp_path / "raw-roots"
    target_parent.mkdir()
    target = target_parent / "installed"
    original_replace = os.replace
    published_from_target_filesystem = False

    def require_target_filesystem(source: Path, destination: Path) -> None:
        nonlocal published_from_target_filesystem
        source_path = Path(source)
        destination_path = Path(destination)
        if destination_path == target:
            assert target_parent in source_path.parents
            assert staging not in source_path.parents
            published_from_target_filesystem = True
        original_replace(source_path, destination_path)

    monkeypatch.setattr(os, "replace", require_target_filesystem)
    materialize_source_artifact(lock_path, staging, target, archive_path=archive)
    assert published_from_target_filesystem

    preserved = target_parent / "preserved-empty-target"
    preserved.mkdir()

    def fail_publish(source: Path, destination: Path) -> None:
        if Path(destination) == preserved:
            raise OSError("simulated atomic publish failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_publish)
    with pytest.raises(Stage2SourceArtifactError, match="materialization failed"):
        materialize_source_artifact(
            lock_path,
            staging,
            preserved,
            archive_path=archive,
        )
    assert preserved.is_dir()
    assert not any(preserved.iterdir())

    target_link = target_parent / "target-link"
    target_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(Stage2SourceArtifactError, match="symlink"):
        materialize_source_artifact(
            lock_path,
            staging,
            target_link,
            archive_path=archive,
        )


def test_stage2_source_artifact_rejects_lock_identity_and_inventory_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _archive, _manifest, lock_path, lock_payload = _bundle(tmp_path, monkeypatch)
    identities = cast(dict[str, object], lock_payload["identities"])
    identities["dataset_digest"] = "f" * 64
    _write_json(lock_path, lock_payload)
    with pytest.raises(Stage2SourceArtifactError, match="accepted r1 identities"):
        load_source_artifact_lock(lock_path)

    inventory_root = tmp_path / "inventory"
    inventory_root.mkdir()
    _archive, _manifest, lock_path, lock_payload = _bundle(inventory_root, monkeypatch)
    sources = cast(list[dict[str, object]], lock_payload["sources"])
    sources[0]["source_url"] = "https://data.binance.vision/unlocked.zip"
    _write_json(lock_path, lock_payload)
    with pytest.raises(Stage2SourceArtifactError, match="URL identity"):
        load_source_artifact_lock(lock_path)


def test_stage2_source_rebuild_restores_exact_raw_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, _manifest, lock_path, _lock_payload = _bundle(tmp_path, monkeypatch)
    staging = tmp_path / "staging"
    staging.mkdir()
    raw_root = tmp_path / "installed"
    materialize_source_artifact(lock_path, staging, raw_root, archive_path=archive)
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    config = tmp_path / "stage2.toml"
    config.write_text(
        "\n".join(
            (
                'schema = "tracequant-stage2-dataset-v1"',
                'dataset_id = "binance-usdm-btceth-202001-202608-r1"',
                'nautilus_version = "2.0.0rc4"',
                'environment = "offline"',
                'source = "binance-public-data"',
                'market = "futures/um"',
                'archive_frequency = "monthly"',
                'window_start = "2020-01-01T00:00:00Z"',
                'window_end = "2026-09-01T00:00:00Z"',
                'instrument_ids = ["BTCUSDT-PERP.BINANCE", "ETHUSDT-PERP.BINANCE"]',
                'bar_intervals = ["15m", "1h", "4h"]',
                'bar_aggregation = "LAST-EXTERNAL"',
                f'raw_root = "{raw_root}"',
                f'catalog_path = "{catalog}"',
                "",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        source_artifact, "fetch_stage2_instruments", lambda: ((), object())
    )
    monkeypatch.setattr(
        source_artifact,
        "instrument_snapshot_payload",
        lambda *_args, **_kwargs: {"checksum_sha256": "a" * 64},
    )

    def generate(*_args: object, **_kwargs: object) -> tuple[None, None, Path]:
        (raw_root / "stage2_instrument_snapshot.json").write_text(
            "generated sidecar", encoding="utf-8"
        )
        return None, None, catalog

    monkeypatch.setattr(source_artifact, "prepare_stage2_combined_catalog", generate)
    monkeypatch.setattr(
        source_artifact, "require_stage2_catalog_identity", lambda _catalog: None
    )
    lock = load_source_artifact_lock(lock_path)
    monkeypatch.setattr(
        source_artifact, "_catalog_identities", lambda _catalog: dict(lock.identities)
    )
    assert source_artifact.rebuild_and_verify_catalog(lock_path, config) == catalog
    assert not (raw_root / "stage2_instrument_snapshot.json").exists()
    verify_source_root(lock, raw_root)


@pytest.mark.parametrize(
    "case", ["absolute", "duplicate", "symlink", "traversal", "unknown"]
)
def test_stage2_source_artifact_rejects_unsafe_archive_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    _archive, _manifest, lock_path, lock_payload = _bundle(tmp_path, monkeypatch)
    malicious = tmp_path / f"malicious-{case}.tar.zst"
    _malicious_archive(malicious, case)
    release = cast(dict[str, object], lock_payload["release"])
    archive_asset = cast(dict[str, object], release["archive_asset"])
    archive_asset["sha256"] = sha256_file(malicious)
    archive_asset["size"] = malicious.stat().st_size
    _write_json(lock_path, lock_payload)
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(Stage2SourceArtifactError, match="unsafe|duplicate|unknown"):
        materialize_source_artifact(
            lock_path,
            staging,
            tmp_path / "target",
            archive_path=malicious,
        )


def test_tracked_stage2_source_publication_binds_exact_inventory_and_preserves_catalog_release() -> (
    None
):
    source_lock = cast(
        dict[str, object], json.loads(TRACKED_LOCK.read_text(encoding="utf-8"))
    )
    publication = cast(
        dict[str, object], json.loads(PUBLICATION_RECORD.read_text(encoding="utf-8"))
    )
    catalog_lock = cast(
        dict[str, object], json.loads(CATALOG_LOCK.read_text(encoding="utf-8"))
    )
    catalog_publication = cast(
        dict[str, object], json.loads(CATALOG_PUBLICATION.read_text(encoding="utf-8"))
    )
    inventory = cast(dict[str, object], source_lock["source_inventory"])
    release = cast(dict[str, object], source_lock["release"])
    assert inventory == {
        "file_count": 1636,
        "primary_source_count": 800,
        "source_count": 818,
        "source_inventory_digest": inventory["source_inventory_digest"],
        "supplemental_source_count": 18,
    }
    assert len(cast(list[object], source_lock["files"])) == 1636
    assert len(cast(list[object], source_lock["sources"])) == 818
    assert source_lock["identities"] == catalog_lock["identities"]
    assert cast(str, release["tag"]) == RELEASE_TAG
    assert cast(dict[str, object], publication["source_artifact_lock"])[
        "sha256"
    ] == sha256_file(TRACKED_LOCK)
    assert publication["identities"] == source_lock["identities"]
    assert (
        cast(dict[str, object], publication["validation"])["release_restore_count"] == 2
    )
    assert (
        cast(dict[str, object], publication["validation"])[
            "catalog_rebuild_identity_match"
        ]
        is True
    )
    assert (
        cast(dict[str, object], publication["retention"])["independent_backup"]
        == "verified-operator-managed-persistent-copy-outside-release-and-temporary-storage"
    )
    serialized = json.dumps(publication, sort_keys=True)
    assert "/home/" not in serialized
    assert "/tmp/" not in serialized
    assert catalog_publication["release"] == {
        "archive": {
            "format": "tar+zstd",
            "name": "binance-usdm-btceth-202001-202608-r1.catalog.tar.zst",
            "sha256": "54094e48796c0829ad8a8484361eb1654c29346aeac87dfde7b7d9e34dc45ae4",
            "size": 26319927,
        },
        "file_manifest": {
            "name": "binance-usdm-btceth-202001-202608-r1.catalog.files.json",
            "sha256": "b4ea808e09cec123be9ad840319ddc79d46e5190bf320c28609a0eb89110fb23",
            "size": 5111,
        },
        "tag": "stage2-binance-usdm-btceth-202001-202608-r1",
        "url": (
            "https://github.com/PhoenixSss/tracequant/releases/tag/"
            "stage2-binance-usdm-btceth-202001-202608-r1"
        ),
    }
