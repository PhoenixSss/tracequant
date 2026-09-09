import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from tracequant.data import (
    ArchiveHttpResponse,
    BinanceArchiveObjectPlan,
    BinanceFundingRateBackfill,
    BinanceFundingRateCoverageGapPlan,
    BinanceFundingRateCoverageStatus,
    BinanceFundingRateStatus,
    BinancePriceIndexId,
    BinancePublicHistoryDataType,
    RawArtifactNotFoundError,
    RawObjectIdentity,
    RawStore,
    plan_binance_funding_rate_archives,
)
from tracequant.domain import InstrumentId, TimeRange

HEADER = "calc_time,funding_interval_hours,last_funding_rate\n"
EIGHT_HOURS_MS = 8 * 60 * 60 * 1000


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


def _plan(
    instrument: InstrumentId, request_range: TimeRange
) -> BinanceArchiveObjectPlan:
    plans = plan_binance_funding_rate_archives(instrument, request_range)
    assert len(plans) == 1
    assert isinstance(plans[0], BinanceArchiveObjectPlan)
    return plans[0]


def _zip(member_name: str, payload: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, payload.encode())
    return output.getvalue()


def _archive(plan: BinanceArchiveObjectPlan, rows: list[str]) -> bytes:
    return _zip(plan.member_name, HEADER + "".join(rows))


def _monthly_rows(
    start: datetime,
    end: datetime,
    *,
    interval_hours: int = 8,
) -> list[str]:
    rows: list[str] = []
    cursor = start
    rates = ("-0.00010000", "0", "0.00020000")
    while cursor < end:
        rows.append(
            f"{int(cursor.timestamp() * 1000)},{interval_hours},"
            f"{rates[len(rows) % len(rates)]}\n"
        )
        cursor += timedelta(hours=interval_hours)
    return rows


def _july_rows() -> list[str]:
    return _monthly_rows(
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 8, 1, tzinfo=UTC),
    )


def _responses(
    plan: BinanceArchiveObjectPlan, archive: bytes
) -> dict[str, ArchiveHttpResponse]:
    digest = hashlib.sha256(archive).hexdigest()
    return {
        plan.url: ArchiveHttpResponse(
            200,
            archive,
            {"Content-Type": "application/zip", "ETag": "not-an-identity"},
        ),
        plan.checksum_url: ArchiveHttpResponse(
            200,
            f"{digest}  {plan.url.rsplit('/', 1)[-1]}\n".encode(),
            {"Content-Type": "text/plain"},
        ),
    }


def test_funding_planner_uses_each_intersecting_full_month_once() -> None:
    request_range = _range("2024-01-31T23:59:59", "2024-03-01T00:00:01")
    plans = plan_binance_funding_rate_archives(InstrumentId("BTCUSDT"), request_range)

    assert len(plans) == 3
    assert all(isinstance(plan, BinanceArchiveObjectPlan) for plan in plans)
    object_plans = [
        plan for plan in plans if isinstance(plan, BinanceArchiveObjectPlan)
    ]
    assert [
        plan.request.archive_object_boundary.period_start.isoformat()
        for plan in object_plans
        if plan.request.archive_object_boundary is not None
    ] == ["2024-01-01", "2024-02-01", "2024-03-01"]
    assert all(
        plan.request.data_type is BinancePublicHistoryDataType.SETTLED_FUNDING_RATE
        for plan in object_plans
    )
    assert all(plan.request.interval is None for plan in object_plans)
    assert object_plans[1].object_key == (
        "data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-02.zip"
    )

    tiny = plan_binance_funding_rate_archives(
        InstrumentId("BTCUSDT"),
        TimeRange(
            start=datetime(2024, 2, 1, tzinfo=UTC),
            end=datetime(2024, 2, 1, tzinfo=UTC) + timedelta(microseconds=1),
        ),
    )
    assert len(tiny) == 1
    assert isinstance(tiny[0], BinanceArchiveObjectPlan)


def test_unknown_month_is_gap_without_speculative_download(tmp_path: Path) -> None:
    transport = FixtureHttp({})
    store = RawStore(tmp_path)

    result = BinanceFundingRateBackfill(store, http_get=transport).run(
        InstrumentId("ETHUSDT"),
        _range("2026-07-31T23:00:00", "2026-08-01T01:00:00"),
    )

    assert result.completed is False
    assert [item.status for item in result.objects] == [
        BinanceFundingRateStatus.CHECKSUM_NOT_FOUND,
        BinanceFundingRateStatus.COVERAGE_GAP,
    ]
    assert isinstance(result.objects[1].plan, BinanceFundingRateCoverageGapPlan)
    assert result.objects[1].coverage_status is BinanceFundingRateCoverageStatus.GAP
    assert result.objects[1].actual_record_range is None
    first_plan = result.objects[0].plan
    assert isinstance(first_plan, BinanceArchiveObjectPlan)
    assert [url for url, _ in transport.calls] == [first_plan.checksum_url]
    gap_identity = RawObjectIdentity.from_request(result.objects[1].plan.request)
    assert store.list_acquisition_manifests(gap_identity)[0].status == "coverage_gap"


@pytest.mark.parametrize("symbol", ["BTCUSDC", "ETHUSDC", "BNBUSDT"])
def test_unsupported_instrument_is_rejected_before_fetch(
    tmp_path: Path, symbol: str
) -> None:
    transport = FixtureHttp({})
    backfill = BinanceFundingRateBackfill(RawStore(tmp_path), http_get=transport)

    with pytest.raises(
        ValueError,
        match=rf"^instrument {symbol} has no frozen funding-rate archive coverage$",
    ):
        backfill.run(
            InstrumentId(symbol),
            _range("2026-07-01T00:00:00", "2026-08-01T00:00:00"),
        )

    assert transport.calls == []


def test_price_index_pair_is_rejected_before_fetch(tmp_path: Path) -> None:
    transport = FixtureHttp({})
    backfill = BinanceFundingRateBackfill(RawStore(tmp_path), http_get=transport)

    with pytest.raises(TypeError, match="^instrument must be an InstrumentId$"):
        backfill.run(
            cast(InstrumentId, BinancePriceIndexId("BTCUSDT")),
            _range("2026-07-01T00:00:00", "2026-08-01T00:00:00"),
        )

    assert transport.calls == []


def test_funding_backfill_publishes_verified_raw_from_supported_entry(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "BTCUSDT",
            _range("2026-07-01T00:00:00", "2026-08-01T00:00:00"),
        ),
        (
            "ETHUSDT",
            _range("2026-07-15T12:00:00", "2026-07-15T12:00:01"),
        ),
    )
    rows = _july_rows()
    assert len(rows) == 93

    for symbol, request_range in cases:
        instrument = InstrumentId(symbol)
        plan = _plan(instrument, request_range)
        archive = _archive(plan, rows)
        responses = _responses(plan, archive)
        transport = FixtureHttp(responses)
        store = RawStore(tmp_path / symbol)

        result = BinanceFundingRateBackfill(
            store,
            http_get=transport,
            timeout=2.5,
            clock=lambda: datetime(2026, 8, 2, tzinfo=UTC),
        ).run(instrument, request_range)

        assert result.request_range == request_range
        assert result.completed is True
        assert result.objects[0].status is BinanceFundingRateStatus.PUBLISHED
        assert (
            result.objects[0].coverage_status
            is BinanceFundingRateCoverageStatus.COMPLETE
        )
        assert result.objects[0].request_range == request_range
        assert result.objects[0].actual_record_range == _range(
            "2026-07-01T00:00:00", "2026-07-31T16:00:00.001000"
        )
        artifact = store.read_request(plan.request)
        assert artifact.frame.columns == [
            "calc_time",
            "funding_interval_hours",
            "last_funding_rate",
        ]
        assert artifact.frame.height == 93
        assert artifact.frame["calc_time"].head(1).item() == 1_782_864_000_000
        assert artifact.frame["calc_time"].tail(1).item() == 1_785_513_600_000
        assert set(artifact.frame["funding_interval_hours"].to_list()) == {8}
        assert set(artifact.frame["last_funding_rate"].head(3).to_list()) == {
            "-0.00010000",
            "0",
            "0.00020000",
        }
        assert artifact.manifest.caller_request_range == request_range
        assert artifact.manifest.actual_record_range == _range(
            "2026-07-01T00:00:00", "2026-07-31T16:00:00.001000"
        )
        assert artifact.manifest.raw_schema_identifier == (
            "binance.um.funding-rate.csv.v1"
        )
        assert artifact.manifest.upstream_checksum == (
            f"sha256:{hashlib.sha256(archive).hexdigest()}"
        )
        assert artifact.manifest.provenance is not None
        assert artifact.manifest.provenance.object_key == plan.object_key
        assert artifact.manifest.provenance.csv_member == plan.member_name
        assert (
            artifact.manifest.provenance.archive_sha256
            == hashlib.sha256(archive).hexdigest()
        )
        assert artifact.manifest.provenance.validation_evidence == (
            "checksum_response_verified",
            "archive_sha256_matches_checksum",
            "zip_member_structure_verified",
            "funding_csv_schema_and_rows_verified",
            "funding_event_time_range_recorded",
            "funding_fixed_interval_object_coverage_verified",
            "source_object_coverage_verified",
        )
        assert artifact.archive_path.read_bytes() == archive
        assert (
            artifact.checksum_response_path.read_bytes()
            == responses[plan.checksum_url].body
        )
        revision = artifact.revision_identity
        assert revision is not None
        assert store.read_exact_revision(
            RawObjectIdentity.from_request(plan.request), revision
        ).frame.equals(artifact.frame)
        assert [url for url, _ in transport.calls] == [
            plan.checksum_url,
            plan.url,
        ]
        assert all(timeout == 2.5 for _, timeout in transport.calls)


@pytest.mark.parametrize(
    "payload",
    [
        "1782864000000,8,0.0001\n",
        "calc_time,funding_interval_hours,last_funding_rate,extra\n"
        "1782864000000,8,0.0001,x\n",
        "calc_time,last_funding_rate\n1782864000000,0.0001\n",
    ],
)
def test_unknown_missing_or_headerless_schema_is_rejected(
    tmp_path: Path, payload: str
) -> None:
    request_range = _range("2026-07-01T00:00:00", "2026-08-01T00:00:00")
    plan = _plan(InstrumentId("BTCUSDT"), request_range)
    archive = _zip(plan.member_name, payload)

    result = BinanceFundingRateBackfill(
        RawStore(tmp_path), http_get=FixtureHttp(_responses(plan, archive))
    ).run(InstrumentId("BTCUSDT"), request_range)

    assert result.objects[0].status is BinanceFundingRateStatus.INVALID_CONTENT
    assert result.objects[0].detail == (
        "CSV header must exactly match the funding schema"
    )


@pytest.mark.parametrize(
    ("row_index", "replacement", "expected_status", "message"),
    [
        (0, "1782864000000,0,0.1\n", "invalid_content", "positive integer"),
        (0, "1782864000000,8,NaN\n", "invalid_content", "finite numeric"),
        (0, "1782864000000,8, 0.1\n", "invalid_content", "finite numeric"),
        (1, "1782864000000,8,0.1\n", "invalid_content", "strictly increasing"),
        (0, "1780185600000,8,0.1\n", "invalid_content", "outside"),
        (1, "1782907200000,8,0.1\n", "coverage_gap", "missing event"),
        (0, "1782864000000,4,0.1\n", "coverage_gap", "interval changes"),
    ],
)
def test_invalid_rows_and_unproven_coverage_fail_closed(
    tmp_path: Path,
    row_index: int,
    replacement: str,
    expected_status: str,
    message: str,
) -> None:
    request_range = _range("2026-07-01T00:00:00", "2026-08-01T00:00:00")
    plan = _plan(InstrumentId("BTCUSDT"), request_range)
    rows = _july_rows()
    rows[row_index] = replacement
    archive = _archive(plan, rows)
    store = RawStore(tmp_path)

    result = BinanceFundingRateBackfill(
        store, http_get=FixtureHttp(_responses(plan, archive))
    ).run(InstrumentId("BTCUSDT"), request_range)

    assert result.completed is False
    assert result.objects[0].status.value == expected_status
    assert message in (result.objects[0].detail or "")
    if expected_status == "coverage_gap":
        assert result.objects[0].actual_record_range is not None
    with pytest.raises(RawArtifactNotFoundError):
        store.read_request(plan.request)


@pytest.mark.parametrize("missing", ["first", "middle", "last"])
def test_missing_edge_or_middle_event_is_a_coverage_gap(
    tmp_path: Path, missing: str
) -> None:
    request_range = _range("2026-07-10T00:00:00", "2026-07-10T00:00:01")
    plan = _plan(InstrumentId("ETHUSDT"), request_range)
    rows = _july_rows()
    del rows[{"first": 0, "middle": 40, "last": 92}[missing]]
    archive = _archive(plan, rows)

    result = BinanceFundingRateBackfill(
        RawStore(tmp_path), http_get=FixtureHttp(_responses(plan, archive))
    ).run(InstrumentId("ETHUSDT"), request_range)

    assert result.objects[0].status is BinanceFundingRateStatus.COVERAGE_GAP
    assert result.completed is False


def test_idempotency_and_upstream_replacement_preserve_exact_revisions(
    tmp_path: Path,
) -> None:
    request_range = _range("2026-07-15T00:00:00", "2026-07-15T00:00:01")
    instrument = InstrumentId("BTCUSDT")
    plan = _plan(instrument, request_range)
    original = _archive(plan, _july_rows())
    revised_rows = _july_rows()
    revised_rows[1] = revised_rows[1].replace(",0\n", ",0.00000001\n")
    revised = _archive(plan, revised_rows)
    store = RawStore(tmp_path)

    first = BinanceFundingRateBackfill(
        store, http_get=FixtureHttp(_responses(plan, original))
    ).run(instrument, request_range)
    repeated = BinanceFundingRateBackfill(
        store, http_get=FixtureHttp(_responses(plan, original))
    ).run(instrument, request_range)
    replacement = BinanceFundingRateBackfill(
        store, http_get=FixtureHttp(_responses(plan, revised))
    ).run(instrument, request_range)

    assert first.objects[0].status is BinanceFundingRateStatus.PUBLISHED
    assert repeated.objects[0].status is BinanceFundingRateStatus.EXISTING
    assert replacement.objects[0].status is BinanceFundingRateStatus.PUBLISHED
    identity = RawObjectIdentity.from_request(plan.request)
    revisions = store.list_verified_revisions(identity)
    assert len(revisions) == 2
    assert {
        item.frame["last_funding_rate"].head(2).to_list()[1] for item in revisions
    } == {"0", "0.00000001"}


def test_cross_month_partial_failure_does_not_hide_published_month(
    tmp_path: Path,
) -> None:
    request_range = _range("2026-06-30T23:00:00", "2026-07-01T01:00:00")
    plans = plan_binance_funding_rate_archives(InstrumentId("BTCUSDT"), request_range)
    assert all(isinstance(plan, BinanceArchiveObjectPlan) for plan in plans)
    june, july = plans
    assert isinstance(june, BinanceArchiveObjectPlan)
    assert isinstance(july, BinanceArchiveObjectPlan)
    june_archive = _archive(
        june,
        _monthly_rows(
            datetime(2026, 6, 1, tzinfo=UTC),
            datetime(2026, 7, 1, tzinfo=UTC),
        ),
    )
    store = RawStore(tmp_path)

    result = BinanceFundingRateBackfill(
        store, http_get=FixtureHttp(_responses(june, june_archive))
    ).run(InstrumentId("BTCUSDT"), request_range)

    assert [item.status for item in result.objects] == [
        BinanceFundingRateStatus.PUBLISHED,
        BinanceFundingRateStatus.CHECKSUM_NOT_FOUND,
    ]
    assert result.completed is False
    assert store.read_request(june.request).frame.height == 90
    with pytest.raises(RawArtifactNotFoundError):
        store.read_request(july.request)
