from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

AGENT_WORKFLOW = str(Path(__file__).parents[2] / "tools" / "agent_workflow")
if AGENT_WORKFLOW not in sys.path:
    sys.path.insert(0, AGENT_WORKFLOW)

from workflow_common import (  # type: ignore[import-not-found]  # noqa: E402
    CommandRunner,
    build_workflow_env,
    command_warning,
)


def test_build_workflow_env_defaults_to_repo_local_uv_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)

    env = build_workflow_env(tmp_path)

    assert env["UV_CACHE_DIR"] == str(tmp_path / ".workflow.local" / "uv-cache")


def test_build_workflow_env_preserves_explicit_uv_cache_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom_cache = "/some/custom/cache"
    monkeypatch.setenv("UV_CACHE_DIR", custom_cache)

    env = build_workflow_env(tmp_path)

    assert env["UV_CACHE_DIR"] == custom_cache


def test_command_runner_passes_repo_local_uv_cache_to_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "uv-cache-env.txt"
    uv = tmp_path / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text(os.environ['UV_CACHE_DIR'], encoding='utf-8')\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    result = CommandRunner(tmp_path).run(
        ["uv", "run", "--frozen", "pytest"],
        command_id="test-uv-cache-env",
        validation=True,
    )

    assert result.returncode == 0
    assert marker.read_text(encoding="utf-8") == str(
        tmp_path / ".workflow.local" / "uv-cache"
    )


def test_command_runner_times_out_and_reports_bounded_diagnostic(
    tmp_path: Path,
) -> None:
    result = CommandRunner(tmp_path).run(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        command_id="test-timeout",
        timeout_seconds=0.2,
    )

    assert result.returncode == 124
    assert result.stdout == ""
    assert result.timed_out is True
    assert result.timeout_seconds == 0.2
    warning = command_warning(result)
    assert warning["timed_out"] is True
    assert warning["timeout_seconds"] == 0.2
    assert "timed out after 0.2 seconds" in warning["error"]


def test_command_runner_retries_only_when_requested(tmp_path: Path) -> None:
    marker = tmp_path / "attempted"
    flaky = tmp_path / "flaky-command"
    flaky.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        f"marker = Path({str(marker)!r})\n"
        "if marker.exists():\n"
        "    print('recovered')\n"
        "else:\n"
        "    marker.write_text('1', encoding='utf-8')\n"
        "    raise SystemExit(1)\n",
        encoding="utf-8",
    )
    flaky.chmod(0o755)

    result = CommandRunner(tmp_path).run(
        [str(flaky)],
        command_id="test-retry",
        timeout_seconds=1,
        retries=1,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "recovered"
