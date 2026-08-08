# PR Independent Review（Agent-neutral Review Semantics）

本文件是 TraceQuant Independent PR Review 的 **shared semantic owner**：
fresh-session、strict read-only、head lock、independent judgement、
verdict semantics 与 remediation 规则由本文件权威定义，Codex / Claude 的
`task-pr-review-runner` Skills 引用本文件，各自保留 agent-specific
executable procedure（命令序列、权限行为、Runner argv 等）。

> **读取纪律**：Review Skill 启动时按 intent 读取本文件最小必要 section；
> 不因本文件存在而默认加载 Delivery / Closeout / Feature Completion 内容。

## 1. Purpose / Independence

- Review 是对一个 Task PR 的**独立**质量门禁：fresh session 中由未参与
  该 head 实现或 remediation 的 session 执行。
- Review 的结论基于 current Task specification + effective diff +
  relevant code/tests/checks 的完整证据，不继承 Delivery 的 self-check
  结论（no Delivery verdict inheritance）。
- Review 不修复 findings、不改变 Issue/PR/Project state、不 merge。

## 2. Fresh-session requirement

Review 必须在 fresh session 执行：该 session 未参与 implementation 或
remediation of the reviewed head。任何新 commit 后都必须由新的独立
Review session 重新审查；不得复用旧 session 或旧 verdict。

## 3. Strict read-only

Review 对 repository / Issue / PR / Project 完全只读：不写文件、
不修改 GitHub state、不提交 GitHub Review、不 merge。

## 4. Review identity

Review 锁定以下身份（确定性验证，非模型解读）：

- target Task（number 为主键，current title 为 canonical）；
- target PR（base / head SHA）；
- 当前 Task specification（leaf body + 有效 spec）。

## 5. Head lock

- 必须锁定 reviewed head：base SHA / head SHA / effective diff 在审查期间
  保持不变。
- 审查期间 head 若发生变化：**invalidate review**；重新锁定后重审，
  旧结论无效。
- merge 时 merged head ≠ reviewed head：**block closeout**（见 §10）。

## 6. Effective diff

Review 的对象是 effective diff（base → head 的全部变更），而非部分 commit
或摘要。必须完整阅读变更，并检查变更涉及的代码、测试、文档与相关调用方。

## 7. Evidence expectations

- 机械事实（Issue state、labels、Status、PR identity、checks、diff digest）
  以 deterministic Runner 快照为准（.agents/policies/workflow-evidence.md）。
- 语义证据（正确性、范围、验收覆盖）由 Review session 独立形成
  evidence matrix：逐项对照 Acceptance Criteria、scope boundary、
  相关代码与测试。
- evidence status 与 verdict 的映射必须确定性：不完整证据不得产生
  passing verdict。

## 8. Verdict semantics

固定 verdict 集合：

- **PASSING**：当前 head 满足 Task 的 Objective / Requirements /
  Acceptance Criteria，无 blocker 级 findings。
- **NON-PASSING**：存在必须修复的 findings；输出 bounded remediation
  handoff（§9）。
- 不产出「conditional pass」或模糊状态；每个 Review 只有一个最终 verdict。

## 9. Remediation handoff

NON-PASSING Review 输出 bounded remediation handoff，仅包含 Delivery
Skill 修复所需的最小信息：

- findings（severity、位置、失败场景、最小修改方向）；
- 当前锁定的 base/head；
- 重新 review 的要求（new head → fresh re-review）。

handoff 不得包含完整历史、无关 findings 或主观偏好。

## 10. Merge / Closeout interaction

- merge 前必须存在当前 PR head 的 passing Review + maintainer manual
  Squash Merge。
- merged head ≠ reviewed head → closeout 必须被阻塞，人工介入。
- merge 后如需验证（closeout），由 closeout Skill 独立执行
  （docs/development/issue-workflow.md §13）。

## 11. Relationship to Skills and policies

- shared semantics：本文件。
- executable procedure：Codex `.agents/skills/task-pr-review-runner/`、
  Claude `.claude/skills/task-pr-review-runner/`（含工具纪律、Runner argv、
  权限行为）。
- mechanical gates / evidence：`.agents/policies/workflow-evidence.md` +
  `tools/agent_workflow/`。
- `AGENTS.md` 仅保留必须 always-loaded 的 hard invariant（fresh independent
  session、review independence、fail closed / head identity safety）。
