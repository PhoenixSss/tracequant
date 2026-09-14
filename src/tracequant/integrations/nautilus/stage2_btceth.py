from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from nautilus_trader.adapters.binance import (
    BinanceDataClientConfig,
    BinanceEnvironment,
    BinanceInstrumentProviderConfig,
    BinanceProductType,
    load_binance_instruments,
)
from nautilus_trader.model import Bar, BarType, CryptoPerpetual, Price, Quantity
from nautilus_trader.persistence import ParquetDataCatalog

from tracequant.integrations.nautilus import (
    EXPECTED_VERSION,
    UPSTREAM_RELEASE_IDENTITY,
    distribution_version,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_COVERAGE_FILENAME,
    STAGE2_INSTRUMENT_IDS,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    STAGE2_MANIFEST_FILENAME,
    STAGE2_NAUTILUS_VERSION,
    STAGE2_SOURCE_SCHEMA,
    Stage2CoverageReport,
    Stage2DataError,
    Stage2DatasetConfig,
    Stage2KlineArchive,
    Stage2KlineRow,
    Stage2SeriesCoverage,
    Stage2SourceManifest,
    Stage2SourceObject,
    build_source_object,
    combined_source_checksum,
    discover_stage2_kline_archives,
    isoformat_utc,
    load_stage2_config,
    millis_to_nanos,
    parse_utc,
    read_verified_kline_archive,
    require_utc,
    stage2_bar_type_str,
    validate_kline_series,
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


def query_stage2_bars(catalog_path: Path, bar_type: str) -> tuple[Bar, ...]:
    if not catalog_path.is_absolute():
        raise Stage2DataError("catalog_path must be an absolute path")
    if not catalog_path.is_dir():
        raise Stage2DataError("catalog_path must be an existing directory")
    catalog = ParquetDataCatalog(str(catalog_path))
    return tuple(catalog.query_bars(identifiers=[bar_type]))


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
