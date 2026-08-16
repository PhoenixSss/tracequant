# Agent Workflow Skills

## Skill 集合

当前 Runner 工作流：

```text
.agents/skills/task-delivery-runner/SKILL.md
.agents/skills/task-pr-review-runner/SKILL.md
.agents/skills/task-closeout/SKILL.md
.agents/skills/feature-completion-audit/SKILL.md
```

历史基准，仅在维护者明确点名时使用：

```text
.agents/skills/task-delivery/SKILL.md
.agents/skills/task-pr-review/SKILL.md
```

不要在一个会话中组合历史基准与 Runner Skill。

## Runner Delivery

```text
请按 task-delivery-runner 完整处理
[Task] <当前完整标题> #<Task编号>，
直到 PR 准备好接受独立审查。
```

Delivery 负责 readiness、实现、targeted validation、提交、最终
`workflow-delivery`、push、PR、checks、`delivery-readiness` 和独立 Review handoff。
它不执行独立 Review、Merge、Issue close 或 Closeout。

Skill 支持只执行一个指定 Phase。前置事实必须验证，完成后在指定边界停止。

## Independent Review

```text
请使用 task-pr-review-runner，独立只读审查
[Task] <当前完整标题> #<Task编号>
对应的 PR #<PR编号>。

Expected base SHA: <base SHA>
Expected head SHA: <head SHA>
```

Review 必须在未参与实现/修复的新会话中运行，严格只读，锁定 base/head/effective
 diff，完整阅读变更，运行独立 `workflow-review` 和 recheck，输出一个固定 verdict：

```text
通过，可以人工合并
有条件通过，不得合并
不通过，需要修复
```

## Review remediation

非通过 Review 输出 `Remediation handoff`。标准修复调用：

```text
请按 task-delivery-runner 修复
[Task] <当前完整标题> #<Task编号>
对应 PR #<PR编号> 的独立审查问题，
并继续处理，直到 PR 再次准备好接受新的独立审查。

Review remediation handoff:

<粘贴 handoff>
```

Delivery 默认修复 Blocking/High/Medium。Low/Nit 仅在维护者明确要求时处理。客观
pending/unavailable gate 只重查或等待；涉及 Task 范围、验收标准、公共行为或架构
决策时停止等待维护者授权。新 head 必须由新的独立 Review 会话审查。

## Manual Merge 与 Closeout

Review 通过后，维护者核对当前 PR head 与 reviewed head，确认 checks 和 threads，
在 GitHub 人工 Squash Merge。

```text
PR #<PR编号> 已由我人工 Squash Merge。

请使用 task-closeout，完成
[Task] <当前完整标题> #<Task编号>
及 PR #<PR编号> 的合并后核验与分支清理。
```

Closeout 验证真实 Merge、自动 Issue closure、同步 main、post-merge validation、最终
Project/label 状态，并只删除精确验证过的 Task branch。它不会 Merge 或手工关闭
Issue。

## Feature Completion Audit

```text
请使用 feature-completion-audit，独立只读审计
[Feature] <当前完整标题> #<Feature编号>。

Expected main SHA: <当前 main SHA>
```

Feature Audit 锁定 audited main、完整 direct-child set 和 Feature 内容，映射每项
验收标准，审查 current-main integration/safety，运行验证与 recheck，输出一个固定
结论。Feature closeout 仍由维护者人工执行。

## Runner 与证据

规范见：

```text
.agents/policies/workflow-evidence.md
.agents/policies/command-execution.md
docs/workflows/workflow-evidence.md
```

当前 Skill/Runner 内容哈希用于复现，不形成来自 main/base 才可执行的版本门禁。
业务对象 SHA 锁定、独立 Review 和人工 Merge 边界保持不变。

本地 artifacts：

```text
.agents/evidence.local/
.agents/validation.local/
```

不得提交。Token 分析只在仓库外读取 Codex rollout JSONL。

## Skill provenance 与验证入口

当前 Runner Skill 与保留的历史 baseline Skill 路径、哈希和共存约束由
`skill_path_audit.py` 机械审计；它不接受历史 Skill 作为当前 Runner 的回退路径。

验证命令：

```bash
tools/agent_workflow/skill_path_audit.py
```

## 仓库外 Token 消耗分析边界

Codex rollout JSONL、Token 报告和会话级比较数据只在仓库外分析，且不得提交本仓库。外部分析不改变 Skill 权限、质量门禁、Review verdict、人工 Merge 或完成证据。
