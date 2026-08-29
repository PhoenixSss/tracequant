# PR Independent Review（LCK Review / Remediation Semantics）

本文件是 TraceQuant Independent Review 与 Remediation 的 shared semantic owner。
Review / Remediation 的机械生命周期控制属于 LCK；Codex / Claude 只承担语义角色。
Git / GitHub current state 是机械事实权威，Delivery 输出、旧 snapshot、expected SHA、
PR identity handoff 或 validation snapshot 都不能授权后续 Review / Remediation。

## 1. Purpose / independence

Independent Review 是一个单独、fresh 的只读 invocation。Review Agent 不得参与
被审查 head 的 implementation 或 remediation，也不得继承 Delivery 的正确性、
风险、AC 覆盖或推荐结论。

Review Agent 只负责：

```text
Inspect
Reason
Judge
Report
```

Review 不修复实现、不提交 GitHub Review、不改 Issue / PR / Project、不 merge。

## 2. LCK launcher preflight

在第一次调用 LCK 前，先确认项目解释器入口可用：

```bash
command -v uv
uv --version
uv run --frozen python --version
```

该检查失败属于 environment/launcher failure，不是 Review verdict，也不得转换为
`STOP_REQUIRED`。裸 `python` 不是本仓库的环境依赖；不要通过全局 alias 或软链接规避。

## 3. LCK live target resolution

Review 必须从以下入口开始：

```bash
uv run --frozen python tools/agent_workflow/lck.py review prepare <TASK>
```

调用方只提供 Task number。不得向 Review LCK 传入 Delivery 提供的 base SHA、head
SHA、PR number、checks snapshot、validation snapshot 或 snapshot id 作为 authority。

LCK 在本次 invocation 开始时从 live Git / GitHub 重新解析 authoritative inputs：

- current OPEN non-Draft PR；
- current PR base / head；
- current Task Contract；
- current applicable checks；
- Review eligibility 所需的其他 live facts。

`merge base`、`effective diff` 与 `changed-file inventory` 不是额外的 live authority
query；它们在 base/head identity 冻结、standalone clone 创建并按需 materialize exact
commit 后，**只在 temporary clone 内机械推导**。因此 source repository 即使尚未拥有
current PR head object，也不得在 clone 创建前因本地 `git merge-base/diff` 失败而阻止
fresh Review。

`review prepare` 在开始 live resolution、temporary clone 和 formal validation 前建立一个
Task-local operation marker；它只用于阻止同一 Task 的重复 in-flight Prepare，不是
`review_id` 或跨阶段 authority。成功时才返回 `review_id`、live review target、Task
Contract、validation、checks 和 isolated `review_root`。Formal validation FAIL 会先
把 command-level result、validated base/head 和 evidence path 持久化，再 fail closed，
不产生 semantic Review `review_id`。Marker 在 standalone clone path 被预留之前
只记录 operation ownership；创建后、validation、异常退出和成功 handoff 都更新
同一个 marker。成功 handoff 会一直保留到 Review completion，以便 command result
丢失时仍能从 marker + guard 判断 ownership；后续 Prepare 只有在 owner 已退出且
handoff 的 clone/guard 不完整时才回收其 LCK-owned state。

LCK 的最终 stdout 是紧凑的 `lck-agent-view`，并提供 `receipt_reference`；Review Prepare
仍直接提供完整 Task Contract 与 sealed review target 所需的最小输入。完整 operation
snapshot、guard、validation/check evidence 和诊断信息保存在 ignored
`.workflow.local/lck/audit-receipts/` Receipt 中，progress heartbeat 仍只写 stderr，不能
成为第二个 lifecycle result。

Review Prepare 的 formal validation 是 exact reviewed head 的 authoritative mechanical
validation。Semantic Reviewer 应消费 Prepare 返回的 validation/check evidence，不默认重跑
pytest、Ruff、mypy、lock 或 Skill validator 等等价 suite。这里的 independent 指
**independent semantic judgement**，不是 duplicate mechanical validation。

## 4. Fresh semantic context and read-only workspace

LCK 在系统临时目录创建 reviewed head 的 detached standalone clone；它通过本地 clone
读取 source repository，但不注册 source worktree、不修改 source `.git/worktrees`。Clone
使用独立 Git metadata/object storage，origin 恢复为真实 remote；LCK 先在 exact head 上运行
正式 Review validation；需要跨 clone 生命周期保留的 validation evidence 复制到 ignored
`.workflow.local/lck/`，再将整个 temporary Review clone 封为 read-only。Review Complete
成功持久化 PASS / FAIL，或以需要重新 Prepare 的 stale terminal outcome 结束后，才直接删除该
clone；如果 Review Complete 在 terminal result 持久化前因 transient/infrastructure failure
退出，必须保留 guard、clone 和 prepare marker，使同一 `review_id` 可安全重试。异常中断只需
按 operation-owned path 回收临时目录，不需要 `git worktree remove/prune`。

Reviewer 只从 `review_root`、当前 Task Contract 和必要的 current GitHub context
建立新的 semantic context。不得把 Delivery self-review、旧 Review verdict、旧
remediation rationale 或 persuasive framing 当作 evidence。

Review Agent 可以读取完整 effective diff、相关 unchanged code、tests、docs、config
和 public interfaces；必须逐项判断 Acceptance Criteria、正确性、failure paths、
安全边界与回归风险。
对 tests 的检查是读取 test source、判断 coverage 与 failure semantics。sealed `review_root`
是 immutable review evidence artifact，不是 executable development workspace。若 LCK 已提供的
validation/check evidence 无法支持某项 requirement 判断，Reviewer 必须报告明确的
validation/evidence gap，而不是在 sealed clone 中自行执行 suite 创建替代 validation authority。
Review Prepare 不等待 CI checks 进入终态；semantic Review 可以与 CI 并行。Checks
success 只在 Review Complete 与后续 Merge Preflight 的 fresh gate 中决定是否可进入
人工合并边界。

## 5. Review Complete：fresh applicability snapshot

语义审查结束后，调用 `review complete`。它是一个**新的 LCK operation**，不是
Review Prepare authority 的延续：

PASS：

```bash
uv run --frozen python tools/agent_workflow/lck.py review complete <TASK> \
  --review-id <REVIEW_ID> \
  --verdict PASS
```

FAIL：先把 blocking findings 写入 review workspace 之外的临时/ignored 文本文件，
再调用：

```bash
uv run --frozen python tools/agent_workflow/lck.py review complete <TASK> \
  --review-id <REVIEW_ID> \
  --verdict FAIL \
  --findings-file <FINDINGS_FILE>
```

`review_id` 定位 Review Prepare 封存的 reviewed target、validation evidence 和
workspace ownership；这些是“审查了什么”的历史证据，不是 Review Complete 的当前
机械 authority。

Review Complete 在 operation 入口只获取一次 fresh `ReviewCompleteSnapshot`，并与
Prepare 时封存的 target 比较：

该 snapshot 使用 review-complete fact profile：读取当前 Task Contract、关系/阻塞、
OPEN PR identity 与当前 checks，但不读取 Issue comments、closure timeline、source
Delivery workspace staged/changed/worktree inventory 或完整 PR history。PASS 所需的
exact-base required-check policy 绑定在同一个 bounded operation-start window 内，不触发
第二次 full live-state resolve。

```text
PR changed            → REVIEW_STALE_PR
PR head changed       → REVIEW_STALE_HEAD
PR base changed       → REVIEW_STALE_BASE
Task Contract changed → REVIEW_STALE_TASK
effective diff changed→ REVIEW_STALE_DIFF
```

PASS 的 current applicable checks 也必须仍然满足 completion gate；FAIL 只观察 current
checks，从而不会因 pending 或 failed CI 延迟 semantic finding。若 PR/base/head/Task
identity 仍一致，effective diff applicability 从仍受 LCK ownership 保护的 sealed Review
clone 重新机械推导；Review Complete 不要求 source repository materialize reviewed commits。

任何 stale result 都不会把 semantic verdict 发布成当前有效 Review PASS/FAIL，也不会
解除 fresh-review requirement；必须重新执行新的 Review Prepare。Review Complete
snapshot 冻结后不得再 nested/full Resolve。

## 6. Verdict semantics and Merge Preflight

正式 Review 只有两种 semantic verdict：PASS / FAIL，但 PASS 还必须经过独立的
Merge Preflight operation 才能进入人工合并边界。

### PASS

要求：semantic Review 无 blocking finding，Prepare 时 formal Review validation 通过，
Review Complete fresh identity 与 sealed reviewed target 一致，current applicable checks
仍通过。

Review Complete 返回：

```text
READY_FOR_MERGE_PREFLIGHT
```

随后执行新的只读 operation：

```bash
uv run --frozen python tools/agent_workflow/lck.py merge preflight <TASK>
```

Merge Preflight 再获取一次 fresh `MergeSnapshot`，独立确认 current Task / PR / head /
base、accepted Review receipt、**exact PR base commit** 中的 required-check policy 与
当前 exact-head check results、blockers 和 mergeability。当前 checkout 或 PR head
不能重定义本次 merge 的 required-check authority。只有它返回：

```text
READY_FOR_HUMAN_MERGE
```

才可报告 `通过，可以人工合并`，并立即停在 Human manual Squash Merge boundary。
Agent / Skill / LCK v1 都不自动 merge。

Review Complete 与 Merge Preflight 都做 freshness 检查并不重复：二者是两个独立
operation，中间 current state 仍可能变化。

### FAIL / `不通过，需要修复`

当当前 reviewed object 存在 Blocking / High / Medium semantic finding，或 Task 要求
未满足时使用。FAIL 必须提供可供后续修复理解的 findings。

如果 Review Complete 证明该 reviewed target 仍适用，LCK 返回：

```text
STOP_REQUIRED
```

并且：

```text
Review FAIL
→ STOP
→ Human decides what to do
```

绝不自动进入 Remediation，也不存在 Review → Delivery → Review 自动循环。Low / Nit
可以报告，但不应伪装成 blocking finding。

formal validation / checks 自身无法产生可接受结果时，LCK 在 objective gate 处 STOP；
不要通过 `CONDITIONAL`、fallback snapshot 或旧 Delivery evidence 绕过 gate。

## 7. Review record boundary

`review complete` 可以把 accepted Review 结果写到 ignored
`.workflow.local/lck/reviews/` 作为 audit / diagnostic record。它可记录 sealed reviewed
identity、Prepare validation/checks、fresh completion snapshot/checks 和 semantic findings，
用于回答“当时审查了什么、在接受 verdict 时它是否仍适用”。

该 record **不是当前机械授权**。Merge Preflight、Remediation、Closeout 都必须在自己的
operation boundary 获取 fresh Git / GitHub facts。尤其不得把 record 中的旧 head、base、
checks 或 validation 当作后续写操作的 current identity。

## 8. Explicit Remediation

Remediation 只能由 Human 明确发起，并且必须引用当前 Task **最新已完成且为 FAIL**
的 LCK `review_id`：

```bash
uv run --frozen python tools/agent_workflow/lck.py remediation prepare <TASK> \
  --review-id <FAILED_REVIEW_ID>
```

LCK 默认从 workspace-local failed Review audit record 读取 **semantic findings only**。
如果 Human 明确切换 clone / Agent runtime，而该 ignored local record 不存在，可以通过
`--findings-file <COMPLETED_REVIEW_FINDINGS>` 携带已经完成的 Review findings。该文件不是
Review authority，也不能携带机械 target；LCK 在两种路径下都独立从 live Git / GitHub
重新解析：

- current Task；
- current OPEN PR；
- current PR head/base；
- current local/remote Task branch；
- current workspace state。

旧 Review record 或 portable findings file 里的任何 SHA / base / PR identity 都不具有
admission authority；即使当前 head 已变化，机械 target 也以 live state 为准，findings
仅作为待理解的语义输入。Local record 存在时继续走原有严格校验路径；portable findings
只在该 local audit record 缺失时启用，不改变正常 Codex 路径。

Implementation Agent 在 LCK 准备的 current Task workspace 上：

```text
Understand findings
→ Repair
→ Diagnose
→ add regression coverage
```

不得直接 commit / push / create PR / change lifecycle state。

## 9. Remediation completion

语义修复完成后：

```bash
uv run --frozen python tools/agent_workflow/lck.py remediation complete <TASK> \
  --review-id <FAILED_REVIEW_ID> \
  --commit-message "<repair commit message>" \
  --summary "<repair summary>" \
  --risks "<risks or limitations>"
```

LCK 复用 Initial Delivery 已迁移的 bounded effects：

```text
reacquire live facts
→ Critical Outcome
→ formal Delivery validation
→ commit exact validated repair tree
→ ensure remote Task branch
→ reuse existing OPEN PR
→ current checks
→ verify final local/remote/PR head alignment
→ READY_FOR_NEW_REVIEW
→ STOP
```

Remediation 不允许创建替代 PR；current existing OPEN PR 若不存在或身份不再唯一，
必须 STOP。Remediation complete 也不会自动启动 Review。成功 completion 会写入一个本地
`fresh-review-required` **negative lifecycle boundary**：它只阻止再次 remediation，
不选择或授权 PR/head/base；机械 target 仍完全由下一阶段 live state 解析。

任何 repair commit 产生 new head 后，都必须由 Human 在新的 fresh invocation 中
重新启动 Independent Review。只有新的 Review verdict 被 LCK 正式接受后，该 boundary
才解除；因此旧 FAIL `review_id` 不能连续驱动第二次 remediation。

## 10. Provider neutrality

Codex 与 Claude 使用同一个 LCK mechanical contract。二者都可以作为
Implementation Agent 或 fresh Review Agent；provider 选择不改变 lifecycle
correctness、live-state authority、read-only Review、stale guard、Human boundary
或 no-auto-remediation 规则。

## 11. Merge / Closeout interaction

PASS 只表示当前 Review invocation 对当时 live-resolved reviewed object 可进入
Human merge decision。实际 merge 前仍必须由 deterministic merge preflight / Human
确认 current PR 状态；LCK v1 不自动 merge。

Closeout redesign 不属于本 cutover。任何 Closeout consumer 都不得因为存在旧
snapshot / handoff 就跳过 current merged identity 的重新确认。
