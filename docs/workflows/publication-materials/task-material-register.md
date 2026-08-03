# Task 产出材料登记清单

> 用途：记录每个 Task 为《代理开发工作流设计指导手册》和《代理工作流 Token 优化技术分享文章》
> 产生了哪些可引用材料、材料保存在哪里、能否公开，以及还需要完成哪些归档动作。
>
> 本清单只登记与最终文档、实验复核和工作流设计有关的材料，不枚举每个 Task 的全部业务代码文件。

## 登记规则

- 每个 Task 在 Delivery、独立 Review 和 Closeout 后分别更新一次。
- 仓库内材料使用仓库相对路径；仓库外材料使用稳定逻辑名称、Run ID 和 SHA-256。
- 原始 rollout、`.agents/evidence.local/`、凭据和本机配置不得因为登记而提交到仓库。
- `正式样本`、`历史样本`、`无效样本`和`环境证据`必须分开标记。
- Issue、PR、HEAD 或 Review 身份尚未固定时写 `待回填`，不得猜测。
- 同一材料由多个 Task 使用时，`produced_by` 只记录首次产生它的 Task，后续 Task 记录为
  `consumed_or_extended`。
- 最终文档引用材料前，必须确认来源、统计口径、脱敏状态和适用边界。

## 项目与 Issue 层级

- Epic：`#61 [Epic] Agent 开发工作流与工程效能治理`
- Feature：`#62 [Feature] 建立 Task Workflow Token 基准并完成第一轮优化`
- Feature：`#77 [Feature] 迁移 WSL2 执行环境并降低 Guardian 与命令调用开销`
  - `#80` 建立第二轮 Task Workflow Token 实验协议并冻结 Task #65 基准
  - `#81` 采集 Task #65 当前 Windows Task Workflow merge-pre 基准
  - `#82` 建立可复现的 WSL2 Codex 开发环境与执行能力诊断
  - `#83` 建立 WSL2 Task Workflow Validation Runner 与最小权限 Rules
  - `#84` 建立只读 GitHub Evidence Runner 与 Git 操作审批边界
  - `#85` 将 Task Workflow Skills 切换到统一 Runner 并移除重复命令路径
  - `#86` 采集 Task #65 WSL2 优化候选 merge-pre 基准并评估效果
- Feature：`#78 [Feature] 重构 Task Workflow 规格上下文与独立审查`
  - `#87` 审计 Task Workflow 规格与治理上下文重复并定义 Canonical Spec
  - `#88` 实现 Task Workflow 阶段 Context Compiler 与可追溯上下文视图
  - `#89` 定义独立 Review 事实继承契约与审查失效规则
  - `#90` 建立 Task Workflow 固定 Patch Review 对照实验框架
  - `#91` 基于 Task #65 完成 Issue、Context Compiler 与 Review 输入对照实验
  - `#92` 审计 Task Workflow 质量门禁并定义风险自适应 Profiles
- Feature：`#79 [Feature] 状态机化 Task Closeout 并建立连续执行与恢复能力`

## 状态说明

| 状态 | 含义 |
| --- | --- |
| `repository-final` | 已进入仓库并可由 commit/PR 追踪 |
| `external-final` | 仓库外证据完整，已有 SHA-256 和稳定身份 |
| `external-pending-manifest` | 原始材料存在，但还需生成或登记 SHA-256 manifest |
| `historical-sample` | 可用于历史画像，不能直接充当本轮因果对照 |
| `formal-sample` | 满足对应协议，可用于正式比较 |
| `invalid-sample` | 保留为失败案例，不得作为正式指标样本 |
| `delivery-pending` | 内容已产生，但 Delivery/Review/合并身份尚未冻结 |
| `placeholder` | 为后续 Task 预留，尚无产物 |

## 总览

| Task | Issue / PR | Parent | 主要材料角色 | 当前状态 | 两份最终文档用途 |
| --- | --- | --- | --- | --- | --- |
| Task #63：基础配置管理与环境变量加载 | Issue #63 / PR #67 | Feature #2 | 历史完整工作流 Token 与流程样本 | `historical-sample`、已完成 | Token 历史基线；Delivery/Review/Closeout 案例 |
| Task #64：结构化日志与敏感信息保护 | Issue #64 / PR #71 | Feature #2 | 历史完整工作流 Token 与安全实现样本 | `historical-sample`、已完成 | Token 历史基线；敏感信息与 Review 案例 |
| Task #80：第二轮实验协议与 Task #65 输入冻结 | Issue #80 / PR：待回填 | Feature #77 / Epic #61 | 协议、冻结输入、统计口径、材料 Schema | `repository-final`、Issue 已完成 | 两份文档的实验方法和权威输入 |
| Task #81：Windows merge-pre 基准采集 | Issue #81；业务 Issue #65；实验 PR #99 | Feature #77 / Epic #61 | 当前 Windows 正式基准、失败 pilot、rollout | `formal-sample` + `invalid-sample`、Issue 已完成 | 当前基准、失败恢复、Token/命令/Guardian 数据 |
| Task #82：可复现 WSL2 Codex 环境与能力诊断 | Issue #82 / PR #100 / reviewed head `96b20b5...` / merge `767e995...` | Feature #77 / Epic #61 | 环境指南、诊断工具、能力矩阵、决策与案例 | `repository-final`、已完成 | 指导手册环境章节；Token 文章候选变量与边界 |
| Task #83：WSL2 Validation Runner 与最小权限 Rules | Issue #83 / PR #101 / reviewed head `d162bc9...` / merge `74a7587...` | Feature #77 / Epic #61 | Runner、Rules、权限与命令成本材料 | `repository-final`、已完成 | 后续优化机制和控制面证据 |
| Task #84：只读 GitHub Evidence Runner 与 Git 审批边界 | Issue #84 / PR #102 / final PR head `61a2f0d...` / merge `e1c3b58...` | Feature #77 / Epic #61 | GitHub 只读证据、Git 写边界与审批材料 | `repository-final`、已完成 | 证据采集、安全边界和命令成本 |
| Task #85：Skills 切换至统一 Runner | Issue #85；PR：待创建；Base `e1c3b58...` | Feature #77 / Epic #61 | 统一命令路径、重复移除与 Skill 迁移材料 | `delivery-pending` | 工作流收敛机制和 Token 优化来源 |
| Task #86：Task #65 WSL2 candidate merge-pre | Issue #86；业务 Issue #65；实验 PR：待创建 | Feature #77 / Epic #61 | Candidate Token、质量、Guardian、命令与时长 | `placeholder` | 最终 Windows/WSL2 对照和优化结论 |
| Task #87：Canonical Spec 审计 | Issue #87；PR：待创建 | Feature #78 / Epic #61 | 规格重复、治理来源与 Canonical Spec | `placeholder` | 规格治理和上下文去重 |
| Task #88：阶段 Context Compiler | Issue #88；PR：待创建 | Feature #78 / Epic #61 | 上下文编译、追溯视图与输入体积材料 | `placeholder` | 上下文优化机制和可追溯性 |
| Task #89：独立 Review 事实继承契约 | Issue #89；PR：待创建 | Feature #78 / Epic #61 | Review 事实边界、失效与继承规则 | `placeholder` | 独立审查设计和质量边界 |
| Task #90：固定 Patch Review 对照框架 | Issue #90；PR：待创建 | Feature #78 / Epic #61 | 固定 Patch Review 实验框架 | `placeholder` | Review Token 与质量对照方法 |
| Task #91：Task #65 Context/Review 输入对照实验 | Issue #91；PR：待创建 | Feature #78 / Epic #61 | Issue、Compiler 与 Review 输入对照数据 | `placeholder` | 第二阶段实验结果与因果分析 |
| Task #92：质量门禁与风险自适应 Profiles | Issue #92；PR：待创建 | Feature #78 / Epic #61 | 风险分层、质量门禁和 Profile 材料 | `placeholder` | 质量/成本权衡与适应性策略 |

---

## Task #63 — 实现基础配置管理与环境变量加载

### 身份

- Issue：`#63`
- PR：`#67`
- Parent Feature：`#2 [Feature] 工程与研究基础`
- Workflow main SHA：`158c92140b75f00d35c16f905a7b3eccb05d4403`
- Squash merge commit：`961df2e7ff3b93ec2a7d4d8dba08039788222947`
- 样本类型：`historical-baseline / full workflow`
- 阶段范围：Delivery + Review + Closeout

### 已产生材料

- [x] Task Token Analysis Report v1
  逻辑文件：`task-63-token-analysis-v1.md`
  SHA-256：`d9f0e27da5ca1298e284409dea45a948082e3976b2700aa18d4a4bf1129ceee5`
- [x] Delivery root rollout
  `rollout-2026-07-26T14-08-10-019f9d09-c38b-71e2-a7f3-5c99940c4e2e.jsonl`
  SHA-256：`d5453dccef722a6a47417cca6fc6da776eaab342c555c75abc3809f2b998a7e5`
- [x] Delivery Guardian rollout
  `rollout-2026-07-26T14-08-36-019f9d0a-297a-7111-b74a-422125c93d7c.jsonl`
  SHA-256：`7ec6b500f21482f0e1562d6740264e5ad6e229089561bba73ffc693d6f2fd781`
- [x] Review/Closeout root rollout
  `rollout-2026-07-26T14-27-01-019f9d1b-063b-7d21-8ac8-615e61948ac9.jsonl`
  SHA-256：`f2fc85483d03caa6b04610cbc1f74a9137cfac5c8dab354184fdd1fc78c31d2a`
- [x] Review/Closeout Guardian rollout
  `rollout-2026-07-26T14-28-14-019f9d1c-21ee-7460-90e3-c89ddc92a2e7.jsonl`
  SHA-256：`129232dbc15ae69685e78ecba38fa0f364e45a7cf776461b632a19a718b8a9c8`
- [x] 正式历史指标
  merge-pre：`5,933,126` Tokens；full workflow：`8,692,744` Tokens。
- [x] 质量守门信息
  Validation passed；独立 Review 通过；无 Medium-or-higher finding；无 Review invalidation。
- [x] 异常材料
  旧 Telemetry closeout 的 `workflow_main_sha` 冲突，可作为“流程完成但旧遥测收尾失败”的恢复案例。

### 最终文档用途

- 指导手册：完整 Task 生命周期、阶段边界、Closeout 异常与人工核验案例。
- Token 文章：历史完整流程画像；只能按共同阶段重算后与 merge-pre 口径比较。

### 待办

- [ ] 将报告和四份 rollout 纳入统一仓库外 evidence manifest。
- [ ] 为最终文章生成统一的 phase/actor/process CSV，不直接引用人工抄录数字。
- [ ] 在最终文档中明确其环境、workflow 和 Telemetry 与 Task #65 第二轮不同。

---

## Task #64 — 实现结构化日志与敏感信息保护

### 身份

- Issue：`#64`
- PR：`#71`
- Parent Feature：`#2 [Feature] 工程与研究基础`
- Workflow main SHA：`331df18e23f4a2c8677021e04205f61337427746`
- Squash merge commit：`7daf3e9d6f224387a84ba17b12b90bc10b8145fc`
- 样本类型：`historical-baseline / full workflow`
- 阶段范围：Delivery + Review + Closeout

### 已产生材料

- [x] Task Token Analysis Report v1
  逻辑文件：`task-64-token-analysis-v1.md`
  SHA-256：`6a7511444caaf190cbeb210c585c147b816c0e81b45a3e8cea906cd69b994f9a`
- [x] Delivery root rollout
  `rollout-2026-07-26T17-40-22-019f9dcc-07c2-7e10-9593-0df7d67c52b4.jsonl`
  SHA-256：`9378990201ffc3e9308247c57411adfacd5c5c8b866903c5a9bbe18aacab437b`
- [x] Delivery Guardian rollout
  `rollout-2026-07-26T17-41-44-019f9dcd-49c1-7a03-9a5c-3f06fa718317.jsonl`
  SHA-256：`7e4587ead70c3e52a1f44908cfd17a01405f37bfe48c9940e960204402570541`
- [x] Review/Closeout root rollout
  `rollout-2026-07-26T18-00-51-019f9dde-c968-7b92-9a27-76a0caaf148d.jsonl`
  SHA-256：`eea12d05d2802fc3dba2f21e11f5b2571a72d574164fe4091595ad29ad455943`
- [x] Review/Closeout Guardian rollout
  `rollout-2026-07-26T18-02-18-019f9de0-1d2b-74c3-82be-bfba6d9eb8be.jsonl`
  SHA-256：`9c825f7439aeb46a2a7b65f4b1c0be4f164d7338c458387ff96854b4f8db44dc`
- [x] 正式历史指标
  merge-pre：`8,516,786` Tokens；full workflow：`11,969,449` Tokens。
- [x] 质量守门信息
  Validation passed；独立 Review 通过；无 Medium-or-higher finding；无返工。
- [x] 异常材料
  有效 Review 前存在一次 zero-Token aborted turn 和一次 rollback；未计入阶段 Token。

### 最终文档用途

- 指导手册：结构化日志、敏感信息保护和独立 Review 的安全案例。
- Token 文章：第二个历史完整流程样本，用于展示 Task 复杂度和阶段成本差异。

### 待办

- [ ] 将报告和四份 rollout 纳入统一仓库外 evidence manifest。
- [ ] 将敏感信息保护的业务实现证据与 workflow 过程证据分开引用。
- [ ] 保留 zero-Token aborted/rollback 的口径说明，避免误算为正式阶段。

---

## Task #80 — 建立第二轮 Task Workflow Token 实验协议并冻结 Task #65 基准

### 身份

- Task Issue：`#80`
- Parent Feature：`#77`
- Epic：`#61`
- PR：`待回填`
- Protocol ID：`task-65-round-2-v1`，后续 amendment 必须保留版本历史。
- 产物状态：已进入仓库；GitHub Delivery/Review 身份待在本清单补齐。

### 已产生的仓库材料

- [x] `docs/workflows/benchmarks/task-65-round-2/README.md`
- [x] `docs/workflows/benchmarks/task-65-round-2/protocol.md`
- [x] `docs/workflows/benchmarks/task-65-round-2/benchmark-manifest.json`
- [x] `docs/workflows/benchmarks/task-65-round-2/task-65-original.md`
- [x] `docs/workflows/benchmarks/task-65-round-2/task-65-frozen.md`
- [x] `docs/workflows/benchmarks/task-65-round-2/task-65-telemetry-only.diff`
- [x] `docs/workflows/benchmarks/task-65-round-2/environment-current-windows.md`
- [x] `docs/workflows/benchmarks/task-65-round-2/materials/experiment-record.example.json`
- [x] `docs/workflows/benchmarks/task-65-round-2/materials/publication-materials.example.json`

### 已产生的仓库外交付材料

- [x] 协议冻结完整包
  `quant-system-task65-protocol-freeze.zip`
  SHA-256：`49b761047136e76ab73cb6e4e063d36add60adf08abca987fe845faaf8e3651e`
- [x] Git Patch
  `task65-protocol-freeze.patch`
  SHA-256：`f47b638c281de107d4f885e1dd3c35b6fd2813d470f55ef02087e70d3bfdacc4`

### 最终文档用途

- 指导手册：冻结输入、权威来源、阶段边界、drift 和中止规则。
- Token 文章：实验变量、控制变量、统计公式、质量门槛和可比性等级。

### 待办

- [ ] 回填 PR、Delivery HEAD、reviewed HEAD 和 Review verdict。
- [ ] Candidate 前记录 business base、candidate control-plane SHA 和 composite base amendment。
- [ ] 任何协议更新都创建新版本，不覆盖本版 manifest。

---

## Task #81 — 采集 Task #65 当前 Windows Task Workflow merge-pre 基准

### 身份

- Task Issue：`#81`
- Parent Feature：`#77`
- Epic：`#61`
- 关联业务 Issue：`#65`
- 正式实验 PR：`#99`，Draft/Open，`Refs #65`
- Formal Run ID：`task65-current-windows-20260801T110435Z`
- Branch：`experiment/task65-current-windows`
- Business/base SHA：`a492f0b334f950f2613b4b2204e96bef413355be`
- Final head：`a4168d063c8e8d39dd90717bab195e522cc3fcbf`
- Final tree：`b6b3d9d99ac90d0e35a73d2f3fb3be418d67aff7`
- Effective diff SHA-256：`69991b6424aa1ae7b7a51dd702722c13d193f6130f459d2ecc51856b259000f2`
- Review verdict：`pass`
- 生命周期边界：未 Merge、未 Closeout、未关闭 Issue #65。

### 正式样本材料

- [x] 六份完整 rollout：Preparation root/Guardian、Delivery root/Guardian、Review root/Guardian。
- [x] 完整 rollout 包
  逻辑文件：`rollouts(1).zip`
  SHA-256：`fc6dd9843b6cf0af9b5f98aff8b71913c9d135c258b3933431d781d92413190d`
- [x] Formal merge-pre：`8,359,801` Tokens。
- [x] Preparation：`505,328` Tokens，单独记录，不计入 merge-pre。
- [x] Delivery：`6,380,740` Tokens。
- [x] Review：`1,979,061` Tokens。
- [x] CI-equivalent validation：`152 passed`，lock、Ruff、format、Mypy、diff 和 workflow validators 通过。
- [x] Review findings：无。
- [x] GitHub 状态与清理：PR #99 Draft/Open，无 closing linkage，无 Merge/Closeout。

### 无效 pilot 材料

- [x] Pilot 归档
  `task65-current-windows-run-20260801.zip`
  SHA-256：`7c771a003bab75c2a7b959f667c70c69b03a4a434d78e72b328f5670b22ff7c2`
- [x] Pilot report、acceptance matrix、protocol deviations、claim/evidence map、failure timeline、
  metrics、process/token CSV 和 Mermaid 图源。
- [x] 分类：`invalid-sample / incomparable`。只能作为协议漂移和失败恢复案例，不得替代正式 PR #99 样本。

### 最终文档用途

- 指导手册：实验准备、隔离、Draft PR、非关闭引用、独立 Review 和失败 pilot 处理。
- Token 文章：当前 Windows merge-pre 正式基准；prompt/环境成本和失败案例。

### 待办

- [ ] 为正式 Run 生成统一 `record.json`、metrics/process/token CSV、claim/evidence map 和报告包。
- [ ] 将六份 rollout 的单文件 SHA、session/parent、actor 和时间范围写入统一 evidence manifest。
- [ ] 回填归档目录的稳定位置。
- [ ] PR #99 在 Candidate 冻结和维护者决策前保持未合并。

---

## Task #82 — 建立可复现的 WSL2 Codex 开发环境与执行能力诊断

### 身份

- Task Issue：`#82`
- Parent Feature：`#77`
- Epic：`#61`
- PR：`#100`
- Branch：`82-task-reproducible-wsl2-codex-environment`
- Base SHA：`a492f0b334f950f2613b4b2204e96bef413355be`
- final Delivery HEAD：`96b20b5505e5c0db053933792e67e298212c8cf9`
- reviewed HEAD / verdict：`96b20b5505e5c0db053933792e67e298212c8cf9` / `pass`
- squash merge commit：`767e995e7872c5eaea46002cf02381cef87f3eab`
- Issue / Project：Issue closed；Project Status `Done`
- Branch cleanup：completed
- Post-merge validation：`112 passed` and quality success
- 当前状态：Task #82 已完成，最终身份由 Task #83 回填。

### 已产生的仓库材料

- [x] `.python-version`
- [x] `tools/wsl2_codex_diagnostic.py`
- [x] `docs/workflows/wsl2-codex-environment/README.md`
- [x] `docs/workflows/wsl2-codex-environment/current-diagnostic.json`
- [x] `docs/workflows/wsl2-codex-environment/capability-matrix.md`
- [x] `docs/workflows/wsl2-codex-environment/runner-environment-contract.md`
- [x] `docs/workflows/wsl2-codex-environment/article-materials.json`
- [x] `docs/workflows/wsl2-codex-environment/publication-materials.json`
- [x] `docs/workflows/wsl2-codex-environment/evidence-index.json`
- [x] `docs/workflows/wsl2-codex-environment/external-evidence-manifest.json`
- [x] `docs/workflows/wsl2-codex-environment/publication-readiness.md`
- [x] `docs/workflows/wsl2-codex-environment/decisions-and-cases.md`
- [x] `docs/workflows/wsl2-codex-environment/external-evidence-manifest.example.json`
- [x] `docs/workflows/wsl2-codex-environment/visuals/`
- [x] 本 Task 产出的全局登记文件：`docs/workflows/publication-materials/task-material-register.md`

### 已产生的仓库外环境 evidence

- [x] Initial diagnostic
  Run ID：`20260801T133921Z-66016f44`
- [x] Authentication/fetch/formal-write follow-up
  Run ID：`20260801T145709Z-followup`
- [x] GitHub reversible-write/proxy final gaps
  Run ID：`20260801T152359Z-final-gaps`
- [x] 三轮 JSON/JSONL parse checks 均通过；原始材料保留在 `.agents/evidence.local/`，不得提交。
- [x] committed manifest：`docs/workflows/wsl2-codex-environment/external-evidence-manifest.json`
  repository inclusion status：`committed-manifest-only`

### 仓库外交付与材料补充包

- [x] `quant-system-wsl2-environment-delivery.zip`
  SHA-256：`47b62d8b00abe18bfb0b7ce4ecbc5c8efbe07f825305ab1e6247a2189a62ab50`
- [x] `task-wsl2-codex-environment.patch`
  SHA-256：`27874c0bd7d22bd9659c1afa5b020bf2593a48c0d08ac92b3dd6cb80dab7982e`
- [x] `task-wsl2-codex-environment-manifest.json`
  SHA-256：`21547c5efae4b8eefc6271312309a7471e76308fee6f9bf1de473d70f04abd3c`
- [x] `wsl2-publication-materials-supplement.zip`
  SHA-256：`e1823018b86599f17e124eaa45cd004a0773715ad0df0f74e60bc033c39ba2c7`
- [x] `wsl2-publication-materials-supplement.patch`
  SHA-256：`99e8eb58af992834ce84001f4c8fe10a69b80a244b4ee5be0a70a34d557dae08`

这些交付包用于传递变更，不替代最终 Git commit、PR、Review 和仓库外原始 evidence。

### 最终文档用途

- 指导手册：WSL2 复现、Python/uv/Git/gh、代理、诊断、approval、回滚和故障排查。
- Token 文章：候选环境变量、Windows/WSL2 能力差异、反直觉案例和研究边界。
- 本 Task **不能**提供 Token 降幅或 Candidate 优于 Windows 的结论。

### 生命周期来源

- [x] Issue、Parent、Epic、PR、Branch 和 Base SHA 已稳定记录。
- [x] 三轮 `.agents/evidence.local/` 已生成可提交 SHA-256 manifest，未提交原始 evidence。
- [x] 本清单、`publication-materials.json`、`evidence-index.json` 和 `publication-readiness.md` 的身份模型一致。
- [x] independent Review evidence 记录 reviewed HEAD `96b20b5505e5c0db053933792e67e298212c8cf9`、Review verdict `pass`、findings 和验证结果。
- [x] Merge/Closeout evidence 记录 squash merge commit `767e995e7872c5eaea46002cf02381cef87f3eab`、最终 Issue closed、Project Status `Done`、branch cleanup completed 和 post-merge validation `112 passed`。
- [x] Task #82 最终身份由 Task #83 回填；未修改 Task #82 已审查 HEAD。

---

## Task #83 — 建立 WSL2 Task Workflow Validation Runner 与最小权限 Rules

### 身份

- Task Issue：`#83`
- PR：`#101`
- Parent Feature：`#77`
- Epic：`#61`
- Branch：`83-task-wsl2-validation-runner-rules`
- Base / workflow / control-plane SHA：`767e995e7872c5eaea46002cf02381cef87f3eab`
- final Delivery / reviewed HEAD：`d162bc9f2846854c3f4bf0dcc6a938102f850d14`
- Review verdict：`pass`
- squash merge commit：`74a75872078221c38dbd132a1d438b0bb05c1870`
- Issue / Project：Issue closed；Project Status `Done`
- Branch cleanup：completed
- Post-merge validation：`148 passed`；remote `quality: SUCCESS`
- Closeout evidence：plan `ev-937d4dbc6ce95774`；validation `val-closeout-3170e0980aa4`；final `ev-902fd5acf87a567f`
- 生命周期状态：Task #83 已完成；Task #65 candidate 未执行。

### 已产生材料

- [x] 仓库内实现和文档：
  `tools/agent_workflow/wsl2_validation_runner.py`；
  `tools/agent_workflow/wsl2_validation_profiles.json`；
  `.codex/rules/quant-system-wsl-validation.rules`；
  `docs/workflows/wsl2-validation-runner/README.md`；
  `docs/workflows/wsl2-validation-runner/publication-materials.json`；
  `docs/workflows/wsl2-validation-runner/live-activation-evidence.json`；
  `docs/workflows/wsl2-validation-runner/current-ci-equivalent-evidence.json`；
  `docs/workflows/wsl2-validation-runner/security-hardening-cases.md`；
  `docs/workflows/wsl2-validation-runner/maintenance-and-adoption.md`；
  `docs/workflows/wsl2-validation-runner/visuals/`；
  `tests/tools/test_wsl2_validation_runner.py`；
  `tests/tools/test_wsl2_validation_rules.py`。
- [x] 参考 rules 输入：
  `/tmp/quant-system-wsl.rules`，SHA-256
  `0a23cf86db6a04aaaea8fda5f82303f42eb962fd828d83c4d05ca01b011b25ab`。
  Reused：固定 runner 规则应保持最小权限、禁止 `gh auth token`、Git/GitHub 写操作继续审批。
  Excluded：不复制其宽泛只读 GitHub evidence 规则到本 Task 的 validation runner rules。
- [x] Token/process/quality 指标：
  `docs/workflows/wsl2-validation-runner/publication-materials.json`
  按 `observed`、`derived`、`expected`、`not-measured` 区分。
- [x] Rules live activation probe：
  `docs/workflows/wsl2-validation-runner/live-activation-evidence.json`；
  profile `targeted`；direct execution yes；Guardian turns `0`；approval prompts
  `0`；elevated executions `0`；duration `10284 ms`；stdout `346 bytes`；
  result SHA-256
  `1e0cc6cd7850457dd83b9be58e0da48a20c99f0cfd3e349f426fc9c3616a8caa`。
  Raw result remains ignored under `.agents/validation.local/`.
  该观测使用 Runner `1.0.0`；最终候选为 `1.0.1`，Rules 文件未变化。
  Live probe 支持 Rules activation / direct routing 结论，`1.0.1`
  完整性与进程树语义由自动化测试支持。
- [x] 决策、失败、恢复和反直觉案例：
  runner tests cover subcommand failure, process-group timeout/interruption cleanup,
  trusted Runner/spec/Rules replacement rejection, non-canonical command rejection,
  trailing argv rejection before validation execution, CI drift, result-write
  failure, wrong cwd and symlink invocation. Real execpolicy tests cover allowed
  runner profile prefixes, negative interpreter/tool/shell/Git/GitHub cases, and
  the prefix boundary where `targeted tests/tools` and `targeted arbitrary-value`
  return `allow` while the runner rejects them.
- [x] 可编辑图表源：
  `docs/workflows/wsl2-validation-runner/visuals/validation-boundary.mmd`
  records Codex prefix policy decision, runner full argv validation, and
  canonical validation command boundaries.

### 最终文档用途

- 指导手册：固定入口、profile 选择、rules 激活 checkpoint、失败诊断和回滚。
- Token 文章：命令收敛机制、局部输出体积、失败案例和未测量 Guardian 边界。
- 本 Task **不能**提供 Token 降幅、Task #65 Candidate 优于 Windows 或质量无回退的结论。

### 待办

- [x] final Delivery / reviewed HEAD、Review verdict、merge commit、Issue/Project、branch cleanup 和 post-merge validation 已由 Task #84 回填。
- [x] 新 Codex 会话加载 rules 后，仓库外记录 live Guardian、approval、输出大小和时长。
- [x] `baseRefOid` 环境兼容阻塞通过升级 WSL2 GitHub CLI 消除；Task #84 继续负责固定 Evidence Runner、partial/unknown 和审批边界。

---

## Task #84 — 建立只读 GitHub Evidence Runner 与 Git 操作审批边界

### 身份

- Task Issue：`#84`
- PR：`#102`
- Parent Feature：`#77`
- Epic：`#61`
- Base / workflow / control-plane SHA：`74a75872078221c38dbd132a1d438b0bb05c1870`
- Live material capture HEAD：`3814af3d95ece48ef03c59536dd025f5fb5511fb`
- Final PR head：`61a2f0d085e411bf5ed614f0d8703a9c0f122fa2`
- reviewed HEAD / verdict：独立 Review evidence 未包含在本次 Task #85 输入中，保持外部生命周期事实
- Squash merge commit：`e1c3b587a5fa1a61217fb9160015472bc0e36154`
- 生命周期状态：Issue `CLOSED`；Project `Done`；branch cleanup completed；Task #65 candidate 未执行。

### 已产生材料

- [x] 固定 Runner 与 profiles：
  `tools/agent_workflow/wsl2_github_evidence_runner.py`；
  `tools/agent_workflow/wsl2_github_evidence_profiles.json`。
- [x] 最小权限 Rules：
  `.codex/rules/quant-system-wsl-evidence.rules`。
- [x] Evidence read-only mode：
  `tools/agent_workflow/workflow_evidence.py` 支持固定 Runner 使用的
  `WORKFLOW_EVIDENCE_READ_ONLY=1`，跳过 `git fetch` 并返回 `local_main_sha`。
- [x] 单元、集成、负向和 Rules 测试：
  `tests/tools/test_wsl2_github_evidence_runner.py`；
  `tests/tools/test_wsl2_github_evidence_rules.py`。
- [x] 使用、安全、凭据、回滚和故障排查：
  `docs/workflows/wsl2-github-evidence-runner/README.md`；
  `docs/workflows/wsl2-github-evidence-runner/git-approval-boundary.md`；
  `docs/workflows/wsl2-github-evidence-runner/security-and-troubleshooting.md`。
- [x] 指导手册与 Token 文章材料：
  `docs/workflows/wsl2-github-evidence-runner/publication-materials.json`；
  `docs/workflows/wsl2-github-evidence-runner/historical-command-baseline.json`；
  `docs/workflows/wsl2-github-evidence-runner/environment-capability.json`；
  `docs/workflows/wsl2-github-evidence-runner/live-evidence-capture-plan.md`；
  `docs/workflows/wsl2-github-evidence-runner/publication-readiness.md`；
  `docs/workflows/wsl2-github-evidence-runner/templates/`；
  `docs/workflows/wsl2-github-evidence-runner/visuals/`。
- [x] Live profile、recheck、execpolicy 和外部 manifest 摘要：
  `docs/workflows/wsl2-github-evidence-runner/live-profile-evidence.json`；
  `docs/workflows/wsl2-github-evidence-runner/live-recheck-evidence.json`；
  `docs/workflows/wsl2-github-evidence-runner/rules-live-evidence.json`；
  `docs/workflows/wsl2-github-evidence-runner/external-live-evidence-manifest.json`。

### 当前证据边界

- [x] 测试覆盖固定 profiles、Task/PR/schema、partial/unknown、linkage fail、
  remote-ref drift、snapshot recheck、truncation、敏感信息、错误 origin、
  symlink、路径含空格、并发输出隔离和无 GitHub 写操作。
- [x] Rules 测试覆盖固定入口、直接 `gh`/Git/shell 不放行、Git/GitHub 写
  继续审批，以及 prefix allow + Runner complete-argv reject 的组合边界。
- [x] Task #63/#64 外部分析报告中的历史 Git/`gh`、Guardian 与 Token
  聚合数据已以摘要和 source SHA 归档；原始 rollout/report 不提交。
- [x] Live profile、snapshot recheck、execpolicy 和环境能力的版本化采集
  契约、JSON 模板与 readiness matrix 已归档。
- [x] 创建 PR 并提交 trusted files 后，对真实 Task #84 / PR 执行 live profile，
  记录 API 调用数、stdout、时长、Guardian、approval 和 result SHA-256。
- [x] 使用同一 snapshot 执行 live recheck，并记录真实
  execpolicy matrix 和 Runner 尾随参数拒绝。
- [ ] 外部 Token 指标继续留给 rollout 分析和 Task #86，不在本 Task 推导。

### 最终文档用途

- 指导手册：固定只读 evidence 入口、profile/schema、partial/unknown、
  snapshot/recheck、凭据、Git/GitHub 审批与回滚。
- Token 文章：模型可见命令收敛、内部 API operation accounting、紧凑输出、
  失败案例与未测量 Token 边界。

### 待办

- [x] 创建/更新 PR 后回填 PR、live material capture HEAD 和 effective diff identity。
- [ ] reviewed HEAD、verdict 和 findings 继续由独立 Review lifecycle evidence 回填。
- [x] Merge/Closeout：merge `e1c3b587...`；Issue `CLOSED`；Project `Done`；branch cleanup completed；post-merge `218 passed, 1 skipped`，`quality` pass。
- [x] Task #85 已建立静态前后命令路径、Skill 行数、移除清单和统一 authority map；运行时 Candidate 指标仍留给 #86。

---

## Task #85 — 将 Task Workflow Skills 切换到统一 Runner 并移除重复命令路径

### 身份

- Task Issue：`#85`
- Parent Feature：`#77`
- Epic：`#61`
- Base / workflow / control-plane SHA：`e1c3b587a5fa1a61217fb9160015472bc0e36154`
- PR / Delivery HEAD / reviewed HEAD / merge：待生命周期产生。
- Task #65 Candidate：未执行。

### 已产生材料

- [x] 更新 Task Skills：
  `.agents/skills/task-delivery/SKILL.md`；
  `.agents/skills/task-pr-review/SKILL.md`；
  `.agents/skills/task-closeout/SKILL.md`。
- [x] 统一 Runner 与 trusted front-door：
  `tools/agent_workflow/wsl2_validation_runner.py`；
  `tools/agent_workflow/wsl2_github_evidence_runner.py`；
  `tools/agent_workflow/trusted_runner.py`；
  对应 profiles 和最小权限 Rules。
- [x] 去重与契约测试：
  `tools/agent_workflow/skill_path_audit.py`；
  `tests/tools/test_workflow_skills.py`；
  Runner、Rules、trusted bundle 和 Skill validator 测试。
- [x] 使用、回滚和材料：
  `docs/workflows/task-skill-runner-migration/README.md`；
  `docs/workflows/task-skill-runner-migration/removed-legacy-paths.md`；
  `docs/workflows/task-skill-runner-migration/rollback-and-compatibility.md`；
  `docs/workflows/task-skill-runner-migration/before-after-command-paths.json`；
  `docs/workflows/task-skill-runner-migration/publication-materials.json`；
  `docs/workflows/task-skill-runner-migration/live-evidence-capture-plan.md`；
  `docs/workflows/task-skill-runner-migration/publication-readiness.md`；
  `docs/workflows/task-skill-runner-migration/templates/`；
  `docs/workflows/task-skill-runner-migration/examples/`；
  `docs/workflows/task-skill-runner-migration/visuals/`。

### 当前证据边界

- [x] 三个 Task Skills 的静态总行数从 `685` 降为 `547`，减少 `138` 行（`20.15%`）。
- [x] 审计定义的 legacy command fragments 从 `4` 降为 `0`。
- [x] Delivery、Review、Closeout 的 Evidence/Validation 权威来源和 recheck 规则已固定。
- [x] 成功路径禁止同时运行 fixed Runner 和完整 legacy mechanical chain。
- [x] partial/unknown/drift/fail 使用有界展开，不把紧凑 digest 代替失败证据。
- [x] push、GitHub 写、危险 Git、branch deletion 与人工 Merge 边界保持。
- [x] Task #85 自身 Review 的 predecessor-control-plane bootstrap exception 已明确，合并后失效。
- [ ] 运行时 Guardian、Token、端到端模型可见命令和质量对照需由 Task #86 Candidate 实验产生。

### 最终文档用途

- 指导手册：统一 authority map、trusted Review、bounded expansion、回滚与兼容。
- Token 文章：Skill 静态精简、legacy path 去除和统一调用机制；不单独声称 Token 降幅。

### 待办

- [ ] Delivery 后回填 PR、最终 head、effective diff 和 live readiness evidence。
- [ ] 独立 Review 后回填 reviewed head、verdict 和 findings。
- [ ] Merge/Closeout 后由 Task #86 或后续维护回填 merge、Issue/Project 和 branch cleanup。

---

## 已编号的后续 Task

以下 Task 已有正式 Issue 编号，但尚未产生可冻结材料；Task #83 与 #84 已展开为上方完整登记小节。每个 Task 开始 Delivery 后，应把对应条目扩展为完整登记小节。

| Issue | Task | Parent | 当前材料状态 |
| --- | --- | --- | --- |
| `#86` | 采集 Task #65 WSL2 优化候选 merge-pre 基准并评估效果 | Feature `#77` | `placeholder` |
| `#87` | 审计 Task Workflow 规格与治理上下文重复并定义 Canonical Spec | Feature `#78` | `placeholder` |
| `#88` | 实现 Task Workflow 阶段 Context Compiler 与可追溯上下文视图 | Feature `#78` | `placeholder` |
| `#89` | 定义独立 Review 事实继承契约与审查失效规则 | Feature `#78` | `placeholder` |
| `#90` | 建立 Task Workflow 固定 Patch Review 对照实验框架 | Feature `#78` | `placeholder` |
| `#91` | 基于 Task #65 完成 Issue、Context Compiler 与 Review 输入对照实验 | Feature `#78` | `placeholder` |
| `#92` | 审计 Task Workflow 质量门禁并定义风险自适应 Profiles | Feature `#78` | `placeholder` |

---

## 后续 Task 登记模板

复制以下小节，为每个后续优化 Task 新增一项：

```markdown
## Task — <TITLE>

### 身份

- Task Issue：
- PR：
- Parent：
- Base / workflow / control-plane SHA：
- Delivery HEAD：
- reviewed HEAD / verdict：
- merge commit：
- 生命周期状态：

### 已产生材料

- [ ] 仓库内实现和文档：
- [ ] 仓库外 rollout/evidence：
- [ ] Token/process/quality 指标：
- [ ] 决策、失败、恢复和反直觉案例：
- [ ] 可编辑图表源：

### 最终文档用途

- 指导手册：
- Token 文章：

### 待办

- [ ] 生成 SHA-256 manifest。
- [ ] 建立 claim → evidence 映射。
- [ ] 回填 Delivery/Review/Merge/Closeout 身份。
- [ ] 标记可公开、内部或禁止提交。
- [ ] 更新最终文档章节映射。
```

## 全局归档完成条件

- [ ] 每个用于最终文档的 Task 都有登记小节。
- [ ] 每份外部报告和 rollout 集合都有 SHA-256 manifest。
- [ ] 每个正式样本都有 Task、base、control plane、head、reviewed head 和 verdict。
- [ ] 每个无效样本都明确标记不可用于正式指标。
- [ ] 每个公开 claim 都能定位到仓库文件或仓库外 evidence。
- [ ] 两份最终文档引用的统计口径与登记清单一致。
- [ ] 原始 rollout、凭据和 `.agents/evidence.local/` 未进入 Git。
- [ ] Task #65 Candidate 完成后补入 Candidate 与 Windows 正式对照。
