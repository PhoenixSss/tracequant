from __future__ import annotations

import hashlib
import json
import math
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest

from tracequant.integrations.nautilus import stage2_artifact
from tracequant.integrations.nautilus.stage2_artifact import sha256_file
from tracequant.research import stage3_artifacts as artifacts
from tracequant.research.stage3_artifacts import (
    ARTIFACT_IDENTITY_FIELDS,
    MANIFEST_FILENAME,
    MODEL_FILENAME,
    ArtifactWindow,
    Stage2ArtifactIdentity,
    Stage3ArtifactError,
    TrainingProvenance,
    default_effective_parameters,
    freeze_training_parameters,
    load_lightgbm_artifact,
    load_training_parameters,
    locked_stage2_identity,
    synthetic_fixture_provenance,
    train_fixture_lightgbm_artifact,
    train_lightgbm_artifact,
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
TRAIN_START = datetime(2020, 1, 1, tzinfo=UTC)
TRAIN_END = datetime(2020, 2, 1, tzinfo=UTC)
EVALUATION_END = datetime(2020, 2, 8, tzinfo=UTC)


def _training_frame() -> pl.DataFrame:
    records: list[dict[str, object]] = []
    first = TRAIN_START + timedelta(hours=169) - timedelta(milliseconds=1)
    for hour in range(96):
        decision_ts = datetime_to_nanos(first + timedelta(hours=hour))
        for instrument_index, instrument_id in enumerate(STAGE2_INSTRUMENT_IDS):
            features = {
                name: float(
                    (hour + 1) * (feature_index + 1) / 10_000 + instrument_index * 0.001
                )
                for feature_index, name in enumerate(FEATURE_NAMES)
            }
            features["instrument_code"] = float(instrument_index)
            records.append(
                {
                    "instrument_id": instrument_id,
                    "decision_ts": decision_ts,
                    **features,
                    "feature_schema_digest": FEATURE_SCHEMA_DIGEST,
                    "label_available": True,
                    "label_log_return_4h": float(
                        math.sin(hour / 12) / 100 + instrument_index * 0.0001
                    ),
                    "label_end_ts": decision_ts + 4 * HOUR_NS,
                    "tradable": False,
                }
            )
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
    return pl.DataFrame(records, schema=schema).sort(["decision_ts", "instrument_id"])


def _window() -> ArtifactWindow:
    return ArtifactWindow(
        train_start=TRAIN_START,
        train_end=TRAIN_END,
        evaluation_start=TRAIN_END,
        evaluation_end=EVALUATION_END,
        role="development",
    )


def _freeze(tmp_path: Path) -> Path:
    path = tmp_path / "parameter-record" / "parameters.json"
    freeze_training_parameters(
        path,
        revision="stage3-lgbm-r1",
        parameters=default_effective_parameters(),
        repository_root=REPOSITORY_ROOT,
    )
    return path


def _train(
    tmp_path: Path, name: str = "artifact"
) -> tuple[Path, Path, dict[str, object]]:
    parameter_path = _freeze(tmp_path)
    partition = tmp_path / name
    manifest = train_fixture_lightgbm_artifact(
        _training_frame(),
        output_partition=partition,
        parameter_record_path=parameter_path,
        stage2_identity=locked_stage2_identity(),
        window=_window(),
        provenance=synthetic_fixture_provenance(created_at="2026-09-20T00:00:00Z"),
        repository_root=REPOSITORY_ROOT,
    )
    return partition, parameter_path, manifest


def _train_fixture(
    frame: pl.DataFrame,
    *,
    output_partition: Path,
    parameter_record_path: Path,
    window: ArtifactWindow | None = None,
) -> dict[str, object]:
    return train_fixture_lightgbm_artifact(
        frame,
        output_partition=output_partition,
        parameter_record_path=parameter_record_path,
        stage2_identity=locked_stage2_identity(),
        window=window if window is not None else _window(),
        provenance=synthetic_fixture_provenance(created_at="2026-09-20T00:00:00Z"),
        repository_root=REPOSITORY_ROOT,
    )


def _load_fixture(
    artifact_partition: Path,
    *,
    parameter_record_path: Path,
    expected_stage2_identity: Stage2ArtifactIdentity | None = None,
) -> artifacts.LightGBMArtifactPredictor:
    return artifacts._load_lightgbm_artifact(
        artifact_partition,
        parameter_record_path=parameter_record_path,
        expected_stage2_identity=(
            locked_stage2_identity()
            if expected_stage2_identity is None
            else expected_stage2_identity
        ),
        repository_root=REPOSITORY_ROOT,
        expected_provenance_kind="synthetic_fixture",
    )


def _rewrite_manifest(partition: Path, manifest: dict[str, object]) -> None:
    manifest["artifact_id"] = artifacts._artifact_id(manifest)
    manifest["manifest_digest"] = artifacts._manifest_digest(manifest)
    (partition / MANIFEST_FILENAME).write_text(
        artifacts._canonical_json(manifest) + "\n", encoding="utf-8"
    )


def test_stage3_lightgbm_artifact_round_trips_with_locked_identity(
    tmp_path: Path,
) -> None:
    parameter_path = _freeze(tmp_path)
    frame = _training_frame()
    provenance = synthetic_fixture_provenance(created_at="2026-09-20T00:00:00Z")
    manifests: list[dict[str, object]] = []
    predictors = []
    for name in ("artifact-one", "artifact-two"):
        partition = tmp_path / name
        manifests.append(
            train_fixture_lightgbm_artifact(
                frame,
                output_partition=partition,
                parameter_record_path=parameter_path,
                stage2_identity=locked_stage2_identity(),
                window=_window(),
                provenance=provenance,
                repository_root=REPOSITORY_ROOT,
            )
        )
        predictors.append(
            _load_fixture(
                partition,
                parameter_record_path=parameter_path,
            )
        )

    prediction_input = frame.select(FEATURE_NAMES).head(12)
    first_predictions = predictors[0].predict(
        prediction_input, feature_schema_digest=FEATURE_SCHEMA_DIGEST
    )
    second_predictions = predictors[1].predict(
        prediction_input, feature_schema_digest=FEATURE_SCHEMA_DIGEST
    )
    assert first_predictions == second_predictions
    assert all(math.isfinite(value) for value in first_predictions)
    assert manifests[0]["artifact_id"] == manifests[1]["artifact_id"]
    assert manifests[0]["manifest_digest"] == manifests[1]["manifest_digest"]
    assert (
        cast(dict[str, object], manifests[0]["model"])["checksum_sha256"]
        == cast(dict[str, object], manifests[1]["model"])["checksum_sha256"]
    )
    provenance_differences = set(predictors[0].provenance_differences)
    assert {"git_sha", "uv_lock_checksum"} <= provenance_differences
    assert provenance_differences <= {
        "git_sha",
        "uv_lock_checksum",
        "git_worktree_dirty",
    }
    assert tuple(cast(list[str], manifests[0]["artifact_identity_fields"])) == (
        ARTIFACT_IDENTITY_FIELDS
    )


def test_production_loader_rejects_synthetic_fixture_artifacts(
    tmp_path: Path,
) -> None:
    partition, parameter_path, _ = _train(tmp_path)

    with pytest.raises(Stage3ArtifactError, match="requires formal_git provenance"):
        load_lightgbm_artifact(
            partition,
            parameter_record_path=parameter_path,
            expected_stage2_identity=locked_stage2_identity(),
            repository_root=REPOSITORY_ROOT,
        )


def test_code_inventory_digest_tracks_the_runtime_gate_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = artifacts._code_inventory(REPOSITORY_ROOT)
    expected_modules = (
        "tracequant.integrations.nautilus",
        "tracequant.integrations.nautilus.stage2_artifact",
        "tracequant.integrations.nautilus.stage2_btceth",
        "tracequant.research.stage3_artifacts",
        "tracequant.research.source_schema",
        "tracequant.research.stage3_features",
        "tracequant.research.views",
        "tracequant.source_data.stage2_btceth",
    )
    assert tuple(item["module"] for item in original) == expected_modules

    assert stage2_artifact.__file__ is not None
    target_path = Path(stage2_artifact.__file__).resolve()
    original_checksums = {
        cast(str, item["module"]): cast(str, item["checksum_sha256"])
        for item in original
    }
    target_module = "tracequant.integrations.nautilus.stage2_artifact"
    replacement = (
        "f" * 64 if original_checksums[target_module] != "f" * 64 else "0" * 64
    )
    original_sha256_file = sha256_file

    def drifted_sha256_file(path: Path) -> str:
        if Path(path).resolve() == target_path:
            return replacement
        return original_sha256_file(path)

    monkeypatch.setattr(artifacts, "sha256_file", drifted_sha256_file)
    drifted = artifacts._code_inventory(REPOSITORY_ROOT)
    drifted_checksums = {
        cast(str, item["module"]): cast(str, item["checksum_sha256"])
        for item in drifted
    }

    assert {
        module
        for module in expected_modules
        if original_checksums[module] != drifted_checksums[module]
    } == {target_module}
    assert artifacts._code_digest(original) != artifacts._code_digest(drifted)


def test_lightgbm_distribution_identity_covers_executed_python_and_native_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lightgbm = artifacts._load_lightgbm()
    runtime = artifacts._runtime_identity(lightgbm, repository_root=REPOSITORY_ROOT)
    environment = cast(dict[str, object], runtime["environment"])
    compatibility = cast(dict[str, object], runtime["compatibility"])
    distribution = cast(dict[str, object], environment["lightgbm_distribution"])
    files = cast(list[dict[str, object]], distribution["files"])
    paths = {cast(str, item["relative_path"]) for item in files}

    assert {"lightgbm/basic.py", "lightgbm/engine.py"} <= paths
    assert (
        compatibility["lightgbm_distribution_digest"]
        == distribution["file_inventory_digest"]
    )

    basic_path = Path(lightgbm.basic.__file__).resolve()
    original_sha256_file = sha256_file

    def drifted_sha256_file(path: Path) -> str:
        if Path(path).resolve() == basic_path:
            return "f" * 64
        return original_sha256_file(path)

    monkeypatch.setattr(artifacts, "sha256_file", drifted_sha256_file)
    with pytest.raises(Stage3ArtifactError, match="does not match RECORD"):
        artifacts._runtime_identity(lightgbm, repository_root=REPOSITORY_ROOT)


def test_formal_trainer_reuses_the_accepted_window_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = _training_frame()
    identity = locked_stage2_identity()
    config = Stage3Config(
        schema="tracequant-stage3-features-v1",
        dataset_id=identity.dataset_id,
        acceptance_digest=identity.acceptance_digest,
        dataset_digest=identity.dataset_digest,
        source_manifest_digest=identity.source_manifest_digest,
        market_data_manifest_digest=identity.market_data_manifest_digest,
        instrument_snapshot_checksum=identity.instrument_snapshot_checksum,
        runtime_identity=identity.runtime_identity,
        artifact_lock_path=tmp_path / "artifact-lock.json",
        catalog_path=tmp_path / "catalog",
        evidence_root=tmp_path / "evidence",
        run_root=tmp_path / "run",
    )
    calls: dict[str, object] = {}

    def accepted_loader(*args: object, **kwargs: object) -> Any:
        calls["accepted_loader"] = (args, kwargs)
        return {
            instrument_id: (instrument_id,) for instrument_id in STAGE2_INSTRUMENT_IDS
        }

    def fixture_frame(rows: object) -> pl.DataFrame:
        instrument_id = cast(tuple[str], rows)[0]
        return frame.filter(pl.col("instrument_id") == instrument_id)

    def captured_training(
        input_frame: pl.DataFrame, **kwargs: object
    ) -> dict[str, object]:
        calls["training_frame"] = input_frame
        calls["training_kwargs"] = kwargs
        return {"status": "captured"}

    monkeypatch.setattr(artifacts, "load_accepted_feature_window", accepted_loader)
    monkeypatch.setattr(artifacts, "feature_frame", fixture_frame)
    monkeypatch.setattr(artifacts, "_train_lightgbm_artifact", captured_training)
    result = train_lightgbm_artifact(
        config,
        acceptance_record_path=tmp_path / "acceptance.json",
        output_partition=config.evidence_root / "artifact",
        parameter_record_path=config.evidence_root / "parameters.json",
        window=_window(),
        provenance=TrainingProvenance(
            kind="formal_git",
            git_sha="0" * 40,
            uv_lock_checksum="0" * 64,
            created_at="2026-09-20T00:00:00Z",
        ),
        repository_root=REPOSITORY_ROOT,
    )
    assert result == {"status": "captured"}
    assert calls["accepted_loader"]
    captured_frame = cast(pl.DataFrame, calls["training_frame"])
    assert captured_frame.equals(frame)
    captured_kwargs = cast(dict[str, object], calls["training_kwargs"])
    assert captured_kwargs["stage2_identity"] == identity
    assert captured_kwargs["fixture_mode"] is False
    with pytest.raises(Stage3ArtifactError, match="rejects synthetic"):
        artifacts._validate_provenance(
            synthetic_fixture_provenance(created_at="2026-09-20T00:00:00Z"),
            repository_root=REPOSITORY_ROOT,
            fixture_mode=False,
        )


def test_formal_trainer_confines_outputs_to_the_configured_evidence_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = locked_stage2_identity()
    config = Stage3Config(
        schema="tracequant-stage3-features-v1",
        dataset_id=identity.dataset_id,
        acceptance_digest=identity.acceptance_digest,
        dataset_digest=identity.dataset_digest,
        source_manifest_digest=identity.source_manifest_digest,
        market_data_manifest_digest=identity.market_data_manifest_digest,
        instrument_snapshot_checksum=identity.instrument_snapshot_checksum,
        runtime_identity=identity.runtime_identity,
        artifact_lock_path=tmp_path / "artifact-lock.json",
        catalog_path=tmp_path / "catalog",
        evidence_root=tmp_path / "evidence",
        run_root=tmp_path / "run",
    )

    def unexpected_loader(*args: object, **kwargs: object) -> object:
        raise AssertionError("the accepted catalog must not be read for unsafe outputs")

    monkeypatch.setattr(artifacts, "load_accepted_feature_window", unexpected_loader)

    def train(output_partition: Path, parameter_record_path: Path) -> None:
        train_lightgbm_artifact(
            config,
            acceptance_record_path=tmp_path / "acceptance.json",
            output_partition=output_partition,
            parameter_record_path=parameter_record_path,
            window=_window(),
            provenance=TrainingProvenance(
                kind="formal_git",
                git_sha="0" * 40,
                uv_lock_checksum="0" * 64,
                created_at="2026-09-20T00:00:00Z",
            ),
            repository_root=REPOSITORY_ROOT,
        )

    with pytest.raises(Stage3ArtifactError, match="inside the configured evidence"):
        train(
            config.catalog_path / "model-fold-1",
            config.evidence_root / "parameters.json",
        )
    with pytest.raises(Stage3ArtifactError, match="inside the configured evidence"):
        train(
            config.evidence_root / "model-fold-1",
            config.run_root / "parameters.json",
        )


def test_artifact_partition_claim_is_exclusive(tmp_path: Path) -> None:
    partition = tmp_path / "contended-artifact"
    claim_path = artifacts._claim_artifact_partition(partition)
    assert claim_path.is_file()
    with pytest.raises(Stage3ArtifactError, match="already claimed"):
        artifacts._claim_artifact_partition(partition)


def test_existing_latest_symlink_is_rejected_before_resolution(tmp_path: Path) -> None:
    concrete = tmp_path / "concrete"
    concrete.mkdir()
    latest = tmp_path / "latest"
    latest.symlink_to(concrete, target_is_directory=True)

    with pytest.raises(Stage3ArtifactError, match="latest alias"):
        freeze_training_parameters(
            latest / "parameters.json",
            revision="stage3-lgbm-r1",
            parameters=default_effective_parameters(),
            repository_root=REPOSITORY_ROOT,
        )

    parameter_path = _freeze(tmp_path / "valid")
    with pytest.raises(Stage3ArtifactError, match="latest alias"):
        _train_fixture(
            _training_frame(),
            output_partition=latest / "artifact",
            parameter_record_path=parameter_path,
        )


def test_parameter_record_is_closed_frozen_and_alias_free(tmp_path: Path) -> None:
    target = tmp_path / "parameters.json"
    parameters = default_effective_parameters()
    parameters["num_iterations"] = parameters.pop("num_boost_round")
    with pytest.raises(Stage3ArtifactError, match="alias"):
        freeze_training_parameters(
            target,
            revision="r1",
            parameters=parameters,
            repository_root=REPOSITORY_ROOT,
        )
    parameter_path = _freeze(tmp_path)
    frozen = load_training_parameters(parameter_path)
    assert frozen.parameter_map() == default_effective_parameters()
    with pytest.raises(Stage3ArtifactError, match="immutable"):
        freeze_training_parameters(
            parameter_path,
            revision="r2",
            parameters=default_effective_parameters(),
            repository_root=REPOSITORY_ROOT,
        )


def test_lightgbm_4_7_cpu_gbdt_effective_defaults_are_expanded() -> None:
    parameters = default_effective_parameters()
    assert {
        key: parameters[key]
        for key in (
            "histogram_pool_size",
            "max_bin_by_feature",
            "monotone_constraints",
            "feature_contri",
            "forcedsplits_filename",
            "cegb_tradeoff",
            "cegb_penalty_split",
            "cegb_penalty_feature_lazy",
            "cegb_penalty_feature_coupled",
            "interaction_constraints",
            "forcedbins_filename",
            "early_stopping_round",
            "use_quantized_grad",
        )
    } == {
        "histogram_pool_size": -1.0,
        "max_bin_by_feature": (255,) * len(FEATURE_NAMES),
        "monotone_constraints": (0,) * len(FEATURE_NAMES),
        "feature_contri": (1.0,) * len(FEATURE_NAMES),
        "forcedsplits_filename": "",
        "cegb_tradeoff": 1.0,
        "cegb_penalty_split": 0.0,
        "cegb_penalty_feature_lazy": (0.0,) * len(FEATURE_NAMES),
        "cegb_penalty_feature_coupled": (0.0,) * len(FEATURE_NAMES),
        "interaction_constraints": "",
        "forcedbins_filename": "",
        "early_stopping_round": 0,
        "use_quantized_grad": False,
    }


@pytest.mark.parametrize(
    "key",
    [
        "is_enable_sparse",
        "enable_bundle",
        "max_bin_by_feature",
        "use_quantized_grad",
    ],
)
def test_dataset_construction_defaults_are_explicit_and_required(
    tmp_path: Path, key: str
) -> None:
    parameters = default_effective_parameters()
    parameters.pop(key)

    with pytest.raises(Stage3ArtifactError, match="missing, unknown, or alias"):
        freeze_training_parameters(
            tmp_path / f"{key}.json",
            revision="stage3-lgbm-r1",
            parameters=parameters,
            repository_root=REPOSITORY_ROOT,
        )


def test_training_rejects_frame_identity_purge_and_partition_drift(
    tmp_path: Path,
) -> None:
    parameter_path = _freeze(tmp_path)
    frame = _training_frame()
    with pytest.raises(Stage3ArtifactError, match="row order"):
        _train_fixture(
            frame.reverse(),
            output_partition=tmp_path / "reordered",
            parameter_record_path=parameter_path,
        )
    with pytest.raises(Stage3ArtifactError, match="NaN"):
        _train_fixture(
            frame.with_columns(pl.lit(float("nan")).alias(FEATURE_NAMES[0])),
            output_partition=tmp_path / "nan",
            parameter_record_path=parameter_path,
        )
    purge_boundary = TRAIN_START + timedelta(hours=172)
    purge_window = ArtifactWindow(
        train_start=TRAIN_START,
        train_end=purge_boundary,
        evaluation_start=purge_boundary,
        evaluation_end=purge_boundary + timedelta(days=1),
        role="development",
    )
    with pytest.raises(Stage3ArtifactError, match="purge"):
        _train_fixture(
            frame.head(2),
            output_partition=tmp_path / "purge",
            parameter_record_path=parameter_path,
            window=purge_window,
        )
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "existing").write_text("immutable", encoding="utf-8")
    with pytest.raises(Stage3ArtifactError, match="empty"):
        _train_fixture(
            frame,
            output_partition=nonempty,
            parameter_record_path=parameter_path,
        )


def test_loader_rejects_manifest_model_parameter_and_runtime_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partition, parameter_path, _ = _train(tmp_path)
    manifest_path = partition / MANIFEST_FILENAME
    original_manifest = cast(
        dict[str, object], json.loads(manifest_path.read_text(encoding="utf-8"))
    )

    tampered = dict(original_manifest)
    tampered["artifact_id"] = "0" * 64
    manifest_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(Stage3ArtifactError, match="manifest digest"):
        _load_fixture(
            partition,
            parameter_record_path=parameter_path,
        )

    manifest_path.write_text(
        artifacts._canonical_json(original_manifest) + "\n", encoding="utf-8"
    )
    model_path = partition / MODEL_FILENAME
    model_path.write_text(model_path.read_text(encoding="utf-8") + "tamper\n")
    with pytest.raises(Stage3ArtifactError, match="model checksum"):
        _load_fixture(
            partition,
            parameter_record_path=parameter_path,
        )

    parameter_partition, _, _ = _train(tmp_path / "parameter-drift", name="artifact")
    replacement_parameters = tmp_path / "replacement-parameters" / "parameters.json"
    freeze_training_parameters(
        replacement_parameters,
        revision="stage3-lgbm-r2",
        parameters=default_effective_parameters(),
        repository_root=REPOSITORY_ROOT,
    )
    with pytest.raises(Stage3ArtifactError, match="frozen record"):
        _load_fixture(
            parameter_partition,
            parameter_record_path=replacement_parameters,
        )

    feature_partition, feature_parameters, _ = _train(
        tmp_path / "feature-drift", name="artifact"
    )
    feature_model_path = feature_partition / MODEL_FILENAME
    model_text = feature_model_path.read_text(encoding="utf-8")
    assert "feature_names=ret_1h" in model_text
    feature_model_path.write_text(
        model_text.replace("feature_names=ret_1h", "feature_names=wrong_ret_1h", 1),
        encoding="utf-8",
    )
    feature_manifest = cast(
        dict[str, object],
        json.loads((feature_partition / MANIFEST_FILENAME).read_text(encoding="utf-8")),
    )
    feature_model = cast(dict[str, object], feature_manifest["model"])
    feature_model["checksum_sha256"] = sha256_file(feature_model_path)
    _rewrite_manifest(feature_partition, feature_manifest)
    with pytest.raises(Stage3ArtifactError, match="feature names"):
        _load_fixture(
            feature_partition,
            parameter_record_path=feature_parameters,
        )

    partition, parameter_path, _ = _train(tmp_path / "runtime", name="artifact")
    original_runtime = artifacts._runtime_identity

    def drifted_runtime(
        lightgbm: object, *, repository_root: Path
    ) -> dict[str, object]:
        runtime = original_runtime(lightgbm, repository_root=repository_root)
        compatibility = dict(cast(dict[str, object], runtime["compatibility"]))
        compatibility["code_digest"] = "f" * 64
        runtime["compatibility"] = compatibility
        return runtime

    monkeypatch.setattr(artifacts, "_runtime_identity", drifted_runtime)
    with pytest.raises(Stage3ArtifactError, match="code or dependency"):
        _load_fixture(
            partition,
            parameter_record_path=parameter_path,
        )


@pytest.mark.parametrize("member_name", [MANIFEST_FILENAME, MODEL_FILENAME])
@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_loader_rejects_linked_artifact_members(
    tmp_path: Path,
    member_name: str,
    link_kind: str,
) -> None:
    partition, parameter_path, _ = _train(tmp_path / f"{member_name}-{link_kind}")
    member_path = partition / member_name
    link_target = tmp_path / f"{member_name}-{link_kind}-target"
    link_target.write_bytes(member_path.read_bytes())
    member_path.unlink()
    if link_kind == "symlink":
        member_path.symlink_to(link_target)
    else:
        member_path.hardlink_to(link_target)

    with pytest.raises(Stage3ArtifactError, match="unlinked regular file"):
        _load_fixture(partition, parameter_record_path=parameter_path)


def test_loader_parses_the_same_model_bytes_it_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partition, parameter_path, manifest = _train(tmp_path)
    model_path = partition / MODEL_FILENAME
    lightgbm = artifacts._load_lightgbm()
    original_booster = lightgbm.Booster
    captured: dict[str, str] = {}

    def replace_path_before_parse(*args: object, **kwargs: object) -> object:
        assert not args
        assert "model_file" not in kwargs
        model_text = cast(str, kwargs["model_str"])
        captured["checksum"] = hashlib.sha256(model_text.encode("utf-8")).hexdigest()
        model_path.write_text("replacement after immutable read\n", encoding="utf-8")
        return original_booster(model_str=model_text)

    monkeypatch.setattr(lightgbm, "Booster", replace_path_before_parse)
    predictor = _load_fixture(partition, parameter_record_path=parameter_path)

    expected_checksum = cast(dict[str, object], manifest["model"])["checksum_sha256"]
    assert captured["checksum"] == expected_checksum
    assert predictor._booster.num_feature() == len(FEATURE_NAMES)
    assert (
        model_path.read_text(encoding="utf-8") == "replacement after immutable read\n"
    )


def test_loader_reports_provenance_but_rejects_stage2_drift(tmp_path: Path) -> None:
    partition, parameter_path, _ = _train(tmp_path)
    predictor = _load_fixture(
        partition,
        parameter_record_path=parameter_path,
    )
    assert predictor.provenance_differences
    identity = locked_stage2_identity()
    wrong_identity = Stage2ArtifactIdentity(
        dataset_id=identity.dataset_id + "-wrong",
        acceptance_digest=identity.acceptance_digest,
        dataset_digest=identity.dataset_digest,
        source_manifest_digest=identity.source_manifest_digest,
        market_data_manifest_digest=identity.market_data_manifest_digest,
        instrument_snapshot_checksum=identity.instrument_snapshot_checksum,
        runtime_identity=identity.runtime_identity,
    )
    with pytest.raises(Stage3ArtifactError, match="Stage 2"):
        _load_fixture(
            partition,
            parameter_record_path=parameter_path,
            expected_stage2_identity=wrong_identity,
        )


def test_prediction_rejects_schema_dtype_and_nonfinite_inputs(tmp_path: Path) -> None:
    partition, parameter_path, _ = _train(tmp_path)
    predictor = _load_fixture(
        partition,
        parameter_record_path=parameter_path,
    )
    frame = _training_frame().select(FEATURE_NAMES).head(2)
    with pytest.raises(Stage3ArtifactError, match="digest"):
        predictor.predict(frame, feature_schema_digest="wrong")
    with pytest.raises(Stage3ArtifactError, match="names or order"):
        predictor.predict(
            frame.select(tuple(reversed(FEATURE_NAMES))),
            feature_schema_digest=FEATURE_SCHEMA_DIGEST,
        )
    with pytest.raises(Stage3ArtifactError, match="dtype"):
        predictor.predict(
            frame.with_columns(pl.col(FEATURE_NAMES[0]).cast(pl.Float32)),
            feature_schema_digest=FEATURE_SCHEMA_DIGEST,
        )
    with pytest.raises(Stage3ArtifactError, match="NaN"):
        predictor.predict(
            frame.with_columns(pl.lit(float("inf")).alias(FEATURE_NAMES[0])),
            feature_schema_digest=FEATURE_SCHEMA_DIGEST,
        )

    class NonFiniteBooster:
        def feature_name(self) -> list[str]:
            return list(FEATURE_NAMES)

        def num_feature(self) -> int:
            return len(FEATURE_NAMES)

        def current_iteration(self) -> int:
            return 1

        def predict(self, *args: object, **kwargs: object) -> object:
            return pl.Series([float("nan"), 0.0], dtype=pl.Float64).to_numpy()

    nonfinite_predictor = artifacts.LightGBMArtifactPredictor(
        _booster=NonFiniteBooster(),
        artifact_id="fixture",
        provenance_differences=(),
    )
    with pytest.raises(Stage3ArtifactError, match="output is not finite"):
        nonfinite_predictor.predict(frame, feature_schema_digest=FEATURE_SCHEMA_DIGEST)


def test_manifest_identity_excludes_provenance_but_manifest_digest_tracks_it(
    tmp_path: Path,
) -> None:
    partition, _, _ = _train(tmp_path)
    manifest = cast(
        dict[str, object],
        json.loads((partition / MANIFEST_FILENAME).read_text(encoding="utf-8")),
    )
    artifact_id = manifest["artifact_id"]
    manifest_digest = manifest["manifest_digest"]
    provenance = dict(cast(dict[str, object], manifest["provenance"]))
    provenance["git_sha"] = "fixture-" + "1" * 56
    provenance["created_at"] = "2026-09-21T00:00:00Z"
    manifest["provenance"] = provenance
    assert artifacts._artifact_id(manifest) == artifact_id
    assert artifacts._manifest_digest(manifest) != manifest_digest


def test_lightgbm_lock_has_only_the_approved_base_runtime_closure() -> None:
    lock = cast(
        dict[str, object],
        tomllib.loads((REPOSITORY_ROOT / "uv.lock").read_text(encoding="utf-8")),
    )
    packages = cast(list[dict[str, object]], lock["package"])
    by_name = {cast(str, package["name"]): package for package in packages}
    assert cast(str, by_name["lightgbm"]["version"]) == "4.7.0"
    dependencies = {
        cast(str, dependency["name"])
        for dependency in cast(
            list[dict[str, object]], by_name["lightgbm"]["dependencies"]
        )
    }
    assert dependencies == {"narwhals", "numpy", "scipy"}
    assert not {
        "xgboost",
        "scikit-learn",
        "pandas",
        "pyarrow",
        "optuna",
        "mlflow",
        "matplotlib",
    } & set(by_name)
