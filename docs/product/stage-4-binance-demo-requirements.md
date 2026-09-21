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
唯一最小值时停止；不得猜测或扩大为 portfolio sizing。

## 2. 稳定行为要求

| ID | 冻结要求 |
| --- | --- |
| `ST4-REQ-001` | 每个入口在 import/config validation 阶段验证精确 rc4 version、commit identity 和 dependency lock digest；身份不符时不得创建客户端。 |
| `ST4-REQ-002` | 唯一环境是 Binance USD-M Futures Demo，endpoint 不可覆盖，凭据只来自两个 Demo 专用变量。 |
| `ST4-REQ-003` | 唯一 instrument、单 account、one-way、isolated、1x、最多一个活动/未决订单是不可配置边界。 |
| `ST4-REQ-004` | order-enabled 入口执行 §1.2 的一次性 operator gate，并通过 rc4 config 主动设置 account mode；设置或确认失败即关闭。 |
| `ST4-REQ-005` | order quantity 严格按 §1.3 计算为一个最小有效量；所有 order-enabled 入口共用同一校验合同。 |
| `ST4-REQ-006` | 所有网络和订单阶段使用 §3 的唯一 deadline；v1.0 不提供 timeout override。 |
| `ST4-REQ-007` | 核对只读取 Nautilus cache 和公开 execution/account reports；TraceQuant 不拥有第二套 adapter、order、position、ledger、accounting 或 reconciler。 |
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
| ready | 60 秒 | instrument 已加载、订阅活动，且 order-enabled 场景完成账户准入 |
| DataTester observation | 60 秒 | 同时取得有效 quote、trade、instrument identity/constraints 和时间戳样本 |
| order acceptance | 10 秒 | 唯一订单收到确定 `OrderAccepted` 或确定 reject |
| market / reduce-only fill | 30 秒 | 订单完整成交；partial fill 不延长 deadline |
| cancellation | 10 秒 | cancel 被确定确认，或 race 经报告确定为 fill |
| reconciliation | 30 秒 | cache 与所需公开 reports 完成一致性分类 |
| cleanup | 60 秒 | 无活动/未决订单、无持仓、无 unresolved unknown |

连接或 ready 超时不允许提交订单。order acceptance 超时是 ambiguous acknowledgement，不能
被当成 reject；只能进入 reconciliation/安全清场。任何 cleanup deadline 到期都保持
`HALTED`，不得发布成功记录。

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

1. 账户、instrument 和 constraints ready，且 Nautilus cache/reports 证明无活动订单、无持仓；
2. 以一个最小有效量提交 market buy，等待完整成交；
3. 对实际 long quantity 提交 reduce-only market sell，等待完整成交并证明 flat；
4. 以一个最小有效量在当前 best bid 低一个 price increment 处提交 post-only limit buy；
5. 等待确定 accepted，随后 cancel，并等待确定 canceled；
6. reconcile 并 cleanup，证明无活动/未决订单、无持仓、无 unknown。

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
查询公开 reports/reconcile，或按已证明的非零 position 发出一次 reduce-only cleanup。
这些动作不能把本次结果改回 `COMPLETE`。

## 6. 失败关闭矩阵

| 情况 | 事件前置状态 | 允许动作 | 禁止动作 | 终态 | 最小证据 |
| --- | --- | --- | --- | --- | --- |
| connect / subscription timeout | 尚未 ready | 停止 client，写 timeout | 创建 execution client（DataTester）或提交订单 | `HALTED` | deadline、已连接组件、缺失 subscription |
| 空数据 | DataTester observation 到期 | 停止并保留订阅统计 | 把连接成功当行情成功 | `HALTED` | quote/trade counts 均或任一为零 |
| stale / out-of-order timestamp | 正在观察对应 stream | 停止场景；无订单时直接结束 | 忽略、重排后伪装成功 | `HALTED` | stream、前后 timestamp、age |
| order reject | 等待 acceptance | 记录确定 reject；若已有 exposure 则安全清场 | 修改参数并重发 | `HALTED` | client order reference、reason、cache status |
| ambiguous acknowledgement | 等待 acceptance 超时或 transport unknown | 查询正常 Nautilus report/reconcile；确定已知 order 后可 cancel | 当作 reject、用新 ID 重发、继续开仓 | `HALTED` | command identity、timeout/transport fact、report result |
| duplicate / out-of-order event | 任一 order state | 忽略重复的状态推进并 reconcile；记录冲突 | 二次推进、二次提交 | `HALTED` | event identity、expected/observed state |
| cancel/fill race | 等待 cancel | reconcile；若 fill 已发生则按实际 position reduce-only cleanup | 同时假定 canceled 和 filled、重发 cancel/order | `HALTED` | cancel event、fill/report、最终 position |
| late fill | 已收到 cancel 或已开始 reconcile | 更新 Nautilus-owned事实并按实际 exposure 清场 | 保持原 flat 假设、发布成功 | `HALTED` | late fill identity、先前状态、cleanup result |
| unexpected partial fill | 等待任一完整 fill | 停止成功路径；按已报告 cumulative quantity 有限清场 | 等待/补单凑满、实现通用 partial-fill workflow | `HALTED` | ordered/cumulative/leaves quantity、reports |
| handler exception | 任一实际使用的 Strategy handler | 顶层保护记录 fatal diagnostic、锁住提交、进入安全清场 | 吞掉异常、继续状态推进 | `HALTED` | handler、异常类型/脱敏信息、state、submission count |
| cache/report conflict | reconciliation / cleanup | 保留两侧 Nautilus facts 和 `conflicting` 分类 | 选择有利一侧、用 raw REST 裁决 | `HALTED` | cache/report digests、冲突字段 |
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
| `ST4-SAFE-009` | 只有 cache/reports 证明无活动/未决订单、无持仓、无 unknown 才可 `COMPLETE`；否则保持 `HALTED`。 |

## 8. 状态核对合同

每类事实按以下互斥结果分类。分类输入只能是同一 run identity 下的 Nautilus cache 和公开
execution/account reports。

| 分类 | 定义 |
| --- | --- |
| `consistent` | 所有 required cache/report observations 存在，identity、状态和数值在 Nautilus 原生 precision 内一致。 |
| `missing` | 当前场景要求的任一 cache 或 report observation 不存在。 |
| `conflicting` | 两个 Nautilus-owned observation 对同一事实给出不相容 identity、状态或数值。 |
| `unknown` | acknowledgement 未决、report 不完整、无法解析，或事实不能从公开 surface 确定。 |
| `cleanup_incomplete` | 任一活动/未决订单、非零 position 或 unresolved unknown 仍存在。 |

具体核对最小集合：

- **Order**：client order reference、instrument、side、type、quantity、reduce-only/post-only、
  accepted/canceled/filled status 在 cache 与 `OrderStatusReport` 一致；
- **Fill**：trade reference 唯一，order reference、side、last/cumulative quantity、price、fee 和
  liquidity side 与 cache/event/`FillReport` 一致；
- **Position**：instrument、side 和 quantity 在 cache 与 `PositionStatusReport` 一致；成功清场
  必须两侧均为 flat/absent-active-position；
- **Balance**：只比较运行前后公开 account/balance snapshots，并验证其变化能由公开 reports
  中的 fills、fees 和 realized PnL 在 native currency precision 内解释。运行跨过 funding 或出现
  其他无法解释变化时为 `conflicting`，不得增加 funding ledger；
- **Cleanup**：上述 order/position 以及所有 unknown 同时清零。单独的 balance 一致不能证明清场。

## 9. 证据合同

### 9.1 `tracequant-stage4-demo-evidence-v1`

每次 DataTester、ExecTester 或 Strategy 运行生成一份 canonical JSON。除枚举外的字符串必须
非空；digest 使用小写 SHA-256；时间为 UTC RFC 3339。schema 只允许以下顶层事实：

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

`run_id` 从排除 `run_id` 和 `evidence_digest` 的 canonical payload 导出，避免循环身份。
DataTester 的 order/fill/position/balance observations 必须明确为 `not_applicable`；这是合同明确
排除该事实的 marker，不是 §8 reconciliation 分类，也不能伪造空成功。ExecTester 和 Strategy
必须包含全部四类观察及 cleanup。ExecTester 与 Strategy 在同一 acceptance batch 中必须产生
相同 `account_reference_digest`，但最终 tracked record 不保存该值。

### 9.2 `tracequant-stage4-demo-acceptance-v1`

最终 tracked record 只包含：schema、source commit、dependency lock digest、runtime identity、
environment、config/instrument constraints digest、三个 source evidence digest、required scenario
矩阵结果、最终 order/fill/position/balance 分类、cleanup counts、`LIVE_NOT_APPROVED`、生成时间和
acceptance digest。它不得嵌入原始事件、日志或 account reference。相同输入的 canonical payload
（排除生成时间和自身 digest）必须得到相同 acceptance digest。

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
| `ST4-EVID-003` | order/fill/position/balance 只按 §8 分类；非 `consistent` 必定阻止成功。 |
| `ST4-EVID-004` | 原始证据只写全新 repo 外分区；tracked 内容必须脱敏、只含逻辑引用和 digest。 |
| `ST4-EVID-005` | 跨运行 identity、config 或 digest 不一致时禁止拼接证据，失败证据必须保留在外部分区。 |
| `ST4-EVID-006` | DataTester 证据包含 quote/trade counts、instrument constraints 和 timestamp 检查结果，不含执行事实。 |
| `ST4-EVID-007` | ExecTester 证据逐项绑定 market fill、reduce-only flat、passive accepted/canceled 及最终清场。 |
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
  Strategy/order API、cache 和 reports；不得复制 Nautilus data/execution/order/position/account/
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
