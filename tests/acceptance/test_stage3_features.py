from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import polars as pl
import pytest
from nautilus_trader.model import (
    Bar,
    CryptoPerpetual,
    Currency,
    FundingRateUpdate,
    InstrumentId,
    MarkPriceUpdate,
    Price,
    Quantity,
    Symbol,
)
from nautilus_trader.persistence import ParquetDataCatalog

from tracequant.integrations.nautilus import UPSTREAM_RELEASE_IDENTITY
from tracequant.integrations.nautilus import stage2_artifact as artifact_module
from tracequant.integrations.nautilus.stage2_artifact import sha256_file
from tracequant.integrations.nautilus.stage2_btceth import (
    instrument_snapshot_payload,
    stage2_bar_type,
    write_stage2_funding_rate_updates,
)
from tracequant.research import stage3_features as stage3
from tracequant.research.stage3_features import (
    FEATURE_ATOL,
    FEATURE_LOOKBACK_HOURS,
    FEATURE_NAMES,
    FEATURE_RTOL,
    FEATURE_SCHEMA_DIGEST,
    HOUR_NS,
    LABEL_HORIZON_HOURS,
    MARK_MAX_AGE_NS,
    STAGE2_ACCEPTANCE_DIGEST,
    STAGE2_DATASET_DIGEST,
    STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM,
    STAGE2_MARKET_DATA_MANIFEST_DIGEST,
    STAGE2_SOURCE_MANIFEST_DIGEST,
    STAGE3_CONFIG_SCHEMA,
    BarProjection,
    FeatureObservation,
    FundingProjection,
    IncrementalFeatureState,
    MarkProjection,
    Stage3Config,
    Stage3DataError,
    bind_accepted_stage2_catalog,
    build_feature_rows,
    feature_frame,
    feature_schema_digest,
    feature_schema_payload,
    load_accepted_feature_window,
    load_stage3_config,
    load_stage3_config_from_env,
    mark_allowed_gap_ns,
    purge_training_rows,
    require_feature_frame_schema,
)
from tracequant.research.views import load_bars, load_funding, load_mark_prices
from tracequant.source_data.stage2_btceth import (
    STAGE2_BAR_INTERVALS,
    STAGE2_COVERAGE_FILENAME,
    STAGE2_DATA_TYPE_BARS,
    STAGE2_DATASET_ID,
    STAGE2_DIGEST_FILENAME,
    STAGE2_EXPECTED_SOURCE_COUNT,
    STAGE2_EXPECTED_SUPPLEMENTAL_SOURCE_COUNT,
    STAGE2_FUNDING_SCHEDULE_TOLERANCE_MS,
    STAGE2_FUNDING_STREAM_ID,
    STAGE2_INSTRUMENT_IDS,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    STAGE2_INTERVAL_MS,
    STAGE2_MANIFEST_FILENAME,
    STAGE2_MARK_ALLOWED_GAPS,
    STAGE2_NAUTILUS_VERSION,
    STAGE2_SOURCE_SCHEMA,
    STAGE2_WINDOW_START_ISO,
    Stage2CoverageReport,
    Stage2SeriesCoverage,
    Stage2SourceManifest,
    Stage2SourceObject,
    acceptance_record_digest,
    coverage_summary,
    dataset_digest,
    datetime_to_nanos,
    market_data_manifest_digest,
    parse_utc,
    require_complete_acceptance_record,
    source_manifest_digest,
    stage2_bar_type_str,
    write_json,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_RECORD = (
    REPOSITORY_ROOT / "docs/product/stage2-btceth-dataset-acceptance.json"
)
BTC = "BTCUSDT-PERP.BINANCE"
ETH = "ETHUSDT-PERP.BINANCE"

MS_NS = 1_000_000
QUARTER_HOUR_NS = 15 * 60 * 1_000_000_000
FUNDING_INTERVAL_NS = 8 * HOUR_NS
DATASET_START = parse_utc(STAGE2_WINDOW_START_ISO)
DATASET_START_NS = datetime_to_nanos(DATASET_START)
# The accepted grids are anchored on the tracked coverage, not on the window.
BAR_ORIGIN_NS = DATASET_START_NS + HOUR_NS - MS_NS
MARK_ORIGIN_NS = DATASET_START_NS + QUARTER_HOUR_NS - MS_NS
EVALUATION_START = DATASET_START + timedelta(hours=168)
EVALUATION_END = DATASET_START + timedelta(days=19)
# The accepted coverage records two explained mark omissions as open_time
# intervals; the row each one omits is the bar that closes at
# 2020-01-19T13:29:59.999Z on the close_time-anchored accepted grid.
APPROVED_MARK_GAP_INDEX = (
    mark_allowed_gap_ns()[0][0] - MARK_ORIGIN_NS
) // QUARTER_HOUR_NS


def _bar(index: int, *, instrument_id: str = BTC) -> BarProjection:
    close = 100.0 + index * 0.2 + (index % 5) * 0.01
    return BarProjection(
        instrument_id=instrument_id,
        open=f"{close - 0.05:.8f}",
        high=f"{close + 0.20:.8f}",
        low=f"{close - 0.20:.8f}",
        close=f"{close:.8f}",
        volume=f"{10.0 + (index % 17):.8f}",
        ts_event=BAR_ORIGIN_NS + index * HOUR_NS,
    )


def _events(
    count: int,
    *,
    start_index: int = 0,
    instrument_id: str = BTC,
) -> tuple[tuple[MarkProjection, ...], tuple[FundingProjection, ...]]:
    omitted = {start for start, _ in mark_allowed_gap_ns()}
    marks = tuple(
        MarkProjection(
            instrument_id=instrument_id,
            value=f"{100.1 + index * 0.05:.8f}",
            ts_event=MARK_ORIGIN_NS + index * QUARTER_HOUR_NS,
        )
        for index in range(start_index * 4, (start_index + count) * 4)
        if MARK_ORIGIN_NS + index * QUARTER_HOUR_NS not in omitted
    )
    funding = tuple(
        FundingProjection(
            instrument_id=instrument_id,
            rate=f"{0.0001 + index * 0.000001:.8f}",
            ts_event=DATASET_START_NS + index * HOUR_NS,
        )
        for index in range(start_index, start_index + count)
        if index % 8 == 0
    )
    return marks, funding


def _feed(
    bars: Sequence[BarProjection],
    marks: Sequence[MarkProjection],
    funding: Sequence[FundingProjection],
    *,
    tradable_from: int | None = None,
) -> tuple[IncrementalFeatureState, tuple[FeatureObservation, ...]]:
    """Drive the native-event state exactly as a live Strategy would."""
    state = IncrementalFeatureState(bars[0].instrument_id)
    mark_index = 0
    funding_index = 0
    rows: list[FeatureObservation] = []
    for bar in bars:
        while mark_index < len(marks) and marks[mark_index].ts_event <= bar.ts_event:
            state.push_mark(marks[mark_index])
            mark_index += 1
        while (
            funding_index < len(funding)
            and funding[funding_index].ts_event <= bar.ts_event
        ):
            state.push_funding(funding[funding_index])
            funding_index += 1
        rows.append(
            state.push_bar(
                bar,
                tradable=tradable_from is not None and bar.ts_event >= tradable_from,
            )
        )
    return state, tuple(rows)


def _config(catalog_path: Path, tmp_path: Path) -> Stage3Config:
    return Stage3Config(
        schema=STAGE3_CONFIG_SCHEMA,
        dataset_id=STAGE2_DATASET_ID,
        acceptance_digest=STAGE2_ACCEPTANCE_DIGEST,
        dataset_digest=STAGE2_DATASET_DIGEST,
        source_manifest_digest=STAGE2_SOURCE_MANIFEST_DIGEST,
        market_data_manifest_digest=STAGE2_MARKET_DATA_MANIFEST_DIGEST,
        instrument_snapshot_checksum=STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM,
        runtime_identity=UPSTREAM_RELEASE_IDENTITY,
        artifact_lock_path=(
            REPOSITORY_ROOT / stage3.STAGE2_ARTIFACT_LOCK_RELATIVE_PATH
        ),
        catalog_path=catalog_path,
        evidence_root=tmp_path / "evidence",
        run_root=tmp_path / "runs",
    )


# --- accepted-catalog fixture -------------------------------------------------


def _grid(
    origin_ns: int, cadence_ns: int, limit_ns: int, skip: frozenset[int]
) -> tuple[int, ...]:
    count = -(-(limit_ns - 1 - origin_ns) // cadence_ns)
    return tuple(
        origin_ns + index * cadence_ns for index in range(count) if index not in skip
    )


def _perpetual(instrument_id: str, symbol: str, base: str) -> CryptoPerpetual:
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
        ts_event=0,
        ts_init=0,
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
    )


def _source_objects(count: int, *, label: str) -> tuple[Stage2SourceObject, ...]:
    return tuple(
        Stage2SourceObject(
            path=f"fixture/{label}/{index}.zip",
            source_url=f"https://example.invalid/{label}/{index}.zip",
            sha256=hashlib.sha256(f"{label}:{index}".encode()).hexdigest(),
            checksum_url=f"https://example.invalid/{label}/{index}.zip.CHECKSUM",
            source_kind="archive",
            instrument_id=STAGE2_INSTRUMENT_IDS[index % len(STAGE2_INSTRUMENT_IDS)],
            data_type=STAGE2_DATA_TYPE_BARS,
            start_ns=DATASET_START_NS,
            end_ns=DATASET_START_NS + HOUR_NS,
            rows=1,
        )
        for index in range(count)
    )


def _series_coverage(entry: dict[str, object]) -> Stage2SeriesCoverage:
    instrument_id = cast(str, entry["instrument_id"])
    bar_type = entry.get("bar_type")
    bar_interval = ""
    if isinstance(bar_type, str):
        bar_interval = next(
            interval
            for interval in STAGE2_BAR_INTERVALS
            if stage2_bar_type_str(instrument_id, interval) == bar_type
        )
    return Stage2SeriesCoverage(
        instrument_id=instrument_id,
        data_type=cast(str, entry["data_type"]),
        bar_interval=bar_interval,
        row_count=cast(int, entry["row_count"]),
        first_ts_event=cast(int, entry["first_ts_event"]),
        last_ts_event=cast(int, entry["last_ts_event"]),
        duplicate_count=cast(int, entry["duplicate_count"]),
        out_of_order_count=cast(int, entry["out_of_order_count"]),
        gap_count=cast(int, entry["gap_count"]),
        source_checksum=cast(str, entry["source_sha256"]),
        gap_explanation=cast(str, entry["gap_explanation"]),
    )


def _build_accepted_catalog(
    root: Path,
    *,
    missing_marks: frozenset[int] = frozenset(),
    missing_funding: frozenset[int] = frozenset(),
    include_approved_gap: bool = False,
    truncate_marks: bool = False,
    funding_offset_ms: int = 0,
) -> tuple[Path, Path, dict[str, object], dict[str, object], Path]:
    """Write a deterministic Stage 2-shaped accepted catalog under ``root``.

    The catalog is small, but every identity surface it carries is derived the
    way the accepted Stage 2 pipeline derives it: the coverage report is rebuilt
    from the tracked acceptance record, the digest record comes from the
    production payload builder, and the series sit on the accepted grids. Only
    the source-object identities are fixture-local, because the accepted
    800-object manifest lives outside this repository.

    ``funding_offset_ms`` shifts every written funding event off its accepted
    grid slot by that many milliseconds. Accepted funding carries the source
    ``calc_time`` unchanged, so the real series is not schedule-aligned and sits
    a few milliseconds off the close_time-anchored grid; the tracked coverage
    records the same shape at its tail. The declared coverage is the tracked
    record either way, so the offset only moves the rows the consumer reads.
    """
    tracked = cast(
        dict[str, object],
        json.loads(ACCEPTANCE_RECORD.read_text(encoding="utf-8")),
    )
    coverage = Stage2CoverageReport(
        dataset_id=STAGE2_DATASET_ID,
        series=tuple(
            _series_coverage(cast(dict[str, object], item))
            for item in cast(list[object], tracked["coverage_summary"])
        ),
    )
    assert list(coverage_summary(coverage)) == tracked["coverage_summary"]

    catalog_path = root / "accepted-catalog"
    catalog_path.mkdir(parents=True)
    catalog = ParquetDataCatalog(str(catalog_path))
    instruments = (
        _perpetual(BTC, "BTCUSDT", "BTC"),
        _perpetual(ETH, "ETHUSDT", "ETH"),
    )
    catalog.write_instruments(list(instruments))
    snapshot = instrument_snapshot_payload(
        instruments, fetched_at=parse_utc("2026-09-15T03:08:10Z")
    )

    decision_end_ns = datetime_to_nanos(EVALUATION_END)
    label_end_ns = decision_end_ns + LABEL_HORIZON_HOURS * HOUR_NS
    omitted_marks = (
        frozenset() if include_approved_gap else frozenset({APPROVED_MARK_GAP_INDEX})
    )
    omitted_marks = omitted_marks | missing_marks
    for instrument_id in STAGE2_INSTRUMENT_IDS:
        bars = []
        for index, ts_event in enumerate(
            _grid(BAR_ORIGIN_NS, HOUR_NS, label_end_ns, frozenset())
        ):
            close = Price.from_str(f"{100.0 + index * 0.25:.2f}")
            bars.append(
                Bar(
                    bar_type=stage2_bar_type(instrument_id, "1h"),
                    open=close,
                    high=Price.from_str(f"{100.0 + index * 0.25 + 0.5:.2f}"),
                    low=Price.from_str(f"{100.0 + index * 0.25 - 0.5:.2f}"),
                    close=close,
                    volume=Quantity.from_str(f"{10.0 + (index % 13):.3f}"),
                    ts_event=ts_event,
                    ts_init=ts_event,
                )
            )
        catalog.write_bars(bars)
        mark_grid = _grid(
            MARK_ORIGIN_NS, QUARTER_HOUR_NS, decision_end_ns, omitted_marks
        )
        catalog.write_mark_price_updates(
            [
                MarkPriceUpdate(
                    instrument_id=InstrumentId.from_str(instrument_id),
                    value=Price.from_str(f"{100.05 + ts_event % 100_000:.2f}"),
                    ts_event=ts_event,
                    ts_init=ts_event,
                )
                for ts_event in (mark_grid[:-3] if truncate_marks else mark_grid)
            ]
        )

    write_stage2_funding_rate_updates(
        catalog,
        catalog_path,
        [
            (
                instrument_id,
                tuple(
                    FundingRateUpdate(
                        instrument_id=InstrumentId.from_str(instrument_id),
                        rate=Decimal(f"0.0001{index:03d}"),
                        ts_event=ts_event,
                        ts_init=ts_event,
                        interval=480,
                        next_funding_ns=ts_event,
                    )
                    for ts_event in (
                        slot + funding_offset_ms * MS_NS
                        for slot in _grid(
                            DATASET_START_NS,
                            FUNDING_INTERVAL_NS,
                            decision_end_ns,
                            missing_funding,
                        )
                    )
                ),
            )
            for instrument_id in STAGE2_INSTRUMENT_IDS
        ],
        instance_id=STAGE2_FUNDING_STREAM_ID,
    )

    manifest = Stage2SourceManifest(
        schema=STAGE2_SOURCE_SCHEMA,
        dataset_id=STAGE2_DATASET_ID,
        nautilus_version=STAGE2_NAUTILUS_VERSION,
        instrument_snapshot_checksum=cast(str, snapshot["checksum_sha256"]),
        instrument_snapshot_fetched_at=cast(str, snapshot["fetched_at"]),
        sources=_source_objects(STAGE2_EXPECTED_SOURCE_COUNT, label="source"),
        supplemental_sources=_source_objects(
            STAGE2_EXPECTED_SUPPLEMENTAL_SOURCE_COUNT, label="supplemental"
        ),
    )
    record = dict(tracked)
    record["catalog_evidence"] = {
        "coverage_filename": STAGE2_COVERAGE_FILENAME,
        "dataset_digest_filename": STAGE2_DIGEST_FILENAME,
        "instrument_snapshot_filename": STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
        "source_manifest_filename": STAGE2_MANIFEST_FILENAME,
        "source_object_count": len(manifest.sources),
        "supplemental_source_count": len(manifest.supplemental_sources),
    }
    record["coverage_summary"] = list(coverage_summary(coverage))
    record["instrument_snapshot"] = manifest.instrument_snapshot_identity()
    record["market_data_manifest_digest"] = market_data_manifest_digest(manifest)
    record["source_manifest_digest"] = source_manifest_digest(manifest)
    record["dataset_digest"] = dataset_digest(
        manifest=manifest,
        coverage=coverage,
        runtime_identity=UPSTREAM_RELEASE_IDENTITY,
    )
    record["acceptance_digest"] = acceptance_record_digest(record)
    require_complete_acceptance_record(record)

    write_json(catalog_path / STAGE2_MANIFEST_FILENAME, manifest.to_json_dict())
    write_json(catalog_path / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME, snapshot)
    write_json(
        catalog_path / STAGE2_COVERAGE_FILENAME,
        {
            "dataset_id": STAGE2_DATASET_ID,
            "series": cast(list[object], record["coverage_summary"]),
        },
    )
    write_json(
        catalog_path / STAGE2_DIGEST_FILENAME,
        {
            "coverage": record["coverage_summary"],
            "dataset_id": record["dataset_id"],
            "instrument_snapshot": record["instrument_snapshot"],
            "market_data_manifest_digest": record["market_data_manifest_digest"],
            "nautilus_version": record["nautilus_version"],
            "runtime_identity": record["runtime_identity"],
            "source_manifest_digest": record["source_manifest_digest"],
        },
    )
    record_path = root / "stage2-acceptance.json"
    write_json(record_path, record)
    artifact_lock_path = _write_fixture_artifact_lock(
        catalog_path, root, record, snapshot
    )
    return catalog_path, record_path, record, snapshot, artifact_lock_path


def _write_fixture_artifact_lock(
    catalog_path: Path,
    root: Path,
    record: Mapping[str, object],
    snapshot: Mapping[str, object],
) -> Path:
    """Lock the fixture tree exactly as the tracked release locks production."""
    files = [
        {
            "path": item.relative_to(catalog_path).as_posix(),
            "sha256": sha256_file(item),
            "size": item.stat().st_size,
        }
        for item in sorted(catalog_path.rglob("*"))
        if item.is_file()
    ]
    identities = {
        "acceptance_digest": record["acceptance_digest"],
        "dataset_digest": record["dataset_digest"],
        "instrument_snapshot_checksum": snapshot["checksum_sha256"],
        "market_data_manifest_digest": record["market_data_manifest_digest"],
        "runtime_identity": record["runtime_identity"],
        "source_manifest_digest": record["source_manifest_digest"],
    }
    base_url = (
        f"https://github.com/{artifact_module.RELEASE_REPOSITORY}/releases/download/"
        f"{artifact_module.RELEASE_TAG}"
    )
    lock_path = root / "stage2-artifact.lock.json"
    write_json(
        lock_path,
        {
            "archive_root": STAGE2_DATASET_ID,
            "dataset_id": STAGE2_DATASET_ID,
            "files": files,
            "identities": identities,
            "release": {
                "archive_asset": {
                    "format": artifact_module.ARCHIVE_FORMAT,
                    "name": artifact_module.ARCHIVE_ASSET_NAME,
                    "sha256": "0" * 64,
                    "size": 1,
                    "url": f"{base_url}/{artifact_module.ARCHIVE_ASSET_NAME}",
                },
                "file_manifest_asset": {
                    "name": artifact_module.FILE_MANIFEST_ASSET_NAME,
                    "sha256": "1" * 64,
                    "size": 1,
                    "url": f"{base_url}/{artifact_module.FILE_MANIFEST_ASSET_NAME}",
                },
                "repository": artifact_module.RELEASE_REPOSITORY,
                "tag": artifact_module.RELEASE_TAG,
            },
            "schema": artifact_module.ARTIFACT_LOCK_SCHEMA,
        },
    )
    return lock_path


def _bind_fixture_identity(
    monkeypatch: pytest.MonkeyPatch,
    record: dict[str, object],
    snapshot: dict[str, object],
    artifact_lock_path: Path,
) -> Stage3Config:
    """Rebind the Stage 3 locked identity to the fixture-derived accepted record.

    The production constants pin the one accepted 2020-2026 dataset whose
    800-object manifest lives outside this repository, so a repository-local
    fixture cannot reproduce them byte for byte. Rebinding the pinned identity
    values and immutable catalog lock to this mechanically derived equivalent lets
    the formal loader be
    exercised end to end. Every other tracked acceptance field is still checked
    by ``require_complete_acceptance_record`` on the fixture record, and the
    unmodified constants keep their own fail-closed coverage in the mismatch
    assertion above.
    """
    monkeypatch.setattr(stage3, "STAGE2_ACCEPTANCE_DIGEST", record["acceptance_digest"])
    monkeypatch.setattr(stage3, "STAGE2_DATASET_DIGEST", record["dataset_digest"])
    monkeypatch.setattr(
        stage3, "STAGE2_SOURCE_MANIFEST_DIGEST", record["source_manifest_digest"]
    )
    monkeypatch.setattr(
        stage3,
        "STAGE2_MARKET_DATA_MANIFEST_DIGEST",
        record["market_data_manifest_digest"],
    )
    monkeypatch.setattr(
        stage3, "STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM", snapshot["checksum_sha256"]
    )
    identities = {
        "acceptance_digest": record["acceptance_digest"],
        "dataset_digest": record["dataset_digest"],
        "instrument_snapshot_checksum": snapshot["checksum_sha256"],
        "market_data_manifest_digest": record["market_data_manifest_digest"],
        "runtime_identity": record["runtime_identity"],
        "source_manifest_digest": record["source_manifest_digest"],
    }
    monkeypatch.setattr(artifact_module, "LOCKED_IDENTITIES", identities)
    monkeypatch.setattr(
        stage3, "STAGE2_ARTIFACT_LOCK_SHA256", sha256_file(artifact_lock_path)
    )
    return Stage3Config(
        schema=STAGE3_CONFIG_SCHEMA,
        dataset_id=STAGE2_DATASET_ID,
        acceptance_digest=cast(str, record["acceptance_digest"]),
        dataset_digest=cast(str, record["dataset_digest"]),
        source_manifest_digest=cast(str, record["source_manifest_digest"]),
        market_data_manifest_digest=cast(str, record["market_data_manifest_digest"]),
        instrument_snapshot_checksum=cast(str, snapshot["checksum_sha256"]),
        runtime_identity=UPSTREAM_RELEASE_IDENTITY,
        artifact_lock_path=artifact_lock_path,
        catalog_path=Path("/fixture-catalog"),
        evidence_root=Path("/fixture-evidence"),
        run_root=Path("/fixture-runs"),
    )


def _accepted_config(
    template: Stage3Config,
    catalog_path: Path,
    tmp_path: Path,
    *,
    artifact_lock_path: Path | None = None,
) -> Stage3Config:
    return Stage3Config(
        schema=template.schema,
        dataset_id=template.dataset_id,
        acceptance_digest=template.acceptance_digest,
        dataset_digest=template.dataset_digest,
        source_manifest_digest=template.source_manifest_digest,
        market_data_manifest_digest=template.market_data_manifest_digest,
        instrument_snapshot_checksum=template.instrument_snapshot_checksum,
        runtime_identity=template.runtime_identity,
        artifact_lock_path=artifact_lock_path or template.artifact_lock_path,
        catalog_path=catalog_path,
        evidence_root=tmp_path / "accepted-evidence",
        run_root=tmp_path / "accepted-runs",
    )


def _reference_features(
    bars_frame: pl.DataFrame,
    marks_frame: pl.DataFrame,
    funding_frame: pl.DataFrame,
    *,
    instrument_code: float,
) -> pl.DataFrame:
    """Recompute every Stage 3 feature with Polars, not with the tested state.

    This is the independent batch path: returns and volatilities come from
    vectorized expressions, the auxiliary as-of lookups are window joins, and the
    24h funding sum is a difference of cumulative sums. A defect in the
    incremental Python formula cannot be inherited here.
    """
    frame = (
        bars_frame.select(
            pl.col("ts_event").alias("decision_ts"),
            pl.col("close").cast(pl.Float64).alias("close"),
            pl.col("high").cast(pl.Float64).alias("high"),
            pl.col("low").cast(pl.Float64).alias("low"),
            pl.col("volume").cast(pl.Float64).alias("volume"),
        )
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1.0).alias("ret_1h"),
            (pl.col("close") / pl.col("close").shift(4) - 1.0).alias("ret_4h"),
            (pl.col("close") / pl.col("close").shift(24) - 1.0).alias("ret_24h"),
            (pl.col("close") / pl.col("close").shift(168) - 1.0).alias("ret_168h"),
            ((pl.col("high") - pl.col("low")) / pl.col("close")).alias("range_1h"),
            (pl.col("close") / pl.col("close").shift(1)).log().alias("log_return"),
        )
        .with_columns(
            pl.col("log_return")
            .pow(2)
            .rolling_sum(window_size=24, min_samples=24)
            .sqrt()
            .alias("rv_24h"),
            pl.col("log_return")
            .pow(2)
            .rolling_sum(window_size=168, min_samples=168)
            .sqrt()
            .alias("rv_168h"),
            (
                (
                    pl.col("volume")
                    - pl.col("volume").rolling_mean(window_size=24, min_samples=24)
                )
                / pl.col("volume").rolling_std(window_size=24, min_samples=24, ddof=0)
            ).alias("volume_z_24h"),
        )
        .sort("decision_ts")
    )
    marks = marks_frame.select(
        pl.col("ts_event").alias("decision_ts"),
        pl.col("value").cast(pl.Float64).alias("mark"),
    ).sort("decision_ts")
    funding = (
        funding_frame.select(
            pl.col("ts_event"), pl.col("rate").cast(pl.Float64).alias("rate")
        )
        .sort("ts_event")
        .with_columns(pl.col("rate").cum_sum().alias("cum"))
    )
    frame = frame.join_asof(marks, on="decision_ts", strategy="backward")
    frame = frame.join_asof(
        funding.select(
            pl.col("ts_event").alias("decision_ts"), pl.col("rate").alias("funding")
        ).sort("decision_ts"),
        on="decision_ts",
        strategy="backward",
    )
    frame = frame.with_columns((pl.col("decision_ts") - 24 * HOUR_NS).alias("floor_ts"))
    frame = frame.sort("floor_ts").join_asof(
        funding.select(
            pl.col("ts_event").alias("floor_ts"), pl.col("cum").alias("cum_floor")
        ).sort("floor_ts"),
        on="floor_ts",
        strategy="backward",
    )
    frame = frame.sort("decision_ts").join_asof(
        funding.select(
            pl.col("ts_event").alias("decision_ts"), pl.col("cum").alias("cum_at")
        ).sort("decision_ts"),
        on="decision_ts",
        strategy="backward",
    )
    hour = ((pl.col("decision_ts") // (3_600 * 1_000_000_000)) % 24).cast(pl.Float64)
    angle = 2.0 * math.pi * hour / 24.0
    return frame.with_columns(
        (pl.col("mark") / pl.col("close") - 1.0).alias("basis_mark_last"),
        pl.col("funding").alias("funding_latest"),
        (pl.col("cum_at") - pl.col("cum_floor")).alias("funding_sum_24h"),
        angle.sin().alias("hour_sin"),
        angle.cos().alias("hour_cos"),
        pl.lit(instrument_code).alias("instrument_code"),
    )


def test_stage3_features_are_bound_to_accepted_catalog_and_causal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A behavior fixture cannot masquerade as the accepted 2020-2026 catalog.
    fixture_catalog = tmp_path / "fixture-catalog"
    fixture_catalog.mkdir()
    with pytest.raises(Stage3DataError, match="catalog identity|missing"):
        bind_accepted_stage2_catalog(
            _config(fixture_catalog, tmp_path),
            acceptance_record_path=ACCEPTANCE_RECORD,
        )

    bars = tuple(_bar(index) for index in range(177))
    marks, funding = _events(len(bars))
    evaluation_start = bars[168].ts_event
    batch = build_feature_rows(
        bars,
        marks,
        funding,
        decision_start_ts=evaluation_start,
        require_ready_at_decision_start=True,
    )
    assert all(row.status == "warming_up" for row in batch[:168])
    assert batch[168].status == "ready"
    assert batch[168].tradable is True
    assert batch[167].tradable is False
    assert batch[168].decision_ts == datetime_to_nanos(
        parse_utc("2020-01-08T00:59:59.999Z")
    )
    serialized_features = cast(
        list[dict[str, object]], feature_schema_payload()["features"]
    )
    assert tuple(item["name"] for item in serialized_features) == FEATURE_NAMES
    assert batch[168].feature_schema_digest == FEATURE_SCHEMA_DIGEST
    assert len(batch[168].require_ready()) == 14
    assert batch[168].label_available is True
    assert batch[168].label_end_ts == batch[168].decision_ts + 4 * HOUR_NS
    expected_label = math.log(float(bars[172].close) / float(bars[169].open))
    assert batch[168].label_log_return_4h == pytest.approx(expected_label)
    assert all(not row.label_available for row in batch[-4:] if row.status == "ready")

    # The native-event state produces identical rows for the same decisions.
    _, incremental = _feed(bars, marks, funding, tradable_from=evaluation_start)
    for left, right in zip(batch[168:], incremental[168:], strict=True):
        assert left.decision_ts == right.decision_ts
        assert left.require_ready() == pytest.approx(
            right.require_ready(), abs=FEATURE_ATOL, rel=FEATURE_RTOL
        )

    # A future mark is retained for its own future decision and cannot leak backward.
    future_mark = MarkProjection(BTC, "999999", bars[-1].ts_event + HOUR_NS)
    with_future = build_feature_rows(bars, (*marks, future_mark), funding)
    assert with_future[168].require_ready() == batch[168].require_ready()

    frame = feature_frame(batch[168:-4])
    require_feature_frame_schema(frame)
    assert frame.columns[2:16] == list(FEATURE_NAMES)
    assert all(str(frame.schema[name]) == "Float64" for name in FEATURE_NAMES)

    # Purge removes labels ending on the evaluation boundary, not only after it.
    boundary = batch[172].label_end_ts
    assert boundary is not None
    purged = purge_training_rows(batch, evaluation_start=boundary)
    assert purged
    assert all(
        row.label_end_ts is not None and row.label_end_ts < boundary for row in purged
    )

    # A stopped mark stream must not turn ready once its last value is stale.
    with pytest.raises(Stage3DataError, match="mark is stale"):
        _feed(bars[:169], marks[: 169 * 4 - 2], funding)

    with pytest.raises(Stage3DataError, match="instrument"):
        build_feature_rows(
            bars,
            (MarkProjection(ETH, "100", bars[0].ts_event),),
            funding,
        )
    with pytest.raises(Stage3DataError, match="duplicate or out-of-order"):
        build_feature_rows(bars, (marks[0], marks[0]), funding)

    # A truncated decision series cannot satisfy the named readiness boundary.
    short = bars[40:]
    with pytest.raises(Stage3DataError, match="complete lookback"):
        build_feature_rows(
            short,
            marks,
            funding,
            decision_start_ts=short[0].ts_event,
            require_ready_at_decision_start=True,
        )

    # --- formal accepted-catalog path ----------------------------------------
    catalog_path, record_path, record, snapshot, artifact_lock_path = (
        _build_accepted_catalog(tmp_path)
    )
    template = _bind_fixture_identity(monkeypatch, record, snapshot, artifact_lock_path)
    config = _accepted_config(template, catalog_path, tmp_path)

    window = load_accepted_feature_window(
        config,
        acceptance_record_path=record_path,
        start=DATASET_START,
        end=EVALUATION_END,
        decision_start=EVALUATION_START,
        mode="evaluation",
    )
    rows = window[BTC]
    assert set(window) == set(STAGE2_INSTRUMENT_IDS)
    assert all(item.status == "warming_up" for item in rows[:168])
    assert all(not item.tradable for item in rows[:168])
    assert rows[168].status == "ready"
    assert rows[168].decision_ts == (
        datetime_to_nanos(EVALUATION_START) + HOUR_NS - MS_NS
    )
    assert rows[168].tradable is True
    assert rows[168].label_available is True
    assert (
        rows[168].label_end_ts == rows[168].decision_ts + LABEL_HORIZON_HOURS * HOUR_NS
    )
    assert all(item.tradable for item in rows[168:])
    ready = tuple(item for item in rows if item.status == "ready")
    require_feature_frame_schema(feature_frame(ready))

    # The formal evaluation context rejects a window without the full 168h of
    # pre-start history.
    with pytest.raises(Stage3DataError, match="168h pre-start context"):
        load_accepted_feature_window(
            config,
            acceptance_record_path=record_path,
            start=EVALUATION_START - timedelta(hours=167),
            end=EVALUATION_END,
            decision_start=EVALUATION_START,
            mode="evaluation",
        )

    # The formal training window uses the same warm-up rule, emits no tradable
    # signal, and purging drops every row whose label crosses the boundary.
    training = load_accepted_feature_window(
        config,
        acceptance_record_path=record_path,
        start=DATASET_START,
        end=EVALUATION_END,
        decision_start=EVALUATION_START,
        mode="training",
    )[BTC]
    assert training[167].status == "warming_up"
    assert all(not item.tradable for item in training)
    training_boundary = DATASET_START + timedelta(hours=176)
    trainable = purge_training_rows(training, evaluation_start=training_boundary)
    assert trainable
    assert all(
        cast(int, item.label_end_ts) < datetime_to_nanos(training_boundary)
        for item in trainable
    )
    excluded = next(
        item
        for item in training
        if item.status == "ready" and item.decision_ts > trainable[-1].decision_ts
    )
    assert cast(int, excluded.label_end_ts) >= datetime_to_nanos(training_boundary)

    # --- non-tautological batch/runtime parity -------------------------------
    # AC4 names both instruments, so each one is compared against its own
    # independent Polars recomputation instead of a BTC-only reference.
    for instrument_id, instrument_code in zip(
        STAGE2_INSTRUMENT_IDS, (0.0, 1.0), strict=True
    ):
        instrument_ready = tuple(
            item for item in window[instrument_id] if item.status == "ready"
        )
        loaded = feature_frame(instrument_ready)
        reference = _reference_features(
            load_bars(
                catalog_path,
                stage2_bar_type_str(instrument_id, "1h"),
                DATASET_START,
                EVALUATION_END + timedelta(hours=LABEL_HORIZON_HOURS),
            ),
            load_mark_prices(
                catalog_path, instrument_id, DATASET_START, EVALUATION_END
            ),
            load_funding(
                catalog_path, instrument_id, DATASET_START, EVALUATION_END
            ).collect(),
            instrument_code=instrument_code,
        ).filter(pl.col("decision_ts") <= loaded["decision_ts"].max())
        combined = loaded.join(reference, on="decision_ts", how="inner", suffix="_ref")
        # Guard against a vacuously green comparison over an empty ready set.
        assert combined.height == loaded.height == len(instrument_ready) > 100
        assert combined["instrument_code"].to_list() == pytest.approx(
            [instrument_code] * combined.height
        )
        for name in FEATURE_NAMES:
            assert combined[name].to_list() == pytest.approx(
                combined[f"{name}_ref"].to_list(), abs=FEATURE_ATOL, rel=FEATURE_RTOL
            )

    # --- sidecar declarations must agree with the tracked acceptance --------
    coverage_file = catalog_path / STAGE2_COVERAGE_FILENAME
    coverage_payload = cast(
        dict[str, object], json.loads(coverage_file.read_text(encoding="utf-8"))
    )
    coverage_series = cast(list[dict[str, object]], coverage_payload["series"])
    coverage_series[0]["row_count"] = cast(int, coverage_series[0]["row_count"]) + 1
    _check_sidecar_rejected(
        coverage_file,
        coverage_payload,
        "coverage conflicts with acceptance",
        config=config,
        record=record_path,
    )

    digest_file = catalog_path / STAGE2_DIGEST_FILENAME
    digest_payload = cast(
        dict[str, object], json.loads(digest_file.read_text(encoding="utf-8"))
    )
    digest_payload["runtime_identity"] = "conflicting-runtime-identity"
    _check_sidecar_rejected(
        digest_file,
        digest_payload,
        "identity conflicts with acceptance",
        config=config,
        record=record_path,
    )

    # The accepted catalog is usable again once the sidecars are restored.
    assert load_accepted_feature_window(
        config,
        acceptance_record_path=record_path,
        start=DATASET_START,
        end=EVALUATION_END,
        decision_start=EVALUATION_START,
        mode="evaluation",
    )

    # --- the rows actually consumed must match the accepted coverage ---------
    # An unapproved mark or funding gap fails closed even though every sidecar
    # still matches the tracked record.
    gapped_path, gapped_record, _, _, gapped_lock = _build_accepted_catalog(
        tmp_path / "gapped", missing_marks=frozenset({900})
    )
    monkeypatch.setattr(stage3, "STAGE2_ARTIFACT_LOCK_SHA256", sha256_file(gapped_lock))
    with pytest.raises(Stage3DataError, match="does not approve"):
        load_accepted_feature_window(
            _accepted_config(
                template,
                gapped_path,
                tmp_path / "gapped",
                artifact_lock_path=gapped_lock,
            ),
            acceptance_record_path=gapped_record,
            start=DATASET_START,
            end=EVALUATION_END,
            decision_start=EVALUATION_START,
            mode="evaluation",
        )

    funding_gap_path, funding_gap_record, _, _, funding_gap_lock = (
        _build_accepted_catalog(
            tmp_path / "funding-gap", missing_funding=frozenset({12})
        )
    )
    monkeypatch.setattr(
        stage3, "STAGE2_ARTIFACT_LOCK_SHA256", sha256_file(funding_gap_lock)
    )
    with pytest.raises(Stage3DataError, match="does not approve"):
        load_accepted_feature_window(
            _accepted_config(
                template,
                funding_gap_path,
                tmp_path / "funding-gap",
                artifact_lock_path=funding_gap_lock,
            ),
            acceptance_record_path=funding_gap_record,
            start=DATASET_START,
            end=EVALUATION_END,
            decision_start=EVALUATION_START,
            mode="evaluation",
        )

    # A truncated tail no longer covers the accepted query window.
    truncated_path, truncated_record, _, _, truncated_lock = _build_accepted_catalog(
        tmp_path / "truncated", truncate_marks=True
    )
    monkeypatch.setattr(
        stage3, "STAGE2_ARTIFACT_LOCK_SHA256", sha256_file(truncated_lock)
    )
    with pytest.raises(Stage3DataError, match="accepted query window"):
        load_accepted_feature_window(
            _accepted_config(
                template,
                truncated_path,
                tmp_path / "truncated",
                artifact_lock_path=truncated_lock,
            ),
            acceptance_record_path=truncated_record,
            start=DATASET_START,
            end=EVALUATION_END,
            decision_start=EVALUATION_START,
            mode="evaluation",
        )

    # The two tracked mark omissions are expected input; a catalog that keeps a
    # record the accepted coverage says was omitted is not.
    included_path, included_record, _, _, included_lock = _build_accepted_catalog(
        tmp_path / "included", include_approved_gap=True
    )
    monkeypatch.setattr(
        stage3, "STAGE2_ARTIFACT_LOCK_SHA256", sha256_file(included_lock)
    )
    with pytest.raises(Stage3DataError, match="coverage omits"):
        load_accepted_feature_window(
            _accepted_config(
                template,
                included_path,
                tmp_path / "included",
                artifact_lock_path=included_lock,
            ),
            acceptance_record_path=included_record,
            start=DATASET_START,
            end=EVALUATION_END,
            decision_start=EVALUATION_START,
            mode="evaluation",
        )

    # Keep every accepted sidecar unchanged, but remove one actual 1h row well
    # after an otherwise valid early evaluation query. Query-local grid checks
    # cannot observe this tail deletion; the immutable artifact binding must.
    bar_file = next(
        (catalog_path / "data" / "bars" / stage2_bar_type_str(BTC, "1h")).glob(
            "*.parquet"
        )
    )
    complete_bars = pl.read_parquet(bar_file)
    early_end = DATASET_START + timedelta(days=10)
    assert cast(int, complete_bars.get_column("ts_event").max()) > datetime_to_nanos(
        early_end + timedelta(hours=LABEL_HORIZON_HOURS)
    )
    complete_bars.head(complete_bars.height - 1).write_parquet(bar_file)
    monkeypatch.setattr(
        stage3, "STAGE2_ARTIFACT_LOCK_SHA256", sha256_file(artifact_lock_path)
    )
    with pytest.raises(Stage3DataError, match="locked Stage 2 artifact"):
        load_accepted_feature_window(
            config,
            acceptance_record_path=record_path,
            start=DATASET_START,
            end=early_end,
            decision_start=EVALUATION_START,
            mode="evaluation",
        )


def _check_sidecar_rejected(
    path: Path,
    payload: dict[str, object],
    match: str,
    *,
    config: Stage3Config,
    record: Path,
) -> None:
    original = path.read_text(encoding="utf-8")
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        with pytest.raises(Stage3DataError, match=match):
            load_accepted_feature_window(
                config,
                acceptance_record_path=record,
                start=DATASET_START,
                end=EVALUATION_END,
                decision_start=EVALUATION_START,
                mode="evaluation",
            )
    finally:
        path.write_text(original, encoding="utf-8")


def test_mark_allowed_gap_events_are_indexed_on_the_close_time_grid() -> None:
    """The locked mark allow-list must be indexed on the accepted event grid.

    ``validate_kline_series`` derives ``ts_event`` from ``close_time``, and a bar
    closes one millisecond before the next one opens, so the open_time endpoints
    of the locked allow-list are not grid timestamps. Indexing them directly
    shifts each approved omission onto the previous row, which the accepted
    catalog presents, and inverts which sequence the coverage check accepts.
    """
    interval_ms = STAGE2_INTERVAL_MS["15m"]
    allowed = mark_allowed_gap_ns()
    assert len(allowed) == len(STAGE2_MARK_ALLOWED_GAPS)
    for (previous_open, next_open), (omitted_ns, following_ns) in zip(
        STAGE2_MARK_ALLOWED_GAPS, allowed, strict=True
    ):
        # Each locked pair omits exactly the one bar opened in between.
        omitted_open_ms = previous_open + interval_ms
        assert next_open - omitted_open_ms == interval_ms
        assert omitted_ns == (omitted_open_ms + interval_ms - 1) * MS_NS
        assert following_ns == (next_open + interval_ms - 1) * MS_NS
        # Both sit exactly on the accepted mark grid, one cadence apart.
        assert (omitted_ns - MARK_ORIGIN_NS) % QUARTER_HOUR_NS == 0
        assert (following_ns - MARK_ORIGIN_NS) % QUARTER_HOUR_NS == 0
        assert following_ns - omitted_ns == QUARTER_HOUR_NS
    omitted_index = (allowed[0][0] - MARK_ORIGIN_NS) // QUARTER_HOUR_NS
    assert omitted_index == APPROVED_MARK_GAP_INDEX
    # The grid point one cadence earlier is a row the accepted catalog presents,
    # so the omission must never be indexed onto it.
    assert (
        allowed[0][0] - MS_NS - MARK_ORIGIN_NS
    ) // QUARTER_HOUR_NS == omitted_index - 1


def test_accepted_funding_is_bound_at_the_producer_schedule_tolerance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Accepted funding is not schedule-aligned and must not be rejected for it.

    Stage 2 writes the source ``calc_time`` as the funding event timestamp and
    accepts any series whose settlements stay inside its own written schedule
    tolerance of the nominal interval, so the consumed series is not on the
    millisecond close_time grid the bar and mark series are derived on. The
    tracked coverage records that shape at its tail, so requiring the close_time
    grid would reject the one accepted catalog.
    """
    within_path, within_record, record, snapshot, artifact_lock_path = (
        _build_accepted_catalog(tmp_path / "funding-offset", funding_offset_ms=3)
    )
    template = _bind_fixture_identity(monkeypatch, record, snapshot, artifact_lock_path)
    window = load_accepted_feature_window(
        _accepted_config(template, within_path, tmp_path / "funding-offset"),
        acceptance_record_path=within_record,
        start=DATASET_START,
        end=EVALUATION_END,
        decision_start=EVALUATION_START,
        mode="evaluation",
    )
    assert set(window) == set(STAGE2_INSTRUMENT_IDS)
    assert any(item.status == "ready" for item in window[BTC])

    # The bound is the producer's written schedule tolerance, not an unbounded
    # exemption: funding further off the accepted grid still fails closed.
    outside_path, outside_record, _, _, outside_lock = _build_accepted_catalog(
        tmp_path / "funding-outside",
        funding_offset_ms=STAGE2_FUNDING_SCHEDULE_TOLERANCE_MS + 1,
    )
    monkeypatch.setattr(
        stage3, "STAGE2_ARTIFACT_LOCK_SHA256", sha256_file(outside_lock)
    )
    with pytest.raises(Stage3DataError, match="accepted coverage grid"):
        load_accepted_feature_window(
            _accepted_config(
                template,
                outside_path,
                tmp_path / "funding-outside",
                artifact_lock_path=outside_lock,
            ),
            acceptance_record_path=outside_record,
            start=DATASET_START,
            end=EVALUATION_END,
            decision_start=EVALUATION_START,
            mode="evaluation",
        )


def test_out_of_window_read_reports_the_stage3_error_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rejected read must not surface the Stage 2 error type.

    ``views`` rejects a query window outside the declared dataset window with
    ``Stage2DataError``. Callers of this module catch ``Stage3DataError``, so an
    escaping Stage 2 type would leave an out-of-window request uncaught even
    though the read does fail closed.
    """
    catalog_path, record_path, record, snapshot, artifact_lock_path = (
        _build_accepted_catalog(tmp_path / "outside-window")
    )
    template = _bind_fixture_identity(monkeypatch, record, snapshot, artifact_lock_path)
    with pytest.raises(Stage3DataError, match="outside the declared window"):
        load_accepted_feature_window(
            _accepted_config(template, catalog_path, tmp_path / "outside-window"),
            acceptance_record_path=record_path,
            start=DATASET_START - timedelta(days=1),
            end=EVALUATION_END,
            decision_start=EVALUATION_START,
            mode="evaluation",
        )


@pytest.mark.parametrize("instrument_id", [BTC, ETH])
@pytest.mark.parametrize("missing_index", [19, 20])
def test_incremental_funding_gap_during_warmup_cannot_become_ready(
    instrument_id: str, missing_index: int
) -> None:
    bars = tuple(_bar(index, instrument_id=instrument_id) for index in range(169))
    marks, funding = _events(len(bars), instrument_id=instrument_id)
    funding = tuple(replace(event, rate="0.0001") for event in funding)
    _, complete = _feed(bars, marks, funding, tradable_from=bars[-1].ts_event)
    assert complete[-1].tradable
    assert complete[-1].require_ready()[FEATURE_NAMES.index("funding_sum_24h")] == (
        pytest.approx(0.0003)
    )
    # The 08:00/16:00 settlement is absent, but the newest 00:00 settlement
    # arrives before the first ready decision. Age checks alone cannot see it.
    incomplete = funding[:missing_index] + funding[missing_index + 1 :]
    assert incomplete[-1].ts_event == funding[-1].ts_event
    with pytest.raises(Stage3DataError, match="funding.*unapproved gap"):
        _feed(bars, marks, incomplete, tradable_from=bars[-1].ts_event)


@pytest.mark.parametrize("rate_dtype", [pl.Boolean, pl.Int64])
def test_funding_source_projection_rejects_rate_dtype_drift(
    rate_dtype: type[pl.DataType] | pl.DataType,
) -> None:
    frame = pl.DataFrame(
        {
            "instrument_id": [BTC],
            "rate": [True if rate_dtype == pl.Boolean else 1],
            "interval": [480],
            "next_funding_ns": [DATASET_START_NS],
            "ts_event": [DATASET_START_NS],
            "ts_init": [DATASET_START_NS],
        },
        schema={
            "instrument_id": pl.String,
            "rate": rate_dtype,
            "interval": pl.UInt64,
            "next_funding_ns": pl.UInt64,
            "ts_event": pl.UInt64,
            "ts_init": pl.UInt64,
        },
    )
    with pytest.raises(Stage3DataError, match="source projection schema or dtype"):
        stage3._funding_from_frame(frame)


def test_incremental_funding_rejects_float_source_projection() -> None:
    state = IncrementalFeatureState(BTC)
    with pytest.raises(Stage3DataError, match="source type must be str or Decimal"):
        state.push_funding(
            FundingProjection(
                instrument_id=BTC,
                rate=cast(str | Decimal, 0.0001),
                ts_event=DATASET_START_NS,
            )
        )


@pytest.mark.parametrize("instrument_id", [BTC, ETH])
def test_incremental_mark_gap_during_warmup_cannot_become_ready(
    instrument_id: str,
) -> None:
    bars = tuple(_bar(index, instrument_id=instrument_id) for index in range(169))
    marks, funding = _events(len(bars), instrument_id=instrument_id)
    missing = 160 * 4 + 1
    incomplete = marks[:missing] + marks[missing + 1 :]
    assert incomplete[-1].ts_event == bars[-1].ts_event
    with pytest.raises(Stage3DataError, match="mark.*unapproved gap"):
        _feed(bars, incomplete, funding, tradable_from=bars[-1].ts_event)


@pytest.mark.parametrize(
    ("series", "boundary"),
    [("funding", "prefix"), ("mark", "prefix"), ("mark", "suffix")],
)
def test_incremental_auxiliary_boundaries_need_more_than_a_fresh_latest_event(
    series: str, boundary: str
) -> None:
    bars = tuple(_bar(index) for index in range(169))
    marks, funding = _events(len(bars))
    if series == "funding":
        # Both remaining settlements are fresh but the 08:00 rate is missing.
        funding = funding[-2:]
    elif boundary == "prefix":
        marks = marks[-1:]
    else:
        # Age == 15m alone cannot approve an unrecorded mark omission.
        marks = marks[:-1]
        assert bars[-1].ts_event - marks[-1].ts_event == MARK_MAX_AGE_NS
    with pytest.raises(
        Stage3DataError, match=f"{series}.*incomplete required coverage"
    ):
        _feed(bars, marks, funding, tradable_from=bars[-1].ts_event)


@pytest.mark.parametrize("instrument_id", [BTC, ETH])
@pytest.mark.parametrize("gap_index", [0, 1])
def test_incremental_mark_allows_only_the_two_accepted_omissions(
    instrument_id: str, gap_index: int
) -> None:
    omitted, following = mark_allowed_gap_ns()[gap_index]
    decision_index = -(-(omitted - BAR_ORIGIN_NS) // HOUR_NS)
    start_index = decision_index - FEATURE_LOOKBACK_HOURS
    bars = tuple(
        _bar(index, instrument_id=instrument_id)
        for index in range(start_index, decision_index + 2)
    )
    marks, funding = _events(
        len(bars), start_index=start_index, instrument_id=instrument_id
    )
    _, rows = _feed(bars, marks, funding, tradable_from=bars[168].ts_event)
    row = rows[168]
    latest = next(
        event for event in reversed(marks) if event.ts_event <= row.decision_ts
    )
    assert row.status == "ready" and row.tradable
    assert row.decision_ts - latest.ts_event == (
        0 if gap_index == 0 else MARK_MAX_AGE_NS
    )
    assert row.require_ready()[FEATURE_NAMES.index("basis_mark_last")] == pytest.approx(
        float(latest.value) / float(bars[168].close) - 1.0
    )
    assert rows[169].status == "ready"  # Also prove continuity after the gap.
    for extra_omission in (omitted - QUARTER_HOUR_NS, following):
        with pytest.raises(Stage3DataError, match="mark.*(gap|coverage|stale)"):
            _feed(
                bars,
                tuple(event for event in marks if event.ts_event != extra_omission),
                funding,
                tradable_from=bars[168].ts_event,
            )


def test_incremental_funding_tolerance_and_slot_drift() -> None:
    bars = tuple(_bar(index) for index in range(169))
    marks, funding = _events(len(bars))
    tolerance = STAGE2_FUNDING_SCHEDULE_TOLERANCE_MS * MS_NS
    offsets = (0, -tolerance, 0, tolerance)
    shifted = tuple(
        replace(event, ts_event=event.ts_event + offsets[index % len(offsets)])
        for index, event in enumerate(funding)
    )
    _, expected = _feed(bars, marks, funding)
    _, actual = _feed(bars, marks, shifted)
    for left, right in zip(expected[168:], actual[168:], strict=True):
        assert left.require_ready() == pytest.approx(
            right.require_ready(), abs=FEATURE_ATOL, rel=FEATURE_RTOL
        )

    state = IncrementalFeatureState(BTC)
    state.push_funding(funding[0])
    with pytest.raises(Stage3DataError, match="duplicate or out-of-order slots"):
        state.push_funding(replace(funding[0], ts_event=funding[0].ts_event + MS_NS))
    with pytest.raises(Stage3DataError, match="accepted coverage grid"):
        state.push_funding(
            replace(funding[1], ts_event=funding[1].ts_event + tolerance + 1)
        )


def test_feature_schema_digest_is_stable_and_sensitive() -> None:
    payload = feature_schema_payload()
    assert FEATURE_SCHEMA_DIGEST == (
        "6170d256a110effad4da4b97d6f22444d8bd8aac446d341610699f013b0e1f84"
    )
    assert feature_schema_digest() == FEATURE_SCHEMA_DIGEST
    # Sensitivity has to be asserted through the digest itself: comparing a
    # mutated copy against the payload it was copied from holds by construction
    # and would keep holding for a digest that ignored the field entirely.
    changed = dict(payload)
    changed["mark_as_of_max_age_ns"] = MARK_MAX_AGE_NS - 1
    assert stage3._canonical_digest(changed) != FEATURE_SCHEMA_DIGEST


def test_feature_schema_serializes_missing_data_rules_and_binds_them_to_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = feature_schema_payload()
    assert json.loads(json.dumps(original)) == original
    policy = cast(dict[str, object], original["missing_data_policy"])
    assert policy["unapproved_gap"] == "error, including during warm-up"
    assert policy["silent_fill_interpolate_forward_fill_or_drop"] == "forbidden"
    assert policy["volume_std_24_zero"] == "error"
    mark = cast(dict[str, object], policy["mark"])
    assert mark["allowed_gap_event_intervals_ns"] == [
        [datetime_to_nanos(parse_utc(start)), datetime_to_nanos(parse_utc(end))]
        for start, end in (
            ("2020-01-19T13:29:59.999Z", "2020-01-19T13:44:59.999Z"),
            ("2023-11-10T03:59:59.999Z", "2023-11-10T04:14:59.999Z"),
        )
    ]
    warm_up = cast(dict[str, object], policy["warm_up"])
    assert warm_up["required_contiguous_decision_bars"] == 169
    assert warm_up["first_tradable_decision_not_ready"] == "error"
    assert warm_up["incomplete_bar_history"] == "warming_up; no values or trading"

    # Exercise the public digest path; no missing-data rule may be excluded.
    for key in policy:
        changed = {**original, "missing_data_policy": {**policy, key: "changed"}}
        monkeypatch.setattr(
            stage3, "feature_schema_payload", lambda payload=changed: payload
        )
        assert feature_schema_digest() != FEATURE_SCHEMA_DIGEST


def test_stage3_typed_config_has_no_implicit_path_or_identity_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    config_path = tmp_path / "stage3.toml"
    config_path.write_text(
        "\n".join(
            (
                f'schema = "{STAGE3_CONFIG_SCHEMA}"',
                f'dataset_id = "{STAGE2_DATASET_ID}"',
                f'acceptance_digest = "{STAGE2_ACCEPTANCE_DIGEST}"',
                f'dataset_digest = "{STAGE2_DATASET_DIGEST}"',
                f'source_manifest_digest = "{STAGE2_SOURCE_MANIFEST_DIGEST}"',
                f'market_data_manifest_digest = "{STAGE2_MARKET_DATA_MANIFEST_DIGEST}"',
                f'instrument_snapshot_checksum = "{STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM}"',
                f'runtime_identity = "{UPSTREAM_RELEASE_IDENTITY}"',
                f'catalog_path = "{catalog}"',
                f'evidence_root = "{tmp_path / "evidence"}"',
                f'run_root = "{tmp_path / "runs"}"',
            )
        ),
        encoding="utf-8",
    )
    loaded = load_stage3_config(config_path, repository_root=REPOSITORY_ROOT)
    assert loaded.catalog_path == catalog
    assert loaded.artifact_lock_path == (
        REPOSITORY_ROOT / stage3.STAGE2_ARTIFACT_LOCK_RELATIVE_PATH
    )

    monkeypatch.delenv("TRACEQUANT_STAGE3_CONFIG", raising=False)
    with pytest.raises(Stage3DataError, match="must point"):
        load_stage3_config_from_env(repository_root=REPOSITORY_ROOT)
    monkeypatch.setenv("TRACEQUANT_STAGE3_CONFIG", str(config_path))
    assert load_stage3_config_from_env(repository_root=REPOSITORY_ROOT) == loaded

    relative = config_path.read_text(encoding="utf-8").replace(
        f'catalog_path = "{catalog}"', 'catalog_path = "catalog"'
    )
    config_path.write_text(relative, encoding="utf-8")
    with pytest.raises(Stage3DataError, match="absolute external"):
        load_stage3_config(config_path, repository_root=REPOSITORY_ROOT)


def test_typed_config_rejects_output_roots_overlapping_the_catalog(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    lines = (
        f'schema = "{STAGE3_CONFIG_SCHEMA}"',
        f'dataset_id = "{STAGE2_DATASET_ID}"',
        f'acceptance_digest = "{STAGE2_ACCEPTANCE_DIGEST}"',
        f'dataset_digest = "{STAGE2_DATASET_DIGEST}"',
        f'source_manifest_digest = "{STAGE2_SOURCE_MANIFEST_DIGEST}"',
        f'market_data_manifest_digest = "{STAGE2_MARKET_DATA_MANIFEST_DIGEST}"',
        f'instrument_snapshot_checksum = "{STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM}"',
        f'runtime_identity = "{UPSTREAM_RELEASE_IDENTITY}"',
        f'catalog_path = "{catalog}"',
        f'evidence_root = "{tmp_path / "evidence"}"',
        f'run_root = "{tmp_path / "runs"}"',
    )
    config_path = tmp_path / "stage3.toml"

    # A derived output root nested under the catalog would write derived
    # feature/label data back into the sole Nautilus catalog.
    for key, value in (
        ("run_root", catalog / "stage3-run"),
        ("evidence_root", catalog / "stage3-evidence"),
        ("run_root", tmp_path),
    ):
        config_path.write_text(
            "\n".join(
                f'{key} = "{value}"' if line.startswith(f"{key} = ") else line
                for line in lines
            ),
            encoding="utf-8",
        )
        with pytest.raises(Stage3DataError, match="must not overlap"):
            load_stage3_config(config_path, repository_root=REPOSITORY_ROOT)

    # A catalog nested under an output root overlaps just as much.
    nested = tmp_path / "runs" / "catalog"
    nested.mkdir(parents=True)
    config_path.write_text(
        "\n".join(
            f'catalog_path = "{nested}"' if line.startswith("catalog_path = ") else line
            for line in lines
        ),
        encoding="utf-8",
    )
    with pytest.raises(Stage3DataError, match="must not overlap"):
        load_stage3_config(config_path, repository_root=REPOSITORY_ROOT)


def _typed_config_values(catalog: Path, evidence: Path, run: Path) -> dict[str, str]:
    return {
        "schema": STAGE3_CONFIG_SCHEMA,
        "dataset_id": STAGE2_DATASET_ID,
        "acceptance_digest": STAGE2_ACCEPTANCE_DIGEST,
        "dataset_digest": STAGE2_DATASET_DIGEST,
        "source_manifest_digest": STAGE2_SOURCE_MANIFEST_DIGEST,
        "market_data_manifest_digest": STAGE2_MARKET_DATA_MANIFEST_DIGEST,
        "instrument_snapshot_checksum": STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM,
        "runtime_identity": UPSTREAM_RELEASE_IDENTITY,
        "catalog_path": str(catalog),
        "evidence_root": str(evidence),
        "run_root": str(run),
    }


def test_typed_config_fails_closed_on_every_enumerated_ac8_branch(
    tmp_path: Path,
) -> None:
    """AC8 names these fail-closed branches; each one is asserted, not assumed."""
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    evidence = tmp_path / "evidence"
    run = tmp_path / "runs"
    base = _typed_config_values(catalog, evidence, run)
    config_path = tmp_path / "stage3.toml"

    def write(values: Mapping[str, str]) -> Path:
        config_path.write_text(
            "\n".join(f'{key} = "{value}"' for key, value in values.items()),
            encoding="utf-8",
        )
        return config_path

    def rejected(values: Mapping[str, str], match: str) -> None:
        with pytest.raises(Stage3DataError, match=match):
            load_stage3_config(write(values), repository_root=REPOSITORY_ROOT)

    # The baseline is accepted, so each rejection below is attributable to its
    # own override rather than to a malformed fixture.
    baseline = load_stage3_config(write(base), repository_root=REPOSITORY_ROOT)
    assert baseline.catalog_path == catalog
    assert baseline.evidence_root == evidence

    # Unknown and missing fields are both schema drift.
    rejected({**base, "unexpected_key": "x"}, "fields do not match the schema")
    rejected(
        {key: value for key, value in base.items() if key != "run_root"},
        "fields do not match the schema",
    )

    # An absolute path is still refused inside the repository, and an unpinned
    # `latest` alias is refused even outside it.
    rejected(
        {**base, "run_root": str(REPOSITORY_ROOT / "stage3-runs")},
        "must not be inside the repository",
    )
    rejected(
        {**base, "run_root": str(tmp_path / "latest" / "runs")},
        "must not use a latest alias",
    )

    # A config value that conflicts with the locked accepted identity fails.
    rejected(
        {**base, "instrument_snapshot_checksum": "0" * 64},
        "conflicts with the locked identity",
    )

    # An existing output partition must carry the exact locked identity. An empty
    # directory is not a partition, so it must hold a row to reach the check.
    evidence.mkdir()
    (evidence / "stale-output.parquet").write_bytes(b"")
    rejected(base, "has no locked identity")
    (evidence / "stage3_partition_identity.json").write_text(
        json.dumps(
            {
                "acceptance_digest": STAGE2_ACCEPTANCE_DIGEST,
                "dataset_id": STAGE2_DATASET_ID,
                "runtime_identity": "conflicting-runtime-identity",
            }
        ),
        encoding="utf-8",
    )
    rejected(base, "identity does not match")


def test_incremental_feature_state_stays_bounded_by_the_lookbacks() -> None:
    hours = 24 * 30
    bars = tuple(_bar(index) for index in range(hours + 169))
    marks, funding = _events(len(bars))

    live, live_rows = _feed(bars, marks, funding)
    retained_marks, retained_funding = live.retained_event_counts
    # 233,758 accepted mark rows must not accumulate in live Strategy state.
    assert retained_marks == 1
    assert retained_funding <= 4

    # The bounded state answers exactly what a memoryless recomputation over the
    # minimal trailing window answers.
    tail = bars[-(FEATURE_LOOKBACK_HOURS + 1) :]
    tail_start = tail[0].ts_event
    _, tail_rows = _feed(
        tail,
        tuple(item for item in marks if item.ts_event >= tail_start),
        tuple(item for item in funding if item.ts_event >= tail_start - 24 * HOUR_NS),
    )
    assert live_rows[-1].status == tail_rows[-1].status == "ready"
    assert live_rows[-1].require_ready() == pytest.approx(
        tail_rows[-1].require_ready(), abs=FEATURE_ATOL, rel=FEATURE_RTOL
    )


def test_label_boundary_uses_four_distinct_future_bars() -> None:
    bars = tuple(_bar(index) for index in range(173))
    marks, funding = _events(len(bars))
    rows = build_feature_rows(bars, marks, funding)
    assert rows[168].label_available is True
    assert rows[169].label_available is False
    broken = list(bars)
    del broken[170]
    with pytest.raises(Stage3DataError, match="gap"):
        build_feature_rows(tuple(broken), marks, funding)


def test_zero_volume_bars_are_accepted_input_not_malformed() -> None:
    """The Task enumerates NaN/Inf/duplicate/order/missing; zero volume is legal."""
    bars = [_bar(index) for index in range(173)]
    flat = bars[100]
    bars[100] = BarProjection(
        instrument_id=flat.instrument_id,
        open=flat.open,
        high=flat.high,
        low=flat.low,
        close=flat.close,
        volume="0.00000000",
        ts_event=flat.ts_event,
    )
    marks, funding = _events(len(bars))
    assert build_feature_rows(tuple(bars), marks, funding)[168].status == "ready"

    # A negative volume remains a malformed projection.
    negative = list(bars)
    negative[100] = BarProjection(
        instrument_id=flat.instrument_id,
        open=flat.open,
        high=flat.high,
        low=flat.low,
        close=flat.close,
        volume="-1",
        ts_event=flat.ts_event,
    )
    with pytest.raises(Stage3DataError, match="must not be negative"):
        build_feature_rows(tuple(negative), marks, funding)


def test_timestamps_are_integer_nanoseconds() -> None:
    invalid = _bar(0)
    invalid = BarProjection(
        instrument_id=invalid.instrument_id,
        open=invalid.open,
        high=invalid.high,
        low=invalid.low,
        close=invalid.close,
        volume=invalid.volume,
        ts_event=True,
    )
    marks, funding = _events(1)
    with pytest.raises(Stage3DataError, match="timestamp"):
        build_feature_rows((invalid,), marks, funding)


def test_evaluation_context_duration_is_exactly_168_hours() -> None:
    start = parse_utc("2021-12-25T00:00:00Z")
    evaluation = parse_utc("2022-01-01T00:00:00Z")
    assert evaluation - start == timedelta(hours=168)
