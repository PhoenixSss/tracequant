# TraceQuant 阶段 4：Binance Demo 实施与验收基线

| 字段 | 值 |
| --- | --- |
| 文档状态 | v1.0 实施候选；固定 rc4 supported path（见 §1.2、§4.2） |
| 文档版本 | `v1.0` |
| 日期 | `2026-09-22` |
| Feature | [#384](https://github.com/PhoenixSss/tracequant/issues/384) |
| 固定运行时 | NautilusTrader `2.0.0rc4` / `a0400251110653b6d8ae6a9b5b89c4543fa85a2d` |
| 唯一环境 | Binance USD-M Futures Demo |
| 产品状态 | `DEMO_ONLY`、`LIVE_NOT_APPROVED` |

本文档是 Feature #384 的唯一阶段 4 实施与验收基线。阶段 4 只组合固定 rc4 已有的公开
config、DataTester、ExecTester、Strategy、cache 和 account observations；不要求 rc4 暴露其
内部 hedge-mode/leverage response，也不引入 raw Binance client。#386–#391 只能实现
§10 将其标记为 primary 或 applicable 的编号要求；primary 表示该编号唯一的实现所有者，
applicable 表示该叶项必须服从但不得复制所有权。它们可以消费前置叶项的产物，但不得重新解释、
复制所有权或增加交付义务。任何新增场景、环境、instrument、账户模式、基础设施、持续运行能力或
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
| Runtime | NautilusTrader `2.0.0rc4`，commit `a0400251110653b6d8ae6a9b5b89c4543fa85a2d` | rc5、未锁定版本、运行时身份漂移、仓库内 patched/forked `nautilus_trader` |
| Execution target | CPython 3.13、`linux-x86_64`、官方 `cp313-cp313-manylinux_2_34_x86_64` wheel | 其他 Python/platform tag、sdist/source build、非官方或重打包 wheel |
| Venue / product | `BINANCE` / `BinanceProductType.USD_M` | Spot、COIN-M、Margin、其他交易所 |
| Environment | `BinanceEnvironment.DEMO` | 默认 Live、`LIVE`、legacy `TESTNET`、自定义 endpoint |
| Instrument | `BTCUSDT-PERP.BINANCE`；provider 使用 `load_all=False` 和唯一 `load_ids` | 第二个 instrument、全量加载、动态 allowlist |
| Account | 一个 acceptance batch 由同一 runner 向三个 order-enabled 子进程注入同一份 Demo credential snapshot | 每 entry 覆盖 credential、第二个 account、完整 account ID、Live account |
| Position mode | one-way；Nautilus 使用 `OmsType.NETTING` | hedge / dual-side |
| Margin / leverage | `BTCUSDT` 为 `BinanceMarginType.ISOLATED`、`1x` | cross、动态 leverage、其他 symbol 设置 |
| Active order cap | 全局最多 `1` 个活动或 acknowledgement 未决订单 | batch、并行订单、unknown 时新订单 |
| Quantity | 开仓与 passive order 使用运行时 constraints 算出的最小有效量；任何 reduce-only close 精确等于最新已确认的 `abs(net position)` | 硬编码数量、portfolio sizing、对 close quantity 向上/向下取整、绕过适用 filter |
| Credentials | `BINANCE_DEMO_API_KEY`、`BINANCE_DEMO_API_SECRET` | 通用 Live/Testnet 变量、文件内 secret、命令行 secret |

`base_url_http`、`base_url_ws` 和 `base_url_ws_trading` 必须为 `None`；由 rc4 adapter 根据
`DEMO` 选择官方 endpoint。任何 URL override 即准入失败。DataTester 不创建 execution
client；需要认证的入口只允许 adapter 从上述两个 Demo 专用环境变量取得凭据，TraceQuant
配置对象、日志和 evidence 不得复制其值。

### 1.2 账户模式设置、准入与 rc4 行为确认

rc4 的公开 execution config 必须同时设置：

```text
oms_type = OmsType.NETTING
futures_margin_types = {"BTCUSDT": BinanceMarginType.ISOLATED}
futures_leverages = {"BTCUSDT": 1}
use_position_ids = true
```

这些字段的语义不同：`oms_type=NETTING` 声明 Nautilus OMS 预期，但不切换 Binance position
mode；`futures_margin_types` 的非“已经是目标模式”错误会使连接失败；`futures_leverages` 是
best-effort，venue 业务拒绝只产生 warning。rc4 连接时会查询实际 hedge mode 并据此生成订单参数，
但该结果和 leverage response 没有暴露到公开 Python admission surface。因此 Stage 4 不把“连接
成功”单独解释为 one-way/1x 已确认。

每个 order-enabled attempt 在创建 network client 前必须同时满足：

1. 操作者刚刚在 Binance Demo UI 中确认账户为 one-way、`BTCUSDT` isolated、1x、无活动订单且
   flat，并为**本 attempt**显式提供固定令牌 `ONE_WAY_ISOLATED_1X_FLAT_CONFIRMED`；令牌不得默认、
   缓存或跨 attempt 复用；
2. exact execution config 包含上述四个固定字段，endpoint 无 override；
3. attempt 由 §9.3 的同一 batch runner 启动，禁止 entry-level credential override；三个
   order-enabled 子进程使用 runner 在 batch 开始时取得的同一份环境 snapshot；
4. client 连接成功，从而证明 isolated 设置成功或 venue 已处于 isolated。连接失败即在首单前
   `HALTED`。

operator gate 是允许最小 Demo canary order 的风险准入，不是最终验收事实。每个包含 market fill 的
order-enabled run 必须在第一笔最小数量开仓完整成交后、任何后续开仓前，通过 rc4 公开观察完成：

- **one-way**：本 run 的 cache order/position events 不得出现 Binance hedge leg
  （`...-LONG`/`...-SHORT`）position identity；必须得到单一净 position，且相反方向 reduce-only
  close 后该 position 归零。缺失/null identity 本身不能证明 one-way；出现 hedge-leg identity、多于
  一个 open position、事件序列不完整或 identity unknown 都使本 run 不可逆地 `HALTED`；
- **1x**：确认开仓 order terminal、无其他活动/未决订单且账户没有其他 position 后，触发一次公开
  `query_account`，从随后到达的 `AccountState.info["total_initial_margin"]` 取得
  `observed_initial_margin`。将 request 至 snapshot 之间、最长 5 秒窗口内同 instrument 的合格 mark
  prices 取最小/最大值；窗口相对价差必须不超过 `0.5%`。令中点 mark 计算
  `expected_initial_margin = abs(position_quantity) * mark_price_midpoint`，允许误差为
  `abs(position_quantity) * (mark_price_max - mark_price_min + price_increment) + one_currency_quantum`。
  observed 与 expected 的绝对差不得超过该值。缺少 snapshot、存在其他 margin owner、窗口过宽、
  结果超差或无法唯一归因都使本 run `HALTED`；
- **isolated**：连接阶段的目标设置结果与上述单 position/无其他 margin owner 前提共同记录为
  `isolated_behavior_confirmed=true`；不得从 instrument 的默认 margin 值推导。

行为确认失败后禁止新的正常场景订单；只有在原订单 terminal、零 active/inflight 且净 position
确定时，才按 §5 提交唯一 reduce-only cleanup。清场成功仍保持 `HALTED`。三个确认都通过后才可
记录 `account_mode_behavior=consistent` 并继续。passive-only ExecTester attempt 必须绑定同一 batch
中先完成且通过上述确认的 market-close attempt，并仍重新执行 operator gate；它不能独立建立模式
证据。

rc4 的 `BinanceExecutionClientConfig.account_id` 是调用方别名，不是交易所身份。Stage 4 不要求
Binance derivatives API 未公开的稳定 account ID，也不记录或散列该别名；单 account 边界由一个
batch runner 的不可覆盖 credential snapshot 和 attempt-local operator gate 保证。不得读取 warning、
private/Rust client 或 raw Binance REST/WebSocket 来替代以上公开行为证据。

### 1.3 最小有效数量

每个 order-enabled 场景都必须从本次 attempt 加载的 rc4 公开 Nautilus instrument 得到数量，
不接受源码或用户配置中的静态 quantity。Stage 4 的“instrument constraints”明确指 rc4
`Instrument` 规范化并公开的 `size_increment`、`minimum_quantity`、`maximum_quantity`（若有）和
`minimum_notional`（若有）；不得要求或伪造 rc4 未保留的 raw `MARKET_LOT_SIZE` 字段。venue 对
成功订单的 accepted/filled 事实是未公开 venue rule 的最终确认；确定 reject 是安全的 `HALTED`，
不得修改 quantity 重发。

开仓/passive order 先冻结 `notional_price`：limit 使用 §3.1 合格 quote 推导并校验精确提交价格
（passive buy 为 `best bid - 1 price increment`）；market 使用同 instrument 的合格 mark price。
quantity 是满足上述公开 minimum quantity、size precision/increment、maximum quantity 和 minimum
notional 的最小 step 倍数。reduce-only close 不计算 minimum notional，使用下文精确持仓原值。
缺少公开 constraint、价格输入或唯一最小值时停止；不得猜测、保守放大或扩大为 portfolio sizing。

Strategy 的 quantity 计算、构造和提交前复核绑定同一价格输入。rc4 ExecTester quantity 在 config
构造时固定，因此 §4.2 允许其薄入口先用同一 command 内的 rc4 data-only planning phase 取得
instrument 与价格，计算 quantity 后立即构造 tester；从 planning sample 到 tester 首次 submit 的
总时限为 10 秒。提交后必须用 cache 中实际 order price 或最近 pre-submit mark price 重新运行同一
最小量公式；不相等即使 venue 接受也为 `HALTED`，但不得取消已发生的安全清场义务。

任何正常或失败路径的 reduce-only market close 使用独立的精确清平算法，不能复用上述最小开仓量：

1. 只有证明前一订单 terminal、active/inflight count 为零，并由当前入口的 §8 observation profile
   得到唯一、非零的 one-way net position 后，才冻结一次 `close_quantity = abs(position)`；失败路径
   必须逐项执行 §5 的同一 proof 顺序；
2. close side 必须与该 position 相反，订单必须是 market + `reduce_only=true`，提交 quantity 必须与
   `close_quantity` 数值完全相等；禁止按 step/minimum/notional 向上放大、向下截断或提交 dust
   follow-up；
3. 精确 `close_quantity` 必须可由 Nautilus `Quantity` 无损表示，并通过 rc4 公开 Instrument 的
   size precision/increment 以及 minimum/maximum quantity（若公开）。Binance USD-M 的 `-4164`
   合同豁免 reduce-only order 的 `MIN_NOTIONAL`；cleanup 不得应用 minimum notional、取得 mark
   price 或放大 exposure。venue 若因公开 surface 未表达的 filter 拒绝 close，必须记录确定 reject、
   保持 `HALTED/cleanup_incomplete`，不得修改 quantity 重发；
4. 精确 `close_quantity` 违反任一公开 constraint 时不得创建订单；不得复制 raw exchange filters
   或把开仓 quantity 代替真实 exposure；
5. 构造前再次读取 position；若它与冻结值不完全相同，原 quantity 作废，必须重新完成
   terminal → zero-active → position proof。只有同一次冻结值完成构造、提交并最终证明 position 为零，
   才能记录 `cleanup_complete`；任何 partial/late fill 或 cancel/fill race 都遵循同一规则。

该合同也适用于官方 ExecTester。其固定 `order_qty`/`open_position_on_start_qty` 只能来自 §4.2
同一 command 的短时 planning phase；先前验收 run、硬编码、保守放大或跨 batch 快照均不允许。

### 1.4 可执行身份准入

`source_commit`、dependency lock 和 runtime identity 必须描述本次进程实际执行的 bytes，而不是把
预期常量写入 evidence。每个入口必须在 import/config validation 阶段执行以下唯一准入，并在任何
network client 创建前完成；任一步不可证明或不相等都以 `identity_unverified` 或更具体的稳定 code
进入 `HALTED`：

1. **TraceQuant source**：`source_commit` 是当前 repository `HEAD` 的完整 commit SHA。所有 tracked
   文件的 index 与 working-tree bytes/mode 必须和该 commit 的 blob 完全相同，任何 staged、unstaged
   或 deleted tracked path 都失败。`src/tracequant/**` 下不得存在 Git 未跟踪或 ignored 的可导入/
   可执行文件或 symlink（包括 `.py`、`.pyi`、`.pyc`、native extension）；已经载入的每个
   `tracequant.*` module realpath 必须位于该 commit 的 `src/tracequant`，是 tracked regular file，且
   bytes 与对应 blob 相同。`PYTHONPATH`、cwd 或其他 import root 产生第二个 namespace/source 时失败。
   evidence 记录 commit、Git tree object ID，以及按 §9.1 规则对按 path 排序的
   `{path, mode, blob_oid}` tracked manifest 求得的 `source_tree_digest`；只记录“staged/unstaged
   change count = 0、untracked/ignored executable count = 0”和 import-origin digest，不记录本机绝对路径。
2. **Dependency lock**：运行时 `uv.lock` 必须是上述 commit 的 tracked blob 且 bytes 完全相等；
   `dependency_lock_digest` 是这些实际 bytes 的 SHA-256。`pyproject.toml` 与 `uv.toml` 同样必须来自
   该 clean commit；lock 中必须仍是 PyPI registry 的精确 `nautilus-trader==2.0.0rc4`，且
   `uv.toml` 仍禁止该包 source build。
3. **批准的 wheel → commit 映射**：唯一映射是
   `nautilus_trader-2.0.0rc4-cp313-cp313-manylinux_2_34_x86_64.whl` / SHA-256
   `0c97c4385d55833fc3cce4934ca48a2a5fa0ea1d69d0a775b88c2bbf6fb79005` → upstream commit
   `a0400251110653b6d8ae6a9b5b89c4543fa85a2d`，其批准记录是
   `docs/releases/tracequant-v2-bootstrap-2026-09-13.md`。version 字符串本身不能建立该映射；
   其他 lock 中的 platform wheel 或 sdist hash 不属于本 Demo target。
4. **安装与实际 distribution bytes**：运行环境必须由 clean、disposable environment 的
   `uv sync --locked --dev --no-build-package nautilus-trader --no-cache` 产生，并带有 repo 外、脱敏的
   provisioning attestation，记录实际下载 wheel 的 filename/SHA-256、index identity、WHEEL tag 和
   上述映射。provisioning 后不得运行会 import `nautilus_trader` 的命令；四个入口必须在同一已证明的
   environment 中依次以 `PYTHONDONTWRITEBYTECODE=1 python -B <entrypoint>` 启动，且每个入口在 import
   `nautilus_trader` 前同时断言环境变量值精确为 `"1"`、`sys.dont_write_bytecode is True`。任一条件不满足
   即以 `bytecode_write_not_disabled` 在创建 client 前 `HALTED`；入口及其 child process 不得清除或覆盖
   该环境变量。这样前序进程不会产生未列入 wheel `RECORD` 的 `__pycache__/*.pyc`，后序进程仍检查同一
   installed tree；不得通过运行间删除 cache、忽略 `.pyc` 或为每个入口换一份未证明的新环境规避身份
   连续性。运行时在 import 前使用 distribution metadata 重算：名称/版本必须精确匹配，
   `direct_url.json` 必须不存在，module origin 必须位于该 distribution root；逐项验证 `RECORD` 中
   每个文件的 size/hash，拒绝缺失、hash mismatch，以及 `nautilus_trader` package roots 或对应
   `.dist-info` 中未列入 `RECORD` 的 `.py`/`.pyi`/`.pyc`/native executable。`installed_tree_digest` 是按 path 排序的
   `{path, size, sha256}`（包括对 `RECORD` 本身现场求得的值）数组按 §9.1 canonical bytes 计算的
   SHA-256，并且必须等于 provisioning attestation 的值。
5. **Runtime digest 投影**：`runtime.identity_digest` 只对以下完整对象按 §9.1 canonical-byte 规则
   求 SHA-256：`distribution`、`version`、`upstream_commit`、`wheel_filename`、`wheel_sha256`、
   `wheel_tag`、`installed_tree_digest`、`bytecode_write_disabled=true`；计算时只排除
   `identity_digest` 本身。字段缺失、expected/actual
   不一致、attestation 缺失或 digest 不一致都失败，不能用文档常量补值。

## 2. 稳定行为要求

| ID | 冻结要求 |
| --- | --- |
| `ST4-REQ-001` | 每个入口在 import/config validation 阶段执行 §1.4：验证 clean source tree、实际 lock bytes、批准 wheel→commit 映射、安装后 distribution bytes 和 runtime digest；身份不能由期望常量自证，任一不符时不得创建客户端。 |
| `ST4-REQ-002` | 唯一环境是 Binance USD-M Futures Demo，endpoint 不可覆盖，凭据只来自两个 Demo 专用变量。 |
| `ST4-REQ-003` | 唯一 instrument、同一 batch credential、one-way、isolated、1x、最多一个活动/未决订单是不可配置边界；模式按 §1.2 由 attempt-local operator gate 与 rc4 公开行为证据共同确认。 |
| `ST4-REQ-004` | order-enabled 入口执行 §1.2 的一次性 operator gate、固定 rc4 config 和首个最小 canary fill 后的自动行为确认；gate 或 config 不能单独支持成功，确认失败时只允许有限清场并保持 `HALTED`。 |
| `ST4-REQ-005` | 开仓/passive quantity 按 §1.3 从 rc4 公开 Instrument 与 order-type-specific `notional_price` 计算为最小有效量；ExecTester 使用同一 command 的短时 planning phase 并事后复核；reduce-only close 精确等于最新已确认 `abs(position)`，不得取整或放大，不应用 `MIN_NOTIONAL`。 |
| `ST4-REQ-006` | 所有网络和订单阶段使用 §3 的唯一 deadline；v1.0 不提供 timeout override。 |
| `ST4-REQ-007` | 核对只读取 §8 的 entry-specific rc4 公开观察面：ExecTester 使用 `LiveNode.cache` 可检索 object graph，Strategy 使用 callbacks/cache/account snapshots；不得要求取得 rc4 公共 Python API 不返回的 tester 或 report objects。TraceQuant 不拥有第二套 adapter、order、position、ledger、accounting 或 reconciler。 |
| `ST4-REQ-008` | DataTester 只执行 §4.1 的数据场景，不创建 execution client。 |
| `ST4-REQ-009` | ExecTester 只执行 §4.2 的两个互斥主 attempt 及仅在确定非零残余仓位时启动的 cleanup-only attempt；所有订单由官方 ExecTester 提交，planning/reconciliation 薄封装只读，特殊 risk bypass 不得进入普通 Strategy。 |
| `ST4-REQ-010` | Demo Strategy 只执行 §4.3 的固定顺序，不读取阶段 3 模型、不产生 alpha、不做 portfolio sizing。 |
| `ST4-REQ-011` | Strategy 只使用 §5 的固定前进状态；终态只有 `COMPLETE` 或 `HALTED`，不得抽象为可配置工作流引擎。 |
| `ST4-REQ-012` | 最终验收按 §4.4 从四个全新外部分区依次消费 DataTester、两个独立 ExecTester attempt、Strategy 证据，并在同一 batch/identity 下聚合。 |
| `ST4-REQ-013` | 成功矩阵只包括 data、market complete fill、passive limit accepted/canceled、long/short、reduce-only close 和最终清场。 |
| `ST4-REQ-014` | 阶段 4 完成后停止扩展并保持 `LIVE_NOT_APPROVED`；§12 的能力只能由阶段 5 或以后承接。 |

## 3. 唯一 deadline

所有 deadline 使用 monotonic clock，自触发动作写入本地 command/event queue 后开始计算；
达到边界即超时，不因日志、心跳或无关事件续期。v1.0 不允许 CLI、环境变量或配置文件覆盖。

| 阶段 | Deadline | 成功事件 |
| --- | ---: | --- |
| network connect | 30 秒 | 所需 data client（以及 order-enabled 场景的 execution client）均连接 |
| ready | 60 秒 | instrument 已加载、订阅活动，且 order-enabled 场景完成账户准入并取得 §3.1 合格 quote |
| DataTester observation | 60 秒 | 同时取得有效 quote、trade、instrument identity/constraints 和时间戳样本 |
| order price readiness | 10 秒 | 取得 §3.1 对该订单类型要求的合格 quote/mark-price 输入，以精确 limit 提交价格或 market mark price 完成 quantity 计算和提交前复核 |
| order acceptance | 10 秒 | 唯一订单收到确定 `OrderAccepted` 或确定 reject |
| market / reduce-only fill | 30 秒 | 订单完整成交；partial fill 不延长 deadline |
| cancellation | 10 秒 | cancel 被确定确认，或 race 经后续 §8 entry observation profile 确定为 fill |
| reconciliation | 30 秒 | 所需 §8 entry observation profile 完成分类 |
| cleanup | 60 秒 | 无活动/未决订单、无持仓、无 unresolved unknown |

连接或 ready 超时不允许提交订单。order acceptance 超时是 ambiguous acknowledgement，不能
被当成 reject；只能进入 reconciliation/安全清场。任何 cleanup deadline 到期都保持
`HALTED`，不得发布成功记录。

### 3.1 Order-enabled price freshness

ExecTester 和 Strategy 的开仓 market/limit order 使用本节规则。
合格 quote 必须属于
`BTCUSDT-PERP.BINANCE`，具有非空 bid/ask，`ts_event` 不得比本地 UTC wall clock 快超过 1 秒，
检查时的 `now - ts_event` 不得超过 5 秒，并且 `ts_event` 不得早于本次 run 对该 quote stream
已经接受的最后一个非重复 timestamp。年龄比较使用检查瞬间的 wall clock；10 秒等待 deadline
使用 monotonic clock，两者不能互相替代。

开仓 market order 还必须通过正常 Nautilus 公共 data/cache surface 取得同一 instrument 的正数
mark-price sample。它采用与 quote 相同的 identity、future 1 秒、age 5 秒和本 run 内 timestamp
单调规则，但维护独立的 mark-price stream last-seen timestamp；禁止 raw Binance client、日志或
private object。固定 runtime 若不公开该输入，开仓 market order 必须 fail closed。

order-enabled ready 必须先取得一份合格 quote。此后每一次正常场景开仓订单提交前都重新启动一次
10 秒 order price readiness deadline。limit order 在 deadline 内取得新合格
quote，先推导并校验精确提交价格，再用该价格计算 quantity；market order 在 deadline 内取得新合格
mark-price sample，并用它计算 quantity。passive price 显示仍来自同一合格 quote 的 best bid。
提交前复核时，所绑定 sample 年龄仍须不超过 5 秒，identity/price constraints 仍匹配且期间未观察到
对应 stream 更新但倒退的 timestamp；不满足就丢弃该输入并在原 10 秒 deadline 内等待下一份，不能
重新启动 deadline。

deadline 到期、future/stale/out-of-order、缺少开仓订单类型所需 quote/mark price 或 instrument identity
不匹配时，本次运行进入 `HALTED`，并禁止提交该正常场景订单。reduce-only cleanup 按 §1.3 不执行
`MIN_NOTIONAL` 校验，因此不以缺少或 stale mark price 为由拒绝精确清平；它仍须通过 terminal、
zero-active、position 与适用数量 filter proof。不得用最近一次 DataTester run、ready 时缓存但在
提交时已过期的 quote/mark price、trade price 或 wall-clock sleep 代替本规则，也不得以 bid/ask
代替 market order 的 mark price。

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

入口必须使用官方 `ExecTesterConfig`，并要求显式 `--order-enabled` 意图以及 §1.2 的精确
operator token。TraceQuant 薄封装只负责短时 data-only quantity planning、冻结两个主 attempt 的
config、启动/停止 tester、运行无 strategy 的只读 reconciliation node、读取 §8.1 cache graph 和输出
证据；所有开仓、limit、cancel 和 cleanup order 都必须由官方 ExecTester 提交。

rc4 的 fixed quantity 和同步 `on_stop` 行为通过分离职责与严格调用时机使用：

1. 每个主 attempt 先在同一 command 和 partition 内启动 rc4 data-only planning phase，仅加载目标
   instrument 并取得 §3.1 合格 quote/mark price；按 §1.3 计算 quantity，立即停止 data client，并在
   10 秒总时限内构造 `ExecTesterConfig`。planning 不创建 execution client、不提交订单；
2. tester 运行期间薄封装只读 cache，不调用 tester 私有方法、不在 tester 外下单；实际 order 的
   price/最近 pre-submit mark price 必须证明 planning quantity 仍是 §1.3 的最小值，否则场景失败；
3. `cancel_orders_on_stop` 与 `close_positions_on_stop` 在任何一个 tester instance 中不得同时为 true。
   market instance 只有在 opening order 已 terminal 且 active/inflight 为零后才允许 stop-and-close；
   passive instance 只 cancel、不 close；
4. passive stop 后以新的无 strategy LiveNode 通过正常 Nautilus reconciliation/cache 路径等待原 order
   terminal 并证明零 active/inflight。若确定 flat，结束；若确定非零 position，场景已失败，并且只在
   该 proof 完成后启动一个 cleanup-only 官方 ExecTester instance；若状态 unknown 则不得 cleanup；
5. cleanup-only instance 禁用 open/limit/stop legs 和 cancel-on-stop，启用
   `close_positions_on_stop=true`、`close_positions_qty_precision=None`、
   `reduce_only_on_stop=true`，并复用原 passive instance 的 trader/strategy/account/instrument identity，
   使 reconciliation 后的 position 仍由该 strategy 唯一拥有。它只在 §5 proof 已完成时 stop 一次，
   使官方 tester 从 cache 的精确 position quantity 提交唯一 reduce-only market close。venue reject、
   残余仓位或 deadline 都保持 `HALTED/cleanup_incomplete`，不得改量重发。

唯一计划由两个互斥主 attempt 组成；各自使用独立 LiveNode、repo 外 partition 和唯一
strategy/order tag，开始前均证明 flat、零 active/inflight，并重新执行 §1.2 admission：

1. **market-close attempt**：planning phase 计算 market buy quantity；tester 关闭全部 limit/stop
   legs，设置该值为 `open_position_on_start_qty` 且 `open_position_on_first_quote=true`，只提交唯一
   market buy。完整 fill、terminal 和零 active/inflight 后先完成 §1.2 自动行为确认，再以
   `cancel_orders_on_stop=false`、`close_positions_on_stop=true`、exact/no-truncation、reduce-only
   配置停止 tester，等待唯一 market sell 完整成交并证明 flat；
2. **passive-cancel attempt**：planning phase 按 `best bid - 1 price increment` 计算 limit quantity；
   tester 禁用 open-position/stop/sell，只启用一次性 post-only limit buy，设置
   `tob_offset_ticks=1`，禁止 modify、追价和补单。accepted 后以
   `cancel_orders_on_stop=true`、`close_positions_on_stop=false` 停止；随后按第 4、5 项完成只读
   reconciliation，以及仅在确定非零 position 时的官方 cleanup-only failure path。

两个 attempt 的证据共同构成一个 `exec_tester` required scenario；任一个失败都会使该 scenario
`FAIL/HALTED`，后一个不得用来弥补前一个。两者绝不同时运行，所以 active/pending order cap 仍为
1。每个主 attempt 分别生成一份 §9.1 canonical evidence record；failure path 中的 reconciliation
与 cleanup-only component 使用同一 scenario/partition，并把 component config、开始结束时间和
cache fact digest 纳入该失败 record，不能隐藏子运行。rc4 的
`LiveNode.add_builtin_strategy("ExecTester", config)` 返回 `None`，也没有公开 strategy
getter；薄封装不得声称持有 tester 或读取其 callback 参数。场景边界只能由薄封装按 §8.1 只读
轮询 `LiveNode.cache` 识别：market leg 是唯一新增且完整 filled 的 market order；close leg 是唯一
新增且完整 filled 的 reduce-only order 且 `positions_open()` 为空；passive leg 是唯一新增、先
accepted 后 canceled 且没有 `OrderFilled` 的 post-only order。多余/无法归属的 order、deadline、
缺失 history 或不相容 terminal fact 均进入非 `consistent` 分类。

只有该独立入口可以使用官方 tester 所需的最小 risk bypass；它必须在入口内封闭且不能由普通
Strategy import、配置或调用。post-only order 若 reject、partial fill 或在 cancel 前/期间成交，
场景立即判失败；只有在原 order terminal、零 active/inflight 且 position 重算为确定非零后，才能按
§1.3 由 cleanup-only tester 以 `abs(position)` 原值做唯一一笔 reduce-only cleanup；原值不通过公开
constraints 时不得提交。不得为了让场景通过而改成 aggressive limit、重发、在 tester 外提交订单
或扩大 tester 功能。

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
立即停止并进入 `HALTED`，且只能按 §5 的严格顺序安全清场。Strategy 不读取阶段 3 artifact，不接受 signal、target
position 或 sizing 参数。

### 4.4 最终证据聚合与发布

在同一 source commit/source-tree digest、dependency lock、已通过 §1.2 admission/behavior confirmation
的精确 rc4 runtime 和 frozen config identity 上，使用四个
全新且初始为空的 repo 外分区依次运行 DataTester、ExecTester market-close attempt、ExecTester
passive-cancel attempt 和 Strategy。四个分区的逻辑 ID 必须分别为 `data_tester`、
`exec_tester_market_close`、`exec_tester_passive_cancel`、`demo_strategy`，每个分区恰好产生一份
§9.1 canonical evidence record。聚合器只验证既有
证据，不创建客户端或订单。所有 required scenario 为 `PASS`、所有 identity/digest 一致且最终
order/position/unknown 清场后，才可原子发布
`docs/product/stage4-binance-demo-acceptance.json`。任何失败不得创建新成功文件，也不得覆盖
既有成功文件。operator gate、config 或离线测试不能替代缺失的 order-enabled 行为证据。

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
`HALTED` 是不可逆 latch：禁止任何新开仓；所有 failure matrix 分支只能按以下顺序前进，不能把各步
当作并列动作：

1. 对可识别的原 order 至多请求一次 cancel，并通过公开 `query_order`/`query_account` 与正常
   callback/cache 更新执行 reconciliation；
2. 等待并证明原 order 已进入 terminal；
3. 证明 active/inflight order count 为零；
4. 从同一 entry observation profile 重新计算 position；
5. 只有 position 为确定非零时，才按 §1.3 冻结 `close_quantity = abs(position)`；仅当它可无损
   构造并原值通过 reduce-only 适用的 rc4 公开数量 constraints 时提交恰好一笔 cleanup，否则不提交并记录
   `cleanup_incomplete`。

任一 proof 在对应 deadline 内缺失、冲突或 unknown 时保持 `HALTED`，停止自动动作且不得提交
cleanup。cleanup 本身完成后仍保持 `HALTED`，不能把本次结果改回 `COMPLETE`。

## 6. 失败关闭矩阵

| 情况 | 事件前置状态 | 允许动作 | 禁止动作 | 终态 | 最小证据 |
| --- | --- | --- | --- | --- | --- |
| source / lock / runtime identity 无法验证或漂移 | import/config validation | 按 §1.4 记录稳定 failure code 和已完成的脱敏 actual/expected digests | 用 expected 常量补值、创建 network client、继续场景或发布成功 | `HALTED` | source-tree/lock/runtime verification result；不得含绝对路径 |
| connect / subscription timeout | 尚未 ready | 停止 client，写 timeout | 创建 execution client（DataTester）或提交订单 | `HALTED` | deadline、已连接组件、缺失 subscription |
| 空数据 | DataTester observation 到期 | 停止并保留订阅统计 | 把连接成功当行情成功 | `HALTED` | quote/trade counts 均或任一为零 |
| stale / out-of-order timestamp | 正在观察对应 stream | 停止场景；无订单时直接结束 | 忽略、重排后伪装成功 | `HALTED` | stream、前后 timestamp、age |
| order reject | 等待 acceptance | 记录确定 reject；若已有 exposure 则严格按 §5 terminal → zero-active → position → cleanup 顺序清场 | 修改参数并重发、未证明零 active/inflight 就 cleanup | `HALTED` | client order reference、reason、cache status |
| ambiguous acknowledgement | 等待 acceptance 超时或 transport unknown | Strategy 最多发出一次公开 `query_order` 并等待其 profile 更新；ExecTester 停止正常场景后只启动 §4.2 无 strategy reconciliation node，等待 cache graph 收敛 | 把 query 的 `None` 返回值当 report、当作 reject、用新 ID 重发、继续开仓、原 order 未 terminal 或 active/inflight 非零时 cleanup | `HALTED` | command/entry identity、timeout/transport fact、terminal/active proof、后续 entry-profile result |
| duplicate / out-of-order event | 任一 order state | 忽略重复的状态推进并 reconcile；记录冲突 | 二次推进、二次提交 | `HALTED` | event identity、expected/observed state |
| cancel/fill race | 等待 cancel | reconcile；严格按 §5 证明原 order terminal 与零 active/inflight 后重算 position；仅当 `abs(position)` 原值通过 §1.3 rc4 公开 constraints 时提交唯一 cleanup | 同时假定 canceled 和 filled、重发 cancel/order、用当前 cumulative fill 提前 cleanup、调整 exposure 以通过 constraint | `HALTED` | §8 entry profile 的 cancel/fill facts、terminal/active proof、冻结 position/constraint 结果、最终 position |
| late fill | 已收到 cancel 或已开始 reconcile | 更新 Nautilus-owned 事实；重新完成 §5 terminal/zero-active proof 后仅按最新 `abs(position)` 原值清场 | 保持原 flat 假设、在原 order 仍可 late fill 时 cleanup、取整/放大 exposure、发布成功 | `HALTED` | late fill identity、先前状态、terminal/active proof、冻结 position/filter 结果、cleanup result |
| unexpected partial fill | 等待任一完整 fill | 停止成功路径；先等原 order terminal 与零 active/inflight，再仅按重算的 `abs(position)` 原值有限清场；原值不合法则不提交 | 按当前 cumulative quantity 提前 cleanup、取整/放大、等待/补单凑满、实现通用 partial-fill workflow | `HALTED` | ordered/cumulative/leaves quantity、terminal/active proof、entry-profile position/filter observations |
| handler exception | 任一实际使用的 Strategy handler | 顶层保护记录 fatal diagnostic、锁住提交、按 §5 顺序进入安全清场 | 吞掉异常、继续状态推进 | `HALTED` | handler、异常类型/脱敏信息、state、submission count |
| observation conflict | reconciliation / cleanup | 保留 §8 entry profile 的 Nautilus facts 和 `conflicting` 分类 | 选择有利事实、用 raw REST 裁决 | `HALTED` | observation digests、冲突字段 |
| cleanup timeout | `CLEANING` 或 `HALTED` cleanup | 停止自动动作，保留外部诊断 | 标记 flat、发布成功、无限重试 | `HALTED` | active orders、position、unknown、deadline |

### 6.1 rc4 handler exception 防护

upstream #5039 报告 rc4 的 Python Strategy handler exception 可能在 pyo3/Rust 边界被静默
吞掉。阶段 4 Strategy **实际实现的每一个 handler** 都必须在最外层 `except Exception`；范围包括
但不限于 lifecycle（如 `on_start`/`on_stop`）、quote/data、timer、order 和 position handlers，
以 #390 最终代码中的完整 handler 清单为准，不能只保护 order/position callbacks。异常离开 Python
handler 前必须同步完成三件事：写入脱敏 fatal diagnostic、设置不可逆 submission inhibit latch、
转入 `HALTED`。不得依赖 runtime 自动打印或停止 node；保护逻辑本身失败也必须保持 inhibit latch。

#390 必须从实际 Strategy class 机械枚举/断言冻结的 handler 清单，并为清单中的**每一个 handler**
提供一个独立、确定性、仅离线测试可启用的 fault-injection case。每个 case 在该 handler body 内
抛出异常，并分别证明 handler identity 与脱敏 diagnostic 存在、终态为 `HALTED`、inhibit latch
已设置、异常后的 submission count 不增加；测试还必须在新增 handler 未加入 case table 时失败。
注入只允许由一个阶段专用枚举选择目标 handler，不得成为任意 callable、plugin/hook registry，
联网配置必须拒绝任何非 disabled 值。

## 7. 安全要求

| ID | 冻结要求 |
| --- | --- |
| `ST4-SAFE-001` | 非 Demo、越界、身份不完整、缺少 operator token 或 secret 来源错误，必须在 network client 创建前失败。 |
| `ST4-SAFE-002` | `missing`、`conflicting`、`unknown` 或 `cleanup_incomplete` 都是失败，不得降级为 warning 或成功。 |
| `ST4-SAFE-003` | DataTester 对空数据、错误 identity、stale/future/out-of-order timestamp 失败关闭。 |
| `ST4-SAFE-004` | ExecTester bypass 仅限独立入口；普通 Strategy 不能 import 或配置该能力。 |
| `ST4-SAFE-005` | ExecTester 的 reject、ambiguous ACK、unexpected/partial fill 和 cancel/fill race 禁止盲目重发；只有 §4.2 无 strategy reconciliation node 按 §5 证明原订单 terminal、零 active/inflight 并重算确定 position，且精确 `abs(position)` 原值通过 §1.3 rc4 公开 constraints 后，cleanup-only 官方 ExecTester 才可执行唯一 cleanup；不得应用 `MIN_NOTIONAL`。 |
| `ST4-SAFE-006` | Strategy 对 ambiguous、duplicate、out-of-order、timeout 和 conflict 设置不可逆 `HALTED` latch，禁止新开仓。 |
| `ST4-SAFE-007` | partial fill、late fill 或 passive limit 成交必定使验收失败；不得按中间 cumulative fill 提前清场，只能在原订单 terminal、零 active/inflight 后以 Nautilus 重算的精确 `abs(position)` 原值有限清场；不得为满足 rc4 公开 minimum/step 取整或放大，也不得把 reduce-only 豁免的 `MIN_NOTIONAL` 当作拒绝清场条件；原值违反公开 constraint 时禁止提交并保持 `cleanup_incomplete`。 |
| `ST4-SAFE-008` | 所有实际 handler 具有 §6.1 顶层保护；冻结清单中的每个 handler 都有独立 fault-injection case，并以 completeness assertion 防止新增 handler 漏测。 |
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
- **Account mode**：market-close 与 Strategy 按 §1.2 从 cache order/position identity、唯一净持仓、
  mark price 和公开 account margin snapshot 形成行为确认；passive-cancel 只可引用同 batch 已通过的
  market-close `run_id`。operator token、config 或连接成功不能单独形成 `consistent`；
- **Cleanup**：cache 的 open/in-flight order counts、open positions 和当前 entry profile 的 terminal
  facts 同时为零/closed，且无 deadline 或其他 unresolved unknown。发生过非零 exposure 时还必须记录
  §1.3 冻结的 exact position、close quantity、逐项 rc4 公开 constraint 判定、`MIN_NOTIONAL=not_applicable`
  marker 和最终 flat proof；若精确
  exposure 不可合法提交，分类必须是 `cleanup_incomplete`。单独的 balance 一致不能证明清场。

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

以下类型和对象定义是 v1 schema 的规范形式，而非示例。`object{...}` 表示成员集合封闭、每个列出
成员恰好出现一次且拒绝额外成员；`[T; N]` 表示长度恰为 `N` 的 array，`[T]` 表示保持事件发生顺序的
array；`A | B` 表示联合类型。`uint` 是 `0..9007199254740991` 的 JSON integer。`sha256` 是
`^[0-9a-f]{64}$`，`git_oid` 是 `^[0-9a-f]{40}$`，`stable_code` 是
`^[a-z][a-z0-9_]{0,63}$`，`timestamp` 与 `decimal` 分别服从本节前述时间和十进制字符串规则。
除显式写出的 `null` 外没有 nullable 字段。

公用封闭对象如下；它们在 evidence、acceptance 和 frozen config 中复用时必须保持完全相同的成员名
和类型：

```text
SourceTree = object{
  git_tree_oid: git_oid,
  source_tree_digest: sha256,
  staged_change_count: 0,
  unstaged_change_count: 0,
  untracked_ignored_executable_count: 0,
  import_origin_digest: sha256
}

RuntimeIdentity = object{
  distribution: "nautilus-trader",
  version: "2.0.0rc4",
  upstream_commit: "a0400251110653b6d8ae6a9b5b89c4543fa85a2d",
  wheel_filename: "nautilus_trader-2.0.0rc4-cp313-cp313-manylinux_2_34_x86_64.whl",
  wheel_sha256: "0c97c4385d55833fc3cce4934ca48a2a5fa0ea1d69d0a775b88c2bbf6fb79005",
  wheel_tag: "cp313-cp313-manylinux_2_34_x86_64",
  installed_tree_digest: sha256,
  bytecode_write_disabled: true,
  identity_digest: sha256
}

Instrument = object{
  instrument_id: "BTCUSDT-PERP.BINANCE",
  price_precision: uint,
  price_increment: decimal,
  size_precision: uint,
  size_increment: decimal,
  minimum_quantity: decimal,
  maximum_quantity: decimal | null,
  minimum_notional: decimal | null,
  constraint_source: "RC4_PUBLIC_INSTRUMENT",
  constraints_digest: sha256
}
```

`source_tree_digest` 的输入是 §1.4 已定义的、按 path 排序的
`[{"path":string,"mode":"100644"|"100755"|"120000"|"160000","blob_oid":git_oid}]`；
path 必须是 NFC、非空、无 `.`/`..` segment 的 repository-relative POSIX path。`installed_tree_digest`
输入中的 path 使用同一规则、`size` 是 `uint`。`import_origin_digest` 的输入是按
`module`、再按 `relative_path` 排序的
`[{"module":string,"relative_path":string,"sha256":sha256}]`，其中 path 必须是 repository-relative
POSIX path。`RuntimeIdentity.identity_digest` 删除本对象的 `identity_digest` 后计算；
`installed_tree_digest` 仍使用 §1.4 的精确投影。`Instrument.constraints_digest` 删除本对象的
`constraints_digest` 后计算。以上全部使用 §9.1 canonical bytes。

每类最小观察使用以下封闭对象。为避免让实现叶项自行决定 digest 内容，repo 外分区中的 digest source
也被冻结为以下封闭 fact 对象；reference 使用 digest 只为脱敏，不允许用本地路径、完整账户标识或
venue raw payload 代替。`TimedFact` arrays 按 `ts_event`、`ts_init`、`identity_digest` 排序；
`OrderFact[]`、`FillFact[]` 和 `PositionFact[]` 按 `ts_event`、再按第一个
`*_reference_digest` 排序。`EventFact[]` 是唯一例外，必须保持 callback/cache event history 的
实际发生顺序，不按 event time 重排；同值仍重复出现，不能作为 set 去重：

```text
TimedFact = object{
  identity_digest: sha256,
  ts_event: timestamp,
  ts_init: timestamp
}
EventFact = object{
  event_type: stable_code,
  event_reference_digest: sha256,
  ts_event: timestamp,
  ts_init: timestamp
}
AccountSnapshot = object{
  currency: string,
  total: decimal,
  free: decimal,
  locked: decimal,
  ts_event: timestamp
}
MarketDataFacts = object{
  quotes: [TimedFact],
  trades: [TimedFact],
  mark_prices: [TimedFact]
}
OrderFact = object{
  client_order_reference_digest: sha256,
  venue_order_reference_digest: sha256 | null,
  side: "BUY" | "SELL",
  order_type: "MARKET" | "LIMIT",
  quantity: decimal,
  reduce_only: true | false,
  post_only: true | false,
  cumulative_quantity: decimal,
  leaves_quantity: decimal,
  terminal_status: "FILLED" | "CANCELED" | "REJECTED" | "EXPIRED" | "UNKNOWN",
  ts_event: timestamp,
  event_sequence_digest: sha256
}
FillFact = object{
  trade_reference_digest: sha256,
  order_reference_digest: sha256,
  side: "BUY" | "SELL",
  last_quantity: decimal,
  price: decimal,
  commission_amount: decimal,
  commission_currency: string,
  liquidity_side: "MAKER" | "TAKER",
  cumulative_quantity: decimal,
  average_price: decimal,
  position_change: decimal,
  position_reference_digest: sha256,
  before_account_snapshot_digest: sha256,
  after_account_snapshot_digest: sha256,
  ts_event: timestamp
}
PositionFact = object{
  position_reference_digest: sha256,
  side: "LONG" | "SHORT" | "FLAT",
  quantity: decimal,
  realized_pnl: decimal,
  ts_event: timestamp,
  event_sequence_digest: sha256
}
BalanceFact = object{
  before: AccountSnapshot,
  after: AccountSnapshot,
  commission_total: decimal,
  realized_pnl_total: decimal,
  observed_change: decimal,
  explained_change: true | false,
  before_ts_event: timestamp,
  after_ts_event: timestamp
}

MarketDataObservation = object{
  classification: "consistent" | "missing" | "conflicting" | "unknown",
  quote_count: uint,
  trade_count: uint,
  mark_price_count: uint,
  digest: sha256
}
OrderObservation = object{
  classification: "consistent" | "missing" | "conflicting" | "unknown" | "not_applicable",
  submitted_count: uint,
  accepted_count: uint,
  terminal_count: uint,
  active_count: uint,
  inflight_count: uint,
  rejected_count: uint,
  digest: sha256 | null
}
FillObservation = object{
  classification: "consistent" | "missing" | "conflicting" | "unknown" | "not_applicable",
  fill_count: uint,
  completely_filled_order_count: uint,
  partial_fill_order_count: uint,
  late_fill_count: uint,
  digest: sha256 | null
}
PositionObservation = object{
  classification: "consistent" | "missing" | "conflicting" | "unknown" | "not_applicable",
  observed_position_count: uint,
  open_position_count: uint,
  closed_position_count: uint,
  final_net_quantity: decimal | null,
  realized_pnl: decimal | null,
  digest: sha256 | null
}
BalanceObservation = object{
  classification: "consistent" | "missing" | "conflicting" | "unknown" | "not_applicable",
  snapshot_count: uint,
  explained_change: true | false | null,
  digest: sha256 | null
}
AccountModeObservation = object{
  classification: "consistent" | "missing" | "conflicting" | "unknown" | "not_applicable",
  operator_gate_confirmed: true | false | null,
  isolated_behavior_confirmed: true | false | null,
  one_way_netting_confirmed: true | false | null,
  leverage_one_confirmed: true | false | null,
  confirmation_run_id: sha256 | null,
  observed_initial_margin: decimal | null,
  expected_initial_margin: decimal | null,
  absolute_error: decimal | null,
  allowed_error: decimal | null,
  digest: sha256 | null
}
Cleanup = object{
  classification: "consistent" | "missing" | "conflicting" | "unknown" |
                  "cleanup_incomplete" | "not_applicable",
  active_order_count: uint,
  pending_order_count: uint,
  open_position_count: uint,
  unresolved_unknown_count: uint,
  final_net_quantity: decimal | null,
  digest: sha256 | null
}
Failure = object{
  code: stable_code,
  phase: "identity" | "connect" | "ready" | "data" | "account_mode" | "order_price" |
         "order_acceptance" | "fill" | "cancellation" | "reconciliation" |
         "cleanup" | "handler" | "aggregation",
  diagnostic_codes: [stable_code],
  diagnostic_digest: sha256
}
```

所有 `client_order`、`venue_order`、`trade`、`position` reference digest 使用同一精确投影：
`SHA256(UTF8("tracequant-stage4-reference-v1") || 0x00 || UTF8(<kind>) || 0x00 ||
UTF8(NFC(<public-reference>)))`，其中 `<kind>` 必须恰为上述四个 literal，public reference 非空。
`TimedFact.identity_digest` 使用
`SHA256(UTF8("tracequant-stage4-market-fact-v1") || 0x00 || UTF8(<containing-array-key>) || 0x00 ||
UTF8("BTCUSDT-PERP.BINANCE") || 0x00 || UTF8(ts_event) || 0x00 || UTF8(ts_init))`，array key 恰为
`quotes`、`trades` 或 `mark_prices`。`EventFact.event_reference_digest` 使用同一公式和 domain，
但 array key 替换为 `event_type`。`event_sequence_digest` 是按事件发生顺序保存的 `EventFact[]` 的
canonical SHA-256。account snapshot digest 是单个 `AccountSnapshot`
的 canonical SHA-256；FillFact 的 before/after digest 必须指向同一 run 的对应 snapshot。
`diagnostic_codes` 长度为 `1..16`，按 code point 升序且不得重复；`diagnostic_digest` 删除
`Failure.diagnostic_digest` 后对完整 Failure 对象计算，因此 code、phase 和全部 detail code 都被绑定。

`MarketDataObservation.digest = SHA256(canonical(MarketDataFacts))`，三个 count 必须分别等于三个 array
长度。Order/Fill/Position observation 的 `digest` 分别是 `OrderFact[]`、`FillFact[]`、
`PositionFact[]` 的 canonical SHA-256，相应总 count 必须等于 array 长度；其余分类 count 是该 array
按明示状态机械归约的结果。`BalanceObservation.digest` 是单个 `BalanceFact` 的 canonical SHA-256，
`snapshot_count` 成功时恰为 `2` 且 `explained_change` 必须等于 `BalanceFact.explained_change`。
`AccountModeObservation.digest` 删除该对象的 `digest` 后计算；market-close 与 Strategy 的成功记录
必须直接包含 §1.2 的三个确认及 margin 数值，`confirmation_run_id` 为 `null`；passive-cancel
成功记录的确认来自同 batch 已通过的 market-close record，三个 bool 和四个 margin decimal 必须逐
byte 复制，`confirmation_run_id` 指向该 run。
`Cleanup.digest` 是以下精确对象的 canonical SHA-256：
`object{active_order_count:uint,pending_order_count:uint,open_position_count:uint,
unresolved_unknown_count:uint,final_net_quantity:decimal|null}`。这些 category digest 均无删除投影；
顶层 EvidenceV1 的 `evidence_digest` 又覆盖 classification、counts 和 category digest。

`not_applicable` 对象的所有 count 必须为 `0`，所有 decimal/bool 以及
`AccountModeObservation.confirmation_run_id` 必须为 `null`，`digest` 必须为 `null`；其他
classification 的 `digest` 必须是 `sha256`。DataTester 的 order/fill/position/balance/account-mode
和 cleanup 恰为 `not_applicable`。其 market-data 必须有 `quote_count >= 1`、`trade_count >= 1`、
`mark_price_count = 0`。三个 order-enabled scenario 的 order/fill/position/balance/account-mode 和 cleanup 均不得
为 `not_applicable`；market-close 的成功计数恰为 2 submitted/accepted/terminal、2 fills，
passive-cancel 恰为 1 submitted/accepted/terminal、0 fills，Strategy 恰为
5 submitted/accepted/terminal、4 fills。成功记录还要求相应分类全部为 `consistent`，所有
active/inflight/open/pending/partial/late/unresolved count 为 `0`、cleanup
`final_net_quantity = "0"`；失败记录允许计数反映提前终止，但不得伪造成功基数。

完整 run record 只有以下形状：

```text
EvidenceV1 = object{
  schema: "tracequant-stage4-demo-evidence-v1",
  run_id: sha256,
  source_commit: git_oid,
  source_tree: SourceTree,
  dependency_lock_digest: sha256,
  runtime: RuntimeIdentity,
  environment: "BINANCE_DEMO_USD_M",
  config_digest: sha256,
  acceptance_batch_id: sha256,
  instrument: Instrument,
  scenario: "data_tester" | "exec_tester_market_close" |
            "exec_tester_passive_cancel" | "demo_strategy",
  started_at: timestamp,
  ended_at: timestamp,
  result: "PASS" | "FAIL",
  terminal_state: "COMPLETE" | "HALTED",
  observations: object{
    market_data: MarketDataObservation,
    order: OrderObservation,
    fill: FillObservation,
    position: PositionObservation,
    balance: BalanceObservation,
    account_mode: AccountModeObservation
  },
  cleanup: Cleanup,
  failure: Failure | null,
  evidence_digest: sha256
}
```

`ended_at` 不得早于 `started_at`。`PASS` 与 `COMPLETE` 必须成对且 `failure = null`；`FAIL` 与
`HALTED` 必须成对且 `failure` 非 null。`run_id`、`evidence_digest` 的删除投影严格保持本节开头定义。

#387 必须提交至少一个包含非 ASCII/NFC、nullable、decimal string 和 nested key ordering 的 evidence
golden vector，固定 canonical UTF-8 bytes、`run_id` 与 `evidence_digest`；#391 必须提交 acceptance
golden vector，证明 `generated_at` 改变不改变 `acceptance_digest`，任一其他输入改变会改变 digest。
`run_id` 和 `evidence_digest` 严格按上述两个投影依序导出，避免循环身份。
DataTester 的 order/fill/position/balance/account-mode observations 必须明确为 `not_applicable`；这是合同明确
排除该事实的 marker，不是 §8 reconciliation 分类，也不能伪造空成功。两个 ExecTester attempt
和 Strategy 必须包含全部五类观察及 cleanup。任何 evidence 或最终 tracked record 都不得保存
credential、credential 派生值或 account identity。

### 9.2 `tracequant-stage4-demo-acceptance-v1`

最终 tracked record 只有以下封闭形状；所有 `required_runs` 值在可发布 record 中都是 literal
`"PASS"`，两个 ExecTester 项另外共同归约为同一个 `exec_tester: "PASS"`：

```text
AcceptanceV1 = object{
  schema: "tracequant-stage4-demo-acceptance-v1",
  acceptance_batch_id: sha256,
  source_commit: git_oid,
  source_tree_digest: sha256,
  dependency_lock_digest: sha256,
  runtime_identity_digest: sha256,
  environment: "BINANCE_DEMO_USD_M",
  config_digest: sha256,
  instrument: object{
    instrument_id: "BTCUSDT-PERP.BINANCE",
    constraints_digest: sha256
  },
  evidence_digests: object{
    data_tester: sha256,
    exec_tester_market_close: sha256,
    exec_tester_passive_cancel: sha256,
    demo_strategy: sha256
  },
  required_runs: object{
    data_tester: "PASS",
    exec_tester_market_close: "PASS",
    exec_tester_passive_cancel: "PASS",
    demo_strategy: "PASS",
    exec_tester: "PASS"
  },
  reconciliation: object{
    order: "consistent",
    fill: "consistent",
    position: "consistent",
    balance: "consistent",
    account_mode: "consistent"
  },
  cleanup: object{
    active_order_count: 0,
    pending_order_count: 0,
    open_position_count: 0,
    unresolved_unknown_count: 0,
    final_net_quantity: "0"
  },
  product_status: "LIVE_NOT_APPROVED",
  generated_at: timestamp,
  acceptance_digest: sha256
}
```

四个 `evidence_digests` 必须两两不同，并分别等于对应 EvidenceV1 的 `evidence_digest`；四份 source
record 的 shared identity 字段必须逐 byte 相同。tracked record 不得嵌入原始事件、日志、batch
nonce、credential、credential 派生值或 account identity。`acceptance_digest` 只删除顶层
`generated_at` 与 `acceptance_digest` 后计算，不删除或
替换任何嵌套成员；相同输入必须得到相同 digest。

### 9.3 分区、batch、credential 边界、脱敏和发布

- batch runner 在任何联网运行前创建一个全新、初始为空、repo 外且仅当前用户可访问的 batch root；
  以 OS CSPRNG 在内存中生成恰好 32 bytes 的 nonce，并计算
  `acceptance_batch_id = SHA256(UTF8("tracequant-stage4-acceptance-batch-v1") || 0x00 || nonce)`。
  nonce 随即清零且不得写盘；只有 64 位小写 hex batch ID 可传给四个顺序子进程并写入 evidence；
- runner 在启动任何 client 前一次性读取两个 Demo credential 环境变量，拒绝缺失或空值。DataTester
  子进程的环境显式删除这两个变量；三个 order-enabled 子进程都从同一个不可变内存 snapshot 注入
  完全相同的两个值。子命令、entry config 和环境 override 均不得提供第二个 credential 来源；
- credential snapshot 只用于构造子进程环境，由 rc4 adapter 正常读取。runner、entry 和 aggregator
  不计算 credential hash/HMAC、不打印值、不把值或任何派生 identity 放入 config/evidence。任一
  order-enabled 子进程异常退出即使整个 batch 失败，不能用另一个环境重启或拼接成功证据；
- 聚合器要求四份 record 的 `acceptance_batch_id` 相同；任一缺失或不相等都拒绝发布。batch 结束、
  拒绝或失败后，runner 在 `finally` 中清除内存 snapshot。进程崩溃后的分区永久失效，只能创建新
  batch，不能复用其 record 补跑；
- 每次联网运行使用新的、显式命名且初始为空的 repo 外 evidence partition；四个逻辑 partition ID
  固定为 `data_tester`、`exec_tester_market_close`、`exec_tester_passive_cancel`、`demo_strategy`，
  并与 record 的 `scenario` 一致。运行只记录逻辑 partition ID，任何 tracked record 不得出现绝对
  本机路径；
- frozen config digest 的 payload 只允许使用下述 §9.3.1 封闭对象；只记录 credential **变量名**，
  不含值；batch ID、nonce、credential 和 account identity 都不属于 config payload；
- secret、完整账户标识、签名请求、认证 header/cookie、可重放认证材料和未脱敏 raw payload
  不得进入配置文件、Issue、日志或 tracked record；
- 原始成功和失败证据都留在外部分区；仓库只发布通过 §9.2 的脱敏 acceptance record；
- required evidence 缺失、identity/config drift、digest 不匹配、非 `consistent` 分类或清场不完整
  时，不得创建或覆盖成功记录。写入必须先完整验证临时 payload，再做单文件原子替换。

#### 9.3.1 Frozen config payload

四个进程使用完全相同的下列对象；入口/scenario、partition、batch、credential 和任何动态
observation 都不得加入。array 的顺序也是合同的一部分。`config_digest` 是该完整对象按 §9.1
canonical bytes 计算的 SHA-256，不存在自引用字段或其他删除投影：

```text
FrozenConfigV1 = object{
  schema: "tracequant-stage4-demo-config-v1",
  runtime: object{
    identity_digest: sha256,
    python: "3.13",
    platform: "linux-x86_64",
    wheel_tag: "cp313-cp313-manylinux_2_34_x86_64",
    bytecode_write_disabled: true
  },
  environment: "BINANCE_DEMO_USD_M",
  product: object{
    venue: "BINANCE",
    product_type: "USD_M",
    endpoint_policy: "DEMO_DEFAULTS_ONLY",
    base_url_http: null,
    base_url_ws: null,
    base_url_ws_trading: null
  },
  instrument: object{
    load_all: false,
    load_ids: ["BTCUSDT-PERP.BINANCE"; 1],
    constraints_digest: sha256
  },
  account_mode: object{
    oms_type: "NETTING",
    position_mode: "ONE_WAY",
    margin_type: "ISOLATED",
    leverage: 1,
    operator_gate_token_name: "ONE_WAY_ISOLATED_1X_FLAT_CONFIRMED",
    credential_variable_names: ["BINANCE_DEMO_API_KEY",
                                "BINANCE_DEMO_API_SECRET"]
  },
  limits: object{
    active_or_pending_order_cap: 1,
    opening_quantity_policy: "MIN_VALID_FROM_ORDER_PRICE_INPUT",
    cleanup_quantity_policy: "EXACT_CONFIRMED_ABS_NET_POSITION",
    cleanup_order_type: "MARKET_REDUCE_ONLY",
    min_notional_for_cleanup: "NOT_APPLICABLE"
  },
  deadlines_seconds: object{
    network_connect: 30,
    ready: 60,
    data_tester_observation: 60,
    order_price_readiness: 10,
    order_acceptance: 10,
    market_or_reduce_only_fill: 30,
    cancellation: 10,
    reconciliation: 30,
    cleanup: 60
  }
}
```

`runtime.identity_digest` 必须等于本 run `RuntimeIdentity.identity_digest`，
`instrument.constraints_digest` 必须等于本 run `Instrument.constraints_digest`。四份 EvidenceV1 的
`config_digest` 必须逐 byte 相同；任何 member、literal、array 顺序或 digest 不同都属于 config drift。

### 9.4 证据要求编号

| ID | 冻结要求 |
| --- | --- |
| `ST4-EVID-001` | 准入成功输出不含 secret 的 frozen config payload 和 digest，失败不输出可冒充成功的 digest。 |
| `ST4-EVID-002` | 所有 run evidence 使用 `tracequant-stage4-demo-evidence-v1`，并绑定 §1.4/§9.1 对实际 source、lock、wheel 和 installed distribution bytes 验证得到的完整 identity；预期常量不能自证。 |
| `ST4-EVID-003` | order/fill/position/balance/account-mode 只用 §8 为当前入口冻结的 rc4 public observation profile 分类；ExecTester 使用可检索 cache object graph，Strategy 使用 callback/cache/account；非 `consistent` 必定阻止成功。 |
| `ST4-EVID-004` | 原始证据只写四个全新 repo 外分区；batch nonce 与 credential snapshot 按 §9.3 仅在 runner 内存中生成/取得并清除，tracked 内容必须脱敏、只含逻辑引用和 digest。 |
| `ST4-EVID-005` | 跨运行 identity、config 或 digest 不一致时禁止拼接证据，失败证据必须保留在外部分区。 |
| `ST4-EVID-006` | DataTester 证据包含 quote/trade counts、instrument constraints 和 timestamp 检查结果，不含执行事实。 |
| `ST4-EVID-007` | 两个 ExecTester 主 attempt 各自产生独立 record/partition/digest，用 §8.1 的可检索 cache object graph 分别绑定 market fill/reduce-only flat 与 passive accepted/canceled；failure cleanup-only component 也必须进入所属失败 record，不能隐藏子运行。 |
| `ST4-EVID-008` | Strategy 证据绑定固定状态序列、每个 order/fill、fault protection 和最终 reconciliation/cleanup。 |
| `ST4-EVID-009` | 最终 record 使用 `tracequant-stage4-demo-acceptance-v1`，完整聚合四个新分区/四个 source digest，验证同一 `acceptance_batch_id`，并声明 `LIVE_NOT_APPROVED`；tracked record 不保存 credential、派生 identity 或配置 `AccountId`。 |
| `ST4-EVID-010` | acceptance digest 对同一输入稳定；任何失败、漂移或未清场不得创建或覆盖成功记录。 |

## 10. #386–#391 primary / applicable 映射

下表逐一列出每个稳定编号的唯一 primary owner 和全部额外 applicable 叶项。primary 负责实现并
验证该编号；applicable 叶项必须在自己的有界交付物中服从该编号，但不得复制实现所有权。
`—` 表示除 primary 外没有额外 applicable 叶项。叶项只能承接表中将其列为 primary 或 applicable
的编号；依赖方可以读取前置产物，但不能据此增加未映射的交付义务。

| 稳定编号 | 唯一 primary owner | 额外 applicable 叶项 |
| --- | --- | --- |
| `ST4-REQ-001` | [#386](https://github.com/PhoenixSss/tracequant/issues/386) | #387、#388、#389、#390、#391 |
| `ST4-REQ-002` | [#386](https://github.com/PhoenixSss/tracequant/issues/386) | #387、#388、#389、#390、#391 |
| `ST4-REQ-003` | [#386](https://github.com/PhoenixSss/tracequant/issues/386) | #387、#388、#389、#390、#391 |
| `ST4-REQ-004` | [#386](https://github.com/PhoenixSss/tracequant/issues/386) | #389、#390、#391 |
| `ST4-REQ-005` | [#386](https://github.com/PhoenixSss/tracequant/issues/386) | #389、#390、#391 |
| `ST4-REQ-006` | [#386](https://github.com/PhoenixSss/tracequant/issues/386) | #388、#389、#390、#391 |
| `ST4-REQ-007` | [#387](https://github.com/PhoenixSss/tracequant/issues/387) | #388、#389、#390、#391 |
| `ST4-REQ-008` | [#388](https://github.com/PhoenixSss/tracequant/issues/388) | #391 |
| `ST4-REQ-009` | [#389](https://github.com/PhoenixSss/tracequant/issues/389) | #391 |
| `ST4-REQ-010` | [#390](https://github.com/PhoenixSss/tracequant/issues/390) | #391 |
| `ST4-REQ-011` | [#390](https://github.com/PhoenixSss/tracequant/issues/390) | #391 |
| `ST4-REQ-012` | [#391](https://github.com/PhoenixSss/tracequant/issues/391) | — |
| `ST4-REQ-013` | [#391](https://github.com/PhoenixSss/tracequant/issues/391) | #388、#389、#390 |
| `ST4-REQ-014` | [#391](https://github.com/PhoenixSss/tracequant/issues/391) | #386、#387、#388、#389、#390 |
| `ST4-SAFE-001` | [#386](https://github.com/PhoenixSss/tracequant/issues/386) | #388、#389、#390、#391 |
| `ST4-SAFE-002` | [#387](https://github.com/PhoenixSss/tracequant/issues/387) | #388、#389、#390、#391 |
| `ST4-SAFE-003` | [#388](https://github.com/PhoenixSss/tracequant/issues/388) | #391 |
| `ST4-SAFE-004` | [#389](https://github.com/PhoenixSss/tracequant/issues/389) | #390、#391 |
| `ST4-SAFE-005` | [#389](https://github.com/PhoenixSss/tracequant/issues/389) | #391 |
| `ST4-SAFE-006` | [#390](https://github.com/PhoenixSss/tracequant/issues/390) | #391 |
| `ST4-SAFE-007` | [#390](https://github.com/PhoenixSss/tracequant/issues/390) | #389、#391 |
| `ST4-SAFE-008` | [#390](https://github.com/PhoenixSss/tracequant/issues/390) | #391 |
| `ST4-SAFE-009` | [#390](https://github.com/PhoenixSss/tracequant/issues/390) | #388、#389、#391 |
| `ST4-EVID-001` | [#386](https://github.com/PhoenixSss/tracequant/issues/386) | #387、#388、#389、#390、#391 |
| `ST4-EVID-002` | [#387](https://github.com/PhoenixSss/tracequant/issues/387) | #388、#389、#390、#391 |
| `ST4-EVID-003` | [#387](https://github.com/PhoenixSss/tracequant/issues/387) | #388、#389、#390、#391 |
| `ST4-EVID-004` | [#387](https://github.com/PhoenixSss/tracequant/issues/387) | #388、#389、#390、#391 |
| `ST4-EVID-005` | [#387](https://github.com/PhoenixSss/tracequant/issues/387) | #388、#389、#390、#391 |
| `ST4-EVID-006` | [#388](https://github.com/PhoenixSss/tracequant/issues/388) | #391 |
| `ST4-EVID-007` | [#389](https://github.com/PhoenixSss/tracequant/issues/389) | #391 |
| `ST4-EVID-008` | [#390](https://github.com/PhoenixSss/tracequant/issues/390) | #391 |
| `ST4-EVID-009` | [#391](https://github.com/PhoenixSss/tracequant/issues/391) | — |
| `ST4-EVID-010` | [#391](https://github.com/PhoenixSss/tracequant/issues/391) | — |

| 叶子 Issue | 有界交付物 |
| --- | --- |
| [#386](https://github.com/PhoenixSss/tracequant/issues/386) | typed config、凭据/账户准入、constraints/quantity/deadline 合同、frozen config digest；实现 §1.2 operator/config gate 和行为确认计划，不修改上游包 |
| [#387](https://github.com/PhoenixSss/tracequant/issues/387) | evidence schema、Nautilus-owned 状态分类、外部分区与脱敏/digest 行为 |
| [#388](https://github.com/PhoenixSss/tracequant/issues/388) | 官方 DataTester 的固定 plan、薄入口和 data evidence |
| [#389](https://github.com/PhoenixSss/tracequant/issues/389) | 固定 rc4 的短时 planning、两个官方 ExecTester 主 attempt、只读 reconciliation、必要时 cleanup-only tester，以及完整 execution evidence |
| [#390](https://github.com/PhoenixSss/tracequant/issues/390) | 固定 Demo Strategy、§1.2 行为确认、完整 handler 清单与逐 handler fault protection、reconciliation/cleanup evidence |
| [#391](https://github.com/PhoenixSss/tracequant/issues/391) | fresh evidence matrix、最终聚合器和 tracked acceptance record |

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
- 禁止在仓库中 vendor、fork、monkeypatch 或复制官方 `nautilus_trader` namespace，也禁止通过
  解析 warning/log、私有 Rust/Python 对象或 tester 外下单绕过 §1.2/§4.2 admission 与行为证据；
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

阶段 4 只有在本文档完成版本修订、#386–#391 各自通过、#391 在固定 rc4 上发布有效 acceptance
record 且 Feature completion audit 通过后才完成。operator declaration、配置或离线 plan 不能替代
order-enabled 行为证据。最终成功状态仍是：

```text
NAUTILUS_PRIMARY
BINANCE_DEMO_BASIC_LOOP_ACCEPTED
LIVE_NOT_APPROVED
```

## 13. 规范来源

- [TraceQuant 分阶段推进计划](<../research/foundation-selection/TraceQuant 分阶段推进计划.md>)，阶段 4/5；
- [ADR-0001：NautilusTrader primary runtime](../architecture/adr-0001-nautilustrader-primary-runtime.md)；
- [NautilusTrader rc4 Binance integration](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/docs/integrations/binance.md)；
- [rc4 Binance Futures leverage/margin config application（`apply_futures_config`，L1200–L1246）](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/adapters/binance/src/futures/execution.rs#L1200-L1246)；
- [rc4 Binance Futures hedge-mode query and OMS comparison during connect（L1720–L1733）](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/adapters/binance/src/futures/execution.rs#L1720-L1733)；
- [Binance USD-M current position mode](https://developers.binance.com/docs/derivatives/usds-margined-futures/account/rest-api/Get-Current-Position-Mode)；
- [Binance USD-M change initial leverage](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Change-Initial-Leverage)；
- [Binance USD-M position information V3](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Position-Information-V3)；
- [Binance USD-M exchange information / order filters](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)；
- [Binance `-4164 MIN_NOTIONAL`：reduce-only 豁免](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/error-code#-4164-min_notional)；
- [rc4 Demo config 示例中的调用方 `AccountId`（L1307–L1313）](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/docs/integrations/binance.md#L1307-L1313)；
- [rc4 AccountState 使用配置 account ID 并公开 total margin info（L340–L437）](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/adapters/binance/src/futures/execution.rs#L340-L437)；
- [rc4 ExecTester fixed quantity config](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/testkit/src/testers/exec/config.rs#L47-L98)；
- [rc4 ExecTester quote/order behavior](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/testkit/src/testers/exec/strategy.rs#L264-L278)；
- [rc4 ExecTester exact close-on-stop path](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/testkit/src/testers/exec/strategy.rs#L1830-L1902)；
- [nautilus_trader #5039](https://github.com/nautechsystems/nautilus_trader/issues/5039)。
