from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from nautilus_trader.adapters.binance import (
    BinanceDataClientConfig,
    BinanceEnvironment,
    BinanceInstrumentProviderConfig,
    BinanceProductType,
    load_binance_instruments,
)
from nautilus_trader.backtest import BacktestDataConfig
from nautilus_trader.common import Cache, Clock
from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model import (
    Bar,
    BarType,
    CryptoPerpetual,
    FundingRateUpdate,
    InstrumentId,
    MarkPriceUpdate,
    Price,
    Quantity,
)
from nautilus_trader.persistence import ParquetDataCatalog, StreamingFeatherWriter

from tracequant.integrations.nautilus import (
    EXPECTED_VERSION,
    UPSTREAM_RELEASE_IDENTITY,
    distribution_version,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_COVERAGE_FILENAME,
    STAGE2_DATA_TYPE_MARK,
    STAGE2_FUNDING_STREAM_ID,
    STAGE2_INSTRUMENT_IDS,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    STAGE2_MANIFEST_FILENAME,
    STAGE2_NAUTILUS_VERSION,
    STAGE2_SOURCE_SCHEMA,
    Stage2CoverageReport,
    Stage2DataError,
    Stage2DatasetConfig,
    Stage2FundingArchive,
    Stage2FundingRow,
    Stage2KlineArchive,
    Stage2KlineRow,
    Stage2SeriesCoverage,
    Stage2SourceManifest,
    Stage2SourceObject,
    build_funding_source_object,
    build_source_object,
    combined_source_checksum,
    discover_stage2_funding_archives,
    discover_stage2_kline_archives,
    discover_stage2_mark_archives,
    funding_interval_minutes,
    isoformat_utc,
    load_stage2_config,
    millis_to_nanos,
    parse_utc,
    read_verified_funding_archive,
    read_verified_kline_archive,
    require_recent_mark_for_funding,
    require_utc,
    stage2_bar_type_str,
    validate_funding_series,
    validate_kline_series,
    validate_mark_series,
    write_json,
)


def stage2_bar_type(instrument_id: str, interval: str) -> BarType:
    return BarType.from_str(stage2_bar_type_str(instrument_id, interval))


def bars_from_kline_rows(
    rows: Sequence[Stage2KlineRow],
    *,
    instrument_id: str,
    interval: str,
) -> tuple[Bar, ...]:
    bar_type = stage2_bar_type(instrument_id, interval)
    bars: list[Bar] = []
    for row in rows:
        close_time = int(row.close_time)
        ts_event = millis_to_nanos(close_time)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price.from_str(row.open),
                high=Price.from_str(row.high),
                low=Price.from_str(row.low),
                close=Price.from_str(row.close),
                volume=Quantity.from_str(row.volume),
                ts_event=ts_event,
                ts_init=ts_event,
            )
        )
    return tuple(bars)


def mark_price_updates_from_kline_rows(
    rows: Sequence[Stage2KlineRow],
    *,
    instrument_id: str,
) -> tuple[MarkPriceUpdate, ...]:
    native_id = InstrumentId.from_str(instrument_id)
    marks: list[MarkPriceUpdate] = []
    for row in rows:
        ts_event = millis_to_nanos(int(row.close_time))
        marks.append(
            MarkPriceUpdate(
                instrument_id=native_id,
                value=Price.from_str(row.close),
                ts_event=ts_event,
                ts_init=ts_event,
            )
        )
    return tuple(marks)


def funding_rate_updates_from_rows(
    rows: Sequence[Stage2FundingRow],
    *,
    instrument_id: str,
) -> tuple[FundingRateUpdate, ...]:
    native_id = InstrumentId.from_str(instrument_id)
    updates: list[FundingRateUpdate] = []
    for row in rows:
        ts_event = millis_to_nanos(int(row.calc_time))
        interval = funding_interval_minutes(row)
        updates.append(
            FundingRateUpdate(
                instrument_id=native_id,
                rate=Decimal(row.last_funding_rate),
                ts_event=ts_event,
                ts_init=ts_event,
                interval=interval,
                next_funding_ns=ts_event,
            )
        )
    return tuple(updates)


def instrument_snapshot_payload(
    instruments: Sequence[CryptoPerpetual],
    *,
    fetched_at: datetime,
) -> dict[str, object]:
    serialized = [instrument.to_dict() for instrument in instruments]
    return {
        "checksum_sha256": _snapshot_checksum(serialized),
        "fetched_at": isoformat_utc(fetched_at),
        "instruments": serialized,
        "nautilus_version": STAGE2_NAUTILUS_VERSION,
        "runtime_identity": UPSTREAM_RELEASE_IDENTITY,
    }


def instruments_from_snapshot(
    path: Path,
) -> tuple[tuple[CryptoPerpetual, ...], datetime, str]:
    if not path.is_file():
        raise Stage2DataError("instrument snapshot is missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise Stage2DataError("instrument snapshot is invalid")
    if payload.get("nautilus_version") != STAGE2_NAUTILUS_VERSION:
        raise Stage2DataError("instrument snapshot nautilus version does not match")
    if payload.get("runtime_identity") != UPSTREAM_RELEASE_IDENTITY:
        raise Stage2DataError("instrument snapshot runtime identity does not match")
    raw_instruments = payload.get("instruments")
    if not isinstance(raw_instruments, list) or not raw_instruments:
        raise Stage2DataError("instrument snapshot has no instruments")
    decoded: list[CryptoPerpetual] = []
    for item in raw_instruments:
        if not isinstance(item, dict):
            raise Stage2DataError("instrument snapshot contains non-object instruments")
        decoded.append(CryptoPerpetual.from_dict(item))
    instruments = tuple(decoded)
    checksum = _snapshot_checksum([item.to_dict() for item in instruments])
    expected = payload.get("checksum_sha256")
    if not isinstance(expected, str) or checksum != expected:
        raise Stage2DataError("instrument snapshot checksum does not match")
    fetched_at = parse_utc(_require_snapshot_str(payload, "fetched_at"))
    return instruments, fetched_at, checksum


def build_stage2_binance_instrument_client_config(
    proxy_url: str,
) -> BinanceDataClientConfig:
    if not proxy_url:
        raise Stage2DataError(
            "Nautilus Binance instrument requests require a runtime HTTPS proxy"
        )
    config = BinanceDataClientConfig(
        product_type=BinanceProductType.USD_M,
        environment=BinanceEnvironment.LIVE,
        api_key=None,
        api_secret=None,
        proxy_url=proxy_url,
        instrument_provider=BinanceInstrumentProviderConfig(
            load_all=False,
            load_ids=list(STAGE2_INSTRUMENT_IDS),
        ),
    )
    if not config.has_proxy_url:
        raise Stage2DataError("Binance instrument client config must carry a proxy_url")
    return config


def fetch_stage2_instruments() -> tuple[tuple[CryptoPerpetual, ...], datetime]:
    config = build_stage2_binance_instrument_client_config(_runtime_proxy_url())
    loaded = tuple(
        _require_crypto_perpetual(item) for item in _run_load_instruments(config)
    )
    _require_stage2_instruments(loaded)
    return loaded, datetime.now(UTC)


def prepare_stage2_bar_catalog(
    config_path: Path,
    *,
    repository_root: Path,
    instruments: Sequence[object] | None = None,
    instrument_snapshot_path: Path | None = None,
    fetched_at: datetime | None = None,
) -> tuple[Stage2SourceManifest, Stage2CoverageReport]:
    if distribution_version() != EXPECTED_VERSION:
        raise Stage2DataError(
            "installed Nautilus version does not match the locked runtime"
        )
    config = load_stage2_config(config_path, repository_root=repository_root)
    native, observed_at, snapshot_checksum = _resolve_instruments(
        config,
        instruments=instruments,
        instrument_snapshot_path=instrument_snapshot_path,
        fetched_at=fetched_at,
    )
    source_objects, prepared = _load_validated_stage2_series(config)
    catalog = ParquetDataCatalog(str(config.catalog_path))
    catalog.write_instruments(list(native))
    coverage_series: list[Stage2SeriesCoverage] = []
    for instrument_id, interval, bars, series_coverage in prepared:
        catalog.write_bars(list(bars))
        queried = tuple(
            catalog.query_bars(
                identifiers=[stage2_bar_type_str(instrument_id, interval)]
            )
        )
        _assert_round_trip(bars, queried)
        coverage_series.append(series_coverage)
    manifest = Stage2SourceManifest(
        schema=STAGE2_SOURCE_SCHEMA,
        dataset_id=config.dataset_id,
        nautilus_version=config.nautilus_version,
        sources=tuple(source_objects),
    )
    report = Stage2CoverageReport(
        dataset_id=config.dataset_id,
        series=tuple(coverage_series),
    )
    snapshot_payload = instrument_snapshot_payload(native, fetched_at=observed_at)
    write_json(config.catalog_path / STAGE2_MANIFEST_FILENAME, manifest.to_json_dict())
    write_json(config.catalog_path / STAGE2_COVERAGE_FILENAME, report.to_json_dict())
    write_json(
        config.catalog_path / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
        snapshot_payload,
    )
    write_json(_default_instrument_snapshot_path(config), snapshot_payload)
    if snapshot_checksum != _snapshot_checksum(
        [instrument.to_dict() for instrument in native]
    ):
        raise Stage2DataError("instrument snapshot checksum does not match")
    return manifest, report


def prepare_stage2_mark_funding_catalog(
    config_path: Path,
    *,
    repository_root: Path,
    instruments: Sequence[object] | None = None,
    instrument_snapshot_path: Path | None = None,
    fetched_at: datetime | None = None,
) -> tuple[Stage2SourceManifest, Stage2CoverageReport]:
    if distribution_version() != EXPECTED_VERSION:
        raise Stage2DataError(
            "installed Nautilus version does not match the locked runtime"
        )
    config = load_stage2_config(config_path, repository_root=repository_root)
    native, observed_at, snapshot_checksum = _resolve_instruments(
        config,
        instruments=instruments,
        instrument_snapshot_path=instrument_snapshot_path,
        fetched_at=fetched_at,
    )
    source_objects, marks, fundings, coverage_series = (
        _load_validated_stage2_mark_funding(config)
    )
    catalog = ParquetDataCatalog(str(config.catalog_path))
    catalog.write_instruments(list(native))
    for instrument_id, mark_updates in marks:
        catalog.write_mark_price_updates(list(mark_updates))
        queried = tuple(
            catalog.query_mark_price_updates(instrument_ids=[instrument_id])
        )
        _assert_mark_round_trip(mark_updates, queried)
    write_stage2_funding_rate_updates(
        catalog,
        config.catalog_path,
        fundings,
        instance_id=STAGE2_FUNDING_STREAM_ID,
    )
    manifest = Stage2SourceManifest(
        schema=STAGE2_SOURCE_SCHEMA,
        dataset_id=config.dataset_id,
        nautilus_version=config.nautilus_version,
        sources=tuple(source_objects),
    )
    report = Stage2CoverageReport(
        dataset_id=config.dataset_id,
        series=tuple(coverage_series),
    )
    snapshot_payload = instrument_snapshot_payload(native, fetched_at=observed_at)
    write_json(config.catalog_path / STAGE2_MANIFEST_FILENAME, manifest.to_json_dict())
    write_json(config.catalog_path / STAGE2_COVERAGE_FILENAME, report.to_json_dict())
    write_json(
        config.catalog_path / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
        snapshot_payload,
    )
    write_json(_default_instrument_snapshot_path(config), snapshot_payload)
    if snapshot_checksum != _snapshot_checksum(
        [instrument.to_dict() for instrument in native]
    ):
        raise Stage2DataError("instrument snapshot checksum does not match")
    return manifest, report


def write_stage2_funding_rate_updates(
    catalog: ParquetDataCatalog,
    catalog_path: Path,
    updates: Sequence[tuple[str, tuple[FundingRateUpdate, ...]]],
    *,
    instance_id: str,
) -> None:
    stream_root = catalog_path / "live" / instance_id
    stream_root.mkdir(parents=True, exist_ok=True)
    writer = StreamingFeatherWriter(
        path=str(stream_root),
        cache=Cache(),
        clock=Clock.new_test(),
        include_types=["funding_rate_update"],
        replace=True,
    )
    try:
        for _instrument_id, series in updates:
            if not series:
                raise Stage2DataError("funding series is empty")
            for item in series:
                writer.write(item)
        writer.flush()
    finally:
        if not writer.is_closed:
            writer.close()
    catalog.convert_stream_to_data(
        instance_id,
        "funding_rate_update",
        subdirectory="live",
    )


def query_stage2_bars(
    catalog_path: Path,
    bar_type: str,
    *,
    start_ns: int | None = None,
    end_ns: int | None = None,
) -> tuple[Bar, ...]:
    catalog = _require_catalog(catalog_path)
    return tuple(catalog.query_bars(identifiers=[bar_type], start=start_ns, end=end_ns))


def query_stage2_mark_prices(
    catalog_path: Path,
    instrument_id: str,
    *,
    start_ns: int | None = None,
    end_ns: int | None = None,
) -> tuple[MarkPriceUpdate, ...]:
    catalog = _require_catalog(catalog_path)
    return tuple(
        catalog.query_mark_price_updates(
            instrument_ids=[instrument_id],
            start=start_ns,
            end=end_ns,
        )
    )


def project_stage2_bars(
    catalog_path: Path,
    bar_type: str,
    *,
    start_ns: int,
    end_ns_exclusive: int,
) -> tuple[dict[str, str | int], ...]:
    bars = query_stage2_bars(
        catalog_path,
        bar_type,
        start_ns=start_ns,
        end_ns=_inclusive_end(start_ns, end_ns_exclusive),
    )
    records = tuple(_bar_projection(bar) for bar in bars)
    if not records:
        raise Stage2DataError("catalog query returned no bars")
    return records


def project_stage2_mark_prices(
    catalog_path: Path,
    instrument_id: str,
    *,
    start_ns: int,
    end_ns_exclusive: int,
) -> tuple[dict[str, str | int], ...]:
    marks = query_stage2_mark_prices(
        catalog_path,
        instrument_id,
        start_ns=start_ns,
        end_ns=_inclusive_end(start_ns, end_ns_exclusive),
    )
    records = tuple(_mark_projection(mark) for mark in marks)
    if not records:
        raise Stage2DataError("catalog query returned no mark prices")
    return records


def query_stage2_funding_files(
    catalog_path: Path,
    instrument_id: str,
    *,
    start_ns: int | None = None,
    end_ns: int | None = None,
) -> tuple[Path, ...]:
    if instrument_id not in STAGE2_INSTRUMENT_IDS:
        raise Stage2DataError("instrument is not a stage 2 target")
    catalog = _require_catalog(catalog_path)
    files = catalog.query_files(
        "funding_rate_update",
        identifiers=[instrument_id],
        start=start_ns,
        end=end_ns,
    )
    resolved: list[Path] = []
    for item in files:
        path = Path(item)
        if not path.is_absolute():
            path = catalog_path / path
        resolved.append(path)
    return tuple(resolved)


def stage2_funding_backtest_data_config(
    catalog_path: Path,
    instrument_id: str,
) -> BacktestDataConfig:
    if instrument_id not in STAGE2_INSTRUMENT_IDS:
        raise Stage2DataError("instrument is not a stage 2 target")
    return BacktestDataConfig(
        data_type="FundingRateUpdate",
        catalog_path=str(catalog_path),
        instrument_id=InstrumentId.from_str(instrument_id),
    )


def nautilus_close_sma(
    bars: Sequence[Bar],
    period: int,
) -> tuple[float | None, ...]:
    if period <= 0:
        raise Stage2DataError("sma period must be positive")
    indicator = SimpleMovingAverage(period)
    values: list[float | None] = []
    for bar in bars:
        indicator.handle_bar(bar)
        values.append(float(indicator.value) if indicator.initialized else None)
    return tuple(values)


def stage2_price_spec(catalog_path: Path, instrument_id: str) -> tuple[int, str]:
    catalog = _require_catalog(catalog_path)
    matches = [
        instrument
        for instrument in catalog.instruments(instrument_ids=[instrument_id])
        if str(instrument.id) == instrument_id
    ]
    if len(matches) != 1:
        raise Stage2DataError("instrument identity does not match")
    instrument = matches[0]
    return int(instrument.price_precision), str(instrument.price_increment)


def _require_catalog(catalog_path: Path) -> ParquetDataCatalog:
    if not catalog_path.is_absolute():
        raise Stage2DataError("catalog_path must be an absolute path")
    if not catalog_path.is_dir():
        raise Stage2DataError("catalog_path must be an existing directory")
    return ParquetDataCatalog(str(catalog_path))


def _inclusive_end(start_ns: int, end_ns_exclusive: int) -> int:
    if end_ns_exclusive <= start_ns:
        raise Stage2DataError("query window is inverted")
    return end_ns_exclusive - 1


def _bar_projection(bar: Bar) -> dict[str, str | int]:
    return {
        "bar_type": str(bar.bar_type),
        "instrument_id": str(bar.bar_type.instrument_id),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
        "ts_event": int(bar.ts_event),
        "ts_init": int(bar.ts_init),
    }


def _mark_projection(mark: MarkPriceUpdate) -> dict[str, str | int]:
    return {
        "instrument_id": str(mark.instrument_id),
        "value": str(mark.value),
        "ts_event": int(mark.ts_event),
        "ts_init": int(mark.ts_init),
    }


def _default_instrument_snapshot_path(config: Stage2DatasetConfig) -> Path:
    return config.raw_root / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME


def _load_validated_stage2_series(
    config: Stage2DatasetConfig,
) -> tuple[
    list[Stage2SourceObject],
    list[tuple[str, str, tuple[Bar, ...], Stage2SeriesCoverage]],
]:
    archives = discover_stage2_kline_archives(config)
    grouped: dict[tuple[str, str], list[Stage2KlineArchive]] = {}
    for archive in archives:
        key = (archive.instrument_id, archive.interval)
        grouped.setdefault(key, []).append(archive)
    source_objects: list[Stage2SourceObject] = []
    prepared: list[tuple[str, str, tuple[Bar, ...], Stage2SeriesCoverage]] = []
    for instrument_id in config.instrument_ids:
        for interval in config.bar_intervals:
            series_archives = grouped[(instrument_id, interval)]
            series_rows: list[Stage2KlineRow] = []
            series_digests: list[str] = []
            for archive in series_archives:
                rows, digest = read_verified_kline_archive(
                    archive,
                    window_start=config.window_start,
                    window_end=config.window_end,
                )
                source_objects.append(build_source_object(archive, rows, digest))
                series_rows.extend(rows)
                series_digests.append(digest)
            series_coverage = validate_kline_series(
                series_rows,
                instrument_id=instrument_id,
                interval=interval,
                source_checksum=combined_source_checksum(tuple(series_digests)),
            )
            bars = bars_from_kline_rows(
                series_rows, instrument_id=instrument_id, interval=interval
            )
            _assert_bars_match_rows(
                bars, series_rows, instrument_id=instrument_id, interval=interval
            )
            prepared.append((instrument_id, interval, bars, series_coverage))
    return source_objects, prepared


def _load_validated_stage2_mark_funding(
    config: Stage2DatasetConfig,
) -> tuple[
    list[Stage2SourceObject],
    list[tuple[str, tuple[MarkPriceUpdate, ...]]],
    list[tuple[str, tuple[FundingRateUpdate, ...]]],
    list[Stage2SeriesCoverage],
]:
    mark_archives = discover_stage2_mark_archives(config)
    funding_archives = discover_stage2_funding_archives(config)
    marks_grouped: dict[str, list[Stage2KlineArchive]] = {}
    for mark_archive in mark_archives:
        marks_grouped.setdefault(mark_archive.instrument_id, []).append(mark_archive)
    funding_grouped: dict[str, list[Stage2FundingArchive]] = {}
    for funding_archive in funding_archives:
        funding_grouped.setdefault(funding_archive.instrument_id, []).append(
            funding_archive
        )
    source_objects: list[Stage2SourceObject] = []
    marks: list[tuple[str, tuple[MarkPriceUpdate, ...]]] = []
    fundings: list[tuple[str, tuple[FundingRateUpdate, ...]]] = []
    coverage_series: list[Stage2SeriesCoverage] = []
    for instrument_id in config.instrument_ids:
        mark_rows: list[Stage2KlineRow] = []
        mark_digests: list[str] = []
        for mark_archive in marks_grouped[instrument_id]:
            mark_chunk, digest = read_verified_kline_archive(
                mark_archive,
                window_start=config.window_start,
                window_end=config.window_end,
            )
            source_objects.append(
                build_source_object(
                    mark_archive,
                    mark_chunk,
                    digest,
                    data_type=STAGE2_DATA_TYPE_MARK,
                )
            )
            mark_rows.extend(mark_chunk)
            mark_digests.append(digest)
        mark_coverage = validate_mark_series(
            mark_rows,
            instrument_id=instrument_id,
            source_checksum=combined_source_checksum(tuple(mark_digests)),
        )
        mark_updates = mark_price_updates_from_kline_rows(
            mark_rows, instrument_id=instrument_id
        )
        _assert_marks_match_rows(mark_updates, mark_rows, instrument_id=instrument_id)
        funding_rows: list[Stage2FundingRow] = []
        funding_digests: list[str] = []
        for funding_archive in funding_grouped[instrument_id]:
            funding_chunk, digest = read_verified_funding_archive(
                funding_archive,
                window_start=config.window_start,
                window_end=config.window_end,
            )
            source_objects.append(
                build_funding_source_object(funding_archive, funding_chunk, digest)
            )
            funding_rows.extend(funding_chunk)
            funding_digests.append(digest)
        funding_coverage = validate_funding_series(
            funding_rows,
            instrument_id=instrument_id,
            source_checksum=combined_source_checksum(tuple(funding_digests)),
        )
        require_recent_mark_for_funding(mark_rows, funding_rows)
        funding_updates = funding_rate_updates_from_rows(
            funding_rows, instrument_id=instrument_id
        )
        _assert_funding_match_rows(
            funding_updates, funding_rows, instrument_id=instrument_id
        )
        marks.append((instrument_id, mark_updates))
        fundings.append((instrument_id, funding_updates))
        coverage_series.append(mark_coverage)
        coverage_series.append(funding_coverage)
    return source_objects, marks, fundings, coverage_series


def _resolve_instruments(
    config: Stage2DatasetConfig,
    *,
    instruments: Sequence[object] | None,
    instrument_snapshot_path: Path | None,
    fetched_at: datetime | None,
) -> tuple[tuple[CryptoPerpetual, ...], datetime, str]:
    if instruments is not None:
        native = tuple(_require_crypto_perpetual(item) for item in instruments)
        _require_stage2_instruments(native)
        observed_at = (
            require_utc(fetched_at) if fetched_at is not None else datetime.now(UTC)
        )
        checksum = _snapshot_checksum([item.to_dict() for item in native])
        return native, observed_at, checksum
    snapshot = instrument_snapshot_path
    if snapshot is None:
        snapshot = _default_instrument_snapshot_path(config)
        if not snapshot.is_file():
            snapshot = None
    if snapshot is not None:
        native, observed_at, checksum = instruments_from_snapshot(snapshot)
        _require_stage2_instruments(native)
        return native, observed_at, checksum
    native, observed_at = fetch_stage2_instruments()
    checksum = _snapshot_checksum([item.to_dict() for item in native])
    return native, observed_at, checksum


def _require_crypto_perpetual(instrument: object) -> CryptoPerpetual:
    if not isinstance(instrument, CryptoPerpetual):
        raise Stage2DataError("instrument is not a Nautilus CryptoPerpetual")
    return instrument


def _require_stage2_instruments(instruments: Sequence[CryptoPerpetual]) -> None:
    ids = tuple(str(instrument.id) for instrument in instruments)
    if ids != STAGE2_INSTRUMENT_IDS:
        raise Stage2DataError("instrument identity does not match")


def _assert_bars_match_rows(
    bars: Sequence[Bar],
    rows: Sequence[Stage2KlineRow],
    *,
    instrument_id: str,
    interval: str,
) -> None:
    expected_type = stage2_bar_type(instrument_id, interval)
    if len(bars) != len(rows):
        raise Stage2DataError("bar count does not match source records")
    for bar, row in zip(bars, rows, strict=True):
        if bar.bar_type != expected_type:
            raise Stage2DataError(
                "bar type, symbol, or interval is not the stage 2 target"
            )
        ts_event = millis_to_nanos(int(row.close_time))
        if int(bar.ts_event) != ts_event or int(bar.ts_init) != ts_event:
            raise Stage2DataError("bar event time does not match close_time")
        if str(bar.open) != row.open or str(bar.high) != row.high:
            raise Stage2DataError("bar OHLC does not match source strings")
        if str(bar.low) != row.low or str(bar.close) != row.close:
            raise Stage2DataError("bar OHLC does not match source strings")
        if str(bar.volume) != row.volume:
            raise Stage2DataError("bar volume does not match source strings")


def _assert_marks_match_rows(
    marks: Sequence[MarkPriceUpdate],
    rows: Sequence[Stage2KlineRow],
    *,
    instrument_id: str,
) -> None:
    expected_id = InstrumentId.from_str(instrument_id)
    if len(marks) != len(rows):
        raise Stage2DataError("mark count does not match source records")
    for mark, row in zip(marks, rows, strict=True):
        if mark.instrument_id != expected_id:
            raise Stage2DataError("mark instrument is not the stage 2 target")
        ts_event = millis_to_nanos(int(row.close_time))
        if int(mark.ts_event) != ts_event or int(mark.ts_init) != ts_event:
            raise Stage2DataError("mark event time does not match close_time")
        if str(mark.value) != row.close:
            raise Stage2DataError("mark price does not match source close")


def _assert_funding_match_rows(
    updates: Sequence[FundingRateUpdate],
    rows: Sequence[Stage2FundingRow],
    *,
    instrument_id: str,
) -> None:
    expected_id = InstrumentId.from_str(instrument_id)
    if len(updates) != len(rows):
        raise Stage2DataError("funding count does not match source records")
    for update, row in zip(updates, rows, strict=True):
        if update.instrument_id != expected_id:
            raise Stage2DataError("funding instrument is not the stage 2 target")
        ts_event = millis_to_nanos(int(row.calc_time))
        if int(update.ts_event) != ts_event or int(update.ts_init) != ts_event:
            raise Stage2DataError("funding event time does not match calc_time")
        if update.next_funding_ns != ts_event:
            raise Stage2DataError("funding next_funding_ns does not match calc_time")
        if Decimal(str(update.rate)) != Decimal(row.last_funding_rate):
            raise Stage2DataError("funding rate does not match source strings")
        if update.interval != funding_interval_minutes(row):
            raise Stage2DataError("funding interval does not match source hours")


def _assert_mark_round_trip(
    written: Sequence[MarkPriceUpdate], queried: Sequence[MarkPriceUpdate]
) -> None:
    if len(written) != len(queried):
        raise Stage2DataError("catalog mark count does not match the source record")
    for source, loaded in zip(written, queried, strict=True):
        if loaded.instrument_id != source.instrument_id:
            raise Stage2DataError(
                "catalog mark instrument does not match the source record"
            )
        if int(loaded.ts_event) != int(source.ts_event):
            raise Stage2DataError("catalog event time does not match the source record")
        if int(loaded.ts_init) != int(source.ts_init):
            raise Stage2DataError("catalog init time does not match the source record")
        if str(loaded.value) != str(source.value):
            raise Stage2DataError("catalog mark price does not match the source record")


def _assert_round_trip(written: Sequence[Bar], queried: Sequence[Bar]) -> None:
    if len(written) != len(queried):
        raise Stage2DataError("catalog bar count does not match the source record")
    for source, loaded in zip(written, queried, strict=True):
        if loaded.bar_type != source.bar_type:
            raise Stage2DataError("catalog bar type does not match the source record")
        if int(loaded.ts_event) != int(source.ts_event):
            raise Stage2DataError("catalog event time does not match the source record")
        if int(loaded.ts_init) != int(source.ts_init):
            raise Stage2DataError("catalog init time does not match the source record")
        if (
            str(loaded.open) != str(source.open)
            or str(loaded.high) != str(source.high)
            or str(loaded.low) != str(source.low)
            or str(loaded.close) != str(source.close)
            or str(loaded.volume) != str(source.volume)
        ):
            raise Stage2DataError("catalog OHLCV does not match the source record")


def _snapshot_checksum(serialized: Sequence[dict[str, object]]) -> str:
    payload = json.dumps(list(serialized), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_snapshot_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise Stage2DataError(
            f"instrument snapshot field {key} must be a non-empty string"
        )
    return value


def _runtime_proxy_url() -> str:
    proxy_url = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    if not proxy_url:
        raise Stage2DataError(
            "Nautilus Binance instrument requests require a runtime HTTPS proxy"
        )
    return proxy_url


def _run_load_instruments(config: BinanceDataClientConfig) -> list[object]:
    return list(asyncio.run(load_binance_instruments(config)))
