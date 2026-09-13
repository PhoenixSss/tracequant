from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nautilus_trader.adapters.binance import (
    BINANCE_CLIENT_ID,
    BinanceDataClientConfig,
    BinanceDataClientFactory,
    BinanceEnvironment,
    BinanceInstrumentProviderConfig,
    BinanceProductType,
    load_binance_instruments,
)
from nautilus_trader.common import DataActor, DataActorConfig, Environment
from nautilus_trader.live import LiveNode
from nautilus_trader.model import Bar, BarType, TraderId
from nautilus_trader.persistence import ParquetDataCatalog

from tracequant.integrations.nautilus import UPSTREAM_RELEASE_IDENTITY
from tracequant.source_data.stage1_btcusdt import (
    HOUR_NS,
    STAGE1_BAR_TYPE,
    STAGE1_CATALOG_ENVIRONMENT,
    STAGE1_CATALOG_SCHEMA,
    STAGE1_DATA_PATH,
    STAGE1_INSTRUMENT_ID,
    STAGE1_MAX_BARS_PER_REQUEST,
    STAGE1_PROVENANCE_FILENAME,
    STAGE1_SOURCE_URL,
    Stage1BarProvenance,
    Stage1DataError,
    closed_bar_ts_event,
    expected_bar_count,
    request_segments,
    require_stage1_window,
    require_utc,
    stage1_window,
)

_CONNECTION_TIMEOUT_SECS = 120


def stage1_bar_type() -> BarType:
    return BarType.from_str(STAGE1_BAR_TYPE)


def stage1_catalog_partition(catalog_root: Path) -> Path:
    if not catalog_root.is_absolute():
        raise Stage1DataError("catalog_root must be an absolute path")
    if not catalog_root.is_dir():
        raise Stage1DataError("catalog_root must be an existing directory")
    return (
        catalog_root
        / "nautilus"
        / UPSTREAM_RELEASE_IDENTITY
        / STAGE1_CATALOG_SCHEMA
        / STAGE1_CATALOG_ENVIRONMENT
    )


def build_stage1_binance_data_client_config(proxy_url: str) -> BinanceDataClientConfig:
    if not proxy_url:
        raise Stage1DataError(
            "Nautilus Binance public data requests require a runtime HTTPS proxy"
        )
    config = BinanceDataClientConfig(
        product_type=BinanceProductType.USD_M,
        environment=BinanceEnvironment.LIVE,
        api_key=None,
        api_secret=None,
        proxy_url=proxy_url,
        instrument_provider=BinanceInstrumentProviderConfig(
            load_all=False,
            load_ids=[STAGE1_INSTRUMENT_ID],
        ),
    )
    if not config.has_proxy_url:
        raise Stage1DataError("Binance data client config must carry a proxy_url")
    return config


def bars_checksum(bars: Sequence[Bar]) -> str:
    digest = hashlib.sha256()
    for bar in bars:
        line = (
            f"{bar.bar_type}|{int(bar.ts_event)}|"
            f"{bar.open}|{bar.high}|{bar.low}|{bar.close}|{bar.volume}\n"
        )
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()


def validate_selected_btcusdt_bars(
    bars: Sequence[Bar],
    *,
    window_start: datetime,
    window_end: datetime,
) -> None:
    start = require_utc(window_start)
    end = require_utc(window_end)
    expected = expected_bar_count(start, end)
    if not bars:
        raise Stage1DataError("bar sequence is empty")
    if len(bars) != expected:
        raise Stage1DataError("bar count does not match the requested window")
    expected_type = stage1_bar_type()
    previous: int | None = None
    for bar in bars:
        if bar.bar_type != expected_type:
            raise Stage1DataError(
                "bar type, symbol, or interval is not the stage 1 target"
            )
        ts_event = int(bar.ts_event)
        if previous is None:
            if ts_event != closed_bar_ts_event(start):
                raise Stage1DataError("first bar event time does not match the window")
        else:
            if ts_event <= previous:
                raise Stage1DataError("bars are not strictly increasing by event time")
            if ts_event - previous != HOUR_NS:
                raise Stage1DataError(
                    "bars contain a non-hourly gap or duplicate event time"
                )
        previous = ts_event
    if previous != closed_bar_ts_event(end - timedelta(hours=1)):
        raise Stage1DataError("last bar event time does not match the window")


def prepare_selected_btcusdt_history(
    catalog_root: Path,
    *,
    bars: Sequence[Bar] | None = None,
    expected_checksum: str | None = None,
    fetched_at: datetime | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> tuple[tuple[Bar, ...], Stage1BarProvenance]:
    if bars is None:
        start, end = require_stage1_window(*stage1_window())
        if window_start is not None or window_end is not None:
            raise Stage1DataError("live Nautilus fetch uses the locked stage 1 window")
        native_bars, instruments = fetch_selected_btcusdt_history()
        observed_at = fetched_at or datetime.now(UTC)
    else:
        if window_start is None or window_end is None:
            raise Stage1DataError("ingested bars require an explicit window")
        start, end = require_stage1_window(window_start, window_end)
        native_bars = tuple(bars)
        instruments = ()
        observed_at = fetched_at or datetime.now(UTC)
    return _ingest_selected_btcusdt_history(
        catalog_root,
        bars=native_bars,
        instruments=instruments,
        window_start=start,
        window_end=end,
        expected_checksum=expected_checksum,
        fetched_at=observed_at,
    )


def fetch_selected_btcusdt_history() -> tuple[tuple[Bar, ...], tuple[object, ...]]:
    start, end = stage1_window()
    segments = request_segments(start, end)
    proxy_url = _runtime_proxy_url()
    config = build_stage1_binance_data_client_config(proxy_url)
    instruments = tuple(_run_load_instruments(config))
    actor = _Stage1HistoryActor(segments)
    builder = (
        LiveNode.builder(
            "STAGE1-BTCUSDT-HISTORY",
            TraderId.from_str("TRACEQUANT-001"),
            Environment.LIVE,
        )
        .add_data_client(None, BinanceDataClientFactory(), config)
        .with_timeout_connection(_CONNECTION_TIMEOUT_SECS)
        .with_delay_post_stop_secs(0)
        .with_reconciliation(False)
    )
    node = builder.build()
    node.add_actor(actor)
    try:
        node.run()
    finally:
        node.dispose()
    if actor.error is not None:
        raise Stage1DataError(actor.error)
    return tuple(actor.bars), instruments


def read_selected_btcusdt_history(
    catalog_root: Path,
) -> tuple[tuple[Bar, ...], Stage1BarProvenance]:
    partition = stage1_catalog_partition(catalog_root)
    provenance = _read_provenance(partition / STAGE1_PROVENANCE_FILENAME)
    catalog = ParquetDataCatalog(str(partition))
    bars = tuple(catalog.query_bars())
    _assert_round_trip(bars, provenance)
    return bars, provenance


def _ingest_selected_btcusdt_history(
    catalog_root: Path,
    *,
    bars: Sequence[Bar],
    instruments: Sequence[object],
    window_start: datetime,
    window_end: datetime,
    expected_checksum: str | None,
    fetched_at: datetime,
) -> tuple[tuple[Bar, ...], Stage1BarProvenance]:
    validate_selected_btcusdt_bars(
        bars, window_start=window_start, window_end=window_end
    )
    checksum = bars_checksum(bars)
    if expected_checksum is not None and checksum != expected_checksum:
        raise Stage1DataError("checksum does not match the fixed source record")
    partition = stage1_catalog_partition(catalog_root)
    if partition.exists() and any(partition.iterdir()):
        raise Stage1DataError(
            "catalog partition already exists and must not be mutated"
        )
    partition.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(str(partition))
    if instruments:
        catalog.write_instruments(list(instruments))
    catalog.write_bars(list(bars))
    provenance = Stage1BarProvenance(
        source_url=STAGE1_SOURCE_URL,
        data_path=STAGE1_DATA_PATH,
        fetched_at=require_utc(fetched_at),
        window_start=window_start,
        window_end=window_end,
        bar_count=len(bars),
        checksum_sha256=checksum,
        bar_type=STAGE1_BAR_TYPE,
        first_ts_event=int(bars[0].ts_event),
        last_ts_event=int(bars[-1].ts_event),
        runtime_identity=UPSTREAM_RELEASE_IDENTITY,
    )
    _write_provenance(partition / STAGE1_PROVENANCE_FILENAME, provenance)
    read_bars = tuple(catalog.query_bars())
    _assert_round_trip(read_bars, provenance)
    if bars_checksum(read_bars) != checksum:
        raise Stage1DataError(
            "catalog round-trip checksum does not match the source record"
        )
    return read_bars, provenance


def _assert_round_trip(bars: Sequence[Bar], provenance: Stage1BarProvenance) -> None:
    validate_selected_btcusdt_bars(
        bars,
        window_start=provenance.window_start,
        window_end=provenance.window_end,
    )
    if len(bars) != provenance.bar_count:
        raise Stage1DataError("catalog bar count does not match provenance")
    if str(bars[0].bar_type) != provenance.bar_type:
        raise Stage1DataError("catalog bar type does not match provenance")
    if int(bars[0].ts_event) != provenance.first_ts_event:
        raise Stage1DataError("catalog first event time does not match provenance")
    if int(bars[-1].ts_event) != provenance.last_ts_event:
        raise Stage1DataError("catalog last event time does not match provenance")
    if bars_checksum(bars) != provenance.checksum_sha256:
        raise Stage1DataError("catalog checksum does not match provenance")


def _write_provenance(path: Path, provenance: Stage1BarProvenance) -> None:
    path.write_text(
        json.dumps(provenance.to_json_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_provenance(path: Path) -> Stage1BarProvenance:
    if not path.is_file():
        raise Stage1DataError("stage 1 provenance record is missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise Stage1DataError("stage 1 provenance record is invalid")
    provenance = Stage1BarProvenance.from_json_dict(payload)
    if provenance.data_path != STAGE1_DATA_PATH:
        raise Stage1DataError("provenance data path is not USE_NAUTILUS")
    if provenance.source_url != STAGE1_SOURCE_URL:
        raise Stage1DataError("provenance source URL does not match the selected path")
    if provenance.bar_type != STAGE1_BAR_TYPE:
        raise Stage1DataError("provenance bar type does not match the selected path")
    if provenance.runtime_identity != UPSTREAM_RELEASE_IDENTITY:
        raise Stage1DataError(
            "provenance runtime identity does not match the locked runtime"
        )
    return provenance


def _runtime_proxy_url() -> str:
    proxy_url = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    if not proxy_url:
        raise Stage1DataError(
            "Nautilus Binance public data requests require a runtime HTTPS proxy"
        )
    return proxy_url


def _run_load_instruments(config: BinanceDataClientConfig) -> list[object]:
    return list(asyncio.run(load_binance_instruments(config)))


class _Stage1HistoryActor(DataActor):
    def __init__(self, segments: tuple[tuple[datetime, datetime], ...]) -> None:
        super().__init__(DataActorConfig())
        self._segments = segments
        self._index = 0
        self.bars: list[Bar] = []
        self.error: str | None = None

    def on_start(self) -> None:
        self._request_current()

    def on_historical_bars(self, bars: object) -> None:
        try:
            payload = list(bars) if isinstance(bars, Iterable) else None
        except TypeError:
            payload = None
        if payload is None:
            self.error = "historical bar payload is not iterable"
            self.shutdown_system(reason=self.error)
            return
        received = [bar for bar in payload if isinstance(bar, Bar)]
        if len(received) != len(payload):
            self.error = "historical payload contains non-Bar objects"
            self.shutdown_system(reason=self.error)
            return
        if not received:
            self.error = "empty historical bar segment"
            self.shutdown_system(reason=self.error)
            return
        self.bars.extend(received)
        self._index += 1
        if self._index >= len(self._segments):
            self.shutdown_system(reason="stage1-history-complete")
            return
        self._request_current()

    def on_fault(self) -> None:
        self.error = "Nautilus historical bar actor faulted"
        self.shutdown_system(reason=self.error)

    def _request_current(self) -> None:
        start, end = self._segments[self._index]
        self.request_bars(
            bar_type=stage1_bar_type(),
            start=start,
            end=end,
            limit=STAGE1_MAX_BARS_PER_REQUEST,
            client_id=BINANCE_CLIENT_ID,
        )
