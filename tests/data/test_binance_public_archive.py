import hashlib
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from tracequant.data import (
    ArchiveHttpResponse,
    BinanceArchiveAcquisitionOutcome,
    BinanceArchiveAcquisitionStatus,
    BinanceArchiveDatasetAdapter,
    BinanceArchiveObjectPlan,
    BinanceContractKlineBackfill,
    BinancePublicArchiveAcquisition,
    RawStore,
    plan_binance_contract_kline_archives,
)
from tracequant.domain import InstrumentId, TimeRange


def _archive(member_name: str, start_open_time: int) -> bytes:
    header = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_volume,taker_buy_quote_volume,ignore\n"
    )
    rows = "".join(
        f"{start_open_time + offset * 60_000},61000.0,61020.0,60990.0,"
        f"61010.0,12.5,{start_open_time + offset * 60_000 + 59_999},"
        "762500.0,42,6.0,366000.0,0\n"
        for offset in range(24 * 60)
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member_name, (header + rows).encode())
    return output.getvalue()


def test_contract_kline_uses_shared_archive_acquisition_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_range = TimeRange(
        start=datetime(2024, 2, 29, tzinfo=UTC),
        end=datetime(2024, 2, 29, 0, 1, tzinfo=UTC),
    )
    plan = cast(
        BinanceArchiveObjectPlan,
        plan_binance_contract_kline_archives(InstrumentId("BTCUSDT"), request_range)[0],
    )
    archive = _archive(plan.member_name, 1_709_164_800_000)
    checksum_name = plan.url.rsplit("/", 1)[-1]
    responses = {
        plan.url: ArchiveHttpResponse(
            200, archive, {"Content-Type": "application/zip"}
        ),
        plan.checksum_url: ArchiveHttpResponse(
            200,
            f"{hashlib.sha256(archive).hexdigest()}  {checksum_name}\n".encode(),
            {"Content-Type": "text/plain"},
        ),
    }
    calls = 0
    original_acquire = BinancePublicArchiveAcquisition.acquire

    def acquire(
        self: BinancePublicArchiveAcquisition,
        shared_plan: BinanceArchiveObjectPlan,
        adapter: BinanceArchiveDatasetAdapter,
    ) -> BinanceArchiveAcquisitionOutcome:
        nonlocal calls
        calls += 1
        return original_acquire(self, shared_plan, adapter)

    monkeypatch.setattr(BinancePublicArchiveAcquisition, "acquire", acquire)

    class FixtureTransport:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def __call__(self, url: str, timeout: float) -> ArchiveHttpResponse:
            del timeout
            self.calls.append(url)
            return responses.get(url, ArchiveHttpResponse(404, b"missing", {}))

    transport = FixtureTransport()
    store = RawStore(tmp_path)
    result = BinanceContractKlineBackfill(store, http_get=transport).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert calls == 1
    assert result.completed
    assert transport.calls == [plan.checksum_url, plan.url]
    artifact = store.read_request(plan.request)
    assert artifact.frame.height == 24 * 60
    assert artifact.manifest.provenance is not None
    assert artifact.manifest.provenance.csv_member == plan.member_name


def test_contract_kline_creates_an_isolated_adapter_per_archive_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_range = TimeRange(
        start=datetime(2024, 2, 29, tzinfo=UTC),
        end=datetime(2024, 3, 2, tzinfo=UTC),
    )
    assert (
        len(
            plan_binance_contract_kline_archives(InstrumentId("BTCUSDT"), request_range)
        )
        == 2
    )
    adapters: list[BinanceArchiveDatasetAdapter] = []
    adapter_ids: list[int] = []
    schema_identifiers: list[str] = []

    def acquire(
        self: BinancePublicArchiveAcquisition,
        shared_plan: BinanceArchiveObjectPlan,
        adapter: BinanceArchiveDatasetAdapter,
    ) -> BinanceArchiveAcquisitionOutcome:
        del self, shared_plan
        adapters.append(adapter)
        adapter_ids.append(id(adapter))
        schema_identifiers.append(adapter.raw_schema_identifier)
        if len(adapter_ids) == 1:
            adapter.raw_schema_identifier = "mutated-by-fixture"
        return BinanceArchiveAcquisitionOutcome(
            status=BinanceArchiveAcquisitionStatus.PUBLISHED
        )

    monkeypatch.setattr(BinancePublicArchiveAcquisition, "acquire", acquire)

    result = BinanceContractKlineBackfill(RawStore(tmp_path)).run(
        InstrumentId("BTCUSDT"), request_range
    )

    assert result.completed
    assert len(adapter_ids) == 2
    assert adapters[0] is not adapters[1]
    assert schema_identifiers == [
        "binance.um.contract-kline.csv.v1",
        "binance.um.contract-kline.csv.v1",
    ]
