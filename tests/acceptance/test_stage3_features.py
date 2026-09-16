from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from tracequant.integrations.nautilus import UPSTREAM_RELEASE_IDENTITY
from tracequant.research.stage3_features import (
    FEATURE_ATOL,
    FEATURE_NAMES,
    FEATURE_RTOL,
    FEATURE_SCHEMA_DIGEST,
    HOUR_NS,
    MARK_MAX_AGE_NS,
    STAGE2_ACCEPTANCE_DIGEST,
    STAGE2_DATASET_DIGEST,
    STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM,
    STAGE2_MARKET_DATA_MANIFEST_DIGEST,
    STAGE2_SOURCE_MANIFEST_DIGEST,
    STAGE3_CONFIG_SCHEMA,
    BarProjection,
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
    load_stage3_config,
    load_stage3_config_from_env,
    purge_training_rows,
    require_feature_frame_schema,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_DATASET_ID,
    STAGE2_WINDOW_START_ISO,
    datetime_to_nanos,
    parse_utc,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_RECORD = (
    REPOSITORY_ROOT / "docs/product/stage2-btceth-dataset-acceptance.json"
)
BTC = "BTCUSDT-PERP.BINANCE"
ETH = "ETHUSDT-PERP.BINANCE"


def _bar(index: int, *, instrument_id: str = BTC) -> BarProjection:
    close = 100.0 + index * 0.2 + (index % 5) * 0.01
    return BarProjection(
        instrument_id=instrument_id,
        open=f"{close - 0.05:.8f}",
        high=f"{close + 0.20:.8f}",
        low=f"{close - 0.20:.8f}",
        close=f"{close:.8f}",
        volume=f"{10.0 + (index % 17):.8f}",
        ts_event=_first_close_ns() + index * HOUR_NS,
    )


def _first_close_ns() -> int:
    start = datetime_to_nanos(parse_utc(STAGE2_WINDOW_START_ISO))
    return start + HOUR_NS - 1_000_000


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


def test_stage3_features_are_bound_to_accepted_catalog_and_causal(
    tmp_path: Path,
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

    # The native-event state uses the same finite formula and produces identical rows.
    state = IncrementalFeatureState(BTC)
    mark_index = 0
    funding_index = 0
    incremental = []
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
        incremental.append(
            state.push_bar(bar, tradable=bar.ts_event >= evaluation_start)
        )
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
