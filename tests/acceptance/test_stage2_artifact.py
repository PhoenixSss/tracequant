from __future__ import annotations

import io
import json
import tarfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
import zstandard
from nautilus_trader.model import (
    CryptoPerpetual,
    Currency,
    InstrumentId,
    Price,
    Quantity,
    Symbol,
)
from nautilus_trader.persistence import ParquetDataCatalog

import tracequant.integrations.nautilus.stage2_artifact as artifact_module
from tracequant.integrations.nautilus.stage2_artifact import (
    ARCHIVE_FORMAT,
    ARTIFACT_LOCK_SCHEMA,
    Stage2ArtifactError,
    build_release_assets,
    load_artifact_lock,
    materialize_catalog,
    sha256_file,
    verify_catalog,
)
from tracequant.integrations.nautilus.stage2_btceth import (
    instrument_snapshot_payload,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_COVERAGE_FILENAME,
    STAGE2_DATASET_ID,
    STAGE2_DIGEST_FILENAME,
    STAGE2_INSTRUMENT_IDS,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    STAGE2_MANIFEST_FILENAME,
    STAGE2_NAUTILUS_VERSION,
    STAGE2_SOURCE_SCHEMA,
    write_json,
)

TAG = "stage2-binance-usdm-btceth-202001-202608-r1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TRACKED_LOCK = (
    REPOSITORY_ROOT / "config/datasets/binance-usdm-btceth-202001-202608-r1.lock.json"
)
PUBLICATION_RECORD = (
    REPOSITORY_ROOT / "docs/product/stage2-btceth-dataset-publication.json"
)


def _instrument(instrument_id: str, symbol: str, base: str) -> CryptoPerpetual:
    usdt = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(instrument_id),
        raw_symbol=Symbol(symbol),
        base_currency=Currency.from_str(base),
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        ts_event=0,
        ts_init=0,
    )


def _catalog(root: Path) -> Path:
    catalog_path = root / "catalog"
    catalog_path.mkdir(parents=True)
    instruments = (
        _instrument(STAGE2_INSTRUMENT_IDS[0], "BTCUSDT", "BTC"),
        _instrument(STAGE2_INSTRUMENT_IDS[1], "ETHUSDT", "ETH"),
    )
    ParquetDataCatalog(str(catalog_path)).write_instruments(list(instruments))
    snapshot = instrument_snapshot_payload(
        instruments,
        fetched_at=datetime(2026, 9, 15, 3, 8, 10, tzinfo=UTC),
    )
    snapshot_identity = {
        "checksum_sha256": snapshot["checksum_sha256"],
        "fetched_at": snapshot["fetched_at"],
        "filename": STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    }
    write_json(catalog_path / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME, snapshot)
    write_json(
        catalog_path / STAGE2_MANIFEST_FILENAME,
        {
            "dataset_id": STAGE2_DATASET_ID,
            "instrument_snapshot": snapshot_identity,
            "nautilus_version": STAGE2_NAUTILUS_VERSION,
            "schema": STAGE2_SOURCE_SCHEMA,
            "sources": [],
            "supplemental_sources": [],
        },
    )
    write_json(
        catalog_path / STAGE2_DIGEST_FILENAME,
        {
            "coverage": [],
            "dataset_id": STAGE2_DATASET_ID,
            "instrument_snapshot": snapshot_identity,
            "market_data_manifest_digest": "a" * 64,
            "nautilus_version": STAGE2_NAUTILUS_VERSION,
            "runtime_identity": ("2.0.0rc4+a0400251110653b6d8ae6a9b5b89c4543fa85a2d"),
            "source_manifest_digest": "b" * 64,
        },
    )
    write_json(
        catalog_path / STAGE2_COVERAGE_FILENAME,
        {"dataset_id": STAGE2_DATASET_ID, "series": []},
    )
    return catalog_path


def _bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, dict[str, object]]:
    catalog = _catalog(tmp_path / "source")
    monkeypatch.setattr(
        artifact_module,
        "LOCKED_IDENTITIES",
        artifact_module._catalog_identities(catalog),
    )
    archive, manifest, lock = build_release_assets(
        catalog,
        tmp_path / "publication",
        repository="PhoenixSss/tracequant",
        tag=TAG,
    )
    lock_path = tmp_path / "artifact.lock.json"
    write_json(lock_path, lock)
    return archive, manifest, lock


def _malicious_archive(path: Path, case: str) -> None:
    with path.open("wb") as compressed:
        with zstandard.ZstdCompressor().stream_writer(
            compressed, closefd=False
        ) as writer:
            with tarfile.open(fileobj=writer, mode="w|") as bundle:
                if case == "duplicate":
                    for _ in range(2):
                        duplicate = tarfile.TarInfo(STAGE2_DATASET_ID)
                        duplicate.type = tarfile.DIRTYPE
                        bundle.addfile(duplicate)
                    return
                names = {
                    "absolute": "/absolute",
                    "symlink": f"{STAGE2_DATASET_ID}/escape",
                    "traversal": "../escape",
                    "unknown": f"{STAGE2_DATASET_ID}/unknown",
                }
                info = tarfile.TarInfo(names[case])
                if case == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = "../../outside"
                    bundle.addfile(info)
                else:
                    info.size = 1
                    bundle.addfile(info, fileobj=io.BytesIO(b"x"))


def test_stage2_artifact_materializes_only_the_locked_accepted_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive, manifest, lock_payload = _bundle(tmp_path, monkeypatch)
    lock_path = tmp_path / "artifact.lock.json"
    lock = load_artifact_lock(lock_path)

    assert lock_payload["schema"] == ARTIFACT_LOCK_SCHEMA
    release = cast(dict[str, object], lock_payload["release"])
    archive_asset = cast(dict[str, object], release["archive_asset"])
    assert archive_asset["format"] == ARCHIVE_FORMAT
    assert archive_asset["sha256"] == sha256_file(archive)
    manifest_asset = cast(dict[str, object], release["file_manifest_asset"])
    assert manifest_asset["sha256"] == sha256_file(manifest)

    staging = tmp_path / "staging"
    staging.mkdir()
    first = tmp_path / "first" / STAGE2_DATASET_ID
    first.parent.mkdir()
    assert materialize_catalog(lock_path, staging, first, archive_path=archive) == first
    verify_catalog(lock, first)

    second = tmp_path / "second" / STAGE2_DATASET_ID
    second.parent.mkdir()
    materialize_catalog(lock_path, staging, second, archive_path=archive)
    verify_catalog(lock, second)

    (second / lock.files[0].path).unlink()
    with pytest.raises(Stage2ArtifactError, match="inventory"):
        verify_catalog(lock, second)

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "keep").write_text("do not replace", encoding="utf-8")
    with pytest.raises(Stage2ArtifactError, match="empty"):
        materialize_catalog(lock_path, staging, nonempty, archive_path=archive)
    assert (nonempty / "keep").read_text(encoding="utf-8") == "do not replace"

    corrupted = tmp_path / "corrupted.tar.zst"
    corrupted.write_bytes(archive.read_bytes() + b"corrupt")
    with pytest.raises(Stage2ArtifactError, match="size"):
        materialize_catalog(
            lock_path,
            staging,
            tmp_path / "corrupt-target",
            archive_path=corrupted,
        )
    assert not (tmp_path / "corrupt-target").exists()

    locked_file = first / lock.files[0].path
    locked_file.write_bytes(locked_file.read_bytes() + b"drift")
    with pytest.raises(Stage2ArtifactError, match="size"):
        verify_catalog(lock, first)


@pytest.mark.parametrize(
    "case", ["absolute", "duplicate", "symlink", "traversal", "unknown"]
)
def test_stage2_artifact_rejects_unsafe_members_and_lock_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    archive, _manifest, lock_payload = _bundle(tmp_path, monkeypatch)
    release = cast(dict[str, object], lock_payload["release"])
    archive_asset = cast(dict[str, object], release["archive_asset"])
    malicious = tmp_path / f"malicious-{case}.tar.zst"
    _malicious_archive(malicious, case)
    archive_asset["sha256"] = sha256_file(malicious)
    archive_asset["size"] = malicious.stat().st_size
    lock_path = tmp_path / "malicious.lock.json"
    write_json(lock_path, lock_payload)
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(Stage2ArtifactError, match="unsafe|duplicate|unknown"):
        materialize_catalog(
            lock_path,
            staging,
            tmp_path / "target",
            archive_path=malicious,
        )

    malformed = json.loads(lock_path.read_text(encoding="utf-8"))
    malformed["unknown"] = True
    write_json(lock_path, malformed)
    with pytest.raises(Stage2ArtifactError, match="schema"):
        load_artifact_lock(lock_path)

    identity_drift = dict(lock_payload)
    identity_drift["identities"] = dict(
        cast(dict[str, object], lock_payload["identities"])
    )
    cast(dict[str, object], identity_drift["identities"])["dataset_digest"] = "f" * 64
    write_json(lock_path, identity_drift)
    with pytest.raises(Stage2ArtifactError, match="accepted r1 identity"):
        load_artifact_lock(lock_path)

    assert archive.is_file()


def test_tracked_publication_record_binds_release_and_artifact_lock() -> None:
    publication = cast(
        dict[str, object], json.loads(PUBLICATION_RECORD.read_text(encoding="utf-8"))
    )
    lock_payload = cast(
        dict[str, object], json.loads(TRACKED_LOCK.read_text(encoding="utf-8"))
    )
    artifact_lock = cast(dict[str, object], publication["artifact_lock"])
    release = cast(dict[str, object], publication["release"])
    locked_release = cast(dict[str, object], lock_payload["release"])

    assert artifact_lock["sha256"] == sha256_file(TRACKED_LOCK)
    assert publication["identities"] == lock_payload["identities"]
    assert release["tag"] == locked_release["tag"] == TAG
    assert release["archive"] == {
        key: value
        for key, value in cast(
            dict[str, object], locked_release["archive_asset"]
        ).items()
        if key != "url"
    }
    assert release["file_manifest"] == {
        key: value
        for key, value in cast(
            dict[str, object], locked_release["file_manifest_asset"]
        ).items()
        if key != "url"
    }
    serialized = json.dumps(publication, sort_keys=True)
    assert "/home/" not in serialized
    assert "/tmp/" not in serialized
    validation = cast(dict[str, object], publication["validation"])
    assert validation["release_restore_count"] == 2
    assert validation["staging_deleted_between_restores"] is True
