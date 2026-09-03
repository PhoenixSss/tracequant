import json
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from tracequant.data import (
    BinanceArchiveObjectBoundary,
    BinanceKlineInterval,
    BinancePublicHistoryDataType,
    BinancePublicHistoryRequest,
    BinancePublicHistorySourceKind,
    RawArtifactConflictError,
    RawArtifactNotFoundError,
    RawArtifactValidationError,
    RawObjectIdentity,
    RawSourceObject,
    RawStore,
)
from tracequant.domain import InstrumentId, TimeRange

CREATED_AT = datetime(2024, 3, 2, 12, 30, tzinfo=UTC)


def _request(
    *,
    source_kind: BinancePublicHistorySourceKind = (
        BinancePublicHistorySourceKind.ARCHIVE_DAILY
    ),
    request_range: TimeRange | None = None,
) -> BinancePublicHistoryRequest:
    request_range = request_range or TimeRange(
        start=datetime(2024, 2, 29, tzinfo=UTC),
        end=datetime(2024, 3, 1, tzinfo=UTC),
    )
    return BinancePublicHistoryRequest(
        instrument=InstrumentId("BTCUSDT"),
        data_type=BinancePublicHistoryDataType.CONTRACT_KLINE,
        interval=BinanceKlineInterval.ONE_MINUTE,
        source_kind=source_kind,
        archive_object_boundary=(
            None
            if source_kind is BinancePublicHistorySourceKind.REST
            else BinanceArchiveObjectBoundary.day(date(2024, 2, 29))
        ),
        request_range=request_range,
    )


def _source_object(
    *,
    request: BinancePublicHistoryRequest | None = None,
    close: str = "61234.50",
    upstream_revision: str | None = "archive:2024-03-01",
) -> RawSourceObject:
    return RawSourceObject(
        request=request or _request(),
        rows=pl.DataFrame(
            {
                "open_time": pl.Series(
                    "open_time", [1709164800000, 1709164860000], dtype=pl.Int64
                ),
                "open": pl.Series("open", ["61000.00", "61100.00"], dtype=pl.String),
                "close": pl.Series("close", ["61100.00", close], dtype=pl.String),
                "trade_count": pl.Series("trade_count", [50, 61], dtype=pl.Int64),
            }
        ),
        actual_record_range=TimeRange(
            start=datetime(2024, 2, 29, tzinfo=UTC),
            end=datetime(2024, 2, 29, 0, 2, tzinfo=UTC),
        ),
        raw_schema_identifier="binance.um.contract-kline.csv.v1",
        producer_version="tracequant/0.1.0",
        upstream_checksum="sha256:upstream-example",
        upstream_revision=upstream_revision,
    )


def _store(root: Path) -> RawStore:
    return RawStore(root, clock=lambda: CREATED_AT)


def test_raw_store_publishes_only_complete_verified_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    source_object = _source_object()
    final_path = store.path_for(source_object.identity)

    def interrupt_publish(
        unused_store: RawStore, source: Path, destination: Path
    ) -> None:
        assert source.parent == destination.parent
        assert (source / "data.parquet").is_file()
        assert (source / "manifest.json").is_file()
        raise RuntimeError("simulated interruption before publish")

    monkeypatch.setattr(RawStore, "_publish_directory", interrupt_publish)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        store.write(source_object)

    assert not final_path.exists()
    with pytest.raises(RawArtifactNotFoundError):
        store.read(source_object.identity)

    monkeypatch.undo()
    artifact = store.write(source_object)

    assert artifact.path == final_path
    assert artifact.frame.equals(source_object.rows)
    assert artifact.manifest.completed is True
    assert artifact.manifest.record_count == 2
    assert artifact.manifest.parquet_file_size == artifact.data_path.stat().st_size
    assert artifact.manifest.created_at == CREATED_AT
    assert len(artifact.manifest.project_sha256) == 64
    assert store.read(source_object.identity).frame.equals(source_object.rows)


def test_manifest_contains_complete_source_and_provenance_evidence(
    tmp_path: Path,
) -> None:
    artifact = _store(tmp_path).write(_source_object())
    payload = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))

    assert payload["manifest_schema_version"] == 3
    assert payload["completed"] is True
    assert payload["object_identity"]["source"] == _request().source_identity.to_dict()
    assert payload["caller_request_range"] == _request().request_range.to_dict()
    assert payload["actual_record_range"] == {
        "start": "2024-02-29T00:00:00Z",
        "end": "2024-02-29T00:02:00Z",
    }
    assert payload["upstream_checksum"] == "sha256:upstream-example"
    assert payload["upstream_revision"] == "archive:2024-03-01"
    assert payload["raw_schema_identifier"] == "binance.um.contract-kline.csv.v1"
    assert payload["producer_version"] == "tracequant/0.1.0"
    assert payload["created_at"] == "2024-03-02T12:30:00Z"
    assert payload["provenance"] is None


def test_raw_parquet_preserves_parsed_source_columns_values_and_dtypes(
    tmp_path: Path,
) -> None:
    source_object = _source_object()
    artifact = _store(tmp_path).write(source_object)

    assert artifact.frame.schema == source_object.rows.schema
    assert artifact.frame.to_dict(as_series=False) == source_object.rows.to_dict(
        as_series=False
    )
    assert "instrument" not in artifact.frame.columns
    assert "start" not in artifact.frame.columns


def test_paths_are_deterministic_from_controlled_identity_fields(
    tmp_path: Path,
) -> None:
    local_time_request = _request(
        request_range=TimeRange(
            start=datetime(2024, 2, 29, 8, tzinfo=timezone(timedelta(hours=8))),
            end=datetime(2024, 3, 1, 8, tzinfo=timezone(timedelta(hours=8))),
        )
    )
    first = RawObjectIdentity.from_request(local_time_request)
    second = RawObjectIdentity.from_request(_request())

    assert first == second
    assert first.object_id == second.object_id
    relative = RawStore("ignored-a").relative_path(first)
    assert relative == RawStore("ignored-b").relative_path(second)
    assert relative.parts[:5] == ("raw", "v1", "binance", "um", "BTCUSDT")
    assert all(part not in {"", ".", ".."} for part in relative.parts)
    assert not relative.is_absolute()
    assert RawStore(tmp_path).path_for(first) == tmp_path / relative


def test_rest_request_range_is_a_stable_object_boundary() -> None:
    first_request = _request(source_kind=BinancePublicHistorySourceKind.REST)
    equivalent_request = _request(
        source_kind=BinancePublicHistorySourceKind.REST,
        request_range=TimeRange(
            start=datetime(2024, 2, 29, 8, tzinfo=timezone(timedelta(hours=8))),
            end=datetime(2024, 3, 1, 8, tzinfo=timezone(timedelta(hours=8))),
        ),
    )
    later_request = _request(
        source_kind=BinancePublicHistorySourceKind.REST,
        request_range=TimeRange(
            start=datetime(2024, 3, 1, tzinfo=UTC),
            end=datetime(2024, 3, 2, tzinfo=UTC),
        ),
    )

    first = RawObjectIdentity.from_request(first_request)
    assert first == RawObjectIdentity.from_request(equivalent_request)
    assert first.object_id != RawObjectIdentity.from_request(later_request).object_id


def test_same_content_is_idempotent_but_different_content_conflicts(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = store.write(_source_object())
    second = store.write(_source_object())

    assert second.path == first.path
    assert second.manifest == first.manifest
    with pytest.raises(RawArtifactConflictError, match="different content"):
        store.write(_source_object(close="99999.00"))
    assert store.read(_source_object().identity).frame.equals(first.frame)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("data", "size|checksum"),
        ("checksum", "checksum"),
        ("version", "unsupported manifest schema version"),
        ("completed", "not completed"),
    ],
)
def test_reader_rejects_corrupt_or_unsupported_artifact(
    tmp_path: Path, mutation: str, message: str
) -> None:
    store = _store(tmp_path)
    artifact = store.write(_source_object())
    if mutation == "data":
        artifact.data_path.write_bytes(artifact.data_path.read_bytes() + b"corrupt")
    else:
        payload = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
        if mutation == "checksum":
            payload["project_sha256"] = "0" * 64
        elif mutation == "version":
            payload["manifest_schema_version"] = 4
        else:
            payload["completed"] = False
        artifact.manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RawArtifactValidationError, match=message):
        store.read(_source_object().identity)


@pytest.mark.parametrize("missing_name", ["data.parquet", "manifest.json"])
def test_reader_rejects_missing_final_component(
    tmp_path: Path, missing_name: str
) -> None:
    store = _store(tmp_path)
    artifact = store.write(_source_object())
    (artifact.path / missing_name).unlink()

    with pytest.raises(RawArtifactNotFoundError, match="requires both"):
        store.read(_source_object().identity)


def test_import_and_construction_do_not_touch_filesystem(tmp_path: Path) -> None:
    root = tmp_path / "not-created"
    store = RawStore(root)

    assert not root.exists()
    store.relative_path(_source_object().identity)
    assert not root.exists()
