# Agent Workflow Skills

本文件是 Agent-neutral workflow Skills 的**注册表 / 导航 / 兼容性入口**：
列出当前 Skills、历史基准、共享语义 owner 与验证入口。共享生命周期语义由
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

历史基准，仅在维护者明确点名时使用：

```text
.agents/skills/task-delivery/SKILL.md
.agents/skills/task-pr-review/SKILL.md
```

不要在一个会话中组合历史基准与 Runner Skill。

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
docs/workflows/workflow-evidence.md
```

本地 artifacts：

```text
.agents/evidence.local/
.agents/validation.local/
```

不得提交。

## Skill provenance 与验证入口

当前 Runner Skill 与保留的历史 baseline Skill 路径、哈希和共存约束由
`skill_path_audit.py` 机械审计；它不接受历史 Skill 作为当前 Runner 的回退路径。

验证命令：

```bash
tools/agent_workflow/skill_path_audit.py
```

## 仓库外 Token 消耗分析边界

Codex rollout JSONL、Token 报告和会话级比较数据只在仓库外分析，且不得提交本仓库。外部分析不改变 Skill 权限、质量门禁、Review verdict、人工 Merge 或完成证据。
