# Agent Workflow Skills

本文件是 Agent-neutral workflow Skills 的**注册表 / 导航 / 兼容性入口**：
列出当前 Skills、共享语义 owner 与验证入口。共享生命周期语义由
`docs/development/issue-workflow.md`（lifecycle 规范）与
`docs/development/pr-review.md`（Independent Review 规范）权威定义；
本文件不再复制生命周期语义或执行 procedure。

## Skill 集合

当前 Runner 工作流：

```text
.agents/skills/task-delivery-runner/SKILL.md
.agents/skills/task-pr-review-runner/SKILL.md
.agents/skills/task-closeout/SKILL.md
.agents/skills/feature-completion-audit/SKILL.md
```

Claude Code 对应 Skill：

```text
.claude/skills/task-delivery-runner/SKILL.md
.claude/skills/task-pr-review-runner/SKILL.md
.claude/skills/task-closeout/SKILL.md
.claude/skills/feature-completion-audit/SKILL.md
```

## 共享语义与导航

- Agent-neutral lifecycle 规范：`docs/development/issue-workflow.md`
  （readiness、Delivery、PR/CI、Independent Review 位置、Human Gate、
  remediation、manual Squash Merge、Closeout、Feature Completion、
  natural-language entry、source-of-truth 模型）。
- Independent Review 规范：`docs/development/pr-review.md`
  （fresh session、head lock、verdict semantics、remediation handoff）。
- Issue specification authoring：`docs/development/issue-authoring.md`。
- 自然语言入口（无需维护者提供内部 Skill 名称）：

```text
实现 Issue #N
审查 PR #N
PR #N 已人工合并，请完成 closeout
```

- 每个 Skill 保留自足的 executable procedure；生命周期语义按需从上述 shared
  docs 读取最小必要 section。

## Runner 与证据

规范见：

```text
.agents/policies/workflow-evidence.md
.agents/policies/command-execution.md
```

本地 artifacts 按 ownership 分区：

```text
.agents/evidence.local/      # historical/Feature-audit Evidence output
.agents/validation.local/    # ordinary Validation Runner workspace output
.workflow.local/lck/         # LCK runtime state / preserved Review evidence
```

不得提交。Independent Review 在 source repository 仅写 `.workflow.local/lck/`；
`.agents/validation.local/` 如被 formal Review validation 使用，只存在于 disposable
standalone clone 内。

## Task #88 architecture audit

The current Workflow execution map, Agent / Skill / Runner ownership matrix,
fixed-mechanics coverage audit, evidence limitations, and follow-up candidate
dispositions are recorded in
[`task-workflow-architecture-audit.md`](task-workflow-architecture-audit.md).
That document is an audit/design artifact only; it does not activate Runner,
Context Compiler, batching, Review-session, Closeout, sandbox, approval, or
quality-gate changes.

## Task #123 cleanup evidence

- [Historical Task #122 Migration Acceptance Report](migration-acceptance/task-122-migration-acceptance-report.md)
- [Task #123 Legacy Agent Workflow Cleanup Evidence](legacy-agent-workflow-cleanup.md)

## Skill identity 验证

当前 Codex / Claude Skill 路径、共享语义引用、单一机械入口与每个文件的
SHA-256 由以下只读审计统一验证：

```bash
tools/agent_workflow/skill_path_audit.py
```

审计输出只覆盖 `active_skills` 与 `claude_skills`。已退役 Legacy Skill 不再位于
active discovery namespace，也不再作为 current routing、失败回退或 competing
semantic owner；其历史内容由 Git 历史及 frozen migration / benchmark evidence 保留。

## Final source-of-truth matrix

| Artifact family | Classification | Durable responsibility |
| --- | --- | --- |
| `AGENTS.md` | ACTIVE | repository invariants、leaf-first retrieval 与 natural-language workflow entry |
| `docs/development/issue-workflow.md` | ACTIVE | shared lifecycle、readiness、Delivery、Closeout 与 Feature audit semantics |
| `docs/development/pr-review.md` | ACTIVE | Independent Review semantics 与 verdict/remediation contract |
| `CLAUDE.md` | ACTIVE | Claude-specific thin adapter 与 Skill discovery |
| `.agents/skills/*-runner/`、`.agents/skills/task-closeout/`、`.agents/skills/feature-completion-audit/` | ACTIVE | Codex executable procedures |
| `.claude/skills/` current four Skills | ACTIVE | Claude executable procedures |
| `tools/agent_workflow/wsl2_validation_runner.py`、`workflow_validation.py`、validation profiles 与 current tests | ACTIVE | deterministic validation plans、exit codes 与 bounded diagnostics |
| `tools/agent_workflow/workflow_evidence.py` | AUDIT-ONLY | Feature audit evidence 与 LCK 使用的 read-only query helpers；不具备 Task lifecycle authority |
| pre-LCK Task Evidence Runner、Task profiles、Codex Rules、dedicated Runner/Rules tests 与 `self_review.py` binder/test | REMOVED | 仅保留历史 publication / migration provenance；不属于当前 workflow entry point |
| retired `.agents/skills/task-delivery/`、`.agents/skills/task-pr-review/` | DEAD / ABSENT | Legacy executable Skills 已退役；历史内容由 Git 历史及 frozen evidence 保留 |
| `docs/workflows/task-skill-runner-migration/` 与 `docs/workflows/benchmarks/` | HISTORICAL EVIDENCE ONLY | frozen migration/benchmark/audit provenance |
| Claude current Skills 中的 Codex/Claude permission-boundary 说明 | COMPATIBILITY ONLY | cross-agent adapter guidance; retained intentionally while both agents are supported |
| retired Skill-variant provenance JSON/doc/tool/test bundle | DEAD / ABSENT | replaced by `skill_path_audit.py`; all stale current references removed |
| removed trusted-runner、runtime usage-measurement 与 runtime manifest machinery | DEAD / ABSENT | no current responsibility; absence is regression-tested |

当前没有 `UNCERTAIN` artifact。`COMPATIBILITY ONLY` 项仍有明确的双 Agent
文档用途，未满足删除条件，因此有意保留。Legacy executable Skills 已完成 caller /
routing / validator 收敛后删除，不再作为 compatibility runtime artifact 保留。

Current reference graph：

```text
Issue body (business specification)
  -> AGENTS.md (repository invariants and entry resolution)
  -> shared development docs (lifecycle / review semantics)
  -> agent-specific current Skills (executable procedure)
  -> LCK + Validation Runner (Task lifecycle control and deterministic validation)
  -> workflow_evidence.py (Feature audit / read-only helpers only)
  -> Git / GitHub Issues, relationships, Projects, PRs and CI (durable state)
```

`.agents/evidence.local/`、`.agents/validation.local/` 与 `.workflow.local/lck/` 都是
Git-ignored local artifacts，但 ownership 不同：前两者属于历史 Evidence / ordinary
Validation Runner，后者属于 LCK runtime 与需要跨 temporary Review clone 生命周期保留的
Review evidence。它们都不是 Git/GitHub authority 或新的 source of truth。

## 仓库外 Token 消耗分析边界

Codex rollout JSONL、Token 报告和会话级比较数据只在仓库外分析，且不得提交本仓库。外部分析不改变 Skill 权限、质量门禁、Review verdict、人工 Merge 或完成证据。
