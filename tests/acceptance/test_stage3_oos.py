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
from tracequant.integrations.nautilus import (
    UPSTREAM_RELEASE_IDENTITY,
    stage3_evaluation,
    stage3_oos,
)
from tracequant.research import stage3_artifacts
from tracequant.research.stage3_artifacts import (
    PREDICTION_ATOL,
    PREDICTION_RTOL,
    synthetic_fixture_provenance,
)
from tracequant.research.stage3_features import (
    STAGE2_ACCEPTANCE_DIGEST,
    STAGE2_ARTIFACT_LOCK_RELATIVE_PATH,
    STAGE2_DATASET_DIGEST,
    STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM,
    STAGE2_MARKET_DATA_MANIFEST_DIGEST,
    STAGE2_SOURCE_MANIFEST_DIGEST,
    STAGE3_CONFIG_SCHEMA,
    Stage3Config,
    Stage3DataError,
)
from tracequant.source_data.stage2_btceth import (
    STAGE2_DATASET_ID,
    STAGE2_INSTRUMENT_SNAPSHOT_FILENAME,
)


def test_stage3_oos_rebuild_compares_both_strategies_from_one_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog, stage2_record, template, artifact_lock = (
        momentum_fixture._accepted_fixture(monkeypatch, tmp_path)
    )
    evaluation_fixture._install_real_fixture_matrix(monkeypatch)
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
        provenance=synthetic_fixture_provenance(created_at="2026-01-01T00:00:00Z"),
    )
    second = stage3_oos._rebuild_stage3_oos_fixture(
        config("second"),
        stage2_acceptance_record_path=stage2_record,
        stage3_acceptance_record_path=tmp_path / "second-stage3-acceptance.json",
        provenance=synthetic_fixture_provenance(created_at="2026-01-02T00:00:00Z"),
    )

    assert first.evaluation.result_digest == second.evaluation.result_digest
    # Exact evidence digests retain creation-time provenance. All business
    # identities and results must still agree across these distinct rebuilds.
    assert first.acceptance_digest != second.acceptance_digest
    first_stable = copy.deepcopy(first.acceptance_record)
    second_stable = copy.deepcopy(second.acceptance_record)
    for stable in (first_stable, second_stable):
        del stable["acceptance_digest"]
        del cast(dict[str, object], stable["evaluation"])["manifest_digest"]
    assert first_stable == second_stable
    assert (
        first.evaluation.manifest["manifest_digest"]
        != second.evaluation.manifest["manifest_digest"]
    )
    for fold in stage3_evaluation.expanding_folds():
        relative = Path("artifacts") / fold.fold_id / "manifest.json"
        first_artifact = json.loads(
            (first.evaluation.evidence_partition / relative).read_text("utf-8")
        )
        second_artifact = json.loads(
            (second.evaluation.evidence_partition / relative).read_text("utf-8")
        )
        assert first_artifact["artifact_id"] == second_artifact["artifact_id"]
        assert first_artifact["provenance"]["created_at"] == "2026-01-01T00:00:00Z"
        assert second_artifact["provenance"]["created_at"] == "2026-01-02T00:00:00Z"
        assert first_artifact["manifest_digest"] != second_artifact["manifest_digest"]
    for collection in ("base_runs", "sensitivity_runs"):
        for field in (
            "artifact_id",
            "model_checksum",
            "training_parameter_digest",
            "training_parameter_revision",
            "window",
        ):
            changed = copy.deepcopy(first.evaluation.manifest)
            reference = next(
                run
                for run in cast(list[dict[str, object]], changed[collection])
                if run["strategy"] == "lightgbm"
            )
            cast(dict[str, object], reference["artifact"])[field] = "changed"
            assert (
                stage3_evaluation._evaluation_result_digest(changed)
                != first.evaluation.result_digest
            ), (collection, field)
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

    conflicting_run_identity = copy.deepcopy(first.acceptance_record)
    conflicting_runs = cast(list[dict[str, object]], conflicting_run_identity["runs"])
    conflicting_runs[0]["run_identity"] = "0" * 64
    conflicting_run_identity["acceptance_digest"] = stage3_oos.stage3_acceptance_digest(
        conflicting_run_identity
    )
    with pytest.raises(stage3_oos.Stage3OosError, match="config/result identity"):
        stage3_oos._require_complete_stage3_acceptance_record(
            conflicting_run_identity,
            allow_synthetic_fixture=True,
            repository_root=Path(stage3_oos.__file__).resolve().parents[4],
        )

    for changed_field in ("scenario_digest", "nautilus_digest", "metric"):
        conflicting = copy.deepcopy(first.acceptance_record)
        changed_run = next(
            run
            for run in cast(list[dict[str, object]], conflicting["runs"])
            if run["scenario"] == "zero_fee"
        )
        if changed_field == "metric":
            metrics = cast(dict[str, object], conflicting["metrics"])
            summaries = cast(list[dict[str, object]], metrics["sensitivity"])
            changed_summary = next(
                item
                for item in summaries
                if item["strategy"] == changed_run["strategy"]
                and item["scenario"] == "zero_fee"
            )
            cast(dict[str, object], changed_summary["summary"])["total_pnl"] = "123.5"
            metrics["summary_digest"] = stage3_oos._digest(
                {
                    key: value
                    for key, value in metrics.items()
                    if key != "summary_digest"
                }
            )
        else:
            changed_run[changed_field] = "0" * 64
        conflicting["acceptance_digest"] = stage3_oos.stage3_acceptance_digest(
            conflicting
        )
        with pytest.raises(
            stage3_oos.Stage3OosError, match="result identity conflicts"
        ):
            stage3_oos._require_complete_stage3_acceptance_record(
                conflicting,
                allow_synthetic_fixture=True,
                repository_root=Path(stage3_oos.__file__).resolve().parents[4],
            )


def test_stage3_oos_checks_persisted_outputs_before_replacing_acceptance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    catalog, stage2_record, template, artifact_lock = (
        momentum_fixture._accepted_fixture(monkeypatch, tmp_path)
    )
    evaluation_fixture._install_real_fixture_matrix(monkeypatch)
    config = feature_fixture._accepted_config(
        template, catalog, tmp_path / "output", artifact_lock_path=artifact_lock
    )
    provenance = synthetic_fixture_provenance(created_at="2026-01-01T00:00:00Z")
    completed = stage3_evaluation.run_stage3_evaluation(
        config, acceptance_record_path=stage2_record, provenance=provenance
    )
    target = tmp_path / "acceptance.json"
    original_record = b'{"previous_acceptance":true}\n'

    def finished_evaluation(
        *_args: object, **_kwargs: object
    ) -> stage3_evaluation.Stage3EvaluationOutcome:
        # Inject the completed output at the publication boundary. Exercise the
        # same replacement writer as formal rebuilds, retaining fixture identity.
        target.write_bytes(original_record)
        return completed

    monkeypatch.setattr(stage3_evaluation, "run_stage3_evaluation", finished_evaluation)
    monkeypatch.setattr(
        stage3_oos, "_write_json_exclusive", stage3_oos._write_json_replacing
    )

    def rebuild() -> None:
        stage3_oos._rebuild_stage3_oos_fixture(
            config,
            stage2_acceptance_record_path=stage2_record,
            stage3_acceptance_record_path=target,
            provenance=provenance,
        )

    # Prove this publication path succeeds when every persisted output is intact.
    rebuild()
    assert target.read_bytes() != original_record
    target.unlink()

    paths = [
        config.run_root / "manifest.json",
        config.run_root / "stage3_partition_identity.json",
        config.evidence_root / "stage3_partition_identity.json",
        config.evidence_root / "training-parameters.json",
    ]
    first_fold = stage3_evaluation.expanding_folds()[0].fold_id
    paths.extend(sorted((config.evidence_root / "artifacts" / first_fold).iterdir()))
    paths.extend(sorted((config.run_root / "base" / first_fold / "lightgbm").iterdir()))
    paths.extend(
        sorted((config.run_root / "sensitivity" / "lightgbm" / "zero_fee").iterdir())
    )
    for path in paths:
        original = path.read_bytes()
        for missing in (True, False):
            if missing:
                path.unlink()
            elif path.suffix == ".json":
                path.write_text('{"corrupt":true}\n', encoding="utf-8")
            else:
                path.write_bytes(b"corrupted model\n")
            try:
                with pytest.raises(stage3_oos.Stage3OosError):
                    rebuild()
                assert target.read_bytes() == original_record, path
            finally:
                path.write_bytes(original)
                target.unlink(missing_ok=True)

    # Excluding time-derived hashes from result identity must not permit a
    # different, internally valid artifact manifest to replace the loaded one.
    artifact_path = config.evidence_root / "artifacts" / first_fold / "manifest.json"
    original_artifact = artifact_path.read_bytes()
    artifact_payload = json.loads(original_artifact)
    artifact_payload["provenance"]["created_at"] = "2026-01-02T00:00:00Z"
    artifact_payload["manifest_digest"] = stage3_oos._digest(
        {
            key: value
            for key, value in artifact_payload.items()
            if key != "manifest_digest"
        }
    )
    stage3_artifacts._validate_manifest_envelope(artifact_payload)
    artifact_path.write_text(json.dumps(artifact_payload), encoding="utf-8")
    try:
        with pytest.raises(
            stage3_oos.Stage3OosError, match="conflicts with loaded artifact"
        ):
            rebuild()
        assert target.read_bytes() == original_record
    finally:
        artifact_path.write_bytes(original_artifact)
        target.unlink(missing_ok=True)

    manifest_path = config.run_root / "base" / first_fold / "lightgbm" / "manifest.json"
    original = manifest_path.read_bytes()
    for field in (
        "artifact",
        "prediction_digest",
        "metrics",
        "fold",
        "scenario",
        "strategy",
        "strategy_config",
        "fee_provenance",
        "nautilus_digest",
    ):
        payload = json.loads(original)
        payload[field] = "0" * 64
        payload["manifest_digest"] = stage3_oos._digest(
            {key: value for key, value in payload.items() if key != "manifest_digest"}
        )
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            with pytest.raises(
                stage3_oos.Stage3OosError, match="conflicts with its summary"
            ):
                rebuild()
            assert target.read_bytes() == original_record
        finally:
            manifest_path.write_bytes(original)
            target.unlink(missing_ok=True)


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


def _formal_rebuild_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Stage3Config, Path, Path, Path]:
    catalog, stage2_record, _template, artifact_lock = (
        momentum_fixture._accepted_fixture(monkeypatch, tmp_path)
    )
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    (repository / "uv.lock").write_text("locked\n", encoding="utf-8")
    for relative in stage3_oos._REBUILD_CONTRACT_PATHS:
        contract_file = repository / relative
        contract_file.parent.mkdir(parents=True, exist_ok=True)
        contract_file.write_text("frozen contract\n", encoding="utf-8")
    expected_lock = repository / STAGE2_ARTIFACT_LOCK_RELATIVE_PATH
    expected_lock.parent.mkdir(parents=True)
    expected_lock.write_bytes(artifact_lock.read_bytes())
    tracked_record = repository / stage3_oos.STAGE3_ACCEPTANCE_RELATIVE_PATH
    tracked_record.parent.mkdir(parents=True, exist_ok=True)
    original = b'{"existing":true}\n'
    tracked_record.write_bytes(original)
    config = Stage3Config(
        schema=STAGE3_CONFIG_SCHEMA,
        dataset_id=STAGE2_DATASET_ID,
        acceptance_digest=STAGE2_ACCEPTANCE_DIGEST,
        dataset_digest=STAGE2_DATASET_DIGEST,
        source_manifest_digest=STAGE2_SOURCE_MANIFEST_DIGEST,
        market_data_manifest_digest=STAGE2_MARKET_DATA_MANIFEST_DIGEST,
        instrument_snapshot_checksum=STAGE2_INSTRUMENT_SNAPSHOT_CHECKSUM,
        runtime_identity=UPSTREAM_RELEASE_IDENTITY,
        artifact_lock_path=expected_lock,
        catalog_path=catalog,
        evidence_root=tmp_path / "formal-output" / "evidence",
        run_root=tmp_path / "formal-output" / "runs",
    )

    def clean_git(_repository: Path, *arguments: str) -> str:
        if arguments == ("status", "--porcelain"):
            return ""
        if arguments == ("rev-parse", "HEAD"):
            return "1" * 40
        raise AssertionError(f"unexpected Git arguments: {arguments}")

    monkeypatch.setattr(stage3_oos, "_repository_root", lambda: repository)
    monkeypatch.setattr(stage3_artifacts, "_run_git", clean_git)
    monkeypatch.setattr(
        stage3_oos, "bind_accepted_stage2_catalog", lambda *_a, **_k: None
    )
    return config, stage2_record, repository, tracked_record


def test_stage3_oos_formal_rebuild_reaches_evaluation_with_tracked_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, stage2_record, _repository, tracked_record = _formal_rebuild_inputs(
        monkeypatch, tmp_path
    )
    original = tracked_record.read_bytes()

    class EvaluationStarted(Exception):
        pass

    def stop_at_evaluation(*_args: object, **kwargs: object) -> None:
        provenance = cast(stage3_artifacts.TrainingProvenance, kwargs["provenance"])
        assert provenance.kind == "formal_git"
        assert provenance.git_sha == "1" * 40
        raise EvaluationStarted

    monkeypatch.setattr(stage3_evaluation, "run_stage3_evaluation", stop_at_evaluation)

    with pytest.raises(EvaluationStarted):
        stage3_oos.rebuild_stage3_oos(
            config,
            stage2_acceptance_record_path=stage2_record,
        )
    assert tracked_record.read_bytes() == original


@pytest.mark.parametrize(
    "drift", ["dirty_tree", "head", "lock", "contract", "missing_lock", None]
)
def test_formal_rebuild_rechecks_repository_immediately_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    drift: str | None,
) -> None:
    config, stage2_record, repository, target = _formal_rebuild_inputs(
        monkeypatch, tmp_path
    )
    original = target.read_bytes()
    frozen_contract = stage3_oos._rebuild_contract_digest(repository)
    candidate: dict[str, object] = {"acceptance_digest": "0" * 64}
    clean_git = stage3_artifacts._run_git

    def build_record(*_args: object, **kwargs: object) -> dict[str, object]:
        assert kwargs["rebuild_contract_digest"] == frozen_contract
        return candidate

    def mutate_after_validation(*_args: object, **_kwargs: object) -> None:
        if drift == "dirty_tree":
            monkeypatch.setattr(
                stage3_artifacts, "_run_git", lambda *_args: " M README.md"
            )
        elif drift == "head":

            def changed_head(root: Path, *args: str) -> str:
                return (
                    "2" * 40
                    if args == ("rev-parse", "HEAD")
                    else clean_git(root, *args)
                )

            monkeypatch.setattr(stage3_artifacts, "_run_git", changed_head)
        elif drift == "lock":
            (repository / "uv.lock").write_text("changed\n", encoding="utf-8")
        elif drift == "contract":
            (repository / stage3_oos._REBUILD_CONTRACT_PATHS[0]).write_text(
                "changed\n", encoding="utf-8"
            )
        elif drift == "missing_lock":
            (repository / "uv.lock").unlink()

    monkeypatch.setattr(
        stage3_evaluation, "run_stage3_evaluation", lambda *_a, **_k: None
    )
    monkeypatch.setattr(stage3_oos, "_build_acceptance_record", build_record)
    monkeypatch.setattr(
        stage3_oos,
        "_require_complete_stage3_acceptance_record",
        mutate_after_validation,
    )

    if drift is None:
        stage3_oos.rebuild_stage3_oos(
            config, stage2_acceptance_record_path=stage2_record
        )
        assert json.loads(target.read_text(encoding="utf-8")) == candidate
    else:
        with pytest.raises(stage3_oos.Stage3OosError, match="identity has drifted"):
            stage3_oos.rebuild_stage3_oos(
                config, stage2_acceptance_record_path=stage2_record
            )
        assert target.read_bytes() == original
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_stage3_oos_atomically_replaces_the_tracked_record(tmp_path: Path) -> None:
    target = tmp_path / "stage3-acceptance.json"
    target.write_text('{"old":true}\n', encoding="utf-8")

    stage3_oos._write_json_replacing(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert not list(tmp_path.glob(f".{target.name}.*.tmp"))


@pytest.mark.parametrize("suffix", [Path(), Path("new-partition")])
def test_stage3_oos_rejects_a_lexical_latest_symlink_before_resolution(
    tmp_path: Path,
    suffix: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    concrete = tmp_path / "catalog-r1"
    concrete.mkdir()
    latest = tmp_path / "latest"
    latest.symlink_to(concrete, target_is_directory=True)

    with pytest.raises(stage3_oos.Stage3OosError, match="latest alias"):
        stage3_oos._external_path(
            latest / suffix,
            repository_root=repository,
            name="catalog_path",
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


def test_committed_stage3_acceptance_record_is_current_or_rejected_as_historical() -> (
    None
):
    record_path = (
        Path(__file__).resolve().parents[2] / stage3_oos.STAGE3_ACCEPTANCE_RELATIVE_PATH
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    # Review E1 requires two formal rebuilds from the repaired clean head. That
    # head does not exist until LCK commits this repair. Preserve this one known
    # historical record unchanged; never relabel it as current acceptance.
    if record["acceptance_digest"] == (
        "f8df1359595ad1ba53ab3cf23f40c8c1b825ffae0023206abaff05d5afeedb4f"
    ):
        assert (
            stage3_oos.stage3_acceptance_digest(record) == record["acceptance_digest"]
        )
        with pytest.raises(stage3_oos.Stage3OosError, match="rebuild contract"):
            stage3_oos.require_complete_stage3_acceptance_record(record)
        return
    stage3_oos.require_complete_stage3_acceptance_record(record)
