# 量化系统项目规划基线与偏移矫正文档

- **版本：** 1.0
- **日期：** 2026-07-19
- **项目：** `PhoenixSss/quant-system`

> [!WARNING]
> **历史快照：** 本文件保留 2026-07-19 时点的规划和决策语义，其中“当前 GitHub 状态”已经过时，
> 不应作为实时项目状态使用。当前项目全景与实施入口请查看 [项目路线图](project-roadmap.md)，
> 当前技术选型请查看 [技术基线](../architecture/technical-baseline.md)；最新规划结构以 GitHub Issue 为准。

## 1. 用途

本文档用于：

- 固化当前量化系统的技术路线与规划方法；
- 在网页会话过长、开启新会话或切换 Codex 后恢复上下文；
- 检查 Epic、Feature、Task 是否发生范围或阶段偏移；
- 作为三份原始研究报告进入 Git 前的临时规划基线。

重新上传本文档时，应先核对当前 GitHub Issue、Project 和仓库状态，再继续规划或实施。


## 2. 权威优先级

### 文档尚未全部进入 Git 时

1. 已批准的 GitHub Issue 和明确决策；
2. 两份后期技术确认报告；
3. `technical-roadmap-research.md`；
4. 本文档；
5. 临时聊天讨论。

### Codex 完成文档迁移后

1. 已批准的 ADR、Issue 和 PR 决策；
2. `docs/architecture/technical-baseline.md`；
3. `docs/planning/project-roadmap.md`；
4. 三份原始研究报告；
5. 聊天记录。

发生冲突时，执行高优先级资料；不得静默折中。


## 3. 原始研究资料

规划依据为：

- `deep-research-report.md`
- `deep-research-report (2).md`
- `technical-roadmap-research.md`

三份报告共同确定：

- 个人项目不从超低延迟高频起步；
- 首期聚焦 BTC/ETH 中频与日内永续合约研究；
- 先完成数据与研究闭环，再进入 Shadow、Demo、小资金实盘；
- 官方交易所数据优先；
- 研究逻辑、数据标准、标签、验证、执行 policy 和风控自研；
- 通用交易内核和基础设施优先复用。


## 4. 已确认技术路线

- 首期交易场所：Binance USDⓈ-M 永续合约；
- 首批标的：BTC、ETH；
- 首批合约：BTCUSDT、ETHUSDT；USDC 合约先确认可用性；
- 基础历史粒度：1m；
- 研究与决策周期：15m～4h；
- 历史数据格式：Parquet；
- 批量处理：Polars；
- 查询与切片：DuckDB；
- 事件驱动回测及未来执行底座：NautilusTrader；
- 主模型：LightGBM；
- Challenger：XGBoost；
- 深度学习、GNN、RL、LLM 直接出信号后置；
- 实验追踪：MLflow；
- 未来状态与审计：PostgreSQL；
- 未来缓存/状态：Valkey 优先，保持 Redis 协议兼容；
- 监控：Prometheus、Grafana、Alertmanager；
- 部署：Docker Compose 起步，Kubernetes 后置。

核心原则：

> 自研 Alpha、数据标准、验证、执行策略和风险；复用交易内核与通用基础设施。


## 5. 固定规划方法

采用以下顺序：

> Epic 先定边界 → Feature 补齐骨架 → 当前 Feature 补齐剩余任务 → 只细化近期 Task

### Epic

提前规划完整，表示一个可独立验收的阶段目标。

### Feature

所有 Epic 的 Feature 骨架在网页端提前规划完整，以获得项目全景。每个 Feature 至少包含：

- 背景、目标和价值；
- 输入、输出；
- 功能范围与非功能要求；
- 异常和边界；
- 范围外；
- 完成条件；
- 建议子任务；
- 依赖、父 Epic、风险和规模。

### Task

滚动规划：

- 当前 Task：完整规格，可进入 `codex:ready`；
- 接下来 1～2 个 Task：可以先建，保留 `codex:needs-spec`；
- 更远 Task：只写在 Feature 的建议子任务中；
- 原则上只有一个主要 Task 处于 `In Progress`；
- 每完成 2～4 个 Task，复核一次所属 Feature。

不得一次创建所有远期 Task。


## 6. 项目 Epic 全景

### Epic #1：Research MVP

目标：完成可重复、无未来数据泄漏、包含现实成本假设的离线研究闭环。

Feature 骨架：

1. 工程与研究基础；
2. Binance 公共市场数据与原始数据湖；
3. 标准化行情与数据质量；
4. 特征、标签与研究数据集；
5. 向量化研究回测与绩效评估；
6. NautilusTrader 事件驱动回测与成本仿真；
7. 基线策略与机器学习研究；
8. 实验复现、模型登记与研究报告。

### Epic #2：Shadow & Demo MVP

目标：在不使用真实资金的情况下验证实时运行、风险、订单语义和恢复。

Feature 骨架：

1. 实时运行与部署基础；
2. 实时公共行情与事件管线；
3. 在线特征、信号与模型推理；
4. Shadow 组合与虚拟账户；
5. Binance Demo 与 NautilusTrader 执行接入；
6. 运行时风控与 Kill Switch；
7. 订单、成交与仓位对账恢复；
8. 监控、告警与运行手册。

### Epic #3：Live MVP

目标：以极小资金验证真实执行、真实成本和长期稳定性。

Feature 骨架：

1. 实盘凭据与环境隔离；
2. 小资金实盘执行；
3. 账户、订单、成交与仓位真相账本；
4. 实盘风险预算与紧急降级；
5. 手续费、资金费率与 PnL 归因；
6. 模型发布、审批与回滚；
7. 生产监控、告警与日终审计；
8. 故障恢复与实盘运行手册。

### Epic #4：Production Hardening & Expansion

目标：扩展已验证系统，不干扰首期 MVP。

Feature 骨架：

1. BTC/ETH 多标的组合与统一风险预算；
2. 自动重训与 Challenger 模型体系；
3. 链上、衍生品与文本辅助数据；
4. L2/L3 数据与微观结构研究；
5. 多交易所研究与适配评估；
6. 容量、流动性与冲击成本研究；
7. 研究机与实盘节点分离部署；
8. 高可用、容灾与平台扩展。


## 7. 阶段边界与门禁

总体顺序：

`Research MVP → Shadow & Demo MVP → Live MVP → Production Hardening & Expansion`

### Research MVP 范围

`公开数据 → 原始数据湖 → 标准化与质量检查 → 特征与标签 → 向量化研究 → 事件回测 → 基线策略/模型 → 研究报告`

不包含私有 API、API Key、账户、订单、持仓、user data stream、Demo 或真实下单。

### Research → Shadow & Demo

至少要求：

- 数据和实验可重复；
- 无明显未来泄漏；
- 至少一个可解释规则基线；
- 至少一个模型基线；
- fee、funding 和滑点进入净收益；
- NautilusTrader 事件回测可用；
- 研究报告记录限制和风险。

### Shadow & Demo → Live

至少要求：

- Shadow 连续稳定运行；
- Demo 订单生命周期通过；
- 对账、重启恢复和 Kill Switch 通过；
- 数据延迟、拒单和状态异常可检测；
- 监控与告警可用。

### Live → Production Expansion

至少要求：

- 极小资金运行无失控仓位；
- 真实成本可归因；
- 订单、成交、仓位和权益可审计；
- 故障恢复和紧急降级有效；
- 模型发布可回滚。


## 8. 当前 GitHub 状态

仓库：`PhoenixSss/quant-system`

- Epic #1：`[Epic] Research MVP`
  - OPEN / Specifying / P1 / L / Research / Research MVP
  - `type:epic`、`codex:needs-spec`

- Feature #2：`[Feature] 工程与研究基础`
  - OPEN / Specifying / P1 / M / Foundation / Research MVP
  - `type:feature`、`area:foundation`、`codex:needs-spec`

已完成：

- #3 初始化 uv Python 工程与 src 包布局；
- #5 配置 pytest、Ruff 与 mypy；
- #7 建立 GitHub Actions CI；
- #9 建立 UTC 时间规范与基础工具。

当前 main 与 origin/main 同步，PR #10 已 squash merge，main CI 成功，本地 33 tests passed，工作区干净。

Feature #2 虽显示当前登记子任务 4/4，但仍保持 OPEN；剩余规划包括配置、日志、核心领域基础、路径规范、开发文档和技术路线文档迁移。


## 9. 网页端与 Codex 的交接

### 网页端继续完成

1. 修订 Epic #1 的 Feature 骨架；
2. 创建 Epic #2、#3、#4；
3. 创建四个 Epic 下的全部 Feature；
4. 设置 Parent、Status、Priority、Size、Phase、Target 和标签；
5. 记录阶段门禁；
6. 审查重复职责、遗漏、依赖倒置和阶段混入；
7. 审查与三份研究报告的一致性；
8. 添加最终项目全景评论。

网页端暂不完成全部 Task、实现级 API、远期数据库表结构和远期模块内部实现。

### Codex 接管点

全部 Epic 和 Feature 骨架完成并通过全局审查后，创建 docs-only Task：

`[Task] 将技术选型研究与完整项目规划纳入仓库`

建议产物：

```text
docs/
├─ architecture/
│  └─ technical-baseline.md
├─ planning/
│  └─ project-roadmap.md
└─ research/
   ├─ deep-research-report.md
   ├─ deep-research-report-2.md
   └─ technical-roadmap-research.md
```

文档 PR 合并后，Git 仓库成为长期唯一可信来源。


## 10. 偏移检查

重新上传本文档时，先检查：

### 技术偏移

- 是否仍为 Binance USDⓈ-M、BTC/ETH、1m 基础粒度；
- 是否把 15m～4h 错当成多套原始真相；
- 是否仍采用 Parquet、Polars、DuckDB；
- 是否仍以 NautilusTrader 为事件回测与执行底座；
- 是否过早引入 L2/L3、多交易所、深度模型或 Kubernetes。

### 阶段偏移

- Research 是否混入私有 API、账户或下单；
- Shadow 与 Demo 是否失去独立边界；
- 是否在门禁前进入 Live；
- Production 扩展是否侵入 MVP。

### 规划偏移

- 是否一次创建全部 Task；
- 是否多个主要 Task 同时 In Progress；
- Feature 是否缺少完成条件；
- 是否出现重复 Feature；
- 是否绕过 Issue 实施重大能力。

### 工程偏移

- Codex 是否自行决定重大架构；
- 是否无 Issue 扩大范围；
- 是否让聊天替代仓库文档；
- 是否绕过测试、CI 或 PR。


## 11. 当前下一步

固定顺序：

1. 给 Epic #1 添加“规划修订”评论；
2. 将其 Feature 骨架修订为 8 个；
3. 创建 Epic #2；
4. 创建 Epic #3；
5. 创建 Epic #4；
6. 依次补齐四个 Epic 的全部 Feature；
7. 做全局依赖、重复职责和阶段边界审查；
8. 做三份研究报告一致性审查；
9. 添加最终项目全景评论；
10. 创建 Codex 文档迁移 Task；
11. 文档进入 Git 后，回到 Feature #2，滚动细化近期 Task。

---

## 12. 新会话矫正提示词

```text
请先完整阅读我上传的《量化系统项目规划基线与偏移矫正文档》。

将它作为当前项目的规划基线，并结合当前 GitHub Issue、Project 和仓库状态进行核对。

请先报告：
1. 当前规划与基线一致的部分；
2. 已发生的合理变更；
3. 可能发生的技术、阶段、规划或工程偏移；
4. 需要我确认的冲突。

在我确认前，不要创建新 Issue，不要修改仓库，不要改变 Epic/Feature 边界。
```

---

## 13. 最终原则

> 先获得完整项目视图，再逐步实现。
>
> Epic 和 Feature 提前规划完整，Task 只滚动细化近期工作。
>
> 研究逻辑和风险规则掌握在自己的代码中，交易内核和通用基础设施优先复用。
>
> 先正确，再真实，最后扩张。
>
> Git 仓库最终成为唯一可信来源。
