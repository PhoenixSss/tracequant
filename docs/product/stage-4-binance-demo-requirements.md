# TraceQuant 阶段 4：Binance Demo 实施与验收基线

| 字段 | 值 |
| --- | --- |
| 文档状态 | v1.0 实施候选 |
| 文档版本 | `v1.0` |
| 日期 | `2026-09-22` |
| Feature | [#384](https://github.com/PhoenixSss/tracequant/issues/384) |
| 固定运行时 | NautilusTrader `2.0.0rc4` / `a0400251110653b6d8ae6a9b5b89c4543fa85a2d` |
| 产品状态 | `DEMO_ONLY`、`LIVE_NOT_APPROVED` |

本文档是 Feature #384 的唯一阶段 4 实施与验收基线。它冻结产品边界、可观察结果和安全顺序，
不冻结非必要的进程、LiveNode 或 tester-instance 拓扑。#386–#391 只实现 §10 映射给自己的
primary/applicable 要求；达到自己的 Critical Outcome 与 Acceptance Criteria 后停止。

## 1. 唯一支持边界

| 项目 | 唯一选择 | 拒绝 |
| --- | --- | --- |
| Runtime | NautilusTrader `2.0.0rc4`，固定 upstream commit | rc5、兼容版本范围、fork/patch |
| Platform | CPython 3.13、`linux-x86_64`、官方 cp313 wheel | sdist、其他 wheel tag |
| Venue | Binance USD-M Futures Demo | Live、legacy Testnet、Spot、COIN-M、自定义 endpoint |
| Instrument | `BTCUSDT-PERP.BINANCE` | 第二 instrument、全量动态 allowlist |
| Account | 一个 batch 使用同一份 Demo credential snapshot | 第二 account、entry 覆盖 credential |
| Position | one-way；Nautilus `OmsType.NETTING` | hedge / dual-side |
| Margin | isolated、1x | cross、动态 leverage |
| Orders | 最多一个 active/inflight order | 并发订单、批量订单 |
| Product status | `DEMO_ONLY`、`LIVE_NOT_APPROVED` | Live admission |

凭据只允许由 rc4 adapter 从 `BINANCE_DEMO_API_KEY` 和
`BINANCE_DEMO_API_SECRET` 读取。配置、日志、Issue、原始/发布证据均不得复制 secret、完整账户
标识、签名请求、认证 header 或 credential 派生身份。

### 1.1 最小 runtime identity

每个入口在创建 network client 前验证并记录：

- 当前实际 `source_commit`，且 tracked source 无 staged/unstaged/deleted change；
- 当前 `uv.lock` bytes 的 SHA-256；
- 安装 distribution 名称/版本恰为 `nautilus-trader==2.0.0rc4`；
- lock 中目标 wheel 的 SHA-256 恰为
  `0c97c4385d55833fc3cce4934ca48a2a5fa0ea1d69d0a775b88c2bbf6fb79005`，对应批准的
  upstream commit `a0400251110653b6d8ae6a9b5b89c4543fa85a2d`。

任一不符都在联网前 `HALTED`。阶段 4 不要求逐文件 source/wheel manifest、import-origin 扫描、
pycache 禁令、wheel `RECORD` 全量复验、installed-tree digest 或 provisioning attestation。

### 1.2 账户模式准入与行为确认

每个 order-enabled **attempt** 是一个逻辑场景，从 operator/config gate 开始，到该场景终态结束；
它可以与其他 attempt 位于同一或不同 process/LiveNode，但不得继承另一个 attempt 的账户模式证据。
创建 client 前必须：

1. 操作者刚刚在 Demo UI 确认 one-way、`BTCUSDT` isolated、1x、flat、零活动订单，并为本
   attempt 提供精确 token `ONE_WAY_ISOLATED_1X_FLAT_CONFIRMED`；token 不得默认、缓存或复用；
2. execution config 固定为 `OmsType.NETTING`、
   `futures_margin_types={"BTCUSDT": ISOLATED}`、`futures_leverages={"BTCUSDT": 1}`、
   `use_position_ids=true`，且 endpoint 无 override；
3. 本 attempt 使用 batch owner 提供的 credential snapshot，entry 不得覆盖。

rc4 的 margin-type 配置失败会阻止连接，但 leverage 业务拒绝只产生 warning；hedge-mode 查询也没有
公开 typed admission result。因此 gate/config 只允许一个最小 market canary，不能支持成功。
每个 attempt 必须以自己的 canary 完整成交，并在继续正常场景订单前用正常 Nautilus surface 确认：

- **one-way**：cache order/position observations 只出现一个净 position，不出现
  `...-LONG`/`...-SHORT` hedge leg；缺失 identity 或 observation 不完整不能算确认；
- **isolated**：固定 margin config 成功连接，且 canary 期间只有目标 instrument 的唯一 position，
  无其他 order/position 或 margin owner；
- **1x**：canary terminal 且无其他 order/position 后公开 `query_account`，读取随后
  `AccountState.info["total_initial_margin"]`。请求前后 5 秒内的合格 mark price 构成区间，
  相对价差不超过 0.5%；observed initial margin 必须落在
  `abs(quantity) * [mark_min, mark_max]`，允许两端各扩展
  `abs(quantity) * price_increment + one_currency_quantum`；`one_currency_quantum` 是 rc4 public
  USDT currency precision 的一个最小单位。

确认后立即以 exact reduce-only quantity 清平 canary。任一确认失败都进入不可逆 `HALTED`，只允许
§5 安全清场。ExecTester market、ExecTester passive、Strategy 各是独立 attempt：passive attempt
在自己的 passive order 前也必须完成自己的 canary/确认/清平，不能引用或复制 market attempt 的值。

`BinanceExecutionClientConfig.account_id` 是调用方别名，不是 Binance 认证身份，不能用来证明
account 相同。batch 内单 account 由同一 credential snapshot、禁止 entry override 和 attempt-local
gate 保证；credential 不做 hash/HMAC，不写入证据。不得解析 warning、读取 private/Rust client 或
创建 raw Binance REST/WebSocket client 替代上述确认。

### 1.3 Quantity 与价格输入

正常开仓/被动单从本 attempt 的 rc4 public `Instrument` 读取：
`size_increment`、`minimum_quantity`、可选 `maximum_quantity` 和
`minimum_notional`。不得要求 rc4 未公开的 raw `MARKET_LOT_SIZE`，也不得从源码或用户配置
硬编码 quantity。

- limit 使用同一合格 quote 推导的实际提交价格；passive buy 固定为
  `best_bid - one_price_increment`；
- market 使用同 instrument 的合格 mark price；
- quantity 是满足 minimum quantity、size precision/increment、maximum quantity（若有）和
  minimum notional（若有）的最小 step 倍数；
- 缺少 constraint/价格、结果不唯一或超过 maximum 时不提交；venue 对未公开规则的确定 reject
  使 attempt `HALTED`，不得改量重发。

quote/mark 必须属于目标 instrument、价格为正，`ts_event` 不得比当前 UTC 快超过 1 秒，检查时
年龄不超过 5 秒，且本 stream 内 timestamp 不倒退。提交前的 price-readiness deadline 为 10 秒；
过期输入必须在原 deadline 内替换，不能重启 deadline。

锁定的 rc4 `ExecTester` 是本边界的特例：其 built-in strategy 只能在 `LiveNode` 启动前注册，
并会从首个 quote callback 直接提交 pending market open 或维护 limit order；rc4 没有允许薄入口
在同一 callback 前插入 policy gate 的 public hook。因而 ExecTester 的“提交前 price readiness”
特指：创建 order-enabled node 前，入口必须在上述 10 秒 deadline 内取得并验证本 attempt 的
public instrument/quote/mark input，并只从该 input 冻结 tester quantity 与预期 passive price。
node 启动后的 cache observation 不是第二个放行 gate，而是强制一致性证明：runtime instrument
constraints 必须相同，quote/mark 必须仍合格，按 runtime price 重新计算的最小 quantity 必须等于
冻结 quantity，且缓存中的实际 order 必须逐项匹配预期 side、type、quantity、price、
`reduce_only` 与 terminal status。任一漂移均为 `conflicting/HALTED`，禁止重发，只可按 §5 清场。
该特例不适用于 §4.3 Strategy；Strategy 仍须在自己的 handler 内先通过 readiness gate 再提交。

reduce-only cleanup 不复用开仓最小量公式。它只在原订单 terminal、零 active/inflight 且净持仓
确定后冻结 `close_quantity=abs(position)`，side 与持仓相反，使用 market +
`reduce_only=true`。quantity 必须可由 Nautilus 无损表示并满足 rc4 public size/min/max
constraints；不得取整、放大或应用 `MIN_NOTIONAL`。构造前 position 变化则重新执行 proof。
公开 constraint 不满足或 venue reject 时不重试，保持 `HALTED/cleanup_incomplete`。

## 2. 稳定行为要求

| ID | 冻结要求 |
| --- | --- |
| `ST4-REQ-001` | 所有入口执行 §1.1 最小 runtime identity；漂移时联网前失败。 |
| `ST4-REQ-002` | 唯一环境、endpoint 与凭据来源遵循 §1。 |
| `ST4-REQ-003` | 单 instrument/account、one-way、isolated、1x、最多一个 active/inflight order。 |
| `ST4-REQ-004` | 每个 order-enabled attempt 执行自己的 operator/config gate、canary 和 §1.2 行为确认。 |
| `ST4-REQ-005` | 正常 quantity 与 exact reduce-only cleanup 遵循 §1.3。 |
| `ST4-REQ-006` | 全部阶段使用 §3 固定 deadline，不提供 override。 |
| `ST4-REQ-007` | 核对只用 rc4 public cache/callback/account observations，不建立第二 owner。 |
| `ST4-REQ-008` | DataTester 只执行 §4.1，不创建 execution client。 |
| `ST4-REQ-009` | ExecTester 只执行 §4.2；canary 与 passive 订单由官方 tester 提交，唯一 exact reduce-only 清场可由本入口私有 Nautilus Strategy 通过 public `order_factory.market` / `submit_order` 提交冻结数量且 `reduce_only=True` 的单笔订单，特殊 bypass 不外泄。 |
| `ST4-REQ-010` | 普通 Strategy 只执行 §4.3；§4.2 私有清场 Strategy 只允许提交已证明的唯一 reduce-only close；两者均不读阶段 3、不产生 alpha、不做 portfolio sizing。 |
| `ST4-REQ-011` | §4.3 普通 Strategy 只允许固定前进状态，终态为 `COMPLETE` 或 `HALTED`；§4.2 私有清场 Strategy 只允许一次有证明的提交。 |
| `ST4-REQ-012` | 聚合四个逻辑 scenario record；process/LiveNode/tester 拓扑不是合同。 |
| `ST4-REQ-013` | 成功矩阵仅包含 data、market fill、passive accepted/canceled、long/short、reduce-only flat。 |
| `ST4-REQ-014` | 阶段完成后停止扩展并保持 `LIVE_NOT_APPROVED`。 |

## 3. 固定 deadline

deadline 使用 monotonic clock，自对应动作进入本地 queue 开始，不因日志/心跳续期：

| 阶段 | Deadline | 成功条件 |
| --- | ---: | --- |
| connect | 30 秒 | 所需 client 已连接 |
| ready | 60 秒 | instrument、订阅、gate/config ready |
| data observation | 60 秒 | quote、trade、instrument、timestamp 均有效 |
| price readiness | 10 秒 | 取得 §1.3 合格 quote/mark 并完成 quantity |
| order acceptance | 10 秒 | 确定 accepted 或 reject |
| market/reduce-only fill | 30 秒 | 完整成交 |
| cancellation | 10 秒 | 确定 canceled 或 race 已被识别 |
| reconciliation | 30 秒 | §6 observations 已分类 |
| cleanup | 60 秒 | 零 active/pending、flat、无 unknown |

connect/ready 超时前不得下单。acceptance 超时是 ambiguous ACK，不是 reject。任何超时进入
`HALTED`；cleanup 超时不得发布成功。

## 4. 有限场景

### 4.1 官方 DataTester

直接使用官方 `DataTester` 或只固定 config/deadline/evidence 的薄入口。唯一成功条件：

- 只连接 Binance USD-M Demo，只加载 `BTCUSDT-PERP.BINANCE`；
- 观察至少一个有效 quote 和 trade；
- instrument identity、price/size precision 与 constraints 可用；
- quote/trade timestamp 通过 §1.3 freshness/ordering 检查；
- 输出一个新的 repo 外 data partition，execution observations 为 `not_applicable`。

它不得读取 credential、创建 execution client 或提交订单。

### 4.2 官方 ExecTester

直接使用官方 `ExecTester`；薄入口只可准备 rc4 public instrument/price 输入、构造固定 config、
控制有界开始/停止、读取 public cache/account observations 和输出 §7 record。canary 与 passive
订单由官方 tester 提交。由于 rc4 tester 的 `close_positions_on_stop` 在复核失败后的停机仍会
自动下单，本入口的唯一 exact reduce-only 清场使用私有 Nautilus Strategy 的 public
`order_factory.market` / `submit_order` 提交冻结数量且 `reduce_only=True` 的单笔订单；它必须在同一次调用中复核当前零 active/inflight 与
精确持仓，且普通停机不下单。实现自行选择满足结果合同的最小 rc4 public 组合；本文不规定
process、LiveNode、tester-instance 或 reconciliation component 数量。

两个逻辑 attempt：

1. **`exec_tester_market_close`**：用唯一 market buy 作为本 attempt canary；完整成交后执行
   §1.2 模式确认，再以 exact reduce-only market sell 清平。
2. **`exec_tester_passive_cancel`**：先在本 attempt 内以官方 tester 完成自己的最小 market
   canary、§1.2 确认和 exact reduce-only 清平；随后提交唯一 post-only limit buy，价格为
   best bid 低一个 increment，accepted 后请求 cancel，最终证明 canceled、零 fill、零 position。

两个 attempt 使用独立逻辑 partition/record，但可在同一或不同 runtime topology 顺序执行。
上述私有清场 Strategy 只在已证明的唯一 close 节点注册，不能开仓或处理普通信号；其提交
异常视为 unknown，不得重试。passive
order 若成交、partial fill 或发生 cancel/fill race，场景立即失败；仅按 §5 proof 执行唯一有限
cleanup。reject、ambiguous ACK、unknown/conflict 不得盲目重发。tester 所需 risk bypass 只能由该
入口使用，普通 Strategy 不得 import/configure。

### 4.3 TraceQuant 最小 Demo Strategy

Strategy 只通过 Nautilus public Strategy/order API，按以下顺序前进：

```text
ready
  -> market long complete fill (本 attempt canary)
  -> account-mode confirmed
  -> reduce-only long close -> flat
  -> market short complete fill
  -> reduce-only short close -> flat
  -> passive post-only limit buy accepted
  -> cancel confirmed
  -> reconcile -> COMPLETE
```

状态只能前进一次；不匹配、重复或倒序事件不能推进。任何 failure 进入不可逆 `HALTED`，禁止新开仓，
只允许 §5 清场。Strategy 不读取阶段 3 artifact，不接受 signal、target position 或 sizing 参数。

### 4.4 最终聚合

为一个 acceptance batch 生成随机、非 credential-derived opaque `batch_id`。同一 batch 顺序取得
四个逻辑 record：DataTester、两个 ExecTester attempts、Strategy。每个 scenario 使用新的 repo 外
partition；partition 是逻辑证据边界，不意味着独立进程。

聚合器只验证既有 record，不联网、不下单、不补跑失败片段。四个 record 必须具有相同
source/lock/runtime/config/instrument/batch identity，全部 `PASS/COMPLETE` 且最终 flat、零订单、
无 unknown，才可原子发布 `docs/product/stage4-binance-demo-acceptance.json`。失败不得创建或
覆盖成功记录。

## 5. 失败关闭与清场

`HALTED` 是不可逆 latch。触发 failure 后禁止新开仓和盲目重发，只能按顺序：

1. 若原 order 可识别且 Nautilus public API 支持，对它至多请求一次 cancel；
2. 用正常 Nautilus query/callback/cache 路径 reconciliation；
3. 证明原 order terminal；
4. 证明 active/inflight count 为零；
5. 重新读取净 position；
6. position 确定非零时，按 §1.3 提交至多一笔 exact reduce-only cleanup；
7. 证明零订单、flat、无 unresolved unknown，或记录 `cleanup_incomplete`。

任一步缺失、冲突、unknown 或超时即停止自动动作。清场成功仍为 `HALTED`，不能改回成功。

| 情况 | 允许动作 | 禁止动作 | 最小证据 |
| --- | --- | --- | --- |
| identity/config/gate 失败 | 联网前停止 | 创建 client、下单 | 失败字段与脱敏 actual/expected |
| connect/subscription/data 失败 | 停止 client | 把连接当数据成功 | deadline、缺失组件/count |
| stale/future/out-of-order data | 停止场景 | 重排后伪装成功 | stream、timestamp、age |
| reject | 记录 reject；已有 exposure 时按本节清场 | 改参数重发 | order reference、reason、cache state |
| ambiguous ACK/unknown | reconciliation；目标明确时一次 cancel | 当作 reject、换 ID 重发、立即 cleanup | query/cache terminal 与 active proof |
| duplicate/out-of-order event | 不推进并 reconcile | 二次推进/提交 | expected/observed state |
| cancel/fill race、late/partial fill | 判失败，等 terminal/零 active 后按最新 position 清场 | 补单、按中间 fill 提前清场 | fill、terminal、position、cleanup |
| handler exception | inhibit submission、fatal diagnostic、有限清场 | 吞掉异常、继续推进 | handler、state、submission count |
| observation conflict | 保留冲突并停止 | 选有利事实、用 raw REST 裁决 | source facts 与冲突字段 |
| cleanup timeout/reject | 停止自动动作 | 重试、改量、标记 flat | order/position/unknown/deadline |

### 5.1 Handler exception

upstream #5039 表明 rc4 的 Strategy callback exception 可能被运行时吞掉。#390 实际使用的每个
lifecycle/data/timer/order/position handler 最外层都必须：捕获 `Exception`、写脱敏 fatal
diagnostic、设置 submission-inhibit latch、进入 `HALTED`。一个确定性离线 fault-injection 测试
必须证明异常可观察、终态为 `HALTED` 且异常后 submission count 不增加。注入只允许阶段专用固定
选择，不得成为 plugin/hook registry；联网配置必须禁用。

## 6. 状态核对与安全要求

状态判断只读取当前 attempt 的 Nautilus-owned facts：

- DataTester：官方 tester 的 instrument 与 data observations；
- ExecTester：public `LiveNode.cache` order/position/account object graph；
- Strategy：public callbacks、cache、query 更新与 account snapshots；
- balance：run 前后公开 account snapshots，以及 fills、fees、realized PnL 能解释的变化。

不得要求 rc4 未公开的 tester callback/strategy getter/report，不得维护 shadow order state、
第二套 ledger/accounting/reconciler，也不得以日志/private object/raw Binance API 裁决冲突。

分类只允许：

- `consistent`：所需事实齐全且相互一致；
- `missing`：required fact 未出现；
- `conflicting`：公开 facts 对同一状态不一致；
- `unknown`：无法证明 terminal、order ownership 或 position；
- `not_applicable`：该 scenario 明确不产生此类事实；
- `cleanup_incomplete`：失败后未证明零订单/flat/无 unknown。

| ID | 冻结要求 |
| --- | --- |
| `ST4-SAFE-001` | 非 Demo、越界、identity 不完整、gate 缺失或 secret 来源错误在联网前失败。 |
| `ST4-SAFE-002` | `missing/conflicting/unknown/cleanup_incomplete` 不得降级为成功。 |
| `ST4-SAFE-003` | DataTester 对空数据、错误 identity 与异常 timestamp 失败关闭。 |
| `ST4-SAFE-004` | ExecTester bypass 仅限该入口，Strategy 不可访问。 |
| `ST4-SAFE-005` | ExecTester 的 reject/ambiguous/unexpected fill/race 禁止重发，只能按 §5 清场。 |
| `ST4-SAFE-006` | Strategy 对 ambiguous/duplicate/out-of-order/timeout/conflict 锁定 `HALTED`。 |
| `ST4-SAFE-007` | partial/late/passive fill 必定失败；只用 terminal 后最新 exact position 清场。 |
| `ST4-SAFE-008` | 实际 handlers 具有 §5.1 顶层保护和确定性 fault injection。 |
| `ST4-SAFE-009` | 只有当前 attempt 证明零订单、flat、无 unknown 才可 `COMPLETE`。 |

## 7. 最小证据合同

### 7.1 序列化与 digest

每个 record 使用仓库既有 compact sorted JSON 约定：

```python
json.dumps(payload_without_own_digest, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
```

对其 UTF-8 bytes 计算 SHA-256，写入该 record 唯一的顶层 digest 字段。decimal 使用非指数十进制
字符串，timestamp 使用 UTC `Z`。不定义 NFC/Unicode canonicalization、逐 fact/category/reference
digest、event-sequence digest、runtime tree digest 或 schema registry。

“唯一的顶层 digest”指每个 schema 只有一个对自身 payload 求值的 digest：EvidenceV1 是
`evidence_digest`，AcceptanceV1 是 `acceptance_digest`，frozen config 是 `config_digest`。
`dependency_lock_sha256`、`config_digest` 和 `evidence_digests` 在消费它们的 record 中只是既有
artifact identity reference，不触发再次分层求 digest。

### 7.2 `tracequant-stage4-demo-evidence-v1`

每个逻辑 scenario 恰有一个 record：

```text
EvidenceV1 = {
  schema: "tracequant-stage4-demo-evidence-v1",
  source_commit: git_sha,
  dependency_lock_sha256: sha256,
  runtime: {
    distribution: "nautilus-trader",
    version: "2.0.0rc4",
    upstream_commit: "a0400251110653b6d8ae6a9b5b89c4543fa85a2d",
    wheel_sha256: "0c97c4385d55833fc3cce4934ca48a2a5fa0ea1d69d0a775b88c2bbf6fb79005"
  },
  environment: "BINANCE_DEMO_USD_M",
  config_digest: sha256,
  batch_id: opaque_id,
  instrument: {
    id: "BTCUSDT-PERP.BINANCE",
    price_precision: uint | null,
    price_increment: decimal | null,
    size_precision: uint | null,
    size_increment: decimal | null,
    minimum_quantity: decimal | null,
    maximum_quantity: decimal | null,
    minimum_notional: decimal | null
  },
  scenario: "data_tester" | "exec_tester_market_close" |
            "exec_tester_passive_cancel" | "demo_strategy",
  started_at: timestamp,
  ended_at: timestamp,
  result: "PASS" | "FAIL",
  terminal_state: "COMPLETE" | "HALTED",
  observations: {
    market_data: {classification, quote_count, trade_count, timestamp_valid},
    order: {classification, submitted, accepted, terminal, active, pending, ambiguous},
    fill: {classification, complete, partial, late},
    position: {classification, open_count, final_net_quantity},
    balance: {classification, before_observed, after_observed, explained_change},
    account_mode: {
      classification,
      operator_gate_confirmed,
      canary_complete,
      one_way_confirmed,
      isolated_confirmed,
      leverage_one_confirmed,
      observed_initial_margin,
      allowed_initial_margin_min,
      allowed_initial_margin_max
    }
  },
  cleanup: {
    classification,
    active_order_count,
    pending_order_count,
    open_position_count,
    unresolved_unknown_count,
    final_net_quantity
  },
  failure: {code, phase, diagnostic_codes} | null,
  evidence_digest: sha256
}
```

`classification` 使用 §6 枚举。DataTester 的 execution/account-mode/cleanup 分类为
`not_applicable`；三个 order-enabled records 的这些分类不得为 `not_applicable`。成功必须
`PASS/COMPLETE`、failure 为 null、required classifications 为 `consistent`、零
active/pending/open/unknown 且 final net quantity 为 `"0"`。失败必须
`FAIL/HALTED` 且包含 failure；实际 count 不得伪造成成功。

只有 `FAIL/HALTED` 可在 instrument-ready 前的当前 attempt 失败时保留锁定的 instrument
`id`，并将其余全部 instrument constraint 字段同时设为 `null`；部分缺失非法，且对应
market-data classification 必须为 `missing`。`PASS/COMPLETE` 仍要求完整 instrument
constraints。此表示不进入只接受四份 PASS record 的 AcceptanceV1 聚合。

原始 events、account snapshots 和 diagnostics 留在对应 repo 外 partition；tracked record 不包含
原始 payload、绝对路径、credential/account identity。四个 scenario record 的
`source_commit`、lock/runtime、environment、config、batch 和 instrument identity 必须相同。

### 7.3 Frozen config

#386 定义一个封闭的小型 payload：runtime constants、Demo environment、endpoint policy、
instrument allowlist、one-way/isolated/1x、order cap、quantity policy、§3 deadlines 和两个
credential **变量名**。它按 §7.1 计算一个 `config_digest`；不包含 credential value、account
identity、batch/scenario/partition 或动态 observations。

### 7.4 `tracequant-stage4-demo-acceptance-v1`

```text
AcceptanceV1 = {
  schema: "tracequant-stage4-demo-acceptance-v1",
  source_commit,
  dependency_lock_sha256,
  runtime,
  environment: "BINANCE_DEMO_USD_M",
  config_digest,
  batch_id,
  instrument_id: "BTCUSDT-PERP.BINANCE",
  evidence_digests: {
    data_tester,
    exec_tester_market_close,
    exec_tester_passive_cancel,
    demo_strategy
  },
  required_runs: {
    data_tester: "PASS",
    exec_tester_market_close: "PASS",
    exec_tester_passive_cancel: "PASS",
    demo_strategy: "PASS"
  },
  final_state: {
    active_order_count: 0,
    pending_order_count: 0,
    open_position_count: 0,
    unresolved_unknown_count: 0,
    final_net_quantity: "0"
  },
  product_status: "LIVE_NOT_APPROVED",
  generated_at,
  acceptance_digest: sha256
}
```

`acceptance_digest` 按 §7.1 删除自身后计算；这是 acceptance record 唯一顶层 digest。聚合器验证
每个 source record 的 `evidence_digest`，但不创建额外 category/reference digest。相同 payload
得到相同 digest；任一 run 失败、identity/config/batch drift、unknown/conflict 或未清场都拒绝发布。

### 7.5 证据编号

| ID | 冻结要求 |
| --- | --- |
| `ST4-EVID-001` | 准入输出最小 frozen config/digest，不含 secret。 |
| `ST4-EVID-002` | run record 绑定 §1.1 最小 source/lock/runtime identity。 |
| `ST4-EVID-003` | observations 只用 §6 Nautilus-owned facts；非 consistent 阻止成功。 |
| `ST4-EVID-004` | 原始证据写入四个新逻辑 partitions；batch ID 防止跨批拼接且不派生自 credential。 |
| `ST4-EVID-005` | identity/config/batch drift 或 record digest 错误拒绝聚合。 |
| `ST4-EVID-006` | DataTester record 只含 data/instrument/timestamp 结果。 |
| `ST4-EVID-007` | 两个 ExecTester records 各含本 attempt 自有 mode canary、execution 与 cleanup 结果。 |
| `ST4-EVID-008` | Strategy record 绑定固定状态序列、handler protection 与最终 reconciliation。 |
| `ST4-EVID-009` | acceptance 聚合四个 record 并声明 `LIVE_NOT_APPROVED`。 |
| `ST4-EVID-010` | 失败或未清场不创建/覆盖成功 record；同一 payload digest 稳定。 |

## 8. Batch、credential 与发布边界

- batch owner 一次读取两个非空 Demo credential 环境变量；所有 order-enabled attempts 使用同一
  snapshot，entry/CLI/config 不得提供第二来源；
- DataTester 不取得 credential；credential 只传给实际需要 execution client 的 composition；
- 实现可使用单进程或多个有界进程，但不得把 process/LiveNode/tester 数量写入 evidence 或作为成功条件；
- batch ID 使用标准库安全随机值，与 credential/account identity 无关；
- credential 不 hash、不打印、不持久化；实现不声称 Python 能可靠擦除不可变字符串；
- 每个 scenario 使用全新 repo 外逻辑 partition，tracked 内容只保留 §7.4 acceptance；
- 写入 acceptance 前完整验证临时 payload，再原子替换；失败保留外部诊断，不覆盖既有成功文件。

## 9. 强制最小实现约束

> 以下约束适用于 #386–#391；违反任一项即不符合阶段 4。

- 优先直接组合 rc4 public config、tester、Strategy/order API、cache 和 account observations；
- 禁止通用 exchange/provider abstraction、adapter facade、plugin/hook registry、DI container、
  workflow/orchestration engine、scheduler/daemon、通用状态机/CLI、schema registry、event store、
  数据库、消息队列、dashboard、alerting 和 evidence/provenance platform；
- 禁止 raw Binance client、shadow order state、第二套 ledger/accounting/reconciler；
- 禁止逐文件 source/wheel attestation、逐 fact/category/reference digest 树和自定义 Unicode
  canonicalization protocol；
- 禁止为 Live、多交易所、多 symbol/account、hedge/cross、动态 leverage、阶段 3 模型或阶段 5
  recovery 预留接口、抽象基类或扩展点；
- 禁止 vendor/fork/monkeypatch `nautilus_trader`，或用 log/private object/未经 §4.2 唯一清场证明的 tester 外下单绕过合同；
- 不新增第三方依赖；确实无法完成时停止并请求独立范围变更；
- credentialed Demo 不进入普通 CI；CI 只验证 deterministic plan、边界、状态、schema、digest、
  脱敏和 failure behavior。

## 10. #386–#391 责任映射

primary 实现并验证编号；applicable 只服从，不复制所有权：

| 编号 | Primary | Applicable |
| --- | --- | --- |
| `ST4-REQ-001` | #386 | #387–#391 |
| `ST4-REQ-002` | #386 | #387–#391 |
| `ST4-REQ-003` | #386 | #387–#391 |
| `ST4-REQ-004` | #386 | #389–#391 |
| `ST4-REQ-005` | #386 | #389–#391 |
| `ST4-REQ-006` | #386 | #388–#391 |
| `ST4-REQ-007` | #387 | #388–#391 |
| `ST4-REQ-008` | #388 | #391 |
| `ST4-REQ-009` | #389 | #391 |
| `ST4-REQ-010` | #390 | #391 |
| `ST4-REQ-011` | #390 | #391 |
| `ST4-REQ-012` | #391 | — |
| `ST4-REQ-013` | #391 | #388–#390 |
| `ST4-REQ-014` | #391 | #386–#390 |
| `ST4-SAFE-001` | #386 | #388–#391 |
| `ST4-SAFE-002` | #387 | #388–#391 |
| `ST4-SAFE-003` | #388 | #391 |
| `ST4-SAFE-004` | #389 | #390–#391 |
| `ST4-SAFE-005` | #389 | #391 |
| `ST4-SAFE-006` | #390 | #391 |
| `ST4-SAFE-007` | #390 | #389、#391 |
| `ST4-SAFE-008` | #390 | #391 |
| `ST4-SAFE-009` | #390 | #388、#389、#391 |
| `ST4-EVID-001` | #386 | #387–#391 |
| `ST4-EVID-002` | #387 | #388–#391 |
| `ST4-EVID-003` | #387 | #388–#391 |
| `ST4-EVID-004` | #387 | #388–#391 |
| `ST4-EVID-005` | #387 | #388–#391 |
| `ST4-EVID-006` | #388 | #391 |
| `ST4-EVID-007` | #389 | #391 |
| `ST4-EVID-008` | #390 | #391 |
| `ST4-EVID-009` | #391 | — |
| `ST4-EVID-010` | #391 | — |

| Leaf | 有界交付物 |
| --- | --- |
| [#386](https://github.com/PhoenixSss/tracequant/issues/386) | typed config、offline gate、纯 quantity 函数、deadline、frozen config digest |
| [#387](https://github.com/PhoenixSss/tracequant/issues/387) | 最小 schema/serializer、状态分类、repo 外 partition 与脱敏 |
| [#388](https://github.com/PhoenixSss/tracequant/issues/388) | 官方 DataTester 薄入口与 data evidence |
| [#389](https://github.com/PhoenixSss/tracequant/issues/389) | 官方 ExecTester 两个 logical attempts、mode evidence、reconciliation/cleanup |
| [#390](https://github.com/PhoenixSss/tracequant/issues/390) | 最小 Strategy、handler protection、mode/reconciliation/cleanup evidence |
| [#391](https://github.com/PhoenixSss/tracequant/issues/391) | fresh matrix 顺序执行、最小聚合器和 tracked acceptance |

依赖顺序为 #386 → #387 → #388 → #389 → #390 → #391。它传递已验证产物，不授权下游增加
上游或未来阶段的义务。

## 11. 阶段 5 边界与退出

阶段 4 不包括 persistence、restart/disconnect recovery、通用 partial-fill workflow、funding、
stop orders、soak、监控告警、生产 risk controls、Live admission、多 symbol/account/exchange 或
hedge/cross mode。

#386–#391 全部完成、#391 发布有效 acceptance 且 Feature completion audit 通过后，最终状态为：

```text
NAUTILUS_PRIMARY
BINANCE_DEMO_BASIC_LOOP_ACCEPTED
LIVE_NOT_APPROVED
```

## 12. 规范来源

- [TraceQuant 分阶段推进计划](<../research/foundation-selection/TraceQuant 分阶段推进计划.md>)；
- [ADR-0001](../architecture/adr-0001-nautilustrader-primary-runtime.md)；
- [NautilusTrader rc4 Binance integration](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/docs/integrations/binance.md)；
- [rc4 futures config application（L1200–L1246）](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/adapters/binance/src/futures/execution.rs#L1200-L1246)；
- [rc4 hedge-mode query（L1720–L1733）](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/adapters/binance/src/futures/execution.rs#L1720-L1733)；
- [rc4 AccountState margin info（L340–L437）](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/adapters/binance/src/futures/execution.rs#L340-L437)；
- [rc4 ExecTester config](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/testkit/src/testers/exec/config.rs#L47-L98)；
- [Binance current position mode](https://developers.binance.com/docs/derivatives/usds-margined-futures/account/rest-api/Get-Current-Position-Mode)；
- [Binance change initial leverage](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Change-Initial-Leverage)；
- [Binance Position Information V3](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Position-Information-V3)；
- [Binance exchange information](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)；
- [nautilus_trader #5039](https://github.com/nautechsystems/nautilus_trader/issues/5039)。
