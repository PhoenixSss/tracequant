from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest

from tests.acceptance import test_stage3_evaluation as evaluation_fixture
from tests.acceptance import test_stage3_features as feature_fixture
from tests.acceptance import test_stage3_momentum as momentum_fixture
from tracequant.integrations.nautilus import stage3_evaluation, stage3_oos
from tracequant.research.stage3_artifacts import synthetic_fixture_provenance
from tracequant.research.stage3_features import Stage3Config


def test_stage3_oos_rebuild_compares_both_strategies_from_one_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog, stage2_record, template, artifact_lock = (
        momentum_fixture._accepted_fixture(monkeypatch, tmp_path)
    )
    evaluation_fixture._install_real_fixture_matrix(monkeypatch)
    provenance = synthetic_fixture_provenance(created_at="2026-01-01T00:00:00Z")

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
    assert {artifact["provenance_kind"] for artifact in artifacts} == {
        "synthetic_fixture"
    }
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
    drifted["acceptance_digest"] = stage3_oos.stage3_acceptance_digest(drifted)
    with pytest.raises(stage3_oos.Stage3OosError, match="parameter identity"):
        stage3_oos._require_complete_stage3_acceptance_record(
            drifted,
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


def _absolute_strings(value: object) -> list[str]:
    if isinstance(value, dict):
        return [found for item in value.values() for found in _absolute_strings(item)]
    if isinstance(value, list):
        return [found for item in value for found in _absolute_strings(item)]
    if isinstance(value, str) and Path(value).is_absolute():
        return [value]
    return []


def test_stage3_oos_record_is_canonical_json_serializable() -> None:
    payload = {
        "schema": stage3_oos.STAGE3_ACCEPTANCE_SCHEMA,
        "template": stage3_oos.rebuild_command_template(),
    }
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload
