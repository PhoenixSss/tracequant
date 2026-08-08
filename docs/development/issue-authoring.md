# Issue Authoring (Issue Specification v2)

本文件定义 TraceQuant 的 Issue Specification v2：五类 Issue（Epic / Feature / Task /
Bug / Research）的职责、正文结构、authoring 规则和信息所有权。它与
`.github/ISSUE_TEMPLATE/*.yml` 保持一致，是创建和迁移 Issue 的唯一 authoring 参考。

> **核心原则：Minimum sufficient specification。**
> 让 fresh Codex / Claude session 获得正确完成当前工作所需的最少但充分的信息。
> 不设固定字符数或 Token 上限；正文中每段文字至少承担一个作用：change scope、
> change expected behavior、change acceptance、impose a real constraint、
> point to necessary source of truth。否则 **remove or link instead of copy**。

## 1. 统一语义模型

```text
Epic
→ program / product outcome and boundaries

Feature
→ coherent behavioral capability (WHAT)

Leaf work item
├── Task      → implementation contract
├── Bug       → defect contract
└── Research  → evidence / decision contract
```

- Task、Bug、Research 是不同 work kind，但处于相同 leaf-work 层级，可作为 Feature 的
  Sub-issue。
- 少数情况下 Bug / Research 可直接挂在 Epic 下，但不是默认结构。
- **不得**把五种类型理解为五个互斥层级。
- 普通实现工作默认保持 `1 Task ≈ 1 PR ≈ 1 main squash commit`。

## 2. 信息所有权

一条事实只能有一个 canonical owner，其他位置只引用、不复制（除非当前工作在缺少该
信息时无法正确理解）。

| 信息 | Canonical owner |
|---|---|
| Repository hard safety / agent routing | `AGENTS.md` |
| Claude-specific guidance | `CLAUDE.md` |
| Reusable development workflow | `docs/development/*` |
| Stable architecture invariants | `docs/architecture/*` |
| Durable architecture decisions | ADR |
| Program / product outcome | Epic |
| Behavioral capability | Feature |
| Implementation contract | Task |
| Defect contract | Bug |
| Evidence / decision investigation | Research |
| Current Issue specification | Issue body |
| Discussion / change history | Issue comments |
| Parent hierarchy | GitHub Parent / Sub-issues |
| Blocking dependency | GitHub blocked-by / blocking |
| Status / Priority / Size | Private GitHub Project |
| Classification / lifecycle | Labels |
| Implementation evidence | PR |
| Standard automated validation | CI |
| Merge enforcement | Ruleset |

Issue body 中不得复制 repository-wide rules、标准验证、Git/PR workflow、Project
metadata 或完整 Parent specification。

## 3. 五类 Issue 的职责与正文结构

### Epic — outcome + boundaries

> 为什么做，以及完成后整个系统达到什么状态。Epic 不是普通 coding-agent
> implementation unit。

REQUIRED：`Outcome`、`Why`、`Scope`、`Non-goals`、`Success / Exit Criteria`
OPTIONAL：`Constraints / Decisions`、`Risks`、`References`

- Outcome 描述最终状态，不是 Feature/Task 清单。
- Success / Exit Criteria 描述 Epic-level result。
- 不复制各 Feature 的完整 specification；不维护 child task checklist。
- 不包含标准 pytest / Ruff / mypy / CI、Git workflow、Priority / Size / Status、
  Parent / Dependency 文本字段。
- Constraints / Decisions 只保存真正跨 Feature 且已批准的约束，优先引用 ADR。
- Agent 收到“实现 Epic #N”时不应直接编码：若没有可执行 leaf work item，应报告需要
  拆分或指定 Task/Bug。

### Feature — behavioral capability (WHAT)

> 系统需要具备什么行为或能力。明显偏 WHAT，不偏 HOW。

REQUIRED：`Capability`、`Scope`、`Non-goals`、`Acceptance Criteria`
OPTIONAL：`Context`、`Key Scenarios / Edge Cases`、`Constraints / Decisions`、
`References`

- 不写文件名、class、function、implementation sequence、逐文件修改方案；只有实现
  方式本身已是 approved architecture / compatibility contract 时才进入
  Constraints / Decisions。
- 只有一个主要实现目标、一个 PR 即可完成、无需多个独立 leaf work item、没有长期
  独立 behavioral specification 价值的“Feature”应直接创建 Task。

### Task — implementation contract（主要 coding-agent execution contract）

REQUIRED：`Objective`、`Requirements`、`Acceptance Criteria`
OPTIONAL：`Context`、`Scope Boundary / Non-goals`、`Constraints / Decisions`、
`References`、`Task-specific Verification`

Task 必须满足：one primary objective、bounded scope、independently verifiable、
normally one PR、minimal unrelated context、observable acceptance。

Task 不得复制：parent Feature / Epic 完整正文、repository architecture summary、
Git workflow、branch/commit/PR/merge instruction、标准 pytest / Ruff / mypy、
Project metadata、historical discussion。

`Task-specific Verification` 只用于标准 CI 之外确有必要的特殊验证。

### Bug — defect contract（独立 Form，本身就是 leaf work item）

REQUIRED：`Observed`、`Expected`、`Reproduction / Evidence`、`Acceptance Criteria`
OPTIONAL：`Impact`、`Environment`、`Logs / Screenshots`、
`Scope Boundary / Non-goals`、`Regression Evidence`

- Reproduction / Evidence 可以是复现步骤、失败测试、日志或其他足以定位问题的证据。
- Environment / Logs 非普适字段，均为 optional，不强迫填写 `N/A`。
- Acceptance Criteria 描述恢复后的 contract，而不是简单“错误消失”。

### Research — evidence / decision contract

> 适用于 technical selection、exchange/API investigation、architecture spike、
> benchmark、performance investigation、quantitative hypothesis、dependency /
> security / feasibility / data-quality investigation。

REQUIRED：`Question / Decision Needed`、`Context`、`Scope`、`Non-goals`、
`Evidence / Evaluation Criteria`、`Expected Outcome / Artifact`
OPTIONAL：`Hypotheses`、`Data Requirements`、`Method`、`Constraints`、`References`

边界：已经决定实施 → Task；是否应该实施仍需证据 → Research。

Research 有效结果可以是 `IMPLEMENT` / `DO NOT IMPLEMENT` / `NEEDS MORE EVIDENCE` /
`ARCHITECTURE DECISION`。“决定不做”属于有效完成结果。不强制所有调查填写
Hypotheses / Data Requirements / Method。

## 4. Acceptance Criteria authoring

AC 必须描述**可以观察、测试或明确判断的完成事实**。允许简洁 checklist、
Given/When/Then 或等价 behavioral statements；不强制 Gherkin。

禁止：

- generic Definition of Done（`pytest passes`、`Ruff passes`、`mypy passes`、
  `CI green`、`PR created`）——由 repository workflow / CI 负责；
- 把 implementation plan 当 AC（除非具体结构本身就是 contract）。

优先描述：externally observable behavior、invariant、failure behavior、
state transition、regression condition。

## 5. Non-goals 规则

| Type | Non-goals |
|---|---|
| Epic | REQUIRED |
| Feature | REQUIRED |
| Task | OPTIONAL（存在明显 scope-creep 风险时填写） |
| Bug | OPTIONAL |
| Research | REQUIRED |

不得为了模板完整强迫填写 `None` / `N/A` / `无`。

## 6. Metadata 从正文移除

正文不包含：Parent、Dependency、Priority、Size、Status、Ready checklist、
standard validation、branch/PR workflow。目标 ownership：

```text
Parent                  → GitHub Parent/Sub-issues
hard dependency         → GitHub blocked-by/blocking
Status / Priority / Size → Private GitHub Project
classification / lifecycle → labels
standard validation     → CI / repository rules
branch / PR / Squash workflow → development docs / Ruleset
```

标题前缀 `[Epic] / [Feature] / [Task] / [Bug] / [Research]` 和 `type:*` labels
保留（personal account 下 native Issue Types 不可用，前缀对 notification、PR
reference 和 plain-text Agent input 有辨识价值）。

## 7. Issue body 与 comments

- Issue body = current specification；comments = discussion / decision history。
- 讨论导致 requirement 变化时：更新 body 使其表达最新 canonical specification，
  并添加一条简短 comment 说明 changed what / why。
- Comment 不得长期作为 silent specification override。

## 8. Requirement precedence

```text
1. Platform / maintainer / security hard boundaries
2. Repository hard invariants and active durable decisions
   (applicable AGENTS.md, active architecture contracts, accepted ADRs)
3. Current leaf Issue body (Task / Bug / Research)
4. Parent Feature current specification
5. Parent Epic current outcome / constraints
6. General conventions / background docs
7. Historical discussion / comments
```

- specific Task 不得静默违反 safety invariant / active ADR。
- current canonical source 优先于复制到下级 Issue 的旧文本。
- comment 改 requirement 后必须 canonicalize 回 body。
- 同级 source 冲突且无法确定权威时触发 Human Gate。

## 9. Research closeout

Research body 表达 pre-research specification；不把完整过程日志堆入正文。

- 小型调查：Issue final comment 记录 Conclusion / Evidence / Recommendation /
  Follow-up。
- 有长期复用价值：提交 versioned repository research report，由 PR review；
  Issue 只保留简短结果和报告链接。
- Architecture decision：`Research → evidence → ADR`，Issue 链接 ADR。
- 如需实现：`Research → new Task(s)`。

## 10. Semantic information budget（Token 效率）

不设字符数 / Token 上限。优先消除：

- **Very high**：Epic 中复制 child Feature specs；Task 中复制 repository-wide
  rules；permanent body 中保存大段 workflow boilerplate。
- **High**：standard validation；Git workflow；Parent / Dependency duplication；
  Project metadata duplication。
- **Medium**：Ready checklist；required `N/A` 字段；over-structured Research
  fields。

不得为了节省 Token 删除：safety constraints、task-specific requirements、
observable behavior、meaningful edge cases、acceptance、important scope boundary。

## 11. 迁移规则（历史 Issue 改写时适用）

- 只迁移**明确尚未开始实施**的 Issue（OPEN + Project/lifecycle state 尚未开始 +
  无 active/merged implementation PR + 无 implementation-in-progress evidence）。
- Closed / Done / In Progress / Review / 已完成但 metadata 漂移 / 已产生正式
  implementation history 的 Issue 一律不改写。
- Epic / Feature 不能只看自身 Status：如果任何 descendant 已进入 implementation、
  已完成或已产生实际 PR，默认 `DO NOT AUTO-MIGRATE`，除非维护者明确批准。
- **Normalize structure, preserve meaning**：允许调整章节、合并重复、删除
  repository-wide boilerplate、删除已由 native GitHub/Project 正确表达的 metadata、
  修复明显 active stale naming；禁止改变产品目标、新增 requirement、删除 safety
  constraint / observable behavior / Non-goal / 重要 edge case、改变 AC 语义、
  根据当前代码重新设计旧 specification、把实现建议升级为 requirement。
- **Form `type: markdown` guidance 不得进入 Issue body**：Form 顶部的 authoring
  guidance 只用于创建 Issue 时向作者显示辅助说明（`type: markdown` 不会作为用户
  输入提交），迁移/shadow conversion 只生成由可提交 form fields 对应的
  specification 内容；migrated Issue body 必须直接从第一个实际 specification
  section（`### Outcome` / `### Capability` / `### Objective` 等）开始。
- 缺失信息（Non-goals、AC、Evidence criteria 等）只能从原正文、current canonical
  parent、明确引用且仍 active 的 ADR / architecture contract 恢复；无法确定时标记
  `NEEDS MAINTAINER REVIEW`，不自动创造 requirement。
- 正文中的 Parent/Dependency 只有已被 native GitHub relationships 正确表达时才可
  移除；否则标记 `NEEDS MAINTAINER REVIEW`，不得静默删除。
- 迁移默认只修改 Issue body；title、state、labels、assignee、milestone、Parent、
  Sub-issues、blocked-by/blocking、Project fields、comments、linked PR 一律不动。
- 批量改写前必须经过 Human Gate；正式迁移逐个进行：重新读取最新 body → 与
  snapshot hash 比较（已变化则 SKIP 并报告）→ 转换 → 更新 → 重新读取做 semantic
  comparison。

## 12. 相关文档

- `AGENTS.md` — repository hard safety、agent routing、issue-driven workflow。
- `CLAUDE.md` — Claude Code 开发命令与架构上下文。
- `.github/ISSUE_TEMPLATE/*.yml` — 五类 Issue Form 的字段定义（本文档的机器可执行
  对应物）。
- `docs/architecture/*` — 稳定架构不变量。
- `docs/development/*` — 可复用开发工作流（本文件即其一）。
