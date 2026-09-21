# TraceQuant 阶段 4：Binance Demo 实施与验收基线

| 字段 | 值 |
| --- | --- |
| 文档状态 | 已批准实施基线 |
| 文档版本 | `v1.0` |
| 日期 | `2026-09-21` |
| Feature | [#384](https://github.com/PhoenixSss/tracequant/issues/384) |
| 固定运行时 | NautilusTrader `2.0.0rc4` / `a0400251110653b6d8ae6a9b5b89c4543fa85a2d` |
| 唯一环境 | Binance USD-M Futures Demo |
| 产品状态 | `DEMO_ONLY`、`LIVE_NOT_APPROVED` |

本文档是 Feature #384 的唯一阶段 4 实施与验收基线。#386–#391 只能实现
§10 分配给自己的编号要求；它们可以消费前置叶项的产物，但不得重新解释、复制所有权或
增加交付义务。任何新增场景、环境、instrument、账户模式、基础设施、持续运行能力或
第三方依赖都是独立范围变更，必须先获得 maintainer 明确批准并修订本文档版本。

本基线细化
[分阶段推进计划](<../research/foundation-selection/TraceQuant 分阶段推进计划.md>) 的阶段 4，
并服从 [ADR-0001](../architecture/adr-0001-nautilustrader-primary-runtime.md) 的 Nautilus
领域所有权。它不升级到 rc5，也不接受兼容版本范围。

## 1. 目标与唯一支持边界

阶段 4 只证明一个有界基础闭环：官方 DataTester 行情验收、官方 ExecTester 安全子集、
TraceQuant 最小 Demo Strategy，以及最终证据聚合和发布。成功不证明恢复性、长期稳定性
或 Live 可用性。

### 1.1 冻结选择

| 项目 | 唯一选择 | 明确拒绝 |
| --- | --- | --- |
| Runtime | NautilusTrader `2.0.0rc4`，commit `a0400251110653b6d8ae6a9b5b89c4543fa85a2d` | rc5、未锁定版本、运行时身份漂移 |
| Venue / product | `BINANCE` / `BinanceProductType.USD_M` | Spot、COIN-M、Margin、其他交易所 |
| Environment | `BinanceEnvironment.DEMO` | 默认 Live、`LIVE`、legacy `TESTNET`、自定义 endpoint |
| Instrument | `BTCUSDT-PERP.BINANCE`；provider 使用 `load_all=False` 和唯一 `load_ids` | 第二个 instrument、全量加载、动态 allowlist |
| Account | 一次运行只使用一个 Demo account；证据只保存不可逆 account reference digest | 第二个 account、完整 account ID、Live account |
| Position mode | one-way；Nautilus 使用 `OmsType.NETTING` | hedge / dual-side |
| Margin / leverage | `BTCUSDT` 为 `BinanceMarginType.ISOLATED`、`1x` | cross、动态 leverage、其他 symbol 设置 |
| Active order cap | 全局最多 `1` 个活动或 acknowledgement 未决订单 | batch、并行订单、unknown 时新订单 |
| Quantity | 运行时 instrument constraints 允许的一个最小有效订单量 | 硬编码数量、放大 sizing、绕过 minimum/precision/notional |
| Credentials | `BINANCE_DEMO_API_KEY`、`BINANCE_DEMO_API_SECRET` | 通用 Live/Testnet 变量、文件内 secret、命令行 secret |

`base_url_http`、`base_url_ws` 和 `base_url_ws_trading` 必须为 `None`；由 rc4 adapter 根据
`DEMO` 选择官方 endpoint。任何 URL override 即准入失败。DataTester 不创建 execution
client；需要认证的入口只允许 adapter 从上述两个 Demo 专用环境变量取得凭据，TraceQuant
配置对象、日志和 evidence 不得复制其值。

### 1.2 账户模式设置与确认

rc4 的公开 execution config 必须同时设置：

```text
oms_type = OmsType.NETTING
futures_margin_types = {"BTCUSDT": BinanceMarginType.ISOLATED}
futures_leverages = {"BTCUSDT": 1}
```

rc4 没有一个公开 typed 查询面能在客户端创建前统一证明 venue 上的 one-way、isolated 和
1x。阶段 4 因此采用唯一的 pre-run operator gate：操作者在 Binance Demo UI 中确认这三项，
然后为**本次** order-enabled 运行显式提供固定令牌
`ONE_WAY_ISOLATED_1X_CONFIRMED`。令牌不是持久授权，不得默认、缓存或跨运行复用；缺少或
不精确匹配时必须在创建 network client 前失败。客户端启动后仍由上述 rc4 config 主动设置
isolated 和 1x；任一设置被拒绝或无法确认成功，本次运行进入 `HALTED`，不得提交订单。
不得为读取或设置这些值创建 raw Binance REST client。

### 1.3 最小有效数量

每个 order-enabled 场景都必须从本次运行加载的 Nautilus instrument 计算数量，不接受配置
中的静态 quantity。设 `step` 为 size increment，`min_qty` 为 minimum quantity；若存在
`min_notional`，用提交前最新且未 stale 的同侧可成交价格计算满足 notional 的最小 step 倍数。
最终 quantity 是同时满足 `step`、`min_qty`、precision 和 `min_notional` 的最小值，并由
Nautilus instrument 的 quantity 构造/校验路径生成。缺少 constraint、缺少有效价格或无法得到
唯一最小值时停止；不得猜测或扩大为 portfolio sizing。这里的“最新且未 stale”只能按 §3.1
判定，quantity 计算和紧接其后的提交必须使用同一份合格 quote snapshot。

## 2. 稳定行为要求

| ID | 冻结要求 |
| --- | --- |
| `ST4-REQ-001` | 每个入口在 import/config validation 阶段验证精确 rc4 version、commit identity 和 dependency lock digest；身份不符时不得创建客户端。 |
| `ST4-REQ-002` | 唯一环境是 Binance USD-M Futures Demo，endpoint 不可覆盖，凭据只来自两个 Demo 专用变量。 |
| `ST4-REQ-003` | 唯一 instrument、单 account、one-way、isolated、1x、最多一个活动/未决订单是不可配置边界。 |
| `ST4-REQ-004` | order-enabled 入口执行 §1.2 的一次性 operator gate，并通过 rc4 config 主动设置 account mode；设置或确认失败即关闭。 |
| `ST4-REQ-005` | order quantity 严格按 §1.3 计算为一个最小有效量；所有 order-enabled 入口共用 §3.1 的 price freshness 和校验合同。 |
| `ST4-REQ-006` | 所有网络和订单阶段使用 §3 的唯一 deadline；v1.0 不提供 timeout override。 |
| `ST4-REQ-007` | 核对只读取 §8 的 entry-specific rc4 公开观察面：ExecTester 使用 `LiveNode.cache` 可检索 object graph，Strategy 使用 callbacks/cache/account snapshots；不得要求取得 rc4 公共 Python API 不返回的 tester 或 report objects。TraceQuant 不拥有第二套 adapter、order、position、ledger、accounting 或 reconciler。 |
| `ST4-REQ-008` | DataTester 只执行 §4.1 的数据场景，不创建 execution client。 |
| `ST4-REQ-009` | ExecTester 只执行 §4.2 的独立安全子集，特殊 risk bypass 不得进入普通 Strategy。 |
| `ST4-REQ-010` | Demo Strategy 只执行 §4.3 的固定顺序，不读取阶段 3 模型、不产生 alpha、不做 portfolio sizing。 |
| `ST4-REQ-011` | Strategy 只使用 §5 的固定前进状态；终态只有 `COMPLETE` 或 `HALTED`，不得抽象为可配置工作流引擎。 |
| `ST4-REQ-012` | 最终验收按 §4.4 从全新外部分区依次消费 DataTester、ExecTester、Strategy 证据，并在同一 identity 下聚合。 |
| `ST4-REQ-013` | 成功矩阵只包括 data、market complete fill、passive limit accepted/canceled、long/short、reduce-only close 和最终清场。 |
| `ST4-REQ-014` | 阶段 4 完成后停止扩展并保持 `LIVE_NOT_APPROVED`；§11 的能力只能由阶段 5 或以后承接。 |

## 3. 唯一 deadline

所有 deadline 使用 monotonic clock，自触发动作写入本地 command/event queue 后开始计算；
达到边界即超时，不因日志、心跳或无关事件续期。v1.0 不允许 CLI、环境变量或配置文件覆盖。

| 阶段 | Deadline | 成功事件 |
| --- | ---: | --- |
| network connect | 30 秒 | 所需 data client（以及 order-enabled 场景的 execution client）均连接 |
| ready | 60 秒 | instrument 已加载、订阅活动，且 order-enabled 场景完成账户准入并取得 §3.1 合格 quote |
| DataTester observation | 60 秒 | 同时取得有效 quote、trade、instrument identity/constraints 和时间戳样本 |
| order price readiness | 10 秒 | 取得 §3.1 合格 quote，并以同一 snapshot 完成 quantity/passive price 计算和提交前复核 |
| order acceptance | 10 秒 | 唯一订单收到确定 `OrderAccepted` 或确定 reject |
| market / reduce-only fill | 30 秒 | 订单完整成交；partial fill 不延长 deadline |
| cancellation | 10 秒 | cancel 被确定确认，或 race 经后续 §8 entry observation profile 确定为 fill |
| reconciliation | 30 秒 | 所需 §8 entry observation profile 完成分类 |
| cleanup | 60 秒 | 无活动/未决订单、无持仓、无 unresolved unknown |

连接或 ready 超时不允许提交订单。order acceptance 超时是 ambiguous acknowledgement，不能
被当成 reject；只能进入 reconciliation/安全清场。任何 cleanup deadline 到期都保持
`HALTED`，不得发布成功记录。

### 3.1 Order-enabled price freshness

ExecTester、Strategy 以及 reduce-only cleanup 使用同一规则。合格 quote 必须属于
`BTCUSDT-PERP.BINANCE`，具有非空 bid/ask，`ts_event` 不得比本地 UTC wall clock 快超过 1 秒，
检查时的 `now - ts_event` 不得超过 5 秒，并且 `ts_event` 不得早于本次 run 对该 quote stream
已经接受的最后一个非重复 timestamp。年龄比较使用检查瞬间的 wall clock；10 秒等待 deadline
使用 monotonic clock，两者不能互相替代。

order-enabled ready 必须先取得一份合格 quote。此后每一次正常场景订单或 reduce-only cleanup
提交前都重新启动一次 10 秒 order price readiness deadline；在 deadline 内取得的新合格 quote
同时用于：buy 的 `min_notional` ask、sell 的 `min_notional` bid、§4.2/§4.3 passive buy 的 best bid，
以及 quantity/price 构造后的立即提交前复核。复核时 snapshot 年龄仍须不超过 5 秒且期间未观察到
更新但倒退的 timestamp；不满足就丢弃该 snapshot 并在原 10 秒 deadline 内等待下一条，不能重新
启动 deadline。

deadline 到期、future/stale/out-of-order、缺少所需 bid/ask 或 instrument identity 不匹配时，本次
运行进入 `HALTED`。对正常场景禁止提交；对 cleanup 也不得凭旧价格猜测提交，必须保留已证明的
exposure 和 `cleanup_incomplete` 诊断交给操作者。不得用最近一次 DataTester run、ready 时缓存但在
提交时已过期的 quote、trade price、mark price 或 wall-clock sleep 代替本规则。

## 4. 有限场景矩阵

### 4.1 官方 DataTester

使用 rc4 官方 `DataTesterConfig`，或只负责组装冻结配置和输出证据的薄封装。唯一计划是：

1. 以 `load_all=False` 加载 `BTCUSDT-PERP.BINANCE`，启动 USD-M Demo data client；
2. 订阅该 instrument 的 quotes 和 trades，并请求/观察 instrument；
3. 在 60 秒 observation deadline 内至少取得一条有效 quote 和一条 trade；
4. 记录 instrument identity、price precision/increment、size precision/increment、minimum
   quantity、可用时的 minimum notional，以及各流 `ts_event`/`ts_init`；
5. 证明事件属于 allowlist，`ts_event` 非未来超过 1 秒、接收时年龄不超过 5 秒，且同一
   stream 的非重复事件时间不倒退；然后停止。

前置条件是 `ST4-REQ-001/002/003/006` 准入通过。成功终态是 data evidence
`COMPLETE`；连接/订阅超时、空数据、错误 instrument、缺少 constraints、stale 或
out-of-order timestamp 均为 `HALTED`。此入口没有 execution config、order 或账户清场动作。

### 4.2 官方 ExecTester 安全子集

使用 rc4 官方 `ExecTesterConfig`，或只负责冻结配置、逐项选择场景和输出证据的薄封装。
入口必须要求显式 `--order-enabled` 意图以及 §1.2 的精确 operator token。唯一顺序是：

1. 账户、instrument 和 constraints ready，且 §8 的公开 cache/account snapshots 证明无活动订单、无持仓；
2. 以一个最小有效量提交 market buy，等待完整成交；
3. 对实际 long quantity 提交 reduce-only market sell，等待完整成交并证明 flat；
4. 以一个最小有效量在当前 best bid 低一个 price increment 处提交 post-only limit buy；
5. 等待确定 accepted，随后 cancel，并等待确定 canceled；
6. reconcile 并 cleanup，证明无活动/未决订单、无持仓、无 unknown。

rc4 的 `LiveNode.add_builtin_strategy("ExecTester", config)` 返回 `None`，也没有公开 strategy
getter；薄封装不得声称持有官方 tester，不能读取它的 handler callback 参数。订单动作仍完全由
官方 ExecTester 执行。薄封装只在每个场景边界按 §8.1 从 `LiveNode.cache` 读取公开的 Nautilus
order/position/account object graph 并输出证据；它不提交订单、不订阅 private message bus、不解析
日志，也不创建第二套 adapter、ledger 或 reconciler。

场景边界由薄封装只读轮询该 cache graph 并在既有 deadline 内识别：market leg 必须出现唯一新增、
完整 filled 的 market order；reduce-only leg 必须出现唯一新增、完整 filled 的 reduce-only order 且
`positions_open()` 为空；passive leg 必须出现唯一新增、先 accepted 后 canceled 且没有 `OrderFilled`
的 post-only limit order。任何 deadline 到期、多余/无法归属的新增 order、缺失 event history 或
不相容 terminal fact 都进入 §8 的非 `consistent` 分类；这些 predicate 不提供下单能力，也不复制
tester 状态机。

只有该独立入口可以使用官方 tester 所需的最小 risk bypass；能力必须在入口内封闭且不能由
Strategy import、配置或调用。post-only order 若 reject、partial fill 或在 cancel 前/期间成交，
场景立即判失败；若形成 position，只能按实际可证明 quantity 做 reduce-only cleanup。不得为了
让场景通过而改成 aggressive limit、重发或扩大 tester 功能。

### 4.3 TraceQuant 最小 Demo Strategy

Strategy 只通过 Nautilus Strategy/order API，按以下顺序前进：

```text
ready
  -> market long complete fill
  -> reduce-only long close -> flat
  -> market short complete fill
  -> reduce-only short close -> flat
  -> passive post-only limit buy accepted
  -> cancel confirmed
  -> reconcile
  -> cleanup
  -> COMPLETE
```

passive price 与 §4.2 相同：提交时 best bid 低一个 price increment。任一步出现 reject、
partial/late fill、ambiguous ACK、duplicate/out-of-order event、状态冲突或 timeout，成功路径
立即停止并进入 `HALTED` 安全清场。Strategy 不读取阶段 3 artifact，不接受 signal、target
position 或 sizing 参数。

### 4.4 最终证据聚合与发布

在同一 source commit、dependency lock、rc4 runtime 和 frozen config identity 上，使用三个
全新且初始为空的 repo 外分区依次运行 DataTester、ExecTester 和 Strategy。聚合器只验证既有
证据，不创建客户端或订单。所有 required scenario 为 `PASS`、所有 identity/digest 一致且最终
order/position/unknown 清场后，才可原子发布
`docs/product/stage4-binance-demo-acceptance.json`。任何失败不得创建新成功文件，也不得覆盖
既有成功文件。

## 5. Strategy 固定状态

这些是阶段专用常量，不是通用状态机：

```text
ADMITTING -> READY
READY -> WAIT_LONG_ACCEPT -> WAIT_LONG_FILL
WAIT_LONG_FILL -> WAIT_LONG_CLOSE_ACCEPT -> WAIT_LONG_CLOSE_FILL
WAIT_LONG_CLOSE_FILL -> WAIT_SHORT_ACCEPT -> WAIT_SHORT_FILL
WAIT_SHORT_FILL -> WAIT_SHORT_CLOSE_ACCEPT -> WAIT_SHORT_CLOSE_FILL
WAIT_SHORT_CLOSE_FILL -> WAIT_PASSIVE_ACCEPT -> WAIT_PASSIVE_CANCEL
WAIT_PASSIVE_CANCEL -> RECONCILING -> CLEANING -> COMPLETE
any non-terminal state -> HALTED
```

只有收到与当前唯一 order identity、expected side、quantity 和 state 都一致的事件才能前进。
`HALTED` 是不可逆 latch：禁止任何新开仓；只允许对已知 order 发出一次可证明安全的 cancel、
发出一次公开 `query_order`/`query_account` 刷新命令并等待正常 callback/cache 更新，或按已证明的
非零 position 发出一次 reduce-only cleanup。
这些动作不能把本次结果改回 `COMPLETE`。

## 6. 失败关闭矩阵

| 情况 | 事件前置状态 | 允许动作 | 禁止动作 | 终态 | 最小证据 |
| --- | --- | --- | --- | --- | --- |
| connect / subscription timeout | 尚未 ready | 停止 client，写 timeout | 创建 execution client（DataTester）或提交订单 | `HALTED` | deadline、已连接组件、缺失 subscription |
| 空数据 | DataTester observation 到期 | 停止并保留订阅统计 | 把连接成功当行情成功 | `HALTED` | quote/trade counts 均或任一为零 |
| stale / out-of-order timestamp | 正在观察对应 stream | 停止场景；无订单时直接结束 | 忽略、重排后伪装成功 | `HALTED` | stream、前后 timestamp、age |
| order reject | 等待 acceptance | 记录确定 reject；若已有 exposure 则安全清场 | 修改参数并重发 | `HALTED` | client order reference、reason、cache status |
| ambiguous acknowledgement | 等待 acceptance 超时或 transport unknown | Strategy 最多发出一次公开 `query_order` 并等待其 profile 更新；ExecTester 不代替官方 tester 发 command，只在 reconciliation deadline 内等待 cache graph 收敛；确定已知 order 后可 cancel | 把 query 的 `None` 返回值当 report、当作 reject、用新 ID 重发、继续开仓 | `HALTED` | command/entry identity、timeout/transport fact、后续 entry-profile result |
| duplicate / out-of-order event | 任一 order state | 忽略重复的状态推进并 reconcile；记录冲突 | 二次推进、二次提交 | `HALTED` | event identity、expected/observed state |
| cancel/fill race | 等待 cancel | reconcile；若 fill 已发生则按实际 position reduce-only cleanup | 同时假定 canceled 和 filled、重发 cancel/order | `HALTED` | §8 entry profile 的 cancel/fill facts、最终 position |
| late fill | 已收到 cancel 或已开始 reconcile | 更新 Nautilus-owned事实并按实际 exposure 清场 | 保持原 flat 假设、发布成功 | `HALTED` | late fill identity、先前状态、cleanup result |
| unexpected partial fill | 等待任一完整 fill | 停止成功路径；按 §8 entry profile 已证明的 cumulative quantity 有限清场 | 等待/补单凑满、实现通用 partial-fill workflow | `HALTED` | ordered/cumulative/leaves quantity、entry-profile observations |
| handler exception | 任一实际使用的 Strategy handler | 顶层保护记录 fatal diagnostic、锁住提交、进入安全清场 | 吞掉异常、继续状态推进 | `HALTED` | handler、异常类型/脱敏信息、state、submission count |
| observation conflict | reconciliation / cleanup | 保留 §8 entry profile 的 Nautilus facts 和 `conflicting` 分类 | 选择有利事实、用 raw REST 裁决 | `HALTED` | observation digests、冲突字段 |
| cleanup timeout | `CLEANING` 或 `HALTED` cleanup | 停止自动动作，保留外部诊断 | 标记 flat、发布成功、无限重试 | `HALTED` | active orders、position、unknown、deadline |

### 6.1 rc4 handler exception 防护

upstream #5039 报告 rc4 的 Python Strategy handler exception 可能在 pyo3/Rust 边界被静默
吞掉。阶段 4 所有实际使用的 order/position handlers 必须在最外层 `except Exception`，在异常
离开 Python handler 前同步完成三件事：写入脱敏 fatal diagnostic、设置不可逆 submission
inhibit latch、转入 `HALTED`。不得依赖 runtime 自动打印或停止 node。

#390 必须有一个阶段专用、仅离线测试可启用的确定性 fault injection，在一个实际 handler body
抛出异常，并证明 diagnostic 存在、终态为 `HALTED`、异常后 submission count 不增加。该注入点
不得成为 plugin/hook registry，联网配置必须拒绝启用它。

## 7. 安全要求

| ID | 冻结要求 |
| --- | --- |
| `ST4-SAFE-001` | 非 Demo、越界、身份不完整、缺少 operator token 或 secret 来源错误，必须在 network client 创建前失败。 |
| `ST4-SAFE-002` | `missing`、`conflicting`、`unknown` 或 `cleanup_incomplete` 都是失败，不得降级为 warning 或成功。 |
| `ST4-SAFE-003` | DataTester 对空数据、错误 identity、stale/future/out-of-order timestamp 失败关闭。 |
| `ST4-SAFE-004` | ExecTester bypass 仅限独立入口；普通 Strategy 不能 import 或配置该能力。 |
| `ST4-SAFE-005` | ExecTester 的 reject、ambiguous ACK、unexpected/partial fill 和 cancel/fill race 禁止盲目重发，并执行有界安全清场。 |
| `ST4-SAFE-006` | Strategy 对 ambiguous、duplicate、out-of-order、timeout 和 conflict 设置不可逆 `HALTED` latch，禁止新开仓。 |
| `ST4-SAFE-007` | partial fill、late fill 或 passive limit 成交必定使验收失败；只按 Nautilus 已证明 exposure 有限清场。 |
| `ST4-SAFE-008` | 所有实际 handler 具有 §6.1 顶层保护，并以确定性 fault injection 证明异常可观察且停止后续提交。 |
| `ST4-SAFE-009` | 只有 §8 对当前入口冻结的 observation profile 证明无活动/未决订单、无持仓、无 unknown 才可 `COMPLETE`；否则保持 `HALTED`。 |

## 8. 状态核对合同

每类事实按以下互斥结果分类。分类输入只能来自同一 run identity 下、rc4 公共 Python API
直接暴露、并由当前入口冻结的 Nautilus-owned observation profile。

### 8.1 入口 observation profiles

- **ExecTester profile**：唯一观察面是 `LiveNode.cache` 的公开 object graph。薄封装以冻结的
  instrument、ExecTester `strategy_id`/order tag 和本次场景开始时的 ID 集合为边界，读取新增的
  `orders()`/`orders_open()`/`orders_closed()`/`orders_inflight()`、`positions()`/
  `positions_open()`/`positions_closed()` 与 venue account。Order/Position 的公开 `events()`、
  order 的 terminal/cumulative/leaves/average-price/commission facts，以及 Account 的公开
  `events()`/`last_event()` 都属于这一个可检索 cache object graph。它们不是 tester callbacks，
  也不得被表述成相互独立的第二 observation。
- **Strategy profile**：`on_order_*`、`on_order_filled`、`on_position_*` callbacks 收到的不可变
  事件，加上 `Strategy.cache` 中同一 order/position/account 的公开对象和查询结果；account 的
  运行前后状态来自公开 `Account.events()`/`Account.last_event()`。
- **DataTester profile**：只使用 §4.1 的 quote/trade/instrument observations；order、fill、position
  和 balance 明确为 `not_applicable`。

ExecTester 的场景 collector 必须在运行前保存上述过滤范围内的 ID 集合和 account snapshot，场景
结束后只接受新增且能由配置中的 frozen strategy/order identity 唯一归属的对象。ID 归属不唯一、
closed object 已被 purge、event history 缺失或不能把对象唯一绑定到当前场景时为 `missing` 或
`unknown`，不得扫描后猜测。相同 cache object 的前后两次读取只表示状态演进，不是两份独立证据；
ExecTester 的 `consistent` 判定是该单一 object graph 内 required facts 的完整性和不变量成立，不要求
无法取得的 callback-side 副本。

rc4 的 `Strategy.query_order` 和 `Strategy.query_account` 是返回 `None` 的 command 方法；它们只可在
ambiguous/cleanup 路径各触发一次有界刷新，证据仍必须来自随后到达的 callback 事件或 cache/account
状态。rc4 虽公开 `OrderStatusReport`、`FillReport`、`PositionStatusReport` 类型，但 Strategy、
LiveNode 和 Cache 没有返回这些 raw report objects 的公共 accessor/callback。因此阶段计划中的
“cache/report”在本锁定版本内不得被解释为必须取得这些对象，也不得使用 Rust execution-client
内部、private message bus 或 raw Binance REST 补足它们。

任一 selected profile 要求的 callback、cache query、object event history 或运行前后 `AccountState`
在 deadline 内不可获得时，按 `missing` 或 `unknown` 失败关闭；不能把 command 调用成功、日志、
同一 cache 对象的两次读取或本地反向合成对象当作额外 observation。这个规则既不把 rc4 缺少的
raw report accessor 或 ExecTester callback getter 设为成功前提，也不降低 required fact 缺失时的
失败语义。

| 分类 | 定义 |
| --- | --- |
| `consistent` | 当前入口 profile 的 required facts 均存在；ExecTester cache graph 的内部不变量成立，或 Strategy callback 与 cache/account facts 在 Nautilus 原生 precision 内一致。 |
| `missing` | 当前入口 profile 要求的任一 event、object、snapshot 或字段不存在。 |
| `conflicting` | 当前入口 profile 内的 Nautilus-owned facts 对同一 identity、状态或数值不相容。 |
| `unknown` | acknowledgement 未决、观察不完整、无法解析，或事实不能从上述公开 surface 确定。 |
| `cleanup_incomplete` | 任一活动/未决订单、非零 position 或 unresolved unknown 仍存在。 |

具体核对最小集合：

- **Order**：ExecTester 使用 cache order 的公开 event history 和 terminal facts；Strategy 使用
  `OrderAccepted`/`OrderCanceled`/`OrderFilled` callback sequence 并与 cache order 核对。两者都必须
  绑定 client/venue order reference、instrument、side、type、quantity、reduce-only/post-only、
  cumulative/leaves quantity、时间和最终状态；
- **Fill**：ExecTester 从 cache order 的公开 `events()` 选择真实 `OrderFilled`，并核对其 trade
  reference 唯一性、order reference、side、last quantity、price、commission、liquidity side、order
  cumulative quantity/average price 及 position/account 变化；Strategy 对 callback 中的同一事实与
  cache 核对。两者都不得从汇总字段反向合成“独立 fill report”；
- **Position**：ExecTester 使用 cache position 的公开 `events()`、quantity/side/realized PnL 和
  `positions_open()`/`positions_closed()`；Strategy 使用 `PositionOpened`/`PositionChanged`/
  `PositionClosed` callbacks 并与这些 cache facts 核对。两者都以本场景真实 `OrderFilled` 的
  side/last quantity 做有界净额校验；该运行内算术不是持久 ledger。成功清场必须净额为零、position
  已 closed 且 cache 无 open position；
- **Balance**：比较同一公开 Account 对象中运行前后的 `AccountState` snapshots，并验证变化能由当前
  entry profile 中真实 `OrderFilled.commission` 与 position realized PnL 在 native currency precision
  内解释。ExecTester 的最终 snapshot 必须晚于最后一个场景 terminal event；Strategy 的最终 snapshot
  必须在有界 `query_account` 之后更新。运行跨过 funding、snapshot 未更新/缺失或出现其他无法解释
  变化时为 `conflicting`，不得增加 funding ledger；
- **Cleanup**：cache 的 open/in-flight order counts、open positions 和当前 entry profile 的 terminal
  facts 同时为零/closed，且无 deadline 或其他 unresolved unknown。单独的 balance 一致不能证明清场。

## 9. 证据合同

### 9.1 `tracequant-stage4-demo-evidence-v1`

每次 DataTester、ExecTester 或 Strategy 运行生成一份 canonical JSON。除枚举外的字符串必须
非空；digest 使用小写 SHA-256；时间固定为 UTC `YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ`（九位小数、
只允许 `Z`）。所有 `run_id`、`evidence_digest`、
`acceptance_digest` 和 §9.3 frozen config digest 共用以下唯一 canonical-byte 规则：

1. 输入先按对应 v1 schema 验证；所有 schema-declared 字段必须存在，额外字段拒绝。只有 schema
   明确声明 nullable 的字段才可为 JSON `null`；不得用 omission 代替 `null`。`not_applicable` 是
   schema 声明的枚举字符串，不能与 `null` 或 omission 互换。解析时拒绝重复 object member name；
   key 经 NFC 规范化后若发生碰撞也拒绝。
2. 所有字符串必须是有效 Unicode scalar values 并规范化为 NFC；lone surrogate 拒绝。数字只允许
   JSON integer；price、quantity、money、fee 和其他 decimal 必须先按 Nautilus native precision
   量化，再编码为不带指数、无多余前导零、零固定为 `"0"` 的十进制字符串。JSON float 拒绝。
3. 对象 key 按 Unicode code point 升序递归排序；array 保持 schema 定义或事件发生顺序。序列化使用
   UTF-8、无 BOM、`ensure_ascii=false`、`,`/`:` 分隔且无其他 whitespace，末尾无换行。`"` 和 `\`
   分别转义为 `\"` 和 `\\`，`/` 不转义；U+0008/U+0009/U+000A/U+000C/U+000D 分别使用
   `\b`/`\t`/`\n`/`\f`/`\r`，其他 U+0000–U+001F 使用小写 hex 的 `\u00xx`。布尔值和 `null`
   使用 JSON 小写字面量。
4. digest 是上述精确 bytes 的 SHA-256 小写 64 位 hex。`run_id` 投影只删除顶层 `run_id` 和
   `evidence_digest`；写入派生 `run_id` 后，`evidence_digest` 投影只删除顶层 `evidence_digest`。
   `acceptance_digest` 投影只删除顶层 `generated_at` 和 `acceptance_digest`。字段删除后不得用
   `null` 占位，也不得删除任何其他字段。frozen config digest 直接 hash 不含自引用 digest 字段的
   完整 frozen config payload，不做其他投影。

#387 必须提交至少一个包含非 ASCII/NFC、nullable、decimal string 和 nested key ordering 的 evidence
golden vector，固定 canonical UTF-8 bytes、`run_id` 与 `evidence_digest`；#391 必须提交 acceptance
golden vector，证明 `generated_at` 改变不改变 `acceptance_digest`，任一其他输入改变会改变 digest。
schema 只允许以下顶层事实：

| 字段 | 内容 |
| --- | --- |
| `schema` | 固定 `tracequant-stage4-demo-evidence-v1` |
| `run_id` | 由本记录稳定 payload 导出的不可逆 ID |
| `source_commit` | 运行源码 commit |
| `dependency_lock_digest` | `uv.lock` digest |
| `runtime` | 固定 version、commit 和可验证 runtime identity digest |
| `environment` | 固定 `BINANCE_DEMO_USD_M` |
| `config_digest` | §9.3 脱敏冻结配置 digest |
| `account_reference_digest` | DataTester 为 `null`；order-enabled 运行使用同一 acceptance batch 内稳定、跨 batch 不可关联的加盐 digest；不得保存 account ID |
| `instrument` | 固定 `BTCUSDT-PERP.BINANCE` 及本次 constraints digest |
| `scenario` | `data_tester`、`exec_tester` 或 `demo_strategy` |
| `started_at` / `ended_at` | 运行边界 |
| `result` / `terminal_state` | `PASS`/`FAIL` 与 `COMPLETE`/`HALTED` |
| `observations` | §8 最小 order/fill/position/balance 分类和 scenario-specific counts/digests |
| `cleanup` | active、pending、position、unknown counts 与分类 |
| `failure` | 成功时 `null`；失败时稳定 code、阶段和脱敏 diagnostic digest |
| `evidence_digest` | canonical payload（排除本字段）的 digest |

`run_id` 和 `evidence_digest` 严格按上述两个投影依序导出，避免循环身份。
DataTester 的 order/fill/position/balance observations 必须明确为 `not_applicable`；这是合同明确
排除该事实的 marker，不是 §8 reconciliation 分类，也不能伪造空成功。ExecTester 和 Strategy
必须包含全部四类观察及 cleanup。ExecTester 与 Strategy 在同一 acceptance batch 中必须产生
相同 `account_reference_digest`，但最终 tracked record 不保存该值。

### 9.2 `tracequant-stage4-demo-acceptance-v1`

最终 tracked record 只包含：schema、source commit、dependency lock digest、runtime identity、
environment、config/instrument constraints digest、三个 source evidence digest、required scenario
矩阵结果、最终 order/fill/position/balance 分类、cleanup counts、`LIVE_NOT_APPROVED`、生成时间和
acceptance digest。它不得嵌入原始事件、日志或 account reference。相同输入按 §9.1 的
acceptance 投影必须得到相同 acceptance digest。

### 9.3 分区、脱敏和发布

- 每次联网运行使用新的、显式命名且初始为空的 repo 外 evidence partition；运行只记录逻辑
  partition ID，任何 tracked record 不得出现绝对本机路径；
- frozen config digest 的 payload 包含 runtime、environment、product、instrument、account-mode
  declaration、limits 和 deadlines；只记录 credential **变量名**，不含值；
- secret、完整账户标识、签名请求、认证 header/cookie、可重放认证材料和未脱敏 raw payload
  不得进入配置文件、Issue、日志或 tracked record；
- 原始成功和失败证据都留在外部分区；仓库只发布通过 §9.2 的脱敏 acceptance record；
- required evidence 缺失、identity/config drift、digest 不匹配、非 `consistent` 分类或清场不完整
  时，不得创建或覆盖成功记录。写入必须先完整验证临时 payload，再做单文件原子替换。

### 9.4 证据要求编号

| ID | 冻结要求 |
| --- | --- |
| `ST4-EVID-001` | 准入成功输出不含 secret 的 frozen config payload 和 digest，失败不输出可冒充成功的 digest。 |
| `ST4-EVID-002` | 所有 run evidence 使用 `tracequant-stage4-demo-evidence-v1` 并绑定 §9.1 的完整 identity。 |
| `ST4-EVID-003` | order/fill/position/balance 只用 §8 为当前入口冻结的 rc4 public observation profile 分类；ExecTester 使用可检索 cache object graph，Strategy 使用 callback/cache/account；非 `consistent` 必定阻止成功。 |
| `ST4-EVID-004` | 原始证据只写全新 repo 外分区；tracked 内容必须脱敏、只含逻辑引用和 digest。 |
| `ST4-EVID-005` | 跨运行 identity、config 或 digest 不一致时禁止拼接证据，失败证据必须保留在外部分区。 |
| `ST4-EVID-006` | DataTester 证据包含 quote/trade counts、instrument constraints 和 timestamp 检查结果，不含执行事实。 |
| `ST4-EVID-007` | ExecTester 证据用 §8.1 的可检索 cache object graph 逐项绑定 market fill、reduce-only flat、passive accepted/canceled 及最终清场，不要求不可取得的 tester callback。 |
| `ST4-EVID-008` | Strategy 证据绑定固定状态序列、每个 order/fill、fault protection 和最终 reconciliation/cleanup。 |
| `ST4-EVID-009` | 最终 record 使用 `tracequant-stage4-demo-acceptance-v1`，完整聚合三个新分区且声明 `LIVE_NOT_APPROVED`。 |
| `ST4-EVID-010` | acceptance digest 对同一输入稳定；任何失败、漂移或未清场不得创建或覆盖成功记录。 |

## 10. #386–#391 唯一映射

每个编号只由一项叶子 Issue 实现。依赖方可以读取其输出，但不能复制编号所有权。

| 叶子 Issue | 唯一承接编号 | 有界交付物 |
| --- | --- | --- |
| [#386](https://github.com/PhoenixSss/tracequant/issues/386) | `ST4-REQ-001`–`006`；`ST4-SAFE-001`；`ST4-EVID-001` | typed config、凭据/账户准入、constraints/quantity/deadline 合同、frozen config digest |
| [#387](https://github.com/PhoenixSss/tracequant/issues/387) | `ST4-REQ-007`；`ST4-SAFE-002`；`ST4-EVID-002`–`005` | evidence schema、Nautilus-owned 状态分类、外部分区与脱敏/digest 行为 |
| [#388](https://github.com/PhoenixSss/tracequant/issues/388) | `ST4-REQ-008`；`ST4-SAFE-003`；`ST4-EVID-006` | 官方 DataTester 的固定 plan、薄入口和 data evidence |
| [#389](https://github.com/PhoenixSss/tracequant/issues/389) | `ST4-REQ-009`；`ST4-SAFE-004`–`005`；`ST4-EVID-007` | 官方 ExecTester 安全子集、隔离 bypass 和 execution evidence |
| [#390](https://github.com/PhoenixSss/tracequant/issues/390) | `ST4-REQ-010`–`011`；`ST4-SAFE-006`–`009`；`ST4-EVID-008` | 固定 Demo Strategy、handler fault protection、reconciliation/cleanup evidence |
| [#391](https://github.com/PhoenixSss/tracequant/issues/391) | `ST4-REQ-012`–`014`；`ST4-EVID-009`–`010` | fresh evidence matrix、最终聚合器和 tracked acceptance record |

前置关系只传递已验证产物：#386 → #387 → #388 → #389 → #390 → #391。任何叶项在自己的
编号、Critical Outcome 和 Acceptance Criteria 满足后必须停止；不得以“方便后续叶项”为由
提前实现下一项。

## 11. 强制最小实现约束

> **这些约束可审计且适用于 #386–#391。违反任一项即不符合阶段 4，即使 happy path 能运行。**

- 优先直接组合 rc4 的 public config、factory、`DataTesterConfig`、`ExecTesterConfig`、
  Strategy/order API、entry-specific public observations、cache 和 account snapshots；不得依赖不可获取的
  ExecTester callback、strategy getter 或 report objects，
  不得复制 Nautilus data/execution/order/position/account/
  reconciliation 语义；
- 禁止新增通用 exchange/provider abstraction、adapter facade、plugin/hook registry、依赖注入
  容器、workflow/orchestration engine、scheduler/daemon、通用状态机/CLI/schema registry、event
  store、数据库、消息队列、dashboard、alerting 或 evidence platform；
- 禁止为 Live、多交易所、多 symbol、多 account、hedge/cross、动态 leverage、阶段 3 模型或
  阶段 5 恢复性预留接口、配置层、抽象基类和扩展点；
- 禁止 raw Binance REST/WebSocket client、第二套 ledger/accounting/reconciler 和 shadow order
  state；不能用它们裁决 Nautilus unknown/conflict；
- 禁止后台进程、持久化运行状态、自动重试系统和无限重试；一次验收必须在本进程和本次
  deadlines 内结束；
- 阶段专用常量、typed config、有限函数和小 data structure 优于假设未来复用的抽象；新抽象
  必须由当前冻结路径的必要行为证明；
- 不新增第三方依赖；若标准库、TraceQuant 和固定 rc4 确实无法完成，必须停止并取得
  maintainer 对独立范围变更的批准；
- credentialed Demo run 不进入普通 CI。CI 只验证 deterministic plan、边界、状态转换、schema、
  digest、脱敏和 failure behavior；真实联网 evidence 在 repo 外生成。

## 12. 阶段 5 边界与退出判定

以下能力明确不属于阶段 4：persistence、restart recovery、disconnect/reconnect recovery、通用
partial-fill 支持、funding settlement、stop orders、长时间 soak、监控告警、生产 risk controls、
Live admission、多 symbol/account/exchange、hedge/cross mode。它们不得因阶段 4 的失败场景或
清场需要而提前实现。

阶段 4 只有在 #386–#391 各自通过、#391 发布有效 acceptance record 且 Feature completion
audit 通过后才完成。完成只表示一个 Binance Demo 基础闭环的有界证据成立；最终状态仍是：

```text
NAUTILUS_PRIMARY
BINANCE_DEMO_BASIC_LOOP_ACCEPTED
LIVE_NOT_APPROVED
```

## 13. 规范来源

- [TraceQuant 分阶段推进计划](<../research/foundation-selection/TraceQuant 分阶段推进计划.md>)，阶段 4/5；
- [ADR-0001：NautilusTrader primary runtime](../architecture/adr-0001-nautilustrader-primary-runtime.md)；
- [NautilusTrader rc4 Binance integration](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/docs/integrations/binance.md)；
- [nautilus_trader #5039](https://github.com/nautechsystems/nautilus_trader/issues/5039)。
