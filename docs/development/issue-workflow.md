# Issue Workflow（Agent-neutral Lifecycle Specification）

本文件是 TraceQuant Agent-neutral Issue lifecycle 的 **shared semantic owner**：
Issue 从建立到完成的所有 shared lifecycle semantics（readiness、Delivery、
PR/CI、Independent Review 引用、Human Gate、Closeout、Feature completion、
natural-language entry resolution、source-of-truth 模型、failure/ambiguity
handling）由本文件权威定义。

Codex 与 Claude Code 的 workflow Skills 引用本文件作为生命周期语义来源，
各自保留 agent-specific executable procedure。本文件不复制 Retrieval v2 规则
（其 authoritative owner 是根目录 `AGENTS.md`）。

> **共享文档读取纪律**：shared document 的存在 ≠ 每次全文加载。
> Skill / Agent 应按 current intent / phase 读取本文件最小必要 section，
> 评估充分性后再按需扩展。Delivery 不应因本文件存在而默认加载
> Review / Closeout / Feature Completion 全部章节。

## 1. Lifecycle overview

```text
Issue (Specifying)
  → Ready（codex:ready + Project Ready + 无 blocker）
  → Delivery（branch → implementation/tests → commit/push → PR）
  → Independent Review（fresh session，read-only；可与 CI 并行）
  → maintainer manual Squash Merge
  → Closeout（merge identity / state convergence / branch cleanup）
  → Feature completion（hierarchy-aware audit）
```

GitHub Issue、Parent/Sub-issues、blocked-by/blocking、Pull Request、Project
Status 与 labels 是 durable workflow state 的载体；本文件定义其语义，
GitHub native metadata 是其机械事实。

## 2. Issue specification ownership

- Issue 正文的 authoring 规则与信息所有权由 `docs/development/issue-authoring.md`
  （Issue Specification v2）权威定义。
- 本文件不重复 authoring 规则；specification 编写 / 修订时按需引用
  `issue-authoring.md`。
- 当前 leaf Issue body 是 current work-item business specification
  （见 §15 Source-of-truth authority model）。

## 3. Lifecycle metadata ownership

| Metadata | Owner | 说明 |
|---|---|---|
| Issue body | issue-authoring.md（authoring 规则）+ maintainer | body 是 canonical specification |
| type/area labels | maintainer / issue-authoring | issue type 语义 |
| `codex:ready` / `codex:blocked` / `codex:needs-spec` | lifecycle 状态标签，本文件定义语义 | ready = 可实施；blocked = 不得实施；needs-spec = 规格不完整 |
| Project Status（Inbox/Specifying/Ready/In Progress/Review/Blocked/Done） | lifecycle 状态，与 labels 配合 | 本文件定义含义；Runner 提供机械事实 |
| Parent / Sub-issues / blocked-by / blocking | GitHub native metadata | 不在正文重复机械 metadata |
| PR / merge identity | GitHub native（Squash Merge） | closeout 验证对象 |

不得在 Issue 正文重复维护与 native relationship 等价的机械 metadata。

## 4. Readiness

一个 Task 满足以下条件才可开始 Delivery：

- `codex:ready` label 存在且无 `codex:blocked`；
- Project Status = Ready（或等效 lifecycle 状态）；
- 无未解决的 blocked-by；
- body 具有完整 Objective / Requirements / Acceptance Criteria / Non-goals；
- 无 Human Gate 未决事项。

readiness 由 deterministic Runner 快照验证（机械事实），
不由模型对全文的解读决定。

## 5. Natural-language entry resolution

维护者可使用以下 Agent-neutral 自然语言入口，无需知道内部 Skill 名称。
详细解析契约：

### `实现 Issue #N`

- intent = **Delivery**
- 解析：target Issue → readiness 验证 → shared Delivery lifecycle semantics
  （§6）→ 正确 Delivery Skill（Codex: `.agents/skills/task-delivery-runner/`；
  Claude: `.claude/skills/task-delivery-runner/`）→ 需要时 deterministic Runner。
- 不要求维护者提供内部 Skill 名称。

### `审查 PR #N`

- intent = **Independent Review**
- 解析：target PR → fresh-session expectation → read-only independent review
  → head lock → 当前 Task specification + effective diff →
  no Delivery verdict inheritance。shared semantics 见
  `docs/development/pr-review.md`；执行 Skill：`task-pr-review-runner`
  （Codex: `.agents/skills/task-pr-review-runner/`；
  Claude: `.claude/skills/task-pr-review-runner/`）。

### `PR #N 已人工合并，请完成 closeout`

- intent = **Closeout**
- 解析：merge identity 验证 → reviewed head == merged head →
  Issue / Project lifecycle convergence → canonical main 同步 →
  branch lifecycle → post-merge validation。执行 Skill：`task-closeout`
  （Codex: `.agents/skills/task-closeout/`；Claude: `.claude/skills/task-closeout/`）。

### Feature completion

维护者请求对指定 open Feature 执行独立只读 completion audit 时：
intent = **Feature Completion Audit**，执行 Skill：`feature-completion-audit`
（Codex: `.agents/skills/feature-completion-audit/`；
Claude: `.claude/skills/feature-completion-audit/`；
hierarchy-aware exception，见 `AGENTS.md`）。

### 解析失败 / 歧义

intent 无法可靠解析时：**不要猜**。回退到 explicit Skill-name fallback，
或进入 Human Gate（§12）。

## 6. Delivery semantics

The project-level `uv.toml` directs canonical `uv` commands to
`.workflow.local/uv-cache`. This is local runtime state and the directory is
Git-ignored; an explicit `UV_CACHE_DIR` override remains supported. Workflow
subprocesses continue to receive the same path through `build_workflow_env()`.

- Task `Critical Outcome` 是正式 Delivery gate：LCK 从当前 Task body 读取结构化
  contract，使用固定 pytest verifier 执行真实 supported path。缺失、malformed、unsafe
  target 或 verifier failure 均 fail closed；不得用普通 unit/static check 替代。
- Initial Delivery 的 lifecycle mechanics（workspace prepare、commit validated tree、
  remote synchronization、OPEN PR resolve/create、Project Status → Review）由 LCK
  deterministic control 执行；Agent/Skill 不提供 branch/SHA/PR/refspec authority。
- 一次 Initial Delivery 覆盖：LCK Delivery Prepare → semantic implementation / targeted
  development validation → LCK Delivery Complete → Critical Outcome → formal validation →
  commit validated tree → ensure remote branch → ensure OPEN PR → observe current CI checks
  （non-blocking）→ Project Status `Review` → final live verification → `READY_FOR_REVIEW`。
- 一个 Task 通常产生一个 PR（base = `main`）。
- Initial Task branch bootstrap is LCK-owned. An existing branch is reusable only when
  Task identity, ownership, base, and the required worktree state are mechanically proven.
  A missing branch is creatable only when current/local/remote `main` identities are
  aligned, no local/remote/worktree conflict exists, and the target uses canonical
  `task/<Issue number>-<slug>` naming. The Skill does not create the branch and has no
  fallback write route.
- Noncanonical names such as `task-<Issue number>` are never new-branch creation targets.
  Historical numeric forms may be reused only for an existing branch whose ownership is
  proven. Ambiguous identity or inconsistent live state fails closed.
- 实现只处理当前 leaf Task；不进行无关重构。Targeted validation during implementation is
  development feedback only. Final Delivery authorization is owned by LCK, which runs the
  Critical Outcome and formal validation before committing the exact validated tree.
- Within one `delivery complete` invocation, the observed Task body, `origin/main`, Task
  branch and committed head are ephemeral preconditions. If they change before a later
  side effect, LCK stops and the next invocation reacquires/revalidates current facts; no
  cross-phase snapshot becomes authority.
- Human Gate 事项未决时不得继续 Delivery（§9）。
- Delivery 不执行 Independent Review、不 merge、不 close Issue、不 closeout。

## 7. PR / CI lifecycle

- PR base = `main`，Squash-only merge policy 由 GitHub Ruleset 定义
  （本文件不重定义）。
- CI（`.github/workflows/ci.yml`）运行 pytest / ruff / mypy。LCK 所要求的
  check 名称由 canonical CI workflow 中的静态 job 定义，并从 **exact trusted
  base commit** 读取；不能读取调用者当前 checkout、未提交修改或 candidate
  head 上的未来 workflow policy。LCK v1 对 dynamic job name / matrix job
  fail closed。Initial Delivery / Remediation 只观察当前 PR checks，不以 required
  check success 作为完成条件；Review Complete / Merge Preflight 由 exact PR base
  policy 约束。候选 head 对该配置的修改只在 merge 后成为后续 operation 的新
  policy，不能降低它自身的验收门禁。GitHub
  `statusCheckRollup` 只提供 exact PR head 上这些 check 的 live result。
  GitHub Ruleset 可继续作为平台侧独立强制层，但 plan/permission-dependent
  Required-Checks discovery API 不作为 LCK lifecycle authority。
- PR 关联 `Closes #N` 仅当 maintainer 预期 merge 后 close。
- merge 决策：passing Independent Review for current PR head
  + maintainer manual Squash Merge。
- merge 前由 LCK 执行 deterministic preflight：
  uv run --frozen python tools/agent_workflow/lck.py merge preflight <TASK>；
  LCK 只返回人工合并就绪，不执行 merge。

## 8. Independent Review

shared semantics 由 `docs/development/pr-review.md` 权威定义：
fresh session、strict read-only、head lock、independent judgement、
no Delivery mechanical authority inheritance、LCK live target resolution、
Review Prepare/Complete operation-boundary stale guard、PASS / FAIL、new head → fresh re-review。

本文件只声明其在 lifecycle 中的位置：Review 可在 CI checks 运行期间与其并行，
但 Review Complete 的 PASS 与 maintainer merge 前的 Merge Preflight 都必须
基于各自 fresh authority 验证 checks 通过；Review FAIL 必须先 STOP，只有
Human 显式发起后才进入 remediation（§10）。

## 9. Human Gate

以下情形必须 STOP 并进入显式 Human Gate（维护者批准前不得 repository
write 或继续）：

- Task body / Audit 揭示 target architecture 与当前 contract 重大冲突；
- 实现必须扩大到 Non-goal（Runner 大改、Context Compiler、Ruleset 等）；
- 规格歧义在 expansion trigger 顺序内无法解决；
- fail-closed 条件触发且无法安全定位冲突源；
- 维护者显式要求。

Human Gate 批准默认在同一执行会话记录；跨会话恢复时维护者必须显式提供
或指向已批准的 gate evidence。**不得**为寻找批准记录而默认读取全部
Issue comments（Retrieval v2 comments default-off 仍适用）。

## 10. Remediation after failed Review

- Review FAIL → LCK 返回 `STOP_REQUIRED`，不得自动启动修复。
- Human 显式提供当前 Task completed FAIL 的 `review_id` 启动 remediation；
  workspace-local Review record 中只有 findings 可作为 semantic input，current PR/head/base
  必须由 LCK live reacquire。跨 clone / Agent runtime 切换导致该 ignored local record 不可用时，
  Human 可额外提供该 completed Review 的 `--findings-file`；它仍然只是 semantic input，
  不能提供任何机械 identity/authorization。
- Implementation Agent 修复后由 LCK 完成 validation / commit / push / existing
  PR reuse，产生 new head，并在 `READY_FOR_NEW_REVIEW` 再次 STOP。
- 如果正式进入 Remediation 后确认不存在 implementation repair，且只剩 external/future
  Review evidence，必须调用 `remediation no-change` 形成 `NO_IMPLEMENTATION_CHANGE`
  receipt 并释放 prepared session；不得制造空提交，也不得手工删除 session marker。该
  operation 会 fresh reacquire 当前 PR/base/head，要求工作树 clean 且 candidate 未变化；
  不 commit/push、不设置 `fresh-review-required`，也不声称 deferred evidence 已满足。
- 只能在 repaired head 形成后、独立 provider 会话或 fresh Review 中真实产生的验收
  evidence（例如 Task 显式要求的 provider-attributed / cross-provider receipts）不得作为
  `remediation complete` 的循环前置条件；它们保持 pending，并在后续 Independent Review
  acceptance 中继续 fail-closed。`READY_FOR_NEW_REVIEW` 不表示这些 evidence 已满足。
- prepared Remediation session 未经 `remediation complete` 或 `remediation no-change`
  正式终结时，新的 Review Prepare 必须 fail closed；不得让 Review 与未终结的
  implementation session 交错，避免遗留 session 锁死后续 invocation。
- successful remediation 设置 `fresh-review-required` negative boundary；在新的 Review
  verdict 被接受前不得再次 remediation。该 boundary 不携带 target authorization。
- 任何新 commit 都需要 **fresh independent re-review**；不得复用旧 verdict。

## 11. Manual Squash Merge boundary

- 只有 maintainer 可以执行 Squash Merge。
- 任何 workflow Skill / Agent 都不得 merge（包括自动 merge）。
- merge 前必须存在当前 PR head 的 passing Independent Review。

## 12. Failure / Ambiguity handling

| 场景 | 行为 |
|---|---|
| Specification ambiguity | 按 expansion trigger 顺序展开 → 仍不 resolve → **Human Gate**，绝不猜测 |
| Conflict（body / parent / ADR / 实现） | **fail closed**；无法安全定位 → Human Gate |
| Missing dependency | native relationship / 最小相关源 → unresolved → Human Gate |
| Review failure | `STOP_REQUIRED` → Human explicit remediation → new head → fresh re-review |
| Head changed during Review | `REVIEW_STALE_HEAD`，本次 verdict 无效并重新 Review |
| Relevant base changed during Review | `REVIEW_STALE_BASE`，本次 verdict 无效并重新 Review |
| Merge head ≠ reviewed head | **block closeout**，人工介入 |
| NL entry 无法解析 | explicit Skill-name fallback / Human Gate |

## 13. Closeout semantics

Closeout 仅在 maintainer 已人工 Squash Merge 后执行：

1. 由
   uv run --frozen python tools/agent_workflow/lck.py closeout <TASK>
   从当前 Git / GitHub facts 重新求解 merge identity（reviewed head == 实际 merged head）；
2. 将 Business Delivery 与 Cleanup 分离：merged PR 可立即得到
   Business Delivery = COMPLETE，cleanup 失败只产生 Cleanup = PENDING；
3. 收敛 Issue / Project lifecycle（state、Status、labels）并同步 canonical
   `main`；
4. 仅在 head/tree/worktree proof 完整时删除已验证的 Task branch，识别
   GitHub 已删除的 remote branch，并允许安全重试；
5. 不依赖旧 Kernel session 或 authoritative snapshot lineage。

Closeout 不 repair 代码、不手动 close Issue、不清理无关 branch、
不评估 Feature completion。

## 14. Feature Completion

Feature Completion Audit 是 hierarchy-aware exception（普通 leaf-Issue-first
不适用于它）：读取 target Feature + 相关 child hierarchy + completion state +
implementation/validation evidence，但保持有界（不读无关 comments / docs /
roadmap）。verdict 供 maintainer closeout 决策使用。

## 15. Source-of-truth authority model

Normative / semantic authority 与 Mechanical / factual authority 是两层，
不做单一线性排序：

### Normative / semantic authority（自上而下）

1. system / platform constraints（`.codex/rules`、`.claude/settings.json`、CI、Ruleset）
2. maintainer explicit current instruction / approved Human Gate
3. repository hard rules + active durable architecture decisions / ADR
   （`AGENTS.md` hard sections、`docs/architecture/*`）
4. current leaf Issue body —— **current work-item business specification**
5. Agent-neutral workflow specification（本文件、`pr-review.md`）
6. agent-specific executable Skill
7. historical Issue comments / reports（默认不可见，显式 trigger 才读，
   永不 override 4）

current leaf Issue body 不得覆盖：system/platform、maintainer constraint、
safety、repository hard invariant、active durable architecture decision。

### Mechanical / factual authority

current GitHub / Git / CI / deterministic Runner facts 对其事实域具有
authority：Issue state、labels、Project Status、Parent、blocked-by/blocking、
PR identity、base/head SHA、review head、merge identity、CI/check status、
branch/main state。

mechanical facts 可以使 stale textual assumption 失效，并触发
fail-closed / Human Gate；但 **不得成为 business specification**，
不得覆盖 current Issue requirement。

### Workflow identity locking

与当前 phase 相关的 workflow object identity 必须锁定并验证：Task / Issue
identity、PR base、PR head、effective diff、reviewed head、audited main、
merge SHA 等。这些 identity 在要求稳定的 phase 中发生变化时，必须
invalidation / fail closed / Human Gate，不得静默继续。

### Skill / Runner version is not itself a workflow gate

除非 current Task、repository hard rule 或 approved workflow policy 明确
要求，否则不要求为了执行 workflow 将 Skill / Runner 强制从 `main`、PR base
或特定 trusted commit 重新加载。当前 worktree 中适用的 active Skill / Runner
可以作为执行工具；workflow correctness 由 locked workflow object
identities + current canonical specification + deterministic evidence 保证。
不重新引入 trusted Skill、main-only Skill、Skill hash as workflow state 等
已明确不采用的旧设计。

`AGENTS.md` 只保留 fresh Agent 必须知道的简洁原则；本文件承载完整模型。

## 16. 与其它 instruction 的关系

- `AGENTS.md`：always-loaded control plane（hard rules、Retrieval v2、
  routing 原则）。本文件是 AGENTS 中 lifecycle 细节的 on-demand 展开层。
- `docs/development/pr-review.md`：Independent Review shared semantics。
- `docs/development/issue-authoring.md`：Issue specification authoring。
- `.agents/policies/*`：deterministic mechanics / evidence policy
  （Runner 与证据的权威规则）。
- Codex / Claude workflow Skills：各自 agent-specific executable procedure，
  引用本文件相应 section（按需，非全文）。
