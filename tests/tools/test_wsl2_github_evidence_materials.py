from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
DOCS = ROOT / "docs" / "workflows" / "wsl2-github-evidence-runner"


def _load(relative: str) -> dict[str, Any]:
    value = json.loads((DOCS / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_historical_baseline_is_aggregate_and_non_additive() -> None:
    value = _load("historical-command-baseline.json")
    assert value["status"] == "observed-from-external-aggregate-reports"
    samples = {item["task"]: item for item in value["samples"]}
    assert samples[63]["metrics"]["git_containing_command_calls"] == 110
    assert samples[63]["metrics"]["github_gh_containing_command_calls"] == 54
    assert samples[64]["metrics"]["git_containing_command_calls"] == 98
    assert samples[64]["metrics"]["github_gh_containing_command_calls"] == 65
    assert "must not be added together" in value["scope"]


def test_environment_capability_records_no_secret_value() -> None:
    value = _load("environment-capability.json")
    assert value["tools"]["github_cli"]["observed_version"] == "2.97.0"
    security = value["security"]
    assert security["token_committed"] is False
    assert security["token_value_recorded"] is False
    assert security["complete_credential_recorded"] is False


def test_live_templates_are_valid_and_not_pass_evidence() -> None:
    paths = [
        "templates/live-profile-evidence.example.json",
        "templates/live-recheck-evidence.example.json",
        "templates/rules-live-evidence.example.json",
        "templates/external-live-evidence-manifest.example.json",
    ]
    for path in paths:
        value = _load(path)
        assert value["status"] == "not-measured"


def test_publication_index_references_existing_sources() -> None:
    value = _load("publication-materials.json")
    for relative in value["source_documents"]:
        path = ROOT / relative
        assert path.exists(), relative


def test_committed_materials_do_not_contain_token_shapes() -> None:
    for path in DOCS.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "ghp_" not in text
        assert "github_pat_" not in text
        assert "-----BEGIN PRIVATE KEY-----" not in text
