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

| Task | Issue / PR | 主要材料角色 | 当前状态 | 两份最终文档用途 |
| --- | --- | --- | --- | --- |
| Task #63：基础配置管理与环境变量加载 | Issue #63 / PR #67 | 历史完整工作流 Token 与流程样本 | `historical-sample`、已完成 | Token 历史基线；Delivery/Review/Closeout 案例 |
| Task #64：结构化日志与敏感信息保护 | Issue #64 / PR #71 | 历史完整工作流 Token 与安全实现样本 | `historical-sample`、已完成 | Token 历史基线；敏感信息与 Review 案例 |
| Task #65 第二轮实验协议与输入冻结 | Issue / PR：待回填 | 协议、冻结输入、统计口径、材料 Schema | `repository-final` | 两份文档的实验方法和权威输入 |
| Task #65 Windows merge-pre 基准采集 | 元 Task Issue：待回填；业务 Issue #65；实验 PR #99 | 当前 Windows 正式基准、失败 pilot、rollout | `formal-sample` + `invalid-sample` | 当前基准、失败恢复、Token/命令/Guardian 数据 |
| 可复现 WSL2 Codex 环境与能力诊断 | Issue / PR / HEAD：待回填 | 环境指南、诊断工具、能力矩阵、决策与案例 | `delivery-pending` | 指导手册环境章节；Token 文章候选变量与边界 |
| WSL2 Validation Runner 与最小权限 Rules | 待创建或待执行 | Runner、Rules、权限与命令成本材料 | `placeholder` | 后续优化机制和控制面证据 |
| Task #65 WSL2 candidate merge-pre | 业务 Issue #65；实验 PR：待创建 | Candidate Token、质量、Guardian、命令与时长 | `placeholder` | 最终前后对照和优化结论 |

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

## Task #65 第二轮实验协议与输入冻结

### 身份

- Task Issue：`待回填`
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

- [ ] 回填 Task Issue、PR、Delivery HEAD、reviewed HEAD 和 Review verdict。
- [ ] Candidate 前记录 business base、candidate control-plane SHA 和 composite base amendment。
- [ ] 任何协议更新都创建新版本，不覆盖本版 manifest。

---

## Task — 采集 Task #65 当前 Windows Task Workflow merge-pre 基准

### 身份

- 元 Task Issue：`待回填`
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
- [x] 完整 rollout 目录  
  仓库外路径：`Task — 采集 Task #65 当前 Windows Task Workflow merge-pre 基准/rollouts/`  
  内容等同于未压缩的 `rollouts(1).zip`；原压缩包 SHA-256：
  `fc6dd9843b6cf0af9b5f98aff8b71913c9d135c258b3933431d781d92413190d`
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
- [ ] 回填元 Task Issue 和归档目录的稳定位置。
- [ ] PR #99 在 Candidate 冻结和维护者决策前保持未合并。

---

## Task — 建立可复现的 WSL2 Codex 开发环境与执行能力诊断

### 身份

- Task Issue：`待回填`
- PR：`待回填`
- Delivery HEAD：`待回填`
- reviewed HEAD / verdict：`待回填`
- 当前状态：实现和材料已产生，等待正式 Delivery、独立 Review、Merge 与 Closeout。

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

### 待办

- [ ] 回填 Issue、PR、Delivery HEAD、reviewed HEAD 和 Review verdict。
- [ ] 为三轮 `.agents/evidence.local/` 生成仓库外 SHA-256 manifest。
- [ ] 确认本清单和 `evidence-index.json` 的身份字段一致。
- [ ] 合并前确认所有引用文件 SHA 没有因最终修订而失效。
- [ ] Merge/Closeout 后登记 merge commit、最终 Issue/Project 状态和 branch cleanup。

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
