from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, cast

from tracequant.integrations.nautilus import (
    UPSTREAM_RELEASE_IDENTITY,
    stage3_evaluation,
)
from tracequant.integrations.nautilus.stage3_evaluation import (
    Stage3EvaluationOutcome,
)
from tracequant.research import stage3_artifacts
from tracequant.research.stage3_artifacts import TrainingProvenance
from tracequant.research.stage3_features import (
    FEATURE_SCHEMA_DIGEST,
    STAGE2_ACCEPTANCE_DIGEST,
    STAGE2_ARTIFACT_LOCK_RELATIVE_PATH,
    STAGE2_DATASET_DIGEST,
    STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM,
    STAGE2_MARKET_DATA_MANIFEST_DIGEST,
    STAGE2_SOURCE_MANIFEST_DIGEST,
    STAGE3_CONFIG_SCHEMA,
    Stage3Config,
    bind_accepted_stage2_catalog,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_DATASET_ID,
    STAGE2_INSTRUMENT_IDS,
)

STAGE3_ACCEPTANCE_SCHEMA: Final = "tracequant-stage3-acceptance-v1"
STAGE3_ACCEPTANCE_RELATIVE_PATH: Final = (
    "docs/product/stage3-btceth-oos-acceptance.json"
)
STAGE2_ACCEPTANCE_RELATIVE_PATH: Final = (
    "docs/product/stage2-btceth-dataset-acceptance.json"
)
STAGE3_OOS_VERIFICATION_TEST: Final = (
    "tests/acceptance/test_stage3_oos.py::"
    "test_stage3_oos_rebuild_compares_both_strategies_from_one_catalog"
)
PRODUCT_STATUS: Final = ("OFFLINE_BACKTEST_ONLY", "LIVE_NOT_APPROVED")
_STRATEGIES: Final = ("momentum", "lightgbm")
_SENSITIVITY_SCENARIOS: Final = (
    "zero_fee",
    "double_fee",
    "zero_funding",
    "double_funding",
)
_ACCOUNTING_SCENARIOS: Final = ("base", *_SENSITIVITY_SCENARIOS)
_DIGEST_PATTERN: Final = r"[0-9a-f]{64}"
_WINDOWS_DRIVE_PATTERN: Final = r"^[A-Za-z]:[\\/]"
_REBUILD_CONTRACT_PATHS: Final = (
    "docs/product/stage-3-strategy-and-model-requirements.md",
    "docs/product/stage-3-execution-amendment-r1.md",
    "src/tracequant/integrations/nautilus/stage3_evaluation.py",
    "src/tracequant/integrations/nautilus/stage3_model.py",
    "src/tracequant/integrations/nautilus/stage3_momentum.py",
    "src/tracequant/integrations/nautilus/stage3_oos.py",
    "src/tracequant/integrations/nautilus/strategies/stage3_model.py",
    "src/tracequant/integrations/nautilus/strategies/stage3_momentum.py",
    "src/tracequant/research/stage3_artifacts.py",
    "src/tracequant/research/stage3_features.py",
)
_FEE_PROVENANCE_KEYS: Final = {
    "catalog_maker_fee",
    "catalog_maker_matches_base",
    "catalog_matches_snapshot",
    "catalog_taker_fee",
    "catalog_taker_matches_base",
    "effective_maker_fee",
    "effective_taker_fee",
    "instrument_id",
    "snapshot_maker_fee",
    "snapshot_maker_matches_base",
    "snapshot_taker_fee",
    "snapshot_taker_matches_base",
}
_METRIC_KEYS: Final = {
    "commission",
    "exposure",
    "funding",
    "max_drawdown",
    "per_instrument",
    "sharpe",
    "sortino",
    "total_pnl",
    "total_return",
    "trade_count",
    "turnover",
    "window",
}
_PER_INSTRUMENT_METRIC_KEYS: Final = {
    "commission",
    "fill_count",
    "funding",
    "realized_pnl",
    "trade_count",
    "turnover",
}
_DISPERSION_KEYS: Final = {
    "commission",
    "exposure",
    "funding",
    "max_drawdown",
    "sharpe",
    "sortino",
    "total_pnl",
    "total_return",
    "trade_count",
    "turnover",
}


class Stage3OosError(stage3_evaluation.Stage3EvaluationError):
    """Raised when the finite Stage 3 rebuild cannot produce trusted evidence."""


@dataclass(frozen=True)
class Stage3OosOutcome:
    evaluation: Stage3EvaluationOutcome
    acceptance_record_path: Path
    acceptance_record: dict[str, object]
    acceptance_digest: str


def rebuild_stage3_oos(
    config: Stage3Config,
    *,
    stage2_acceptance_record_path: Path,
) -> Stage3OosOutcome:
    """Run one formal rebuild and atomically publish the fixed tracked record."""
    repository_root = _repository_root()
    return _rebuild_stage3_oos(
        config,
        stage2_acceptance_record_path=stage2_acceptance_record_path,
        stage3_acceptance_record_path=(
            repository_root / STAGE3_ACCEPTANCE_RELATIVE_PATH
        ),
        provenance=None,
        expected_provenance_kind="formal_git",
        allow_synthetic_fixture=False,
    )


def _rebuild_stage3_oos_fixture(
    config: Stage3Config,
    *,
    stage2_acceptance_record_path: Path,
    stage3_acceptance_record_path: Path,
    provenance: TrainingProvenance,
) -> Stage3OosOutcome:
    """Exercise the real finite rebuild without presenting a fixture as formal evidence."""
    if provenance.kind != "synthetic_fixture":
        raise Stage3OosError("fixture rebuild requires synthetic fixture provenance")
    return _rebuild_stage3_oos(
        config,
        stage2_acceptance_record_path=stage2_acceptance_record_path,
        stage3_acceptance_record_path=stage3_acceptance_record_path,
        provenance=provenance,
        expected_provenance_kind="synthetic_fixture",
        allow_synthetic_fixture=True,
    )


def _rebuild_stage3_oos(
    config: Stage3Config,
    *,
    stage2_acceptance_record_path: Path,
    stage3_acceptance_record_path: Path,
    provenance: TrainingProvenance | None,
    expected_provenance_kind: str,
    allow_synthetic_fixture: bool,
) -> Stage3OosOutcome:
    repository_root = _repository_root()
    if allow_synthetic_fixture:
        if config.schema != STAGE3_CONFIG_SCHEMA:
            raise Stage3OosError("fixture rebuild config schema does not match")
    else:
        _require_locked_config(config)
    _require_config_paths(
        config,
        repository_root=repository_root,
        allow_synthetic_fixture=allow_synthetic_fixture,
    )
    record_path = _require_record_target(
        stage3_acceptance_record_path,
        config=config,
        repository_root=repository_root,
        allow_synthetic_fixture=allow_synthetic_fixture,
    )
    if not allow_synthetic_fixture:
        provenance = stage3_artifacts.capture_formal_provenance(repository_root)
    rebuild_contract_digest = _rebuild_contract_digest(repository_root)
    bind_accepted_stage2_catalog(
        config, acceptance_record_path=stage2_acceptance_record_path
    )
    evaluation = stage3_evaluation.run_stage3_evaluation(
        config,
        acceptance_record_path=stage2_acceptance_record_path,
        provenance=provenance,
    )
    bind_accepted_stage2_catalog(
        config, acceptance_record_path=stage2_acceptance_record_path
    )
    record = _build_acceptance_record(
        config,
        evaluation=evaluation,
        expected_provenance_kind=expected_provenance_kind,
        rebuild_contract_digest=rebuild_contract_digest,
    )
    _require_complete_stage3_acceptance_record(
        record,
        allow_synthetic_fixture=allow_synthetic_fixture,
        repository_root=repository_root,
    )
    if allow_synthetic_fixture:
        _write_json_exclusive(record_path, record)
    else:
        assert provenance is not None
        _require_unchanged_formal_repository(
            repository_root,
            provenance=provenance,
            rebuild_contract_digest=rebuild_contract_digest,
        )
        _write_json_replacing(record_path, record)
    return Stage3OosOutcome(
        evaluation=evaluation,
        acceptance_record_path=record_path,
        acceptance_record=record,
        acceptance_digest=cast(str, record["acceptance_digest"]),
    )


def _require_unchanged_formal_repository(
    repository_root: Path,
    *,
    provenance: TrainingProvenance,
    rebuild_contract_digest: str,
) -> None:
    try:
        current = stage3_artifacts.capture_formal_provenance(repository_root)
    except stage3_artifacts.Stage3ArtifactError as exc:
        raise Stage3OosError("formal rebuild repository identity has drifted") from exc
    if (
        current.git_sha != provenance.git_sha
        or current.uv_lock_checksum != provenance.uv_lock_checksum
        or _rebuild_contract_digest(repository_root) != rebuild_contract_digest
    ):
        raise Stage3OosError("formal rebuild repository identity has drifted")


def require_complete_stage3_acceptance_record(
    record: Mapping[str, object],
) -> None:
    """Validate a formal tracked Stage 3 record against the current contract."""
    _require_complete_stage3_acceptance_record(
        record,
        allow_synthetic_fixture=False,
        repository_root=_repository_root(),
    )


def stage3_acceptance_digest(record: Mapping[str, object]) -> str:
    return _digest(
        {key: value for key, value in record.items() if key != "acceptance_digest"}
    )


def rebuild_command_template() -> str:
    return (
        "uv run --frozen python -m "
        "tracequant.integrations.nautilus.stage3_oos rebuild-oos "
        "--catalog-path <ABSOLUTE_CATALOG_PATH> "
        "--evidence-root <ABSOLUTE_NEW_EVIDENCE_PARTITION> "
        "--run-root <ABSOLUTE_NEW_RUN_PARTITION> "
        f"--dataset-id {STAGE2_DATASET_ID} "
        f"--acceptance-digest {STAGE2_ACCEPTANCE_DIGEST} "
        f"--dataset-digest {STAGE2_DATASET_DIGEST} "
        f"--source-manifest-digest {STAGE2_SOURCE_MANIFEST_DIGEST} "
        f"--market-data-manifest-digest {STAGE2_MARKET_DATA_MANIFEST_DIGEST} "
        "--instrument-snapshot-checksum "
        f"{STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM} "
        f"--runtime-identity {UPSTREAM_RELEASE_IDENTITY}"
    )


def _build_acceptance_record(
    config: Stage3Config,
    *,
    evaluation: Stage3EvaluationOutcome,
    expected_provenance_kind: str,
    rebuild_contract_digest: str,
) -> dict[str, object]:
    manifest = evaluation.manifest
    if evaluation.partition.resolve(strict=False) != config.run_root.resolve(
        strict=False
    ) or evaluation.evidence_partition.resolve(
        strict=False
    ) != config.evidence_root.resolve(strict=False):
        raise Stage3OosError("evaluation output roots changed")
    if manifest.get("schema") != stage3_evaluation.STAGE3_EVALUATION_SCHEMA:
        raise Stage3OosError("evaluation manifest schema does not match")
    manifest_digest = _required_digest(
        manifest, "manifest_digest", "evaluation manifest"
    )
    if manifest_digest != _digest(
        {key: value for key, value in manifest.items() if key != "manifest_digest"}
    ):
        raise Stage3OosError("evaluation manifest digest does not match")
    if manifest.get("result_digest") != evaluation.result_digest:
        raise Stage3OosError("evaluation result digest changed")
    folds = _mapping_list(manifest.get("folds"), "evaluation folds")
    expected_folds = [fold.payload() for fold in stage3_evaluation.expanding_folds()]
    if folds != expected_folds:
        raise Stage3OosError("evaluation fold matrix is incomplete")
    base = _mapping_list(manifest.get("base_runs"), "evaluation base runs")
    sensitivity = _mapping_list(
        manifest.get("sensitivity_runs"), "evaluation sensitivity runs"
    )
    all_references = [*base, *sensitivity]
    runs = [
        _compact_run_reference(item, evaluation=evaluation) for item in all_references
    ]
    artifacts = _artifact_references(
        config,
        evaluation=evaluation,
        base_references=base,
        expected_provenance_kind=expected_provenance_kind,
    )
    parameter_revision = _required_string(
        manifest, "training_parameter_revision", "evaluation manifest"
    )
    parameter_digest = _required_digest(
        manifest, "training_parameter_digest", "evaluation manifest"
    )
    parameter_path = evaluation.evidence_partition / "training-parameters.json"
    try:
        frozen_parameters = stage3_artifacts.load_training_parameters(parameter_path)
    except stage3_artifacts.Stage3ArtifactError as exc:
        raise Stage3OosError("training parameter output is invalid") from exc
    if (
        frozen_parameters.revision != parameter_revision
        or frozen_parameters.digest != parameter_digest
    ):
        raise Stage3OosError("training parameter output is incomplete")

    fee_records = _common_base_fee_provenance(base)
    fee_payload: dict[str, object] = {"records": fee_records}
    fee_payload["digest"] = _digest(fee_records)
    metrics_payload = _compact_metrics(base, sensitivity, manifest)
    metrics_payload["summary_digest"] = _digest(metrics_payload)
    frozen = _required_mapping(
        manifest, "frozen_final_test_config", "evaluation manifest"
    )
    strategy_configs = _required_mapping(
        frozen, "strategy_configs", "frozen final-test config"
    )
    run_config_digests = _required_mapping(
        frozen, "run_config_digests", "frozen final-test config"
    )
    configs = {
        "artifact_lock_digest": hashlib.sha256(
            config.artifact_lock_path.read_bytes()
        ).hexdigest(),
        "feature_schema_digest": _required_digest(
            frozen, "feature_schema_digest", "frozen final-test config"
        ),
        "frozen_final_test_config_digest": _required_digest(
            frozen, "final_test_config_digest", "frozen final-test config"
        ),
        "input_digest": _required_digest(manifest, "input_digest", "evaluation"),
        "rebuild_contract_digest": rebuild_contract_digest,
        "run_config_digests": {
            strategy: dict(
                _required_mapping(
                    run_config_digests,
                    strategy,
                    "frozen final-test run configs",
                )
            )
            for strategy in _STRATEGIES
        },
        "shared_execution_digest": _required_digest(
            frozen, "shared_execution_digest", "frozen final-test config"
        ),
        "strategy_config_digests": {
            strategy: _digest(
                _required_mapping(
                    strategy_configs,
                    strategy,
                    "frozen final-test strategy configs",
                )
            )
            for strategy in _STRATEGIES
        },
    }
    record: dict[str, object] = {
        "artifacts": artifacts,
        "configs": configs,
        "evaluation": {
            "manifest_digest": manifest_digest,
            "manifest_relative_filename": "manifest.json",
            "result_digest": _required_digest(
                manifest, "result_digest", "evaluation manifest"
            ),
        },
        "fee_provenance": fee_payload,
        "folds": folds,
        "metrics": metrics_payload,
        "product_status": list(PRODUCT_STATUS),
        "rebuild_command": rebuild_command_template(),
        "runs": runs,
        "schema": STAGE3_ACCEPTANCE_SCHEMA,
        "stage2_input": _stage2_input(config),
        "training_parameters": {
            "record_relative_filename": "training-parameters.json",
            "training_parameter_digest": parameter_digest,
            "training_parameter_revision": parameter_revision,
        },
        "verification_test": STAGE3_OOS_VERIFICATION_TEST,
    }
    record["acceptance_digest"] = stage3_acceptance_digest(record)
    return record


def _compact_run_reference(
    reference: Mapping[str, object], *, evaluation: Stage3EvaluationOutcome
) -> dict[str, object]:
    fold = _required_mapping(reference, "fold", "evaluation run")
    scenario = _required_mapping(reference, "scenario", "evaluation run")
    fold_id = _required_string(fold, "fold_id", "evaluation run fold")
    role = _required_string(fold, "role", "evaluation run fold")
    strategy = _required_string(reference, "strategy", "evaluation run")
    scenario_name = _required_string(scenario, "name", "evaluation run scenario")
    run_type = _required_string(reference, "run_type", "evaluation run")
    relative_partition = _safe_relative_filename(
        _required_string(reference, "partition", "evaluation run")
    )
    relative_manifest = _safe_relative_filename(f"{relative_partition}/manifest.json")
    run_manifest = _read_json_object(
        evaluation.partition / relative_manifest, "evaluation run manifest"
    )
    run_manifest_digest = _required_digest(
        run_manifest, "manifest_digest", "evaluation run manifest"
    )
    if run_manifest_digest != _digest(
        {key: value for key, value in run_manifest.items() if key != "manifest_digest"}
    ):
        raise Stage3OosError("evaluation run manifest digest does not match")
    comparison = {
        "config_digest": reference.get("config_digest"),
        "decision_digest": reference.get("decision_digest"),
        "fee_provenance_digest": reference.get("fee_provenance_digest"),
        "result_digest": reference.get("result_digest"),
        "run_identity": reference.get("run_identity"),
        "scenario_digest": reference.get("scenario_digest"),
        "strategy_config_digest": reference.get("strategy_config_digest"),
    }
    if any(run_manifest.get(key) != value for key, value in comparison.items()):
        raise Stage3OosError("evaluation run output conflicts with its summary")
    artifact = reference.get("artifact")
    artifact_id: str | None = None
    if artifact is not None:
        if not isinstance(artifact, Mapping):
            raise Stage3OosError("evaluation artifact reference is invalid")
        artifact_id = _required_digest(artifact, "artifact_id", "evaluation artifact")
    prediction_digest = reference.get("prediction_digest")
    if prediction_digest is not None and not _is_digest(prediction_digest):
        raise Stage3OosError("evaluation prediction digest is invalid")
    return {
        "artifact_id": artifact_id,
        "config_digest": _required_digest(reference, "config_digest", "run"),
        "decision_digest": _required_digest(reference, "decision_digest", "run"),
        "fee_provenance_digest": _required_digest(
            reference, "fee_provenance_digest", "run"
        ),
        "fold_id": fold_id,
        "manifest_relative_filename": relative_manifest,
        "prediction_digest": prediction_digest,
        "result_digest": _required_digest(reference, "result_digest", "run"),
        "role": role,
        "run_identity": _required_digest(reference, "run_identity", "run"),
        "run_type": run_type,
        "scenario": scenario_name,
        "scenario_digest": _required_digest(reference, "scenario_digest", "run"),
        "strategy": strategy,
        "strategy_config_digest": _required_digest(
            reference, "strategy_config_digest", "run"
        ),
    }


def _artifact_references(
    config: Stage3Config,
    *,
    evaluation: Stage3EvaluationOutcome,
    base_references: Sequence[Mapping[str, object]],
    expected_provenance_kind: str,
) -> list[dict[str, object]]:
    stage2_input = _stage2_input(config)
    artifacts: list[dict[str, object]] = []
    for fold in stage3_evaluation.expanding_folds():
        candidates = [
            item
            for item in base_references
            if item.get("strategy") == "lightgbm"
            and isinstance(item.get("fold"), Mapping)
            and cast(Mapping[str, object], item["fold"]).get("fold_id") == fold.fold_id
        ]
        if len(candidates) != 1:
            raise Stage3OosError("fold artifact references are incomplete")
        run_artifact = _required_mapping(candidates[0], "artifact", "LightGBM base run")
        relative_filename = _safe_relative_filename(
            f"artifacts/{fold.fold_id}/manifest.json"
        )
        artifact = _read_json_object(
            evaluation.evidence_partition / relative_filename,
            "fold artifact manifest",
        )
        try:
            stage3_artifacts._validate_manifest_envelope(artifact)
        except stage3_artifacts.Stage3ArtifactError as exc:
            raise Stage3OosError("fold artifact manifest is invalid") from exc
        provenance = _required_mapping(artifact, "provenance", "artifact manifest")
        training = _required_mapping(artifact, "training", "artifact manifest")
        model = _required_mapping(artifact, "model", "artifact manifest")
        if artifact.get("stage2_input") != stage2_input:
            raise Stage3OosError("fold artifact Stage 2 identity has drifted")
        if provenance.get("kind") != expected_provenance_kind:
            raise Stage3OosError("fold artifact provenance kind is not approved")
        result: dict[str, object] = {
            "artifact_id": _required_digest(artifact, "artifact_id", "artifact"),
            "compatibility_digest": _digest(
                _required_mapping(artifact, "compatibility", "artifact manifest")
            ),
            "environment_digest": _digest(
                _required_mapping(artifact, "environment", "artifact manifest")
            ),
            "fold_id": fold.fold_id,
            "manifest_relative_filename": relative_filename,
            "model_checksum": _required_digest(model, "checksum_sha256", "model"),
            "prediction_tolerance_digest": _digest(
                {
                    "atol": model.get("prediction_atol"),
                    "rtol": model.get("prediction_rtol"),
                }
            ),
            "provenance_kind": expected_provenance_kind,
            "role": fold.role,
            "training_parameter_digest": _required_digest(
                training, "training_parameter_digest", "artifact training"
            ),
            "training_parameter_revision": _required_string(
                training, "training_parameter_revision", "artifact training"
            ),
        }
        result["artifact_binding_digest"] = _artifact_binding_digest(result)
        if any(
            run_artifact.get(key) != result[key]
            for key in (
                "artifact_id",
                "model_checksum",
                "training_parameter_digest",
                "training_parameter_revision",
            )
        ):
            raise Stage3OosError("fold artifact output conflicts with evaluation")
        artifacts.append(result)
    return artifacts


def _artifact_binding_digest(artifact: Mapping[str, object]) -> str:
    fields = {
        "artifact_id",
        "compatibility_digest",
        "environment_digest",
        "fold_id",
        "manifest_relative_filename",
        "model_checksum",
        "prediction_tolerance_digest",
        "provenance_kind",
        "role",
        "training_parameter_digest",
        "training_parameter_revision",
    }
    if not fields.issubset(artifact):
        raise Stage3OosError("artifact identity binding is incomplete")
    return _digest(
        {
            "artifact": {key: artifact[key] for key in sorted(fields)},
            "schema": "tracequant-stage3-acceptance-artifact-binding-v1",
        }
    )


def _common_base_fee_provenance(
    base_references: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    observed: list[list[dict[str, object]]] = []
    for reference in base_references:
        raw = reference.get("fee_provenance")
        values = _mapping_list(raw, "base fee provenance")
        projected: list[dict[str, object]] = []
        for value in values:
            if set(value) != _FEE_PROVENANCE_KEYS:
                raise Stage3OosError("fee provenance fields do not match schema")
            projected.append({key: value[key] for key in sorted(_FEE_PROVENANCE_KEYS)})
        if reference.get("fee_provenance_digest") != _digest(projected):
            raise Stage3OosError("base fee provenance digest does not match")
        projected.sort(key=lambda item: str(item["instrument_id"]))
        observed.append(projected)
    if not observed or any(item != observed[0] for item in observed[1:]):
        raise Stage3OosError("base run fee provenance is incomplete or inconsistent")
    return observed[0]


def _compact_metrics(
    base: Sequence[Mapping[str, object]],
    sensitivity: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
) -> dict[str, object]:
    base_metrics = [
        {
            "fold_id": _required_string(
                _required_mapping(item, "fold", "base run"),
                "fold_id",
                "base run fold",
            ),
            "strategy": _required_string(item, "strategy", "base run"),
            "summary": _metric_summary(_required_mapping(item, "metrics", "base run")),
        }
        for item in base
    ]
    sensitivity_metrics = [
        {
            "fold_id": _required_string(
                _required_mapping(item, "fold", "sensitivity run"),
                "fold_id",
                "sensitivity run fold",
            ),
            "scenario": _required_string(
                _required_mapping(item, "scenario", "sensitivity run"),
                "name",
                "sensitivity scenario",
            ),
            "strategy": _required_string(item, "strategy", "sensitivity run"),
            "summary": _metric_summary(
                _required_mapping(item, "metrics", "sensitivity run")
            ),
        }
        for item in sensitivity
    ]
    dispersion = _required_mapping(manifest, "fold_dispersion", "evaluation")
    return {
        "base": base_metrics,
        "fold_dispersion": {
            strategy: dict(_required_mapping(dispersion, strategy, "fold dispersion"))
            for strategy in _STRATEGIES
        },
        "sensitivity": sensitivity_metrics,
    }


def _metric_summary(metrics: Mapping[str, object]) -> dict[str, object]:
    per_instrument = _required_mapping(metrics, "per_instrument", "run metrics")
    return {
        "commission": metrics.get("commission"),
        "exposure": metrics.get("exposure"),
        "funding": metrics.get("funding"),
        "max_drawdown": metrics.get("max_drawdown"),
        "per_instrument": {
            instrument_id: {
                key: _required_mapping(
                    per_instrument, instrument_id, "per-instrument metrics"
                ).get(key)
                for key in sorted(_PER_INSTRUMENT_METRIC_KEYS)
            }
            for instrument_id in STAGE2_INSTRUMENT_IDS
        },
        "sharpe": metrics.get("sharpe"),
        "sortino": metrics.get("sortino"),
        "total_pnl": metrics.get("total_pnl"),
        "total_return": metrics.get("total_return"),
        "trade_count": metrics.get("trade_count"),
        "turnover": metrics.get("turnover"),
        "window": dict(_required_mapping(metrics, "window", "run metrics")),
    }


def _require_complete_stage3_acceptance_record(
    record: Mapping[str, object],
    *,
    allow_synthetic_fixture: bool,
    repository_root: Path,
) -> None:
    expected_keys = {
        "acceptance_digest",
        "artifacts",
        "configs",
        "evaluation",
        "fee_provenance",
        "folds",
        "metrics",
        "product_status",
        "rebuild_command",
        "runs",
        "schema",
        "stage2_input",
        "training_parameters",
        "verification_test",
    }
    _require_keys(record, expected_keys, "Stage 3 acceptance record")
    if record.get("schema") != STAGE3_ACCEPTANCE_SCHEMA:
        raise Stage3OosError("Stage 3 acceptance schema does not match")
    stage2_input = _required_mapping(
        record, "stage2_input", "Stage 3 acceptance record"
    )
    _require_stage2_input(stage2_input, allow_synthetic_fixture=allow_synthetic_fixture)
    if record.get("product_status") != list(PRODUCT_STATUS):
        raise Stage3OosError("Stage 3 product status does not match")
    if record.get("rebuild_command") != rebuild_command_template():
        raise Stage3OosError("Stage 3 rebuild command does not match")
    if record.get("verification_test") != STAGE3_OOS_VERIFICATION_TEST:
        raise Stage3OosError("Stage 3 verification test does not match")
    acceptance_digest = record.get("acceptance_digest")
    if not _is_digest(acceptance_digest) or acceptance_digest != (
        stage3_acceptance_digest(record)
    ):
        raise Stage3OosError("Stage 3 acceptance digest does not match")

    expected_folds = [fold.payload() for fold in stage3_evaluation.expanding_folds()]
    if record.get("folds") != expected_folds:
        raise Stage3OosError("Stage 3 acceptance folds do not match")
    training = _required_mapping(
        record, "training_parameters", "Stage 3 acceptance record"
    )
    _require_keys(
        training,
        {
            "record_relative_filename",
            "training_parameter_digest",
            "training_parameter_revision",
        },
        "training parameter reference",
    )
    if training.get("record_relative_filename") != "training-parameters.json":
        raise Stage3OosError("training parameter filename does not match")
    revision = _required_string(
        training, "training_parameter_revision", "training parameters"
    )
    parameter_digest = _required_digest(
        training, "training_parameter_digest", "training parameters"
    )
    artifacts = _require_artifacts(
        record.get("artifacts"),
        revision=revision,
        parameter_digest=parameter_digest,
        allow_synthetic_fixture=allow_synthetic_fixture,
    )
    configs = _required_mapping(record, "configs", "Stage 3 acceptance record")
    _require_configs(
        configs,
        stage2_input=stage2_input,
        allow_synthetic_fixture=allow_synthetic_fixture,
        repository_root=repository_root,
    )
    fee = _required_mapping(record, "fee_provenance", "Stage 3 acceptance record")
    _require_fee_provenance(fee)
    runs = _require_runs(
        record.get("runs"), fee=fee, artifacts=artifacts, configs=configs
    )
    metrics = _required_mapping(record, "metrics", "Stage 3 acceptance record")
    _require_metrics(metrics, runs=runs)
    evaluation = _required_mapping(record, "evaluation", "Stage 3 acceptance record")
    _require_keys(
        evaluation,
        {"manifest_digest", "manifest_relative_filename", "result_digest"},
        "evaluation reference",
    )
    if evaluation.get("manifest_relative_filename") != "manifest.json":
        raise Stage3OosError("evaluation manifest filename does not match")
    _required_digest(evaluation, "manifest_digest", "evaluation reference")
    _required_digest(evaluation, "result_digest", "evaluation reference")
    _require_portable_values(record)


def _require_artifacts(
    raw: object,
    *,
    revision: str,
    parameter_digest: str,
    allow_synthetic_fixture: bool,
) -> dict[str, Mapping[str, object]]:
    artifacts = _mapping_list(raw, "Stage 3 artifacts")
    if len(artifacts) != len(stage3_evaluation.expanding_folds()):
        raise Stage3OosError("Stage 3 artifact matrix is incomplete")
    expected_by_fold = {
        fold.fold_id: fold for fold in stage3_evaluation.expanding_folds()
    }
    seen: set[str] = set()
    seen_artifact_ids: set[str] = set()
    compatibility_digests: set[str] = set()
    environment_digests: set[str] = set()
    by_fold: dict[str, Mapping[str, object]] = {}
    expected_provenance = (
        "synthetic_fixture" if allow_synthetic_fixture else "formal_git"
    )
    for artifact in artifacts:
        _require_keys(
            artifact,
            {
                "artifact_binding_digest",
                "artifact_id",
                "compatibility_digest",
                "environment_digest",
                "fold_id",
                "manifest_relative_filename",
                "model_checksum",
                "prediction_tolerance_digest",
                "provenance_kind",
                "role",
                "training_parameter_digest",
                "training_parameter_revision",
            },
            "artifact reference",
        )
        fold_id = _required_string(artifact, "fold_id", "artifact reference")
        fold = expected_by_fold.get(fold_id)
        if fold is None or fold_id in seen or artifact.get("role") != fold.role:
            raise Stage3OosError("artifact fold identity is invalid")
        seen.add(fold_id)
        if artifact.get("manifest_relative_filename") != (
            f"artifacts/{fold_id}/manifest.json"
        ):
            raise Stage3OosError("artifact manifest filename is invalid")
        artifact_id = _required_digest(artifact, "artifact_id", "artifact reference")
        _required_digest(artifact, "model_checksum", "artifact reference")
        compatibility_digests.add(
            _required_digest(artifact, "compatibility_digest", "artifact reference")
        )
        environment_digests.add(
            _required_digest(artifact, "environment_digest", "artifact reference")
        )
        tolerance_digest = _required_digest(
            artifact, "prediction_tolerance_digest", "artifact reference"
        )
        expected_tolerance_digest = _digest(
            {
                "atol": stage3_artifacts.PREDICTION_ATOL,
                "rtol": stage3_artifacts.PREDICTION_RTOL,
            }
        )
        if tolerance_digest != expected_tolerance_digest:
            raise Stage3OosError("artifact prediction tolerance has drifted")
        if artifact_id in seen_artifact_ids:
            raise Stage3OosError("artifact identity is reused across folds")
        seen_artifact_ids.add(artifact_id)
        if artifact.get("artifact_binding_digest") != _artifact_binding_digest(
            artifact
        ):
            raise Stage3OosError("artifact identity binding does not match")
        if (
            artifact.get("training_parameter_revision") != revision
            or artifact.get("training_parameter_digest") != parameter_digest
        ):
            raise Stage3OosError("artifact parameter identity has drifted")
        provenance_kind = artifact.get("provenance_kind")
        if provenance_kind != expected_provenance:
            raise Stage3OosError("artifact provenance kind is not formal")
        by_fold[fold_id] = artifact
    if seen != set(expected_by_fold):
        raise Stage3OosError("artifact folds are incomplete")
    if len(compatibility_digests) != 1 or len(environment_digests) != 1:
        raise Stage3OosError("fold artifact runtime identity is inconsistent")
    return by_fold


def _require_configs(
    configs: Mapping[str, object],
    *,
    stage2_input: Mapping[str, object],
    allow_synthetic_fixture: bool,
    repository_root: Path,
) -> None:
    _require_keys(
        configs,
        {
            "artifact_lock_digest",
            "feature_schema_digest",
            "frozen_final_test_config_digest",
            "input_digest",
            "rebuild_contract_digest",
            "run_config_digests",
            "shared_execution_digest",
            "strategy_config_digests",
        },
        "config digests",
    )
    if configs.get("feature_schema_digest") != FEATURE_SCHEMA_DIGEST:
        raise Stage3OosError("feature schema digest has drifted")
    if configs.get("rebuild_contract_digest") != _rebuild_contract_digest(
        repository_root
    ):
        raise Stage3OosError("rebuild contract digest has drifted")
    artifact_lock_digest = _required_digest(
        configs, "artifact_lock_digest", "config digests"
    )
    if not allow_synthetic_fixture:
        lock_path = repository_root / STAGE2_ARTIFACT_LOCK_RELATIVE_PATH
        if (
            not lock_path.is_file()
            or artifact_lock_digest
            != hashlib.sha256(lock_path.read_bytes()).hexdigest()
        ):
            raise Stage3OosError("artifact lock digest has drifted")
    expected_input_digest = _digest(
        {**dict(stage2_input), "artifact_lock_sha256": artifact_lock_digest}
    )
    if configs.get("input_digest") != expected_input_digest:
        raise Stage3OosError("Stage 3 input digest does not match")
    expected_shared_digest = stage3_evaluation._shared_execution_digest()
    if configs.get("shared_execution_digest") != expected_shared_digest:
        raise Stage3OosError("shared execution digest does not match")
    for key in (
        "artifact_lock_digest",
        "frozen_final_test_config_digest",
        "input_digest",
        "rebuild_contract_digest",
        "shared_execution_digest",
    ):
        _required_digest(configs, key, "config digests")
    strategy_digests = _required_mapping(
        configs, "strategy_config_digests", "config digests"
    )
    _require_keys(strategy_digests, set(_STRATEGIES), "strategy config digests")
    run_digests = _required_mapping(configs, "run_config_digests", "config digests")
    _require_keys(run_digests, set(_STRATEGIES), "run config digests")
    expected_scenarios = set(_ACCOUNTING_SCENARIOS)
    expected_strategy_digests = {
        strategy: stage3_evaluation._strategy_config_digest(strategy)
        for strategy in _STRATEGIES
    }
    if dict(strategy_digests) != expected_strategy_digests:
        raise Stage3OosError("strategy config digests do not match")
    final_fold = stage3_evaluation.expanding_folds()[-1]
    scenarios_by_name = {
        scenario.name: scenario for scenario in stage3_evaluation.accounting_scenarios()
    }
    expected_run_digests = {
        strategy: {
            scenario: stage3_evaluation._run_config_digest(
                final_fold,
                strategy,
                scenarios_by_name[scenario],
            )
            for scenario in _ACCOUNTING_SCENARIOS
        }
        for strategy in _STRATEGIES
    }
    for strategy in _STRATEGIES:
        _required_digest(strategy_digests, strategy, "strategy config digests")
        values = _required_mapping(run_digests, strategy, "run config digests")
        _require_keys(values, expected_scenarios, "strategy run config digests")
        for scenario in expected_scenarios:
            _required_digest(values, scenario, "strategy run config digests")
    if dict(run_digests) != expected_run_digests:
        raise Stage3OosError("run config digests do not match")
    frozen_payload: dict[str, object] = {
        "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
        "input_digest": expected_input_digest,
        "lightgbm_strategy_digest": expected_strategy_digests["lightgbm"],
        "momentum_strategy_digest": expected_strategy_digests["momentum"],
        "run_config_digests": expected_run_digests,
        "shared_execution_digest": expected_shared_digest,
        "strategy_configs": {
            strategy: stage3_evaluation._strategy_config_payload(strategy)
            for strategy in _STRATEGIES
        },
    }
    if configs.get("frozen_final_test_config_digest") != _digest(frozen_payload):
        raise Stage3OosError("frozen final-test config digest does not match")


def _require_fee_provenance(fee: Mapping[str, object]) -> None:
    _require_keys(fee, {"digest", "records"}, "fee provenance")
    records = _mapping_list(fee.get("records"), "fee provenance records")
    if len(records) != len(STAGE2_INSTRUMENT_IDS):
        raise Stage3OosError("fee provenance instruments are incomplete")
    if fee.get("digest") != _digest(records):
        raise Stage3OosError("fee provenance digest does not match")
    seen: set[str] = set()
    observed_order: list[str] = []
    for record in records:
        _require_keys(record, _FEE_PROVENANCE_KEYS, "fee provenance record")
        instrument_id = _required_string(
            record, "instrument_id", "fee provenance record"
        )
        if instrument_id not in STAGE2_INSTRUMENT_IDS or instrument_id in seen:
            raise Stage3OosError("fee provenance instrument is invalid")
        seen.add(instrument_id)
        observed_order.append(instrument_id)
        for key in _FEE_PROVENANCE_KEYS - {
            "instrument_id",
            "catalog_maker_fee",
            "catalog_taker_fee",
            "snapshot_maker_fee",
            "snapshot_taker_fee",
            "effective_maker_fee",
            "effective_taker_fee",
        }:
            if not isinstance(record.get(key), bool):
                raise Stage3OosError("fee provenance comparison is invalid")
        for key in {
            "catalog_maker_fee",
            "catalog_taker_fee",
            "snapshot_maker_fee",
            "snapshot_taker_fee",
        }:
            value = record.get(key)
            if value is not None and not isinstance(value, str):
                raise Stage3OosError("fee provenance source value is invalid")
            if isinstance(value, str):
                _require_non_negative_number_text(
                    value, f"fee provenance source value {key}"
                )
        if (
            record.get("effective_maker_fee") != "0.0002"
            or record.get("effective_taker_fee") != "0.0004"
        ):
            raise Stage3OosError("effective base fees are not frozen")
        if record.get("catalog_maker_matches_base") is not _fee_matches_base(
            record.get("catalog_maker_fee"), Decimal("0.0002")
        ):
            raise Stage3OosError("catalog maker fee comparison is inconsistent")
        if record.get("catalog_taker_matches_base") is not _fee_matches_base(
            record.get("catalog_taker_fee"), Decimal("0.0004")
        ):
            raise Stage3OosError("catalog taker fee comparison is inconsistent")
        if record.get("snapshot_maker_matches_base") is not _fee_matches_base(
            record.get("snapshot_maker_fee"), Decimal("0.0002")
        ):
            raise Stage3OosError("snapshot maker fee comparison is inconsistent")
        if record.get("snapshot_taker_matches_base") is not _fee_matches_base(
            record.get("snapshot_taker_fee"), Decimal("0.0004")
        ):
            raise Stage3OosError("snapshot taker fee comparison is inconsistent")
        if record.get("catalog_matches_snapshot") is not (
            record.get("catalog_maker_fee") == record.get("snapshot_maker_fee")
            and record.get("catalog_taker_fee") == record.get("snapshot_taker_fee")
        ):
            raise Stage3OosError("catalog and snapshot fee comparison is inconsistent")
    if seen != set(STAGE2_INSTRUMENT_IDS):
        raise Stage3OosError("fee provenance instruments are incomplete")
    if observed_order != list(STAGE2_INSTRUMENT_IDS):
        raise Stage3OosError("fee provenance instruments are not canonical")


def _fee_matches_base(value: object, expected: Decimal) -> bool:
    if not isinstance(value, str):
        return False
    try:
        observed = Decimal(value)
    except InvalidOperation as exc:
        raise Stage3OosError("fee provenance source value is not numeric") from exc
    return observed.is_finite() and observed == expected


def _scenario_fee_provenance_digest(
    records: Sequence[Mapping[str, object]],
    scenario: stage3_evaluation.AccountingScenario,
) -> str:
    projected: list[dict[str, object]] = []
    for record in records:
        item = dict(record)
        item["effective_maker_fee"] = str(scenario.maker_fee)
        item["effective_taker_fee"] = str(scenario.taker_fee)
        projected.append(item)
    return _digest(projected)


def _require_runs(
    raw: object,
    *,
    fee: Mapping[str, object],
    artifacts: Mapping[str, Mapping[str, object]],
    configs: Mapping[str, object],
) -> list[Mapping[str, object]]:
    runs = _mapping_list(raw, "Stage 3 runs")
    expected_base = {
        (fold.fold_id, strategy, "base")
        for fold in stage3_evaluation.expanding_folds()
        for strategy in _STRATEGIES
    }
    final_fold = stage3_evaluation.expanding_folds()[-1]
    expected_sensitivity = {
        (final_fold.fold_id, strategy, scenario)
        for strategy in _STRATEGIES
        for scenario in _SENSITIVITY_SCENARIOS
    }
    observed_base: set[tuple[str, str, str]] = set()
    observed_sensitivity: set[tuple[str, str, str]] = set()
    final_base: dict[str, Mapping[str, object]] = {}
    folds_by_id = {fold.fold_id: fold for fold in stage3_evaluation.expanding_folds()}
    fold_roles = {fold_id: fold.role for fold_id, fold in folds_by_id.items()}
    scenarios_by_name = {
        scenario.name: scenario for scenario in stage3_evaluation.accounting_scenarios()
    }
    strategy_config_digests = _required_mapping(
        configs, "strategy_config_digests", "config digests"
    )
    run_config_digests = _required_mapping(
        configs, "run_config_digests", "config digests"
    )
    fee_digest = _required_digest(fee, "digest", "fee provenance")
    fee_records = _mapping_list(fee.get("records"), "fee provenance records")
    fields = {
        "artifact_id",
        "config_digest",
        "decision_digest",
        "fee_provenance_digest",
        "fold_id",
        "manifest_relative_filename",
        "prediction_digest",
        "result_digest",
        "role",
        "run_identity",
        "run_type",
        "scenario",
        "scenario_digest",
        "strategy",
        "strategy_config_digest",
    }
    for run in runs:
        _require_keys(run, fields, "run reference")
        fold_id = _required_string(run, "fold_id", "run reference")
        strategy = _required_string(run, "strategy", "run reference")
        scenario = _required_string(run, "scenario", "run reference")
        run_type = _required_string(run, "run_type", "run reference")
        key = (fold_id, strategy, scenario)
        if fold_id not in fold_roles or run.get("role") != fold_roles[fold_id]:
            raise Stage3OosError("run fold identity is invalid")
        if strategy not in _STRATEGIES:
            raise Stage3OosError("run strategy is invalid")
        expected_scenario = scenarios_by_name.get(
            cast(stage3_evaluation.ScenarioName, scenario)
        )
        if expected_scenario is None or run.get(
            "config_digest"
        ) != stage3_evaluation._run_config_digest(
            folds_by_id[fold_id],
            cast(stage3_evaluation.StrategyName, strategy),
            expected_scenario,
        ):
            raise Stage3OosError("run config digest does not match fixed contract")
        if run.get("fee_provenance_digest") != _scenario_fee_provenance_digest(
            fee_records, expected_scenario
        ):
            raise Stage3OosError("run fee provenance does not match its scenario")
        expected_filename = (
            f"base/{fold_id}/{strategy}/manifest.json"
            if run_type == "base"
            else f"sensitivity/{strategy}/{scenario}/manifest.json"
        )
        if run.get("manifest_relative_filename") != expected_filename:
            raise Stage3OosError("run manifest filename is invalid")
        for digest_key in fields - {
            "artifact_id",
            "fold_id",
            "manifest_relative_filename",
            "prediction_digest",
            "role",
            "run_type",
            "scenario",
            "strategy",
        }:
            _required_digest(run, digest_key, "run reference")
        expected_run_identity = _digest(
            {
                "config_digest": run["config_digest"],
                "result_digest": run["result_digest"],
                "schema": stage3_evaluation.STAGE3_EVALUATION_RUN_SCHEMA,
            }
        )
        if run.get("run_identity") != expected_run_identity:
            raise Stage3OosError(
                "run identity does not match its config/result identity"
            )
        artifact_id = run.get("artifact_id")
        prediction_digest = run.get("prediction_digest")
        if strategy == "lightgbm":
            if not _is_digest(artifact_id) or not _is_digest(prediction_digest):
                raise Stage3OosError("LightGBM run identity is incomplete")
            expected_artifact = artifacts[fold_id].get("artifact_id")
            if artifact_id != expected_artifact:
                raise Stage3OosError("run artifact reference is inconsistent")
        elif artifact_id is not None or prediction_digest is not None:
            raise Stage3OosError("momentum run contains model identity")
        if run.get("strategy_config_digest") != strategy_config_digests.get(strategy):
            raise Stage3OosError("run strategy config reference is inconsistent")
        if fold_id == final_fold.fold_id:
            strategy_run_configs = _required_mapping(
                run_config_digests, strategy, "run config digests"
            )
            if run.get("config_digest") != strategy_run_configs.get(scenario):
                raise Stage3OosError("final-test run config reference is inconsistent")
        if run_type == "base":
            if scenario != "base" or key in observed_base:
                raise Stage3OosError("base run matrix is invalid")
            if run.get("fee_provenance_digest") != fee_digest:
                raise Stage3OosError("base run fee provenance is inconsistent")
            observed_base.add(key)
            if fold_id == final_fold.fold_id:
                final_base[strategy] = run
        elif run_type == "sensitivity":
            if scenario not in _SENSITIVITY_SCENARIOS or key in observed_sensitivity:
                raise Stage3OosError("sensitivity run matrix is invalid")
            observed_sensitivity.add(key)
        else:
            raise Stage3OosError("run type is invalid")
    if observed_base != expected_base or observed_sensitivity != expected_sensitivity:
        raise Stage3OosError("Stage 3 run matrix is incomplete")
    for run in runs:
        if run.get("run_type") != "sensitivity":
            continue
        base = final_base[cast(str, run["strategy"])]
        if run.get("decision_digest") != base.get("decision_digest"):
            raise Stage3OosError("sensitivity run changed decisions")
        if run.get("artifact_id") != base.get("artifact_id"):
            raise Stage3OosError("sensitivity run changed artifact identity")
        if run.get("prediction_digest") != base.get("prediction_digest"):
            raise Stage3OosError("sensitivity run changed predictions")
    return runs


def _require_metrics(
    metrics: Mapping[str, object], *, runs: Sequence[Mapping[str, object]]
) -> None:
    _require_keys(
        metrics,
        {"base", "fold_dispersion", "sensitivity", "summary_digest"},
        "metric summaries",
    )
    payload = {key: value for key, value in metrics.items() if key != "summary_digest"}
    if metrics.get("summary_digest") != _digest(payload):
        raise Stage3OosError("metric summary digest does not match")
    base = _mapping_list(metrics.get("base"), "base metric summaries")
    sensitivity = _mapping_list(
        metrics.get("sensitivity"), "sensitivity metric summaries"
    )
    expected_base = {
        (cast(str, run["fold_id"]), cast(str, run["strategy"]))
        for run in runs
        if run.get("run_type") == "base"
    }
    expected_sensitivity = {
        (
            cast(str, run["fold_id"]),
            cast(str, run["strategy"]),
            cast(str, run["scenario"]),
        )
        for run in runs
        if run.get("run_type") == "sensitivity"
    }
    if len(base) != len(expected_base) or len(sensitivity) != len(expected_sensitivity):
        raise Stage3OosError("metric summary matrix has duplicates or omissions")
    observed_base: set[tuple[str, str]] = set()
    for item in base:
        _require_keys(item, {"fold_id", "strategy", "summary"}, "base metrics")
        base_key = (
            _required_string(item, "fold_id", "base metrics"),
            _required_string(item, "strategy", "base metrics"),
        )
        if base_key in observed_base:
            raise Stage3OosError("base metric summary is duplicated")
        observed_base.add(base_key)
        summary = _required_mapping(item, "summary", "base metrics")
        _require_metric_summary(summary)
        _require_metric_window(summary, fold_id=base_key[0])
    observed_sensitivity: set[tuple[str, str, str]] = set()
    for item in sensitivity:
        _require_keys(
            item,
            {"fold_id", "scenario", "strategy", "summary"},
            "sensitivity metrics",
        )
        sensitivity_key = (
            _required_string(item, "fold_id", "sensitivity metrics"),
            _required_string(item, "strategy", "sensitivity metrics"),
            _required_string(item, "scenario", "sensitivity metrics"),
        )
        if sensitivity_key in observed_sensitivity:
            raise Stage3OosError("sensitivity metric summary is duplicated")
        observed_sensitivity.add(sensitivity_key)
        summary = _required_mapping(item, "summary", "sensitivity metrics")
        _require_metric_summary(summary)
        _require_metric_window(summary, fold_id=sensitivity_key[0])
    if observed_base != expected_base or observed_sensitivity != expected_sensitivity:
        raise Stage3OosError("metric summary matrix is incomplete")
    dispersion = _required_mapping(metrics, "fold_dispersion", "metric summaries")
    _require_keys(dispersion, set(_STRATEGIES), "fold dispersion")
    for strategy in _STRATEGIES:
        values = _required_mapping(dispersion, strategy, "fold dispersion")
        _require_keys(values, _DISPERSION_KEYS, "fold dispersion metrics")
        for value in values.values():
            if not isinstance(value, Mapping):
                raise Stage3OosError("fold dispersion value is invalid")
            _require_keys(value, {"max", "min", "range"}, "fold dispersion value")
            for number in value.values():
                _require_number_text(number, "fold dispersion value")


def _require_metric_summary(summary: Mapping[str, object]) -> None:
    _require_keys(summary, _METRIC_KEYS, "metric summary")
    for key in _METRIC_KEYS - {"per_instrument", "trade_count", "window"}:
        _require_number_text(summary.get(key), f"metric {key}")
    if isinstance(summary.get("trade_count"), bool) or not isinstance(
        summary.get("trade_count"), int
    ):
        raise Stage3OosError("metric trade_count is invalid")
    window = _required_mapping(summary, "window", "metric summary")
    _require_keys(window, {"end", "role", "start"}, "metric window")
    if any(not isinstance(window.get(key), str) for key in ("end", "role", "start")):
        raise Stage3OosError("metric window is invalid")
    per_instrument = _required_mapping(summary, "per_instrument", "metric summary")
    _require_keys(per_instrument, set(STAGE2_INSTRUMENT_IDS), "per-instrument metrics")
    for instrument_id in STAGE2_INSTRUMENT_IDS:
        values = _required_mapping(
            per_instrument, instrument_id, "per-instrument metrics"
        )
        _require_keys(values, _PER_INSTRUMENT_METRIC_KEYS, "instrument metrics")
        for key in _PER_INSTRUMENT_METRIC_KEYS - {"fill_count", "trade_count"}:
            _require_number_text(values.get(key), f"instrument metric {key}")
        for key in ("fill_count", "trade_count"):
            value = values.get(key)
            if isinstance(value, bool) or not isinstance(value, int):
                raise Stage3OosError(f"instrument metric {key} is invalid")


def _require_metric_window(summary: Mapping[str, object], *, fold_id: str) -> None:
    matches = [
        fold for fold in stage3_evaluation.expanding_folds() if fold.fold_id == fold_id
    ]
    if len(matches) != 1:
        raise Stage3OosError("metric fold is invalid")
    fold = matches[0]
    payload = fold.payload()
    expected = {
        "end": payload["evaluation_end"],
        "role": fold.role,
        "start": payload["evaluation_start"],
    }
    if summary.get("window") != expected:
        raise Stage3OosError("metric window does not match its fold")


def _require_locked_config(config: Stage3Config) -> None:
    if _stage2_input(config) != _locked_stage2_input():
        raise Stage3OosError("rebuild input conflicts with the locked Stage 2 identity")
    if config.schema != STAGE3_CONFIG_SCHEMA:
        raise Stage3OosError("rebuild config schema does not match")


def _require_config_paths(
    config: Stage3Config,
    *,
    repository_root: Path,
    allow_synthetic_fixture: bool,
) -> None:
    repository = repository_root.resolve()
    resolved: dict[str, Path] = {}
    for name, raw in (
        ("catalog_path", config.catalog_path),
        ("evidence_root", config.evidence_root),
        ("run_root", config.run_root),
    ):
        if not raw.is_absolute():
            raise Stage3OosError(f"{name} must be an absolute external path")
        if any(part.lower() == "latest" for part in raw.parts):
            raise Stage3OosError(f"{name} must not use a latest alias")
        path = raw.resolve(strict=False)
        if path == repository or repository in path.parents:
            raise Stage3OosError(f"{name} must not be inside the repository")
        if any(part.lower() == "latest" for part in path.parts):
            raise Stage3OosError(f"{name} must not use a latest alias")
        resolved[name] = path
    if not resolved["catalog_path"].is_dir():
        raise Stage3OosError("catalog_path must be an existing directory")
    for left, right in (
        ("catalog_path", "evidence_root"),
        ("catalog_path", "run_root"),
        ("evidence_root", "run_root"),
    ):
        first = resolved[left]
        second = resolved[right]
        if first == second or first in second.parents or second in first.parents:
            raise Stage3OosError(f"{left} and {right} must not overlap")
    artifact_lock = config.artifact_lock_path.resolve(strict=False)
    if not artifact_lock.is_file():
        raise Stage3OosError("Stage 2 artifact lock is missing")
    expected_lock = (repository / STAGE2_ARTIFACT_LOCK_RELATIVE_PATH).resolve()
    if not allow_synthetic_fixture and artifact_lock != expected_lock:
        raise Stage3OosError("Stage 2 artifact lock path does not match")


def _require_stage2_input(
    value: Mapping[str, object], *, allow_synthetic_fixture: bool
) -> None:
    expected_keys = set(_locked_stage2_input())
    _require_keys(value, expected_keys, "Stage 2 input identity")
    if value.get("dataset_id") != STAGE2_DATASET_ID:
        raise Stage3OosError("Stage 2 dataset id does not match")
    if value.get("runtime_identity") != UPSTREAM_RELEASE_IDENTITY:
        raise Stage3OosError("Stage 2 runtime identity does not match")
    for key in expected_keys - {"dataset_id", "runtime_identity"}:
        _required_digest(value, key, "Stage 2 input identity")
    if not allow_synthetic_fixture and dict(value) != _locked_stage2_input():
        raise Stage3OosError("Stage 3 acceptance input identity does not match")


def _stage2_input(config: Stage3Config) -> dict[str, str]:
    return {
        "acceptance_digest": config.acceptance_digest,
        "dataset_digest": config.dataset_digest,
        "dataset_id": config.dataset_id,
        "instrument_snapshot_checksum": config.instrument_snapshot_checksum,
        "market_data_manifest_digest": config.market_data_manifest_digest,
        "runtime_identity": config.runtime_identity,
        "source_manifest_digest": config.source_manifest_digest,
    }


def _locked_stage2_input() -> dict[str, str]:
    return {
        "acceptance_digest": STAGE2_ACCEPTANCE_DIGEST,
        "dataset_digest": STAGE2_DATASET_DIGEST,
        "dataset_id": STAGE2_DATASET_ID,
        "instrument_snapshot_checksum": STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM,
        "market_data_manifest_digest": STAGE2_MARKET_DATA_MANIFEST_DIGEST,
        "runtime_identity": UPSTREAM_RELEASE_IDENTITY,
        "source_manifest_digest": STAGE2_SOURCE_MANIFEST_DIGEST,
    }


def _require_record_target(
    path: Path,
    *,
    config: Stage3Config,
    repository_root: Path,
    allow_synthetic_fixture: bool,
) -> Path:
    target = Path(path)
    if not target.is_absolute():
        raise Stage3OosError("Stage 3 acceptance target must be absolute")
    resolved = target.resolve(strict=False)
    repository = repository_root.resolve()
    tracked_target = repository / STAGE3_ACCEPTANCE_RELATIVE_PATH
    if allow_synthetic_fixture:
        if resolved == repository or repository in resolved.parents:
            raise Stage3OosError(
                "synthetic acceptance evidence cannot enter the repository"
            )
    elif resolved != tracked_target:
        raise Stage3OosError("formal Stage 3 acceptance target is not the tracked path")
    if target.is_symlink():
        raise Stage3OosError("Stage 3 acceptance target must not be a symlink")
    if allow_synthetic_fixture and resolved.exists():
        raise Stage3OosError("Stage 3 acceptance target must not already exist")
    if not allow_synthetic_fixture and resolved.exists() and not resolved.is_file():
        raise Stage3OosError("tracked Stage 3 acceptance target is not a regular file")
    if not resolved.parent.is_dir():
        raise Stage3OosError("Stage 3 acceptance target parent is missing")
    for other in (config.catalog_path, config.evidence_root, config.run_root):
        root = other.resolve(strict=False)
        if resolved == root or resolved in root.parents or root in resolved.parents:
            raise Stage3OosError("Stage 3 acceptance target overlaps an external root")
    return resolved


def _rebuild_contract_digest(repository_root: Path) -> str:
    entries: list[dict[str, str]] = []
    for relative in _REBUILD_CONTRACT_PATHS:
        path = repository_root / relative
        if not path.is_file():
            raise Stage3OosError(f"rebuild contract file is missing: {relative}")
        entries.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return _digest({"schema": "tracequant-stage3-oos-contract-v1", "files": entries})


def _require_portable_values(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise Stage3OosError("acceptance record key is not text")
            _require_portable_values(item)
    elif isinstance(value, list):
        for item in value:
            _require_portable_values(item)
    elif isinstance(value, str):
        if value.startswith("/") or re.match(_WINDOWS_DRIVE_PATTERN, value):
            raise Stage3OosError("acceptance record contains an absolute path")


def _safe_relative_filename(value: str) -> str:
    path = Path(value)
    if (
        path.is_absolute()
        or not value
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise Stage3OosError("external evidence filename is not a safe relative path")
    return value


def _mapping_list(value: object, description: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise Stage3OosError(f"{description} is invalid")
    return cast(list[Mapping[str, object]], value)


def _required_mapping(
    value: Mapping[str, object], key: str, description: str
) -> Mapping[str, object]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise Stage3OosError(f"{description} {key} is invalid")
    return result


def _required_string(value: Mapping[str, object], key: str, description: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise Stage3OosError(f"{description} {key} is invalid")
    return result


def _required_digest(value: Mapping[str, object], key: str, description: str) -> str:
    result = value.get(key)
    if not _is_digest(result):
        raise Stage3OosError(f"{description} {key} is not a SHA-256 digest")
    return cast(str, result)


def _require_keys(
    value: Mapping[str, object], expected: set[str], description: str
) -> None:
    if set(value) != expected:
        raise Stage3OosError(f"{description} fields do not match schema")


def _require_number_text(value: object, description: str) -> None:
    if not isinstance(value, str):
        raise Stage3OosError(f"{description} is not text")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise Stage3OosError(f"{description} is not numeric") from exc
    if not number.is_finite():
        raise Stage3OosError(f"{description} is not finite")


def _require_non_negative_number_text(value: object, description: str) -> None:
    _require_number_text(value, description)
    if Decimal(cast(str, value)) < 0:
        raise Stage3OosError(f"{description} is negative")


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(_DIGEST_PATTERN, value) is not None


def _read_json_object(path: Path, description: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage3OosError(f"{description} is missing or invalid") from exc
    if not isinstance(payload, dict):
        raise Stage3OosError(f"{description} is not an object")
    return cast(dict[str, object], payload)


def _write_json_exclusive(path: Path, payload: Mapping[str, object]) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as exc:
        raise Stage3OosError("Stage 3 acceptance target is immutable") from exc


def _write_json_replacing(path: Path, payload: Mapping[str, object]) -> None:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(0o644)
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as exc:
        raise Stage3OosError(
            "tracked Stage 3 acceptance record could not be published atomically"
        ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _digest(payload: object) -> str:
    try:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise Stage3OosError(
            "Stage 3 acceptance payload is not canonical JSON"
        ) from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _external_path(value: Path, *, repository_root: Path, name: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise Stage3OosError(f"{name} must be an absolute external path")
    if any(part.lower() == "latest" for part in path.parts):
        raise Stage3OosError(f"{name} must not use a latest alias")
    resolved = path.resolve(strict=False)
    repository = repository_root.resolve()
    if resolved == repository or repository in resolved.parents:
        raise Stage3OosError(f"{name} must not be inside the repository")
    if any(part.lower() == "latest" for part in resolved.parts):
        raise Stage3OosError(f"{name} must not use a latest alias")
    return resolved


def _config_from_args(
    args: argparse.Namespace, *, repository_root: Path
) -> Stage3Config:
    catalog_path = _external_path(
        cast(Path, args.catalog_path),
        repository_root=repository_root,
        name="catalog_path",
    )
    evidence_root = _external_path(
        cast(Path, args.evidence_root),
        repository_root=repository_root,
        name="evidence_root",
    )
    run_root = _external_path(
        cast(Path, args.run_root), repository_root=repository_root, name="run_root"
    )
    if not catalog_path.is_dir():
        raise Stage3OosError("catalog_path must be an existing directory")
    config = Stage3Config(
        schema=STAGE3_CONFIG_SCHEMA,
        dataset_id=cast(str, args.dataset_id),
        acceptance_digest=cast(str, args.acceptance_digest),
        dataset_digest=cast(str, args.dataset_digest),
        source_manifest_digest=cast(str, args.source_manifest_digest),
        market_data_manifest_digest=cast(str, args.market_data_manifest_digest),
        instrument_snapshot_checksum=cast(str, args.instrument_snapshot_checksum),
        runtime_identity=cast(str, args.runtime_identity),
        artifact_lock_path=(
            repository_root / STAGE2_ARTIFACT_LOCK_RELATIVE_PATH
        ).resolve(),
        catalog_path=catalog_path,
        evidence_root=evidence_root,
        run_root=run_root,
    )
    _require_locked_config(config)
    return config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Finite Stage 3 OOS rebuild and tracked acceptance record"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    rebuild = commands.add_parser("rebuild-oos")
    rebuild.add_argument("--catalog-path", type=Path, required=True)
    rebuild.add_argument("--evidence-root", type=Path, required=True)
    rebuild.add_argument("--run-root", type=Path, required=True)
    rebuild.add_argument("--dataset-id", required=True)
    rebuild.add_argument("--acceptance-digest", required=True)
    rebuild.add_argument("--dataset-digest", required=True)
    rebuild.add_argument("--source-manifest-digest", required=True)
    rebuild.add_argument("--market-data-manifest-digest", required=True)
    rebuild.add_argument("--instrument-snapshot-checksum", required=True)
    rebuild.add_argument("--runtime-identity", required=True)
    return parser


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "rebuild-oos":
        raise Stage3OosError("unknown Stage 3 OOS command")
    repository_root = _repository_root()
    outcome = rebuild_stage3_oos(
        _config_from_args(args, repository_root=repository_root),
        stage2_acceptance_record_path=(
            repository_root / STAGE2_ACCEPTANCE_RELATIVE_PATH
        ),
    )
    json.dump(
        {
            "acceptance_digest": outcome.acceptance_digest,
            "acceptance_record": STAGE3_ACCEPTANCE_RELATIVE_PATH,
            "evaluation_result_digest": outcome.evaluation.result_digest,
            "product_status": list(PRODUCT_STATUS),
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
