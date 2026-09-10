import hashlib
import io
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracequant.data import (
    ArchiveHttpResponse,
    BinanceArchiveObjectPlan,
    BinanceMarkPriceKlineBackfill,
    BinanceMarkPriceKlineStatus,
    BinancePublicHistoryDataType,
    RawObjectIdentity,
    RawStore,
    plan_binance_mark_price_kline_archives,
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


def _plan(
    instrument: InstrumentId, request_range: TimeRange
) -> BinanceArchiveObjectPlan:
    plans = plan_binance_mark_price_kline_archives(instrument, request_range)
    assert len(plans) == 1
    assert isinstance(plans[0], BinanceArchiveObjectPlan)
    return plans[0]


def _row(
    open_time: int,
    *,
    mark_close: str = "61010.00000000",
    placeholder_count: str = "60",
) -> str:
    return (
        f"{open_time},61000.10000000,61020.20000000,60990.30000000,"
        f"{mark_close},0.00000000,{open_time + 59_999},0,"
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
    header: bool = True,
    member: str | None = None,
) -> bytes:
    payload = ((HEADER if header else "") + "".join(rows)).encode()
    return _zip(member or plan.member_name, payload)


def _full_daily_archive(
    plan: BinanceArchiveObjectPlan,
    start_open_time: int,
    *,
    first_close: str = "61010.00000000",
    first_count: str = "60",
) -> bytes:
    rows = [_row(start_open_time + offset * ONE_MINUTE_MS) for offset in range(24 * 60)]
    rows[0] = _row(
        start_open_time,
        mark_close=first_close,
        placeholder_count=first_count,
    )
    return _archive(plan, rows)


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


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
def test_planner_uses_mark_price_identity_and_daily_monthly_archives(
    symbol: str,
) -> None:
    plans = plan_binance_mark_price_kline_archives(
        InstrumentId(symbol),
        _range("2024-01-31T23:30:00", "2024-03-01T00:30:00"),
    )

    assert all(isinstance(plan, BinanceArchiveObjectPlan) for plan in plans)
    object_plans = [
        plan for plan in plans if isinstance(plan, BinanceArchiveObjectPlan)
    ]
    assert [plan.request.source_kind.value for plan in object_plans] == [
        "archive_daily",
        "archive_monthly",
        "archive_daily",
    ]
    assert all(
        plan.request.data_type is BinancePublicHistoryDataType.MARK_PRICE_KLINE
        for plan in object_plans
    )
    assert object_plans[1].object_key == (
        f"data/futures/um/monthly/markPriceKlines/{symbol}/1m/{symbol}-1m-2024-02.zip"
    )


@pytest.mark.parametrize("symbol", ["BTCUSDC", "ETHUSDC"])
def test_planner_does_not_silently_support_usdc_instruments(symbol: str) -> None:
    with pytest.raises(
        ValueError,
        match=rf"^instrument {symbol} has no frozen mark-price-Kline archive coverage$",
    ):
        plan_binance_mark_price_kline_archives(
            InstrumentId(symbol),
            _range("2024-02-29T00:00:00", "2024-03-01T00:00:00"),
        )


def test_backfill_mark_price_klines_publishes_verified_raw_artifact(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:02:00")
    plan = _plan(InstrumentId("BTCUSDT"), request_range)
    archive = _full_daily_archive(
        plan,
        1_709_164_800_000,
        first_close="61010.12340000",
        first_count="060",
    )
    responses = _responses(plan, archive)
    transport = FixtureHttp(responses)
    store = RawStore(tmp_path)

    result = BinanceMarkPriceKlineBackfill(store, http_get=transport, timeout=2.5).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert result.completed is True
    assert result.objects[0].status is BinanceMarkPriceKlineStatus.PUBLISHED
    artifact = store.read_request(plan.request)
    assert artifact.manifest.raw_schema_identifier == (
        "binance.um.mark-price-kline.csv.v1"
    )
    assert artifact.frame.columns == [
        "open_time",
        "mark_open",
        "mark_high",
        "mark_low",
        "mark_close",
        "placeholder_volume",
        "close_time",
        "placeholder_quote_volume",
        "placeholder_count",
        "placeholder_taker_buy_volume",
        "placeholder_taker_buy_quote_volume",
        "placeholder_ignore",
    ]
    assert artifact.frame.row(0, named=True) == {
        "open_time": 1_709_164_800_000,
        "mark_open": "61000.10000000",
        "mark_high": "61020.20000000",
        "mark_low": "60990.30000000",
        "mark_close": "61010.12340000",
        "placeholder_volume": "0.00000000",
        "close_time": 1_709_164_859_999,
        "placeholder_quote_volume": "0",
        "placeholder_count": "060",
        "placeholder_taker_buy_volume": "0.000",
        "placeholder_taker_buy_quote_volume": "0.00",
        "placeholder_ignore": "0",
    }
    assert "volume" not in artifact.frame.columns
    assert "count" not in artifact.frame.columns
    assert artifact.manifest.upstream_checksum == (
        f"sha256:{hashlib.sha256(archive).hexdigest()}"
    )
    assert artifact.manifest.provenance is not None
    assert artifact.manifest.provenance.validation_evidence == (
        "checksum_response_verified",
        "archive_sha256_matches_checksum",
        "zip_member_structure_verified",
        "mark_price_csv_schema_and_rows_verified",
        "mark_price_placeholders_preserved",
        "source_object_coverage_verified",
    )
    assert artifact.archive_path.read_bytes() == archive
    assert (
        artifact.checksum_response_path.read_bytes()
        == responses[plan.checksum_url].body
    )
    assert [url for url, _ in transport.calls] == [plan.checksum_url, plan.url]
    assert all(timeout == 2.5 for _, timeout in transport.calls)


@pytest.mark.parametrize(
    ("mutate", "expected_status"),
    [
        (
            lambda plan, archive, responses: responses.pop(plan.checksum_url),
            BinanceMarkPriceKlineStatus.CHECKSUM_NOT_FOUND,
        ),
        (
            lambda plan, archive, responses: responses.pop(plan.url),
            BinanceMarkPriceKlineStatus.NOT_FOUND,
        ),
        (
            lambda plan, archive, responses: responses.__setitem__(
                plan.checksum_url,
                ArchiveHttpResponse(
                    200,
                    f"{'0' * 64}  {plan.url.rsplit('/', 1)[-1]}\n".encode(),
                    {},
                ),
            ),
            BinanceMarkPriceKlineStatus.INVALID_CONTENT,
        ),
    ],
)
def test_acquisition_failures_do_not_publish_completed_artifacts(
    tmp_path: Path,
    mutate: Callable[
        [BinanceArchiveObjectPlan, bytes, dict[str, ArchiveHttpResponse]], object
    ],
    expected_status: BinanceMarkPriceKlineStatus,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")
    plan = _plan(InstrumentId("BTCUSDT"), request_range)
    archive = _full_daily_archive(plan, 1_709_164_800_000)
    responses = _responses(plan, archive)
    mutate(plan, archive, responses)
    store = RawStore(tmp_path)

    result = BinanceMarkPriceKlineBackfill(store, http_get=FixtureHttp(responses)).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert result.completed is False
    assert result.objects[0].status is expected_status
    assert not store.path_for(RawObjectIdentity.from_request(plan.request)).exists()


@pytest.mark.parametrize(
    ("build_archive", "expected_status"),
    [
        (
            lambda plan: b"not a ZIP",
            BinanceMarkPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(
                plan,
                [_row(1_709_164_800_000)],
                member="unexpected.csv",
            ),
            BinanceMarkPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(plan, ["1,2,3\n"]),
            BinanceMarkPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _zip(plan.member_name, b"\xff\xfe"),
            BinanceMarkPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(plan, []),
            BinanceMarkPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(
                plan,
                [
                    _row(1_709_164_800_000),
                    _row(1_709_164_920_000),
                ],
            ),
            BinanceMarkPriceKlineStatus.COVERAGE_GAP,
        ),
        (
            lambda plan: _archive(
                plan,
                [
                    _row(1_709_164_800_000),
                    _row(1_709_164_800_000),
                ],
            ),
            BinanceMarkPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(
                plan,
                [_row(1_709_164_800_001)],
            ),
            BinanceMarkPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(
                plan,
                [_row(1_709_164_800_000).replace("61020.20000000", "not-a-number")],
            ),
            BinanceMarkPriceKlineStatus.INVALID_CONTENT,
        ),
        (
            lambda plan: _archive(
                plan,
                [_row(1_709_078_400_000)],
            ),
            BinanceMarkPriceKlineStatus.INVALID_CONTENT,
        ),
    ],
)
def test_parser_rejects_invalid_or_incomplete_objects(
    tmp_path: Path,
    build_archive: Callable[[BinanceArchiveObjectPlan], bytes],
    expected_status: BinanceMarkPriceKlineStatus,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")
    plan = _plan(InstrumentId("ETHUSDT"), request_range)
    archive = build_archive(plan)
    store = RawStore(tmp_path)

    result = BinanceMarkPriceKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan, archive))
    ).run(InstrumentId("ETHUSDT"), request_range)

    assert result.completed is False
    assert result.objects[0].status is expected_status
    assert not store.path_for(RawObjectIdentity.from_request(plan.request)).exists()


@pytest.mark.parametrize("failure", [429, 503, TimeoutError()])
def test_retryable_failures_are_classified_without_publication(
    tmp_path: Path, failure: int | TimeoutError
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-03-01T00:00:00")
    plan = _plan(InstrumentId("BTCUSDT"), request_range)

    def transport(url: str, timeout: float) -> ArchiveHttpResponse:
        del url, timeout
        if isinstance(failure, TimeoutError):
            raise failure
        return ArchiveHttpResponse(failure, b"retry later", {})

    store = RawStore(tmp_path)
    result = BinanceMarkPriceKlineBackfill(store, http_get=transport).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert result.completed is False
    assert result.objects[0].status is BinanceMarkPriceKlineStatus.RETRYABLE_FAILURE
    assert not store.path_for(RawObjectIdentity.from_request(plan.request)).exists()


def test_partial_first_archive_is_not_published_as_a_completed_object(
    tmp_path: Path,
) -> None:
    request_range = _range("2019-12-23T11:58:00", "2019-12-24T00:00:00")
    plan = _plan(InstrumentId("BTCUSDT"), request_range)
    rows = [_row(1_577_102_280_000 + offset * ONE_MINUTE_MS) for offset in range(722)]
    archive = _archive(plan, rows)
    store = RawStore(tmp_path)

    result = BinanceMarkPriceKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan, archive))
    ).run(InstrumentId("BTCUSDT"), request_range)

    assert result.completed is False
    assert result.objects[0].status is BinanceMarkPriceKlineStatus.COVERAGE_GAP
    assert result.objects[0].detail == (
        "archive rows do not cover the complete source object boundary"
    )
    assert result.objects[0].actual_record_range == request_range
    assert not store.path_for(RawObjectIdentity.from_request(plan.request)).exists()


def test_upstream_replacement_creates_a_new_immutable_raw_revision(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:01:00")
    plan = _plan(InstrumentId("BTCUSDT"), request_range)
    original = _full_daily_archive(plan, 1_709_164_800_000)
    replacement = _full_daily_archive(
        plan, 1_709_164_800_000, first_close="62000.00000000"
    )
    store = RawStore(tmp_path)

    first = BinanceMarkPriceKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan, original))
    ).run(InstrumentId("BTCUSDT"), request_range)
    second = BinanceMarkPriceKlineBackfill(
        store, http_get=FixtureHttp(_responses(plan, replacement))
    ).run(InstrumentId("BTCUSDT"), request_range)

    assert first.completed is True
    assert second.completed is True
    assert second.objects[0].status is BinanceMarkPriceKlineStatus.PUBLISHED
    revisions = store.list_verified_revisions(
        RawObjectIdentity.from_request(plan.request)
    )
    assert len(revisions) == 2
    assert {item.frame.item(0, "mark_close") for item in revisions} == {
        "61010.00000000",
        "62000.00000000",
    }


def test_incomplete_local_artifact_is_reported_as_local_failure(
    tmp_path: Path,
) -> None:
    request_range = _range("2024-02-29T00:00:00", "2024-02-29T00:01:00")
    plan = _plan(InstrumentId("ETHUSDT"), request_range)
    store = RawStore(tmp_path)
    incomplete_path = store.path_for(RawObjectIdentity.from_request(plan.request))
    incomplete_path.mkdir(parents=True)
    (incomplete_path / "interrupted-write").write_text("preserve", encoding="utf-8")

    result = BinanceMarkPriceKlineBackfill(store, http_get=FixtureHttp({})).run(
        InstrumentId("ETHUSDT"), request_range
    )

    assert result.completed is False
    assert result.objects[0].status is BinanceMarkPriceKlineStatus.LOCAL_FAILURE
    assert {path.name for path in incomplete_path.iterdir()} == {"interrupted-write"}
