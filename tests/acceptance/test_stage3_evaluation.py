from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import polars as pl
import pytest

from tests.acceptance import test_stage3_features as feature_fixture
from tests.acceptance import test_stage3_model_strategy as model_fixture
from tests.acceptance import test_stage3_momentum as momentum_fixture
from tracequant.integrations.nautilus import stage3_evaluation as evaluation
from tracequant.integrations.nautilus import stage3_model
from tracequant.integrations.nautilus.stage3_evaluation import (
    EvaluationFold,
    EvaluationRun,
    run_stage3_evaluation,
)
from tracequant.integrations.nautilus.stage3_momentum import Stage3MomentumReports
from tracequant.integrations.nautilus.strategies import stage3_model as model_strategy
from tracequant.research import stage3_artifacts
from tracequant.research.stage3_artifacts import synthetic_fixture_provenance
from tracequant.research.stage3_features import Stage3Config
from tracequant.source_data.stage2_btceth import (
    STAGE2_INSTRUMENT_IDS,
    datetime_to_nanos,
)


def test_stage3_evaluation_contract_freezes_folds_and_accounting_scenarios() -> None:
    assert [fold.payload() for fold in evaluation.expanding_folds()] == [
        {
            "fold_id": "development-2022",
            "role": "development",
            "train_start": "2020-01-01T00:00:00Z",
            "train_end": "2022-01-01T00:00:00Z",
            "purge_boundary": "2022-01-01T00:00:00Z",
            "evaluation_start": "2022-01-01T00:00:00Z",
            "evaluation_end": "2023-01-01T00:00:00Z",
        },
        {
            "fold_id": "development-2023",
            "role": "development",
            "train_start": "2020-01-01T00:00:00Z",
            "train_end": "2023-01-01T00:00:00Z",
            "purge_boundary": "2023-01-01T00:00:00Z",
            "evaluation_start": "2023-01-01T00:00:00Z",
            "evaluation_end": "2024-01-01T00:00:00Z",
        },
        {
            "fold_id": "validation-2024",
            "role": "validation",
            "train_start": "2020-01-01T00:00:00Z",
            "train_end": "2024-01-01T00:00:00Z",
            "purge_boundary": "2024-01-01T00:00:00Z",
            "evaluation_start": "2024-01-01T00:00:00Z",
            "evaluation_end": "2025-01-01T00:00:00Z",
        },
        {
            "fold_id": "final-test-2025-2026",
            "role": "final_test",
            "train_start": "2020-01-01T00:00:00Z",
            "train_end": "2025-01-01T00:00:00Z",
            "purge_boundary": "2025-01-01T00:00:00Z",
            "evaluation_start": "2025-01-01T00:00:00Z",
            "evaluation_end": "2026-09-01T00:00:00Z",
        },
    ]
    assert [scenario.payload() for scenario in evaluation.accounting_scenarios()] == [
        {
            "name": "base",
            "maker_fee": "0.0002",
            "taker_fee": "0.0004",
            "funding_multiplier": "1",
        },
        {
            "name": "zero_fee",
            "maker_fee": "0",
            "taker_fee": "0",
            "funding_multiplier": "1",
        },
        {
            "name": "double_fee",
            "maker_fee": "0.0004",
            "taker_fee": "0.0008",
            "funding_multiplier": "1",
        },
        {
            "name": "zero_funding",
            "maker_fee": "0.0002",
            "taker_fee": "0.0004",
            "funding_multiplier": "0",
        },
        {
            "name": "double_funding",
            "maker_fee": "0.0002",
            "taker_fee": "0.0004",
            "funding_multiplier": "2",
        },
    ]


def _install_real_fixture_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    start = feature_fixture.DATASET_START
    folds = (
        EvaluationFold(
            "development-a",
            "development",
            start,
            start + timedelta(days=8),
            start + timedelta(days=11),
        ),
        EvaluationFold(
            "development-b",
            "development",
            start,
            start + timedelta(days=11),
            start + timedelta(days=14),
        ),
        EvaluationFold(
            "validation",
            "validation",
            start,
            start + timedelta(days=14),
            start + timedelta(days=17),
        ),
        EvaluationFold(
            "final-test",
            "final_test",
            start,
            start + timedelta(days=17),
            feature_fixture.EVALUATION_END,
        ),
    )
    monkeypatch.setattr(evaluation, "expanding_folds", lambda: folds)
    monkeypatch.setattr(evaluation, "_validate_fold_contract", lambda value: None)

    def train(
        config: Stage3Config,
        *,
        acceptance_record_path: Path,
        partition: Path,
        parameter_record: Path,
        fold: EvaluationFold,
        provenance: stage3_artifacts.TrainingProvenance,
    ) -> dict[str, object]:
        del acceptance_record_path
        identity = model_fixture._bind_artifact_identity(monkeypatch, config)
        frame = model_fixture._training_frame(fold.window).filter(
            pl.col("label_end_ts") < datetime_to_nanos(fold.evaluation_start)
        )
        return stage3_artifacts.train_fixture_lightgbm_artifact(
            frame,
            output_partition=partition,
            parameter_record_path=parameter_record,
            stage2_identity=identity,
            window=fold.window,
            provenance=provenance,
            repository_root=evaluation._repository_root(),
        )

    def load_fixture(
        artifact_partition: Path,
        *,
        parameter_record_path: Path,
        expected_stage2_identity: stage3_artifacts.Stage2ArtifactIdentity,
        repository_root: Path,
    ) -> stage3_artifacts.LightGBMArtifactPredictor:
        return stage3_artifacts._load_lightgbm_artifact(
            artifact_partition,
            parameter_record_path=parameter_record_path,
            expected_stage2_identity=expected_stage2_identity,
            repository_root=repository_root,
            expected_provenance_kind="synthetic_fixture",
        )

    monkeypatch.setattr(evaluation, "_train_fold_artifact", train)
    monkeypatch.setattr(model_strategy, "load_lightgbm_artifact", load_fixture)


def test_stage3_evaluation_compares_both_strategies_with_accounting_only_sensitivity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog, record_path, template, artifact_lock = momentum_fixture._accepted_fixture(
        monkeypatch, tmp_path
    )
    _install_real_fixture_matrix(monkeypatch)
    provenance = synthetic_fixture_provenance(created_at="2026-01-01T00:00:00Z")

    def config(name: str) -> Stage3Config:
        base = feature_fixture._accepted_config(
            template,
            catalog,
            tmp_path / f"{name}-unused",
            artifact_lock_path=artifact_lock,
        )
        return Stage3Config(
            **{
                **base.__dict__,
                "evidence_root": tmp_path / f"{name}-evidence",
                "run_root": tmp_path / f"{name}-runs",
            }
        )

    first = run_stage3_evaluation(
        config("first"),
        acceptance_record_path=record_path,
        provenance=provenance,
    )
    second = run_stage3_evaluation(
        config("second"),
        acceptance_record_path=record_path,
        provenance=provenance,
    )

    base_runs = cast(list[Mapping[str, object]], first.manifest["base_runs"])
    sensitivity = cast(list[Mapping[str, object]], first.manifest["sensitivity_runs"])
    assert len(base_runs) == 4 * 2
    assert len(sensitivity) == 2 * 4
    assert {item["strategy"] for item in base_runs} == {"momentum", "lightgbm"}
    assert {
        cast(Mapping[str, object], item["scenario"])["name"] for item in sensitivity
    } == {
        "zero_fee",
        "double_fee",
        "zero_funding",
        "double_funding",
    }
    assert {
        cast(Mapping[str, object], item["fold"])["role"] for item in sensitivity
    } == {"final_test"}
    final_base = {
        cast(str, item["strategy"]): item
        for item in base_runs
        if cast(Mapping[str, object], item["fold"])["role"] == "final_test"
    }
    for item in sensitivity:
        base = final_base[cast(str, item["strategy"])]
        assert item["decision_digest"] == base["decision_digest"]
        if item["strategy"] == "lightgbm":
            assert item["prediction_digest"] == base["prediction_digest"]
    assert first.result_digest == second.result_digest
    assert first.manifest["fold_dispersion"] == second.manifest["fold_dispersion"]
    frozen = cast(Mapping[str, object], first.manifest["frozen_final_test_config"])
    frozen_digest_payload = dict(frozen)
    frozen_digest = frozen_digest_payload.pop("final_test_config_digest")
    assert frozen_digest == evaluation._digest(frozen_digest_payload)
    run_config_digests = cast(
        Mapping[str, Mapping[str, str]], frozen["run_config_digests"]
    )
    final_runs = [
        item
        for item in base_runs
        if cast(Mapping[str, object], item["fold"])["role"] == "final_test"
    ] + sensitivity
    for item in final_runs:
        scenario = cast(Mapping[str, str], item["scenario"])["name"]
        assert (
            item["config_digest"]
            == run_config_digests[cast(str, item["strategy"])][scenario]
        )
    drifted = {**frozen, "feature_schema_digest": "drifted"}
    with pytest.raises(
        evaluation.Stage3EvaluationError,
        match="frozen final-test config has drifted",
    ):
        evaluation._require_frozen_final_test_config(
            drifted,
            config=config("drift-check"),
            fold=evaluation.expanding_folds()[-1],
            strategy="momentum",
            scenario=evaluation.base_scenario(),
        )
    assert (first.partition / "manifest.json").is_file()
    assert (
        cast(
            Mapping[str, object],
            cast(Mapping[str, object], first.manifest["accounting_fixtures"])["fee"],
        )["status"]
        == "proved_by_forced_taker_fill"
    )
    for item in final_runs:
        partition = first.partition / cast(str, item["partition"])
        account = json.loads((partition / "account.json").read_text(encoding="utf-8"))
        decisions = json.loads(
            (partition / "decisions.json").read_text(encoding="utf-8")
        )
        assert len(account["equity_curve"]) == len(
            {decision["decision_ts"] for decision in decisions}
        )
        metrics = cast(Mapping[str, object], item["metrics"])
        assert Decimal(cast(str, metrics["total_pnl"])) == (
            Decimal(account["equity_curve"][-1]["total"]) - Decimal("100000")
        )
        funding = json.loads((partition / "funding.json").read_text(encoding="utf-8"))
        assert all(
            "account_delta" in native
            for event in funding["events"]
            for native in event["events"]
        )


def test_accounting_fixture_proves_zero_base_and_double_scaling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog, record_path, template, artifact_lock = momentum_fixture._accepted_fixture(
        monkeypatch, tmp_path
    )
    config = feature_fixture._accepted_config(
        template,
        catalog,
        tmp_path / "fixture-unused",
        artifact_lock_path=artifact_lock,
    )
    fold = EvaluationFold(
        "fixture",
        "development",
        feature_fixture.DATASET_START,
        feature_fixture.EVALUATION_START,
        feature_fixture.EVALUATION_END,
    )
    result = evaluation._run_accounting_fixtures(
        config, acceptance_record_path=record_path, fold=fold
    )

    assert cast(Mapping[str, object], result["fee"])["status"] == (
        "proved_by_forced_taker_fill"
    )
    assert cast(Mapping[str, object], result["funding"])["status"] == (
        "proved_by_position_across_funding_event"
    )


def test_model_sensitivity_replays_frozen_predictions_and_decisions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog, record_path, template, artifact_lock = momentum_fixture._accepted_fixture(
        monkeypatch, tmp_path
    )
    base_config = feature_fixture._accepted_config(
        template,
        catalog,
        tmp_path / "model-base",
        artifact_lock_path=artifact_lock,
    )
    artifact, parameters, _ = model_fixture._fixture_artifact(
        monkeypatch, tmp_path, base_config
    )
    outcome = stage3_model.run_stage3_model_backtest(
        base_config,
        acceptance_record_path=record_path,
        artifact_partition=artifact,
        parameter_record_path=parameters,
    )
    fold = EvaluationFold(
        "fixture",
        "development",
        feature_fixture.DATASET_START,
        feature_fixture.EVALUATION_START,
        feature_fixture.EVALUATION_END,
    )
    base = evaluation._build_run(
        fold=fold,
        strategy="lightgbm",
        scenario=evaluation.base_scenario(),
        reports=outcome.reports,
        partition=outcome.partition,
        fee_provenance=(),
        artifact=outcome.artifact,
        predictions=outcome.reports.predictions,
    )
    replay_config = feature_fixture._accepted_config(
        template,
        catalog,
        tmp_path / "model-double-fee",
        artifact_lock_path=artifact_lock,
    )
    replay = evaluation._run_sensitivity_scenario(
        replay_config,
        acceptance_record_path=record_path,
        fold=fold,
        strategy="lightgbm",
        scenario=evaluation.sensitivity_scenarios()[1],
        base=base,
    )

    assert replay.decision_digest == base.decision_digest
    assert replay.prediction_digest == base.prediction_digest
    assert replay.artifact == base.artifact
    assert Decimal(cast(str, replay.reports.summary["total_commission"])) == (
        Decimal(cast(str, base.reports.summary["total_commission"])) * 2
    )


def _reports_with_mark_to_market_and_offsetting_funding() -> Stage3MomentumReports:
    btc, eth = STAGE2_INSTRUMENT_IDS
    return Stage3MomentumReports(
        decisions=(
            {"decision_ts": 1},
            {"decision_ts": 2},
        ),
        associations=(),
        orders=(),
        fills=(),
        positions=(),
        account={
            "equity_curve": [
                {
                    "gross_exposure": "0",
                    "total": "100000",
                    "ts_event": 1,
                },
                {
                    "gross_exposure": "10000",
                    "total": "100125",
                    "ts_event": 2,
                },
            ],
            "total": "100000 USDT",
        },
        result={},
        summary={
            "fill_count": 0,
            "total_commission": "0",
            "total_funding": "0",
            "trade_count": 0,
            "turnover": "0",
        },
        funding={
            "events": [
                {
                    "events": [
                        {"account_delta": "5", "instrument_id": btc},
                        {"account_delta": "-5", "instrument_id": eth},
                    ],
                    "ts_event": 1,
                }
            ],
            "total_funding": "0",
        },
        terminal={
            "positions": [
                {
                    "instrument_id": btc,
                    "unrealized_pnl": "125 USDT",
                }
            ]
        },
    )


def test_metrics_use_nautilus_mark_to_market_equity_curve() -> None:
    reports = _reports_with_mark_to_market_and_offsetting_funding()
    metrics = evaluation._metrics(reports, fold=evaluation.expanding_folds()[0])

    assert metrics["total_pnl"] == "125"
    assert metrics["total_return"] == "0.00125"


def test_funding_information_uses_per_instrument_native_deltas() -> None:
    reports = _reports_with_mark_to_market_and_offsetting_funding()
    run = EvaluationRun(
        fold=evaluation.expanding_folds()[-1],
        strategy="momentum",
        scenario=evaluation.base_scenario(),
        reports=reports,
        partition=Path("unused"),
        fee_provenance=(),
        artifact=None,
        predictions=(),
        decision_digest="decision",
        prediction_digest=None,
        scenario_digest="scenario",
        information_status={},
        metrics={},
        run_identity="run",
        result_digest="result",
    )

    per_instrument = evaluation._per_instrument_metrics(reports)
    information = evaluation._sensitivity_information(
        run, evaluation.sensitivity_scenarios()[2]
    )

    assert (
        cast(Mapping[str, object], per_instrument[STAGE2_INSTRUMENT_IDS[0]])["funding"]
        == "5"
    )
    assert (
        cast(Mapping[str, object], per_instrument[STAGE2_INSTRUMENT_IDS[1]])["funding"]
        == "-5"
    )
    assert information == {
        "informative": True,
        "reason": "base run has native per-instrument funding exposure",
        "status": "informative",
        "subject": "funding",
    }
