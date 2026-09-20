from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal, cast

from nautilus_trader.model import Bar, CryptoPerpetual

from tracequant.integrations.nautilus import stage3_model, stage3_momentum
from tracequant.integrations.nautilus.stage3_model import (
    Stage3ModelReports,
)
from tracequant.integrations.nautilus.stage3_momentum import (
    Stage3MomentumReports,
)
from tracequant.integrations.nautilus.strategies.stage3_model import (
    BASE_ROUND_TRIP_COST_THRESHOLD,
    Stage3LightGBMStrategy,
    Stage3ModelParameters,
)
from tracequant.integrations.nautilus.strategies.stage3_momentum import (
    MOMENTUM_TARGET_NOTIONAL_USDT,
    Stage3DecisionReplayStrategy,
    Stage3MomentumError,
    Stage3MomentumParameters,
    target_quantity,
)
from tracequant.research import stage3_artifacts
from tracequant.research.stage3_artifacts import (
    ArtifactWindow,
    TrainingProvenance,
)
from tracequant.research.stage3_features import (
    FEATURE_LOOKBACK_HOURS,
    FEATURE_SCHEMA_DIGEST,
    Stage3Config,
    bind_accepted_stage2_catalog,
    load_accepted_feature_window,
    load_stage3_config,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_INSTRUMENT_IDS,
    datetime_to_nanos,
    parse_utc,
)

STAGE3_EVALUATION_SCHEMA: Final = "tracequant-stage3-evaluation-v1"
STAGE3_EVALUATION_RUN_SCHEMA: Final = "tracequant-stage3-evaluation-run-v1"
TRAINING_PARAMETER_REVISION: Final = "stage3-lgbm-r1"
ANNUALIZATION_PERIODS: Final = 24 * 365

StrategyName = Literal["momentum", "lightgbm"]
ScenarioName = Literal[
    "base", "zero_fee", "double_fee", "zero_funding", "double_funding"
]
CommonReports = Stage3MomentumReports | Stage3ModelReports


class Stage3EvaluationError(Stage3MomentumError):
    """Raised when the fixed Stage 3 evaluation matrix cannot be proven."""


@dataclass(frozen=True)
class EvaluationFold:
    fold_id: str
    role: Literal["development", "validation", "final_test"]
    train_start: datetime
    evaluation_start: datetime
    evaluation_end: datetime

    @property
    def window(self) -> ArtifactWindow:
        return ArtifactWindow(
            train_start=self.train_start,
            train_end=self.evaluation_start,
            evaluation_start=self.evaluation_start,
            evaluation_end=self.evaluation_end,
            role=self.role,
        )

    def payload(self) -> dict[str, str]:
        return {
            "fold_id": self.fold_id,
            "role": self.role,
            "train_start": _utc_text(self.train_start),
            "train_end": _utc_text(self.evaluation_start),
            "purge_boundary": _utc_text(self.evaluation_start),
            "evaluation_start": _utc_text(self.evaluation_start),
            "evaluation_end": _utc_text(self.evaluation_end),
        }


EXPANDING_FOLD_SPECS: Final = (
    (
        "development-2022",
        "development",
        "2020-01-01T00:00:00Z",
        "2022-01-01T00:00:00Z",
        "2023-01-01T00:00:00Z",
    ),
    (
        "development-2023",
        "development",
        "2020-01-01T00:00:00Z",
        "2023-01-01T00:00:00Z",
        "2024-01-01T00:00:00Z",
    ),
    (
        "validation-2024",
        "validation",
        "2020-01-01T00:00:00Z",
        "2024-01-01T00:00:00Z",
        "2025-01-01T00:00:00Z",
    ),
    (
        "final-test-2025-2026",
        "final_test",
        "2020-01-01T00:00:00Z",
        "2025-01-01T00:00:00Z",
        "2026-09-01T00:00:00Z",
    ),
)


@dataclass(frozen=True)
class AccountingScenario:
    name: ScenarioName
    maker_fee: Decimal
    taker_fee: Decimal
    funding_multiplier: Decimal

    def payload(self) -> dict[str, str]:
        return {
            "name": self.name,
            "maker_fee": str(self.maker_fee),
            "taker_fee": str(self.taker_fee),
            "funding_multiplier": str(self.funding_multiplier),
        }

    @property
    def digest(self) -> str:
        return _digest(self.payload())


ACCOUNTING_SCENARIO_SPECS: Final = (
    ("base", "0.0002", "0.0004", "1"),
    ("zero_fee", "0", "0", "1"),
    ("double_fee", "0.0004", "0.0008", "1"),
    ("zero_funding", "0.0002", "0.0004", "0"),
    ("double_funding", "0.0002", "0.0004", "2"),
)


def expanding_folds() -> tuple[EvaluationFold, ...]:
    return tuple(
        EvaluationFold(
            fold_id,
            cast(Literal["development", "validation", "final_test"], role),
            parse_utc(train_start),
            parse_utc(evaluation_start),
            parse_utc(evaluation_end),
        )
        for fold_id, role, train_start, evaluation_start, evaluation_end in EXPANDING_FOLD_SPECS
    )


def accounting_scenarios() -> tuple[AccountingScenario, ...]:
    return tuple(
        AccountingScenario(
            cast(ScenarioName, name),
            Decimal(maker_fee),
            Decimal(taker_fee),
            Decimal(funding_multiplier),
        )
        for name, maker_fee, taker_fee, funding_multiplier in ACCOUNTING_SCENARIO_SPECS
    )


def base_scenario() -> AccountingScenario:
    return accounting_scenarios()[0]


def sensitivity_scenarios() -> tuple[AccountingScenario, ...]:
    return accounting_scenarios()[1:]


@dataclass(frozen=True)
class EvaluationRun:
    fold: EvaluationFold
    strategy: StrategyName
    scenario: AccountingScenario
    reports: CommonReports
    partition: Path
    fee_provenance: tuple[dict[str, object], ...]
    artifact: dict[str, object] | None
    predictions: tuple[dict[str, object], ...]
    decision_digest: str
    prediction_digest: str | None
    scenario_digest: str
    information_status: dict[str, object]
    metrics: dict[str, object]
    run_identity: str
    result_digest: str

    def reference(self, *, root: Path) -> dict[str, object]:
        result: dict[str, object] = {
            "config_digest": _run_config_digest(
                self.fold, self.strategy, self.scenario
            ),
            "decision_digest": self.decision_digest,
            "fee_provenance": [dict(item) for item in self.fee_provenance],
            "fee_provenance_digest": _digest(self.fee_provenance),
            "fold": self.fold.payload(),
            "metrics": self.metrics,
            "partition": self.partition.relative_to(root).as_posix(),
            "result_digest": self.result_digest,
            "run_type": ("base" if self.scenario.name == "base" else "sensitivity"),
            "run_identity": self.run_identity,
            "scenario": self.scenario.payload(),
            "scenario_digest": self.scenario_digest,
            "strategy": self.strategy,
            "sensitivity_information": self.information_status,
        }
        if self.artifact is not None:
            result["artifact"] = {
                key: self.artifact[key]
                for key in (
                    "artifact_id",
                    "model_checksum",
                    "training_parameter_digest",
                    "training_parameter_revision",
                )
            }
            result["prediction_digest"] = self.prediction_digest
        return result


@dataclass(frozen=True)
class Stage3EvaluationOutcome:
    partition: Path
    evidence_partition: Path
    manifest: dict[str, object]
    result_digest: str


def run_stage3_evaluation(
    config: Stage3Config,
    *,
    acceptance_record_path: Path,
    provenance: TrainingProvenance | None = None,
) -> Stage3EvaluationOutcome:
    """Run the fixed expanding-window matrix and final accounting replays."""
    folds = expanding_folds()
    scenarios = sensitivity_scenarios()
    _validate_fold_contract(folds)
    _require_new_evaluation_roots(config)
    stage3_momentum._require_runtime()
    stage3_momentum._require_requirements_baseline()
    stage3_model._require_requirements_baseline()
    config.run_root.mkdir(parents=True)
    config.evidence_root.mkdir(parents=True)
    identity = {
        "acceptance_digest": config.acceptance_digest,
        "dataset_id": config.dataset_id,
        "runtime_identity": config.runtime_identity,
    }
    _write_json_exclusive(config.run_root / "stage3_partition_identity.json", identity)
    _write_json_exclusive(
        config.evidence_root / "stage3_partition_identity.json", identity
    )

    parameter_record = config.evidence_root / "training-parameters.json"
    frozen = stage3_artifacts.freeze_training_parameters(
        parameter_record,
        revision=TRAINING_PARAMETER_REVISION,
        parameters=stage3_artifacts.default_effective_parameters(),
        repository_root=_repository_root(),
    )
    formal_provenance = provenance or stage3_artifacts.capture_formal_provenance(
        _repository_root()
    )
    artifacts: dict[str, tuple[Path, dict[str, object]]] = {}
    for fold in folds:
        partition = config.evidence_root / "artifacts" / fold.fold_id
        artifact_manifest = _train_fold_artifact(
            config,
            acceptance_record_path=acceptance_record_path,
            partition=partition,
            parameter_record=parameter_record,
            fold=fold,
            provenance=formal_provenance,
        )
        _require_parameter_identity(artifact_manifest, frozen.revision, frozen.digest)
        artifacts[fold.fold_id] = (partition, artifact_manifest)

    final_fold = folds[-1]
    input_digest = _digest(stage3_momentum._config_identity_payload(config))
    frozen_final_test_config = _freeze_final_test_config(
        config,
        fold=final_fold,
        scenarios=accounting_scenarios(),
    )
    base_references: list[dict[str, object]] = []
    base_metrics: list[tuple[StrategyName, dict[str, object]]] = []
    final_by_strategy: dict[StrategyName, EvaluationRun] = {}
    for fold in folds:
        artifact_partition, _ = artifacts[fold.fold_id]
        for strategy in cast(tuple[StrategyName, ...], ("momentum", "lightgbm")):
            run_partition = config.run_root / "base" / fold.fold_id / strategy
            run = _run_base_strategy(
                replace(config, run_root=run_partition),
                acceptance_record_path=acceptance_record_path,
                artifact_partition=artifact_partition,
                parameter_record=parameter_record,
                fold=fold,
                strategy=strategy,
                frozen_final_test_config=frozen_final_test_config,
            )
            base_references.append(run.reference(root=config.run_root))
            base_metrics.append((strategy, run.metrics))
            if fold.role == "final_test":
                final_by_strategy[strategy] = run

    if set(final_by_strategy) != {"momentum", "lightgbm"}:
        raise Stage3EvaluationError("final-test base strategy matrix is incomplete")
    sensitivity_references: list[dict[str, object]] = []
    for strategy in cast(tuple[StrategyName, ...], ("momentum", "lightgbm")):
        base = final_by_strategy[strategy]
        for scenario in scenarios:
            run_partition = config.run_root / "sensitivity" / strategy / scenario.name
            run = _run_sensitivity_scenario(
                replace(config, run_root=run_partition),
                acceptance_record_path=acceptance_record_path,
                fold=final_fold,
                strategy=strategy,
                scenario=scenario,
                base=base,
                frozen_final_test_config=frozen_final_test_config,
            )
            sensitivity_references.append(run.reference(root=config.run_root))

    final_by_strategy.clear()
    del base, run
    fixtures = _run_accounting_fixtures(
        config,
        acceptance_record_path=acceptance_record_path,
        fold=final_fold,
    )
    dispersion = _fold_dispersion(base_metrics)
    stable_payload: dict[str, object] = {
        "accounting_fixtures": fixtures,
        "base_runs": base_references,
        "fold_dispersion": dispersion,
        "folds": [fold.payload() for fold in folds],
        "frozen_final_test_config": frozen_final_test_config,
        "input_digest": input_digest,
        "sensitivity_runs": sensitivity_references,
        "training_parameter_digest": frozen.digest,
        "training_parameter_revision": frozen.revision,
    }
    result_digest = _digest(stable_payload)
    manifest: dict[str, object] = {
        "schema": STAGE3_EVALUATION_SCHEMA,
        **stable_payload,
        "evidence": {
            "artifact_partitions": {
                fold_id: (Path("artifacts") / fold_id).as_posix()
                for fold_id in artifacts
            },
            "parameter_record": "training-parameters.json",
        },
        "result_digest": result_digest,
    }
    manifest["manifest_digest"] = _digest(manifest)
    _write_json_exclusive(config.run_root / "manifest.json", manifest)
    return Stage3EvaluationOutcome(
        partition=config.run_root,
        evidence_partition=config.evidence_root,
        manifest=manifest,
        result_digest=result_digest,
    )


def _train_fold_artifact(
    config: Stage3Config,
    *,
    acceptance_record_path: Path,
    partition: Path,
    parameter_record: Path,
    fold: EvaluationFold,
    provenance: TrainingProvenance,
) -> dict[str, object]:
    return stage3_artifacts.train_lightgbm_artifact(
        config,
        acceptance_record_path=acceptance_record_path,
        output_partition=partition,
        parameter_record_path=parameter_record,
        window=fold.window,
        provenance=provenance,
        repository_root=_repository_root(),
    )


def _run_base_strategy(
    config: Stage3Config,
    *,
    acceptance_record_path: Path,
    artifact_partition: Path,
    parameter_record: Path,
    fold: EvaluationFold,
    strategy: StrategyName,
    frozen_final_test_config: Mapping[str, object] | None = None,
) -> EvaluationRun:
    _require_frozen_final_test_config(
        frozen_final_test_config,
        config=config,
        fold=fold,
        strategy=strategy,
        scenario=base_scenario(),
    )
    stage3_momentum._require_external_run_root(
        config.run_root, catalog_path=config.catalog_path
    )
    stage3_momentum._claim_run_root(config.run_root)
    try:
        context_start = fold.evaluation_start - timedelta(hours=FEATURE_LOOKBACK_HOURS)
        expected = load_accepted_feature_window(
            config,
            acceptance_record_path=acceptance_record_path,
            start=context_start,
            end=fold.evaluation_end,
            decision_start=fold.evaluation_start,
            mode="evaluation",
        )
        loaded = stage3_momentum._load_native_stage3_data(
            config.catalog_path,
            start=context_start,
            end=fold.evaluation_end,
        )
        start_ns = datetime_to_nanos(fold.evaluation_start)
        end_ns = datetime_to_nanos(fold.evaluation_end)
        artifact: dict[str, object] | None = None
        predictions: tuple[dict[str, object], ...] = ()
        if strategy == "momentum":
            parameters = Stage3MomentumParameters(start_ns, end_ns)
            reports: CommonReports = stage3_momentum._run_engine(loaded, parameters)
            stage3_momentum._require_runtime_feature_parity(
                reports.decisions, expected, loaded.instruments
            )
        else:
            model_parameters = Stage3ModelParameters(start_ns, end_ns)
            model_strategy = Stage3LightGBMStrategy(
                model_parameters,
                artifact_partition=artifact_partition,
                parameter_record_path=parameter_record,
                expected_stage2_identity=stage3_model._stage2_identity(config),
                expected_window=fold.window,
                repository_root=_repository_root(),
            )
            shared = stage3_momentum._run_engine(
                loaded,
                model_parameters.execution_parameters(),
                strategy=model_strategy,
            )
            feature_references = stage3_model._require_prediction_parity(
                model_strategy, expected, loaded.instruments
            )
            predictions = tuple(dict(item) for item in model_strategy.predictions)
            artifact = model_strategy.artifact_reference
            reports = Stage3ModelReports(
                feature_references=feature_references,
                predictions=predictions,
                decisions=shared.decisions,
                associations=shared.associations,
                orders=shared.orders,
                fills=shared.fills,
                positions=shared.positions,
                account=shared.account,
                result=shared.result,
                summary=shared.summary,
                funding=shared.funding,
                terminal=shared.terminal,
            )
        run = _build_run(
            fold=fold,
            strategy=strategy,
            scenario=base_scenario(),
            reports=reports,
            partition=config.run_root,
            fee_provenance=loaded.fee_provenance,
            artifact=artifact,
            predictions=predictions,
        )
        _write_run_partition(run)
        return run
    except Exception:
        stage3_momentum._release_empty_run_root(config.run_root)
        raise


def _run_sensitivity_scenario(
    config: Stage3Config,
    *,
    acceptance_record_path: Path,
    fold: EvaluationFold,
    strategy: StrategyName,
    scenario: AccountingScenario,
    base: EvaluationRun,
    frozen_final_test_config: Mapping[str, object] | None = None,
) -> EvaluationRun:
    if scenario.name == "base":
        raise Stage3EvaluationError("base is not a sensitivity replay scenario")
    if fold.role != "final_test":
        raise Stage3EvaluationError(
            "sensitivity replay is only approved for the final_test fold"
        )
    _require_frozen_final_test_config(
        frozen_final_test_config,
        config=config,
        fold=fold,
        strategy=strategy,
        scenario=scenario,
    )
    stage3_momentum._require_external_run_root(
        config.run_root, catalog_path=config.catalog_path
    )
    stage3_momentum._claim_run_root(config.run_root)
    try:
        bind_accepted_stage2_catalog(
            config, acceptance_record_path=acceptance_record_path
        )
        context_start = fold.evaluation_start - timedelta(hours=FEATURE_LOOKBACK_HOURS)
        loaded = stage3_momentum._load_native_stage3_data(
            config.catalog_path,
            start=context_start,
            end=fold.evaluation_end,
            maker_fee=scenario.maker_fee,
            taker_fee=scenario.taker_fee,
            funding_multiplier=scenario.funding_multiplier,
        )
        parameters = Stage3MomentumParameters(
            datetime_to_nanos(fold.evaluation_start),
            datetime_to_nanos(fold.evaluation_end),
            maker_fee=scenario.maker_fee,
            taker_fee=scenario.taker_fee,
            accounting_only_replay=True,
        )
        replay = Stage3DecisionReplayStrategy(
            parameters, frozen_decisions=base.reports.decisions
        )
        reports = stage3_momentum._run_engine(loaded, parameters, strategy=replay)
        replay.require_complete_replay()
        bind_accepted_stage2_catalog(
            config, acceptance_record_path=acceptance_record_path
        )
        decision_digest = _decision_digest(reports.decisions, strategy)
        if decision_digest != base.decision_digest:
            raise Stage3EvaluationError(
                "accounting scenario changed the frozen target decisions"
            )
        run = _build_run(
            fold=fold,
            strategy=strategy,
            scenario=scenario,
            reports=reports,
            partition=config.run_root,
            fee_provenance=loaded.fee_provenance,
            artifact=base.artifact,
            predictions=base.predictions,
            information_status=_sensitivity_information(base, scenario),
        )
        _write_run_partition(run)
        return run
    except Exception:
        stage3_momentum._release_empty_run_root(config.run_root)
        raise


def _build_run(
    *,
    fold: EvaluationFold,
    strategy: StrategyName,
    scenario: AccountingScenario,
    reports: CommonReports,
    partition: Path,
    fee_provenance: Sequence[Mapping[str, object]],
    artifact: Mapping[str, object] | None,
    predictions: Sequence[Mapping[str, object]],
    information_status: Mapping[str, object] | None = None,
) -> EvaluationRun:
    decision_digest = _decision_digest(reports.decisions, strategy)
    prediction_payload = tuple(dict(item) for item in predictions)
    prediction_digest = _digest(prediction_payload) if prediction_payload else None
    metrics = _metrics(reports, fold=fold)
    artifact_payload = dict(artifact) if artifact is not None else None
    scenario_digest = _scenario_input_digest(
        scenario, reports=reports, fee_provenance=fee_provenance
    )
    stable_result = {
        "artifact_id": (
            artifact_payload.get("artifact_id")
            if artifact_payload is not None
            else None
        ),
        "decision_digest": decision_digest,
        "metrics": metrics,
        "nautilus": _reports_payload(reports),
        "prediction_digest": prediction_digest,
        "scenario_digest": scenario_digest,
        "strategy": strategy,
    }
    result_digest = _digest(stable_result)
    run_identity = _digest(
        {
            "config_digest": _run_config_digest(fold, strategy, scenario),
            "result_digest": result_digest,
            "schema": STAGE3_EVALUATION_RUN_SCHEMA,
        }
    )
    return EvaluationRun(
        fold=fold,
        strategy=strategy,
        scenario=scenario,
        reports=reports,
        partition=partition,
        fee_provenance=tuple(dict(item) for item in fee_provenance),
        artifact=artifact_payload,
        predictions=prediction_payload,
        decision_digest=decision_digest,
        prediction_digest=prediction_digest,
        scenario_digest=scenario_digest,
        information_status=dict(information_status or {"status": "not_applicable"}),
        metrics=metrics,
        run_identity=run_identity,
        result_digest=result_digest,
    )


def _write_run_partition(run: EvaluationRun) -> None:
    reports = run.reports
    payloads: dict[str, object] = {
        "account.json": reports.account,
        "associations.json": list(reports.associations),
        "decisions.json": list(reports.decisions),
        "fee-provenance.json": list(run.fee_provenance),
        "fills.json": list(reports.fills),
        "funding.json": reports.funding,
        "metrics.json": run.metrics,
        "orders.json": list(reports.orders),
        "positions.json": list(reports.positions),
        "result.json": reports.result,
        "summary.json": reports.summary,
        "terminal.json": reports.terminal,
    }
    if isinstance(reports, Stage3ModelReports):
        payloads["feature-references.json"] = list(reports.feature_references)
    if run.artifact is not None:
        payloads["artifact.json"] = run.artifact
        payloads["predictions.json"] = list(run.predictions)
    for name, payload in payloads.items():
        _write_json_exclusive(run.partition / name, payload)
    manifest: dict[str, object] = {
        "schema": STAGE3_EVALUATION_RUN_SCHEMA,
        "artifact": run.artifact,
        "decision_digest": run.decision_digest,
        "fee_provenance": [dict(item) for item in run.fee_provenance],
        "fee_provenance_digest": _digest(run.fee_provenance),
        "fold": run.fold.payload(),
        "metrics": run.metrics,
        "prediction_digest": run.prediction_digest,
        "result_digest": run.result_digest,
        "run_identity": run.run_identity,
        "scenario": run.scenario.payload(),
        "scenario_digest": run.scenario_digest,
        "sensitivity_information": run.information_status,
        "strategy": run.strategy,
    }
    manifest["manifest_digest"] = _digest(manifest)
    _write_json_exclusive(run.partition / "manifest.json", manifest)


def _decision_digest(
    decisions: Sequence[Mapping[str, object]], strategy: StrategyName
) -> str:
    common = (
        "close",
        "decision_id",
        "decision_ts",
        "feature_schema_digest",
        "instrument_id",
        "signal",
        "target_qty",
    )
    model = (
        "artifact_id",
        "base_round_trip_cost_threshold",
        "ordered_feature_digest",
        "prediction_id",
        "score",
        "target_state",
    )
    source = ("ret_24h",) if strategy == "momentum" else model
    projected: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()
    for decision in decisions:
        item = {key: decision.get(key) for key in (*common, *source)}
        if any(value is None for value in item.values()):
            raise Stage3EvaluationError("target-decision contract is incomplete")
        key = (cast(str, item["instrument_id"]), cast(int, item["decision_ts"]))
        if key in seen:
            raise Stage3EvaluationError("target-decision contract is duplicated")
        seen.add(key)
        projected.append(item)
    if not projected:
        raise Stage3EvaluationError("target-decision contract is empty")
    projected.sort(
        key=lambda item: (
            cast(int, item["decision_ts"]),
            cast(str, item["instrument_id"]),
        )
    )
    return _digest(projected)


def _metrics(reports: CommonReports, *, fold: EvaluationFold) -> dict[str, object]:
    decision_times = sorted(
        {cast(int, item["decision_ts"]) for item in reports.decisions}
    )
    if not decision_times:
        raise Stage3EvaluationError("metrics require target decisions")
    raw_curve = reports.account.get("equity_curve")
    if not isinstance(raw_curve, list) or not all(
        isinstance(item, Mapping) for item in raw_curve
    ):
        raise Stage3EvaluationError("Nautilus per-decision equity curve is missing")
    curve = {
        cast(int, item["ts_event"]): item
        for item in cast(list[Mapping[str, object]], raw_curve)
        if isinstance(item.get("ts_event"), int)
    }
    if len(curve) != len(raw_curve) or sorted(curve) != decision_times:
        raise Stage3EvaluationError(
            "Nautilus equity curve does not match target decisions"
        )
    try:
        equities = [Decimal(cast(str, curve[ts]["total"])) for ts in decision_times]
        gross_exposures = [
            Decimal(cast(str, curve[ts]["gross_exposure"])) for ts in decision_times
        ]
    except (ArithmeticError, KeyError) as exc:
        raise Stage3EvaluationError("Nautilus equity curve is invalid") from exc
    if any(not value.is_finite() for value in (*equities, *gross_exposures)):
        raise Stage3EvaluationError("Nautilus equity curve is not finite")
    returns = [
        float(equities[index] / equities[index - 1] - 1)
        for index in range(1, len(equities))
        if equities[index - 1] != 0
    ]
    mean = sum(returns) / len(returns) if returns else 0.0
    variance = (
        sum((item - mean) ** 2 for item in returns) / len(returns) if returns else 0.0
    )
    downside = (
        math.sqrt(sum(min(item, 0.0) ** 2 for item in returns) / len(returns))
        if returns
        else 0.0
    )
    annualization = math.sqrt(ANNUALIZATION_PERIODS)
    sharpe = mean / math.sqrt(variance) * annualization if variance > 0 else 0.0
    sortino = mean / downside * annualization if downside > 0 else 0.0
    peak = equities[0]
    max_drawdown = Decimal(0)
    for equity in equities:
        peak = max(peak, equity)
        if peak:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    final_total = equities[-1]
    pnl = final_total - stage3_momentum.STAGE3_STARTING_USDT
    exposure_values = [
        gross / equity if equity else Decimal(0)
        for gross, equity in zip(gross_exposures, equities, strict=True)
    ]
    exposure = (
        sum(exposure_values, Decimal(0)) / len(exposure_values)
        if exposure_values
        else Decimal(0)
    )
    per_instrument = _per_instrument_metrics(reports)
    return {
        "commission": cast(str, reports.summary["total_commission"]),
        "exposure": str(exposure),
        "fold_id": fold.fold_id,
        "funding": cast(str, reports.summary["total_funding"]),
        "max_drawdown": str(max_drawdown),
        "per_instrument": per_instrument,
        "sharpe": repr(sharpe),
        "sortino": repr(sortino),
        "total_pnl": str(pnl),
        "total_return": str(pnl / stage3_momentum.STAGE3_STARTING_USDT),
        "trade_count": cast(int, reports.summary["trade_count"]),
        "turnover": cast(str, reports.summary["turnover"]),
        "window": {
            "end": _utc_text(fold.evaluation_end),
            "role": fold.role,
            "start": _utc_text(fold.evaluation_start),
        },
    }


def _per_instrument_metrics(reports: CommonReports) -> dict[str, object]:
    result: dict[str, object] = {}
    funding = _funding_by_instrument(reports)
    terminal = {
        cast(str, item["instrument_id"]): item
        for item in cast(Sequence[Mapping[str, object]], reports.terminal["positions"])
    }
    for instrument_id in STAGE2_INSTRUMENT_IDS:
        fills = [
            item for item in reports.fills if item["instrument_id"] == instrument_id
        ]
        positions = [
            item for item in reports.positions if item["instrument_id"] == instrument_id
        ]
        commission = sum(
            (
                _money_amount(cast(str, item["commission"]))
                for item in fills
                if item["commission"] is not None
            ),
            Decimal(0),
        )
        turnover = (
            sum(
                (
                    Decimal(cast(str, item["last_qty"]))
                    * Decimal(cast(str, item["last_px"]))
                    for item in fills
                ),
                Decimal(0),
            )
            / stage3_momentum.STAGE3_STARTING_USDT
        )
        realized = sum(
            (
                _money_amount(cast(str, item["realized_pnl"]))
                for item in positions
                if item["realized_pnl"] is not None
            ),
            Decimal(0),
        )
        result[instrument_id] = {
            "commission": str(commission),
            "fill_count": len(fills),
            "funding": str(funding[instrument_id]),
            "realized_pnl": str(realized),
            "terminal": terminal.get(instrument_id),
            "trade_count": sum(1 for item in positions if item["is_closed"] is True),
            "turnover": str(turnover),
        }
    return result


def _funding_account_deltas(
    reports: CommonReports,
) -> tuple[tuple[str, Decimal], ...]:
    result: list[tuple[str, Decimal]] = []
    timeline = reports.funding.get("events")
    if not isinstance(timeline, list):
        raise Stage3EvaluationError("Nautilus funding events are missing")
    for entry in timeline:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("events"), list):
            raise Stage3EvaluationError("Nautilus funding event is invalid")
        for raw in cast(list[object], entry["events"]):
            if not isinstance(raw, Mapping):
                raise Stage3EvaluationError("Nautilus funding instrument is invalid")
            instrument_id = raw.get("instrument_id")
            delta = raw.get("account_delta")
            if instrument_id not in STAGE2_INSTRUMENT_IDS or not isinstance(delta, str):
                raise Stage3EvaluationError(
                    "Nautilus per-instrument funding is incomplete"
                )
            try:
                value = Decimal(delta)
            except ArithmeticError as exc:
                raise Stage3EvaluationError(
                    "Nautilus per-instrument funding is invalid"
                ) from exc
            if not value.is_finite():
                raise Stage3EvaluationError(
                    "Nautilus per-instrument funding is not finite"
                )
            result.append((cast(str, instrument_id), value))
    return tuple(result)


def _funding_by_instrument(reports: CommonReports) -> dict[str, Decimal]:
    result = {instrument_id: Decimal(0) for instrument_id in STAGE2_INSTRUMENT_IDS}
    for instrument_id, value in _funding_account_deltas(reports):
        result[instrument_id] += value
    return result


def _fold_dispersion(
    runs: Sequence[tuple[StrategyName, Mapping[str, object]]],
) -> dict[str, object]:
    keys = (
        "total_return",
        "total_pnl",
        "sharpe",
        "sortino",
        "max_drawdown",
        "trade_count",
        "turnover",
        "exposure",
        "commission",
        "funding",
    )
    result: dict[str, object] = {}
    for strategy in cast(tuple[StrategyName, ...], ("momentum", "lightgbm")):
        strategy_runs = [metrics for name, metrics in runs if name == strategy]
        if len(strategy_runs) != len(EXPANDING_FOLD_SPECS):
            raise Stage3EvaluationError("base fold matrix is incomplete")
        metrics: dict[str, object] = {}
        for key in keys:
            values = [Decimal(str(metrics[key])) for metrics in strategy_runs]
            low = min(values)
            high = max(values)
            metrics[key] = {
                "max": str(high),
                "min": str(low),
                "range": str(high - low),
            }
        result[strategy] = metrics
    return result


def _run_accounting_fixtures(
    config: Stage3Config,
    *,
    acceptance_record_path: Path,
    fold: EvaluationFold,
) -> dict[str, object]:
    """Prove zero/base/2x fee and funding through real Nautilus accounting."""
    bind_accepted_stage2_catalog(config, acceptance_record_path=acceptance_record_path)
    fixture_end = min(fold.evaluation_start + timedelta(hours=17), fold.evaluation_end)
    base_loaded = stage3_momentum._load_native_stage3_data(
        config.catalog_path, start=fold.evaluation_start, end=fixture_end
    )
    decisions = _fixture_decisions(
        base_loaded.bars,
        base_loaded.instruments,
        start=fold.evaluation_start,
        end=fixture_end,
    )
    reports: dict[ScenarioName, Stage3MomentumReports] = {}
    for scenario in accounting_scenarios():
        loaded = stage3_momentum._load_native_stage3_data(
            config.catalog_path,
            start=fold.evaluation_start,
            end=fixture_end,
            maker_fee=scenario.maker_fee,
            taker_fee=scenario.taker_fee,
            funding_multiplier=scenario.funding_multiplier,
        )
        parameters = Stage3MomentumParameters(
            datetime_to_nanos(fold.evaluation_start),
            datetime_to_nanos(fixture_end),
            maker_fee=scenario.maker_fee,
            taker_fee=scenario.taker_fee,
            accounting_only_replay=True,
        )
        replay = Stage3DecisionReplayStrategy(parameters, frozen_decisions=decisions)
        reports[scenario.name] = stage3_momentum._run_engine(
            loaded, parameters, strategy=replay
        )
        replay.require_complete_replay()
    bind_accepted_stage2_catalog(config, acceptance_record_path=acceptance_record_path)
    base_commission = Decimal(cast(str, reports["base"].summary["total_commission"]))
    base_funding = Decimal(cast(str, reports["base"].summary["total_funding"]))
    if base_commission <= 0:
        raise Stage3EvaluationError("fee fixture did not produce a taker fill")
    if Decimal(cast(str, reports["zero_fee"].summary["total_commission"])) != 0:
        raise Stage3EvaluationError("zero-fee fixture did not produce 0x commission")
    if (
        Decimal(cast(str, reports["double_fee"].summary["total_commission"]))
        != base_commission * 2
    ):
        raise Stage3EvaluationError("double-fee fixture did not produce 2x commission")
    if base_funding == 0:
        raise Stage3EvaluationError("funding fixture did not cross a funding event")
    if Decimal(cast(str, reports["zero_funding"].summary["total_funding"])) != 0:
        raise Stage3EvaluationError("zero-funding fixture did not produce 0x funding")
    if (
        Decimal(cast(str, reports["double_funding"].summary["total_funding"]))
        != base_funding * 2
    ):
        raise Stage3EvaluationError("double-funding fixture did not produce 2x funding")
    return {
        "decision_digest": _decision_digest(decisions, "momentum"),
        "fee": {
            "base": str(base_commission),
            "double": str(base_commission * 2),
            "status": "proved_by_forced_taker_fill",
            "zero": "0",
        },
        "funding": {
            "base": str(base_funding),
            "double": str(base_funding * 2),
            "status": "proved_by_position_across_funding_event",
            "zero": "0",
        },
    }


def _fixture_decisions(
    bars: Sequence[Bar],
    instruments: Sequence[CryptoPerpetual],
    *,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, object], ...]:
    start_ns = datetime_to_nanos(start)
    end_ns = datetime_to_nanos(end)
    first_by_id: dict[str, Bar] = {}
    eligible = sorted(
        (bar for bar in bars if start_ns <= int(bar.ts_event) < end_ns),
        key=lambda bar: (int(bar.ts_event), str(bar.bar_type.instrument_id)),
    )
    for bar in eligible:
        first_by_id.setdefault(str(bar.bar_type.instrument_id), bar)
    instruments_by_id = {str(item.id): item for item in instruments}
    targets = {
        instrument_id: (
            target_quantity(
                signal="long",
                close=Decimal(str(first_by_id[instrument_id].close)),
                instrument=instruments_by_id[instrument_id],
                target_notional=MOMENTUM_TARGET_NOTIONAL_USDT,
            )
            if instrument_id == STAGE2_INSTRUMENT_IDS[0]
            else Decimal(0)
        )
        for instrument_id in STAGE2_INSTRUMENT_IDS
    }
    source: list[dict[str, object]] = []
    for bar in eligible:
        instrument_id = str(bar.bar_type.instrument_id)
        signal = "long" if targets[instrument_id] > 0 else "flat"
        target = targets[instrument_id]
        decision_ts = int(bar.ts_event)
        close = Decimal(str(bar.close))
        decision_id = hashlib.sha256(
            f"{instrument_id}|{decision_ts}|{signal}|{target}|{close}".encode()
        ).hexdigest()
        source.append(
            {
                "close": str(close),
                "decision_id": decision_id,
                "decision_ts": decision_ts,
                "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
                "instrument_id": instrument_id,
                "ret_24h": "1.0" if signal == "long" else "0.0",
                "signal": signal,
                "target_qty": str(target),
            }
        )
    if not source or set(first_by_id) != set(STAGE2_INSTRUMENT_IDS):
        raise Stage3EvaluationError("accounting fixture bars are incomplete")
    return tuple(source)


def _scenario_input_digest(
    scenario: AccountingScenario,
    *,
    reports: CommonReports,
    fee_provenance: Sequence[Mapping[str, object]],
) -> str:
    fees = sorted(
        (
            {
                "effective_maker_fee": item.get("effective_maker_fee"),
                "effective_taker_fee": item.get("effective_taker_fee"),
                "instrument_id": item.get("instrument_id"),
            }
            for item in fee_provenance
        ),
        key=lambda item: str(item["instrument_id"]),
    )
    funding: list[dict[str, object]] = []
    timeline = reports.funding.get("events")
    if not isinstance(timeline, list):
        raise Stage3EvaluationError("Nautilus funding input evidence is missing")
    for entry in timeline:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("events"), list):
            raise Stage3EvaluationError("Nautilus funding input evidence is invalid")
        for event in cast(list[object], entry["events"]):
            if not isinstance(event, Mapping):
                raise Stage3EvaluationError("Nautilus funding event is invalid")
            funding.append(
                {
                    "instrument_id": event.get("instrument_id"),
                    "rate": event.get("rate"),
                    "ts_event": event.get("ts_event"),
                }
            )
    funding.sort(
        key=lambda item: (int(cast(int, item["ts_event"])), str(item["instrument_id"]))
    )
    return _digest(
        {
            "fees": fees,
            "funding": funding,
            "scenario": scenario.payload(),
        }
    )


def _sensitivity_information(
    base: EvaluationRun, scenario: AccountingScenario
) -> dict[str, object]:
    if scenario.name in {"zero_fee", "double_fee"}:
        informative = cast(int, base.reports.summary["fill_count"]) > 0
        subject = "fee"
        reason = "base run has taker fills" if informative else "base run has no fills"
    else:
        informative = any(
            value != 0 for _, value in _funding_account_deltas(base.reports)
        )
        subject = "funding"
        reason = (
            "base run has native per-instrument funding exposure"
            if informative
            else "base run has no native per-instrument funding exposure"
        )
    return {
        "informative": informative,
        "reason": reason,
        "status": "informative" if informative else "not_informative",
        "subject": subject,
    }


def _reports_payload(reports: CommonReports) -> dict[str, object]:
    result: dict[str, object] = {
        "account": reports.account,
        "associations": list(reports.associations),
        "decisions": list(reports.decisions),
        "fills": list(reports.fills),
        "funding": reports.funding,
        "orders": list(reports.orders),
        "positions": list(reports.positions),
        "result": reports.result,
        "summary": reports.summary,
        "terminal": reports.terminal,
    }
    if isinstance(reports, Stage3ModelReports):
        result["feature_references"] = list(reports.feature_references)
        result["predictions"] = list(reports.predictions)
    return result


def _run_config_digest(
    fold: EvaluationFold, strategy: StrategyName, scenario: AccountingScenario
) -> str:
    return _digest(
        {
            "fold": fold.payload(),
            "scenario": scenario.payload(),
            "shared_execution_digest": _shared_execution_digest(),
            "strategy_digest": _strategy_config_digest(strategy),
        }
    )


def _freeze_final_test_config(
    config: Stage3Config,
    *,
    fold: EvaluationFold,
    scenarios: Sequence[AccountingScenario],
) -> dict[str, object]:
    if fold.role != "final_test":
        raise Stage3EvaluationError("final-test config requires the final fold")
    run_config_digests = {
        strategy: {
            scenario.name: _run_config_digest(fold, strategy, scenario)
            for scenario in scenarios
        }
        for strategy in cast(tuple[StrategyName, ...], ("momentum", "lightgbm"))
    }
    payload: dict[str, object] = {
        "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
        "input_digest": _digest(stage3_momentum._config_identity_payload(config)),
        "lightgbm_strategy_digest": _strategy_config_digest("lightgbm"),
        "momentum_strategy_digest": _strategy_config_digest("momentum"),
        "run_config_digests": run_config_digests,
        "shared_execution_digest": _shared_execution_digest(),
    }
    return {**payload, "final_test_config_digest": _digest(payload)}


def _require_frozen_final_test_config(
    frozen: Mapping[str, object] | None,
    *,
    config: Stage3Config,
    fold: EvaluationFold,
    strategy: StrategyName,
    scenario: AccountingScenario,
) -> None:
    if fold.role != "final_test":
        return
    expected = _freeze_final_test_config(
        config,
        fold=fold,
        scenarios=accounting_scenarios(),
    )
    if frozen is None or dict(frozen) != expected:
        raise Stage3EvaluationError("frozen final-test config has drifted")
    run_config_digests = frozen.get("run_config_digests")
    strategy_digests = (
        run_config_digests.get(strategy)
        if isinstance(run_config_digests, Mapping)
        else None
    )
    if not isinstance(strategy_digests, Mapping) or strategy_digests.get(
        scenario.name
    ) != _run_config_digest(fold, strategy, scenario):
        raise Stage3EvaluationError("final-test run config is not frozen")


def _shared_execution_digest() -> str:
    return _digest(
        {
            "account": {
                "base_currency": "USDT",
                "default_leverage": "1",
                "oms_type": "NETTING",
                "starting_balance": "100000",
            },
            "execution": {
                "bar_execution": True,
                "order_type": "MARKET",
                "terminal": "native_mark_valuation",
            },
            "sizing": str(MOMENTUM_TARGET_NOTIONAL_USDT),
        }
    )


def _strategy_config_digest(strategy: StrategyName) -> str:
    return _digest(
        {
            "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
            "strategy": strategy,
            "threshold": (
                "0.005"
                if strategy == "momentum"
                else str(BASE_ROUND_TRIP_COST_THRESHOLD)
            ),
        }
    )


def _require_parameter_identity(
    manifest: Mapping[str, object], revision: str, digest: str
) -> None:
    training = manifest.get("training")
    if not isinstance(training, Mapping) or (
        training.get("training_parameter_revision") != revision
        or training.get("training_parameter_digest") != digest
    ):
        raise Stage3EvaluationError("fold artifact parameter identity has drifted")


def _validate_fold_contract(folds: Sequence[EvaluationFold]) -> None:
    expected = (
        (
            "development-2022",
            "development",
            "2020-01-01T00:00:00Z",
            "2022-01-01T00:00:00Z",
            "2023-01-01T00:00:00Z",
        ),
        (
            "development-2023",
            "development",
            "2020-01-01T00:00:00Z",
            "2023-01-01T00:00:00Z",
            "2024-01-01T00:00:00Z",
        ),
        (
            "validation-2024",
            "validation",
            "2020-01-01T00:00:00Z",
            "2024-01-01T00:00:00Z",
            "2025-01-01T00:00:00Z",
        ),
        (
            "final-test-2025-2026",
            "final_test",
            "2020-01-01T00:00:00Z",
            "2025-01-01T00:00:00Z",
            "2026-09-01T00:00:00Z",
        ),
    )
    observed = tuple(
        (
            fold.fold_id,
            fold.role,
            _utc_text(fold.train_start),
            _utc_text(fold.evaluation_start),
            _utc_text(fold.evaluation_end),
        )
        for fold in folds
    )
    if observed != expected:
        raise Stage3EvaluationError("expanding-window fold contract has drifted")


def _require_new_evaluation_roots(config: Stage3Config) -> None:
    repository = _repository_root()
    roots = (config.evidence_root, config.run_root)
    for root in roots:
        if not root.is_absolute():
            raise Stage3EvaluationError("evaluation output roots must be absolute")
        resolved = root.resolve(strict=False)
        if resolved.exists():
            raise Stage3EvaluationError(
                "evaluation output roots must be new empty identity partitions"
            )
        if repository == resolved or repository in resolved.parents:
            raise Stage3EvaluationError("evaluation output roots must be external")
        if any(part.lower() == "latest" for part in resolved.parts):
            raise Stage3EvaluationError("evaluation output roots cannot use latest")
        catalog = config.catalog_path.resolve(strict=False)
        if (
            resolved == catalog
            or resolved in catalog.parents
            or catalog in resolved.parents
        ):
            raise Stage3EvaluationError("evaluation output overlaps the catalog")
    evidence, runs = (root.resolve(strict=False) for root in roots)
    if evidence == runs or evidence in runs.parents or runs in evidence.parents:
        raise Stage3EvaluationError("evaluation evidence and run roots overlap")


def _write_json_exclusive(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as exc:
        raise Stage3EvaluationError(
            f"evaluation output already exists: {path.name}"
        ) from exc


def _money_amount(value: str) -> Decimal:
    return Decimal(value.split()[0])


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise Stage3EvaluationError("evaluation timestamps must be UTC")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the fixed Stage 3 expanding-window evaluation matrix"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--acceptance-record", type=Path, required=True)
    args = parser.parse_args(argv)
    outcome = run_stage3_evaluation(
        load_stage3_config(args.config, repository_root=_repository_root()),
        acceptance_record_path=args.acceptance_record,
    )
    json.dump(
        {
            "manifest_digest": outcome.manifest["manifest_digest"],
            "result_digest": outcome.result_digest,
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
