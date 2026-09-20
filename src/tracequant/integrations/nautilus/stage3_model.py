from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final, cast

import polars as pl
from nautilus_trader.model import CryptoPerpetual

from tracequant.integrations import nautilus as nautilus_integration
from tracequant.integrations.nautilus import UPSTREAM_RELEASE_IDENTITY, stage2_artifact
from tracequant.integrations.nautilus import stage2_btceth as nautilus_stage2_btceth
from tracequant.integrations.nautilus import stage3_momentum as shared_runner
from tracequant.integrations.nautilus.strategies import stage3_model as model_strategy
from tracequant.integrations.nautilus.strategies import (
    stage3_momentum as execution_strategy,
)
from tracequant.integrations.nautilus.strategies.stage3_model import (
    BASE_ROUND_TRIP_COST_THRESHOLD,
    PREDICTION_ATOL,
    PREDICTION_RTOL,
    Stage3LightGBMStrategy,
    Stage3ModelError,
    Stage3ModelParameters,
    model_signal,
    ordered_feature_digest,
)
from tracequant.research import source_schema as research_source_schema
from tracequant.research import stage3_artifacts, stage3_features
from tracequant.research import views as research_views
from tracequant.research.stage3_artifacts import ArtifactWindow, Stage2ArtifactIdentity
from tracequant.research.stage3_features import (
    FEATURE_ATOL,
    FEATURE_LOOKBACK_HOURS,
    FEATURE_NAMES,
    FEATURE_RTOL,
    FEATURE_SCHEMA_DIGEST,
    FeatureObservation,
    Stage3Config,
    load_accepted_feature_window,
    load_stage3_config,
)
from tracequant.source_data import stage2_btceth as source_stage2_btceth
from tracequant.source_data.stage2_btceth import datetime_to_nanos, parse_utc

STAGE3_MODEL_SCHEMA: Final = "tracequant-stage3-lightgbm-strategy-v1"
STAGE3_REQUIREMENTS_RELATIVE_PATH: Final = (
    "docs/product/stage-3-strategy-and-model-requirements.md"
)
STAGE3_REQUIREMENTS_BASE_SHA: Final = "096dc68237ff94797e76473c7b7773028eba94bb"
STAGE3_REQUIREMENTS_BLOB_SHA: Final = "bceb070e4c15580b3a4987f29bbfbee705e51659"
STAGE3_MODEL_TRAIN_START: Final = "2020-01-01T00:00:00Z"
STAGE3_MODEL_START: Final = "2022-01-01T00:00:00Z"
STAGE3_MODEL_END: Final = "2023-01-01T00:00:00Z"


@dataclass(frozen=True)
class Stage3ModelReports:
    feature_references: tuple[dict[str, object], ...]
    predictions: tuple[dict[str, object], ...]
    decisions: tuple[dict[str, object], ...]
    associations: tuple[dict[str, object], ...]
    orders: tuple[dict[str, object], ...]
    fills: tuple[dict[str, object], ...]
    positions: tuple[dict[str, object], ...]
    account: dict[str, object]
    result: dict[str, object]
    summary: dict[str, object]
    funding: dict[str, object]
    terminal: dict[str, object]


@dataclass(frozen=True)
class Stage3ModelOutcome:
    artifact: dict[str, object]
    reports: Stage3ModelReports
    partition: Path
    run_identity: str
    result_digest: str


def base_model_parameters() -> Stage3ModelParameters:
    return Stage3ModelParameters(
        evaluation_start_ns=datetime_to_nanos(parse_utc(STAGE3_MODEL_START)),
        evaluation_end_ns=datetime_to_nanos(parse_utc(STAGE3_MODEL_END)),
    )


def run_stage3_model_backtest(
    config: Stage3Config,
    *,
    acceptance_record_path: Path,
    artifact_partition: Path,
    parameter_record_path: Path,
) -> Stage3ModelOutcome:
    """Run the fixed 2022 LightGBM Strategy through Nautilus accounting."""
    parameters = base_model_parameters()
    parameters.validate()
    try:
        shared_runner._require_runtime()
        _require_requirements_baseline()
        shared_runner._require_external_run_root(
            config.run_root, catalog_path=config.catalog_path
        )
        shared_runner._claim_run_root(config.run_root)
    except Exception as exc:
        if isinstance(exc, Stage3ModelError):
            raise
        raise Stage3ModelError(f"model run admission failed: {exc}") from exc
    try:
        start = parse_utc(STAGE3_MODEL_START)
        end = parse_utc(STAGE3_MODEL_END)
        context_start = start - timedelta(hours=FEATURE_LOOKBACK_HOURS)
        expected = load_accepted_feature_window(
            config,
            acceptance_record_path=acceptance_record_path,
            start=context_start,
            end=end,
            decision_start=start,
            mode="evaluation",
        )
        loaded = shared_runner._load_native_stage3_data(
            config.catalog_path,
            start=context_start,
            end=end,
        )
        strategy = Stage3LightGBMStrategy(
            parameters,
            artifact_partition=artifact_partition,
            parameter_record_path=parameter_record_path,
            expected_stage2_identity=_stage2_identity(config),
            expected_window=_expected_artifact_window(),
            repository_root=_repository_root(),
        )
        shared_reports = shared_runner._run_engine(
            loaded,
            parameters.execution_parameters(),
            strategy=strategy,
        )
        feature_references = _require_prediction_parity(
            strategy,
            expected,
            loaded.instruments,
        )
        reports = Stage3ModelReports(
            feature_references=feature_references,
            predictions=tuple(dict(item) for item in strategy.predictions),
            decisions=shared_reports.decisions,
            associations=shared_reports.associations,
            orders=shared_reports.orders,
            fills=shared_reports.fills,
            positions=shared_reports.positions,
            account=shared_reports.account,
            result=shared_reports.result,
            summary=shared_reports.summary,
            funding=shared_reports.funding,
            terminal=shared_reports.terminal,
        )
        artifact = strategy.artifact_reference
        run_identity = _run_identity(config, parameters, artifact)
        result_digest = _canonical_digest(_business_result(reports, artifact))
        outcome = Stage3ModelOutcome(
            artifact=artifact,
            reports=reports,
            partition=config.run_root,
            run_identity=run_identity,
            result_digest=result_digest,
        )
        _write_outcome(outcome, config, parameters, loaded.fee_provenance)
        return outcome
    except Exception as exc:
        shared_runner._release_empty_run_root(config.run_root)
        if isinstance(exc, Stage3ModelError):
            raise
        raise Stage3ModelError(f"model Strategy run failed: {exc}") from exc


def canonical_business_result(outcome: Stage3ModelOutcome) -> dict[str, object]:
    return {
        **_business_result(outcome.reports, outcome.artifact),
        "result_digest": outcome.result_digest,
        "run_identity": outcome.run_identity,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 3 fixed-window LightGBM Nautilus backtest"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--acceptance-record", type=Path, required=True)
    parser.add_argument("--artifact-partition", type=Path, required=True)
    parser.add_argument("--parameter-record", type=Path, required=True)
    args = parser.parse_args(argv)
    outcome = run_stage3_model_backtest(
        load_stage3_config(args.config, repository_root=_repository_root()),
        acceptance_record_path=args.acceptance_record,
        artifact_partition=args.artifact_partition,
        parameter_record_path=args.parameter_record,
    )
    json.dump(canonical_business_result(outcome), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _require_requirements_baseline() -> None:
    path = _repository_root() / STAGE3_REQUIREMENTS_RELATIVE_PATH
    if not path.is_file():
        raise Stage3ModelError("Stage 3 requirements baseline is missing")
    content = path.read_bytes()
    header = f"blob {len(content)}\0".encode()
    blob_sha = hashlib.sha1(header + content, usedforsecurity=False).hexdigest()
    if blob_sha != STAGE3_REQUIREMENTS_BLOB_SHA:
        raise Stage3ModelError("Stage 3 requirements baseline blob has drifted")


def _expected_artifact_window() -> ArtifactWindow:
    return ArtifactWindow(
        train_start=parse_utc(STAGE3_MODEL_TRAIN_START),
        train_end=parse_utc(STAGE3_MODEL_START),
        evaluation_start=parse_utc(STAGE3_MODEL_START),
        evaluation_end=parse_utc(STAGE3_MODEL_END),
        role="development",
    )


def _stage2_identity(config: Stage3Config) -> Stage2ArtifactIdentity:
    return Stage2ArtifactIdentity(
        dataset_id=config.dataset_id,
        acceptance_digest=config.acceptance_digest,
        dataset_digest=config.dataset_digest,
        source_manifest_digest=config.source_manifest_digest,
        market_data_manifest_digest=config.market_data_manifest_digest,
        instrument_snapshot_checksum=config.instrument_snapshot_checksum,
        runtime_identity=config.runtime_identity,
    )


def _require_prediction_parity(
    strategy: Stage3LightGBMStrategy,
    expected: Mapping[str, Sequence[FeatureObservation]],
    instruments: Sequence[CryptoPerpetual],
) -> tuple[dict[str, object], ...]:
    expected_rows = {
        (instrument_id, item.decision_ts): item
        for instrument_id, rows in expected.items()
        for item in rows
        if item.status == "ready" and item.tradable
    }
    if len(strategy.predictions) != len(expected_rows) or len(
        strategy.decisions
    ) != len(expected_rows):
        raise Stage3ModelError("runtime prediction count differs from feature rows")
    batch_rows: list[tuple[float, ...]] = []
    references: list[dict[str, object]] = []
    decisions_by_id = {
        cast(str, item["decision_id"]): item for item in strategy.decisions
    }
    for prediction in strategy.predictions:
        key = (
            cast(str, prediction["instrument_id"]),
            cast(int, prediction["decision_ts"]),
        )
        expected_row = expected_rows.get(key)
        runtime_values = strategy.feature_vectors.get(key)
        if expected_row is None or runtime_values is None:
            raise Stage3ModelError("runtime prediction has no accepted feature row")
        batch_values = expected_row.require_ready()
        if any(
            not math.isclose(
                runtime,
                batch,
                abs_tol=FEATURE_ATOL,
                rel_tol=FEATURE_RTOL,
            )
            for runtime, batch in zip(runtime_values, batch_values, strict=True)
        ):
            raise Stage3ModelError("runtime features differ from accepted batch")
        digest = ordered_feature_digest(runtime_values)
        if prediction["ordered_feature_digest"] != digest:
            raise Stage3ModelError("prediction feature digest is inconsistent")
        decision = decisions_by_id.get(cast(str, prediction["decision_id"]))
        if decision is None:
            raise Stage3ModelError("prediction has no target decision")
        for field in (
            "artifact_id",
            "base_round_trip_cost_threshold",
            "decision_ts",
            "feature_schema_digest",
            "instrument_id",
            "ordered_feature_digest",
            "prediction_id",
            "score",
            "target_qty",
            "target_state",
        ):
            if decision.get(field) != prediction[field]:
                raise Stage3ModelError("prediction and decision identity differ")
        batch_rows.append(batch_values)
        references.append(
            {
                "decision_ts": key[1],
                "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
                "instrument_id": key[0],
                "ordered_feature_digest": digest,
            }
        )
    batch_frame = pl.DataFrame(
        {
            name: [values[index] for values in batch_rows]
            for index, name in enumerate(FEATURE_NAMES)
        },
        schema={name: pl.Float64 for name in FEATURE_NAMES},
    )
    batch_scores = strategy.predictor.predict(
        batch_frame,
        feature_schema_digest=FEATURE_SCHEMA_DIGEST,
    )
    instruments_by_id = {str(item.id): item for item in instruments}
    for prediction, batch_score in zip(strategy.predictions, batch_scores, strict=True):
        runtime_score = float(cast(str, prediction["score"]))
        if not math.isclose(
            runtime_score,
            batch_score,
            abs_tol=PREDICTION_ATOL,
            rel_tol=PREDICTION_RTOL,
        ):
            raise Stage3ModelError("runtime prediction differs from batch prediction")
        state = model_signal(batch_score, BASE_ROUND_TRIP_COST_THRESHOLD)
        decision = decisions_by_id[cast(str, prediction["decision_id"])]
        target = execution_strategy.target_quantity(
            signal=state,
            close=Decimal(cast(str, decision["close"])),
            instrument=instruments_by_id[cast(str, prediction["instrument_id"])],
            target_notional=execution_strategy.MOMENTUM_TARGET_NOTIONAL_USDT,
        )
        if (
            prediction["target_state"] != state
            or Decimal(cast(str, prediction["target_qty"])) != target
        ):
            raise Stage3ModelError("runtime model target differs from batch prediction")
    return tuple(references)


def _business_result(
    reports: Stage3ModelReports,
    artifact: Mapping[str, object],
) -> dict[str, object]:
    business_artifact = {
        key: value for key, value in artifact.items() if key != "provenance_differences"
    }
    return {
        "account": reports.account,
        "artifact": business_artifact,
        "associations": list(reports.associations),
        "decisions": list(reports.decisions),
        "feature_references": list(reports.feature_references),
        "fills": list(reports.fills),
        "funding": reports.funding,
        "orders": list(reports.orders),
        "positions": list(reports.positions),
        "predictions": list(reports.predictions),
        "result": reports.result,
        "summary": reports.summary,
        "terminal": reports.terminal,
    }


def _run_identity(
    config: Stage3Config,
    parameters: Stage3ModelParameters,
    artifact: Mapping[str, object],
) -> str:
    return _canonical_digest(
        {
            "artifact_id": artifact["artifact_id"],
            "config_identity": shared_runner._config_identity_payload(config),
            "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
            "parameters": _parameter_payload(parameters),
            "relevant_code_digest": _relevant_code_digest(),
            "requirements_baseline": _requirements_payload(),
            "schema": STAGE3_MODEL_SCHEMA,
        }
    )


def _parameter_payload(parameters: Stage3ModelParameters) -> dict[str, object]:
    return {
        "base_funding_multiplier": "1",
        "base_round_trip_cost_threshold": str(
            parameters.base_round_trip_cost_threshold
        ),
        "evaluation_end_ns": parameters.evaluation_end_ns,
        "evaluation_start_ns": parameters.evaluation_start_ns,
        "instrument_ids": list(parameters.instrument_ids),
        "maker_fee": str(parameters.maker_fee),
        "oms_type": "NETTING",
        "order_type": "MARKET",
        "starting_balance_usdt": str(shared_runner.STAGE3_STARTING_USDT),
        "taker_fee": str(parameters.taker_fee),
        "target_notional_usdt": str(parameters.target_notional_usdt),
    }


def _write_outcome(
    outcome: Stage3ModelOutcome,
    config: Stage3Config,
    parameters: Stage3ModelParameters,
    fee_provenance: Sequence[Mapping[str, object]],
) -> None:
    fee_payload = [dict(item) for item in fee_provenance]
    payloads: dict[str, object] = {
        "account.json": outcome.reports.account,
        "artifact.json": outcome.artifact,
        "associations.json": list(outcome.reports.associations),
        "decisions.json": list(outcome.reports.decisions),
        "feature-references.json": list(outcome.reports.feature_references),
        "fee-provenance.json": fee_payload,
        "fills.json": list(outcome.reports.fills),
        "funding.json": outcome.reports.funding,
        "orders.json": list(outcome.reports.orders),
        "positions.json": list(outcome.reports.positions),
        "predictions.json": list(outcome.reports.predictions),
        "result.json": outcome.reports.result,
        "stage3_partition_identity.json": {
            "acceptance_digest": config.acceptance_digest,
            "artifact_id": outcome.artifact["artifact_id"],
            "dataset_id": config.dataset_id,
            "runtime_identity": config.runtime_identity,
        },
        "summary.json": outcome.reports.summary,
        "terminal.json": outcome.reports.terminal,
    }
    for name, payload in payloads.items():
        _write_json_exclusive(outcome.partition / name, payload)
    _write_json_exclusive(
        outcome.partition / "manifest.json",
        {
            "acceptance_digest": config.acceptance_digest,
            "artifact": outcome.artifact,
            "dataset_digest": config.dataset_digest,
            "dataset_id": config.dataset_id,
            "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
            "fee_provenance": fee_payload,
            "instrument_snapshot_checksum": config.instrument_snapshot_checksum,
            "live_not_approved": shared_runner.LIVE_NOT_APPROVED,
            "market_data_manifest_digest": config.market_data_manifest_digest,
            "offline_backtest_only": shared_runner.OFFLINE_BACKTEST_ONLY,
            "parameters": _parameter_payload(parameters),
            "relevant_code_digest": _relevant_code_digest(),
            "requirements_baseline": _requirements_payload(),
            "result_digest": outcome.result_digest,
            "run_identity": outcome.run_identity,
            "runtime_identity": UPSTREAM_RELEASE_IDENTITY,
            "schema": STAGE3_MODEL_SCHEMA,
            "source_manifest_digest": config.source_manifest_digest,
        },
    )


def _write_json_exclusive(path: Path, payload: object) -> None:
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as exc:
        raise Stage3ModelError(
            f"run output already exists and cannot be overwritten: {path.name}"
        ) from exc


def _requirements_payload() -> dict[str, str]:
    return {
        "base_sha": STAGE3_REQUIREMENTS_BASE_SHA,
        "blob_sha": STAGE3_REQUIREMENTS_BLOB_SHA,
        "path": STAGE3_REQUIREMENTS_RELATIVE_PATH,
    }


def _relevant_code_files() -> tuple[tuple[str, Path], ...]:
    return (
        ("integrations/nautilus/__init__.py", Path(nautilus_integration.__file__)),
        ("integrations/nautilus/stage2_artifact.py", Path(stage2_artifact.__file__)),
        (
            "integrations/nautilus/stage2_btceth.py",
            Path(nautilus_stage2_btceth.__file__),
        ),
        ("integrations/nautilus/stage3_model.py", Path(__file__)),
        (
            "integrations/nautilus/stage3_momentum.py",
            Path(shared_runner.__file__),
        ),
        (
            "integrations/nautilus/strategies/stage3_model.py",
            Path(model_strategy.__file__),
        ),
        (
            "integrations/nautilus/strategies/stage3_momentum.py",
            Path(execution_strategy.__file__),
        ),
        ("research/source_schema.py", Path(research_source_schema.__file__)),
        ("research/stage3_artifacts.py", Path(stage3_artifacts.__file__)),
        ("research/stage3_features.py", Path(stage3_features.__file__)),
        ("research/views.py", Path(research_views.__file__)),
        ("source_data/stage2_btceth.py", Path(source_stage2_btceth.__file__)),
    )


def _relevant_code_digest(
    files: Sequence[tuple[str, Path]] | None = None,
) -> str:
    paths = _relevant_code_files() if files is None else tuple(files)
    return _canonical_digest(
        [
            {"name": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for name, path in sorted(paths)
        ]
    )


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


if __name__ == "__main__":
    raise SystemExit(main())
