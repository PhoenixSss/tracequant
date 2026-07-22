# 量化系统项目路线图

- **最后核验日期：** 2026-07-22
- **核验来源：** GitHub Issue 正文与评论、Sub-issue / Blocked by Relationships、ProjectV2 字段
- **适用仓库：** `PhoenixSss/quant-system`

## 1. 文档目的与核验信息

本文是项目阶段、Issue 结构和当前实施入口的摘要与导航，不替代任何 Issue 的完整正文。
动态状态仅表示上述核验日期的快照；实施前仍须重新检查 GitHub。

不同资料的权威范围如下：

1. **GitHub Issue** 是当前规划结构、职责、父子关系和依赖的主要事实来源。
2. **ADR 与已合并 PR** 对已经实施的架构决策和行为事实具有权威性；它们并非简单从属于尚未实施的规划描述。
3. [技术基线](../architecture/technical-baseline.md) 记录当前技术选型和架构边界。
4. 本文记录阶段与 Issue 导航，不复制技术基线或 Issue 正文。
5. [历史 planning baseline](quant-system-planning-baseline-v1.0.md) 保留特定时点的规划语义。
6. [研究报告](../research/) 是技术路线的历史研究依据，不是已经批准的实施规范。

概括为 `GitHub Issue → ADR / 已合并 PR → technical-baseline.md → project-roadmap.md → 历史 planning baseline → research reports`，但应按上述各自权威范围理解，而不是把它当作对所有事实的一条机械覆盖链。

## 2. 固定规划方法

- Epic 与 Feature 先完整规划，以固定阶段目标和能力边界。
- Task 只围绕近期工作滚动细化，不提前创建全部未来 Task。
- `codex:needs-spec` 表示规格尚不完整、不得实施；`codex:ready` 表示规格完整、允许实施；`codex:blocked` 表示存在已确认阻塞、解除前不得实施。
- Issue 正文中的 `Depends on` 可以列出完整逻辑依赖；GitHub Relationship 的 `Blocked by` 只保留最近的直接 blocker，避免把整个传递依赖图重复到 Relationship 中。

## 3. 技术路线摘要

当前主线以 Binance USDⓈ-M 的 BTC / ETH 为首期范围，以 1m 为基础历史粒度、15m～4h 为主要决策周期。数据采用不可变 Raw Parquet，使用 Polars 处理、DuckDB 查询；先做 vectorized screening，再以 NautilusTrader 进行事件驱动验真。策略研究从可解释 rule baseline 开始，以 LightGBM 为主模型、XGBoost 为 Challenger，并用 MLflow 追踪实验。

运行侧从 Docker Compose 起步，以 PostgreSQL 承载后续真相账本，使用 Prometheus、Grafana、Alertmanager 做监控告警。所有领域与存储时间均使用 timezone-aware UTC。

辅助数据、L2/L3、多交易所、资金放量、Kubernetes、active-active 和多区域容灾均后置并由证据驱动，不是当前默认实施承诺。

## 4. 四个 Epic 和 32 个 Feature

以下标题取自 GitHub Issue 的真实 `content.title`。职责与输出仅作导航，完整范围和验收条件以相应 Issue 为准。

### [#1 `[Epic] Research MVP`](https://github.com/PhoenixSss/quant-system/issues/1)

阶段目标是形成可复现、成本感知、无未来数据泄漏的离线研究闭环；阶段门禁是研究证据包和进入 Shadow & Demo 的明确建议，不包含私有 API 或订单执行。

| Issue | 标题 | 一句职责 | 阶段门禁或主要输出 |
|---|---|---|---|
| [#2](https://github.com/PhoenixSss/quant-system/issues/2) | `[Feature] 工程与研究基础` | 统一 Python 工程、配置、日志、UTC、领域基础与质量工具链。 | 可复现安装及 pytest、Ruff、mypy、CI 基线。 |
| [#11](https://github.com/PhoenixSss/quant-system/issues/11) | `[Feature] Binance 公共市场数据与原始数据湖` | 获取 Binance USDⓈ-M 公共历史数据并以不可变、幂等方式保存。 | Raw Parquet、manifest、校验与恢复契约。 |
| [#15](https://github.com/PhoenixSss/quant-system/issues/15) | `[Feature] 标准化行情与数据质量` | 建立 canonical schema、质量门禁和从 1m 到决策周期的确定性聚合。 | 标准化 Parquet、质量报告与 DuckDB 入口。 |
| [#16](https://github.com/PhoenixSss/quant-system/issues/16) | `[Feature] 特征、标签与研究数据集` | 构建 point-in-time 安全、版本化且可追溯的特征、标签和研究样本。 | 数据集 manifest、catalog、切分与泄漏检查。 |
| [#17](https://github.com/PhoenixSss/quant-system/issues/17) | `[Feature] 向量化研究回测与绩效评估` | 快速筛选策略并按统一时间、成本和样本外口径评估。 | gross/net、成本、风险、稳健性与候选报告。 |
| [#18](https://github.com/PhoenixSss/quant-system/issues/18) | `[Feature] NautilusTrader 事件驱动回测与成本仿真` | 对入围候选进行订单级、成本与执行真实性验真。 | 订单/成交/PnL 报告及与向量化结果的差异分析。 |
| [#19](https://github.com/PhoenixSss/quant-system/issues/19) | `[Feature] 基线策略与机器学习研究` | 完成规则与树模型基线、受控候选生成、优化、筛选和排序。 | 少量可进入 #18 的严格 OOS 候选策略包。 |
| [#20](https://github.com/PhoenixSss/quant-system/issues/20) | `[Feature] 实验复现、模型登记与研究报告` | 统一实验身份、MLflow 登记、artifact 校验与证据报告。 | Research Evidence Bundle 和下一阶段建议。 |

### [#12 `[Epic] Shadow & Demo MVP`](https://github.com/PhoenixSss/quant-system/issues/12)

阶段目标是证明实时数据、在线信号、Shadow/Demo 订单语义、风控、对账与恢复可运行；不接触生产真实资金。

| Issue | 标题 | 一句职责 | 阶段门禁或主要输出 |
|---|---|---|---|
| [#21](https://github.com/PhoenixSss/quant-system/issues/21) | `[Feature] 实时运行与部署基础` | 提供 test/shadow/demo 隔离的单机实时运行与部署基础。 | Docker Compose、runtime manifest、liveness/readiness。 |
| [#22](https://github.com/PhoenixSss/quant-system/issues/22) | `[Feature] 实时公共行情与事件管线` | 将 Binance 公共实时行情转为可信 canonical 事件并可恢复分发。 | gap 修复、journal/checkpoint、回放与数据健康指标。 |
| [#23](https://github.com/PhoenixSss/quant-system/issues/23) | `[Feature] 在线特征、信号与模型推理` | 复用离线定义计算在线特征并生成版本化可信信号。 | 在线/离线一致性、推理 readiness 与统一 signal 契约。 |
| [#24](https://github.com/PhoenixSss/quant-system/issues/24) | `[Feature] Shadow 组合与虚拟账户` | 维护虚拟目标、订单、成交、账户、成本和 PnL。 | append-only Shadow ledger 与回测偏差报告。 |
| [#25](https://github.com/PhoenixSss/quant-system/issues/25) | `[Feature] Binance Demo 执行与订单状态` | 在 Demo 环境验证私有流、订单生命周期和 UNKNOWN 处理。 | Demo 订单证据、延迟/成交质量与状态机结果。 |
| [#26](https://github.com/PhoenixSss/quant-system/issues/26) | `[Feature] 运行时风险、Kill Switch 与安全门禁` | 对信号、订单意图和账户状态实施最终风险裁决与安全降级。 | HALT/REDUCE_ONLY、Kill Switch 和 fail-closed 门禁。 |
| [#27](https://github.com/PhoenixSss/quant-system/issues/27) | `[Feature] 订单、成交与仓位对账恢复` | 用权威快照闭合订单、成交、仓位差异并安全恢复。 | reconciliation、checkpoint、差异处置与恢复门禁。 |
| [#28](https://github.com/PhoenixSss/quant-system/issues/28) | `[Feature] 监控、告警与运行手册` | 汇总数据、信号、执行、风险和恢复的可观测性与操作规程。 | Dashboard、告警、Runbook 和进入 Live 的阶段报告。 |

### [#13 `[Epic] Live MVP`](https://github.com/PhoenixSss/quant-system/issues/13)

阶段目标是仅用极小、固定且批准的真实资金验证生产执行、真实成本、真相账本与长期运行纪律，不实施正式资金放量。

| Issue | 标题 | 一句职责 | 阶段门禁或主要输出 |
|---|---|---|---|
| [#29](https://github.com/PhoenixSss/quant-system/issues/29) | `[Feature] 实盘凭据与环境隔离` | 隔离 Live 凭据、endpoint、账户、节点身份与人工准入。 | 最小权限、固定出口、生产 preflight 与 fail-closed 配置。 |
| [#30](https://github.com/PhoenixSss/quant-system/issues/30) | `[Feature] 小资金实盘执行` | 在显式写授权下执行极小资金订单并保持幂等和可停止。 | execution/write authorization、订单会话与真实成交证据。 |
| [#31](https://github.com/PhoenixSss/quant-system/issues/31) | `[Feature] 账户、订单、成交与仓位真相账本` | 保存权威账户、订单、成交、仓位事实及稳定投影。 | PostgreSQL truth ledger、lineage 与一致性读模型。 |
| [#32](https://github.com/PhoenixSss/quant-system/issues/32) | `[Feature] 实盘风险预算与紧急降级` | 对实盘风险预算和 Kill Switch 保持最终裁决权。 | 风险许可、HALT/REDUCE_ONLY 与紧急安全动作。 |
| [#33](https://github.com/PhoenixSss/quant-system/issues/33) | `[Feature] 手续费、资金费率与 PnL 归因` | 按真实事实归因手续费、资金费率、滑点和 PnL。 | gross/net PnL、成本差异与可审计归因。 |
| [#34](https://github.com/PhoenixSss/quant-system/issues/34) | `[Feature] 模型发布、审批与回滚` | 以人工审批控制模型/策略 release、激活和回滚。 | 不可变 release、审批证据与 last-known-good 回滚。 |
| [#35](https://github.com/PhoenixSss/quant-system/issues/35) | `[Feature] 生产监控、告警与日终审计` | 监控生产安全、成本和状态，并执行日终审计。 | P1/P2 告警、daily audit 与下一窗口门禁。 |
| [#36](https://github.com/PhoenixSss/quant-system/issues/36) | `[Feature] 故障恢复与实盘运行手册` | 以人工、对账优先的方式处理故障并完成 Live 阶段恢复门禁。 | recovery gate、演练记录、Runbook 与 Live MVP 关闭证据。 |

### [#14 `[Epic] Production Hardening & Expansion`](https://github.com/PhoenixSss/quant-system/issues/14)

这是证据驱动的后 MVP 候选扩展集合，不是默认全部实施的平台计划，也不按 Issue 编号形成单一线性链。

| Issue | 标题 | 一句职责 | 阶段门禁或主要输出 |
|---|---|---|---|
| [#37](https://github.com/PhoenixSss/quant-system/issues/37) | `[Feature] BTC/ETH 多标的组合与统一风险预算` | 在固定总 approved capital 下配置 BTC/ETH 资本并统一组合风险。 | 组合目标、风险贡献、并发预留与固定资金验证。 |
| [#38](https://github.com/PhoenixSss/quant-system/issues/38) | `[Feature] 自动重训与 Challenger 模型体系` | 自动产生和评估 Challenger，但不自动生产发布。 | 可审计候选、Champion 对比与 #34 人工审查交接。 |
| [#39](https://github.com/PhoenixSss/quant-system/issues/39) | `[Feature] 链上、衍生品与文本辅助数据` | 以 point-in-time 和可选降级方式评估辅助数据增量价值。 | source registry、增量/消融报告与 core-only fallback。 |
| [#40](https://github.com/PhoenixSss/quant-system/issues/40) | `[Feature] L2/L3 数据与微观结构研究` | 区分并研究 L2 price-level 与真实 L3 market-by-order 数据。 | 订单簿重建、执行模型校准与增量价值报告。 |
| [#41](https://github.com/PhoenixSss/quant-system/issues/41) | `[Feature] 多交易所研究与适配评估` | 在非真实资金环境评估候选交易所和适配语义。 | capability matrix、conformance evidence 与 Pilot 建议。 |
| [#42](https://github.com/PhoenixSss/quant-system/issues/42) | `[Feature] 资金容量、流动性与风险调整后收益优化` | 研究资金增加后的容量、真实成本和风险并做人工批准的分级放量。 | capacity curve、capital ladder、观察窗口与可回滚建议。 |
| [#43](https://github.com/PhoenixSss/quant-system/issues/43) | `[Feature] 研究机与实盘节点分离部署` | 固定节点角色、身份、信任边界和不可变制品发布路径。 | 节点隔离、promotion/attestation、只读回流与迁移验收。 |
| [#44](https://github.com/PhoenixSss/quant-system/issues/44) | `[Feature] 高可用、容灾与平台扩展` | 按批准 tier 建立 standby、fencing、failover、backup 和 DR。 | HA/DR evidence、演练结果与 tier 升降级建议。 |

## 5. 正式跨阶段依赖

截至核验日期，正式的直接跨阶段 Relationships 为：

```text
#20 -> #21
#28 -> #29
#36 -> #37
#36 -> #38
#36 -> #39
#36 -> #40
#36 -> #41
#36 -> #42
#36 -> #43
#43 -> #44
```

其中 `A -> B` 表示 B 直接依赖 A。Epic #14 **不是** `#36 -> #37 -> #38 -> #39 -> #40 -> #41 -> #42 -> #43 -> #44`。#37～#43 大多可在 #36 完成后，根据证据、预算和优先级独立推进；#44 的直接 blocker 是 #43。

## 6. 关键职责边界

- **#19** 负责 Research 阶段的策略发现、候选生成、受控优化、筛选和排序；它不负责实盘资金放量。
- **#38** 负责自动重训和 Challenger 候选生成，**不等于自动生产发布**；任何生产晋升仍经过 #34。
- **#42** 负责资金容量、真实成本校准、固定资金下的有限优化，以及人工批准的分级放量。
- **#37 与 #42：** #37 在固定总 approved capital 下做 BTC/ETH 资本配置；#42 研究资金规模增加后的容量、成本和风险。
- **L2 与 L3：** L2 是 price-level book，L3 是 market-by-order；没有权威订单身份时不得把 L2 声称为 L3。
- **#41** 是多交易所研究与适配评估，**不等于第二交易所真实资金支持**；真实资金 Pilot 需要独立规划和门禁。
- **#43 与 #44：** #43 固定节点角色、身份、信任边界和不可变部署；#44 在此基础上负责 standby、fencing、failover、backup、DR 和平台 tier。

所有 Post-MVP Feature 都不得绕过 Live MVP 已建立的生产安全链：#30 execution / write authorization、#31 truth ledger、#32 risk / Kill Switch、#33 PnL / cost、#34 release approval、#35 production audit、#36 recovery gate。

## 7. 当前实施状态与下一入口

截至 2026-07-22，Feature [#2](https://github.com/PhoenixSss/quant-system/issues/2) 已完成 Task [#3](https://github.com/PhoenixSss/quant-system/issues/3)、[#5](https://github.com/PhoenixSss/quant-system/issues/5)、[#7](https://github.com/PhoenixSss/quant-system/issues/7) 和 [#9](https://github.com/PhoenixSss/quant-system/issues/9)。[#45](https://github.com/PhoenixSss/quant-system/issues/45) 是当前文档迁移 Task。

#45 完成后，实施入口回到 Feature #2，只滚动细化其近期基础 Task；不直接开始 #42 或 Epic #14，也不在本文创建或承诺全部剩余 Task。

## 8. Project 字段与标签体系

以下为核验日期时的稳定枚举；动态项目值仍以 GitHub 为准。

| 类别 | 允许值 |
|---|---|
| Phase | `Foundation`, `Data`, `Research`, `Backtest`, `Shadow`, `Live` |
| Target | `Research MVP`, `Shadow MVP`, `Live MVP`, `Post MVP` |
| 类型标签 | `type:epic`, `type:feature`, `type:task`, `type:bug`, `type:research`, `type:docs` |
| Area | `area:foundation`, `area:data`, `area:features`, `area:labels`, `area:backtest`, `area:strategy`, `area:ml`, `area:risk`, `area:exchange`, `area:execution`, `area:monitoring`, `area:infra` |
| Risk | `risk:data-integrity`, `risk:lookahead`, `risk:live-trading`, `risk:credentials`, `risk:order-state` |
| Codex | `codex:needs-spec`, `codex:ready`, `codex:blocked` |

`area:research` 不存在，也不得使用；研究类工作应使用现有类型与实际责任 Area。

## 9. ProjectV2 Title 说明

Issue-backed Project item 的真实标题以 `content.title` 为准。ProjectV2 的派生 Title 可能短暂不同步；API 不能像修改 DraftIssue 那样直接修改 Issue-backed item 的 Title。因此，不应把派生 Title 异常误判为 Issue 标题错误，也不在文档任务中尝试修复它。
