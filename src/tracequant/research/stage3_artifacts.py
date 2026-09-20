from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import platform
import subprocess
import sysconfig
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, cast

import polars as pl

from tracequant.integrations import nautilus as nautilus_integration
from tracequant.integrations.nautilus import (
    EXPECTED_VERSION as NAUTILUS_VERSION,
)
from tracequant.integrations.nautilus import UPSTREAM_RELEASE_IDENTITY, stage2_artifact
from tracequant.integrations.nautilus import stage2_btceth as nautilus_stage2_btceth
from tracequant.integrations.nautilus.stage2_artifact import sha256_file
from tracequant.research import source_schema, stage3_features
from tracequant.research import views as research_views
from tracequant.research.stage3_features import (
    FEATURE_LOOKBACK_HOURS,
    FEATURE_NAMES,
    FEATURE_SCHEMA,
    FEATURE_SCHEMA_DIGEST,
    HOUR_NS,
    LABEL_HORIZON_HOURS,
    MS_NS,
    STAGE2_ACCEPTANCE_DIGEST,
    STAGE2_DATASET_DIGEST,
    STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM,
    STAGE2_MARKET_DATA_MANIFEST_DIGEST,
    STAGE2_SOURCE_MANIFEST_DIGEST,
    Stage3Config,
    feature_frame,
    feature_schema_payload,
    load_accepted_feature_window,
    require_feature_frame_schema,
)
from tracequant.source_data import stage2_btceth as source_stage2_btceth
from tracequant.source_data.stage2_btceth import (
    STAGE2_DATASET_ID,
    STAGE2_INSTRUMENT_IDS,
    datetime_to_nanos,
    require_utc,
    stage2_bar_type_str,
)

LIGHTGBM_VERSION: Final = "4.7.0"
PARAMETER_RECORD_SCHEMA: Final = "tracequant-stage3-lightgbm-parameters-v1"
ARTIFACT_MANIFEST_SCHEMA: Final = "tracequant-stage3-lightgbm-artifact-v1"
MODEL_FILENAME: Final = "model.txt"
MANIFEST_FILENAME: Final = "manifest.json"
ARTIFACT_CLAIM_FILENAME: Final = ".tracequant-artifact-claim"
PENDING_MODEL_DIRECTORY: Final = ".tracequant-model-pending"
PREDICTION_ATOL: Final = 1e-9
PREDICTION_RTOL: Final = 1e-6
CANONICAL_JSON_RULE: Final = "utf8-sort-keys-compact-ascii-no-nan-v1"
CODE_INVENTORY_SCHEMA: Final = "tracequant-stage3-model-code-inventory-v1"

RUNTIME_DEPENDENCIES: Final = ("lightgbm", "narwhals", "numpy", "scipy")
PARAMETER_KEYS: Final = (
    "objective",
    "metric",
    "boosting_type",
    "data_sample_strategy",
    "tree_learner",
    "learning_rate",
    "num_leaves",
    "max_depth",
    "min_data_in_leaf",
    "min_sum_hessian_in_leaf",
    "feature_fraction",
    "feature_fraction_bynode",
    "bagging_fraction",
    "bagging_freq",
    "lambda_l1",
    "lambda_l2",
    "min_gain_to_split",
    "max_delta_step",
    "path_smooth",
    "extra_trees",
    "linear_tree",
    "max_bin",
    "min_data_in_bin",
    "bin_construct_sample_cnt",
    "boost_from_average",
    "reg_sqrt",
    "use_missing",
    "zero_as_missing",
    "feature_pre_filter",
    "seed",
    "bagging_seed",
    "feature_fraction_seed",
    "data_random_seed",
    "deterministic",
    "force_row_wise",
    "force_col_wise",
    "num_threads",
    "device_type",
    "verbosity",
    "num_boost_round",
)
ARTIFACT_IDENTITY_FIELDS: Final = (
    "stage2_input",
    "features",
    "label",
    "instruments",
    "window",
    "training",
    "environment",
    "compatibility",
    "model",
)

ParameterValue = bool | int | float | str
WindowRole = Literal["development", "validation", "final_test"]
ProvenanceKind = Literal["formal_git", "synthetic_fixture"]
CodeInventoryPath = tuple[str, Path, tuple[str, ...]]


class Stage3ArtifactError(ValueError):
    """Raised when training, artifact identity, or prediction fails closed."""


@dataclass(frozen=True)
class Stage2ArtifactIdentity:
    dataset_id: str
    acceptance_digest: str
    dataset_digest: str
    source_manifest_digest: str
    market_data_manifest_digest: str
    instrument_snapshot_checksum: str
    runtime_identity: str


@dataclass(frozen=True)
class ArtifactWindow:
    train_start: datetime
    train_end: datetime
    evaluation_start: datetime
    evaluation_end: datetime
    role: WindowRole


@dataclass(frozen=True)
class TrainingProvenance:
    kind: ProvenanceKind
    git_sha: str
    uv_lock_checksum: str
    created_at: str


@dataclass(frozen=True)
class FrozenTrainingParameters:
    revision: str
    digest: str
    values: tuple[tuple[str, ParameterValue], ...]

    def parameter_map(self) -> dict[str, ParameterValue]:
        return dict(self.values)


@dataclass(frozen=True)
class LightGBMArtifactPredictor:
    _booster: Any
    artifact_id: str
    provenance_differences: tuple[str, ...]

    def predict(
        self,
        frame: pl.DataFrame,
        *,
        feature_schema_digest: str,
    ) -> tuple[float, ...]:
        if feature_schema_digest != FEATURE_SCHEMA_DIGEST:
            raise Stage3ArtifactError("prediction feature schema digest does not match")
        _require_prediction_frame(frame)
        if tuple(self._booster.feature_name()) != FEATURE_NAMES:
            raise Stage3ArtifactError("loaded Booster feature names changed")
        if self._booster.num_feature() != len(FEATURE_NAMES):
            raise Stage3ArtifactError("loaded Booster feature count changed")
        raw = self._booster.predict(
            frame.to_numpy(),
            num_iteration=self._booster.current_iteration(),
            validate_features=True,
        )
        shape = getattr(raw, "shape", None)
        if shape != (frame.height,):
            raise Stage3ArtifactError("prediction output shape does not match input")
        predictions = tuple(float(value) for value in raw)
        if any(not math.isfinite(value) for value in predictions):
            raise Stage3ArtifactError("prediction output is not finite")
        return predictions


def default_effective_parameters() -> dict[str, ParameterValue]:
    """Return the complete, versioned v1 baseline parameter map."""
    return {
        "objective": "regression",
        "metric": "l2",
        "boosting_type": "gbdt",
        "data_sample_strategy": "bagging",
        "tree_learner": "serial",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_depth": -1,
        "min_data_in_leaf": 20,
        "min_sum_hessian_in_leaf": 0.001,
        "feature_fraction": 1.0,
        "feature_fraction_bynode": 1.0,
        "bagging_fraction": 1.0,
        "bagging_freq": 0,
        "lambda_l1": 0.0,
        "lambda_l2": 0.0,
        "min_gain_to_split": 0.0,
        "max_delta_step": 0.0,
        "path_smooth": 0.0,
        "extra_trees": False,
        "linear_tree": False,
        "max_bin": 255,
        "min_data_in_bin": 3,
        "bin_construct_sample_cnt": 200_000,
        "boost_from_average": True,
        "reg_sqrt": False,
        "use_missing": False,
        "zero_as_missing": False,
        "feature_pre_filter": True,
        "seed": 362,
        "bagging_seed": 362,
        "feature_fraction_seed": 362,
        "data_random_seed": 362,
        "deterministic": True,
        "force_row_wise": True,
        "force_col_wise": False,
        "num_threads": 1,
        "device_type": "cpu",
        "verbosity": -1,
        "num_boost_round": 64,
    }


def locked_stage2_identity() -> Stage2ArtifactIdentity:
    return Stage2ArtifactIdentity(
        dataset_id=STAGE2_DATASET_ID,
        acceptance_digest=STAGE2_ACCEPTANCE_DIGEST,
        dataset_digest=STAGE2_DATASET_DIGEST,
        source_manifest_digest=STAGE2_SOURCE_MANIFEST_DIGEST,
        market_data_manifest_digest=STAGE2_MARKET_DATA_MANIFEST_DIGEST,
        instrument_snapshot_checksum=STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM,
        runtime_identity=UPSTREAM_RELEASE_IDENTITY,
    )


def freeze_training_parameters(
    path: Path,
    *,
    revision: str,
    parameters: Mapping[str, ParameterValue],
    repository_root: Path,
) -> FrozenTrainingParameters:
    """Write one immutable parameter record before any artifact is trained."""
    target = _require_external_file_target(
        path, repository_root=repository_root, description="parameter record"
    )
    _require_revision(revision)
    normalized = _validate_training_parameters(parameters)
    digest = _parameter_digest(normalized)
    payload: dict[str, object] = {
        "schema": PARAMETER_RECORD_SCHEMA,
        "canonical_json": CANONICAL_JSON_RULE,
        "training_parameter_revision": revision,
        "training_parameter_digest": digest,
        "parameters": normalized,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(_canonical_json(payload))
            stream.write("\n")
    except FileExistsError as exc:
        raise Stage3ArtifactError("training parameter record is immutable") from exc
    return FrozenTrainingParameters(
        revision=revision,
        digest=digest,
        values=tuple((key, normalized[key]) for key in sorted(normalized)),
    )


def load_training_parameters(path: Path) -> FrozenTrainingParameters:
    payload = _read_json_object(path, "training parameter record")
    _require_keys(
        payload,
        {
            "schema",
            "canonical_json",
            "training_parameter_revision",
            "training_parameter_digest",
            "parameters",
        },
        "training parameter record",
    )
    if payload["schema"] != PARAMETER_RECORD_SCHEMA:
        raise Stage3ArtifactError("training parameter record schema does not match")
    if payload["canonical_json"] != CANONICAL_JSON_RULE:
        raise Stage3ArtifactError(
            "training parameter canonical JSON rule does not match"
        )
    revision = _require_revision(
        _required_string(
            payload, "training_parameter_revision", "training parameter record"
        )
    )
    parameters = payload["parameters"]
    if not isinstance(parameters, Mapping):
        raise Stage3ArtifactError("training parameter map is invalid")
    normalized = _validate_training_parameters(cast(Mapping[str, object], parameters))
    digest = _parameter_digest(normalized)
    if payload["training_parameter_digest"] != digest:
        raise Stage3ArtifactError("training parameter digest does not match")
    return FrozenTrainingParameters(
        revision=revision,
        digest=digest,
        values=tuple((key, normalized[key]) for key in sorted(normalized)),
    )


def synthetic_fixture_provenance(*, created_at: str) -> TrainingProvenance:
    _require_utc_text(created_at, "fixture created_at")
    return TrainingProvenance(
        kind="synthetic_fixture",
        git_sha="fixture-" + "0" * 56,
        uv_lock_checksum="fixture-" + "0" * 56,
        created_at=created_at,
    )


def capture_formal_provenance(
    repository_root: Path, *, created_at: datetime | None = None
) -> TrainingProvenance:
    """Capture provenance only from a clean, identifiable Git commit."""
    root = Path(repository_root).resolve()
    if not (root / ".git").exists() or not (root / "uv.lock").is_file():
        raise Stage3ArtifactError("formal training repository identity is missing")
    status = _run_git(root, "status", "--porcelain")
    if status:
        raise Stage3ArtifactError("formal artifact training requires a clean Git tree")
    git_sha = _run_git(root, "rev-parse", "HEAD")
    _require_git_sha(git_sha, "formal training Git SHA")
    timestamp = created_at if created_at is not None else datetime.now(UTC)
    timestamp = require_utc(timestamp)
    return TrainingProvenance(
        kind="formal_git",
        git_sha=git_sha,
        uv_lock_checksum=sha256_file(root / "uv.lock"),
        created_at=timestamp.isoformat().replace("+00:00", "Z"),
    )


def train_lightgbm_artifact(
    config: Stage3Config,
    *,
    acceptance_record_path: Path,
    output_partition: Path,
    parameter_record_path: Path,
    window: ArtifactWindow,
    provenance: TrainingProvenance,
    repository_root: Path,
) -> dict[str, object]:
    """Train from the complete accepted Stage 2 gate owned by Stage 3 features."""
    output_partition, parameter_record_path = _require_formal_evidence_paths(
        config,
        output_partition=output_partition,
        parameter_record_path=parameter_record_path,
        repository_root=repository_root,
    )
    if window.train_end != window.evaluation_start:
        raise Stage3ArtifactError(
            "formal artifact training requires the evaluation boundary at train_end"
        )
    rows_by_instrument = load_accepted_feature_window(
        config,
        acceptance_record_path=acceptance_record_path,
        start=window.train_start,
        end=window.train_end,
        decision_start=window.evaluation_start,
        mode="training",
    )
    frames = [
        feature_frame(rows_by_instrument[instrument_id])
        for instrument_id in STAGE2_INSTRUMENT_IDS
    ]
    frame = pl.concat(frames).sort(["decision_ts", "instrument_id"])
    identity = Stage2ArtifactIdentity(
        dataset_id=config.dataset_id,
        acceptance_digest=config.acceptance_digest,
        dataset_digest=config.dataset_digest,
        source_manifest_digest=config.source_manifest_digest,
        market_data_manifest_digest=config.market_data_manifest_digest,
        instrument_snapshot_checksum=config.instrument_snapshot_checksum,
        runtime_identity=config.runtime_identity,
    )
    return _train_lightgbm_artifact(
        frame,
        output_partition=output_partition,
        parameter_record_path=parameter_record_path,
        stage2_identity=identity,
        window=window,
        provenance=provenance,
        repository_root=repository_root,
        fixture_mode=False,
    )


def train_fixture_lightgbm_artifact(
    frame: pl.DataFrame,
    *,
    output_partition: Path,
    parameter_record_path: Path,
    stage2_identity: Stage2ArtifactIdentity,
    window: ArtifactWindow,
    provenance: TrainingProvenance,
    repository_root: Path,
) -> dict[str, object]:
    """Exercise artifact behavior with an explicit synthetic fixture identity."""
    return _train_lightgbm_artifact(
        frame,
        output_partition=output_partition,
        parameter_record_path=parameter_record_path,
        stage2_identity=stage2_identity,
        window=window,
        provenance=provenance,
        repository_root=repository_root,
        fixture_mode=True,
    )


def _train_lightgbm_artifact(
    frame: pl.DataFrame,
    *,
    output_partition: Path,
    parameter_record_path: Path,
    stage2_identity: Stage2ArtifactIdentity,
    window: ArtifactWindow,
    provenance: TrainingProvenance,
    repository_root: Path,
    fixture_mode: bool,
) -> dict[str, object]:
    root = Path(repository_root).resolve()
    partition = _require_external_partition(
        output_partition, repository_root=root, require_empty=True
    )
    _require_external_file_target(
        parameter_record_path,
        repository_root=root,
        description="parameter record",
    )
    frozen = load_training_parameters(parameter_record_path)
    _require_locked_stage2_identity(stage2_identity)
    window_payload = _validate_window(window)
    training_frame = _validate_training_frame(frame, window)
    _validate_provenance(provenance, repository_root=root, fixture_mode=fixture_mode)

    lightgbm = _load_lightgbm()
    runtime = _runtime_identity(lightgbm, repository_root=root)
    parameters = frozen.parameter_map()
    num_boost_round = cast(int, parameters.pop("num_boost_round"))
    dataset = lightgbm.Dataset(
        training_frame.select(FEATURE_NAMES).to_numpy(),
        label=training_frame.get_column("label_log_return_4h").to_numpy(),
        feature_name=list(FEATURE_NAMES),
        free_raw_data=False,
    )
    booster = lightgbm.train(
        parameters,
        dataset,
        num_boost_round=num_boost_round,
        valid_sets=None,
        callbacks=None,
    )
    if tuple(booster.feature_name()) != FEATURE_NAMES:
        raise Stage3ArtifactError("trained Booster feature names do not match")
    if booster.num_feature() != len(FEATURE_NAMES):
        raise Stage3ArtifactError("trained Booster feature count does not match")

    claim_path = _claim_artifact_partition(partition)
    model_path = partition / MODEL_FILENAME
    manifest_path = partition / MANIFEST_FILENAME
    pending_directory = partition / PENDING_MODEL_DIRECTORY
    try:
        pending_directory.mkdir()
    except FileExistsError as exc:
        raise Stage3ArtifactError(
            "artifact model publication is already pending"
        ) from exc
    pending_model_path = pending_directory / MODEL_FILENAME
    booster.save_model(
        str(pending_model_path),
        num_iteration=booster.current_iteration(),
        importance_type="split",
    )
    try:
        os.link(pending_model_path, model_path)
    except FileExistsError as exc:
        raise Stage3ArtifactError("artifact model would overwrite a file") from exc
    except OSError as exc:
        raise Stage3ArtifactError(
            "artifact model could not be published atomically"
        ) from exc
    pending_model_path.unlink()
    pending_directory.rmdir()
    model_checksum = sha256_file(model_path)
    manifest = _build_manifest(
        stage2_identity=stage2_identity,
        frozen=frozen,
        window=window_payload,
        train_first_row_ts=cast(int, training_frame.get_column("decision_ts").min()),
        provenance=provenance,
        runtime=runtime,
        model_checksum=model_checksum,
    )
    try:
        with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(_canonical_json(manifest))
            stream.write("\n")
    except FileExistsError as exc:
        raise Stage3ArtifactError("artifact manifest would overwrite a file") from exc
    try:
        claim_path.unlink()
    except OSError as exc:
        raise Stage3ArtifactError(
            "artifact partition claim could not be released"
        ) from exc
    return manifest


def load_lightgbm_artifact(
    artifact_partition: Path,
    *,
    parameter_record_path: Path,
    expected_stage2_identity: Stage2ArtifactIdentity,
    repository_root: Path,
) -> LightGBMArtifactPredictor:
    """Load one formal artifact through the production inference boundary."""
    return _load_lightgbm_artifact(
        artifact_partition,
        parameter_record_path=parameter_record_path,
        expected_stage2_identity=expected_stage2_identity,
        repository_root=repository_root,
        expected_provenance_kind="formal_git",
    )


def _load_lightgbm_artifact(
    artifact_partition: Path,
    *,
    parameter_record_path: Path,
    expected_stage2_identity: Stage2ArtifactIdentity,
    repository_root: Path,
    expected_provenance_kind: ProvenanceKind,
) -> LightGBMArtifactPredictor:
    """Shared loader used by the formal boundary and fixture-only tests."""
    root = Path(repository_root).resolve()
    partition = _require_external_partition(
        artifact_partition, repository_root=root, require_empty=False
    )
    _require_external_file_target(
        parameter_record_path,
        repository_root=root,
        description="parameter record",
    )
    expected_files = {MANIFEST_FILENAME, MODEL_FILENAME}
    if (
        not partition.is_dir()
        or {item.name for item in partition.iterdir()} != expected_files
    ):
        raise Stage3ArtifactError("artifact partition contents do not match the schema")
    manifest = _read_json_object(partition / MANIFEST_FILENAME, "artifact manifest")
    _validate_manifest_envelope(manifest)
    provenance = _required_mapping(manifest, "provenance", "artifact manifest")
    if provenance["kind"] != expected_provenance_kind:
        raise Stage3ArtifactError(
            f"artifact loader requires {expected_provenance_kind} provenance"
        )
    if manifest["stage2_input"] != _stage2_identity_payload(expected_stage2_identity):
        raise Stage3ArtifactError("artifact Stage 2 input identity does not match")
    _require_locked_stage2_identity(expected_stage2_identity)

    frozen = load_training_parameters(parameter_record_path)
    training = _required_mapping(manifest, "training", "artifact manifest")
    if (
        training["training_parameter_revision"] != frozen.revision
        or training["training_parameter_digest"] != frozen.digest
        or training["parameters"] != frozen.parameter_map()
        or training["parameter_record_schema"] != PARAMETER_RECORD_SCHEMA
    ):
        raise Stage3ArtifactError("artifact parameters do not match the frozen record")

    if manifest["features"] != _feature_payload():
        raise Stage3ArtifactError("artifact feature schema identity does not match")
    if manifest["label"] != _label_payload():
        raise Stage3ArtifactError("artifact label identity does not match")
    if manifest["instruments"] != _instrument_payload():
        raise Stage3ArtifactError("artifact instrument identity does not match")

    lightgbm = _load_lightgbm()
    current_runtime = _runtime_identity(lightgbm, repository_root=root)
    if manifest["environment"] != current_runtime["environment"]:
        raise Stage3ArtifactError(
            "artifact binary or environment identity does not match"
        )
    if manifest["compatibility"] != current_runtime["compatibility"]:
        raise Stage3ArtifactError("artifact code or dependency identity does not match")

    model = _required_mapping(manifest, "model", "artifact manifest")
    model_path = partition / MODEL_FILENAME
    if model["checksum_sha256"] != sha256_file(model_path):
        raise Stage3ArtifactError("artifact model checksum does not match")
    try:
        booster = lightgbm.Booster(model_file=str(model_path))
    except Exception as exc:
        raise Stage3ArtifactError("artifact model file cannot be loaded") from exc
    if tuple(booster.feature_name()) != FEATURE_NAMES:
        raise Stage3ArtifactError("artifact Booster feature names do not match")
    if booster.num_feature() != len(FEATURE_NAMES):
        raise Stage3ArtifactError("artifact Booster feature count does not match")
    return LightGBMArtifactPredictor(
        _booster=booster,
        artifact_id=cast(str, manifest["artifact_id"]),
        provenance_differences=_provenance_differences(provenance, root),
    )


def _validate_training_parameters(
    parameters: Mapping[str, object],
) -> dict[str, ParameterValue]:
    if set(parameters) != set(PARAMETER_KEYS):
        raise Stage3ArtifactError(
            "training parameters contain missing, unknown, or alias keys"
        )
    normalized = dict(parameters)
    expected_strings = {
        "objective": "regression",
        "metric": "l2",
        "boosting_type": "gbdt",
        "data_sample_strategy": "bagging",
        "tree_learner": "serial",
        "device_type": "cpu",
    }
    for key, expected_string in expected_strings.items():
        if normalized[key] != expected_string:
            raise Stage3ArtifactError(f"training parameter {key} is unsupported")
    for key, expected_boolean in {
        "boost_from_average": True,
        "reg_sqrt": False,
        "use_missing": False,
        "zero_as_missing": False,
        "feature_pre_filter": True,
        "extra_trees": False,
        "linear_tree": False,
        "deterministic": True,
        "force_row_wise": True,
        "force_col_wise": False,
    }.items():
        if normalized[key] is not expected_boolean:
            raise Stage3ArtifactError(f"training parameter {key} is unsupported")
    integer_bounds = {
        "num_leaves": (2, None),
        "max_depth": (-1, None),
        "min_data_in_leaf": (1, None),
        "bagging_freq": (0, None),
        "max_bin": (2, None),
        "min_data_in_bin": (1, None),
        "bin_construct_sample_cnt": (1, None),
        "seed": (0, None),
        "bagging_seed": (0, None),
        "feature_fraction_seed": (0, None),
        "data_random_seed": (0, None),
        "num_boost_round": (1, None),
    }
    for key, (minimum, maximum) in integer_bounds.items():
        integer_value = normalized[key]
        if isinstance(integer_value, bool) or not isinstance(integer_value, int):
            raise Stage3ArtifactError(f"training parameter {key} must be an integer")
        if integer_value < minimum or (maximum is not None and integer_value > maximum):
            raise Stage3ArtifactError(f"training parameter {key} is out of range")
    if normalized["num_threads"] != 1 or type(normalized["num_threads"]) is not int:
        raise Stage3ArtifactError("training parameter num_threads must be one")
    if normalized["verbosity"] != -1 or type(normalized["verbosity"]) is not int:
        raise Stage3ArtifactError("training parameter verbosity is unsupported")
    finite_nonnegative = (
        "min_sum_hessian_in_leaf",
        "lambda_l1",
        "lambda_l2",
        "min_gain_to_split",
        "max_delta_step",
        "path_smooth",
    )
    finite_unit = (
        "learning_rate",
        "feature_fraction",
        "feature_fraction_bynode",
        "bagging_fraction",
    )
    for key in finite_nonnegative:
        float_value = normalized[key]
        if (
            type(float_value) is not float
            or not math.isfinite(float_value)
            or float_value < 0.0
        ):
            raise Stage3ArtifactError(f"training parameter {key} is invalid")
    for key in finite_unit:
        unit_value = normalized[key]
        if (
            type(unit_value) is not float
            or not math.isfinite(unit_value)
            or not 0.0 < unit_value <= 1.0
        ):
            raise Stage3ArtifactError(f"training parameter {key} is invalid")
    return cast(dict[str, ParameterValue], normalized)


def _validate_training_frame(
    frame: pl.DataFrame, window: ArtifactWindow
) -> pl.DataFrame:
    try:
        require_feature_frame_schema(frame)
    except ValueError as exc:
        raise Stage3ArtifactError(str(exc)) from exc
    expected_dtypes = {
        "instrument_id": pl.String,
        "decision_ts": pl.Int64,
        **{name: pl.Float64 for name in FEATURE_NAMES},
        "feature_schema_digest": pl.String,
        "label_available": pl.Boolean,
        "label_log_return_4h": pl.Float64,
        "label_end_ts": pl.Int64,
        "tradable": pl.Boolean,
    }
    if (
        frame.schema != expected_dtypes
        or frame.is_empty()
        or frame.null_count().sum_horizontal()[0] != 0
    ):
        raise Stage3ArtifactError(
            "training frame dtype, emptiness, or null contract failed"
        )
    ordered = frame.sort(["decision_ts", "instrument_id"])
    if not frame.equals(ordered):
        raise Stage3ArtifactError("training frame row order is not canonical")
    if frame.select(
        pl.struct(["instrument_id", "decision_ts"]).is_duplicated().any()
    ).item():
        raise Stage3ArtifactError("training frame contains duplicate panel rows")
    instruments = tuple(frame.get_column("instrument_id").unique(maintain_order=True))
    if set(instruments) != set(STAGE2_INSTRUMENT_IDS):
        raise Stage3ArtifactError("training frame is not the complete BTC/ETH panel")
    panel = frame.group_by("decision_ts").agg(
        pl.len().alias("row_count"),
        pl.col("instrument_id").n_unique().alias("instrument_count"),
    )
    if panel.filter(
        (pl.col("row_count") != len(STAGE2_INSTRUMENT_IDS))
        | (pl.col("instrument_count") != len(STAGE2_INSTRUMENT_IDS))
    ).height:
        raise Stage3ArtifactError("training frame is not the complete BTC/ETH panel")
    if not frame.get_column("label_available").all():
        raise Stage3ArtifactError("training frame contains unavailable labels")
    if frame.get_column("tradable").any():
        raise Stage3ArtifactError("training warm-up rows must not be tradable")
    start_ns = datetime_to_nanos(require_utc(window.train_start))
    end_ns = datetime_to_nanos(require_utc(window.train_end))
    purge_ns = datetime_to_nanos(require_utc(window.evaluation_start))
    first_row_ts = cast(int, frame.get_column("decision_ts").min())
    earliest_complete_row = start_ns + (FEATURE_LOOKBACK_HOURS + 1) * HOUR_NS - MS_NS
    if first_row_ts < earliest_complete_row:
        raise Stage3ArtifactError("training window head did not remain warm-up context")
    for row in frame.iter_rows(named=True):
        decision_ts = cast(int, row["decision_ts"])
        label_end_ts = cast(int, row["label_end_ts"])
        if not start_ns <= decision_ts < end_ns:
            raise Stage3ArtifactError("training row is outside the declared window")
        if label_end_ts != decision_ts + LABEL_HORIZON_HOURS * HOUR_NS:
            raise Stage3ArtifactError("training label horizon does not match")
        if label_end_ts >= purge_ns:
            raise Stage3ArtifactError("training row crosses the purge boundary")
        expected_code = 0.0 if row["instrument_id"] == STAGE2_INSTRUMENT_IDS[0] else 1.0
        if row["instrument_code"] != expected_code:
            raise Stage3ArtifactError("training instrument code does not match")
        numeric = [cast(float, row[name]) for name in FEATURE_NAMES]
        numeric.append(cast(float, row["label_log_return_4h"]))
        if any(not math.isfinite(value) for value in numeric):
            raise Stage3ArtifactError("training frame contains NaN or infinity")
    return frame


def _require_prediction_frame(frame: pl.DataFrame) -> None:
    if tuple(frame.columns) != FEATURE_NAMES or frame.is_empty():
        raise Stage3ArtifactError("prediction feature names or order do not match")
    if any(frame.schema[name] != pl.Float64 for name in FEATURE_NAMES):
        raise Stage3ArtifactError("prediction feature dtype does not match")
    if frame.null_count().sum_horizontal()[0] != 0:
        raise Stage3ArtifactError("prediction features contain nulls")
    for row in frame.iter_rows():
        if any(not math.isfinite(cast(float, value)) for value in row):
            raise Stage3ArtifactError("prediction features contain NaN or infinity")


def _validate_window(window: ArtifactWindow) -> dict[str, object]:
    train_start = require_utc(window.train_start)
    train_end = require_utc(window.train_end)
    evaluation_start = require_utc(window.evaluation_start)
    evaluation_end = require_utc(window.evaluation_end)
    if window.role not in {"development", "validation", "final_test"}:
        raise Stage3ArtifactError("artifact window role is invalid")
    if not train_start < train_end <= evaluation_start < evaluation_end:
        raise Stage3ArtifactError("artifact train/evaluation window is invalid")
    return {
        "train_start": _utc_text(train_start),
        "train_end": _utc_text(train_end),
        "purge_boundary": _utc_text(evaluation_start),
        "evaluation_start": _utc_text(evaluation_start),
        "evaluation_end": _utc_text(evaluation_end),
        "role": window.role,
    }


def _build_manifest(
    *,
    stage2_identity: Stage2ArtifactIdentity,
    frozen: FrozenTrainingParameters,
    window: Mapping[str, object],
    train_first_row_ts: int,
    provenance: TrainingProvenance,
    runtime: Mapping[str, object],
    model_checksum: str,
) -> dict[str, object]:
    training: dict[str, object] = {
        "parameter_record_schema": PARAMETER_RECORD_SCHEMA,
        "parameters": frozen.parameter_map(),
        "training_parameter_revision": frozen.revision,
        "training_parameter_digest": frozen.digest,
        "lightgbm_version": LIGHTGBM_VERSION,
        "early_stopping": False,
        "parameter_search": False,
    }
    window_payload = dict(window)
    window_payload["train_first_row_ts"] = train_first_row_ts
    manifest: dict[str, object] = {
        "schema": ARTIFACT_MANIFEST_SCHEMA,
        "canonical_json": CANONICAL_JSON_RULE,
        "artifact_identity_fields": list(ARTIFACT_IDENTITY_FIELDS),
        "stage2_input": _stage2_identity_payload(stage2_identity),
        "features": _feature_payload(),
        "label": _label_payload(),
        "instruments": _instrument_payload(),
        "window": window_payload,
        "training": training,
        "environment": runtime["environment"],
        "compatibility": runtime["compatibility"],
        "provenance": {
            "kind": provenance.kind,
            "git_sha": provenance.git_sha,
            "uv_lock_checksum": provenance.uv_lock_checksum,
            "created_at": provenance.created_at,
        },
        "model": {
            "filename": MODEL_FILENAME,
            "format": "lightgbm-native-text",
            "checksum_sha256": model_checksum,
            "prediction_atol": PREDICTION_ATOL,
            "prediction_rtol": PREDICTION_RTOL,
        },
    }
    manifest["artifact_id"] = _artifact_id(manifest)
    manifest["manifest_digest"] = _manifest_digest(manifest)
    return manifest


def _validate_manifest_envelope(manifest: Mapping[str, object]) -> None:
    _require_keys(
        manifest,
        {
            "schema",
            "canonical_json",
            "artifact_id",
            "manifest_digest",
            "artifact_identity_fields",
            *ARTIFACT_IDENTITY_FIELDS,
            "provenance",
        },
        "artifact manifest",
    )
    if manifest["schema"] != ARTIFACT_MANIFEST_SCHEMA:
        raise Stage3ArtifactError("artifact manifest schema does not match")
    if manifest["canonical_json"] != CANONICAL_JSON_RULE:
        raise Stage3ArtifactError("artifact canonical JSON rule does not match")
    _require_sha256(manifest["artifact_id"], "artifact id")
    _require_sha256(manifest["manifest_digest"], "artifact manifest digest")
    if manifest["artifact_identity_fields"] != list(ARTIFACT_IDENTITY_FIELDS):
        raise Stage3ArtifactError("artifact identity field contract does not match")
    if manifest["manifest_digest"] != _manifest_digest(manifest):
        raise Stage3ArtifactError("artifact manifest digest does not match")
    if manifest["artifact_id"] != _artifact_id(manifest):
        raise Stage3ArtifactError("artifact id does not match")
    training = _required_mapping(manifest, "training", "artifact manifest")
    _require_keys(
        training,
        {
            "parameter_record_schema",
            "parameters",
            "training_parameter_revision",
            "training_parameter_digest",
            "lightgbm_version",
            "early_stopping",
            "parameter_search",
        },
        "artifact training identity",
    )
    if (
        training["parameter_record_schema"] != PARAMETER_RECORD_SCHEMA
        or training["lightgbm_version"] != LIGHTGBM_VERSION
        or training["early_stopping"] is not False
        or training["parameter_search"] is not False
    ):
        raise Stage3ArtifactError("artifact training policy does not match")
    parameters = training["parameters"]
    if not isinstance(parameters, Mapping):
        raise Stage3ArtifactError("artifact training parameters are invalid")
    normalized = _validate_training_parameters(cast(Mapping[str, object], parameters))
    _require_revision(
        _required_string(
            training, "training_parameter_revision", "artifact training identity"
        )
    )
    _require_sha256(training["training_parameter_digest"], "training parameter digest")
    if training["training_parameter_digest"] != _parameter_digest(normalized):
        raise Stage3ArtifactError("artifact training parameter digest does not match")
    model = _required_mapping(manifest, "model", "artifact manifest")
    _require_keys(
        model,
        {
            "filename",
            "format",
            "checksum_sha256",
            "prediction_atol",
            "prediction_rtol",
        },
        "artifact model",
    )
    if model["filename"] != MODEL_FILENAME or model["format"] != "lightgbm-native-text":
        raise Stage3ArtifactError("artifact model format does not match")
    if (
        model["prediction_atol"] != PREDICTION_ATOL
        or model["prediction_rtol"] != PREDICTION_RTOL
    ):
        raise Stage3ArtifactError("artifact prediction tolerance does not match")
    _require_sha256(model["checksum_sha256"], "artifact model checksum")
    _validate_manifest_window(
        _required_mapping(manifest, "window", "artifact manifest")
    )
    _validate_manifest_provenance(
        _required_mapping(manifest, "provenance", "artifact manifest")
    )


def _validate_manifest_window(window: Mapping[str, object]) -> None:
    _require_keys(
        window,
        {
            "train_start",
            "train_end",
            "train_first_row_ts",
            "purge_boundary",
            "evaluation_start",
            "evaluation_end",
            "role",
        },
        "artifact window",
    )
    train_start = _require_utc_text(
        _required_string(window, "train_start", "artifact window"), "train_start"
    )
    train_end = _require_utc_text(
        _required_string(window, "train_end", "artifact window"), "train_end"
    )
    purge_boundary = _require_utc_text(
        _required_string(window, "purge_boundary", "artifact window"),
        "purge_boundary",
    )
    evaluation_start = _require_utc_text(
        _required_string(window, "evaluation_start", "artifact window"),
        "evaluation_start",
    )
    evaluation_end = _require_utc_text(
        _required_string(window, "evaluation_end", "artifact window"),
        "evaluation_end",
    )
    if not train_start < train_end <= evaluation_start < evaluation_end:
        raise Stage3ArtifactError("artifact manifest window is invalid")
    if purge_boundary != evaluation_start:
        raise Stage3ArtifactError("artifact manifest purge boundary is invalid")
    if window["role"] not in {"development", "validation", "final_test"}:
        raise Stage3ArtifactError("artifact manifest window role is invalid")
    first_row_ts = window["train_first_row_ts"]
    if isinstance(first_row_ts, bool) or not isinstance(first_row_ts, int):
        raise Stage3ArtifactError("artifact manifest first training row is invalid")
    earliest = (
        datetime_to_nanos(train_start) + (FEATURE_LOOKBACK_HOURS + 1) * HOUR_NS - MS_NS
    )
    if not earliest <= first_row_ts < datetime_to_nanos(train_end):
        raise Stage3ArtifactError("artifact manifest first training row is invalid")


def _validate_manifest_provenance(provenance: Mapping[str, object]) -> None:
    _require_keys(
        provenance,
        {"kind", "git_sha", "uv_lock_checksum", "created_at"},
        "artifact provenance",
    )
    kind = provenance["kind"]
    git_sha = _required_string(provenance, "git_sha", "artifact provenance")
    lock_checksum = _required_string(
        provenance, "uv_lock_checksum", "artifact provenance"
    )
    _require_utc_text(
        _required_string(provenance, "created_at", "artifact provenance"),
        "artifact provenance created_at",
    )
    if kind == "synthetic_fixture":
        if not git_sha.startswith("fixture-") or not lock_checksum.startswith(
            "fixture-"
        ):
            raise Stage3ArtifactError("artifact fixture provenance is invalid")
    elif kind == "formal_git":
        _require_git_sha(git_sha, "artifact provenance Git SHA")
        _require_sha256(lock_checksum, "artifact provenance lock checksum")
    else:
        raise Stage3ArtifactError("artifact provenance kind is invalid")


def _runtime_identity(lightgbm: Any, *, repository_root: Path) -> dict[str, object]:
    if lightgbm.__version__ != LIGHTGBM_VERSION:
        raise Stage3ArtifactError("installed LightGBM version does not match")
    native_name = getattr(lightgbm.basic._LIB, "_name", None)
    if not isinstance(native_name, str):
        raise Stage3ArtifactError("loaded LightGBM native library has no identity")
    native_path = Path(native_name).resolve()
    if not native_path.is_file():
        raise Stage3ArtifactError("loaded LightGBM native library is missing")
    distribution = importlib.metadata.distribution("lightgbm")
    metadata_text = distribution.read_text("METADATA")
    if metadata_text is None:
        raise Stage3ArtifactError("LightGBM distribution metadata is missing")
    nautilus_version = importlib.metadata.version("nautilus-trader")
    if nautilus_version != NAUTILUS_VERSION:
        raise Stage3ArtifactError("installed Nautilus version does not match")
    dependencies = [
        {
            "name": name,
            "version": importlib.metadata.version(name),
        }
        for name in RUNTIME_DEPENDENCIES
    ]
    dependency_payload: dict[str, object] = {
        "schema": "tracequant-stage3-model-runtime-dependencies-v1",
        "packages": dependencies,
    }
    code_inventory = _code_inventory(repository_root)
    environment = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_abi": sysconfig.get_config_var("SOABI"),
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "polars_version": pl.__version__,
        "nautilus_version": nautilus_version,
        "lightgbm_distribution": {
            "name": "lightgbm",
            "version": distribution.version,
            "metadata_checksum_sha256": hashlib.sha256(
                metadata_text.encode("utf-8")
            ).hexdigest(),
        },
        "lightgbm_native_library": {
            "filename": native_path.name,
            "checksum_sha256": sha256_file(native_path),
        },
    }
    compatibility = {
        "stage2_runtime_identity": UPSTREAM_RELEASE_IDENTITY,
        "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
        "code_inventory": code_inventory,
        "code_inventory_order": "fixed-module-order-v1",
        "code_digest": _code_digest(code_inventory),
        "runtime_dependencies": dependencies,
        "runtime_dependency_order": "normalized-name-ascending-v1",
        "runtime_dependency_digest": _digest(dependency_payload),
    }
    return {"environment": environment, "compatibility": compatibility}


def _code_inventory(repository_root: Path) -> list[dict[str, object]]:
    root = Path(repository_root).resolve()
    entries: list[dict[str, object]] = []
    for module, path, roles in _code_inventory_paths():
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise Stage3ArtifactError(
                "model code is not loaded from the repository"
            ) from exc
        entries.append(
            {
                "module": module,
                "relative_path": relative,
                "roles": list(roles),
                "checksum_sha256": sha256_file(path),
            }
        )
    return entries


def _code_inventory_paths() -> tuple[CodeInventoryPath, ...]:
    return (
        (
            "tracequant.integrations.nautilus",
            _module_path(nautilus_integration),
            ("stage2_runtime_identity",),
        ),
        (
            "tracequant.integrations.nautilus.stage2_artifact",
            _module_path(stage2_artifact),
            ("checksum_gate", "accepted_artifact_lock_gate"),
        ),
        (
            "tracequant.integrations.nautilus.stage2_btceth",
            _module_path(nautilus_stage2_btceth),
            ("catalog_identity_gate", "catalog_reader"),
        ),
        (
            "tracequant.research.stage3_artifacts",
            Path(__file__).resolve(),
            ("trainer", "artifact_loader", "prediction_wrapper"),
        ),
        (
            "tracequant.research.source_schema",
            _module_path(source_schema),
            ("query_window_gate", "timestamp_gate"),
        ),
        (
            "tracequant.research.stage3_features",
            _module_path(stage3_features),
            ("feature_schema", "label_contract", "accepted_input_gate"),
        ),
        (
            "tracequant.research.views",
            _module_path(research_views),
            ("accepted_catalog_reader",),
        ),
        (
            "tracequant.source_data.stage2_btceth",
            _module_path(source_stage2_btceth),
            ("stage2_identity_contract", "time_and_bar_identity_gate"),
        ),
    )


def _module_path(module: Any) -> Path:
    path = getattr(module, "__file__", None)
    if not isinstance(path, str):
        raise Stage3ArtifactError("model code module has no repository path")
    return Path(path).resolve()


def _code_digest(inventory: Sequence[Mapping[str, object]]) -> str:
    return _digest({"schema": CODE_INVENTORY_SCHEMA, "entries": list(inventory)})


def _feature_payload() -> dict[str, object]:
    return {
        "ordered_schema": [dict(item) for item in FEATURE_SCHEMA],
        "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
        "schema_contract": feature_schema_payload(),
    }


def _label_payload() -> dict[str, object]:
    return {
        "name": "label_log_return_4h",
        "horizon_hours": LABEL_HORIZON_HOURS,
        "entry": "next 1h bar open",
        "exit": "fourth horizon 1h bar close",
        "label_end_ts": "decision_ts + 4h",
        "purge_rule": "label_end_ts < evaluation_start",
    }


def _instrument_payload() -> dict[str, object]:
    return {
        "instrument_ids": list(STAGE2_INSTRUMENT_IDS),
        "bar_types": [
            stage2_bar_type_str(instrument_id, "1h")
            for instrument_id in STAGE2_INSTRUMENT_IDS
        ],
        "instrument_code": {
            STAGE2_INSTRUMENT_IDS[0]: 0.0,
            STAGE2_INSTRUMENT_IDS[1]: 1.0,
        },
        "categorical_features": [],
    }


def _stage2_identity_payload(identity: Stage2ArtifactIdentity) -> dict[str, str]:
    return {
        "dataset_id": identity.dataset_id,
        "acceptance_digest": identity.acceptance_digest,
        "dataset_digest": identity.dataset_digest,
        "source_manifest_digest": identity.source_manifest_digest,
        "market_data_manifest_digest": identity.market_data_manifest_digest,
        "instrument_snapshot_checksum": identity.instrument_snapshot_checksum,
        "runtime_identity": identity.runtime_identity,
    }


def _require_locked_stage2_identity(identity: Stage2ArtifactIdentity) -> None:
    if identity != locked_stage2_identity():
        raise Stage3ArtifactError("Stage 2 input identity does not match acceptance")


def _validate_provenance(
    provenance: TrainingProvenance, *, repository_root: Path, fixture_mode: bool
) -> None:
    _require_utc_text(provenance.created_at, "artifact created_at")
    if provenance.kind == "synthetic_fixture":
        if not fixture_mode:
            raise Stage3ArtifactError(
                "formal artifact training rejects synthetic fixture provenance"
            )
        if not provenance.git_sha.startswith(
            "fixture-"
        ) or not provenance.uv_lock_checksum.startswith("fixture-"):
            raise Stage3ArtifactError("synthetic fixture provenance is invalid")
        return
    if provenance.kind != "formal_git":
        raise Stage3ArtifactError("artifact provenance kind is invalid")
    if fixture_mode:
        raise Stage3ArtifactError("fixture training requires synthetic provenance")
    observed = capture_formal_provenance(
        repository_root,
        created_at=datetime.fromisoformat(provenance.created_at.replace("Z", "+00:00")),
    )
    if observed != provenance:
        raise Stage3ArtifactError("formal artifact provenance does not match checkout")


def _provenance_differences(
    recorded: Mapping[str, object], repository_root: Path
) -> tuple[str, ...]:
    _require_keys(
        recorded,
        {"kind", "git_sha", "uv_lock_checksum", "created_at"},
        "artifact provenance",
    )
    differences: list[str] = []
    try:
        current_git = _run_git(repository_root, "rev-parse", "HEAD")
        current_status = _run_git(repository_root, "status", "--porcelain")
    except Stage3ArtifactError:
        current_git = "unavailable"
        current_status = "unavailable"
    lock_path = repository_root / "uv.lock"
    current_lock = sha256_file(lock_path) if lock_path.is_file() else "unavailable"
    if recorded["git_sha"] != current_git:
        differences.append("git_sha")
    if recorded["uv_lock_checksum"] != current_lock:
        differences.append("uv_lock_checksum")
    if current_status:
        differences.append("git_worktree_dirty")
    return tuple(differences)


def _parameter_digest(parameters: Mapping[str, ParameterValue]) -> str:
    return _digest(
        {
            "schema": "tracequant-stage3-lightgbm-effective-parameters-v1",
            "parameters": dict(parameters),
        }
    )


def _artifact_id(manifest: Mapping[str, object]) -> str:
    payload = {
        "schema": "tracequant-stage3-lightgbm-artifact-identity-v1",
        "identity_fields": {key: manifest[key] for key in ARTIFACT_IDENTITY_FIELDS},
    }
    return _digest(payload)


def _manifest_digest(manifest: Mapping[str, object]) -> str:
    payload = {
        key: value for key, value in manifest.items() if key != "manifest_digest"
    }
    return _digest(payload)


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: object) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise Stage3ArtifactError("identity payload is not canonical JSON") from exc


def _read_json_object(path: Path, description: str) -> dict[str, object]:
    target = Path(path)
    if not target.is_file():
        raise Stage3ArtifactError(f"{description} is missing")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage3ArtifactError(f"{description} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise Stage3ArtifactError(f"{description} must be a JSON object")
    return cast(dict[str, object], payload)


def _require_external_file_target(
    path: Path, *, repository_root: Path, description: str
) -> Path:
    """Reject lexical ``latest``; allow other symlinks only to a safe resolved target."""
    target = Path(path)
    if not target.is_absolute():
        raise Stage3ArtifactError(f"{description} path must be absolute")
    if any(part.lower() == "latest" for part in target.parts):
        raise Stage3ArtifactError(f"{description} must not use a latest alias")
    resolved = target.resolve(strict=False)
    if any(part.lower() == "latest" for part in resolved.parts):
        raise Stage3ArtifactError(f"{description} must not use a latest alias")
    root = Path(repository_root).resolve()
    if resolved == root or root in resolved.parents:
        raise Stage3ArtifactError(f"{description} must be outside the repository")
    return resolved


def _require_formal_evidence_paths(
    config: Stage3Config,
    *,
    output_partition: Path,
    parameter_record_path: Path,
    repository_root: Path,
) -> tuple[Path, Path]:
    root = Path(repository_root).resolve()
    evidence_root = _require_external_file_target(
        config.evidence_root,
        repository_root=root,
        description="configured evidence root",
    )
    catalog_root = _require_external_file_target(
        config.catalog_path,
        repository_root=root,
        description="configured Nautilus catalog",
    )
    run_root = _require_external_file_target(
        config.run_root,
        repository_root=root,
        description="configured run root",
    )
    for description, configured_root in (
        ("configured Nautilus catalog", catalog_root),
        ("configured run root", run_root),
    ):
        if _paths_overlap(evidence_root, configured_root):
            raise Stage3ArtifactError(
                f"configured evidence root must not overlap the {description}"
            )

    partition = _require_external_file_target(
        output_partition,
        repository_root=root,
        description="artifact partition",
    )
    parameter_record = _require_external_file_target(
        parameter_record_path,
        repository_root=root,
        description="parameter record",
    )
    for target, description in (
        (partition, "artifact partition"),
        (parameter_record, "parameter record"),
    ):
        if evidence_root not in target.parents:
            raise Stage3ArtifactError(
                f"{description} must be inside the configured evidence root"
            )
        for configured_root, root_description in (
            (catalog_root, "configured Nautilus catalog"),
            (run_root, "configured run root"),
        ):
            if _paths_overlap(target, configured_root):
                raise Stage3ArtifactError(
                    f"{description} must not overlap the {root_description}"
                )
    return partition, parameter_record


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _require_external_partition(
    path: Path, *, repository_root: Path, require_empty: bool
) -> Path:
    partition = _require_external_file_target(
        path, repository_root=repository_root, description="artifact partition"
    )
    if partition.exists() and not partition.is_dir():
        raise Stage3ArtifactError("artifact partition must be a directory")
    if require_empty and partition.is_dir() and any(partition.iterdir()):
        raise Stage3ArtifactError("artifact partition must be empty")
    return partition


def _claim_artifact_partition(partition: Path) -> Path:
    partition.parent.mkdir(parents=True, exist_ok=True)
    try:
        partition.mkdir()
    except FileExistsError:
        if not partition.is_dir():
            raise Stage3ArtifactError("artifact partition must be a directory")
    claim_path = partition / ARTIFACT_CLAIM_FILENAME
    if claim_path.exists():
        raise Stage3ArtifactError("artifact partition is already claimed")
    if any(partition.iterdir()):
        raise Stage3ArtifactError("artifact partition must be empty")

    try:
        with claim_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("tracequant-stage3-lightgbm-artifact-claim-v1\n")
    except FileExistsError as exc:
        raise Stage3ArtifactError("artifact partition is already claimed") from exc
    if {item.name for item in partition.iterdir()} != {ARTIFACT_CLAIM_FILENAME}:
        claim_path.unlink()
        raise Stage3ArtifactError("artifact partition changed while being claimed")
    return claim_path


def _require_keys(
    payload: Mapping[str, object], expected: set[str], description: str
) -> None:
    if set(payload) != expected:
        raise Stage3ArtifactError(f"{description} fields do not match the schema")


def _required_mapping(
    payload: Mapping[str, object], key: str, description: str
) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise Stage3ArtifactError(f"{description} {key} is invalid")
    return cast(Mapping[str, object], value)


def _required_string(payload: Mapping[str, object], key: str, description: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise Stage3ArtifactError(f"{description} {key} is invalid")
    return value


def _load_lightgbm() -> Any:
    try:
        return importlib.import_module("lightgbm")
    except (ImportError, OSError) as exc:
        raise Stage3ArtifactError(
            "LightGBM 4.7.0 CPU runtime is unavailable; verify its native system libraries"
        ) from exc


def _run_git(repository_root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository_root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Stage3ArtifactError("Git provenance identity is unavailable") from exc
    return completed.stdout.strip()


def _utc_text(value: datetime) -> str:
    return require_utc(value).isoformat().replace("+00:00", "Z")


def _require_git_sha(value: object, description: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise Stage3ArtifactError(f"{description} is invalid")
    try:
        int(value, 16)
    except ValueError as exc:
        raise Stage3ArtifactError(f"{description} is invalid") from exc
    return value


def _require_revision(value: str) -> str:
    if (
        not value
        or value.strip() != value
        or any(character.isspace() for character in value)
    ):
        raise Stage3ArtifactError("training parameter revision is invalid")
    return value


def _require_sha256(value: object, description: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise Stage3ArtifactError(f"{description} is invalid")
    try:
        int(value, 16)
    except ValueError as exc:
        raise Stage3ArtifactError(f"{description} is invalid") from exc
    return value


def _require_utc_text(value: str, description: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Stage3ArtifactError(f"{description} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise Stage3ArtifactError(f"{description} must be UTC")
    return parsed
