import hashlib
import io
import zipfile
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from tracequant.data import (
    ArchiveHttpResponse,
    BinanceArchiveObjectPlan,
    BinanceIndexPriceKlineBackfill,
    BinanceIndexPriceKlineCoverageGapPlan,
    BinanceIndexPriceKlineStatus,
    BinancePriceIndexId,
    BinancePublicHistoryDataType,
    RawArtifactConflictError,
    RawObjectIdentity,
    RawStore,
    plan_binance_index_price_kline_archives,
)
from tracequant.domain import InstrumentId, TimeRange

HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore\n"
)
ONE_MINUTE_MS = 60_000


class FixtureHttp:
    def __init__(self, responses: dict[str, ArchiveHttpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout: float) -> ArchiveHttpResponse:
        self.calls.append((url, timeout))
        return self.responses.get(url, ArchiveHttpResponse(404, b"missing", {}))


def _range(start: str, end: str) -> TimeRange:
    return TimeRange(
        start=datetime.fromisoformat(start).replace(tzinfo=UTC),
        end=datetime.fromisoformat(end).replace(tzinfo=UTC),
    )


def _plans(
    pair: BinancePriceIndexId, request_range: TimeRange
) -> tuple[BinanceArchiveObjectPlan, ...]:
    plans = plan_binance_index_price_kline_archives(pair, request_range)
    assert all(isinstance(plan, BinanceArchiveObjectPlan) for plan in plans)
    return tuple(plan for plan in plans if isinstance(plan, BinanceArchiveObjectPlan))


def _row(
    open_time: int,
    *,
    index_close: str = "61010.00000000",
    placeholder_count: str = "60",
) -> str:
    return (
        f"{open_time},61000.10000000,61020.20000000,60990.30000000,"
        f"{index_close},0.00000000,{open_time + 59_999},0,"
        f"{placeholder_count},0.000,0.00,0\n"
    )


def _zip(member: str, payload: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, payload)
    return output.getvalue()


def _archive(
    plan: BinanceArchiveObjectPlan,
    rows: list[str],
    *,
    header: str | None = HEADER,
    member: str | None = None,
) -> bytes:
    payload = ((header or "") + "".join(rows)).encode()
    return _zip(member or plan.member_name, payload)


def _complete_archive(
    plan: BinanceArchiveObjectPlan,
    *,
    first_close: str = "61010.00000000",
    first_count: str = "60",
    header: str | None = HEADER,
) -> bytes:
    boundary = plan.request.archive_object_boundary
    assert boundary is not None
    start = datetime.combine(boundary.period_start, datetime.min.time(), tzinfo=UTC)
    if boundary.granularity.value == "month":
        if boundary.period_start.month == 12:
            next_date = date(boundary.period_start.year + 1, 1, 1)
        else:
            next_date = date(
                boundary.period_start.year, boundary.period_start.month + 1, 1
            )
        end = datetime.combine(next_date, datetime.min.time(), tzinfo=UTC)
    else:
        end = start + timedelta(days=1)
    start_ms = int(start.timestamp() * 1000)
    row_count = int((end - start).total_seconds() // 60)
    rows = [_row(start_ms + offset * ONE_MINUTE_MS) for offset in range(row_count)]
    rows[0] = _row(
        start_ms,
        index_close=first_close,
        placeholder_count=first_count,
    )
    return _archive(plan, rows, header=header)


def _responses(
    plan: BinanceArchiveObjectPlan, archive: bytes
) -> dict[str, ArchiveHttpResponse]:
    checksum = hashlib.sha256(archive).hexdigest()
    return {
        plan.url: ArchiveHttpResponse(
            200, archive, {"Content-Type": "application/zip"}
        ),
        plan.checksum_url: ArchiveHttpResponse(
            200,
            f"{checksum}  {plan.url.rsplit('/', 1)[-1]}\n".encode(),
            {"Content-Type": "text/plain"},
        ),
    }


def test_index_backfill_publishes_verified_raw_from_supported_entry(
    tmp_path: Path,
) -> None:
    scenarios = (
        ("BTCUSDT", _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")),
        ("BTCUSDT", _range("2024-02-01T00:00:00", "2024-03-01T00:00:00")),
        ("ETHUSDT", _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")),
        ("ETHUSDT", _range("2024-02-01T00:00:00", "2024-03-01T00:00:00")),
    )
    store = RawStore(tmp_path)

    for index, (value, request_range) in enumerate(scenarios):
        pair = BinancePriceIndexId(value)
        plan = _plans(pair, request_range)[0]
        archive = _complete_archive(
            plan,
            first_close=f"61010.1234000{index}",
            first_count="060",
            header=None if index % 2 else HEADER,
        )
        responses = _responses(plan, archive)
        transport = FixtureHttp(responses)

        result = BinanceIndexPriceKlineBackfill(
            store, http_get=transport, timeout=2.5
        ).run(pair, request_range)

        assert result.request_range == request_range
        assert result.completed is True
        assert result.objects[0].status is BinanceIndexPriceKlineStatus.PUBLISHED
        artifact = store.read_request(plan.request)
        assert artifact.manifest.object_identity.source.price_index_pair == pair
        assert artifact.manifest.object_identity.source.data_type is (
            BinancePublicHistoryDataType.INDEX_PRICE_KLINE
        )
        assert artifact.manifest.raw_schema_identifier == (
            "binance.um.index-price-kline.csv.v1"
        )
        assert artifact.frame.columns == [
            "open_time",
            "index_open",
            "index_high",
            "index_low",
            "index_close",
            "placeholder_volume",
            "close_time",
            "placeholder_quote_volume",
            "placeholder_count",
            "placeholder_taker_buy_volume",
            "placeholder_taker_buy_quote_volume",
            "placeholder_ignore",
        ]
        assert artifact.frame.item(0, "index_close") == f"61010.1234000{index}"
        assert artifact.frame.item(0, "placeholder_count") == "060"
        assert "volume" not in artifact.frame.columns
        assert "count" not in artifact.frame.columns
        assert artifact.archive_path.read_bytes() == archive
        assert artifact.checksum_response_path.read_bytes() == (
            responses[plan.checksum_url].body
        )
        assert artifact.manifest.provenance is not None
        assert artifact.manifest.provenance.validation_evidence == (
            "checksum_response_verified",
            "archive_sha256_matches_checksum",
            "zip_member_structure_verified",
            "index_price_csv_schema_and_rows_verified",
            "index_price_placeholders_preserved",
            "source_object_coverage_verified",
        )
        assert [url for url, _ in transport.calls] == [plan.checksum_url, plan.url]
        assert all(timeout == 2.5 for _, timeout in transport.calls)


def test_planner_preserves_pair_identity_and_uses_monthly_between_daily_edges() -> None:
    pair = BinancePriceIndexId("ethusdt")
    plans = _plans(
        pair,
        _range("2024-01-31T23:30:00", "2024-03-01T00:30:00"),
    )

    assert [plan.request.source_kind.value for plan in plans] == [
        "archive_daily",
        "archive_monthly",
        "archive_daily",
    ]
    assert all(plan.request.price_index_pair == pair for plan in plans)
    assert all(
        plan.request.request_range == plans[0].request.request_range for plan in plans
    )
    assert plans[1].object_key == (
        "data/futures/um/monthly/indexPriceKlines/ETHUSDT/1m/ETHUSDT-1m-2024-02.zip"
    )
    assert plans[1].member_name == "ETHUSDT-1m-2024-02.csv"


def test_planner_marks_frozen_coverage_bounds_without_speculative_io(
    tmp_path: Path,
) -> None:
    pair = BinancePriceIndexId("BTCUSDT")
    plans = plan_binance_index_price_kline_archives(
        pair,
        _range("2026-08-29T00:00:00", "2026-08-31T00:00:00"),
    )
    assert isinstance(plans[0], BinanceArchiveObjectPlan)
    assert isinstance(plans[1], BinanceIndexPriceKlineCoverageGapPlan)

    transport = FixtureHttp({})
    result = BinanceIndexPriceKlineBackfill(RawStore(tmp_path), http_get=transport).run(
        pair,
        _range("2026-08-30T00:00:00", "2026-08-31T00:00:00"),
    )
    assert result.completed is False
    assert result.objects[0].status is BinanceIndexPriceKlineStatus.COVERAGE_GAP
    assert transport.calls == []


@pytest.mark.parametrize(
    "invalid_pair",
    [InstrumentId("BTCUSDT"), BinancePriceIndexId("BTCUSDC")],
)
def test_invalid_or_unsupported_subject_is_rejected_before_io(
    tmp_path: Path, invalid_pair: object
) -> None:
    transport = FixtureHttp({})
    backfill = BinanceIndexPriceKlineBackfill(RawStore(tmp_path), http_get=transport)

    with pytest.raises((TypeError, ValueError)):
        backfill.run(
            cast(BinancePriceIndexId, invalid_pair),
            _range("2024-02-29T00:00:00", "2024-03-01T00:00:00"),
        )

    assert transport.calls == []


@pytest.mark.parametrize(
    ("build_archive", "expected_status"),
    [
        (lambda plan: b"not a ZIP", BinanceIndexPriceKlineStatus.INVALID_CONTENT),
        (
            lambda plan: _archive(plan, [_row(1_709_164_800_000)], member="wrong.csv"),
            BinanceIndexPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(
                plan, [_row(1_709_164_800_000)], header="bad,header\n"
            ),
            BinanceIndexPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(plan, ["1,2,3\n"]),
            BinanceIndexPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(
                plan,
                [_row(1_709_164_800_000), _row(1_709_164_920_000)],
            ),
            BinanceIndexPriceKlineStatus.COVERAGE_GAP,
        ),
        (
            lambda plan: _archive(
                plan,
                [_row(1_709_164_800_000), _row(1_709_164_800_000)],
            ),
            BinanceIndexPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(
                plan,
                [_row(1_709_164_860_000), _row(1_709_164_800_000)],
            ),
            BinanceIndexPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(plan, [_row(1_709_164_800_001)]),
            BinanceIndexPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(
                plan,
                [_row(1_709_164_800_000).replace("61020.20000000", "NaN")],
            ),
            BinanceIndexPriceKlineStatus.INVALID_CONTENT,
        ),
    ],
)
def test_invalid_schema_values_and_time_coverage_fail_closed(
    tmp_path: Path,
    build_archive: Callable[[BinanceArchiveObjectPlan], bytes],
    expected_status: BinanceIndexPriceKlineStatus,
) -> None:
    pair = BinancePriceIndexId("ETHUSDT")
    request_range = _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")
    plan = _plans(pair, request_range)[0]
    archive = build_archive(plan)
    store = RawStore(tmp_path)

    result = BinanceIndexPriceKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan, archive))
    ).run(pair, request_range)

    assert result.completed is False
    assert result.objects[0].status is expected_status
    assert not store.path_for(RawObjectIdentity.from_request(plan.request)).exists()


def test_partial_first_daily_object_is_a_coverage_gap(tmp_path: Path) -> None:
    pair = BinancePriceIndexId("BTCUSDT")
    request_range = _range("2019-12-23T11:58:00", "2019-12-24T00:00:00")
    plan = _plans(pair, request_range)[0]
    rows = [_row(1_577_102_280_000 + offset * ONE_MINUTE_MS) for offset in range(722)]
    archive = _archive(plan, rows)

    result = BinanceIndexPriceKlineBackfill(
        RawStore(tmp_path), http_get=FixtureHttp(_responses(plan, archive))
    ).run(pair, request_range)

    assert result.completed is False
    assert result.objects[0].status is BinanceIndexPriceKlineStatus.COVERAGE_GAP
    assert result.objects[0].detail == (
        "archive rows do not cover the complete source object boundary"
    )


@pytest.mark.parametrize(
    ("mutate", "expected_status"),
    [
        (
            lambda plan, responses: responses.pop(plan.checksum_url),
            BinanceIndexPriceKlineStatus.CHECKSUM_NOT_FOUND,
        ),
        (
            lambda plan, responses: responses.pop(plan.url),
            BinanceIndexPriceKlineStatus.NOT_FOUND,
        ),
        (
            lambda plan, responses: responses.__setitem__(
                plan.checksum_url,
                ArchiveHttpResponse(
                    200,
                    f"{'0' * 64}  {plan.url.rsplit('/', 1)[-1]}\n".encode(),
                    {},
                ),
            ),
            BinanceIndexPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan, responses: responses.__setitem__(
                plan.checksum_url, ArchiveHttpResponse(503, b"retry", {})
            ),
            BinanceIndexPriceKlineStatus.RETRYABLE_FAILURE,
        ),
    ],
)
def test_acquisition_failures_keep_distinct_status_and_evidence(
    tmp_path: Path,
    mutate: Callable[
        [BinanceArchiveObjectPlan, dict[str, ArchiveHttpResponse]], object
    ],
    expected_status: BinanceIndexPriceKlineStatus,
) -> None:
    pair = BinancePriceIndexId("BTCUSDT")
    request_range = _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")
    plan = _plans(pair, request_range)[0]
    archive = _complete_archive(plan)
    responses = _responses(plan, archive)
    mutate(plan, responses)
    store = RawStore(tmp_path)

    result = BinanceIndexPriceKlineBackfill(store, http_get=FixtureHttp(responses)).run(
        pair, request_range
    )

    assert result.completed is False
    assert result.objects[0].status is expected_status
    identity = RawObjectIdentity.from_request(plan.request)
    assert not store.path_for(identity).exists()
    assert store.list_acquisition_manifests(identity)[0].status == expected_status.value


def test_network_failure_is_retryable_and_preserves_failure_evidence(
    tmp_path: Path,
) -> None:
    pair = BinancePriceIndexId("BTCUSDT")
    request_range = _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")
    plan = _plans(pair, request_range)[0]
    store = RawStore(tmp_path)

    def timeout(url: str, timeout: float) -> ArchiveHttpResponse:
        del url, timeout
        raise TimeoutError("upstream timeout")

    result = BinanceIndexPriceKlineBackfill(store, http_get=timeout).run(
        pair, request_range
    )

    assert result.completed is False
    assert result.objects[0].status is BinanceIndexPriceKlineStatus.RETRYABLE_FAILURE
    identity = RawObjectIdentity.from_request(plan.request)
    assert store.list_acquisition_manifests(identity)[0].detail == "upstream timeout"


def test_cross_object_failure_preserves_success_but_not_batch_completion(
    tmp_path: Path,
) -> None:
    pair = BinancePriceIndexId("BTCUSDT")
    request_range = _range("2024-02-29T23:59:00", "2024-03-01T00:01:00")
    plans = _plans(pair, request_range)
    first_archive = _complete_archive(plans[0])
    store = RawStore(tmp_path)

    result = BinanceIndexPriceKlineBackfill(
        store, http_get=FixtureHttp(_responses(plans[0], first_archive))
    ).run(pair, request_range)

    assert result.completed is False
    assert [item.status for item in result.objects] == [
        BinanceIndexPriceKlineStatus.PUBLISHED,
        BinanceIndexPriceKlineStatus.CHECKSUM_NOT_FOUND,
    ]
    assert store.read_request(plans[0].request).frame.height == 24 * 60


def test_repeat_and_upstream_replacement_keep_exact_immutable_revisions(
    tmp_path: Path,
) -> None:
    pair = BinancePriceIndexId("ETHUSDT")
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:01:00")
    plan = _plans(pair, request_range)[0]
    original = _complete_archive(plan)
    replacement = _complete_archive(plan, first_close="62000.00000000")
    store = RawStore(tmp_path)

    first = BinanceIndexPriceKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan, original))
    ).run(pair, request_range)
    repeated = BinanceIndexPriceKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan, original))
    ).run(pair, request_range)
    revised = BinanceIndexPriceKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan, replacement))
    ).run(pair, request_range)

    assert first.objects[0].status is BinanceIndexPriceKlineStatus.PUBLISHED
    assert repeated.objects[0].status is BinanceIndexPriceKlineStatus.EXISTING
    assert revised.objects[0].status is BinanceIndexPriceKlineStatus.PUBLISHED
    revisions = store.list_verified_revisions(
        RawObjectIdentity.from_request(plan.request)
    )
    assert len(revisions) == 2
    assert {item.frame.item(0, "index_close") for item in revisions} == {
        "61010.00000000",
        "62000.00000000",
    }
    for item in revisions:
        revision = item.revision_identity
        assert revision is not None
        assert (
            store.read_revision(item.manifest.object_identity, revision).path
            == item.path
        )


def test_local_corruption_and_write_conflict_fail_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = BinancePriceIndexId("BTCUSDT")
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:01:00")
    plan = _plans(pair, request_range)[0]
    archive = _complete_archive(plan)
    store = RawStore(tmp_path)
    incomplete_path = store.path_for(RawObjectIdentity.from_request(plan.request))
    incomplete_path.mkdir(parents=True)
    sentinel = incomplete_path / "interrupted-write"
    sentinel.write_text("preserve", encoding="utf-8")

    damaged = BinanceIndexPriceKlineBackfill(store, http_get=FixtureHttp({})).run(
        pair, request_range
    )
    assert damaged.objects[0].status is BinanceIndexPriceKlineStatus.LOCAL_FAILURE
    assert sentinel.read_text(encoding="utf-8") == "preserve"

    clean_store = RawStore(tmp_path / "clean")

    def conflict(self: RawStore, source: object) -> None:
        del self, source
        raise RawArtifactConflictError("same verified revision differs locally")

    monkeypatch.setattr(RawStore, "write", conflict)
    conflicted = BinanceIndexPriceKlineBackfill(
        clean_store, http_get=FixtureHttp(_responses(plan, archive))
    ).run(pair, request_range)
    assert conflicted.objects[0].status is BinanceIndexPriceKlineStatus.CONFLICT


def test_existing_corrupt_revision_is_local_failure_without_network(
    tmp_path: Path,
) -> None:
    pair = BinancePriceIndexId("BTCUSDT")
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:01:00")
    plan = _plans(pair, request_range)[0]
    archive = _complete_archive(plan)
    store = RawStore(tmp_path)
    first = BinanceIndexPriceKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan, archive))
    ).run(pair, request_range)
    assert first.completed is True
    artifact = store.read_request(plan.request)
    artifact.data_path.write_bytes(artifact.data_path.read_bytes() + b"corrupt")
    transport = FixtureHttp({})

    second = BinanceIndexPriceKlineBackfill(store, http_get=transport).run(
        pair, request_range
    )

    assert second.completed is False
    assert second.objects[0].status is BinanceIndexPriceKlineStatus.LOCAL_FAILURE
    assert transport.calls == []


def test_disk_write_failure_is_local_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = BinancePriceIndexId("BTCUSDT")
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:01:00")
    plan = _plans(pair, request_range)[0]
    archive = _complete_archive(plan)
    store = RawStore(tmp_path)

    def disk_failure(self: RawStore, source: object) -> None:
        del self, source
        raise OSError("disk full")

    monkeypatch.setattr(RawStore, "write", disk_failure)
    result = BinanceIndexPriceKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan, archive))
    ).run(pair, request_range)

    assert result.completed is False
    assert result.objects[0].status is BinanceIndexPriceKlineStatus.LOCAL_FAILURE
    assert result.objects[0].detail == "disk full"
