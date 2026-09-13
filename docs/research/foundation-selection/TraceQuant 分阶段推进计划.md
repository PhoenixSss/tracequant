# TraceQuant 分阶段推进计划

> 基底：NautilusTrader v2 `v2.0.0rc4` / `a0400251110653b6d8ae6a9b5b89c4543fa85a2d`  
> 当前决策：`NAUTILUS_PRIMARY`、`LIVE_NOT_APPROVED`  
> 计划原则：每个阶段形成独立可验收结果；未通过退出条件，不提前建设下一阶段能力  
> 时间为单人熟练 Python 开发者的工程量估算，不是上线日期承诺

## 1. 阶段总览

| 阶段 | 目标 | 主要结果 | 预计工程量 | 是否接交易所账户 |
|---|---|---|---:|---|
| **阶段 1** | 最小离线能力闭环 | BTCUSDT 单周期数据 → Nautilus catalog → 单策略回测 → 可核验报告 | 5–8 个工作日 | 否 |
| **阶段 2** | 数据与研究可信度 | BTC/ETH、多周期、funding/mark/index、研究视图、parity tests | 7–12 个工作日 | 否 |
| **阶段 3** | 策略与模型闭环 | 传统策略基线 + 一种 GBDT 模型、artifact contract、OOS 评估 | 8–15 个工作日 | 否 |
| **阶段 4** | Binance Demo 基础闭环 | 行情、下单、撤单、成交、平仓的最小 Demo 流程 | 5–10 个工作日 | 仅 Demo |
| **阶段 5** | Demo 恢复性与持续观察 | 持久化、重启、断连、对账、风险门禁、soak observation | 10–20 个工作日 + 观察期 | 仅 Demo |
| **阶段 6** | Live 准入决策 | 版本、缺陷、Demo 证据和运维条件复审 | 无固定日期 | 默认否 |

阶段之间不是功能堆叠关系，而是证据递进关系：

```text
可重复回测
  → 数据与时间语义可信
  → 策略/模型研究可信
  → Demo 基础执行可信
  → Demo 恢复和长期状态可信
  → 才讨论 Live
```

## 2. 阶段 1：最小离线能力闭环

### 2.1 唯一目标

用一份固定的 BTCUSDT USDⓈ-M 永续合约历史 Bar 数据，在完全离线、不使用交易所凭据的环境中，运行一个最简单的 Nautilus 原生策略，并稳定产出订单、成交、持仓、账户、费用和收益结果。

最小纵向链路：

```text
固定 BTCUSDT 1h Bar 数据
  → 数据 manifest + checksum
  → Nautilus Instrument / Bar
  → Nautilus ParquetDataCatalog
  → Nautilus Strategy（简单均线交叉）
  → Nautilus Backtest
  → BacktestResult + orders/fills/positions/account reports
  → 自动化断言
```

阶段 1 只证明下面一件事：

> TraceQuant 可以在固定环境中，把真实格式的市场数据送入 Nautilus，使用 Nautilus 的交易领域对象完成一次确定、可重复、可测试的回测。

### 2.2 固定范围

| 项目 | 阶段 1 决定 |
|---|---|
| 标的 | 仅 `BTCUSDT` USD-M perpetual |
| 数据 | 固定、不可变的小型历史快照；建议 30–90 天 |
| 粒度 | 仅 `1h` closed bars |
| 价格类型 | `LAST` |
| 策略 | 一个无参数搜索的 SMA/EMA crossover |
| 指标 | 直接使用 Nautilus 内建 moving-average indicator |
| 订单 | 仅 market entry / market exit |
| Position mode | 单方向、NETTING 语义 |
| 初始资金 | 固定 USDT 余额 |
| 成本 | 固定、明确记录的手续费模型；不处理 funding |
| 输出 | Nautilus 原生 result/reports + 一份机器可读摘要 |
| 执行方式 | 一条命令运行，一条命令测试 |

### 2.3 必须交付

1. **版本锁定**
   - Python 版本和 Nautilus 精确版本锁定。
   - 启动时输出或校验实际 Nautilus 版本。

2. **最小数据样本**
   - 数据来源 URL、下载时间、symbol、interval、起止时间、文件 checksum。
   - 数据按事件时间升序；无重复时间戳。
   - 数据只转换为 Nautilus `Bar`，不定义 TraceQuant `Bar` 类。

3. **Nautilus catalog 写入与读取**
   - 使用 `ParquetDataCatalog`。
   - 从 catalog 读出的 Bar 数量、首末时间与 manifest 一致。

4. **一个 Nautilus 原生 Strategy**
   - 使用 Nautilus 生命周期、indicator、OrderFactory、position/cache 查询。
   - 策略只包含信号规则和最小仓位参数。
   - 不创建 Strategy Adapter、Order DTO、Position DTO 或 Portfolio service。

5. **一个确定性回测入口**
   - 固定 instrument、数据、起止时间、资金、手续费和策略参数。
   - 连续运行两次时，排除 UUID 和墙钟耗时后，关键结果一致。

6. **最小自动化验收**
   - catalog 可以读回数据。
   - 策略至少产生一次入场和一次退出。
   - 回测结束无未解释 open order；建议最终持仓为平。
   - orders、fills、positions、account report 均可生成。
   - 余额变化、手续费、已实现 PnL 之间不存在未解释差额。
   - 测试命令返回成功退出码。

7. **最小运行说明**
   - 从空环境安装、准备数据、运行回测、运行测试的命令。
   - 明确当前只允许离线回测。

### 2.4 阶段 1 建议目录

```text
tracequant/
  pyproject.toml
  configs/
    stage1_backtest.toml
  data/
    manifests/
      btcusdt_1h.json
  src/tracequant/
    data_sources/
      binance_bar_snapshot.py
    strategies/
      moving_average_cross.py
    backtest/
      run_stage1.py
  tests/
    test_stage1_data.py
    test_stage1_backtest.py
  README.md
```

历史数据本体是否进入 Git 由体积和数据条款决定；manifest、checksum 和可重复获取方法必须进入版本管理。

### 2.5 明确不做

阶段 1 不得顺手加入以下内容：

- ETHUSDT、多标的、多账户、多 venue。
- 15m/4h、多周期对齐和运行时 Bar 重采样。
- funding、mark、index、order book、tick 数据。
- Jupyter 研究平台、DuckDB、feature store。
- LightGBM、XGBoost、PyTorch、GPU、RL、LLM。
- Optuna、MLflow、Prefect、Dagster。
- Binance Demo/Testnet/Live、API key、WebSocket execution。
- Redis/Postgres 状态持久化和 reconciliation。
- Prometheus、Grafana、Web UI。
- limit/stop/trailing/reduce-only/hedge-mode 订单矩阵。
- daily-loss、stale-data、组合总敞口等高级风控。
- 任何自研 Binance adapter、RiskEngine、Order/Position/Portfolio 或 reconciliation engine。
- 为未来需求设计通用 plugin framework、service mesh 或多仓库拆分。

### 2.6 退出条件

以下条件必须全部满足，阶段 1 才算完成：

```text
[ ] 全新环境可按文档完成安装
[ ] 固定数据可验证 checksum
[ ] 数据可写入并读回 ParquetDataCatalog
[ ] 回测可由单一命令完成
[ ] 至少出现一组完整 entry → fill → exit → fill
[ ] Nautilus 原生五类结果可核验：order/fill/position/account/summary
[ ] 两次运行的关键结果完全一致
[ ] 自动化测试全部通过
[ ] 无交易所凭据，无 Demo/Live 网络依赖
[ ] 未引入被明确排除的组件或第二套交易领域对象
```

阶段 1 的成功标准不是收益率，也不是策略是否有经济价值；它只验证工程闭环和领域所有权。

## 3. 阶段 2：数据与研究可信度

### 目标

把阶段 1 的单样本回测扩展成可支撑策略研究、且时间语义可验证的数据基础。

### 范围

- 增加 ETHUSDT，以及 15m/1h/4h。
- 建立 raw source manifest、checksum、gap/duplicate/coverage report。
- 纳入 funding，并评估历史 mark/index 的可靠来源与覆盖。
- 解决或绕过 rc4 Python catalog 未直接暴露 Funding write/query 的小缺口：优先 upstream/binding 或受控 custom data，不另建数据平台。
- 建立 Polars 只读研究视图；Jupyter 按需启用。
- 验证 Polars 批处理 Bar/indicator 与 Nautilus 运行时结果的 parity。
- 建立 train/validation/test 时间切分和最基本的 leakage guards。

### 退出条件

- BTC/ETH 每个周期都有版本化 coverage report。
- 缺口、重复、时间错序会导致 pipeline 失败，而非静默通过。
- funding 的存储、读取和回测计入方式有自动化测试。
- 研究派生表不写回或覆盖 Nautilus catalog。
- 同一批数据的关键指标在研究与 Nautilus runtime 间误差处于书面阈值内。

## 4. 阶段 3：策略与模型闭环

### 目标

在可信数据基础上完成两个可比较基线：一个传统策略和一个现代数据驱动策略。

### 范围

- 完善传统 momentum/mean-reversion 基线。
- 只选择 LightGBM 或 XGBoost 中一个作为首个 GBDT 模型。
- 定义最小 artifact manifest：数据版本、feature schema、训练窗口、代码 SHA、依赖版本、模型文件 checksum。
- 模型推理直接集成进 Nautilus Strategy；不创建通用 model runtime gateway。
- 完成 walk-forward/OOS 评估、费用和 funding 敏感度分析。
- 只有实验数量形成实际管理问题时，才在阶段尾评估 MLflow/Optuna。

### 退出条件

- 两类策略使用同一 Nautilus backtest/accounting 事实源。
- 训练和推理 feature schema 不一致时 fail closed。
- 任一 artifact 都能追溯到数据、代码和配置。
- OOS 结果可由一条命令重建。
- 研究结果不以单一收益指标决定是否进入 Demo；必须包含回撤、换手、费用敏感度和稳定性。

## 5. 阶段 4：Binance Demo 基础闭环

### 目标

只验证 Nautilus 与 Binance Demo 的基础行情和执行链，不追求长时稳定性或真钱上线。

### 范围

- 明确账户仅使用 one-way 或 hedge 中一种；首选 one-way。
- 明确 cross/isolated 中一种；不同时验证两套模式。
- 先运行 Nautilus 官方 `DataTester` 和 `ExecTester`。
- 再运行 TraceQuant 最小 Demo Strategy。
- 覆盖订阅、market、limit、cancel、完整成交和 reduce-only 平仓。
- 所有订单使用极小 Demo 数量和严格 symbol allowlist。

### 退出条件

- 行情订阅连续稳定，时间戳和 instrument 解析正确。
- 下单、成交、撤单、平仓状态都能由 Nautilus cache/report 解释。
- 交易所订单、持仓、余额与 Nautilus 观察一致。
- 任何未知状态均 fail closed，并保留诊断证据。
- 阶段结束仍保持 `LIVE_NOT_APPROVED`。

## 6. 阶段 5：Demo 恢复性与持续观察

### 目标

证明系统在异常、重启和长时间运行中仍能由 Nautilus 恢复并解释状态。

### 范围

- 通过 Nautilus 官方接口接入 Redis 或 Postgres cache backing，二选一。
- 验证 cold/warm restart、open order、open position、partial fill、断连重连。
- 启用并验证启动 reconciliation、in-flight checks、open-order checks、position checks。
- 实现项目级 daily loss、总敞口、stale-data stop 和 `REDUCING/HALTED` 门禁；只补 Nautilus 缺失政策。
- 建立结构化日志、核心告警和最小 runbook。
- 先完成 24–72 小时工程 soak，再进行更长的策略观察；观察期长度由异常率和变更频率决定，不以日历硬截止。

### 退出条件

- 带 open order/position 的重启场景可重复通过。
- 无影子 REST reconciler；所有修正状态来自 Nautilus reconciliation。
- 每个异常都有 fail-safe 行为、日志、告警和恢复步骤。
- 长时观察期间无未解释的 order/position/balance drift。
- 每次代码、Nautilus 或 Binance API 变更后，关键 Demo regression 会重跑。

## 7. 阶段 6：Live 准入决策

阶段 6 是独立的风险决策，不是开发计划自然到期后的自动上线。

只有同时满足以下条件，才可以另行讨论 tiny-capital canary：

- 目标 Nautilus 版本的官方生产建议已重新核查；若仍是 RC，需要明确的单独风险接受。
- 与 Binance 有关的 blocker、尤其 restart/reconciliation 问题已关闭、规避或经 Demo 证明不可复现。
- 阶段 5 的观察周期完成，且无未解释 drift。
- 手续费、funding、position mode、margin mode、leverage 与实际账户配置一致。
- API key 最小权限、IP 限制、secret rotation、kill switch、告警和回滚全部演练。
- 人工审批 tiny-capital 金额和最大可接受损失。

任一条件不满足时，项目继续停留在 Demo；这不构成项目失败。

## 8. 跨阶段控制规则

### 依赖引入规则

新增库必须回答三个问题：

1. 当前阶段哪个退出条件没有它无法满足？
2. Nautilus 是否已经提供同类能力？
3. 它是否会形成第二套交易领域状态？

不能明确回答第一个问题，或第三个问题答案为“是”，则不引入。

### Ownership 规则

```text
Nautilus owns:
Instrument, market-data domain types, catalog, Strategy lifecycle,
Order, Position, Portfolio, Account, core Risk, Backtest,
Execution, cache state and Reconciliation.

TraceQuant owns:
source provenance, research views, features, labels, models,
strategy intent, policy values, acceptance, deployment and Live gate.
```

### 变更规则

- 每阶段冻结 Nautilus 精确版本；升级作为独立变更评审。
- 不在完成当前阶段的同时迁移框架或升级主要依赖。
- 阶段出口只依据自动化证据和可复现记录，不依据 notebook 截图或人工印象。
- 阶段 1–3 不需要交易所密钥；阶段 4–5 只允许 Demo 密钥；Live 密钥不提前配置。

## 9. 当前立即执行项

当前只创建阶段 1 backlog：

1. 初始化单仓库 Python 项目并锁定 Nautilus 版本。
2. 固定 BTCUSDT 1h 小型数据快照及 manifest/checksum。
3. 转换为 Nautilus Instrument/Bar 并写入 `ParquetDataCatalog`。
4. 实现一个 Nautilus 原生 moving-average Strategy。
5. 建立确定性 backtest config 和单命令入口。
6. 导出 Nautilus 原生 reports 和机器可读摘要。
7. 编写数据、闭环、账务和重复运行测试。
8. 写最小运行文档，标注 `OFFLINE_BACKTEST_ONLY`。

除此之外的工作全部进入后续阶段 backlog，不在首阶段实现。
