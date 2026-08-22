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

非 PASS 的 Review（CONDITIONAL / FAIL，见 §8）在报告末尾输出一个且仅一个
canonical remediation handoff。Review 报告正文与下一 lifecycle invocation
输入分离：正文保留 Verdict、mechanical verification、findings、Acceptance
Criteria coverage 和必要 evidence；handoff 是可以独立复制的完整 Delivery
prompt。

最终输出契约固定为：

````text
## Remediation handoff
```text
请按 task-delivery-runner 修复
[Task] <当前完整标题> #<Task编号>
对应 PR #<PR编号> 的独立审查问题，
并继续处理，直到 PR 再次准备好接受新的独立审查。

Task: #<Task编号>
PR: #<PR编号>
Reviewed head SHA: <SHA>
Verdict: <有条件通过，不得合并 | 不通过，需要修复>

Required remediation:
- [F1][Blocking|High|Medium] <完整 finding，包含证据与预期行为>

Objective gates:
- <尚未满足、不可用、矛盾或需要重新验证的 gate>
- 如无则写：无。

Maintainer decision required:
- <需要维护者授权的事项>
- 如无则写：无。
```
````

canonical code block 必须 self-contained；每个 required finding 在其中完整
出现一次，不得使用“上述”“同上”“见前文”等依赖报告正文的引用。不得在此
section 之前再输出 handoff，也不得再用 wrapper label 嵌套或重复该 block。

handoff 仅包含 Delivery Skill 修复所需的最小信息：

- findings（severity、位置、失败场景、最小修改方向）；
- 当前锁定的 base/head；
- 重新 review 的要求（new head → fresh re-review）。

handoff 不得包含完整历史、无关 findings 或主观偏好。PASS 不输出
remediation handoff section。

Independent Review 保持 strict read-only，不向 GitHub 提交 Approve /
Request changes / Comment Review。`review-remediation` 的 admission 不得依赖
GitHub submitted Review；其语义输入是上述 bounded remediation handoff，
Runner 只负责锁定 Task/PR/base/head 等机械身份。

## 10. Merge / Closeout interaction

- merge 前必须存在当前 PR head 的 passing Review + maintainer manual
  Squash Merge。
- merged head ≠ reviewed head → closeout 必须被阻塞，人工介入。
- merge 后如需验证（closeout），由 closeout Skill 独立执行
  （docs/development/issue-workflow.md §13）。

## 11. Fact trust boundary and inheritance

Review 输入分成两层：

1. **可继承的机械事实**：由 Git / GitHub / CI / deterministic Runner 或
   等价可信事实源产生，具有明确 object identity，可由 Reviewer 重新校验，且
   不包含语义结论。
2. **Reviewer 的语义判断**：对当前 Task specification、effective diff、
   相关代码 / 测试和风险的独立阅读、映射与结论。它不能从 Delivery 或旧
   Review 继承。

“可继承”只表示某个 bounded handoff 可以携带该事实作为重新获取的索引或
候选输入，不表示 Reviewer 可以盲信 snapshot。下列 allowlist 是完整边界；
不在其中的 Delivery 内容默认不是 Review trusted fact。

### 11.1 Allowlisted mechanical facts

| Fact | 可携带的内容 | 最低可信条件 |
|---|---|---|
| Task / PR identity | Task number、current canonical title、PR number、repository、base/head branch | 能由当前 GitHub 对象重新确认，且 Task 与 PR 关系明确 |
| Reviewed object | `base_sha`、`head_sha`、merge-base、effective-diff identity | SHA 可解析，base/head 关系和 effective diff 可重算 |
| Changed files | effective diff 的完整 repository-relative manifest | 来自完整 diff，不是摘要、glob 或手工挑选 |
| Specification identity | Task body hash、Acceptance Criteria identifiers | 当前 leaf body 可重新读取；hash 和 AC 集合可重算 |
| Check facts | required / observed check 的原始状态、名称、run identity、时间 | 来自当前 CI / GitHub 查询；不得携带“因此可合并”的解释 |
| Validation facts | profile、schema / runner identity、exit status、raw result locator、trusted digest | profile 与 base/head 绑定，原始结果可读取并按 freshness contract 重验 |
| Review threads | unresolved-thread count 和 thread identifiers | 来自当前 PR / Review 查询；不携带 finding 的接受或修复结论 |
| Workflow identity | workflow、profile、schema、Runner / Skill content identity | 内容 hash / version 可锁定，且能确认适用于 reviewed object |
| Immutable source locators | commit-pinned 文件、diff、CI result 或 GitHub object locator | locator 指向不可变对象或带有足够 identity 的可重查对象 |

机械事实的来源可以被 handoff 引用，但 source locator、object identity、
freshness contract 任一缺失时，事实不能进入 trusted input。

### 11.2 Facts that always require independent revalidation

Reviewer 至少必须独立确认以下当前事实；handoff 中相同字段不能替代确认：

- 当前 Task / PR identity、repository、canonical title 与两者关系；
- 当前 PR base / head，以及 branch、merge-base 和 effective diff；
- effective diff 的完整内容和 changed-files manifest；
- Task specification hash、Acceptance Criteria identifiers 与当前 body；
- 影响 merge eligibility 的 checks、required-check configuration、Review
  threads 和其它当前状态；
- validation profile、result freshness、Runner / schema identity 与结果；
- merge 时需要的其它当前对象状态（包括 PR open / merged state 和 exact
  merge identity）。

重新校验必须使用当前可信事实源。若 object identity、source freshness 或
字段之间的关系无法确认，Reviewer 必须 fail closed，而不是采用旧值继续
形成 verdict。

### 11.3 Prohibited inherited content

以下内容绝不能成为 Review trusted facts、Acceptance Criteria evidence 或
风险证据：

- “实现正确”“所有 AC 已满足”或“风险可接受”；
- “无需额外测试”或任何对测试充分性的 Delivery 判断；
- Delivery self-review verdict、Delivery 对争议设计的辩护或建议审查方向；
- Delivery reasoning / chain-of-thought；
- 已失败检查的合理化或把暂时未知解释为通过；
- 旧 Review 的 semantic verdict、findings 结论或推荐结果。

调试或 provenance 需要保留 rationale 时，必须逐项标记为
`UNTRUSTED_CLAIM_TO_REVIEW`。该标记内容可以帮助定位来源，但不能进入
evidence matrix，也不能改变 Reviewer 的问题、范围或结论。

### 11.4 Bounded Review Fact Handoff

Fact Handoff 是受限的机械事实包，而不是 Review 报告或语义预审。其最小
字段契约为：

```text
schema_version
task_id
pr_id
task_spec_hash
base_sha
head_sha
effective_diff_sha256
changed_files_manifest
acceptance_criteria_ids
raw_check_facts
validation_facts
workflow_identity
created_at
source_identity
freshness_contract
```

字段规则：

- `task_id` / `pr_id` 必须绑定同一 repository；`task_spec_hash`、base、
  head、diff、manifest 和 AC identifiers 共同定义一个 reviewed object。
- `raw_check_facts` 与 `validation_facts` 只记录可重查的原始状态、profile、
  result locator 和 digest，不把它们改写成质量或合并结论。
- `source_identity` 必须能说明来源对象、查询或 profile identity；
  `freshness_contract` 必须说明哪些当前条件不再满足时 handoff 失效。
- `created_at` 记录产生时间，但不得单独作为 freshness 证明；本契约不
  预设一个可绕过 object revalidation 的固定 TTL。
- 是否把 handoff 实现为 durable artifact 不由本文件预先决定；#91 可以
  实验 artifact 形式，但不能改变字段边界和重新校验要求。

Handoff **不得**包含或派生出以下字段：

```text
delivery_correctness_verdict
delivery_risk_verdict
delivery_ac_satisfaction_verdict
review_conclusion
recommended_review_outcome
```

任何额外的 rationale、priority、suggested focus 或 remediation preference
若被保留，必须是 `UNTRUSTED_CLAIM_TO_REVIEW`，不得被解析为事实字段。

## 12. Drift and invalidation model

下表定义八类 drift 的最低处理。所有“旧 semantic verdict 失效”均指旧的
整体 Review verdict 不得再作为当前 Review / merge eligibility 证据；这不
妨碍未来实验比较是否可以减少重复的机械读取，但不能跳过当前契约要求的
重新确认。

| Drift | 失效的 inherited facts | 重建 handoff | 必须重新运行 / 获取的 mechanical validation | semantic verdict / judgment context |
|---|---|---|---|---|
| `TASK_SPEC_DRIFT` | body hash、AC identifiers，以及从它们派生的 object / evidence | 是 | 重新读取 Task body、重算 spec / AC identity，并重新确认 diff、相关 checks 与 validation | 旧 verdict 失效；必须开始新的 Review judgment context |
| `BASE_DRIFT` | base、merge-base、effective diff、manifest、相关 validation | 是 | 重新锁定 base/head、重算完整 diff，并运行适用 validation / checks | 旧 verdict 失效；必须开始新的 context |
| `HEAD_DRIFT` | head、diff、manifest、checks / validation freshness 和 handoff identity | 是 | 重新锁定 head、重算 diff、重新获取 checks 并运行适用 validation | 旧 verdict 失效；必须开始新的 context |
| `EFFECTIVE_DIFF_DRIFT` | diff digest、changed-files、object identity 和受影响 evidence | 是 | 重新计算完整 diff / manifest，并重新运行受影响的 mechanical validation | 旧 verdict 失效；必须开始新的 context |
| `CHECKS_DRIFT` | raw check facts、required-check freshness、merge-eligibility evidence | 是 | 重新获取当前 checks、threads 和 merge eligibility；validation freshness 受影响时一并重跑 | 旧整体 verdict 失效；至少必须在新的 context 中重新判断 objective gates |
| `VALIDATION_DRIFT` | validation profile / result / digest / freshness | 是 | 使用相同锁定对象重新运行适用 validation profile，并重验 Runner / schema identity | 旧整体 verdict 失效；必须在新的 context 中重新确认验证证据 |
| `WORKFLOW_RULE_DRIFT` | workflow、profile、schema、Runner / Skill identity 与其结果 | 是 | 按当前有效规则重新获取 identity，并重新运行受影响的 mechanical validation | 旧 verdict 失效；必须开始新的 context |
| `HANDOFF_SCHEMA_DRIFT` | handoff schema、字段解释、来源和 freshness contract | 是 | 用当前 schema 重新构造并校验 handoff，重新确认其中全部 object / source facts | 旧 verdict 失效；必须开始新的 context |

具体规则：

1. `TASK_SPEC_DRIFT`、`BASE_DRIFT`、`HEAD_DRIFT` 或
   `EFFECTIVE_DIFF_DRIFT` 出现时，Review 对象已经改变；不得以“代码没有
   变”或“只是 rebase”保留旧 verdict。
2. checks / validation / workflow drift 即使没有改变代码，也会改变当前
   objective gate、证据 freshness 或规则解释；旧整体 verdict 不能用于
   merge，必须重新获取相应事实。
3. 任何 drift 若不能归类，或无法确定 drift 前后的对象关系，按
   `OBJECT_IDENTITY_UNCONFIRMED` 处理：停止、fail closed、丢弃旧 handoff
   和 verdict，等待新的完整锁定。
4. new implementation head、effective diff 改变或 relevant spec / AC 改变
   是强制的 semantic verdict invalidation；这三类不得通过 handoff 复用
   旧 semantic judgement。

## 13. Session isolation semantics

以下概念必须分开，不能用 “fresh session” 一个词替代：

| Concept | 定义 | 不代表什么 |
|---|---|---|
| `fresh top-level session` | 未参与该 reviewed head 实现或 remediation 的新 root session / execution | 不自动保证没有读到 Delivery 结论 |
| `fresh semantic reviewer context` | 只以当前 spec、effective diff 和独立获取的事实建立判断上下文，不带入 Delivery / 旧 Review 的结论、辩护或推荐 | 不要求固定一种进程、CLI 或编排方式 |
| `fact reacquisition` | Reviewer 从 Git / GitHub / CI / Runner 重新读取并核对 facts | 不等于 semantic review 已通过 |
| `fact handoff` | 受 schema、identity 和 freshness 约束的 bounded mechanical facts 包 | 不等于 snapshot 永久可信，也不等于 verdict |
| `delivery reasoning isolation` | 不向 Review trusted input 传递 Delivery reasoning / chain-of-thought / persuasive framing | 不禁止审查者读取必要的实现、测试和规范上下文 |
| `review verdict isolation` | verdict 由当前 Reviewer 独立生成，旧 verdict 只能作为被禁止的 claim 处理 | 不允许 Delivery 预先指定 Review outcome |

满足本契约的候选 execution strategy 至少包括：

| Candidate | Root / semantic context | Mechanical facts |
|---|---|---|
| A | fresh root + fresh semantic reviewer context | full reacquisition |
| B | fresh root + fresh semantic reviewer context | bounded verified handoff，再由 Reviewer revalidate |
| C | alternative isolated reviewer context + fresh semantic reviewer context | full reacquisition |
| D | alternative isolated reviewer context + fresh semantic reviewer context | bounded verified handoff，再由 Reviewer revalidate |

候选只是 #91 的实验输入，不是本 Task 的架构选择。无论采用哪一项，
strict read-only、no Delivery verdict inheritance、current-object revalidation、
independent AC / correctness / safety judgement 和 drift invalidation 都是
不可变 hard gates。

在 #91 作出决定前，当前 `fresh top-level session + full reacquisition` 仍
是正式 operational baseline。该 baseline 的保留不等于宣告它是唯一正确的
最终实现。

## 14. Experimental and downstream protocol input

本节是 #91 可直接消费的契约输入。一个实验 arm 必须先固定同一个 reviewed
object tuple：

```text
task_id
pr_id
task_spec_hash
base_sha
head_sha
effective_diff_sha256
changed_files_manifest
acceptance_criteria_ids
```

在固定 tuple 之后，#91 可以比较上表 A–D 的可选子集，但每个 arm 都必须：

- 以同一组 invariants 为硬门禁，而不是把成本或输入体积下降当作质量豁免；
- 区分 full reacquisition 与 bounded handoff，并记录每个 fact 的来源、
  revalidation 结果和 freshness；
- 对八类 drift 至少验证“旧 handoff / verdict 是否被阻断、是否能重建当前
  object”；
- 单独记录 semantic findings、Acceptance Criteria mapping、objective
  gates 和 session / fact strategy，不把 Delivery self-review 当作 control
  或 evidence；
- 若改变 candidate 矩阵、handoff schema 或 reviewed object，开始新的
  experiment identity，不在同一 arm 中静默替换输入。

因此本契约为实验定义“比较什么”和“哪些结果不可接受”，但不预先决定
session、Context Compiler、durable state 或其它 coordinator 的实现。

## 15. Recovery and closeout consumption

Recovery / closeout 可以在新会话中重建本契约的 mechanical facts：Task / PR
identity、base/head、merge-base / diff、changed files、spec / AC hash、checks、
validation、threads 和 workflow identity。重建必须从可信源重新获取并按照
§11.2 校验，不能把恢复的 handoff 当作历史 verdict。

semantic verdict 必须绑定以下 exact reviewed object：

```text
task_id + pr_id + task_spec_hash + base_sha + head_sha
    + effective_diff_sha256 + changed_files_manifest
    + acceptance_criteria_ids + workflow_identity
```

Closeout / recovery 消费 Review verdict 时至少必须证明：

1. 当前 Task / PR 与 Review record 是同一对象，且 Review record 的来源和
   verdict identity 可定位；
2. 当前 PR base、reviewed head、effective diff、spec / AC identity 和相关
   workflow facts 与 Review record 完全一致；
3. maintainer 实际 merge 的 head 等于 reviewed head，并且 merge tree / scope
   与 reviewed object 一致；
4. 任何 head、spec / AC、effective diff、check / validation 或 workflow drift
   都会阻止继续消费旧 verdict，直到按 §12 重新建立事实和新的 Review
   judgment context。

Independent Review 不提交 GitHub Approve / Request Changes，也不 Merge；因此
“存在可消费的 passing Review”不能被实现为要求一个 GitHub Review 记录。消费
者必须使用符合本契约的 bounded Review record / evidence locator，并独立验证
其 exact identity。#79 的 recovery / closeout、以及后续 #93/#95 的 identity
binding 应复用这些证明条件，不得新增一个绕过 head/spec/diff binding 的捷径。

## 16. Relationship to Skills and policies

- shared semantics：本文件。
- executable procedure：Codex `.agents/skills/task-pr-review-runner/`、
  Claude `.claude/skills/task-pr-review-runner/`（含工具纪律、Runner argv、
  权限行为）。
- mechanical gates / evidence：`.agents/policies/workflow-evidence.md` +
  `tools/agent_workflow/`。
- `AGENTS.md` 仅保留必须 always-loaded 的 hard invariant（fresh independent
  session、review independence、fail closed / head identity safety）。
