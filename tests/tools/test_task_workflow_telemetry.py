from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).parents[2] / "tools" / "agent_workflow" / "telemetry.py"
PYTHON = os.environ.get("WORKFLOW_TEST_PYTHON", sys.executable)


def _write_repo(
    tmp_path: Path,
    *,
    ignored: bool = True,
    valid_config: bool = True,
) -> Path:
    repo = tmp_path / "repo"
    agents = repo / ".agents"
    agents.mkdir(parents=True)
    ignore_lines = []
    if ignored:
        ignore_lines = [
            ".agents/task-workflow-telemetry.local.toml",
            ".agents/telemetry.local/",
        ]
    (repo / ".gitignore").write_text("\n".join(ignore_lines) + "\n", encoding="utf-8")
    raw_value = "false" if valid_config else "true"
    (agents / "task-workflow-telemetry.local.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                'output_dir = ".agents/telemetry.local"',
                'default_mode = "baseline-only"',
                f"store_raw_transcript = {raw_value}",
                "store_command_output = false",
                "store_file_contents = false",
                "allow_usage_patch = true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return repo


def _run(
    repo: Path,
    *args: str,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [PYTHON, str(SCRIPT), *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr)
    return result


def _start(repo: Path, task: int = 123, mode: str = "baseline-only") -> dict[str, Any]:
    result = _run(
        repo,
        "start",
        "--task",
        str(task),
        "--task-title",
        "[Task] Example",
        "--mode",
        mode,
        "--task-kind",
        "feature-code",
        "--size",
        "M",
        "--risk-class",
        "normal",
        "--workflow-shape",
        "task-only",
        "--repository",
        "owner/repo",
        "--workflow-main-sha",
        "abcdef1234567",
        check=True,
    )
    value: dict[str, Any] = json.loads(result.stdout)
    return value


def _phase_file(repo: Path, name: str, *, source: str = "unavailable") -> Path:
    usage: dict[str, Any] = {
        "source": source,
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
        "model": None,
    }
    if source != "unavailable":
        usage.update(input_tokens=100, output_tokens=20, total_tokens=120)
    value = {
        "schema_version": 1,
        "event_type": "phase-summary",
        "usage": usage,
        "context": {
            "governance": {
                "files_read": 2,
                "bytes_read": 1000,
                "lines_read": 50,
                "repeated_bytes_estimate": 100,
            }
        },
        "operations": {
            "tool_calls": 2,
            "github_queries": 1,
            "git_commands": 1,
            "validation_commands": 0,
            "sandbox_attempts": 2,
            "elevated_attempts": 0,
            "retries": 0,
            "retry_categories": {},
            "command_categories": {"github-read": 1, "git-read": 1},
        },
        "report": {
            "report_characters": 100,
            "report_lines": 5,
            "report_estimated_tokens": 25,
            "report_estimation_method": "chars-div-4",
            "copied_to_next_phase": False,
        },
        "rework": {
            "commits_added_after_first_handoff": 0,
            "head_sha_changes": 0,
            "independent_review_runs": 0,
            "review_invalidations": 0,
            "maintainer_decisions": 0,
            "interruptions": 0,
            "findings_by_severity": {},
        },
        "outcome": {
            "phase_result": "pass",
            "workflow_result": None,
            "review_verdict": None,
            "feature_audit_verdict": None,
            "validation_passed": True,
            "telemetry_complete": True,
        },
        "limitations": [],
    }
    path = repo / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _record(repo: Path, phase: str, data_file: Path, task: int = 123) -> dict[str, Any]:
    result = _run(
        repo,
        "record",
        "--task",
        str(task),
        "--phase",
        phase,
        "--data-file",
        str(data_file),
        check=True,
    )
    value: dict[str, Any] = json.loads(result.stdout)
    return value


def _record_required_phases(
    repo: Path,
    task: int = 123,
    source: str = "unavailable",
) -> None:
    for phase in ("task-delivery", "task-pr-review", "task-closeout"):
        _record(repo, phase, _phase_file(repo, f"{phase}.json", source=source), task)


def test_start_and_duplicate_start(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    started = _start(repo)
    assert started["active"] is True
    duplicate = _run(
        repo,
        "start",
        "--task",
        "123",
        "--task-title",
        "[Task] Example",
        "--repository",
        "owner/repo",
        "--workflow-main-sha",
        "abcdef1234567",
        "--task-kind",
        "feature-code",
        "--size",
        "M",
        "--risk-class",
        "normal",
        "--workflow-shape",
        "task-only",
    )
    assert duplicate.returncode == 2
    assert "already has active run" in duplicate.stderr


def test_invalid_config_and_unignored_storage_are_rejected(tmp_path: Path) -> None:
    invalid_repo = _write_repo(tmp_path / "invalid", valid_config=False)
    invalid = _run(
        invalid_repo,
        "start",
        "--task",
        "1",
        "--task-title",
        "[Task] Example",
        "--repository",
        "owner/repo",
        "--workflow-main-sha",
        "abcdef1234567",
        "--task-kind",
        "feature-code",
        "--size",
        "S",
        "--risk-class",
        "normal",
        "--workflow-shape",
        "task-only",
    )
    assert invalid.returncode == 2
    assert "store_raw_transcript must remain false" in invalid.stderr

    unignored_repo = _write_repo(tmp_path / "unignored", ignored=False)
    unignored = _run(
        unignored_repo,
        "start",
        "--task",
        "2",
        "--task-title",
        "[Task] Example",
        "--repository",
        "owner/repo",
        "--workflow-main-sha",
        "abcdef1234567",
        "--task-kind",
        "feature-code",
        "--size",
        "S",
        "--risk-class",
        "normal",
        "--workflow-shape",
        "task-only",
    )
    assert unignored.returncode == 2
    assert "not covered by an exact .gitignore rule" in unignored.stderr


def test_status_without_local_config_is_non_blocking(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = _run(repo, "status", "--task", "9", "--json")
    assert result.returncode == 0
    value = json.loads(result.stdout)
    assert value["active"] is False
    assert value["telemetry_available"] is False


def test_record_is_append_only_and_rejects_duplicate_primary_summary(
    tmp_path: Path,
) -> None:
    repo = _write_repo(tmp_path)
    _start(repo)
    data_file = _phase_file(repo, "phase.json")
    first = _record(repo, "task-delivery", data_file)
    assert first["recorded"] is True
    duplicate = _run(
        repo,
        "record",
        "--task",
        "123",
        "--phase",
        "task-delivery",
        "--data-file",
        str(data_file),
    )
    assert duplicate.returncode == 2
    assert "already exists" in duplicate.stderr


def test_record_rejects_conflicting_workflow_sha_before_append(
    tmp_path: Path,
) -> None:
    repo = _write_repo(tmp_path)
    started = _start(repo)
    run_dir = repo / started["run_path"]
    events_path = run_dir / "events.jsonl"
    manifest_path = run_dir / "manifest.json"
    active_path = repo / ".agents" / "telemetry.local" / "active" / "task-123.json"
    summary_path = run_dir / "summary.json"

    before_events = events_path.read_bytes()
    before_manifest = manifest_path.read_bytes()
    before_active = active_path.read_bytes()

    data_file = _phase_file(repo, "conflicting-closeout.json")
    data = json.loads(data_file.read_text(encoding="utf-8"))
    data["identity"] = {
        "task_canonical_title": "[Task] Example",
        "pr_number": 456,
        "base_sha": "1111111",
        "head_sha": "2222222",
        "workflow_main_sha": "7654321fedcba",
    }
    data_file.write_text(json.dumps(data), encoding="utf-8")

    rejected = _run(
        repo,
        "record",
        "--task",
        "123",
        "--phase",
        "task-closeout",
        "--data-file",
        str(data_file),
    )
    assert rejected.returncode == 2
    assert "workflow_main_sha conflicts with manifest" in rejected.stderr
    assert events_path.read_bytes() == before_events
    assert manifest_path.read_bytes() == before_manifest
    assert active_path.read_bytes() == before_active
    assert not summary_path.exists()

    data["identity"]["workflow_main_sha"] = "abcdef1234567"
    data_file.write_text(json.dumps(data), encoding="utf-8")
    recorded = _record(repo, "task-closeout", data_file)
    assert recorded["recorded"] is True

    stored_event = json.loads(events_path.read_text(encoding="utf-8").splitlines()[-1])
    assert stored_event["identity"]["workflow_main_sha"] == "abcdef1234567"
    assert stored_event["identity"]["base_sha"] == "1111111"
    assert stored_event["identity"]["head_sha"] == "2222222"


def test_schema_and_sensitive_fields_are_rejected(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    _start(repo)
    bad_schema = repo / "bad-schema.json"
    bad_schema.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    result = _run(
        repo,
        "record",
        "--task",
        "123",
        "--phase",
        "task-delivery",
        "--data-file",
        str(bad_schema),
    )
    assert result.returncode == 2
    assert "schema_version" in result.stderr

    sensitive = repo / "sensitive.json"
    sensitive.write_text(
        json.dumps({"schema_version": 1, "prompt": "secret prompt"}),
        encoding="utf-8",
    )
    result = _run(
        repo,
        "record",
        "--task",
        "123",
        "--phase",
        "task-delivery",
        "--data-file",
        str(sensitive),
    )
    assert result.returncode == 2
    assert "forbidden" in result.stderr


def test_usage_patch_and_exact_overwrite_rejection(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    _start(repo)
    event = _record(repo, "task-delivery", _phase_file(repo, "phase.json"))
    usage_file = repo / "usage.json"
    usage_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "usage": {
                    "source": "runtime-exact",
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "output_tokens": 25,
                    "reasoning_tokens": 5,
                    "total_tokens": 125,
                    "model": "model-a",
                },
            }
        ),
        encoding="utf-8",
    )
    patched = _run(
        repo,
        "patch-usage",
        "--task",
        "123",
        "--phase",
        "task-delivery",
        "--event-id",
        event["event_id"],
        "--data-file",
        str(usage_file),
        check=True,
    )
    assert json.loads(patched.stdout)["source"] == "runtime-exact"
    second = _run(
        repo,
        "patch-usage",
        "--task",
        "123",
        "--phase",
        "task-delivery",
        "--event-id",
        event["event_id"],
        "--data-file",
        str(usage_file),
    )
    assert second.returncode == 2
    assert "exact usage cannot be overwritten" in second.stderr


def test_finish_validate_and_summarize_are_deterministic(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    _start(repo)
    _record_required_phases(repo, source="runtime-exact")
    finish = _run(repo, "finish", "--task", "123", check=True)
    finished = json.loads(finish.stdout)
    assert finished["telemetry_complete"] is True

    validated = _run(repo, "validate", "--task", "123", check=True)
    assert json.loads(validated.stdout)["valid"] is True

    first = _run(repo, "summarize", "--task", "123", "--format", "json", check=True)
    second = _run(repo, "summarize", "--task", "123", "--format", "json", check=True)
    assert first.stdout == second.stdout
    summary = json.loads(first.stdout)
    assert summary["total_usage"]["total_tokens"] == 360
    assert summary["usage_coverage"]["by_source"]["runtime-exact"] == 3
    assert summary["quality"]["validation_all_passed"] is True
    assert summary["quality"]["findings_by_severity"]["blocking"] == 0


def test_summarize_refreshes_stale_derived_summary(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    started = _start(repo)
    _record_required_phases(repo)
    _run(repo, "finish", "--task", "123", check=True)

    summary_path = repo / started["run_path"] / "summary.json"
    stale = json.loads(summary_path.read_text(encoding="utf-8"))
    stale["missing_phases"] = ["manual-merge"]
    stale["telemetry_complete"] = False
    summary_path.write_text(json.dumps(stale), encoding="utf-8")

    invalid = _run(repo, "validate", "--task", "123")
    assert invalid.returncode == 2
    assert "stored summary does not match" in invalid.stderr

    summarized = _run(
        repo,
        "summarize",
        "--task",
        "123",
        "--format",
        "json",
        check=True,
    )
    value = json.loads(summarized.stdout)
    assert value["missing_phases"] == []
    assert value["telemetry_complete"] is True
    assert json.loads(summary_path.read_text(encoding="utf-8")) == value

    validated = _run(repo, "validate", "--task", "123", check=True)
    assert json.loads(validated.stdout)["valid"] is True


def test_optional_manual_merge_event_remains_compatible(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    _start(repo)
    _record_required_phases(repo, source="runtime-exact")

    manual_merge_file = _phase_file(repo, "manual-merge.json")
    manual_merge = json.loads(manual_merge_file.read_text(encoding="utf-8"))
    manual_merge["event_type"] = "manual-merge"
    manual_merge["usage"] = {
        "source": "unavailable",
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
        "model": None,
    }
    manual_merge_file.write_text(json.dumps(manual_merge), encoding="utf-8")
    _record(repo, "manual-merge", manual_merge_file)

    finished = _run(repo, "finish", "--task", "123", check=True)
    assert json.loads(finished.stdout)["telemetry_complete"] is True
    summary = _run(
        repo,
        "summarize",
        "--task",
        "123",
        "--format",
        "json",
        check=True,
    )
    value = json.loads(summary.stdout)
    assert value["missing_phases"] == []
    assert value["total_usage"]["total_tokens"] == 360
    assert value["usage_coverage"]["by_source"]["runtime-exact"] == 3
    assert value["usage_coverage"]["by_source"]["unavailable"] == 1


def test_finish_marks_incomplete_run_without_required_phases(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    _start(repo)
    _record(repo, "task-delivery", _phase_file(repo, "phase.json"))
    finish = _run(repo, "finish", "--task", "123", check=True)
    value = json.loads(finish.stdout)
    assert value["telemetry_complete"] is False
    assert "task-pr-review" in value["missing_phases"]


def test_spot_check_reports_insufficient_samples(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    _start(repo, mode="spot-check")
    _record_required_phases(repo, source="runtime-exact")
    _run(repo, "finish", "--task", "123", check=True)
    summary = _run(repo, "summarize", "--task", "123", "--format", "json", check=True)
    value = json.loads(summary.stdout)
    assert value["spot_check"]["sample_sufficient"] is False
    assert value["spot_check"]["anomaly_flags"] == []


def test_cli_help_for_all_commands() -> None:
    for command in (
        None,
        "start",
        "status",
        "record",
        "patch-usage",
        "finish",
        "validate",
        "summarize",
    ):
        args = [PYTHON, str(SCRIPT)]
        if command is not None:
            args.append(command)
        args.append("--help")
        result = subprocess.run(args, check=False, capture_output=True, text=True)
        assert result.returncode == 0
        assert "usage:" in result.stdout.lower()


def test_feature_selector_uses_associated_active_run(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    result = _run(
        repo,
        "start",
        "--task",
        "123",
        "--task-title",
        "[Task] Example",
        "--repository",
        "owner/repo",
        "--workflow-main-sha",
        "abcdef1234567",
        "--feature",
        "7",
        "--task-kind",
        "feature-code",
        "--size",
        "M",
        "--risk-class",
        "normal",
        "--workflow-shape",
        "task-plus-feature-audit",
        check=True,
    )
    assert json.loads(result.stdout)["active"] is True

    status = _run(repo, "status", "--feature", "7", "--json", check=True)
    status_value = json.loads(status.stdout)
    assert status_value["active"] is True
    assert status_value["task_number"] == 123
    assert status_value["feature_number"] == 7

    phase = _phase_file(repo, "feature-audit.json")
    recorded = _run(
        repo,
        "record",
        "--feature",
        "7",
        "--phase",
        "feature-completion-audit",
        "--data-file",
        str(phase),
        check=True,
    )
    assert json.loads(recorded.stdout)["phase"] == "feature-completion-audit"


def test_feature_workflow_requires_audit_but_not_manual_merge(
    tmp_path: Path,
) -> None:
    repo = _write_repo(tmp_path)
    _run(
        repo,
        "start",
        "--task",
        "123",
        "--task-title",
        "[Task] Example",
        "--repository",
        "owner/repo",
        "--workflow-main-sha",
        "abcdef1234567",
        "--feature",
        "7",
        "--task-kind",
        "feature-code",
        "--size",
        "M",
        "--risk-class",
        "normal",
        "--workflow-shape",
        "task-plus-feature-audit",
        check=True,
    )
    _record_required_phases(repo)

    before_audit = _run(repo, "status", "--feature", "7", "--json", check=True)
    assert json.loads(before_audit.stdout)["active"] is True

    _run(
        repo,
        "record",
        "--feature",
        "7",
        "--phase",
        "feature-completion-audit",
        "--data-file",
        str(_phase_file(repo, "feature-audit-complete.json")),
        check=True,
    )
    finished = _run(repo, "finish", "--feature", "7", check=True)
    value = json.loads(finished.stdout)
    assert value["missing_phases"] == []
    assert value["telemetry_complete"] is True


def test_feature_workflow_reports_missing_audit(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    _run(
        repo,
        "start",
        "--task",
        "123",
        "--task-title",
        "[Task] Example",
        "--repository",
        "owner/repo",
        "--workflow-main-sha",
        "abcdef1234567",
        "--feature",
        "7",
        "--task-kind",
        "feature-code",
        "--size",
        "M",
        "--risk-class",
        "normal",
        "--workflow-shape",
        "task-plus-feature-audit",
        check=True,
    )
    _record_required_phases(repo)
    finished = _run(repo, "finish", "--task", "123", check=True)
    value = json.loads(finished.stdout)
    assert value["telemetry_complete"] is False
    assert value["missing_phases"] == ["feature-completion-audit"]


def test_sensitive_token_value_and_naive_timestamp_are_rejected(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    _start(repo)

    token_value = repo / "token-value.json"
    token_value.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "limitations": ["gh" + "p_" + ("a" * 24)],
            }
        ),
        encoding="utf-8",
    )
    result = _run(
        repo,
        "record",
        "--task",
        "123",
        "--phase",
        "task-delivery",
        "--data-file",
        str(token_value),
    )
    assert result.returncode == 2
    assert "sensitive value" in result.stderr

    naive_time = repo / "naive-time.json"
    naive_time.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "recorded_at": "2026-07-24T12:00:00",
            }
        ),
        encoding="utf-8",
    )
    result = _run(
        repo,
        "record",
        "--task",
        "123",
        "--phase",
        "task-delivery",
        "--data-file",
        str(naive_time),
    )
    assert result.returncode == 2
    assert "timezone" in result.stderr


def test_validate_rejects_corrupted_manifest_and_event_schema(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    started = _start(repo)
    run_dir = repo / started["run_path"]
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["unexpected"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = _run(repo, "validate", "--task", "123")
    assert result.returncode == 2
    assert "unknown fields" in result.stderr

    del manifest["unexpected"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    event = _record(repo, "task-delivery", _phase_file(repo, "phase.json"))
    events_path = run_dir / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    events[0]["usage"]["unexpected"] = 1
    events_path.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    result = _run(repo, "validate", "--task", "123")
    assert result.returncode == 2
    assert "unknown usage fields" in result.stderr
    assert event["recorded"] is True


def test_record_rejects_timestamp_before_latest_event(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    _start(repo)
    first = _phase_file(repo, "first.json")
    first_data = json.loads(first.read_text(encoding="utf-8"))
    first_data["recorded_at"] = "2030-01-01T00:00:00Z"
    first.write_text(json.dumps(first_data), encoding="utf-8")
    _record(repo, "task-delivery", first)

    second = _phase_file(repo, "second.json")
    second_data = json.loads(second.read_text(encoding="utf-8"))
    second_data["event_type"] = "interruption"
    second_data["recorded_at"] = "2029-01-01T00:00:00Z"
    second.write_text(json.dumps(second_data), encoding="utf-8")
    result = _run(
        repo,
        "record",
        "--task",
        "123",
        "--phase",
        "task-pr-review",
        "--data-file",
        str(second),
    )
    assert result.returncode == 2
    assert "precedes the latest event" in result.stderr


def test_phase_identity_supplements_summary_run_identity(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path)
    _start(repo)
    phase = _phase_file(repo, "identity.json")
    data = json.loads(phase.read_text(encoding="utf-8"))
    data["identity"] = {
        "task_canonical_title": "[Task] Example",
        "pr_number": 456,
        "feature_number": None,
        "base_sha": "abcdef1234567",
        "head_sha": "1234567abcdef",
        "workflow_main_sha": "abcdef1234567",
        "model": None,
        "changed_files_count": 3,
        "changed_lines": 40,
        "acceptance_criteria_count": 5,
    }
    phase.write_text(json.dumps(data), encoding="utf-8")
    _record(repo, "task-delivery", phase)
    _run(repo, "finish", "--task", "123", check=True)
    summary = _run(
        repo,
        "summarize",
        "--task",
        "123",
        "--format",
        "json",
        check=True,
    )
    run = json.loads(summary.stdout)["run"]
    assert run["pr_number"] == 456
    assert run["base_sha"] == "abcdef1234567"
    assert run["head_sha"] == "1234567abcdef"


def test_numeric_usage_coverage_distinguishes_unavailable_from_measured(
    tmp_path: Path,
) -> None:
    unavailable_repo = _write_repo(tmp_path / "unavailable")
    _start(unavailable_repo)
    _record_required_phases(unavailable_repo, source="unavailable")
    _run(unavailable_repo, "finish", "--task", "123", check=True)
    unavailable_summary = json.loads(
        _run(
            unavailable_repo,
            "summarize",
            "--task",
            "123",
            "--format",
            "json",
            check=True,
        ).stdout
    )
    coverage = unavailable_summary["usage_coverage"]
    assert coverage["events_with_usage"] == 3
    assert coverage["events_with_numeric_usage"] == 0
    assert coverage["events_without_numeric_usage"] == 3
    assert coverage["numeric_usage_complete"] is False

    measured_repo = _write_repo(tmp_path / "measured")
    _start(measured_repo)
    _record_required_phases(measured_repo, source="runtime-exact")
    _run(measured_repo, "finish", "--task", "123", check=True)
    measured_summary = json.loads(
        _run(
            measured_repo,
            "summarize",
            "--task",
            "123",
            "--format",
            "json",
            check=True,
        ).stdout
    )
    measured_coverage = measured_summary["usage_coverage"]
    assert measured_coverage["events_with_numeric_usage"] == 3
    assert measured_coverage["events_without_numeric_usage"] == 0
    assert measured_coverage["numeric_usage_complete"] is True


def test_report_handoff_and_evidence_operation_metrics_are_aggregated(
    tmp_path: Path,
) -> None:
    repo = _write_repo(tmp_path)
    _start(repo)
    data_file = _phase_file(repo, "phase-with-evidence.json")
    value = json.loads(data_file.read_text(encoding="utf-8"))
    value["operations"].update(
        {
            "evidence_snapshots": 1,
            "evidence_rechecks": 1,
            "validation_runner_invocations": 1,
            "fallbacks": 0,
            "snapshot_drifts": 1,
        }
    )
    value["report"].update(
        {
            "previous_handoff_characters": 400,
            "previous_handoff_lines": 20,
            "previous_handoff_estimated_tokens": 100,
            "previous_handoff_estimation_method": "chars-div-4",
        }
    )
    data_file.write_text(json.dumps(value), encoding="utf-8")
    _record(repo, "task-delivery", data_file)
    for phase in ("task-pr-review", "task-closeout"):
        _record(repo, phase, _phase_file(repo, f"{phase}.json"))
    _run(repo, "finish", "--task", "123", check=True)
    summary = json.loads(
        _run(
            repo,
            "summarize",
            "--task",
            "123",
            "--format",
            "json",
            check=True,
        ).stdout
    )
    delivery = summary["phases"]["task-delivery"]
    assert delivery["operations"]["evidence_snapshots"] == 1
    assert delivery["operations"]["evidence_rechecks"] == 1
    assert delivery["operations"]["validation_runner_invocations"] == 1
    assert delivery["operations"]["snapshot_drifts"] == 1
    assert delivery["report"]["previous_handoff_characters"] == 400
    assert delivery["report"]["previous_handoff_lines"] == 20
    assert delivery["report"]["previous_handoff_estimated_tokens"] == 100
    assert delivery["report"]["previous_handoff_estimation_methods"] == [
        "chars-div-4"
    ]
