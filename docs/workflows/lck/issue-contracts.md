# Issue Authoring (Issue Specification v2)

本文件定义 TraceQuant 的 Issue Specification v2：六类 Issue（Epic / Feature / Task /
Bug / Documentation / Research）的职责、正文结构、authoring 规则和信息所有权。它与
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
├── Documentation → documentation fact contract
└── Research  → evidence / decision contract
```

- Task、Bug、Documentation、Research 是不同 work kind，但处于相同 leaf-work 层级，可作为
  Feature 的 Sub-issue。
- 少数情况下 Bug / Documentation / Research 可直接挂在 Epic 下，但不是默认结构。
- **不得**把六种类型理解为六个互斥层级。
- 普通实现工作默认保持 `1 Task ≈ 1 PR ≈ 1 main squash commit`。

## 2. 信息所有权

一条事实只能有一个 canonical owner，其他位置只引用、不复制（除非当前工作在缺少该
信息时无法正确理解）。

| 信息 | Canonical owner |
|---|---|
| Repository hard safety / agent routing | `AGENTS.md` |
| Claude-specific guidance | `CLAUDE.md` |
| LCK workflow contracts | `docs/workflows/lck/*` |
| Stable architecture invariants | `docs/architecture/*` |
| Durable architecture decisions | ADR |
| Program / product outcome | Epic |
| Behavioral capability | Feature |
| Implementation contract | Task |
| Defect contract | Bug |
| Documentation fact contract | Documentation |
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

## 3. 六类 Issue 的职责与正文结构

### Epic — outcome + boundaries

> 为什么做，以及完成后整个系统达到什么状态。Epic 不是普通 coding-agent
> implementation unit。

REQUIRED：`Outcome`、`Why`、`Scope`、`Non-goals`、`Success / Exit Criteria`
OPTIONAL：`Constraints / Decisions`、`Risks`、`References`

- Outcome 描述最终状态，不是 Feature/Task 清单。
- Success / Exit Criteria 描述 Epic-level result。
- Scope 引用已批准的产品/阶段基线，区分本次核心交付与未纳入的条件式扩展；
  阶段出口描述可观察结果，不因后续扩展尚未实施而追加当前完成义务。
- 软件成果完成、研究候选准入和具体运行/资金授权分别判断，不能相互替代。
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

- Scope 必须说明批准的需求来源、主责模块、完整有限成果及必要行为合同；跨模块时
  明确协同职责，不把跨模块协作本身当作拆 Feature 的理由或改变领域所有权。
- 已有 REQ / DP 编号时引用其批准版本/章节及本 Feature 承担的部分；尚未编号的
  批准要求可引用明确来源，不为填模板发明编号、创建包或扩建规划系统。
- AC 覆盖各项成果、必要失败边界及最终集成结果；满足当前 AC 后停止新增开发义务，
  进入既有 completion audit。子项全部关闭不能代替行为证据或正式完成判断。
- 实施前将所有计划成果分解为有限工作清单；具体叶项、输入和先后安排只在一份
  明确引用的规划记录中维护。Feature 保留完整行为边界，不复制子项规格或状态。
- 不写文件名、class、function、implementation sequence、逐文件修改方案；只有实现
  方式本身已是 approved architecture / compatibility contract 时才进入
  Constraints / Decisions。
- 只有一个主要实现目标、一个 PR 即可完成、无需多个独立 leaf work item、没有长期
  独立 behavioral specification 价值的“Feature”应直接创建 Task。

### Task — implementation contract（主要 coding-agent execution contract）

REQUIRED：`Objective`、`Requirements`、`Critical Outcome`、`Acceptance Criteria`
OPTIONAL：`Context`、`Scope Boundary / Non-goals`、`Constraints / Decisions`、
`References`、`Task-specific Verification`

Task 必须满足：one primary objective、bounded scope、independently verifiable、
normally one PR、minimal unrelated context、observable acceptance。

Requirements 必须明确本项承接的批准成果/要求、必要输入合同及可复用实现。
来源可简短引用到批准规格的版本/章节和适用 REQ / DP；正文仍须足以理解本项行为，
不能只列编号让实现者递归读取上游。存在相邻能力混淆或 scope-creep 风险时，
必须在 Requirements 或 `Scope Boundary / Non-goals` 说明排除项。
每个 Task 只承担有限清单中的明确部分，不要求复制整个 Feature 的剩余工作。

`Critical Outcome` 是 Task-level end-to-end acceptance contract，必须使用固定四行格式：

```text
Caller: <真实受支持入口或调用方>
Capability: <本 Task 新增或改变的能力>
Observable result: <从 Caller 可观察到的结果>
Verification test: tests/.../test_*.py::test_*
```

`Verification test` 只能绑定仓库 `tests/` 下一个明确 pytest node id。它不是任意
shell command，也不能用 private helper/file-exists/grep 代替真实 supported path。LCK
在 Delivery Complete 中运行该 verifier；FAIL 是 Delivery veto。Independent Review 仍需
判断该测试是否真实覆盖 `Caller → Capability → Observable result`，不能仅因 pytest
退出码为 0 就继承语义 verdict。

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

### Documentation — documentation fact contract（独立 Form，本身就是 leaf work item）

> 用于新增、修正或收敛文档事实，完成后不改变系统运行行为。

REQUIRED：`Documentation Goal`、`Requirements`、`Acceptance Criteria`
OPTIONAL：`Additional documentation context`

- Documentation Form 只收集完成本次文档变化所必需的最小信息；Sources / References、
  Scope Boundary / Non-goals、Constraints / Decisions 和 documentation-specific
  verification 仅在确有必要时通过 optional context 补充，不要求填写 `N/A`。
- 如果工作需要修改 runtime、业务源码、测试、CI、Agent control behavior 或其它系统
  行为，应重新分类为 Task / Bug，或拆分新的 implementation leaf，不得扩大 Documentation
  范围。
- 行为型 `.md`、policy、Agent instruction 或其它文件不会仅因文件扩展名而成为
  Documentation；类型由工作是否改变文档事实与系统行为决定。

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

- Context 说明结论的具体消费者/待决事项；已有需求或验证项时简短引用来源。
- Scope 固定适用系统/版本、数据/方案/时间边界，以及与问题有关的有限样本、请求、
  试验或资源预算；不要求填全部维度，也不统一规定时长。
- Evidence / Evaluation Criteria 事先定义证据充分性、预算耗尽/来源不可得时的停止
  条件，并区分 fixture、实际来源与适用运行环境的证据。
- Expected Outcome / Artifact 定义有限产物；`NEEDS MORE EVIDENCE` 应交付已知事实、
  限制和有界 follow-up 建议。是否满足本次 Research AC 仍按原合同判断，不能仅用
  “证据不足”自动宣告完成，也不能自动延长研究或创建实施 Task。

### 批准基线、有限工作分解与变更

以下是 authoring 语义，适用于 UI、API 和 Agent 编写的 Issue；不新增表单必填章节、
机器解析字段、Project 状态或 LCK gate。填写提示本身不代替当前 Issue 规格。

**基线引用。** 产品需求、总体设计和阶段计划须经批准并有可访问的明确版本/章节，
才能作为工作分解依据。草稿、示例 REQ/DP、阶段编号或本地评审文件的存在不自动
改变当前 Issue、架构权威或运行授权。引用只提供必要语义，不复制设计全文，
也不将设计中的阶段编号写成第二份 Project metadata。Bug 的明确缺陷证据和
Documentation 的事实来源仍是各自合同，不强迫它们绑定产品 DP。

**一份有限分解。** 在 Feature 实施前，确定全部交付义务和一份规范的工作分解记录；
可放在获准的规划文档或明确的规划正文位置，通过 Feature 引用。已有记录应复用，
不得新建竞争清单。该记录逐项说明：

| 内容 | 必须明确 |
|---|---|
| 来源与成果 | 对应的批准要求、Feature AC、适用 DP/部分及可观察交付物 |
| 已有与剩余 | 当前实现证据、复用部分和全部剩余成果；已完成工作不重新分配 |
| 输入 | 必要合同、消费者及影响实施的未知项；正式 blocker 仍以原生关系表达 |
| 验收与边界 | 适用行为/失败/集成结果、证据类型、排除项和停止条件 |
| 叶项分解 | 有限候选叶项及其承担部分；创建时间可后移，交付义务不因此开放增长 |

有限成果说明 WHAT；代码结构、实现步骤和实际叶项合同仍在相应执行位置定义。
叶项引用用于定位，不复制 Parent、blocked-by、Project Status 或关闭状态的真相。
部分成果可提前提供合同/代码，整体集成验收可后置，但必须分别说明边界；不能把
提前可消费的部分当作整个 Feature 已完成，也不能绕过当前原生 blocker。

**变化处理。** 创建或扩展叶项前，将变化归入下表；未解决的范围冲突只阻止依赖它的
工作，其他已获准且独立的工作可以继续。

| 变化 | 处理 |
|---|---|
| 原义务纯拆细 | 保持输入/输出、AC、排除项与总义务不变，更新唯一分解记录；不将其包装为新能力 |
| 既有要求未满足 | 记录具体合同、复现和修复验收，按既有 Bug/remediation 路径处理；不因没有 DP 编号拒绝必要修复 |
| 必要合同/安全前置遗漏 | 说明来源、最小修订、消费者与剩余工作影响；经明确决定后更新基线，不静默塞入下一批 |
| 新能力或新增完成标准 | 先提出范围变更及取舍/依赖影响，经维护者批准再纳入；“技术上有用”不足以成为范围依据 |
| 迁移、后置或取消既有义务 | 明确旧义务、承接位置或处置、理由及影响；批准后同步规格与必要原生关系，不能伪造完成 |

不以固定 Task 个数压住必要正确性/安全修复，也不以一个宽泛 REQ 容纳无限新义务。
连续发现新前置时，停止受影响部分的滚动创建并复核完整分解。范围变更只记录一次
清楚的决定及必要引用，不新增审批平台、完整历史台账或逐 Task 的通用流程副本。
仍须遵守已有实施、独立 Review、显式 remediation、人工 merge、closeout 和
Feature completion 边界；获准创建/修改规格不自动授予其中其他操作权限。

## 4. Acceptance Criteria authoring

AC 必须描述**可以观察、测试或明确判断的完成事实**。允许简洁 checklist、
Given/When/Then 或等价 behavioral statements；不强制 Gherkin。

禁止：

- generic Definition of Done（`pytest passes`、`Ruff passes`、`mypy passes`、
  `CI green`、`PR created`）——由 repository workflow / CI 负责；
- 把 implementation plan 当 AC（除非具体结构本身就是 contract）。

优先描述：externally observable behavior、invariant、failure behavior、
state transition、regression condition。

Task 的 Critical Outcome 只验证指定 supported path，不替代其余 AC。
Feature 的最终集成结果不能由独立子项各自 PASS 推断；适用证据达到当前完成标准后，
停止追加无来源开发工作并使用既有完成审计，不引入第二套 Outcome Check 完成状态。

## 5. Non-goals 规则

| Type | Non-goals |
|---|---|
| Epic | REQUIRED |
| Feature | REQUIRED |
| Task | OPTIONAL（存在明显 scope-creep 风险时填写） |
| Bug | OPTIONAL |
| Documentation | OPTIONAL（仅在确有必要时通过 Additional documentation context 说明） |
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

标题前缀 `[Epic] / [Feature] / [Task] / [Bug] / [Documentation] / [Research]` 和 `type:*` labels
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
3. Current leaf Issue body (Task / Bug / Documentation / Research)
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

本节规定保持原义务不变的结构迁移。采用新的产品/设计基线并改变范围、AC 或依赖，
属于前述显式范围变更，不能伪装成格式规范化；模板更新不授权批量改写既有 Issue。
对已有实施历史的 Feature，必须明确批准受影响的剩余规格调整及历史证据保留方式，
不能通过自动迁移重写已完成工作的原合同。

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
- `.github/ISSUE_TEMPLATE/*.yml` — 六类 Issue Form 的字段定义（本文档的机器可执行
  对应物）。
- `docs/architecture/*` — 稳定架构不变量。
- `docs/workflows/lck/*` — LCK 工作流契约（本文件即其一）。
