# TraceQuant 阶段 3：策略与模型实施基线

| 字段 | 值 |
| --- | --- |
| 文档状态 | 待维护者批准的实施基线 |
| 文档版本 | `0.1` |
| 日期 | `2026-09-16` |
| 产品状态 | `OFFLINE_BACKTEST_ONLY`、`LIVE_NOT_APPROVED` |
| 固定运行时 | NautilusTrader `2.0.0rc4` / `a0400251110653b6d8ae6a9b5b89c4543fa85a2d` |
| 固定输入数据集 | `binance-usdm-btceth-202001-202608-r1`（Stage 2 已验收） |
| 上位计划 | [TraceQuant 分阶段推进计划](<../research/foundation-selection/TraceQuant 分阶段推进计划.md>) 第 4、8 节 |
| 输入基线 | [阶段 2：Nautilus 同源数据详细需求](stage-2-data-and-research-requirements.md) v0.4 |

## 1. 目标与边界

阶段 3 只解决一个问题：

> 在同一个已验收的 Nautilus 同源数据集和同一套 Nautilus 账务事实上，完成一个传统策略
> 基线和一个数据驱动模型基线，并使任一模型 artifact 都能追溯到数据、特征、训练窗口、
> 代码、依赖和文件身份。

阶段 3 不建设研究平台、模型服务或流程编排系统。归属边界沿用
`docs/architecture/repository-structure.md`：Nautilus 独占 Instrument、Bar、
MarkPriceUpdate、FundingRateUpdate、Strategy 生命周期、Order、Position、Account、
Backtest 和 funding accounting；TraceQuant 拥有数据绑定、因果 feature/label、策略意图、
模型训练与 artifact 合同、研究验收和运行准入。

本文档冻结 Feature「建立阶段 3 策略与模型可信闭环」的输入身份、策略、特征/标签、
warm-up、LightGBM、artifact、窗口角色、accounting-only sensitivity、终场规则、确定性
边界、验收和 Non-goals。实施期不得重新选择首个数据集、Nautilus runtime、GBDT 算法、
决策周期、label horizon 或本文档列出的窗口取值。

阶段 3 的完成只表示软件能力成立。策略是否有经济价值、是否进入 Demo，是本文档 §12
区分的另外两层判断，不由任何收益阈值自动触发。

## 2. 固定输入与输入门禁

### 2.1 输入身份

正式 Stage 3 输入固定为已验收数据集 `binance-usdm-btceth-202001-202608-r1`。tracked
验收记录固定为
[`stage2-btceth-dataset-acceptance.json`](stage2-btceth-dataset-acceptance.json)
（`schema = tracequant-stage2-acceptance-v2`），其身份字段按记录中的实际字段名固定为：

| 记录字段 | 固定值 |
| --- | --- |
| `dataset_id` | `binance-usdm-btceth-202001-202608-r1` |
| `acceptance_digest` | `5909c878a81f0cdea85a8b8f86efd36d9c9bad5b3f3fb4c0f9960bb0551609cd` |
| `dataset_digest` | `e17c6294e0a0e6714e56a44624ade37cff46125c46d8b0ee81ede6093711579c` |
| `source_manifest_digest` | `de86d44c73117e17af2bbcb655cf1c8d4290043fa8854636cc0e4e592a1dc790` |
| `market_data_manifest_digest` | `a0d9a36bb65ec7c2ec41f47cdf6ba7d20f57ad28d8d3494a69624c60d6d0a110` |
| `instrument_snapshot.checksum_sha256` | `dd7fab59448a3b530ab70871ec57c375f6758e409004f9673d9d0cac7ee630bd` |
| `runtime_identity` | `2.0.0rc4+a0400251110653b6d8ae6a9b5b89c4543fa85a2d` |
| `catalog_evidence.instrument_snapshot_filename` | `stage2_instrument_snapshot.json` |
| `catalog_evidence.dataset_digest_filename` | `stage2_dataset_digest.json` |
| `catalog_evidence.source_manifest_filename` | `stage2_source_manifest.json` |
| `catalog_evidence.coverage_filename` | `stage2_coverage.json` |
| `splits.train` / `splits.validation` / `splits.test` | 见 §7.2 |
| `verification_test` | `tests/acceptance/test_stage2_dataset.py::test_frozen_stage2_dataset_drives_research_and_backtest_from_one_catalog` |

Stage 3 的每个正式消费者（研究 loader、训练、两个 Strategy、评估、`rebuild-oos`）都必须
核验上表的**全部**字段，外加外部 catalog 的 instrument 定义与 runtime identity。

### 2.2 普通 catalog identity 不等于完整数据集验收

既有 `require_stage2_catalog_identity(catalog_path)`（RC4 阶段 2 loader 使用的入口）只
证明：catalog 内 manifest identity 自洽、instrument snapshot checksum 自洽、catalog
instrument 定义与该 snapshot 逐字段一致。它**不**证明被消费的 catalog 就是 tracked 验收
记录所指的那个完整数据集。

因此 Stage 3 必须区分两级门禁：

1. **catalog identity（必要但不充分）**：沿用既有 `require_stage2_catalog_identity`
   的检查；
2. **完整数据集验收绑定（充分条件）**：额外要求 tracked 验收记录存在且上表字段全部
   逐字匹配，并要求外部 catalog 的覆盖产物（§2.3）与该记录一致。

任何一级失败都 fail closed。以下情形必须显式失败，不得降级为警告：

- fixture-only catalog、小型合成 catalog 或部分 series 的 catalog；
- 缺失 tracked 验收记录，或记录与仓库固定值不一致；
- tracked 身份与外部 catalog 身份冲突；
- 非固定时间窗口、非 `BTCUSDT-PERP.BINANCE` / `ETHUSDT-PERP.BINANCE` 标的；
- 决策 Bar type 不是 1h closed Bar；
- catalog instrument 定义、snapshot checksum 或 `runtime_identity` 不匹配。

### 2.3 Coverage 与 expected 输入

Stage 3 消费的序列固定为：BTC/ETH 1h closed Bar（决策）、15m MarkPriceUpdate（估值与
basis 特征）、FundingRateUpdate（funding 特征与账务）。每个序列的
`row_count` / `first_ts_event` / `last_ts_event` / `duplicate_count` /
`out_of_order_count` / `gap_count` 必须与验收记录 `coverage_summary` 一致。

验收记录已冻结的唯一允许缺口是 15m mark 的两个区间：

| 缺口区间（源 open time） | tracked `gap_explanation` |
| --- | --- |
| `2020-01-19T13:15Z` | Binance monthly and daily markPriceKlines both omit the 2020-01-19T13:15Z and 2023-11-10T03:45Z intervals; no funding event overlaps either omission |
| `2023-11-10T03:45Z` | 同上（同一字符串覆盖两个区间，BTC 与 ETH 各一份） |

这两个区间是 **expected 输入**，不是 Stage 3 的失败条件。其余 gap、任何
`duplicate_count`、任何 `out_of_order_count`、任何非 BTC/ETH 标的或非 15m mark 覆盖
一律 fail closed。

**as-of 年龄阈值（冻结）：**

```text
mark_as_of_max_age    = 15m   （含 15m；<= 判定，不是 < 判定）
funding_as_of_max_age = 8h
```

`mark_as_of_max_age` 取 15m，与 Stage 2 的 15m mark 周期和
`mark_max_age_at_funding = 15m` 一致。边界必须按“不超过”实现：在冻结源映射下 1h 决策
时刻为 `HH:59:59.999Z`，上述两个 expected mark gap 所影响的决策
（`2020-01-19T13:59:59.999Z`、`2023-11-10T03:59:59.999Z`）其最近 mark 的年龄恰好为
15m。若实现使用严格 `<`，这两个已记录、已批准的缺口会在训练与评估中被误判为数据异常，
因此该边界是合同的一部分，而不是实现细节。

`funding_as_of_max_age` 取 8h，等于 Binance USD-M funding 结算周期；源 `calc_time`
的毫秒级偏移（Stage 2 已记录）不改变该结论。

## 3. 决策、执行与账务合同

### 3.1 决策 Bar 与执行时序

- 决策只在 BTC/ETH 的 1h closed Bar 上产生；决策时刻是该 Bar 的 `ts_event`（close
  time）。在冻结源映射下即 `HH:59:59.999Z`。
- 信号只在 Bar `t` 关闭后生成；任何订单的成交 timestamp 必须严格晚于对应 decision
  timestamp。不得使用同一 Bar 的成交价格。
- 决策 Bar 之后第 k 个 1h Bar 记为 `B_k`。执行模型为 next-Bar execution：订单在 `B_1`
  执行；label 使用 `B_1` 的 open 作为进场价、`B_4` 的 close 作为出场价（§4.4）。
- 两个策略使用完全相同的决策周期、执行时序、sizing、成本、funding 和终场规则。

### 3.2 账户、sizing 与 position mode

| 项目 | 冻结值 |
| --- | --- |
| 起始余额 | `100000` USDT |
| account type | `MARGIN` |
| base currency | `USDT` |
| oms_type | `NETTING` |
| leverage | `1x`（`default_leverage = 1`） |
| 每标的每方向目标名义金额 | `10000` USDT（long / flat / short） |
| 订单类型 | market order |
| 数量来源 | 已关闭 Bar 价格与冻结 instrument increment / precision 计算 |

起始余额必须足以在 1x 下同时支持 BTC 与 ETH 的固定名义金额
（`100000 >= 2 x 10000` 成立）。数量舍入必须使用 catalog 冻结 instrument 的
`price_precision` / `size_precision` / `price_increment` / `size_increment`，不得由
策略自行定义。

反转时必须先平旧仓，等待 open order 清空且仓位关闭后再建立反向仓位。任一未知
order / position 状态不得提交新订单。

策略与模型**不得**拥有 Account、Portfolio、fee 或 funding 状态；订单、成交、持仓、
费用、funding 和账户变化全部由 Nautilus 产生。

### 3.3 base accounting 冻结数值

```text
starting_balance_usdt   = 100000
base_maker_fee          = 0.0002
base_taker_fee          = 0.0004
market_order_fee_side   = taker
base_funding_multiplier = 1   （直接使用 catalog 冻结的 FundingRateUpdate rate）
```

funding 输入固定为已验收数据集内 native `FundingRateUpdate` 记录的原始 `rate` 与
`ts_event`，不做插值、不做重采样、不推算下一次结算；base 场景的乘数为 1。

该组数值与 Stage 1 冻结的 `STAGE1_MAKER_FEE` / `STAGE1_TAKER_FEE` /
`STAGE1_STARTING_USDT` 连续可比，是研究基线，**不声称**代表当前 Binance USDT-M VIP0
的实际费率。阶段 4/5 若需要真实费率，必须作为新 revision 显式引入，不得就地修改。

### 3.4 有效 fee 的显式绑定与逐 fill 核验

有效 fee 必须显式绑定到 Nautilus 实际使用的 instrument 定义（`CryptoPerpetual` 的
`maker_fee` / `taker_fee`）。以下做法被禁止：

- 依赖 `BacktestVenueConfig.fee_model=None` 或任何 venue 级默认 fee model；
- 依赖**未经核验**的 instrument snapshot 默认值，即直接采用 catalog/snapshot 里的
  fee 字段而不与 base 冻结值比对；
- 用「正式窗口没有成交所以 commission 为零」代替费率接线证明。

因此正式运行必须同时满足：

1. **构造/核验**：进入回测的 instrument 其 `maker_fee` / `taker_fee` 精确等于
   `0.0002` / `0.0004`。若 catalog 或 instrument snapshot 的 fee 字段与冻结 base 值
   不一致，运行 fail closed，不得静默采用 snapshot 值，也不得静默修正为 base 值；
2. **逐 fill 核验**：每个实际 market（taker）fill 的 `commission` 等于
   `filled_qty x avg_px x base_taker_fee`（在结算货币精度内）。任一 fill 不满足即
   运行失败；
3. **确定性 fixture 证明**：一个保证发生 taker fill 的确定性 accounting fixture 必须
   证明 zero / base / 2x fee 三个场景的 commission 分别为实际 commission 的
   `0x / 1x / 2x`。该 fixture 是费率接线正确性的**唯一**证据来源。

正式研究窗口若无成交，commission 可以为零，但该状态必须与「费率未绑定/接线失败」
明确区分：无成交时 fee sensitivity 必须标记为**无信息量**（§8.3），不能作为费率接线
正确性的证据。

### 3.5 base round-trip cost threshold

模型信号的判定阈值是 base round-trip 成本底线，冻结为：

```text
base_round_trip_cost_threshold = 2 x base_taker_fee = 0.0008   （8 bps）
long  iff prediction >  +0.0008
short iff prediction <  -0.0008
flat  otherwise
```

推导式固定为「一次进场 + 一次出场，两侧都是 market order，因此按 taker 计费两次」。
funding **不进入**该阈值：funding 由持仓方向与结算时刻共同决定，在决策时刻不可知，
把它并入因果入场阈值会引入前视语义。funding 的账务影响由 Nautilus 在实际结算事件上
产生，并作为独立指标与 sensitivity 场景报告。

该阈值取自本文档冻结值，不因任何 fold 的评估结果调整（§8.2）。

### 3.6 终场规则（两个策略相同）

- 不合成 evaluation window 之外的成交。
- 窗口结束时必须撤销 open order，或明确解释其状态；不得遗留未解释的 open order。
- 允许保留由 Nautilus 最后可用 mark 估值的 position，但必须记录：instrument、
  quantity、mark 价格、unrealized PnL、估值时刻。
- 剩余 position 的估值必须来自 Nautilus，不得由研究侧另行估值。

## 4. Feature 与 Label 合同

### 4.1 固定有序 feature schema

决策 Bar 时刻记为 `t`，`C(τ)` / `H(τ)` / `L(τ)` / `V(τ)` 分别是 `ts_event = τ` 的 1h
closed Bar 的 close / high / low / volume，`M(t)` 是 `ts_event <= t` 的最新 mark 价格。

feature schema 固定为下表顺序，dtype 全部为 Float64：

| # | 名称 | 定义 |
| --- | --- | --- |
| 1 | `ret_1h` | `C(t)/C(t-1h) - 1` |
| 2 | `ret_4h` | `C(t)/C(t-4h) - 1` |
| 3 | `ret_24h` | `C(t)/C(t-24h) - 1` |
| 4 | `ret_168h` | `C(t)/C(t-168h) - 1` |
| 5 | `range_1h` | `(H(t) - L(t)) / C(t)` |
| 6 | `rv_24h` | `sqrt(sum_{i=1..24} ln(C(t-(i-1)h)/C(t-ih))^2)`，不做年化 |
| 7 | `rv_168h` | 同上，求和使用 168 个 1h log return，不做年化 |
| 8 | `volume_z_24h` | `(V(t) - mean_24) / std_24`，24 个 Bar 含 `t`，总体标准差（`ddof=0`） |
| 9 | `basis_mark_last` | `M(t)/C(t) - 1` |
| 10 | `funding_latest` | `ts_event <= t` 的最新 funding 事件的 `rate` |
| 11 | `funding_sum_24h` | `t-24h < ts_event <= t` 区间内全部 funding 事件的 `rate` 之和 |
| 12 | `hour_sin` | `sin(2*pi*h/24)`，`h` 为 `t` 的 UTC 整点小时 |
| 13 | `hour_cos` | `cos(2*pi*h/24)` |
| 14 | `instrument_code` | `BTCUSDT-PERP.BINANCE -> 0.0`，`ETHUSDT-PERP.BINANCE -> 1.0` |

名称、顺序、dtype、lookback 与 as-of 规则必须可序列化，并产生稳定的
`feature_schema_digest`。任一名称、顺序、dtype 或本文档定义的口径漂移即视为 schema
mismatch（§4.5）。`std_24 == 0` 属于非有限值情形（§4.3），不得输出 `Inf`。

每个 feature 行还必须携带真实 `decision_ts`，以及其有序 feature 向量对应的
`feature_schema_digest`。

### 4.2 as-of 与 warm-up

- 所有 feature 只使用 `ts_event <= decision_ts` 的 closed data。
- mark 与 funding 使用**同 instrument** 的 as-of join，并满足 §2.3 的年龄阈值。
- 168h 窗口必须完整：`ret_168h` 与 `rv_168h` 要求决策 Bar 本身在内、跨度恰好 168h 的
  连续 1h close 序列存在。
- 正式 evaluation 窗口必须为每个 fold 加载至少 168h 且足以计算全部 168h 窗口（含决策
  bar）的 `evaluation_start` 前置 context。消费方在 `evaluation_start` 之前只恢复
  feature state，不得发出可交易信号。
- 训练窗口头部使用同一 warm-up 规则：只作 context，不产出训练行。因此在冻结源映射
  下，声明起点为 `2020-01-01T00:00:00Z` 的训练窗口，其首个完整特征行出现在
  `2020-01-08T00:59:59.999Z`（数据集首个 1h close `2020-01-01T00:59:59.999Z` 加
  168h）。artifact manifest 的 `train_start` 记录**声明起点**
  `2020-01-01T00:00:00Z`，首个训练行另用独立字段记录（§6.2）。
- 到首个可交易 decision 时仍不 ready（缺完整 lookback、存在未批准 gap 或 stale
  as-of）时，正式运行整体失败。预期 pre-start warm-up 与异常缺数必须使用**不同且可
  测试**的状态，小型行为 fixture 可以处于 `warming_up`，但不得冒充正式 ready 输入。

### 4.3 缺失、陈旧与非有限值

以下情形 fail closed，不得静默填补、插值、前向填充或丢弃该行后继续：

- 未来值（`ts_event > decision_ts` 的 mark/funding 进入特征）；
- 超过 §2.3 年龄阈值的 mark 或 funding；
- 错误 instrument 的 mark/funding；
- 非 expected 的 mark/funding 缺失（§2.3 的两个已批准缺口除外）；
- duplicate、out-of-order；
- `NaN`、`Inf`、或任何非有限 Float64；
- schema / dtype / 列顺序漂移。

Stage 2 研究视图保留的精确字符串/Decimal 语义不改变：研究 batch 只可在通过精确源投影
校验后转换为模型所需的有限 Float64，且任何派生数据不得写回 Nautilus catalog。

### 4.4 Label 与 purge

label 固定为 decision 后下一 1h Bar open 到四小时 horizon close 的 log return：

```text
B_k = decision bar 之后第 k 个 1h Bar（按 ts_event）
label_log_return_4h = ln( close(B_4) / open(B_1) )
label_end_ts        = decision_ts + 4h
```

`label` 只用于训练，不得进入 feature。`label availability`、`label_end_ts` 必须显式
保留在每个训练行上；若 `B_1..B_4` 任一缺失，该行不可用于训练，且该处置必须显式记录而
非静默丢弃。

边界 purge 固定为：

```text
删除所有 label_end_ts >= evaluation_start 的训练行
```

该 purge 对每个 expanding-window fold 的 `evaluation_start` 独立执行，保证训练行不跨
越任一 evaluation fold 的开始时刻。

### 4.5 batch / runtime parity 与 schema mismatch

- 必须提供不依赖 Nautilus 类型的有限增量 feature state / row 合同，使后续 Strategy 能用
  相同公式消费原生事件投影。
- 在固定 fixture 上，batch 与增量 feature 的 timestamp、列顺序和数值必须处于冻结阈值
  内：`atol = 1e-12`、`rtol = 1e-9`。
- batch 与 Strategy 增量 prediction 在同一记录环境下必须处于
  `atol = 1e-12`、`rtol = 1e-9` 内。
- 训练与推理 schema 不一致（有序 feature 名称、数量、dtype、`feature_schema_digest`
  任一不匹配）时必须 fail closed：Strategy 不启动、不下单。
- 该合同是有限且领域专用的，不得扩展为通用 indicator engine 或 feature framework。

### 4.6 typed config 与路径/身份来源

- 配置**路径**可由显式环境变量提供（例如与既有 `TRACEQUANT_STAGE2_CONFIG` 同模式的
  `TRACEQUANT_STAGE3_CONFIG`）。环境变量缺失时 fail closed，不存在隐式默认路径。
- **身份与路径数值**不得来自环境默认：dataset identity、digest、`catalog_path`、
  `evidence_root`、`run_root`、窗口取值、accounting 数值都必须来自显式 typed config
  或本文档冻结值。
- typed config 只接受显式绝对 external 路径；拒绝相对路径、仓库内 fallback、`latest`
  alias、未知字段、身份冲突，以及已存在的非匹配输出分区。
- import 阶段不得执行 I/O 或读取环境变量。

## 5. 两个策略基线

两个策略共享 §3 的账户、成本、执行、终场规则，以及 §4 的 feature/label 合同和 §7 的
窗口角色；它们只在信号来源上不同。

### 5.1 传统 time-series momentum

- 信号输入：每个标的 1h closed Bar 的 24h return（即 `ret_24h`）。
- 规则：`ret_24h > +0.5%` 为 long，`ret_24h < -0.5%` 为 short，其余为 flat。
- 参数（24h、`+/-0.5%`）进入 typed config 与 manifest，但本阶段不执行任何参数搜索，
  也不按收益调整。
- 无需训练，因此不产生 model artifact。
- 与模型策略一样受 §4.2 的 warm-up 与 readiness 约束：即使策略只需要 24h lookback，
  正式窗口仍按 168h context 口径加载并执行同样的 readiness 失败语义。

### 5.2 LightGBM 模型策略

- Strategy 在 `on_start` 显式加载并完整校验 artifact（§6.4）；校验失败则不启动、不
  下单。
- 用与 batch 一致的增量 feature 合同在 closed Bar 回调中形成预测。
- 信号判定使用 §3.5 的 `base_round_trip_cost_threshold`：高于正向阈值 long、低于负向
  阈值 short、其余 flat。阈值来自配置，不得在运行中根据结果调整。
- 每个预测必须记录：artifact id、instrument、decision timestamp、有序 feature
  digest、score、冻结的 base cost threshold、target state。
- 预测非有限、schema/artifact mismatch、或出现未知 order/position 状态时不下单。
- 不得创建 model runtime gateway、Actor service、外部 inference 进程或通用 model
  adapter。

## 6. LightGBM 训练与 artifact 合同

### 6.1 固定训练配置

```text
lightgbm version      = 4.7.0（唯一首个 GBDT）
device                = CPU
num_threads           = 1（单线程）
deterministic         = true
histogram build       = force_row_wise = true（force_col_wise 不启用）
seeds                 = 全部 seed 固定并记录（含 seed / bagging_seed /
                        feature_fraction_seed / data_random_seed 等）
training interface    = 原生 LightGBM Booster regression 接口
early stopping        = 不启用
参数搜索               = 无
数据排序               = 固定
```

不引入 XGBoost、scikit-learn、pandas、PyTorch、GPU/CUDA、Optuna、MLflow 或服务型
组件。

具体数值超参数（如树数量、学习率、叶子数）由**首个 artifact revision** 固定并写入
manifest；所有 fold 与所有策略变体必须使用完全相同的超参数与 seed 集合，唯一允许的差异
是训练窗口。任何超参数变化都产生新 artifact revision，不覆盖既有证据。

### 6.2 artifact manifest 必要字段

每个 artifact 必须有一份不可变 manifest，至少包含：

- `schema` 与 artifact id；
- Stage 2 输入身份：`dataset_id`、`acceptance_digest`、`dataset_digest`、
  `source_manifest_digest`、`market_data_manifest_digest`、
  `instrument_snapshot_checksum`、`runtime_identity`（值见 §2.1）；
- 有序 feature schema（名称、顺序、dtype）与 `feature_schema_digest`；
- label 定义（`label_log_return_4h`，horizon 4h，`label_end_ts` 规则）；
- instrument ids 与 Bar type；
- `train_start`（**声明起点**）、`train_end`、首个完整训练行 `train_first_row_ts`、
  purge 边界、evaluation 窗口，以及该窗口的角色
  （`development` / `validation` / `final_test`）；
- LightGBM 参数、全部 seed、LightGBM 版本；
- Python、Polars、Nautilus 版本；
- Git SHA 与完整 `uv.lock` checksum（provenance，§6.3）；
- 相关 producer / consumer code digest、模型运行依赖子集 digest；
- model filename 与 model checksum；
- decision / sizing contract（§3.5 阈值、目标名义金额、position mode）。

artifact identity 由稳定 manifest 字段与 model checksum 推导；`created_at`、墙钟耗时、
本机绝对路径不参与业务 identity。

### 6.3 provenance 与运行兼容门禁

必须明确区分两类信息：

| 类别 | 内容 | 行为 |
| --- | --- | --- |
| provenance（追溯用） | Git SHA、完整 `uv.lock` checksum、创建时间 | 必须记录；不单独构成拒绝理由 |
| 运行兼容硬门禁 | 相关 producer/consumer code digest、有序 feature schema、模型运行依赖子集、runtime identity | 任一不匹配即拒绝 |

- 正式 artifact 生成运行必须来自可识别的干净 Git commit；Git SHA 与完整 `uv.lock`
  checksum 是不可删除的 provenance。
- 该要求只约束正式生成运行：fixture / 行为测试使用版本化的合成身份，不因交付验证阶段
  工作树尚未提交而失败。
- 无关文档提交，或只影响开发工具的 lock 变化，**不得**单独使 artifact 变得不兼容。
- loader 必须报告 provenance Git/lock 与当前 checkout 的差异，但只有在对应的**运行
  兼容** digest 也不匹配时，才据此拒绝加载。

### 6.4 不可变 evidence root、model checksum、无 pickle、loader fail-closed

- 每个训练窗口独立产出一个 **absolute external** evidence partition；artifact 只写
  显式 external `evidence_root`，不进入 Git，不覆盖非空目标，不提供 `latest` alias。
- 模型使用 LightGBM 原生可加载格式（例如 `model.txt`）。**禁止** pickle、joblib 和任何
  执行 Python 对象反序列化的格式。
- Loader 必须依次核验：manifest schema/digest → Stage 2 输入身份 → 运行兼容
  code/dependency/runtime digest → model checksum → Booster feature names 与数量 →
  预测输出有限性。
- 以下情形必须 fail closed：manifest 或 model checksum 被篡改、错误 dataset/runtime/
  compatibility digest、缺失/额外/重排 feature、Booster feature mismatch、非有限预测、
  损坏文件、目标分区非空。
- 加载与 import 不执行 I/O、不启动客户端、线程或后台服务。

### 6.5 确定性边界

- 在同一记录的 OS/architecture、同一 LightGBM binary build 和锁定环境内，固定 fixture
  的相同输入连续训练两次，model checksum、feature names 和预测（排除墙钟元数据后）
  必须一致；该环境内的非确定性漂移必须有失败测试。
- model checksum 在跨系统或不同编译器环境中**只**作为文件完整性身份，不声称相同。
- 跨环境数值等价使用书面 prediction tolerance：`atol = 1e-9`、`rtol = 1e-6`。该公差
  远小于 `base_round_trip_cost_threshold`（8e-4），因此不改变冻结环境下的 canonical
  decision。
- 同一记录环境和锁定输入上的重复 evaluation（两个不同空分区），排除墙钟字段和物理根
  路径字段后，必须得到稳定的 artifact / decision / scenario / metric / result digest。

### 6.6 传递依赖闭包与推进计划第 8 节三问

`lightgbm==4.7.0` 的基础传递依赖闭包为 `numpy`、`scipy`、`narwhals`。其 `arrow`、
`dask`、`pandas`、`plotting`、`polars`、`scikit-learn` 均为可选 extra，**不得**引入：
锁入的必须是基础闭包，且 `pandas`、`pyarrow`、`scikit-learn`、`matplotlib` 等 extra
依赖不得出现在锁文件中。

锁入时必须按推进计划第 8 节三问记录结论：

1. **当前阶段哪个退出条件没有它无法满足？** 阶段 3 需要「只选择 LightGBM 或 XGBoost
   中一个作为首个 GBDT 模型」「训练和推理 feature schema 不一致时 fail closed」
   「任一 artifact 都能追溯到数据、代码和配置」以及 OOS 单命令重建；没有 GBDT 训练与
   确定性推理能力，这些退出条件无法满足。
2. **Nautilus 是否已经提供同类能力？** 否。Nautilus 提供交易领域类型、catalog、
   Strategy 生命周期、Order/Position/Account/Portfolio、core risk、Backtest 与
   reconciliation，不提供 GBDT 训练或模型 artifact 合同。
3. **它是否会形成第二套交易领域状态？** 否。这三个库只提供数值数组、科学计算与数据帧
   互操作能力，不包含 Instrument、Order、Position、Account、Portfolio 或账务事实，
   不读取 catalog，也不产生交易状态。因此该闭包**不构成第二套市场数据或账务事实源**，
   也不改变 Nautilus 作为唯一交易与账务事实源的地位。

锁入还必须由锁文件证明闭包确实被锁入且不含被禁止依赖。若实际锁入结果与本节声明不一致，
必须作为新的文档 revision 显式记录，不得静默接受额外依赖。

## 7. 窗口角色与 fixed folds

### 7.1 expanding-window fold 定义

| fold 角色 | 训练窗口（声明起点/终点） | evaluation 窗口 |
| --- | --- | --- |
| 2022 development | `[2020-01-01T00:00:00Z, 2022-01-01T00:00:00Z)` | `[2022-01-01T00:00:00Z, 2023-01-01T00:00:00Z)` |
| 2023 development | `[2020-01-01T00:00:00Z, 2023-01-01T00:00:00Z)` | `[2023-01-01T00:00:00Z, 2024-01-01T00:00:00Z)` |
| 2024 validation | `[2020-01-01T00:00:00Z, 2024-01-01T00:00:00Z)` | `[2024-01-01T00:00:00Z, 2025-01-01T00:00:00Z)` |
| final test / OOS | `[2020-01-01T00:00:00Z, 2025-01-01T00:00:00Z)` | `[2025-01-01T00:00:00Z, 2026-09-01T00:00:00Z)` |

每个 fold 的边界都必须清除 `label_end_ts >= evaluation_start` 的训练行（§4.4）。每个
fold 通过已交付训练能力生成独立、不可变、与该训练窗口绑定的 LightGBM artifact；
evaluation 不复制训练实现，也不选择 `latest` artifact。

final test 之前必须冻结 feature schema、model 参数与 seed、base cost threshold、
sizing、account 与 execution config digest。最终 test 结果不得用于重新训练、调整阈值
或选择参数；任何后续变化产生新的 artifact / evaluation revision，不覆盖既有证据。

### 7.2 与 Stage 2 canonical split 的关系

Stage 2 已冻结的数据视图是：

```text
train      = [2020-01-01T00:00:00Z, 2024-01-01T00:00:00Z)
validation = [2024-01-01T00:00:00Z, 2025-01-01T00:00:00Z)
test       = [2025-01-01T00:00:00Z, 2026-09-01T00:00:00Z)
```

Stage 3 的 fold 角色与 Stage 2 的 split 是**两套不同语义**：

- Stage 3 的 2022 / 2023 development folds 与 2024 validation fold 在时间上**落在
  Stage 2 canonical `train` split 之内**；它们是模型训练的 expanding-window 评估角色，
  不是 Stage 2 的 canonical 窗口角色。
- Stage 3 的 final test/OOS `[2025-01-01T00:00:00Z, 2026-09-01T00:00:00Z)` 与 Stage 2
  canonical `test` split 时间一致，两者的角色语义仍然不同。

因此：Stage 3 的四个角色**不得**被统称为 Stage 2 canonical OOS，也**不得**改变、
重新推导或覆盖 Stage 2 已冻结的 split、数据集身份或 acceptance digest。本文档只在其
之上增加 expanding-window 角色。

### 7.3 warm-up 口径（所有正式窗口）

- evaluation 窗口：加载起点至少 `evaluation_start - 168h` 的 context，且足以计算全部
  168h 窗口（含决策 bar）。`evaluation_start` 之前只 warm up、不交易。
- 训练窗口：头部同样只作 context、不产出训练行（§4.2）。
- 首个可交易 decision 仍不 ready 时，正式运行整体失败。
- 冻结取值下，168h 恰好足够：对 `2022-01-01` 起的窗口，`2021-12-25T00:59:59.999Z`
  的首个 context close 正好支撑 `2022-01-01T00:59:59.999Z` 的首个完整决策。

### 7.4 #361 base run 与 #363 代表性窗口（冻结取值）

两个取值都不允许实现期自行选择，且都落在 §7.1 的 fold 语义内：

| 用途 | 窗口 | 说明 |
| --- | --- | --- |
| 传统动量 base run | `[2022-01-01T00:00:00Z, 2023-01-01T00:00:00Z)` | 2022 development fold |
| LightGBM 代表性窗口 | `[2022-01-01T00:00:00Z, 2023-01-01T00:00:00Z)` | 同一 2022 development fold |
| 代表性窗口所用 artifact 的训练窗口 | `[2020-01-01T00:00:00Z, 2022-01-01T00:00:00Z)` | 2022 fold 的 expanding-window 训练窗口，purge 见 §4.4 |

传统动量基线不训练模型，因此上表第三行只适用于模型策略；其 base run 与模型代表性
窗口的对比不需要 artifact。

选择理由（固定为该口径，实施期不得替换为其他 fold）：

- 两个早期 Task 使用**同一个 development fold**，使传统基线与模型策略的首批运行结果
  可以直接比较，直接服务于「为后续 LightGBM 策略提供同账户、同成本、同终场规则、同
  报告的基线与比较基线」这一目标；若两者使用不同窗口，该比较需要等到完整矩阵评估。
- development fold 是设计上用于开发迭代的 fold，因此 2024 validation fold 与 final
  test/OOS 的首次正式执行保留给 expanding-window 评估 Task，避免提前消耗或污染
  validation 与 OOS 证据。
- 两个窗口所需 pre-start context 均为 `2021-12-25T00:00:00Z` 起的至少 168h；首个可交易
  decision 为 `2022-01-01T00:59:59.999Z`。

## 8. 研究输出与 accounting-only sensitivity

### 8.1 base 指标（冻结口径）

每个 fold、每个策略的 base 输出必须包含：total return / PnL、Sharpe、Sortino、
max drawdown、trade count、turnover、exposure、commission、funding，以及分标的与分窗口
结果和跨 fold 稳定性（fold dispersion）。

为保证两个策略与各 fold 之间可比较，口径固定为：

- 收益序列取自 Nautilus account/result 的权益曲线，逐决策步（1h）
  `r_i = equity_i / equity_{i-1} - 1`；
- `Sharpe = mean(r) / std(r) * sqrt(24 * 365)`，`std` 使用总体标准差（`ddof=0`），
  无风险利率为 0；
- `Sortino = mean(r) / downside_deviation * sqrt(24 * 365)`，其中
  `downside_deviation = sqrt(mean(min(r, 0)^2))`，分母覆盖全部步长；
- `max drawdown` 取自同一权益曲线，报告为正的小数；
- `turnover = sum(|fill notional|) / starting_balance`；
- `exposure = mean(|position notional| / equity)`，对窗口内每个决策步取均值（gross
  exposure，允许跨标的同时持仓）；
- 跨 fold 稳定性（fold dispersion）对每个指标报告其在各 fold 上的最小值、最大值与极差。

指标只能从 Nautilus result / cache / account events 派生。研究侧不得实现第二套 PnL、
fee 或 funding ledger，也不得另行计算账户余额。

### 8.2 accounting-only sensitivity

- sensitivity **只在 final test/OOS** 执行。development / validation folds 只产出 base
  指标，不重复 sensitivity 场景。
- final test/OOS 先为两类策略各执行一次 base Strategy run，并固化 feature、prediction、
  base cost threshold 和 target-decision sequence。
- 随后分别重放**完全相同**的 target decisions，执行四个场景：zero fee、2x fee、
  zero funding、2x funding。
- 场景数值由 base 冻结值显式派生：zero fee = `0 / 0`，2x fee = `0.0004 / 0.0008`，
  zero funding = rate `x 0`，2x funding = rate `x 2`。
- 场景输入只在内存中构造，并携带显式 `scenario_digest`；不得修改或写回 canonical
  catalog，也不得改变 artifact 的 canonical feature input identity。
- 场景不得重新计算 feature、prediction、cost threshold 或 decisions。orders、fills、
  positions、account 与 funding accounting 仍由 Nautilus 产生。
- 每个场景与每个 base run 必须证明其重放的 decisions 与 base run 逐条一致；不一致即
  失败。

### 8.3 无成交与无信息量标记

- 正式 final test/OOS 中某策略若无成交，commission 在各 fee 场景下相同是允许的，但该
  场景必须标记为**无信息量**。
- 无信息量标记与「费率/ funding 接线失败」必须是两个不同且可验证的状态：前者是研究
  结论的限定，后者是运行失败。
- 场景接线正确性的证据只能来自 §3.4 的强制成交 fixture 和跨 funding event 的持仓
  fixture，不能来自正式窗口的零 commission 或零 funding 结果。

## 9. `rebuild-oos` 有限入口

- 必须提供一个有限、同步、确定性的 CLI 入口，从锁定 Stage 2 catalog 重建 Stage 3
  固定训练与评估用例。
- 只接受显式 absolute external `catalog_path`、`evidence_root`、`run_root` 与完整锁定
  Stage 2 identity；不接受环境隐式默认、仓库内 fallback、`latest` alias 或 import-time
  I/O。
- 顺序执行固定的 artifact 训练、双策略 base folds 和 final test accounting-only
  sensitivity，并生成一个 immutable evaluation manifest / summary。
- 每次调用使用**新的空** identity partition；目标非空、存在部分结果、输入/依赖 digest
  漂移或 upstream 输出不完整时失败，不静默覆盖、恢复、跳过或复用部分结果。失败恢复
  方式是人工修复后向新的空分区重跑。
- 生成紧凑 tracked Stage 3 acceptance record，路径固定为
  `docs/product/stage3-btceth-oos-acceptance.json`，schema 固定为
  `tracequant-stage3-acceptance-v1`。该记录只保存 schema、输入/artifact/config/decision/
  scenario/run/result digests、窗口及角色、指标摘要、重建命令模板和 external evidence
  相对文件名；不提交模型、完整报告、catalog、本机绝对路径或 secret。
- 该入口是**一个确定性产品用例**，不得包含 scheduler、DAG、任务队列、并发 worker、
  watcher、插件发现、自动重试、断点续跑、自动发布、自动模型选择或 Demo 晋级。

## 10. 冻结值索引

| 名称 | 冻结值 |
| --- | --- |
| 数据集 | `binance-usdm-btceth-202001-202608-r1` |
| 决策周期 | BTC/ETH 1h closed Bar（`ts_event = close time`） |
| 账务输入 | 15m mark + funding（`FundingRateUpdate`） |
| 起始余额 | `100000` USDT |
| maker / taker | `0.0002` / `0.0004`（market order 按 taker） |
| 目标名义金额 | `10000` USDT / 标的 / 方向 |
| position mode / leverage | `NETTING` / `1x` |
| `base_round_trip_cost_threshold` | `0.0008`（`= 2 x 0.0004`） |
| momentum 参数 | 24h return，`+/-0.5%` deadband |
| feature 数量与顺序 | §4.1 的 14 列，全部 Float64 |
| label | `ln(close(B_4) / open(B_1))`，`label_end_ts = decision_ts + 4h` |
| `mark_as_of_max_age` | `15m`（含） |
| `funding_as_of_max_age` | `8h` |
| 首个完整训练行（2020-01-01 起） | `2020-01-08T00:59:59.999Z` |
| feature parity 公差 | `atol = 1e-12`、`rtol = 1e-9` |
| prediction parity 公差（同环境） | `atol = 1e-12`、`rtol = 1e-9` |
| 跨环境 prediction 公差 | `atol = 1e-9`、`rtol = 1e-6` |
| LightGBM | `4.7.0`，CPU，单线程，`deterministic=true`，`force_row_wise=true` |
| 传统 base run 窗口 | `[2022-01-01, 2023-01-01)` |
| 模型代表性窗口 | `[2022-01-01, 2023-01-01)` |
| 代表性窗口 artifact 训练窗口 | `[2020-01-01, 2022-01-01)` |
| tracked acceptance record | `docs/product/stage3-btceth-oos-acceptance.json`（`tracequant-stage3-acceptance-v1`） |
| 产品状态 | `OFFLINE_BACKTEST_ONLY`、`LIVE_NOT_APPROVED` |

## 11. 验收映射

每个有限成果由对应 leaf Issue 的 Critical Outcome 测试证明；本文档不新增实现骨架：

| 能力 | 验证测试 |
| --- | --- |
| 输入绑定与因果 feature/label | `tests/acceptance/test_stage3_features.py::test_stage3_features_are_bound_to_accepted_catalog_and_causal` |
| 传统动量基线 | `tests/acceptance/test_stage3_momentum.py::test_stage3_momentum_runs_from_stage2_catalog_with_nautilus_accounting` |
| LightGBM artifact 合同 | `tests/acceptance/test_stage3_artifacts.py::test_stage3_lightgbm_artifact_round_trips_with_locked_identity` |
| 模型 Strategy 接入 | `tests/acceptance/test_stage3_model_strategy.py::test_stage3_lightgbm_strategy_predicts_and_trades_through_nautilus` |
| expanding-window 与 sensitivity | `tests/acceptance/test_stage3_evaluation.py::test_stage3_evaluation_compares_both_strategies_with_accounting_only_sensitivity` |
| `rebuild-oos` 与 tracked record | `tests/acceptance/test_stage3_oos.py::test_stage3_oos_rebuild_compares_both_strategies_from_one_catalog` |

测试 fixture 只证明行为与失败边界，不得冒充正式 2020–2026 研究证据。

## 12. 完成、结论与准入的区分

| 层级 | 判定依据 | 本文档的态度 |
| --- | --- | --- |
| Stage 3 软件能力完成 | §11 的自动化证据与可复现记录 | 不以任何收益阈值作为出口条件 |
| 策略研究结论 | base 指标、回撤、换手、费用/funding 敏感度与跨 fold 稳定性 | 结论可以是负收益或模型弱于传统基线，不阻止软件能力验收 |
| 进入 Demo 的准入 | 人工决策，属于后续阶段 | 本文档不提供任何自动晋级机制 |

阶段出口只依据自动化证据与可复现记录，不依据 notebook 截图或人工印象。

## 13. Non-goals

阶段 3 明确不实现：

- scheduler、DAG、任务队列、并发 worker、watcher、插件发现、自动重试、断点续跑、
  自动发布或任何自动化流程系统；
- MLflow、Optuna、Prefect、Dagster、XGBoost、scikit-learn、pandas、PyTorch、
  GPU/CUDA、HPO 或实验跟踪；
- model registry server、model runtime gateway、serving API、通用 model-to-runtime
  middleware、通用 strategy adapter、通用 replay 引擎；
- feature store、研究数据平台、第二套 catalog、第二套市场数据或派生市场数据副本；
- 自研交易领域对象、回测引擎、Portfolio、Account、费用或 funding ledger；
- Demo/Live 执行、凭据配置或自动 Demo 晋级；
- 修改 Stage 2 数据集身份、数据内容、时间切分、acceptance digest 或 Nautilus 版本；
- 在本阶段引入 index、1m mark、新数据集 revision、stop/limit/trailing 订单、组合优化
  或动态风险预算。

## 14. 阶段退出条件

以下条件全部满足时阶段 3 完成（软件能力层面）：

```text
[ ] Stage 3 输入同时通过 catalog identity 与 tracked 数据集验收绑定，fixture 或部分 catalog 会失败
[ ] BTC/ETH 1h closed Bar 决策、15m mark 与 funding 账务输入、NETTING、1x、共同 sizing、
    next-Bar execution 对两个策略完全一致
[ ] feature 只读取 decision_ts 及以前的数据；label 只用于训练；边界清除 label 跨界行
[ ] 正式窗口加载足够的 pre-start context，起点前只 warm up、不交易；首个可交易 decision
    不 ready 时整体失败
[ ] 两个策略产生同一 catalog、同一 Nautilus account 与 execution 语义下的 base 结果
[ ] 每个实际 base taker fill 的 commission 与冻结费率一致；强制成交 fixture 证明
    zero/base/2x fee 的 0x/1x/2x commission
[ ] 模型 artifact 绑定数据、feature schema、label、训练/purge/evaluation 窗口、代码
    provenance 与运行兼容 digest、model checksum，且 loader 对篡改与 schema mismatch fail closed
[ ] expanding-window 的 2022/2023 development、2024 validation、final test/OOS 按固定边界执行
[ ] final test/OOS 执行 accounting-only zero/2x fee 与 zero/2x funding 场景，decisions 与 base 逐条一致
[ ] 研究输出包含收益、回撤、风险调整指标、交易数、换手、commission、funding、分标的/
    分窗口与跨 fold 稳定性
[ ] 两个策略使用相同终场规则：无合成窗口外成交，open order 已撤销或解释，剩余 position
    由 Nautilus 最后可用 mark 估值并记录
[ ] 同一记录环境与锁定输入内的重复重建产生稳定 digest；跨环境只要求文件完整性与书面公差
[ ] rebuild-oos 入口有限、同步、确定性，可从空 external roots 完整重建并生成 tracked record
[ ] 仍为 OFFLINE_BACKTEST_ONLY、LIVE_NOT_APPROVED，无自动化流程系统、模型网关、registry、
    feature store 或第二套交易领域状态
[ ] 负收益或模型弱于传统基线不阻止软件验收，也不自动批准 Demo
```
