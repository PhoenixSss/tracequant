from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal, Self, cast

import polars as pl
from nautilus_trader.model import Bar

from tracequant.integrations.nautilus.strategies.stage3_momentum import (
    BASE_MAKER_FEE,
    BASE_TAKER_FEE,
    MOMENTUM_TARGET_NOTIONAL_USDT,
    Signal,
    Stage3MomentumError,
    Stage3MomentumParameters,
    Stage3MomentumStrategy,
    target_quantity,
)
from tracequant.research import stage3_artifacts
from tracequant.research.stage3_artifacts import (
    MANIFEST_FILENAME,
    ArtifactWindow,
    LightGBMArtifactPredictor,
    Stage2ArtifactIdentity,
    Stage3ArtifactError,
    load_lightgbm_artifact,
)
from tracequant.research.stage3_features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_DIGEST,
    BarProjection,
)
from tracequant.source_data.stage2_btceth import STAGE2_INSTRUMENT_IDS


class _FixedDecimals:
    BASE_ROUND_TRIP_COST_THRESHOLD: Final = Decimal("0.0008")


BASE_ROUND_TRIP_COST_THRESHOLD: Final = _FixedDecimals.BASE_ROUND_TRIP_COST_THRESHOLD
MODEL_TARGET_NOTIONAL_USDT: Final = MOMENTUM_TARGET_NOTIONAL_USDT
PREDICTION_ATOL: Final = 1e-12
PREDICTION_RTOL: Final = 1e-9
TargetState = Literal["long", "flat", "short"]


class Stage3ModelError(Stage3MomentumError):
    """Raised when the fixed Stage 3 model Strategy cannot run safely."""


@dataclass(frozen=True)
class Stage3ModelParameters:
    """Frozen model-signal and shared execution parameters."""

    evaluation_start_ns: int
    evaluation_end_ns: int
    instrument_ids: tuple[str, ...] = STAGE2_INSTRUMENT_IDS
    base_round_trip_cost_threshold: Decimal = BASE_ROUND_TRIP_COST_THRESHOLD
    target_notional_usdt: Decimal = MODEL_TARGET_NOTIONAL_USDT
    maker_fee: Decimal = BASE_MAKER_FEE
    taker_fee: Decimal = BASE_TAKER_FEE

    def validate(self) -> None:
        if self.instrument_ids != STAGE2_INSTRUMENT_IDS:
            raise Stage3ModelError("model instruments do not match Stage 3")
        if self.base_round_trip_cost_threshold != BASE_ROUND_TRIP_COST_THRESHOLD:
            raise Stage3ModelError("model cost threshold is not the frozen 0.0008")
        if self.target_notional_usdt != MODEL_TARGET_NOTIONAL_USDT:
            raise Stage3ModelError("target notional is not the frozen 10000 USDT")
        if self.maker_fee != BASE_MAKER_FEE or self.taker_fee != BASE_TAKER_FEE:
            raise Stage3ModelError("model fees do not match the frozen base fees")
        if self.evaluation_end_ns <= self.evaluation_start_ns:
            raise Stage3ModelError("model evaluation window is inverted")

    def execution_parameters(self) -> Stage3MomentumParameters:
        self.validate()
        return Stage3MomentumParameters(
            evaluation_start_ns=self.evaluation_start_ns,
            evaluation_end_ns=self.evaluation_end_ns,
            instrument_ids=self.instrument_ids,
            target_notional_usdt=self.target_notional_usdt,
            maker_fee=self.maker_fee,
            taker_fee=self.taker_fee,
        )


def model_signal(score: float, threshold: Decimal) -> Signal:
    if not math.isfinite(score):
        raise Stage3ModelError("model prediction is not finite")
    value = Decimal(str(score))
    if value > threshold:
        return "long"
    if value < -threshold:
        return "short"
    return "flat"


def ordered_feature_digest(values: tuple[float, ...]) -> str:
    if len(values) != len(FEATURE_NAMES) or any(
        not math.isfinite(value) for value in values
    ):
        raise Stage3ModelError("ordered feature vector is invalid")
    payload = [
        {"name": name, "value": repr(value)}
        for name, value in zip(FEATURE_NAMES, values, strict=True)
    ]
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class Stage3LightGBMStrategy(Stage3MomentumStrategy):
    """Thin LightGBM signal layer over the shared Stage 3 execution state machine."""

    def __new__(
        cls,
        parameters: Stage3ModelParameters,
        *,
        artifact_partition: Path,
        parameter_record_path: Path,
        expected_stage2_identity: Stage2ArtifactIdentity,
        expected_window: ArtifactWindow,
        repository_root: Path,
    ) -> Self:
        # Nautilus' extension-backed Strategy allocator accepts only the class;
        # consume the Python-owned constructor contract before crossing it.
        return super().__new__(cls)

    def __init__(
        self,
        parameters: Stage3ModelParameters,
        *,
        artifact_partition: Path,
        parameter_record_path: Path,
        expected_stage2_identity: Stage2ArtifactIdentity,
        expected_window: ArtifactWindow,
        repository_root: Path,
    ) -> None:
        parameters.validate()
        super().__init__(parameters.execution_parameters())
        self.model_parameters = parameters
        self.predictions: list[dict[str, object]] = []
        self.feature_vectors: dict[tuple[str, int], tuple[float, ...]] = {}
        self._artifact_partition = artifact_partition
        self._parameter_record_path = parameter_record_path
        self._expected_stage2_identity = expected_stage2_identity
        self._expected_window = expected_window
        self._repository_root = repository_root
        self._predictor: LightGBMArtifactPredictor | None = None
        self._artifact_reference: dict[str, object] | None = None

    @property
    def predictor(self) -> LightGBMArtifactPredictor:
        if self._predictor is None:
            raise Stage3ModelError("model artifact has not been loaded")
        return self._predictor

    @property
    def artifact_reference(self) -> dict[str, object]:
        if self._artifact_reference is None:
            raise Stage3ModelError("model artifact identity has not been loaded")
        return dict(self._artifact_reference)

    def _start(self) -> None:
        try:
            predictor = load_lightgbm_artifact(
                self._artifact_partition,
                parameter_record_path=self._parameter_record_path,
                expected_stage2_identity=self._expected_stage2_identity,
                repository_root=self._repository_root,
            )
        except Stage3ArtifactError as exc:
            raise Stage3ModelError(f"model artifact validation failed: {exc}") from exc
        reference = _validated_artifact_reference(
            self._artifact_partition,
            predictor=predictor,
            expected_window=self._expected_window,
        )
        self._predictor = predictor
        self._artifact_reference = reference
        super()._start()

    def _handle_bar(self, bar: Bar) -> None:
        instrument_id = str(bar.bar_type.instrument_id)
        state = self._feature_states.get(instrument_id)
        if state is None or bar.bar_type != self._bar_types.get(instrument_id):
            raise Stage3ModelError("decision bar is outside the Stage 3 contract")
        decision_ts = int(bar.ts_event)
        tradable = (
            self.model_parameters.evaluation_start_ns
            <= decision_ts
            < self.model_parameters.evaluation_end_ns
        )
        observation = state.push_bar(
            BarProjection(
                instrument_id=instrument_id,
                bar_type=str(bar.bar_type),
                open=str(bar.open),
                high=str(bar.high),
                low=str(bar.low),
                close=str(bar.close),
                volume=str(bar.volume),
                ts_event=decision_ts,
            ),
            tradable=tradable,
        )
        if not tradable:
            return
        if observation.status != "ready":
            raise Stage3ModelError("first tradable model decision is not feature-ready")
        values = observation.require_ready()
        self.feature_vectors[(instrument_id, decision_ts)] = values
        frame = pl.DataFrame(
            {name: [value] for name, value in zip(FEATURE_NAMES, values, strict=True)},
            schema={name: pl.Float64 for name in FEATURE_NAMES},
        )
        score = self.predictor.predict(
            frame,
            feature_schema_digest=observation.feature_schema_digest,
        )[0]
        signal = model_signal(
            score, self.model_parameters.base_round_trip_cost_threshold
        )
        feature_digest = ordered_feature_digest(values)
        instrument = self._instruments[instrument_id]
        target = target_quantity(
            signal=signal,
            close=Decimal(str(bar.close)),
            instrument=instrument,
            target_notional=self.model_parameters.target_notional_usdt,
        )
        prediction: dict[str, object] = {
            "artifact_id": self.predictor.artifact_id,
            "base_round_trip_cost_threshold": str(
                self.model_parameters.base_round_trip_cost_threshold
            ),
            "decision_ts": decision_ts,
            "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
            "instrument_id": instrument_id,
            "ordered_feature_digest": feature_digest,
            "score": repr(score),
            "target_qty": str(target),
            "target_state": signal,
        }
        prediction["prediction_id"] = _prediction_id(prediction)
        decision_fields = dict(prediction)
        self._queue_decision(
            instrument_id=instrument_id,
            decision_ts=decision_ts,
            close=Decimal(str(bar.close)),
            ret_24h=None,
            signal=signal,
            target=target,
            decision_fields=decision_fields,
        )
        prediction["decision_id"] = self.decisions[-1]["decision_id"]
        self.predictions.append(prediction)


def _prediction_id(prediction: dict[str, object]) -> str:
    encoded = json.dumps(
        prediction,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validated_artifact_reference(
    artifact_partition: Path,
    *,
    predictor: LightGBMArtifactPredictor,
    expected_window: ArtifactWindow,
) -> dict[str, object]:
    """Bind Strategy metadata to the same validated artifact identity."""
    manifest = stage3_artifacts._read_regular_json_object(
        artifact_partition / MANIFEST_FILENAME,
        "artifact manifest",
    )
    stage3_artifacts._validate_manifest_envelope(manifest)
    if manifest["artifact_id"] != predictor.artifact_id:
        raise Stage3ModelError("model artifact identity changed while loading")
    window = manifest["window"]
    training = manifest["training"]
    model = manifest["model"]
    if not all(isinstance(item, Mapping) for item in (window, training, model)):
        raise Stage3ModelError("model artifact identity is incomplete")
    window = cast(Mapping[str, object], window)
    training = cast(Mapping[str, object], training)
    model = cast(Mapping[str, object], model)
    expected_window_payload: dict[str, object] = {
        "evaluation_end": _utc_text(expected_window.evaluation_end),
        "evaluation_start": _utc_text(expected_window.evaluation_start),
        "role": expected_window.role,
        "train_end": _utc_text(expected_window.train_end),
        "train_start": _utc_text(expected_window.train_start),
    }
    if any(window.get(key) != value for key, value in expected_window_payload.items()):
        raise Stage3ModelError("model artifact window does not match evaluation")
    return {
        "artifact_id": predictor.artifact_id,
        "manifest_digest": manifest["manifest_digest"],
        "model_checksum": model["checksum_sha256"],
        "provenance_differences": list(predictor.provenance_differences),
        "training_parameter_digest": training["training_parameter_digest"],
        "training_parameter_revision": training["training_parameter_revision"],
        "window": expected_window_payload,
    }


def _utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
