# Historical WSL2 GitHub Evidence Runner archive

This directory is frozen historical publication evidence for the pre-LCK Task
Evidence Runner. It is not a current workflow entry point.

During the LCK v1 cutover the executable compatibility/control surface was
removed from the repository:

```text
tools/agent_workflow/wsl2_github_evidence_runner.py
tools/agent_workflow/wsl2_github_evidence_profiles.json
.codex/rules/tracequant-wsl-evidence.rules
tests/tools/test_wsl2_github_evidence_runner.py
tests/tools/test_wsl2_github_evidence_rules.py
```

The JSON evidence and templates retained in this directory document the former
Runner and may therefore contain those historical paths, profile names,
snapshot identifiers, and drift terminology. They are provenance only and must
not be used to select a Task target, authorize a write, recheck a current Task
phase, or bypass LCK.

Current Task lifecycle control is exclusively:

```text
uv run --frozen python tools/agent_workflow/lck.py <phase> <operation> <task>
```

Feature-level audit evidence remains a separate read-only concern implemented
by the retained `workflow_evidence.py` feature-audit operations; it is not a
Task lifecycle control path.
