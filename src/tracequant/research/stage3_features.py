from __future__ import annotations

import hashlib
import json
import math
import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, Literal, cast

import polars as pl

from tracequant.integrations.nautilus import UPSTREAM_RELEASE_IDENTITY
from tracequant.integrations.nautilus.stage2_btceth import (
    require_stage2_catalog_identity,
)
from tracequant.research.views import load_bars, load_funding, load_mark_prices
from tracequant.source_data.stage2_btceth import (
    STAGE2_ACCEPTANCE_SCHEMA,
    STAGE2_COVERAGE_FILENAME,
    STAGE2_DATA_TYPE_BARS,
    STAGE2_DATA_TYPE_FUNDING,
    STAGE2_DATA_TYPE_MARK,
    STAGE2_DATASET_ID,
    STAGE2_DIGEST_FILENAME,
    STAGE2_FUNDING_SCHEDULE_TOLERANCE_MS,
    STAGE2_INSTRUMENT_IDS,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
    STAGE2_INTERVAL_MS,
    STAGE2_MANIFEST_FILENAME,
    STAGE2_MARK_ALLOWED_GAPS,
    STAGE2_NAUTILUS_VERSION,
    STAGE2_SOURCE_SCHEMA,
    STAGE2_WINDOW_END_ISO,
    STAGE2_WINDOW_START_ISO,
    Stage2DataError,
    datetime_to_nanos,
    parse_utc,
    require_complete_acceptance_record,
    require_utc,
    stage2_bar_type_str,
)

STAGE3_CONFIG_ENV: Final = "TRACEQUANT_STAGE3_CONFIG"
STAGE3_CONFIG_SCHEMA: Final = "tracequant-stage3-features-v1"
STAGE3_ACCEPTANCE_FILENAME: Final = "stage2-btceth-dataset-acceptance.json"

STAGE2_ACCEPTANCE_DIGEST: Final = (
    "5909c878a81f0cdea85a8b8f86efd36d9c9bad5b3f3fb4c0f9960bb0551609cd"
)
STAGE2_DATASET_DIGEST: Final = (
    "e17c6294e0a0e6714e56a44624ade37cff46125c46d8b0ee81ede6093711579c"
)
STAGE2_SOURCE_MANIFEST_DIGEST: Final = (
    "de86d44c73117e17af2bbcb655cf1c8d4290043fa8854636cc0e4e592a1dc790"
)
STAGE2_MARKET_DATA_MANIFEST_DIGEST: Final = (
    "a0d9a36bb65ec7c2ec41f47cdf6ba7d20f57ad28d8d3494a69624c60d6d0a110"
)
STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM: Final = (
    "dd7fab59448a3b530ab70871ec57c375f6758e409004f9673d9d0cac7ee630bd"
)

HOUR_NS: Final = 60 * 60 * 1_000_000_000
MARK_MAX_AGE_NS: Final = 15 * 60 * 1_000_000_000
FUNDING_MAX_AGE_NS: Final = 8 * HOUR_NS
FEATURE_LOOKBACK_HOURS: Final = 168
LABEL_HORIZON_HOURS: Final = 4
FUNDING_WINDOW_HOURS: Final = 24
FEATURE_ATOL: Final = 1e-12
FEATURE_RTOL: Final = 1e-9

MS_NS: Final = 1_000_000
# Accepted bar and mark series are derived from ``close_time``, which Stage 2
# quantizes to the millisecond, so they sit at most one millisecond away from
# their declared grid.
SERIES_GRID_TOLERANCE_NS: Final = MS_NS
# Accepted funding is the one series Stage 2 does not derive from ``close_time``:
# the producer writes the source ``calc_time`` as the event timestamp unchanged
# and accepts any series whose settlements stay inside its own written schedule
# tolerance of the nominal interval. A consumed funding event may therefore
# legitimately sit far outside the millisecond close_time grid, and the consumer
# must not be stricter than the producer that accepted the data. Funding is bound
# to the accepted grid at the producer's written tolerance instead.
FUNDING_GRID_TOLERANCE_NS: Final = STAGE2_FUNDING_SCHEDULE_TOLERANCE_MS * MS_NS
MARK_CADENCE_NS: Final = STAGE2_INTERVAL_MS["15m"] * MS_NS
FUNDING_CADENCE_NS: Final = 8 * HOUR_NS
# An accepted bar closes one millisecond before the next bar opens, so the event
# timestamp an accepted series is gridded on is its close_time and the open_time
# an allow-list is written in is not itself a grid timestamp.
STAGE2_CLOSE_OFFSET_MS: Final = 1

FEATURE_NAMES: Final = (
    "ret_1h",
    "ret_4h",
    "ret_24h",
    "ret_168h",
    "range_1h",
    "rv_24h",
    "rv_168h",
    "volume_z_24h",
    "basis_mark_last",
    "funding_latest",
    "funding_sum_24h",
    "hour_sin",
    "hour_cos",
    "instrument_code",
)


class Stage3DataError(ValueError):
    """Raised when trusted-input or causal feature contracts are violated."""


FEATURE_SCHEMA: Final = (
    {
        "name": "ret_1h",
        "dtype": "Float64",
        "lookback": "1h",
        "as_of": "closed bar <= decision_ts",
        "expression": "C(t)/C(t-1h)-1",
    },
    {
        "name": "ret_4h",
        "dtype": "Float64",
        "lookback": "4h",
        "as_of": "closed bar <= decision_ts",
        "expression": "C(t)/C(t-4h)-1",
    },
    {
        "name": "ret_24h",
        "dtype": "Float64",
        "lookback": "24h",
        "as_of": "closed bar <= decision_ts",
        "expression": "C(t)/C(t-24h)-1",
    },
    {
        "name": "ret_168h",
        "dtype": "Float64",
        "lookback": "168h",
        "as_of": "closed bar <= decision_ts",
        "expression": "C(t)/C(t-168h)-1",
    },
    {
        "name": "range_1h",
        "dtype": "Float64",
        "lookback": "1h",
        "as_of": "closed bar <= decision_ts",
        "expression": "(H(t)-L(t))/C(t)",
    },
    {
        "name": "rv_24h",
        "dtype": "Float64",
        "lookback": "24 log returns",
        "as_of": "closed bar <= decision_ts",
        "expression": "sqrt(sum(log_return^2))",
    },
    {
        "name": "rv_168h",
        "dtype": "Float64",
        "lookback": "168 log returns",
        "as_of": "closed bar <= decision_ts",
        "expression": "sqrt(sum(log_return^2))",
    },
    {
        "name": "volume_z_24h",
        "dtype": "Float64",
        "lookback": "24 bars including t",
        "as_of": "closed bar <= decision_ts",
        "expression": "(V(t)-mean(V,24))/std_pop(V,24)",
    },
    {
        "name": "basis_mark_last",
        "dtype": "Float64",
        "lookback": "latest mark <=15m old",
        "as_of": "same-instrument mark <= decision_ts",
        "expression": "M(t)/C(t)-1",
    },
    {
        "name": "funding_latest",
        "dtype": "Float64",
        "lookback": "latest funding <=8h old",
        "as_of": "same-instrument funding <= decision_ts",
        "expression": "latest funding rate",
    },
    {
        "name": "funding_sum_24h",
        "dtype": "Float64",
        "lookback": "24h",
        "as_of": "same-instrument t-24h < event <= t",
        "expression": "sum(funding rate)",
    },
    {
        "name": "hour_sin",
        "dtype": "Float64",
        "lookback": "decision hour",
        "as_of": "decision_ts",
        "expression": "sin(2*pi*hour/24)",
    },
    {
        "name": "hour_cos",
        "dtype": "Float64",
        "lookback": "decision hour",
        "as_of": "decision_ts",
        "expression": "cos(2*pi*hour/24)",
    },
    {
        "name": "instrument_code",
        "dtype": "Float64",
        "lookback": "fixed",
        "as_of": "instrument identity",
        "expression": "BTC=0.0;ETH=1.0",
    },
)


def feature_schema_payload() -> dict[str, object]:
    return {
        "schema": STAGE3_CONFIG_SCHEMA,
        "features": [dict(item) for item in FEATURE_SCHEMA],
        "mark_as_of_max_age_ns": MARK_MAX_AGE_NS,
        "funding_as_of_max_age_ns": FUNDING_MAX_AGE_NS,
        "missing_data_policy": {
            "unapproved_gap": "error, including during warm-up",
            "silent_fill_interpolate_forward_fill_or_drop": "forbidden",
            "duplicate_or_out_of_order": "error, including repeated grid slots",
            "mark": {
                "cadence_ns": MARK_CADENCE_NS,
                "grid_tolerance_ns": SERIES_GRID_TOLERANCE_NS,
                "allowed_gap_event_intervals_ns": [
                    list(interval) for interval in mark_allowed_gap_ns()
                ],
                "required_coverage": "first retained decision bar through decision_ts",
                "allowed_gap_values": "absent; use causal as-of within age limit",
            },
            "funding": {
                "cadence_ns": FUNDING_CADENCE_NS,
                "grid_tolerance_ns": FUNDING_GRID_TOLERANCE_NS,
                "allowed_gaps": [],
                "required_coverage": "all events in (decision_ts-24h, decision_ts]",
                "boundary_tolerance": (
                    "require earliest possibly included slot and every definitely due "
                    "slot; sum only actual event timestamps in the window"
                ),
            },
            "grid_anchor": {
                "dataset_start": STAGE2_WINDOW_START_ISO,
                "dataset_end_exclusive": STAGE2_WINDOW_END_ISO,
                "mark_first_event_offset_ns": MARK_CADENCE_NS - MS_NS,
                "funding_first_event_offset_ns": 0,
            },
            "warm_up": {
                "required_contiguous_decision_bars": FEATURE_LOOKBACK_HOURS + 1,
                "incomplete_bar_history": "warming_up; no values or trading",
                "training_header": "context only; exclude from training rows",
                "first_tradable_decision_not_ready": "error",
                "ready_with_incomplete_auxiliary_coverage": "error",
            },
            "future_stale_or_wrong_instrument": "error",
            "non_finite_input_or_output": "error",
            "volume_std_24_zero": "error",
        },
        "parity_atol": FEATURE_ATOL,
        "parity_rtol": FEATURE_RTOL,
    }


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


FEATURE_SCHEMA_DIGEST: Final = (
    "6170d256a110effad4da4b97d6f22444d8bd8aac446d341610699f013b0e1f84"
)


def feature_schema_digest() -> str:
    return _canonical_digest(feature_schema_payload())


def mark_allowed_gap_ns() -> tuple[tuple[int, int], ...]:
    """The two accepted mark omissions on the nanosecond decision timeline.

    The accepted mark series is the only series allowed to omit intervals, and
    only the omissions recorded by the locked Stage 2 allow-list. That allow-list
    is written in millisecond ``(previous_open, next_open)`` open_time pairs, so
    each omission is the 15m bar opening at ``previous_open + 15m``. Accepted
    series are gridded on event timestamps, which are close_time derived, so the
    omitted bar's timestamp is its close (``next_open - 1ms``) and the bar that
    follows it lands one cadence later. Both returned timestamps therefore sit
    exactly on the accepted grid, one cadence apart; the open_time endpoints are
    not grid timestamps and must not be indexed as if they were.
    """
    interval_ms = STAGE2_INTERVAL_MS["15m"]
    return tuple(
        (
            (previous_open + 2 * interval_ms - STAGE2_CLOSE_OFFSET_MS) * MS_NS,
            (next_open + interval_ms - STAGE2_CLOSE_OFFSET_MS) * MS_NS,
        )
        for previous_open, next_open in STAGE2_MARK_ALLOWED_GAPS
    )


@dataclass(frozen=True)
class Stage3Config:
    schema: str
    dataset_id: str
    acceptance_digest: str
    dataset_digest: str
    source_manifest_digest: str
    market_data_manifest_digest: str
    instrument_snapshot_checksum: str
    runtime_identity: str
    catalog_path: Path
    evidence_root: Path
    run_root: Path


@dataclass(frozen=True)
class AcceptedStage2Catalog:
    catalog_path: Path
    dataset_id: str
    acceptance_digest: str
    dataset_digest: str
    source_manifest_digest: str
    market_data_manifest_digest: str
    instrument_snapshot_checksum: str
    runtime_identity: str
    coverage: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class AcceptedSeriesCoverage:
    """One accepted Stage 2 series reduced to the grid it must be consumed on."""

    description: str
    first_ts_event: int
    cadence_ns: int
    grid_tolerance_ns: int
    last_index: int
    allowed_gap_indices: frozenset[int]

    def grid_index(self, ts_event: int) -> int:
        offset = ts_event - self.first_ts_event
        index = (2 * offset + self.cadence_ns) // (2 * self.cadence_ns)
        if (
            abs(offset - index * self.cadence_ns) > self.grid_tolerance_ns
            or not 0 <= index <= self.last_index
        ):
            raise Stage3DataError(
                f"{self.description} is not on the accepted coverage grid"
            )
        return index

    def first_eligible_index(self, window_start_ns: int) -> int:
        offset = window_start_ns - self.first_ts_event
        index = max(0, -(-offset // self.cadence_ns))
        while index in self.allowed_gap_indices and index <= self.last_index:
            index += 1
        return index

    def last_eligible_index(self, window_end_ns: int) -> int:
        offset = window_end_ns - 1 - self.first_ts_event
        index = min(self.last_index, offset // self.cadence_ns)
        while index in self.allowed_gap_indices and index >= 0:
            index -= 1
        return index


@dataclass(frozen=True)
class BarProjection:
    instrument_id: str
    open: str | Decimal
    high: str | Decimal
    low: str | Decimal
    close: str | Decimal
    volume: str | Decimal
    ts_event: int


@dataclass(frozen=True)
class MarkProjection:
    instrument_id: str
    value: str | Decimal
    ts_event: int


@dataclass(frozen=True)
class FundingProjection:
    instrument_id: str
    rate: str | Decimal
    ts_event: int


@dataclass(frozen=True)
class FeatureObservation:
    instrument_id: str
    decision_ts: int
    status: Literal["warming_up", "ready"]
    values: tuple[float, ...] | None
    feature_schema_digest: str
    tradable: bool
    label_available: bool = False
    label_log_return_4h: float | None = None
    label_end_ts: int | None = None

    def require_ready(self) -> tuple[float, ...]:
        if self.status != "ready" or self.values is None:
            raise Stage3DataError("feature row is still warming up")
        return self.values


_CONFIG_KEYS: Final = {
    "schema",
    "dataset_id",
    "acceptance_digest",
    "dataset_digest",
    "source_manifest_digest",
    "market_data_manifest_digest",
    "instrument_snapshot_checksum",
    "runtime_identity",
    "catalog_path",
    "evidence_root",
    "run_root",
}


def load_stage3_config(path: Path, *, repository_root: Path) -> Stage3Config:
    config_path = Path(path)
    if not config_path.is_absolute() or not config_path.is_file():
        raise Stage3DataError("stage 3 config must be an existing absolute file")
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise Stage3DataError("stage 3 config is not valid TOML") from exc
    if set(payload) != _CONFIG_KEYS:
        raise Stage3DataError("stage 3 config fields do not match the schema")
    config = Stage3Config(
        schema=_required_string(payload, "schema"),
        dataset_id=_required_string(payload, "dataset_id"),
        acceptance_digest=_required_string(payload, "acceptance_digest"),
        dataset_digest=_required_string(payload, "dataset_digest"),
        source_manifest_digest=_required_string(payload, "source_manifest_digest"),
        market_data_manifest_digest=_required_string(
            payload, "market_data_manifest_digest"
        ),
        instrument_snapshot_checksum=_required_string(
            payload, "instrument_snapshot_checksum"
        ),
        runtime_identity=_required_string(payload, "runtime_identity"),
        catalog_path=_external_path(payload, "catalog_path", repository_root),
        evidence_root=_external_path(payload, "evidence_root", repository_root),
        run_root=_external_path(payload, "run_root", repository_root),
    )
    _require_locked_config(config)
    if not config.catalog_path.is_dir():
        raise Stage3DataError("catalog_path must be an existing directory")
    _require_disjoint_output_roots(config)
    _require_matching_partition(config.evidence_root, config)
    _require_matching_partition(config.run_root, config)
    return config


def load_stage3_config_from_env(*, repository_root: Path) -> Stage3Config:
    raw = os.environ.get(STAGE3_CONFIG_ENV, "")
    if not raw:
        raise Stage3DataError(f"{STAGE3_CONFIG_ENV} must point to the stage 3 config")
    return load_stage3_config(Path(raw), repository_root=repository_root)


def bind_accepted_stage2_catalog(
    config: Stage3Config,
    *,
    acceptance_record_path: Path,
) -> AcceptedStage2Catalog:
    _require_locked_config(config)
    acceptance_path = Path(acceptance_record_path)
    if not acceptance_path.is_file():
        raise Stage3DataError("tracked stage 2 acceptance record is missing")
    acceptance = _read_json_object(acceptance_path, "tracked acceptance record")
    try:
        require_complete_acceptance_record(acceptance)
        require_stage2_catalog_identity(config.catalog_path)
    except Stage2DataError as exc:
        raise Stage3DataError(str(exc)) from exc
    _require_acceptance_locks(acceptance, config)

    snapshot = _read_json_object(
        config.catalog_path / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
        "catalog instrument snapshot",
    )
    if (
        snapshot.get("checksum_sha256") != config.instrument_snapshot_checksum
        or snapshot.get("runtime_identity") != config.runtime_identity
    ):
        raise Stage3DataError("catalog instrument snapshot identity does not match")

    digest_record = _read_json_object(
        config.catalog_path / STAGE2_DIGEST_FILENAME,
        "catalog dataset digest",
    )
    expected_digest_record = {
        "coverage": acceptance["coverage_summary"],
        "dataset_id": acceptance["dataset_id"],
        "instrument_snapshot": acceptance["instrument_snapshot"],
        "market_data_manifest_digest": acceptance["market_data_manifest_digest"],
        "nautilus_version": acceptance["nautilus_version"],
        "runtime_identity": acceptance["runtime_identity"],
        "source_manifest_digest": acceptance["source_manifest_digest"],
    }
    if digest_record != expected_digest_record:
        raise Stage3DataError(
            "external catalog dataset identity conflicts with acceptance"
        )
    if _canonical_digest(digest_record) != config.dataset_digest:
        raise Stage3DataError("external catalog dataset digest does not match")

    coverage_record = _read_json_object(
        config.catalog_path / STAGE2_COVERAGE_FILENAME,
        "catalog coverage",
    )
    coverage = _require_external_coverage(coverage_record, config, acceptance)
    _require_external_manifest(config.catalog_path, config, acceptance)
    return AcceptedStage2Catalog(
        catalog_path=config.catalog_path,
        dataset_id=config.dataset_id,
        acceptance_digest=config.acceptance_digest,
        dataset_digest=config.dataset_digest,
        source_manifest_digest=config.source_manifest_digest,
        market_data_manifest_digest=config.market_data_manifest_digest,
        instrument_snapshot_checksum=config.instrument_snapshot_checksum,
        runtime_identity=config.runtime_identity,
        coverage=coverage,
    )


class IncrementalFeatureState:
    """Finite state consuming every native auxiliary event, including warm-up.

    Sampling just the latest mark/funding at each decision is not sufficient:
    sequence continuity and the complete funding lookback must be proven too.
    """

    def __init__(self, instrument_id: str) -> None:
        if instrument_id not in STAGE2_INSTRUMENT_IDS:
            raise Stage3DataError("instrument is not a stage 3 target")
        self.instrument_id = instrument_id
        self._bars: list[tuple[int, float, float, float, float, float]] = []
        self._marks: list[tuple[int, float]] = []
        self._funding: list[tuple[int, float]] = []
        self._last_bar_ts: int | None = None
        self._last_mark_ts: int | None = None
        self._last_funding_ts: int | None = None
        self._first_mark_ts: int | None = None
        self._first_funding_ts: int | None = None
        self._mark_coverage = _incremental_auxiliary_coverage("mark")
        self._funding_coverage = _incremental_auxiliary_coverage("funding")

    @property
    def retained_event_counts(self) -> tuple[int, int]:
        """Retained (mark, funding) events; both stay bounded by the lookbacks."""
        return (len(self._marks), len(self._funding))

    def push_mark(self, event: MarkProjection) -> None:
        self._require_instrument(event.instrument_id)
        value = _finite_positive(event.value, "mark value")
        self._last_mark_ts = _require_next_auxiliary_timestamp(
            event.ts_event, self._last_mark_ts, self._mark_coverage
        )
        if self._first_mark_ts is None:
            self._first_mark_ts = event.ts_event
        # Only the newest mark can be the as-of value of a later decision, so no
        # earlier mark can affect any future row.
        self._marks[:] = ((event.ts_event, value),)

    def push_funding(self, event: FundingProjection) -> None:
        self._require_instrument(event.instrument_id)
        value = _finite_float(event.rate, "funding rate")
        self._last_funding_ts = _require_next_auxiliary_timestamp(
            event.ts_event, self._last_funding_ts, self._funding_coverage
        )
        if self._first_funding_ts is None:
            self._first_funding_ts = event.ts_event
        self._funding.append((event.ts_event, value))

    def _prune_funding(self, decision_ts: int) -> None:
        if len(self._funding) < 2:
            return
        funding_floor = decision_ts - FUNDING_WINDOW_HOURS * HOUR_NS
        # Events at or before the floor can no longer enter any later 24h sum.
        # The newest event is kept whatever its age so that a stopped series is
        # reported as stale rather than as absent.
        self._funding = [
            item for item in self._funding[:-1] if item[0] > funding_floor
        ] + [self._funding[-1]]

    def push_bar(
        self, bar: BarProjection, *, tradable: bool = False
    ) -> FeatureObservation:
        self._require_instrument(bar.instrument_id)
        self._prune_funding(bar.ts_event)
        if (
            self._last_bar_ts is not None
            and bar.ts_event - self._last_bar_ts != HOUR_NS
        ):
            raise Stage3DataError(
                "decision bars contain a gap, duplicate, or out-of-order timestamp"
            )
        self._last_bar_ts = _require_next_timestamp(
            bar.ts_event, self._last_bar_ts, "bar"
        )
        opened = _finite_positive(bar.open, "bar open")
        high = _finite_positive(bar.high, "bar high")
        low = _finite_positive(bar.low, "bar low")
        close = _finite_positive(bar.close, "bar close")
        volume = _finite_non_negative(bar.volume, "bar volume")
        if high < max(opened, close) or low > min(opened, close) or low > high:
            raise Stage3DataError("bar OHLC projection is invalid")
        self._bars.append((bar.ts_event, opened, high, low, close, volume))
        if len(self._bars) > FEATURE_LOOKBACK_HOURS + 1:
            self._bars.pop(0)
        if len(self._bars) < FEATURE_LOOKBACK_HOURS + 1:
            return FeatureObservation(
                instrument_id=self.instrument_id,
                decision_ts=bar.ts_event,
                status="warming_up",
                values=None,
                feature_schema_digest=FEATURE_SCHEMA_DIGEST,
                tradable=False,
            )
        values = self._feature_values(bar.ts_event)
        return FeatureObservation(
            instrument_id=self.instrument_id,
            decision_ts=bar.ts_event,
            status="ready",
            values=values,
            feature_schema_digest=FEATURE_SCHEMA_DIGEST,
            tradable=tradable,
        )

    def _feature_values(self, decision_ts: int) -> tuple[float, ...]:
        bars = self._bars
        closes = [item[4] for item in bars]
        current = bars[-1]
        log_returns = [
            math.log(right / left) for left, right in zip(closes, closes[1:])
        ]
        volumes = [item[5] for item in bars[-24:]]
        volume_mean = sum(volumes) / 24.0
        variance = sum((item - volume_mean) ** 2 for item in volumes) / 24.0
        if variance <= 0.0:
            raise Stage3DataError("24h volume standard deviation is zero")
        mark_ts, mark = self._latest_as_of(self._marks, decision_ts, "mark")
        if decision_ts - mark_ts > MARK_MAX_AGE_NS:
            raise Stage3DataError("latest mark is stale at the decision timestamp")
        funding_ts, funding = self._latest_as_of(self._funding, decision_ts, "funding")
        if decision_ts - funding_ts > FUNDING_MAX_AGE_NS:
            raise Stage3DataError("latest funding is stale at the decision timestamp")
        funding_floor = decision_ts - FUNDING_WINDOW_HOURS * HOUR_NS
        _require_auxiliary_window(
            self._mark_coverage,
            self._first_mark_ts,
            mark_ts,
            window_start_ns=bars[0][0],
            window_end_ns=decision_ts + 1,
        )
        # Funding timestamps can drift around the nominal settlement. At the
        # left boundary retain proof of any slot that could enter the sum; at
        # the right boundary require only slots whose tolerance has elapsed.
        # Actual timestamps, never nominal slots, determine the causal sum.
        _require_auxiliary_window(
            self._funding_coverage,
            self._first_funding_ts,
            funding_ts,
            window_start_ns=funding_floor - FUNDING_GRID_TOLERANCE_NS + 1,
            window_end_ns=decision_ts - FUNDING_GRID_TOLERANCE_NS + 1,
        )
        funding_sum = sum(
            rate
            for ts_event, rate in self._funding
            if funding_floor < ts_event <= decision_ts
        )
        hour = datetime.fromtimestamp(decision_ts / 1_000_000_000, tz=UTC).hour
        angle = 2.0 * math.pi * hour / 24.0
        values = (
            current[4] / bars[-2][4] - 1.0,
            current[4] / bars[-5][4] - 1.0,
            current[4] / bars[-25][4] - 1.0,
            current[4] / bars[-169][4] - 1.0,
            (current[2] - current[3]) / current[4],
            math.sqrt(sum(item * item for item in log_returns[-24:])),
            math.sqrt(sum(item * item for item in log_returns[-168:])),
            (current[5] - volume_mean) / math.sqrt(variance),
            mark / current[4] - 1.0,
            funding,
            funding_sum,
            math.sin(angle),
            math.cos(angle),
            0.0 if self.instrument_id == STAGE2_INSTRUMENT_IDS[0] else 1.0,
        )
        _require_feature_vector(values)
        return values

    def _latest_as_of(
        self, events: Sequence[tuple[int, float]], decision_ts: int, name: str
    ) -> tuple[int, float]:
        if not events:
            raise Stage3DataError(f"{name} is missing at the first ready decision")
        ts_event, value = events[-1]
        if ts_event > decision_ts:
            raise Stage3DataError(f"future {name} entered the feature state")
        return ts_event, value

    def _require_instrument(self, instrument_id: str) -> None:
        if instrument_id != self.instrument_id:
            raise Stage3DataError("event instrument does not match feature state")


def build_feature_rows(
    bars: Sequence[BarProjection],
    marks: Sequence[MarkProjection],
    funding: Sequence[FundingProjection],
    *,
    decision_start_ts: int | None = None,
    require_ready_at_decision_start: bool = False,
    label_bars: Sequence[BarProjection] | None = None,
) -> tuple[FeatureObservation, ...]:
    if not bars:
        raise Stage3DataError("feature input has no decision bars")
    instrument_id = bars[0].instrument_id
    _require_projection_order(marks, "mark")
    _require_projection_order(funding, "funding")
    resolved_label_bars = label_bars if label_bars is not None else bars
    _require_bar_projection_order(resolved_label_bars)
    state = IncrementalFeatureState(instrument_id)
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
        tradable = decision_start_ts is not None and bar.ts_event >= decision_start_ts
        row = state.push_bar(bar, tradable=tradable)
        rows.append(row)
    if decision_start_ts is not None and require_ready_at_decision_start:
        first = next(
            (item for item in rows if item.decision_ts >= decision_start_ts), None
        )
        if first is None:
            raise Stage3DataError("evaluation window has no decision row")
        if first.status != "ready":
            raise Stage3DataError(
                "first tradable decision does not have complete lookback"
            )
    return _attach_labels(rows, resolved_label_bars)


def purge_training_rows(
    rows: Sequence[FeatureObservation], *, evaluation_start: datetime | int
) -> tuple[FeatureObservation, ...]:
    boundary = (
        evaluation_start
        if isinstance(evaluation_start, int)
        else datetime_to_nanos(require_utc(evaluation_start))
    )
    return tuple(
        row
        for row in rows
        if row.status == "ready"
        and row.label_available
        and row.label_end_ts is not None
        and row.label_end_ts < boundary
    )


def feature_frame(rows: Sequence[FeatureObservation]) -> pl.DataFrame:
    records: list[dict[str, object]] = []
    for row in rows:
        values = row.require_ready()
        record: dict[str, object] = {
            "instrument_id": row.instrument_id,
            "decision_ts": row.decision_ts,
        }
        record.update(zip(FEATURE_NAMES, values, strict=True))
        record.update(
            {
                "feature_schema_digest": row.feature_schema_digest,
                "label_available": row.label_available,
                "label_log_return_4h": row.label_log_return_4h,
                "label_end_ts": row.label_end_ts,
                "tradable": row.tradable,
            }
        )
        records.append(record)
    schema: dict[str, type[pl.DataType] | pl.DataType] = {
        "instrument_id": pl.String,
        "decision_ts": pl.Int64,
        **{name: pl.Float64 for name in FEATURE_NAMES},
        "feature_schema_digest": pl.String,
        "label_available": pl.Boolean,
        "label_log_return_4h": pl.Float64,
        "label_end_ts": pl.Int64,
        "tradable": pl.Boolean,
    }
    return pl.DataFrame(records, schema=schema)


def require_feature_frame_schema(frame: pl.DataFrame) -> None:
    expected_names = (
        "instrument_id",
        "decision_ts",
        *FEATURE_NAMES,
        "feature_schema_digest",
        "label_available",
        "label_log_return_4h",
        "label_end_ts",
        "tradable",
    )
    if tuple(frame.columns) != expected_names:
        raise Stage3DataError("feature frame column order does not match")
    if any(frame.schema[name] != pl.Float64 for name in FEATURE_NAMES):
        raise Stage3DataError("feature frame dtype does not match")
    digests = frame.get_column("feature_schema_digest").unique().to_list()
    if digests != [FEATURE_SCHEMA_DIGEST]:
        raise Stage3DataError("feature frame schema digest does not match")


def load_accepted_feature_window(
    config: Stage3Config,
    *,
    acceptance_record_path: Path,
    start: datetime,
    end: datetime,
    decision_start: datetime,
    mode: Literal["training", "evaluation"],
) -> dict[str, tuple[FeatureObservation, ...]]:
    if mode not in {"training", "evaluation"}:
        raise Stage3DataError("stage 3 feature mode is invalid")
    accepted = bind_accepted_stage2_catalog(
        config, acceptance_record_path=acceptance_record_path
    )
    start_utc = require_utc(start)
    end_utc = require_utc(end)
    decision_start_utc = require_utc(decision_start)
    if not start_utc < end_utc:
        raise Stage3DataError("stage 3 feature window is inverted")
    if not start_utc <= decision_start_utc < end_utc:
        raise Stage3DataError("decision start is outside the feature window")
    if mode == "evaluation" and start_utc > decision_start_utc - timedelta(hours=168):
        raise Stage3DataError("evaluation window is missing 168h pre-start context")
    dataset_start = parse_utc(STAGE2_WINDOW_START_ISO)
    dataset_end = parse_utc(STAGE2_WINDOW_END_ISO)
    label_end = min(end_utc + timedelta(hours=LABEL_HORIZON_HOURS), dataset_end)
    auxiliary_start = max(start_utc - timedelta(hours=8), dataset_start)
    start_ns = datetime_to_nanos(start_utc)
    end_ns = datetime_to_nanos(end_utc)
    label_end_ns = datetime_to_nanos(label_end)
    auxiliary_start_ns = datetime_to_nanos(auxiliary_start)
    results: dict[str, tuple[FeatureObservation, ...]] = {}
    for instrument_id in STAGE2_INSTRUMENT_IDS:
        bar_type = stage2_bar_type_str(instrument_id, "1h")
        try:
            bars_frame = load_bars(config.catalog_path, bar_type, start_utc, label_end)
            marks_frame = load_mark_prices(
                config.catalog_path, instrument_id, auxiliary_start, end_utc
            )
            funding_frame = load_funding(
                config.catalog_path, instrument_id, auxiliary_start, end_utc
            ).collect()
        except Stage2DataError as exc:
            # ``views`` reports every rejected read with the Stage 2 error type,
            # including a query window outside the declared dataset window. This
            # module has one failure contract, so callers that catch only
            # ``Stage3DataError`` must not see a Stage 2 escape.
            raise Stage3DataError(
                f"accepted catalog read failed for {instrument_id}: {exc}"
            ) from exc
        all_bars = _bars_from_frame(bars_frame)
        marks = _marks_from_frame(marks_frame)
        funding = _funding_from_frame(funding_frame)
        _require_accepted_catalog_series(
            accepted,
            data_type=STAGE2_DATA_TYPE_BARS,
            instrument_id=instrument_id,
            bar_type=bar_type,
            observed=tuple(item.ts_event for item in all_bars),
            window_start_ns=start_ns,
            window_end_ns=label_end_ns,
        )
        _require_accepted_catalog_series(
            accepted,
            data_type=STAGE2_DATA_TYPE_MARK,
            instrument_id=instrument_id,
            bar_type=stage2_bar_type_str(instrument_id, "15m"),
            observed=tuple(item.ts_event for item in marks),
            window_start_ns=auxiliary_start_ns,
            window_end_ns=end_ns,
        )
        _require_accepted_catalog_series(
            accepted,
            data_type=STAGE2_DATA_TYPE_FUNDING,
            instrument_id=instrument_id,
            bar_type=None,
            observed=tuple(item.ts_event for item in funding),
            window_start_ns=auxiliary_start_ns,
            window_end_ns=end_ns,
        )
        decision_bars = tuple(item for item in all_bars if item.ts_event < end_ns)
        rows = build_feature_rows(
            decision_bars,
            marks,
            funding,
            decision_start_ts=(
                datetime_to_nanos(decision_start_utc) if mode == "evaluation" else None
            ),
            require_ready_at_decision_start=mode == "evaluation",
            label_bars=all_bars,
        )
        results[instrument_id] = rows
    return results


def _attach_labels(
    rows: Sequence[FeatureObservation], bars: Sequence[BarProjection]
) -> tuple[FeatureObservation, ...]:
    by_instrument: dict[str, dict[int, BarProjection]] = {}
    for bar in bars:
        by_instrument.setdefault(bar.instrument_id, {})[bar.ts_event] = bar
    labeled: list[FeatureObservation] = []
    for row in rows:
        if row.status != "ready":
            labeled.append(row)
            continue
        instrument_bars = by_instrument.get(row.instrument_id, {})
        future = [
            instrument_bars.get(row.decision_ts + offset * HOUR_NS)
            for offset in range(1, 5)
        ]
        label_end_ts = row.decision_ts + LABEL_HORIZON_HOURS * HOUR_NS
        if any(item is None for item in future):
            labeled.append(
                replace(row, label_available=False, label_end_ts=label_end_ts)
            )
            continue
        first, *_, fourth = cast(list[BarProjection], future)
        opened = _finite_positive(first.open, "label entry open")
        closed = _finite_positive(fourth.close, "label horizon close")
        label = math.log(closed / opened)
        if not math.isfinite(label):
            raise Stage3DataError("label is not a finite Float64")
        labeled.append(
            replace(
                row,
                label_available=True,
                label_log_return_4h=label,
                label_end_ts=label_end_ts,
            )
        )
    return tuple(labeled)


def _coverage_int(entry: Mapping[str, object], key: str, description: str) -> int:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise Stage3DataError(f"{description} accepted coverage {key} is invalid")
    return value


def _accepted_series_coverage(
    entry: Mapping[str, object], *, description: str
) -> AcceptedSeriesCoverage:
    first = _coverage_int(entry, "first_ts_event", description)
    last = _coverage_int(entry, "last_ts_event", description)
    rows = _coverage_int(entry, "row_count", description)
    gaps = _coverage_int(entry, "gap_count", description)
    slots = rows - 1 + gaps
    if rows <= 0 or gaps < 0 or slots <= 0 or last <= first:
        raise Stage3DataError(f"{description} accepted coverage is invalid")
    span = last - first
    mean_step = (span + slots // 2) // slots
    cadence = ((mean_step + MS_NS // 2) // MS_NS) * MS_NS
    if cadence <= 0 or abs(span - cadence * slots) > slots * MS_NS:
        raise Stage3DataError(
            f"{description} accepted coverage cadence is not a fixed interval"
        )
    data_type = entry.get("data_type")
    series = AcceptedSeriesCoverage(
        description=description,
        first_ts_event=first,
        cadence_ns=cadence,
        grid_tolerance_ns=(
            FUNDING_GRID_TOLERANCE_NS
            if data_type == STAGE2_DATA_TYPE_FUNDING
            else SERIES_GRID_TOLERANCE_NS
        ),
        last_index=slots,
        allowed_gap_indices=frozenset(),
    )
    if data_type == STAGE2_DATA_TYPE_MARK:
        if gaps != len(mark_allowed_gap_ns()):
            raise Stage3DataError(
                f"{description} accepted mark gaps do not match the locked allow-list"
            )
        intervals = mark_allowed_gap_ns()
    elif gaps:
        raise Stage3DataError(
            f"{description} accepted coverage declares an unapproved gap"
        )
    else:
        intervals = ()
    allowed: set[int] = set()
    for start_ns, end_ns in intervals:
        start_index = series.grid_index(start_ns)
        if end_ns - start_ns != cadence or series.grid_index(end_ns) != start_index + 1:
            raise Stage3DataError(
                f"{description} accepted gap does not match the accepted cadence"
            )
        allowed.add(start_index)
    return replace(series, allowed_gap_indices=frozenset(allowed))


def _require_coverage_entry(
    coverage: Sequence[Mapping[str, object]],
    *,
    data_type: str,
    instrument_id: str,
    bar_type: str | None,
) -> Mapping[str, object]:
    matches = tuple(
        item
        for item in coverage
        if item.get("data_type") == data_type
        and item.get("instrument_id") == instrument_id
        and item.get("bar_type") == bar_type
    )
    if len(matches) != 1:
        raise Stage3DataError(
            f"accepted coverage does not contain one {data_type} series "
            f"for {instrument_id}"
        )
    return matches[0]


def _require_series_matches_coverage(
    series: AcceptedSeriesCoverage,
    observed: Sequence[int],
    *,
    window_start_ns: int,
    window_end_ns: int,
) -> None:
    """Bind the series actually read from the catalog to the accepted coverage.

    The sidecar declarations alone cannot prove that the queried rows are the
    accepted rows, so every observed event must land on the accepted grid and
    every omitted interval must be an interval the accepted coverage approves.
    """
    if not observed:
        raise Stage3DataError(
            f"{series.description} is missing from the accepted catalog"
        )
    previous: int | None = None
    first_index = 0
    last_index = 0
    for ts_event in observed:
        index = series.grid_index(ts_event)
        if index in series.allowed_gap_indices:
            raise Stage3DataError(
                f"{series.description} contains a record the accepted coverage omits"
            )
        if previous is None:
            first_index = index
        else:
            if index <= previous:
                raise Stage3DataError(
                    f"{series.description} contains duplicate or out-of-order "
                    "timestamps"
                )
            for missing in range(previous + 1, index):
                if missing not in series.allowed_gap_indices:
                    raise Stage3DataError(
                        f"{series.description} contains a gap that the accepted "
                        "coverage does not approve"
                    )
        previous = index
        last_index = index
    if first_index != series.first_eligible_index(
        window_start_ns
    ) or last_index != series.last_eligible_index(window_end_ns):
        raise Stage3DataError(
            f"{series.description} does not cover the accepted query window"
        )


def _require_accepted_catalog_series(
    accepted: AcceptedStage2Catalog,
    *,
    data_type: str,
    instrument_id: str,
    bar_type: str | None,
    observed: Sequence[int],
    window_start_ns: int,
    window_end_ns: int,
) -> None:
    entry = _require_coverage_entry(
        accepted.coverage,
        data_type=data_type,
        instrument_id=instrument_id,
        bar_type=bar_type,
    )
    _require_series_matches_coverage(
        _accepted_series_coverage(
            entry, description=f"{instrument_id} {bar_type or data_type}"
        ),
        observed,
        window_start_ns=window_start_ns,
        window_end_ns=window_end_ns,
    )


def _incremental_auxiliary_coverage(
    name: Literal["mark", "funding"],
) -> AcceptedSeriesCoverage:
    """The fixed Stage 2 event grids, without catalog or Nautilus dependencies."""
    start = datetime_to_nanos(parse_utc(STAGE2_WINDOW_START_ISO))
    end = datetime_to_nanos(parse_utc(STAGE2_WINDOW_END_ISO))
    cadence = MARK_CADENCE_NS if name == "mark" else FUNDING_CADENCE_NS
    first = start + cadence - MS_NS if name == "mark" else start
    return AcceptedSeriesCoverage(
        description=name,
        first_ts_event=first,
        cadence_ns=cadence,
        grid_tolerance_ns=(
            SERIES_GRID_TOLERANCE_NS if name == "mark" else FUNDING_GRID_TOLERANCE_NS
        ),
        last_index=(end - 1 - first) // cadence,
        allowed_gap_indices=frozenset(
            (omitted - first) // cadence
            for omitted, _ in (mark_allowed_gap_ns() if name == "mark" else ())
        ),
    )


def _require_next_auxiliary_timestamp(
    value: int, previous: int | None, series: AcceptedSeriesCoverage
) -> int:
    _require_next_timestamp(value, previous, series.description)
    index = series.grid_index(value)
    if index in series.allowed_gap_indices:
        raise Stage3DataError(
            f"{series.description} contains a record the accepted coverage omits"
        )
    if previous is not None:
        previous_index = series.grid_index(previous)
        if index <= previous_index:
            raise Stage3DataError(
                f"{series.description} events contain duplicate or out-of-order slots"
            )
        approved = sum(
            previous_index < gap < index for gap in series.allowed_gap_indices
        )
        if index - previous_index - 1 != approved:
            raise Stage3DataError(f"{series.description} contains an unapproved gap")
    return value


def _require_auxiliary_window(
    series: AcceptedSeriesCoverage,
    first: int | None,
    last: int,
    *,
    window_start_ns: int,
    window_end_ns: int,
) -> None:
    # Adjacent-slot checks prove the interior even after event values are pruned.
    # Boundaries additionally reject a late-starting or stopped stream whose
    # newest event alone would still satisfy the as-of age limit.
    if (
        first is None
        or series.grid_index(first) > series.first_eligible_index(window_start_ns)
        or series.grid_index(last) < series.last_eligible_index(window_end_ns)
    ):
        raise Stage3DataError(
            f"{series.description} has incomplete required coverage at ready decision"
        )


def _require_projection_order(
    events: Sequence[MarkProjection] | Sequence[FundingProjection], name: str
) -> None:
    previous: int | None = None
    for event in events:
        _require_next_timestamp(event.ts_event, previous, name)
        previous = event.ts_event


def _require_bar_projection_order(bars: Sequence[BarProjection]) -> None:
    previous_by_instrument: dict[str, int] = {}
    for bar in bars:
        previous = previous_by_instrument.get(bar.instrument_id)
        if previous is not None and bar.ts_event - previous != HOUR_NS:
            raise Stage3DataError(
                "label bars contain a gap, duplicate, or out-of-order timestamp"
            )
        previous_by_instrument[bar.instrument_id] = _require_next_timestamp(
            bar.ts_event, previous, "label bar"
        )


def _require_next_timestamp(value: int, previous: int | None, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Stage3DataError(f"{name} timestamp is invalid")
    if previous is not None and value <= previous:
        raise Stage3DataError(
            f"{name} events contain duplicate or out-of-order timestamps"
        )
    return value


def _finite_float(value: str | Decimal, field: str) -> float:
    try:
        exact = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise Stage3DataError(f"{field} is not numeric") from exc
    if not exact.is_finite():
        raise Stage3DataError(f"{field} is not finite")
    converted = float(exact)
    if not math.isfinite(converted):
        raise Stage3DataError(f"{field} is not a finite Float64")
    return converted


def _finite_positive(value: str | Decimal, field: str) -> float:
    converted = _finite_float(value, field)
    if converted <= 0.0:
        raise Stage3DataError(f"{field} must be positive")
    return converted


def _finite_non_negative(value: str | Decimal, field: str) -> float:
    """A finite value that may legitimately be zero, such as a bar's volume."""
    converted = _finite_float(value, field)
    if converted < 0.0:
        raise Stage3DataError(f"{field} must not be negative")
    return converted


def _require_feature_vector(values: Sequence[float]) -> None:
    if len(values) != len(FEATURE_SCHEMA) or any(
        not math.isfinite(item) for item in values
    ):
        raise Stage3DataError("feature vector is not finite or has schema drift")


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise Stage3DataError(f"stage 3 config {key} is invalid")
    return value


def _external_path(
    payload: Mapping[str, object], key: str, repository_root: Path
) -> Path:
    path = Path(_required_string(payload, key))
    if not path.is_absolute():
        raise Stage3DataError(f"{key} must be an absolute external path")
    resolved = path.resolve(strict=False)
    repository = Path(repository_root).resolve()
    if resolved == repository or repository in resolved.parents:
        raise Stage3DataError(f"{key} must not be inside the repository")
    if any(part.lower() == "latest" for part in resolved.parts):
        raise Stage3DataError(f"{key} must not use a latest alias")
    return resolved


def _require_locked_config(config: Stage3Config) -> None:
    expected = {
        "schema": STAGE3_CONFIG_SCHEMA,
        "dataset_id": STAGE2_DATASET_ID,
        "acceptance_digest": STAGE2_ACCEPTANCE_DIGEST,
        "dataset_digest": STAGE2_DATASET_DIGEST,
        "source_manifest_digest": STAGE2_SOURCE_MANIFEST_DIGEST,
        "market_data_manifest_digest": STAGE2_MARKET_DATA_MANIFEST_DIGEST,
        "instrument_snapshot_checksum": STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM,
        "runtime_identity": UPSTREAM_RELEASE_IDENTITY,
    }
    for key, value in expected.items():
        if getattr(config, key) != value:
            raise Stage3DataError(
                f"stage 3 config {key} conflicts with the locked identity"
            )


def _require_disjoint_output_roots(config: Stage3Config) -> None:
    catalog = config.catalog_path
    for key, root in (
        ("evidence_root", config.evidence_root),
        ("run_root", config.run_root),
    ):
        if root == catalog or catalog in root.parents or root in catalog.parents:
            raise Stage3DataError(
                f"stage 3 config {key} must not overlap the Nautilus catalog"
            )


def _require_matching_partition(path: Path, config: Stage3Config) -> None:
    if path.exists() and not path.is_dir():
        raise Stage3DataError("stage 3 output root must be a directory")
    if not path.is_dir() or not any(path.iterdir()):
        return
    identity_path = path / "stage3_partition_identity.json"
    if not identity_path.is_file():
        raise Stage3DataError(
            "existing stage 3 output partition has no locked identity"
        )
    identity = _read_json_object(identity_path, "stage 3 output partition identity")
    expected = {
        "acceptance_digest": config.acceptance_digest,
        "dataset_id": config.dataset_id,
        "runtime_identity": config.runtime_identity,
    }
    if identity != expected:
        raise Stage3DataError(
            "existing stage 3 output partition identity does not match"
        )


def _require_acceptance_locks(
    acceptance: Mapping[str, object], config: Stage3Config
) -> None:
    snapshot = acceptance.get("instrument_snapshot")
    if not isinstance(snapshot, Mapping):
        raise Stage3DataError("tracked acceptance instrument snapshot is missing")
    observed = {
        "schema": acceptance.get("schema"),
        "dataset_id": acceptance.get("dataset_id"),
        "acceptance_digest": acceptance.get("acceptance_digest"),
        "dataset_digest": acceptance.get("dataset_digest"),
        "source_manifest_digest": acceptance.get("source_manifest_digest"),
        "market_data_manifest_digest": acceptance.get("market_data_manifest_digest"),
        "instrument_snapshot_checksum": snapshot.get("checksum_sha256"),
        "runtime_identity": acceptance.get("runtime_identity"),
    }
    expected = {
        "schema": STAGE2_ACCEPTANCE_SCHEMA,
        "dataset_id": config.dataset_id,
        "acceptance_digest": config.acceptance_digest,
        "dataset_digest": config.dataset_digest,
        "source_manifest_digest": config.source_manifest_digest,
        "market_data_manifest_digest": config.market_data_manifest_digest,
        "instrument_snapshot_checksum": config.instrument_snapshot_checksum,
        "runtime_identity": config.runtime_identity,
    }
    if observed != expected:
        raise Stage3DataError("tracked stage 2 acceptance identity does not match")


def _read_json_object(path: Path, description: str) -> dict[str, object]:
    if not path.is_file():
        raise Stage3DataError(f"{description} is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Stage3DataError(f"{description} is invalid") from exc
    if not isinstance(payload, dict):
        raise Stage3DataError(f"{description} is invalid")
    return cast(dict[str, object], payload)


def _require_external_coverage(
    payload: Mapping[str, object],
    config: Stage3Config,
    acceptance: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    if payload.get("dataset_id") != config.dataset_id:
        raise Stage3DataError("external catalog coverage dataset does not match")
    catalog_path = payload.get("catalog_path")
    if catalog_path is not None and catalog_path != str(config.catalog_path):
        raise Stage3DataError("external catalog coverage path does not match")
    raw_series = payload.get("series")
    if not isinstance(raw_series, list):
        raise Stage3DataError("external catalog coverage is missing")
    normalized: list[Mapping[str, object]] = []
    for item in raw_series:
        if not isinstance(item, Mapping):
            raise Stage3DataError("external catalog coverage entry is invalid")
        entry = {
            key: value
            for key, value in item.items()
            if key not in {"bar_interval", "catalog_path", "source_checksum"}
        }
        normalized.append(entry)
    expected = acceptance.get("coverage_summary")
    if normalized != expected:
        raise Stage3DataError("external catalog coverage conflicts with acceptance")
    return tuple(normalized)


def _require_external_manifest(
    catalog_path: Path,
    config: Stage3Config,
    acceptance: Mapping[str, object],
) -> None:
    manifest = _read_json_object(
        catalog_path / STAGE2_MANIFEST_FILENAME, "external catalog source manifest"
    )
    expected_snapshot = acceptance.get("instrument_snapshot")
    if (
        manifest.get("schema") != STAGE2_SOURCE_SCHEMA
        or manifest.get("dataset_id") != config.dataset_id
        or manifest.get("nautilus_version") != STAGE2_NAUTILUS_VERSION
        or manifest.get("instrument_snapshot") != expected_snapshot
    ):
        raise Stage3DataError("external source manifest identity does not match")
    sources = manifest.get("sources")
    supplemental = manifest.get("supplemental_sources")
    catalog_evidence = acceptance.get("catalog_evidence")
    if not isinstance(catalog_evidence, Mapping):
        raise Stage3DataError("tracked catalog evidence is missing")
    if (
        not isinstance(sources, list)
        or len(sources) != catalog_evidence.get("source_object_count")
        or not isinstance(supplemental, list)
        or len(supplemental) != catalog_evidence.get("supplemental_source_count")
    ):
        raise Stage3DataError("external source manifest is incomplete")
    identities: dict[str, list[dict[str, object]]] = {}
    for key, values in (("sources", sources), ("supplemental_sources", supplemental)):
        projected: list[dict[str, object]] = []
        for item in values:
            if not isinstance(item, Mapping):
                raise Stage3DataError("external source manifest entry is invalid")
            required = {
                "checksum_url",
                "data_type",
                "end_ns",
                "instrument_id",
                "rows",
                "sha256",
                "source_url",
                "start_ns",
            }
            if not required <= set(item):
                raise Stage3DataError("external source manifest entry is incomplete")
            projected.append({name: item[name] for name in sorted(required)})
        identities[key] = projected
    market_digest = _canonical_digest(identities)
    if market_digest != config.market_data_manifest_digest:
        raise Stage3DataError("external market data manifest digest does not match")
    source_digest = _canonical_digest(
        {
            "instrument_snapshot": expected_snapshot,
            "market_data_manifest_digest": market_digest,
        }
    )
    if source_digest != config.source_manifest_digest:
        raise Stage3DataError("external source manifest digest does not match")


def _bars_from_frame(frame: pl.DataFrame) -> tuple[BarProjection, ...]:
    return tuple(
        BarProjection(
            instrument_id=cast(str, item["instrument_id"]),
            open=cast(str, item["open"]),
            high=cast(str, item["high"]),
            low=cast(str, item["low"]),
            close=cast(str, item["close"]),
            volume=cast(str, item["volume"]),
            ts_event=cast(int, item["ts_event"]),
        )
        for item in frame.iter_rows(named=True)
    )


def _marks_from_frame(frame: pl.DataFrame) -> tuple[MarkProjection, ...]:
    return tuple(
        MarkProjection(
            instrument_id=cast(str, item["instrument_id"]),
            value=cast(str, item["value"]),
            ts_event=cast(int, item["ts_event"]),
        )
        for item in frame.iter_rows(named=True)
    )


def _funding_from_frame(frame: pl.DataFrame) -> tuple[FundingProjection, ...]:
    return tuple(
        FundingProjection(
            instrument_id=cast(str, item["instrument_id"]),
            rate=cast(str, item["rate"]),
            ts_event=cast(int, item["ts_event"]),
        )
        for item in frame.iter_rows(named=True)
    )
