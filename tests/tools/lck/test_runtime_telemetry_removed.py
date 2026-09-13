"""Ensure historical workflow telemetry is not restored as runtime authority."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_no_runtime_telemetry_module_or_command_exists() -> None:
    assert not (ROOT / "tools" / "lck" / "telemetry.py").exists()
    assert not any(
        path.name.startswith("test_telemetry") for path in ROOT.rglob("*.py")
    )


def test_active_lck_code_has_no_runtime_telemetry_module_or_command() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "tools" / "lck").glob("*.py")
    ).casefold()
    assert "from .telemetry" not in source
    assert (
        '"telemetry"'
        not in (ROOT / "tools" / "lck" / "cli.py")
        .read_text(encoding="utf-8")
        .casefold()
    )


def test_external_analysis_boundary_is_documented_in_lck_policy() -> None:
    policy = (ROOT / ".agents" / "policies" / "context-retrieval.md").read_text(
        encoding="utf-8"
    )
    assert "outside this repository" in policy
    assert "must not be committed" in policy
    assert "never change permissions" in policy
