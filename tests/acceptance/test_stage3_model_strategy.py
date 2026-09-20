from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import cast

import polars as pl
import pytest
from nautilus_trader.trading import Strategy

from tests.acceptance import test_stage3_features as feature_fixture
from tests.acceptance import test_stage3_momentum as momentum_fixture
from tracequant.integrations.nautilus import stage3_model, stage3_momentum
from tracequant.integrations.nautilus.stage3_model import (
    canonical_business_result,
    run_stage3_model_backtest,
)
from tracequant.integrations.nautilus.strategies import stage3_model as model_strategy
from tracequant.integrations.nautilus.strategies.stage3_model import (
    BASE_ROUND_TRIP_COST_THRESHOLD,
    Stage3LightGBMStrategy,
    Stage3ModelError,
    Stage3ModelParameters,
    model_signal,
)
from tracequant.research import stage3_artifacts as artifacts
from tracequant.research.stage3_artifacts import (
    ArtifactWindow,
    Stage2ArtifactIdentity,
    default_effective_parameters,
    freeze_training_parameters,
    synthetic_fixture_provenance,
    train_fixture_lightgbm_artifact,
)
from tracequant.research.stage3_features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_DIGEST,
    HOUR_NS,
    Stage3Config,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_INSTRUMENT_IDS,
    datetime_to_nanos,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _iso(value: object) -> str:
    return cast(str, getattr(value, "isoformat")()).replace("+00:00", "Z")


def _bind_artifact_identity(
    monkeypatch: pytest.MonkeyPatch,
    config: Stage3Config,
) -> Stage2ArtifactIdentity:
    values = {
        "STAGE2_ACCEPTANCE_DIGEST": config.acceptance_digest,
        "STAGE2_DATASET_DIGEST": config.dataset_digest,
        "STAGE2_SOURCE_MANIFEST_DIGEST": config.source_manifest_digest,
        "STAGE2_MARKET_DATA_MANIFEST_DIGEST": config.market_data_manifest_digest,
        "STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM": config.instrument_snapshot_checksum,
    }
    for name, value in values.items():
        monkeypatch.setattr(artifacts, name, value)
    return artifacts.locked_stage2_identity()


def _training_frame(window: ArtifactWindow) -> pl.DataFrame:
    records: list[dict[str, object]] = []
    first = window.train_start + timedelta(hours=169) - timedelta(milliseconds=1)
    for hour in range(96):
        decision_ts = datetime_to_nanos(first + timedelta(hours=hour))
        for instrument_index, instrument_id in enumerate(STAGE2_INSTRUMENT_IDS):
            values = {
                name: float(
                    (hour + 1) * (feature_index + 1) / 10_000 + instrument_index * 0.001
                )
                for feature_index, name in enumerate(FEATURE_NAMES)
            }
            values["instrument_code"] = float(instrument_index)
            records.append(
                {
                    "instrument_id": instrument_id,
                    "decision_ts": decision_ts,
                    **values,
                    "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
                    "label_available": True,
                    "label_log_return_4h": 0.01,
                    "label_end_ts": decision_ts + 4 * HOUR_NS,
                    "tradable": False,
                }
            )
    return pl.DataFrame(
        records,
        schema={
            "instrument_id": pl.String,
            "decision_ts": pl.Int64,
            **{name: pl.Float64 for name in FEATURE_NAMES},
            "feature_schema_digest": pl.String,
            "label_available": pl.Boolean,
            "label_log_return_4h": pl.Float64,
            "label_end_ts": pl.Int64,
            "tradable": pl.Boolean,
        },
    ).sort(["decision_ts", "instrument_id"])


def _fixture_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config: Stage3Config,
) -> tuple[Path, Path, dict[str, object]]:
    train_start = feature_fixture.DATASET_START - timedelta(days=31)
    window = ArtifactWindow(
        train_start=train_start,
        train_end=feature_fixture.EVALUATION_START,
        evaluation_start=feature_fixture.EVALUATION_START,
        evaluation_end=feature_fixture.EVALUATION_END,
        role="development",
    )
    monkeypatch.setattr(stage3_model, "STAGE3_MODEL_TRAIN_START", _iso(train_start))
    monkeypatch.setattr(
        stage3_model, "STAGE3_MODEL_START", _iso(feature_fixture.EVALUATION_START)
    )
    monkeypatch.setattr(
        stage3_model, "STAGE3_MODEL_END", _iso(feature_fixture.EVALUATION_END)
    )
    identity = _bind_artifact_identity(monkeypatch, config)
    parameter_path = tmp_path / "parameters" / "stage3-lgbm-r1.json"
    freeze_training_parameters(
        parameter_path,
        revision="stage3-lgbm-r1",
        parameters=default_effective_parameters(),
        repository_root=REPOSITORY_ROOT,
    )
    partition = tmp_path / "artifact"
    manifest = train_fixture_lightgbm_artifact(
        _training_frame(window),
        output_partition=partition,
        parameter_record_path=parameter_path,
        stage2_identity=identity,
        window=window,
        provenance=synthetic_fixture_provenance(created_at="2026-09-20T00:00:00Z"),
        repository_root=REPOSITORY_ROOT,
    )

    def load_fixture(
        artifact_partition: Path,
        *,
        parameter_record_path: Path,
        expected_stage2_identity: Stage2ArtifactIdentity,
        repository_root: Path,
    ) -> artifacts.LightGBMArtifactPredictor:
        return artifacts._load_lightgbm_artifact(
            artifact_partition,
            parameter_record_path=parameter_record_path,
            expected_stage2_identity=expected_stage2_identity,
            repository_root=repository_root,
            expected_provenance_kind="synthetic_fixture",
        )

    monkeypatch.setattr(model_strategy, "load_lightgbm_artifact", load_fixture)
    return partition, parameter_path, manifest


def test_stage3_lightgbm_strategy_predicts_and_trades_through_nautilus(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog, record_path, template, artifact_lock = momentum_fixture._accepted_fixture(
        monkeypatch, tmp_path
    )
    first_config = feature_fixture._accepted_config(
        template,
        catalog,
        tmp_path / "model-run-a",
        artifact_lock_path=artifact_lock,
    )
    second_config = feature_fixture._accepted_config(
        template,
        catalog,
        tmp_path / "model-run-b",
        artifact_lock_path=artifact_lock,
    )
    artifact, parameters, artifact_manifest = _fixture_artifact(
        monkeypatch, tmp_path, first_config
    )

    first = run_stage3_model_backtest(
        first_config,
        acceptance_record_path=record_path,
        artifact_partition=artifact,
        parameter_record_path=parameters,
    )
    second = run_stage3_model_backtest(
        second_config,
        acceptance_record_path=record_path,
        artifact_partition=artifact,
        parameter_record_path=parameters,
    )

    assert issubclass(Stage3LightGBMStrategy, Strategy)
    assert first.artifact["artifact_id"] == artifact_manifest["artifact_id"]
    assert first.reports.predictions
    assert first.reports.decisions
    assert first.reports.orders
    assert first.reports.fills
    assert len(first.reports.feature_references) == len(first.reports.predictions)
    assert all(
        item["artifact_id"] == artifact_manifest["artifact_id"]
        and item["base_round_trip_cost_threshold"] == "0.0008"
        and item["feature_schema_digest"] == FEATURE_SCHEMA_DIGEST
        and len(cast(str, item["ordered_feature_digest"])) == 64
        and item["target_state"] == "long"
        for item in first.reports.predictions
    )
    assert all(
        cast(int, item["fill_ts"]) > cast(int, item["decision_ts"])
        and cast(int, item["fill_ts"]) == cast(int, item["expected_fill_ts"])
        for item in first.reports.associations
    )
    assert first.reports.terminal["open_order_count"] == 0
    assert first.result_digest == second.result_digest
    assert first.run_identity == second.run_identity
    assert canonical_business_result(first) == canonical_business_result(second)

    assert {
        "account.json",
        "artifact.json",
        "associations.json",
        "decisions.json",
        "feature-references.json",
        "fee-provenance.json",
        "fills.json",
        "funding.json",
        "manifest.json",
        "orders.json",
        "positions.json",
        "predictions.json",
        "result.json",
        "stage3_partition_identity.json",
        "summary.json",
        "terminal.json",
    } == {item.name for item in first.partition.iterdir()}
    manifest = cast(
        dict[str, object],
        json.loads((first.partition / "manifest.json").read_text(encoding="utf-8")),
    )
    assert manifest["schema"] == stage3_model.STAGE3_MODEL_SCHEMA
    assert (
        cast(dict[str, object], manifest["parameters"])[
            "base_round_trip_cost_threshold"
        ]
        == "0.0008"
    )


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0008000001, "long"),
        (0.0008, "flat"),
        (0.0, "flat"),
        (-0.0008, "flat"),
        (-0.0008000001, "short"),
    ],
)
def test_model_signal_uses_the_frozen_strict_cost_threshold(
    score: float,
    expected: str,
) -> None:
    assert model_signal(score, BASE_ROUND_TRIP_COST_THRESHOLD) == expected


def test_model_strategy_reuses_long_flat_short_rebalance_and_reversal_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog, _record_path, _template, _artifact_lock = (
        momentum_fixture._accepted_fixture(monkeypatch, tmp_path)
    )
    loaded = stage3_momentum._load_native_stage3_data(
        catalog,
        start=feature_fixture.DATASET_START,
        end=feature_fixture.EVALUATION_END,
    )

    class SequencedPredictor:
        artifact_id = "1" * 64
        provenance_differences: tuple[str, ...] = ()

        def __init__(self) -> None:
            self.calls = 0

        def predict(
            self,
            frame: pl.DataFrame,
            *,
            feature_schema_digest: str,
        ) -> tuple[float, ...]:
            assert frame.height == 1
            assert feature_schema_digest == FEATURE_SCHEMA_DIGEST
            score = 0.01 if self.calls < 32 else -0.01 if self.calls < 64 else 0.0
            self.calls += 1
            return (score,)

    predictor = SequencedPredictor()
    monkeypatch.setattr(
        model_strategy,
        "load_lightgbm_artifact",
        lambda *args, **kwargs: predictor,
    )
    monkeypatch.setattr(
        model_strategy,
        "_validated_artifact_reference",
        lambda *args, **kwargs: {"artifact_id": predictor.artifact_id},
    )
    window = ArtifactWindow(
        train_start=feature_fixture.DATASET_START - timedelta(days=31),
        train_end=feature_fixture.EVALUATION_START,
        evaluation_start=feature_fixture.EVALUATION_START,
        evaluation_end=feature_fixture.EVALUATION_END,
        role="development",
    )
    parameters = Stage3ModelParameters(
        evaluation_start_ns=datetime_to_nanos(window.evaluation_start),
        evaluation_end_ns=datetime_to_nanos(window.evaluation_end),
    )
    strategy = Stage3LightGBMStrategy(
        parameters,
        artifact_partition=tmp_path / "artifact",
        parameter_record_path=tmp_path / "parameters.json",
        expected_stage2_identity=artifacts.locked_stage2_identity(),
        expected_window=window,
        repository_root=REPOSITORY_ROOT,
    )

    reports = stage3_momentum._run_engine(
        loaded,
        parameters.execution_parameters(),
        strategy=strategy,
    )

    assert {item["target_state"] for item in reports.decisions} == {
        "long",
        "flat",
        "short",
    }
    assert any(item["action"] == "reversal_close" for item in reports.decisions)
    assert any(
        item["action"] == "submit_delta" and item["current_qty"] != "0"
        for item in reports.decisions
    )
    assert all(
        cast(int, item["fill_ts"]) > cast(int, item["decision_ts"])
        for item in reports.associations
    )


def test_model_artifact_failure_precedes_nautilus_subscription_or_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def reject_artifact(*args: object, **kwargs: object) -> object:
        raise artifacts.Stage3ArtifactError("tampered manifest")

    monkeypatch.setattr(model_strategy, "load_lightgbm_artifact", reject_artifact)
    window = ArtifactWindow(
        train_start=feature_fixture.DATASET_START - timedelta(days=31),
        train_end=feature_fixture.EVALUATION_START,
        evaluation_start=feature_fixture.EVALUATION_START,
        evaluation_end=feature_fixture.EVALUATION_END,
        role="development",
    )
    strategy = Stage3LightGBMStrategy(
        Stage3ModelParameters(
            evaluation_start_ns=datetime_to_nanos(window.evaluation_start),
            evaluation_end_ns=datetime_to_nanos(window.evaluation_end),
        ),
        artifact_partition=tmp_path / "artifact",
        parameter_record_path=tmp_path / "parameters.json",
        expected_stage2_identity=artifacts.locked_stage2_identity(),
        expected_window=window,
        repository_root=REPOSITORY_ROOT,
    )

    with pytest.raises(Stage3ModelError, match="artifact validation failed"):
        strategy._start()
    assert strategy.predictions == []
    assert strategy.decisions == []
