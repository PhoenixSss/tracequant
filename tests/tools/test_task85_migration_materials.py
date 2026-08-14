from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
DOCS = ROOT / "docs" / "workflows" / "task-skill-runner-migration"


def _load(name: str) -> dict[str, Any]:
    value = json.loads((DOCS / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_static_comparison_records_exact_uploaded_base_metrics() -> None:
    value = _load("before-after-command-paths.json")
    assert value["base_sha"] == "e1c3b587a5fa1a61217fb9160015472bc0e36154"
    assert value["before"]["totals"]["lines"] == 685
    assert value["after"]["totals"]["lines"] == 547
    assert value["derived"]["line_delta"] == -138
    assert value["before"]["totals"]["legacy_command_fragment_count"] == 4
    assert value["after"]["totals"]["legacy_command_fragment_count"] == 0


def test_publication_materials_do_not_claim_runtime_token_reduction() -> None:
    value = _load("publication-materials.json")
    claims = {item["id"]: item for item in value["claims"]}
    assert claims["task85-token-result"]["status"] == "not-measured"
    assert value["metrics"]["runtime_tokens"]["value"] == "unavailable"
    assert value["metrics"]["task65_candidate_result"]["status"] == "deferred-to-#86"


def test_authority_map_separates_delivery_review_and_closeout() -> None:
    value = _load("before-after-command-paths.json")
    boundaries = value["authority_boundaries"]
    assert "workflow-delivery" in boundaries["delivery"]["validation"]
    assert (
        "trusted base validation-runner workflow-review"
        in boundaries["review"]["validation"]
    )
    assert boundaries["closeout"]["validation"] == ["workflow-closeout"]


def test_documentation_sources_exist() -> None:
    value = _load("publication-materials.json")
    for relative in value["source_documents"]:
        assert (ROOT / relative).exists(), relative


def test_materials_preserve_manual_and_write_boundaries() -> None:
    text = (DOCS / "README.md").read_text(encoding="utf-8")
    rollback = (DOCS / "rollback-and-compatibility.md").read_text(encoding="utf-8")
    assert "manual-merge requirement" in text
    assert "write approvals" in text
    assert "Do not merely add the old commands" in rollback


def test_live_evidence_template_is_not_success_evidence() -> None:
    value = _load("templates/live-migration-evidence.example.json")
    assert value["status"] == "not-measured"
    assert value["claim_boundary"]["token_reduction_claimed"] is False
    assert value["claim_boundary"]["task65_candidate_executed"] is False
