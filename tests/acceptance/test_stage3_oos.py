from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from tests.acceptance import test_stage3_evaluation as evaluation_fixture
from tests.acceptance import test_stage3_features as feature_fixture
from tests.acceptance import test_stage3_momentum as momentum_fixture
from tracequant.integrations.nautilus import stage3_evaluation, stage3_oos
from tracequant.research.stage3_artifacts import (
    PREDICTION_ATOL,
    PREDICTION_RTOL,
    synthetic_fixture_provenance,
)
from tracequant.research.stage3_features import (
    Stage3Config,
    Stage3DataError,
)
from tracequant.source_data.stage2_btceth import STAGE2_INSTRUMENT_SNAPSHOT_FILENAME


def test_stage3_oos_rebuild_compares_both_strategies_from_one_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog, stage2_record, template, artifact_lock = (
        momentum_fixture._accepted_fixture(monkeypatch, tmp_path)
    )
    evaluation_fixture._install_real_fixture_matrix(monkeypatch)
    provenance = synthetic_fixture_provenance(created_at="2026-01-01T00:00:00Z")
    catalog_digest = _directory_digest(catalog)

    def config(name: str) -> Stage3Config:
        return feature_fixture._accepted_config(
            template,
            catalog,
            tmp_path / name,
            artifact_lock_path=artifact_lock,
        )

    first = stage3_oos._rebuild_stage3_oos_fixture(
        config("first"),
        stage2_acceptance_record_path=stage2_record,
        stage3_acceptance_record_path=tmp_path / "first-stage3-acceptance.json",
        provenance=provenance,
    )
    second = stage3_oos._rebuild_stage3_oos_fixture(
        config("second"),
        stage2_acceptance_record_path=stage2_record,
        stage3_acceptance_record_path=tmp_path / "second-stage3-acceptance.json",
        provenance=provenance,
    )

    assert first.evaluation.result_digest == second.evaluation.result_digest
    assert first.acceptance_digest == second.acceptance_digest
    assert first.acceptance_record == second.acceptance_record
    assert _directory_digest(catalog) == catalog_digest
    assert first.acceptance_record_path.is_file()
    assert second.acceptance_record_path.is_file()
    assert first.acceptance_record["product_status"] == [
        "OFFLINE_BACKTEST_ONLY",
        "LIVE_NOT_APPROVED",
    ]
    runs = cast(list[dict[str, object]], first.acceptance_record["runs"])
    assert len(runs) == 16
    assert {run["strategy"] for run in runs} == {"momentum", "lightgbm"}
    assert {run["scenario"] for run in runs if run["run_type"] == "sensitivity"} == {
        "zero_fee",
        "double_fee",
        "zero_funding",
        "double_funding",
    }
    artifacts = cast(list[dict[str, object]], first.acceptance_record["artifacts"])
    assert len(artifacts) == 4
    assert {artifact["training_parameter_revision"] for artifact in artifacts} == {
        "stage3-lgbm-r1"
    }
    assert len({artifact["training_parameter_digest"] for artifact in artifacts}) == 1
    assert len({artifact["artifact_id"] for artifact in artifacts}) == len(artifacts)
    assert len({artifact["compatibility_digest"] for artifact in artifacts}) == 1
    assert len({artifact["environment_digest"] for artifact in artifacts}) == 1
    assert {artifact["prediction_tolerance_digest"] for artifact in artifacts} == {
        stage3_oos._digest(
            {
                "atol": PREDICTION_ATOL,
                "rtol": PREDICTION_RTOL,
            }
        )
    }
    assert all(
        artifact["artifact_binding_digest"]
        == stage3_oos._artifact_binding_digest(artifact)
        for artifact in artifacts
    )
    assert {artifact["provenance_kind"] for artifact in artifacts} == {
        "synthetic_fixture"
    }
    configs = cast(dict[str, object], first.acceptance_record["configs"])
    assert (
        configs["artifact_lock_digest"]
        == hashlib.sha256(artifact_lock.read_bytes()).hexdigest()
    )
    stage3_oos._require_complete_stage3_acceptance_record(
        first.acceptance_record,
        allow_synthetic_fixture=True,
        repository_root=Path(stage3_oos.__file__).resolve().parents[4],
    )
    with pytest.raises(stage3_oos.Stage3OosError):
        stage3_oos.require_complete_stage3_acceptance_record(first.acceptance_record)
    assert not _absolute_strings(first.acceptance_record)

    unknown = copy.deepcopy(first.acceptance_record)
    unknown["unexpected"] = True
    unknown["acceptance_digest"] = stage3_oos.stage3_acceptance_digest(unknown)
    with pytest.raises(stage3_oos.Stage3OosError, match="fields do not match"):
        stage3_oos._require_complete_stage3_acceptance_record(
            unknown,
            allow_synthetic_fixture=True,
            repository_root=Path(stage3_oos.__file__).resolve().parents[4],
        )

    drifted = copy.deepcopy(first.acceptance_record)
    drifted_artifacts = cast(list[dict[str, object]], drifted["artifacts"])
    drifted_artifacts[-1]["training_parameter_digest"] = "0" * 64
    drifted_artifacts[-1]["artifact_binding_digest"] = (
        stage3_oos._artifact_binding_digest(drifted_artifacts[-1])
    )
    drifted["acceptance_digest"] = stage3_oos.stage3_acceptance_digest(drifted)
    with pytest.raises(stage3_oos.Stage3OosError, match="parameter identity"):
        stage3_oos._require_complete_stage3_acceptance_record(
            drifted,
            allow_synthetic_fixture=True,
            repository_root=Path(stage3_oos.__file__).resolve().parents[4],
        )

    reused = copy.deepcopy(first.acceptance_record)
    reused_artifacts = cast(list[dict[str, object]], reused["artifacts"])
    reused_artifacts[1]["artifact_id"] = reused_artifacts[0]["artifact_id"]
    reused_artifacts[1]["artifact_binding_digest"] = (
        stage3_oos._artifact_binding_digest(reused_artifacts[1])
    )
    reused["acceptance_digest"] = stage3_oos.stage3_acceptance_digest(reused)
    with pytest.raises(stage3_oos.Stage3OosError, match="reused across folds"):
        stage3_oos._require_complete_stage3_acceptance_record(
            reused,
            allow_synthetic_fixture=True,
            repository_root=Path(stage3_oos.__file__).resolve().parents[4],
        )

    drifted_config = copy.deepcopy(first.acceptance_record)
    cast(dict[str, object], drifted_config["configs"])["shared_execution_digest"] = (
        "0" * 64
    )
    drifted_config["acceptance_digest"] = stage3_oos.stage3_acceptance_digest(
        drifted_config
    )
    with pytest.raises(stage3_oos.Stage3OosError, match="shared execution"):
        stage3_oos._require_complete_stage3_acceptance_record(
            drifted_config,
            allow_synthetic_fixture=True,
            repository_root=Path(stage3_oos.__file__).resolve().parents[4],
        )

    inconsistent_fee = copy.deepcopy(first.acceptance_record)
    fee = cast(dict[str, object], inconsistent_fee["fee_provenance"])
    fee_records = cast(list[dict[str, object]], fee["records"])
    fee_records[0]["snapshot_maker_matches_base"] = not cast(
        bool, fee_records[0]["snapshot_maker_matches_base"]
    )
    fee["digest"] = stage3_oos._digest(fee_records)
    inconsistent_fee["acceptance_digest"] = stage3_oos.stage3_acceptance_digest(
        inconsistent_fee
    )
    with pytest.raises(stage3_oos.Stage3OosError, match="comparison is inconsistent"):
        stage3_oos._require_complete_stage3_acceptance_record(
            inconsistent_fee,
            allow_synthetic_fixture=True,
            repository_root=Path(stage3_oos.__file__).resolve().parents[4],
        )

    wrong_scenario_fee = copy.deepcopy(first.acceptance_record)
    wrong_fee_runs = cast(list[dict[str, object]], wrong_scenario_fee["runs"])
    sensitivity_run = next(
        run for run in wrong_fee_runs if run["run_type"] == "sensitivity"
    )
    sensitivity_run["fee_provenance_digest"] = "0" * 64
    wrong_scenario_fee["acceptance_digest"] = stage3_oos.stage3_acceptance_digest(
        wrong_scenario_fee
    )
    with pytest.raises(stage3_oos.Stage3OosError, match="does not match its scenario"):
        stage3_oos._require_complete_stage3_acceptance_record(
            wrong_scenario_fee,
            allow_synthetic_fixture=True,
            repository_root=Path(stage3_oos.__file__).resolve().parents[4],
        )


def test_stage3_oos_rejects_a_nonempty_acceptance_target_before_evaluation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog, stage2_record, template, artifact_lock = (
        momentum_fixture._accepted_fixture(monkeypatch, tmp_path)
    )
    config = feature_fixture._accepted_config(
        template,
        catalog,
        tmp_path / "unused",
        artifact_lock_path=artifact_lock,
    )
    target = tmp_path / "existing.json"
    target.write_text("{}\n", encoding="utf-8")

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("evaluation must not start for a nonempty target")

    monkeypatch.setattr(stage3_evaluation, "run_stage3_evaluation", unexpected)
    with pytest.raises(stage3_oos.Stage3OosError, match="must not already exist"):
        stage3_oos._rebuild_stage3_oos_fixture(
            config,
            stage2_acceptance_record_path=stage2_record,
            stage3_acceptance_record_path=target,
            provenance=synthetic_fixture_provenance(created_at="2026-01-01T00:00:00Z"),
        )


def test_stage3_oos_rechecks_the_read_only_catalog_after_evaluation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog, stage2_record, template, artifact_lock = (
        momentum_fixture._accepted_fixture(monkeypatch, tmp_path)
    )
    config = feature_fixture._accepted_config(
        template,
        catalog,
        tmp_path / "unused",
        artifact_lock_path=artifact_lock,
    )

    def mutate_catalog(*_args: object, **_kwargs: object) -> None:
        snapshot = catalog / STAGE2_INSTRUMENT_SNAPSHOT_FILENAME
        snapshot.write_bytes(snapshot.read_bytes() + b"\n")

    monkeypatch.setattr(stage3_evaluation, "run_stage3_evaluation", mutate_catalog)
    with pytest.raises(Stage3DataError, match="locked Stage 2 artifact"):
        stage3_oos._rebuild_stage3_oos_fixture(
            config,
            stage2_acceptance_record_path=stage2_record,
            stage3_acceptance_record_path=tmp_path / "stage3-acceptance.json",
            provenance=synthetic_fixture_provenance(created_at="2026-01-01T00:00:00Z"),
        )


def _absolute_strings(value: object) -> list[str]:
    if isinstance(value, dict):
        return [found for item in value.values() for found in _absolute_strings(item)]
    if isinstance(value, list):
        return [found for item in value for found in _absolute_strings(item)]
    if isinstance(value, str) and Path(value).is_absolute():
        return [value]
    return []


def _directory_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_stage3_oos_record_is_canonical_json_serializable() -> None:
    payload = {
        "schema": stage3_oos.STAGE3_ACCEPTANCE_SCHEMA,
        "template": stage3_oos.rebuild_command_template(),
    }
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload
