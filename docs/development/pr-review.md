# PR Independent Review（Agent-neutral Review Semantics）

本文件是 TraceQuant Independent PR Review 的 **shared semantic owner**：
fresh-session、strict read-only、head lock、independent judgement、
verdict semantics 与 remediation 规则由本文件权威定义，Codex / Claude 的
`task-pr-review-runner` Skills 引用本文件，各自保留 agent-specific
executable procedure（命令序列、权限行为、Runner argv 等）。

> **读取纪律**：Review Skill 启动时按 intent 读取本文件最小必要 section；
> 不因本文件存在而默认加载 Delivery / Closeout / Feature Completion 内容。

## 1. Purpose / Independence

- Review 是对一个 implementation-bearing leaf PR 的**独立**质量门禁：fresh session 中由未参与
  该 head 实现或 remediation 的 session 执行。
- Review 的结论基于 current leaf specification + effective diff +
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

- target implementation-bearing leaf（number 为主键，current title 为
  canonical；Issue Specification v2 的 `type:task` 与 `type:bug` 均可审查）；
- target PR（base / head SHA）；
- 当前 leaf specification（body + 有效 spec）。

Review admission 使用与 Delivery 相同的 canonical Issue type contract：`type:*`
label 是权威 carrier，native Issue Type（若可见）必须与 label 一致；缺失、冲突、
未知或非 implementation-bearing leaf type 均 fail closed。

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

Review 输出**三态** verdict（与两套 `task-pr-review-runner` Skills 的
executable contract 一致）：

### 1. PASS / `通过，可以人工合并`

只在 semantic review PASS、objective gates PASS、review identity / head
stable 时允许。这是唯一允许进入 maintainer manual merge decision 的状态。

### 2. CONDITIONAL / `有条件通过，不得合并`

当 semantic review 本身无 BLOCKER / HIGH / MEDIUM finding，但一个或多个
objective gate 为 `partial` / `unknown` / temporarily unverifiable，且不
存在已证明的 semantic failure 或 identity drift 时使用。

- CONDITIONAL ≠ semantic approval for merge；它仍然 **DO NOT MERGE**。
- 必须先恢复 / 重新验证客观 gate。典型例子：remote-ref / GitHub network
  verification 因瞬时网络故障变为 `partial`。

### 3. FAIL / `不通过，需要修复`

当存在 BLOCKER / HIGH / MEDIUM finding、identity / head / diff drift、
required semantic requirement FAIL、或其他确定性 gate 明确 fail 时使用。

CONDITIONAL 与 FAIL 都输出 bounded remediation handoff（§9）；
每个 Review 只有一个最终 verdict。

### Deterministic mapping principle

- gate = `pass` + no blocking findings → **PASS**
- gate = `partial` / `unknown` + no blocking findings + identity stable →
  **CONDITIONAL — DO NOT MERGE**
- `unknown` 因 plan-limit `403`：默认 **CONDITIONAL — DO NOT MERGE**。403
  本身不等于 Required-Checks 查询成功；只有仓库存在正式版本化、明确授权、
  并确定性定义条件与证据负担的 capability-limited fallback policy，且
  当前证据满足全部条件时，才允许 upgrade 至 pass。
- `unknown`（其他原因）/ unsupported schema / lifecycle conflict →
  review incomplete / failing（证据不足，不产生 passing verdict）。
- semantic failure / gate `fail` / identity drift → **FAIL**（review invalid
  as applicable）
- head changed during review → **REVIEW INVALIDATED — HEAD CHANGED**
  （是 review invalidation，不是普通 conditional pass）

## 9. Remediation handoff

非 PASS 的 Review（CONDITIONAL / FAIL，见 §8）输出 bounded remediation
handoff，仅包含 Delivery Skill 修复所需的最小信息。每种 terminal verdict
都必须同时产生 canonical review evidence；PASS 的 artifact 使用空的
findings/remediation 列表，不能授权 Delivery remediation：

- findings（severity、位置、失败场景、最小修改方向）；
- 当前锁定的 base/head；
- 重新 review 的要求（new head → fresh re-review）。

除上述 bounded text handoff 外，Review terminal 必须在允许的 ignored local
evidence root 写入一个 canonical structured review artifact：

```text
.agents/evidence.local/review-handoffs/<evidence_id>.json
```

Claude 与 Codex 使用相同的 evidence root。artifact 的 `evidence_id` 是对
去除自身 `evidence_id` 字段后的 canonical JSON 做 SHA-256；文件名必须等于
该 digest。最小字段为：

```json
{
  "schema_version": 1,
  "kind": "independent-review-handoff",
  "repository": "PhoenixSss/tracequant",
  "task": 0,
  "pr": 0,
  "reviewed_base_sha": "<SHA>",
  "reviewed_head_sha": "<SHA>",
  "verdict": "PASS | CONDITIONAL | FAIL",
  "required_findings": [
    {"id": "F1", "severity": "Blocking | High | Medium", "required": true}
  ],
  "required_remediation": [
    {"id": "F1", "required": true, "description": "<bounded repair>"}
  ],
  "objective_gates": [],
  "maintainer_decision_required": false,
  "created_at": "<UTC timestamp>",
  "freshness": {"status": "fresh", "recheck": "pass"},
  "review_evidence": {
    "review_snapshot_id": "<ev-id>",
    "recheck_snapshot_id": "<ev-id>",
    "effective_diff_sha256": "<SHA-256>",
    "evidence_matrix_path": ".agents/evidence.local/<matrix>.json",
    "evidence_matrix_sha256": "<SHA-256>",
    "review_skill": {"path": "<Skill>/SKILL.md", "sha256": "<SHA-256>"}
  },
  "evidence_id": "<SHA-256>"
}
```

The Review Skill materializes this artifact through the repository producer
boundary `tools/agent_workflow/workflow_evidence.py` using its
`emit-review-handoff` subcommand, which returns the exact `evidence_id` for the
final textual handoff. This is an allowed local write
under the ignored evidence root: Review remains read-only with respect to
implementation files and all GitHub state, while local content-addressed
evidence emission is required. The Delivery Runner receives that ID explicitly
as `--review-handoff-id` and loads exactly that file. It validates the schema,
content address, actual Review Skill bytes, actual matrix bytes, content-
addressed snapshots, Task/PR/base/head identity, stable recheck identity,
finding severity, freshness, and maintainer-decision state. A submitted GitHub
Review is not required when this artifact is valid; an invalid, stale,
malformed, ambiguous, or conflicting artifact remains fail-closed.

The terminal path is mechanical: after the final stable recheck and verdict
payload are complete, the Review adapter invokes the existing
`wsl2_github_evidence_runner.py review-terminal` profile. The profile performs
one final recollection, materializes the artifact, self-verifies the complete
provenance chain, and exposes the exact `review_handoff_id`. Terminal failure
is review failure; no consumable handoff may be printed. PASS reviews also
materialize canonical evidence, while only CONDITIONAL/FAIL artifacts can
authorize remediation.

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
