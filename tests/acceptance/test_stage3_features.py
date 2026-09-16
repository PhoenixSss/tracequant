from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
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
    STAGE2_FUNDING_STREAM_ID,
    STAGE2_INSTRUMENT_IDS,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    STAGE2_MANIFEST_FILENAME,
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
# 2020-01-19T13:14:59.999Z is the first of the two mark intervals the accepted
# coverage records as an explained omission.
APPROVED_MARK_GAP_INDEX = (
    mark_allowed_gap_ns()[0][0] - MS_NS - MARK_ORIGIN_NS
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
) -> tuple[tuple[MarkProjection, ...], tuple[FundingProjection, ...]]:
    marks = tuple(
        MarkProjection(
            instrument_id=BTC,
            value=f"{100.1 + index * 0.2:.8f}",
            ts_event=_bar(index).ts_event,
        )
        for index in range(count)
    )
    funding = tuple(
        FundingProjection(
            instrument_id=BTC,
            rate=f"{0.0001 + index * 0.000001:.8f}",
            ts_event=_bar(index).ts_event,
        )
        for index in range(0, count, 8)
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
) -> tuple[Path, Path, dict[str, object], dict[str, object]]:
    """Write a deterministic Stage 2-shaped accepted catalog under ``root``.

    The catalog is small, but every identity surface it carries is derived the
    way the accepted Stage 2 pipeline derives it: the coverage report is rebuilt
    from the tracked acceptance record, the digest record comes from the
    production payload builder, and the series sit on the accepted grids. Only
    the source-object identities are fixture-local, because the accepted
    800-object manifest lives outside this repository.
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
                    for ts_event in _grid(
                        DATASET_START_NS,
                        FUNDING_INTERVAL_NS,
                        decision_end_ns,
                        missing_funding,
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
    return catalog_path, record_path, record, snapshot


def _bind_fixture_identity(
    monkeypatch: pytest.MonkeyPatch,
    record: dict[str, object],
    snapshot: dict[str, object],
) -> Stage3Config:
    """Rebind the Stage 3 locked identity to the fixture-derived accepted record.

    The production constants pin the one accepted 2020-2026 dataset whose
    800-object manifest lives outside this repository, so a repository-local
    fixture cannot reproduce them byte for byte. Rebinding the five pinned
    values to this mechanically derived equivalent lets the formal loader be
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
    return Stage3Config(
        schema=STAGE3_CONFIG_SCHEMA,
        dataset_id=STAGE2_DATASET_ID,
        acceptance_digest=cast(str, record["acceptance_digest"]),
        dataset_digest=cast(str, record["dataset_digest"]),
        source_manifest_digest=cast(str, record["source_manifest_digest"]),
        market_data_manifest_digest=cast(str, record["market_data_manifest_digest"]),
        instrument_snapshot_checksum=cast(str, snapshot["checksum_sha256"]),
        runtime_identity=UPSTREAM_RELEASE_IDENTITY,
        catalog_path=Path("/fixture-catalog"),
        evidence_root=Path("/fixture-evidence"),
        run_root=Path("/fixture-runs"),
    )


def _accepted_config(
    template: Stage3Config, catalog_path: Path, tmp_path: Path
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

    # The approved mark-gap edge is inclusive at exactly 15 minutes; older is stale.
    boundary_state = IncrementalFeatureState(BTC)
    for event in funding:
        if event.ts_event <= bars[168].ts_event:
            boundary_state.push_funding(event)
    boundary_state.push_mark(
        MarkProjection(BTC, "133.7", bars[168].ts_event - MARK_MAX_AGE_NS)
    )
    boundary_row = None
    for bar in bars[:169]:
        boundary_row = boundary_state.push_bar(bar)
    assert boundary_row is not None and boundary_row.status == "ready"

    stale_state = IncrementalFeatureState(BTC)
    stale_state.push_funding(FundingProjection(BTC, "0.0001", bars[168].ts_event))
    stale_state.push_mark(
        MarkProjection(BTC, "133.7", bars[168].ts_event - MARK_MAX_AGE_NS - 1)
    )
    with pytest.raises(Stage3DataError, match="mark is stale"):
        for bar in bars[:169]:
            stale_state.push_bar(bar)

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
    catalog_path, record_path, record, snapshot = _build_accepted_catalog(tmp_path)
    template = _bind_fixture_identity(monkeypatch, record, snapshot)
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
    loaded = feature_frame(ready)
    reference = _reference_features(
        load_bars(
            catalog_path,
            stage2_bar_type_str(BTC, "1h"),
            DATASET_START,
            EVALUATION_END + timedelta(hours=LABEL_HORIZON_HOURS),
        ),
        load_mark_prices(catalog_path, BTC, DATASET_START, EVALUATION_END),
        load_funding(catalog_path, BTC, DATASET_START, EVALUATION_END).collect(),
        instrument_code=0.0,
    ).filter(pl.col("decision_ts") <= loaded["decision_ts"].max())
    combined = loaded.join(reference, on="decision_ts", how="inner", suffix="_ref")
    # Guard against a vacuously green comparison over an empty ready set.
    assert combined.height == loaded.height == len(ready) > 100
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
    gapped_path, gapped_record, _, _ = _build_accepted_catalog(
        tmp_path / "gapped", missing_marks=frozenset({900})
    )
    with pytest.raises(Stage3DataError, match="does not approve"):
        load_accepted_feature_window(
            _accepted_config(template, gapped_path, tmp_path / "gapped"),
            acceptance_record_path=gapped_record,
            start=DATASET_START,
            end=EVALUATION_END,
            decision_start=EVALUATION_START,
            mode="evaluation",
        )

    funding_gap_path, funding_gap_record, _, _ = _build_accepted_catalog(
        tmp_path / "funding-gap", missing_funding=frozenset({12})
    )
    with pytest.raises(Stage3DataError, match="does not approve"):
        load_accepted_feature_window(
            _accepted_config(template, funding_gap_path, tmp_path / "funding-gap"),
            acceptance_record_path=funding_gap_record,
            start=DATASET_START,
            end=EVALUATION_END,
            decision_start=EVALUATION_START,
            mode="evaluation",
        )

    # A truncated tail no longer covers the accepted query window.
    truncated_path, truncated_record, _, _ = _build_accepted_catalog(
        tmp_path / "truncated", truncate_marks=True
    )
    with pytest.raises(Stage3DataError, match="accepted query window"):
        load_accepted_feature_window(
            _accepted_config(template, truncated_path, tmp_path / "truncated"),
            acceptance_record_path=truncated_record,
            start=DATASET_START,
            end=EVALUATION_END,
            decision_start=EVALUATION_START,
            mode="evaluation",
        )

    # The two tracked mark omissions are expected input; a catalog that keeps a
    # record the accepted coverage says was omitted is not.
    included_path, included_record, _, _ = _build_accepted_catalog(
        tmp_path / "included", include_approved_gap=True
    )
    with pytest.raises(Stage3DataError, match="coverage omits"):
        load_accepted_feature_window(
            _accepted_config(template, included_path, tmp_path / "included"),
            acceptance_record_path=included_record,
            start=DATASET_START,
            end=EVALUATION_END,
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


def test_feature_schema_digest_is_stable_and_sensitive() -> None:
    payload = feature_schema_payload()
    assert FEATURE_SCHEMA_DIGEST == (
        "ee2416993244e3f56622da41bcebe47980fc1b959013c22222a806b664eb359e"
    )
    assert feature_schema_digest() == FEATURE_SCHEMA_DIGEST
    changed = dict(payload)
    changed["mark_as_of_max_age_ns"] = MARK_MAX_AGE_NS - 1
    assert changed != payload


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


def test_incremental_feature_state_stays_bounded_by_the_lookbacks() -> None:
    hours = 24 * 30
    bars = tuple(_bar(index) for index in range(hours + 169))
    marks = tuple(
        MarkProjection(BTC, f"{100.1 + index * 0.001:.8f}", _bar(index).ts_event)
        for index in range(len(bars))
    )
    funding = tuple(
        FundingProjection(BTC, f"{0.0001 + index * 0.000001:.8f}", _bar(index).ts_event)
        for index in range(0, len(bars), 8)
    )

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
