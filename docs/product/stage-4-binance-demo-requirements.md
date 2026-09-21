# TraceQuant 阶段 4：Binance Demo 实施与验收基线

| 字段 | 值 |
| --- | --- |
| 文档状态 | 已批准数据/离线实施基线；order-enabled capability blocked（见 §1.2、§4.2） |
| 文档版本 | `v1.0` |
| 日期 | `2026-09-21` |
| Feature | [#384](https://github.com/PhoenixSss/tracequant/issues/384) |
| 固定运行时 | NautilusTrader `2.0.0rc4` / `a0400251110653b6d8ae6a9b5b89c4543fa85a2d` |
| 唯一环境 | Binance USD-M Futures Demo |
| 产品状态 | `DEMO_ONLY`、`LIVE_NOT_APPROVED` |

本文档是 Feature #384 的唯一阶段 4 实施与验收基线。当前固定 rc4 对 order-enabled
验收存在 §1.2 与 §4.2 明确列出的公共能力缺口；缺口关闭并由 maintainer 批准新的精确
runtime identity 之前，只允许完成不依赖这些能力的数据、schema 和离线 failure-path 工作，
不得运行或发布 order-enabled 成功证据。#386–#391 只能实现
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
| Runtime | NautilusTrader `2.0.0rc4`，commit `a0400251110653b6d8ae6a9b5b89c4543fa85a2d`；order-enabled capability 仍为 blocked | rc5、未锁定版本、运行时身份漂移、仓库内 patched/forked `nautilus_trader` |
| Execution target | CPython 3.13、`linux-x86_64`、官方 `cp313-cp313-manylinux_2_34_x86_64` wheel | 其他 Python/platform tag、sdist/source build、非官方或重打包 wheel |
| Venue / product | `BINANCE` / `BinanceProductType.USD_M` | Spot、COIN-M、Margin、其他交易所 |
| Environment | `BinanceEnvironment.DEMO` | 默认 Live、`LIVE`、legacy `TESTNET`、自定义 endpoint |
| Instrument | `BTCUSDT-PERP.BINANCE`；provider 使用 `load_all=False` 和唯一 `load_ids` | 第二个 instrument、全量加载、动态 allowlist |
| Account | 一次运行只使用一个 Demo account；证据只保存不可逆 account reference digest | 第二个 account、完整 account ID、Live account |
| Position mode | one-way；Nautilus 使用 `OmsType.NETTING` | hedge / dual-side |
| Margin / leverage | `BTCUSDT` 为 `BinanceMarginType.ISOLATED`、`1x` | cross、动态 leverage、其他 symbol 设置 |
| Active order cap | 全局最多 `1` 个活动或 acknowledgement 未决订单 | batch、并行订单、unknown 时新订单 |
| Quantity | 开仓与 passive order 使用运行时 constraints 算出的最小有效量；任何 reduce-only close 精确等于最新已确认的 `abs(net position)` | 硬编码数量、portfolio sizing、对 close quantity 向上/向下取整、绕过适用 filter |
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

上述三个字段在 rc4 中不具有相同语义：`oms_type=NETTING` 只声明 Nautilus OMS 预期，
不会把 venue 从 hedge mode 切换为 one-way；`futures_margin_types` 的非“已经是目标模式”错误会
使 execution client 连接失败；`futures_leverages` 则是 best-effort，venue rejection 只产生
warning 并继续连接。execution config、`LiveNode.cache`、公开 account/position objects 和公开
report/callback 均不返回可供薄封装判定的 one-way/1x admission result。成功连接因此只能证明
isolated 配置没有以 fatal error 失败，不能证明 one-way 或 1x。

唯一 pre-run operator gate 仍是：操作者在 Binance Demo UI 中确认三项后，为**本次**
order-enabled attempt 显式提供固定令牌 `ONE_WAY_ISOLATED_1X_CONFIRMED`。令牌不是持久授权，
不得默认、缓存或跨 attempt 复用；缺少或不精确匹配时必须在创建 network client 前失败。
它只允许进入连接/自动确认阶段，不是 venue 事实、不能使订单路径 ready、不能单独支持
`COMPLETE` 或成功 evidence。

当前冻结 rc4 缺少订单前自动确认 one-way 和 1x 的公共能力，因此所有 order-enabled attempt
必须在首笔订单前以稳定 failure code `account_mode_capability_unavailable` 进入 `HALTED`。
不得用 warning/日志解析、private/Rust execution client、raw Binance REST/WebSocket、测试订单
或连接成功替代确认。#386 只能实现该 fail-closed gate 和离线证据，不能声称修复上游能力。

解除该 gate 必须先通过独立范围变更获得 maintainer 批准、修订本文档版本并锁定新的官方
Nautilus runtime identity。新 runtime 的公共 execution-client connect contract 必须同时保证：

1. venue hedge mode 与 `OmsType.NETTING` 不符时，在 `connected`/order-ready 前返回 fatal error；
2. `BTCUSDT` leverage 设置被拒绝，或成功响应不是精确 `1x` 时，在 `connected`/order-ready 前返回
   fatal error；
3. exact frozen config 加上公开的 connected/ready outcome 足以形成 typed、可测试的
   `account_mode_admitted=true`，不读取日志或私有对象。

仓库不得 vendor、monkeypatch 或复制 `nautilus_trader` 来满足该能力，也不得为读取或设置这些值
创建第二个 Binance client。新的官方 runtime 未被锁定前，#389、#390 的 credentialed
order-enabled 场景与 #391 成功发布保持 blocked。

### 1.3 最小有效数量

每个 order-enabled 场景都必须从本次 attempt 加载的 Nautilus instrument 得到数量，不接受配置
中的静态 quantity。开仓/passive order 先按订单类型选择适用的 size increment、minimum quantity、
precision 和 minimum notional constraints，再冻结本次提交的 `notional_price`：limit order 必须先从
§3.1 合格 quote 推导并通过 tick/price constraints 校验**精确提交价格**（passive buy 即校验后的
`best bid - 1 price increment`），并以该提交价格计算 minimum notional；market order 没有提交价格，
必须使用 §3.1 中同 instrument 的合格 mark-price sample。reduce-only close 不计算最小量，使用下文
冻结的精确持仓原值算法。不得用 buy ask、sell bid 或预期成交价替代 order-type-specific 输入。

对于开仓与 passive order，最终 quantity 是满足全部适用 constraints 的最小 step 倍数，并由
Nautilus instrument 的 quantity 构造/校验路径生成。缺少 order-type-specific constraint、limit
提交价格、公开 mark-price 输入或无法得到唯一最小值时停止；不得猜测、保守放大或扩大为
portfolio sizing。quantity 计算、订单构造与紧接其后的提交前复核必须绑定同一份已识别的价格输入；
limit order 绑定同一 quote snapshot 和由它推导的提交价格，market order 绑定同一 mark-price
sample。

任何正常或失败路径的 reduce-only market close 使用独立的精确清平算法，不能复用上述最小开仓量：

1. 只有证明前一订单 terminal、active/inflight count 为零，并由当前入口的 §8 observation profile
   得到唯一、非零的 one-way net position 后，才冻结一次 `close_quantity = abs(position)`；失败路径
   必须逐项执行 §5 的同一 proof 顺序；
2. close side 必须与该 position 相反，订单必须是 market + `reduce_only=true`，提交 quantity 必须与
   `close_quantity` 数值完全相等；禁止按 step/minimum/notional 向上放大、向下截断或提交 dust
   follow-up；
3. 精确 `close_quantity` 必须可由 Nautilus `Quantity` 无损表示，并通过 reduce-only market order
   的**全部且仅有**以下数量/名义约束：instrument size precision、`MARKET_LOT_SIZE` 的
   `minQty`/`maxQty`/`stepSize`，以及用同一份 §3.1 合格 mark-price sample 计算的 `MIN_NOTIONAL`；
   不得改用普通开仓公式、`LOT_SIZE`、静态值或假定 reduce-only exemption；
4. 固定 runtime 的公共 instrument surface 若不能分别、完整地提供上述 market filters，或精确
   `close_quantity` 违反任一 filter，则不得创建/提交 cleanup order；运行保持 `HALTED`，记录
   `cleanup_incomplete` 及失败的 filter，但不得记录 secret、完整账户标识或 raw venue payload；
5. 构造前再次读取 position；若它与冻结值不完全相同，原 quantity 作废，必须重新完成
   terminal → zero-active → position proof。只有同一次冻结值完成构造、提交并最终证明 position 为零，
   才能记录 `cleanup_complete`；任何 partial/late fill 或 cancel/fill race 都遵循同一规则。

该合同也适用于官方 ExecTester；固定 rc4 的 `ExecTesterConfig.order_qty` 和
`open_position_on_start_qty` 在运行前已定值，不能满足本段合同。不得以先前 DataTester run、
另一个 process/node 的 quote、保守放大 quantity 或静态配置规避。§4.2 冻结相应 capability gate
和能力到位后的唯一双 attempt 计划。

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
   上述映射。运行时在 import 前使用 distribution metadata 重算：名称/版本必须精确匹配，
   `direct_url.json` 必须不存在，module origin 必须位于该 distribution root；逐项验证 `RECORD` 中
   每个文件的 size/hash，拒绝缺失、hash mismatch，以及 `nautilus_trader` package roots 或对应
   `.dist-info` 中未列入 `RECORD` 的 `.py`/`.pyi`/`.pyc`/native executable。`installed_tree_digest` 是按 path 排序的
   `{path, size, sha256}`（包括对 `RECORD` 本身现场求得的值）数组按 §9.1 canonical bytes 计算的
   SHA-256，并且必须等于 provisioning attestation 的值。
5. **Runtime digest 投影**：`runtime.identity_digest` 只对以下完整对象按 §9.1 canonical-byte 规则
   求 SHA-256：`distribution`、`version`、`upstream_commit`、`wheel_filename`、`wheel_sha256`、
   `wheel_tag`、`installed_tree_digest`；计算时只排除 `identity_digest` 本身。字段缺失、expected/actual
   不一致、attestation 缺失或 digest 不一致都失败，不能用文档常量补值。

## 2. 稳定行为要求

| ID | 冻结要求 |
| --- | --- |
| `ST4-REQ-001` | 每个入口在 import/config validation 阶段执行 §1.4：验证 clean source tree、实际 lock bytes、批准 wheel→commit 映射、安装后 distribution bytes 和 runtime digest；身份不能由期望常量自证，任一不符时不得创建客户端。 |
| `ST4-REQ-002` | 唯一环境是 Binance USD-M Futures Demo，endpoint 不可覆盖，凭据只来自两个 Demo 专用变量。 |
| `ST4-REQ-003` | 唯一 instrument、单 account、one-way、isolated、1x、最多一个活动/未决订单是不可配置边界；缺少 §1.2 的公开自动确认能力时 order-enabled 路径不可用。 |
| `ST4-REQ-004` | order-enabled 入口执行 §1.2 的一次性 operator gate 和自动 admission；固定 rc4 必须以 `account_mode_capability_unavailable` 在首单前 `HALTED`，不能把 config、连接成功或日志当确认。 |
| `ST4-REQ-005` | 开仓/passive quantity 按 §1.3 从本 attempt 的 instrument 与 order-type-specific `notional_price` 计算为最小有效量；任何 reduce-only close 则精确等于最新已确认 `abs(position)`，不得取整或放大，且必须通过冻结的 market filters，否则不提交并保持 `cleanup_incomplete`；rc4 ExecTester 必须按 §4.2 fail closed。 |
| `ST4-REQ-006` | 所有网络和订单阶段使用 §3 的唯一 deadline；v1.0 不提供 timeout override。 |
| `ST4-REQ-007` | 核对只读取 §8 的 entry-specific rc4 公开观察面：ExecTester 使用 `LiveNode.cache` 可检索 object graph，Strategy 使用 callbacks/cache/account snapshots；不得要求取得 rc4 公共 Python API 不返回的 tester 或 report objects。TraceQuant 不拥有第二套 adapter、order、position、ledger、accounting 或 reconciler。 |
| `ST4-REQ-008` | DataTester 只执行 §4.1 的数据场景，不创建 execution client。 |
| `ST4-REQ-009` | ExecTester 只执行 §4.2 的两个互斥 attempt；当前 rc4 缺少 order-type-specific deferred quantity 与 staged terminal-reconciliation cleanup capability 时不得联网下单，特殊 risk bypass 不得进入普通 Strategy。 |
| `ST4-REQ-010` | Demo Strategy 只执行 §4.3 的固定顺序，不读取阶段 3 模型、不产生 alpha、不做 portfolio sizing。 |
| `ST4-REQ-011` | Strategy 只使用 §5 的固定前进状态；终态只有 `COMPLETE` 或 `HALTED`，不得抽象为可配置工作流引擎。 |
| `ST4-REQ-012` | 最终验收按 §4.4 从四个全新外部分区依次消费 DataTester、两个独立 ExecTester attempt、Strategy 证据，并在同一 batch/identity 下聚合。 |
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

ExecTester（仅在 §4.2 capability gate 解除后）、Strategy 以及 reduce-only cleanup 使用同一规则。
合格 quote 必须属于
`BTCUSDT-PERP.BINANCE`，具有非空 bid/ask，`ts_event` 不得比本地 UTC wall clock 快超过 1 秒，
检查时的 `now - ts_event` 不得超过 5 秒，并且 `ts_event` 不得早于本次 run 对该 quote stream
已经接受的最后一个非重复 timestamp。年龄比较使用检查瞬间的 wall clock；10 秒等待 deadline
使用 monotonic clock，两者不能互相替代。

market order 还必须通过正常 Nautilus 公共 data/cache surface 取得同一 instrument 的正数
mark-price sample。它采用与 quote 相同的 identity、future 1 秒、age 5 秒和本 run 内 timestamp
单调规则，但维护独立的 mark-price stream last-seen timestamp；禁止 raw Binance client、日志或
private object。固定 runtime 若不公开该输入，market 与 reduce-only market order 必须 fail closed。

order-enabled ready 必须先取得一份合格 quote。此后每一次正常场景订单或 reduce-only cleanup
提交前都重新启动一次 10 秒 order price readiness deadline。limit order 在 deadline 内取得新合格
quote，先推导并校验精确提交价格，再用该价格计算 quantity；market order 在 deadline 内取得新合格
mark-price sample，并用它计算 quantity。passive price 显示仍来自同一合格 quote 的 best bid。
提交前复核时，所绑定 sample 年龄仍须不超过 5 秒，identity/price constraints 仍匹配且期间未观察到
对应 stream 更新但倒退的 timestamp；不满足就丢弃该输入并在原 10 秒 deadline 内等待下一份，不能
重新启动 deadline。

deadline 到期、future/stale/out-of-order、缺少订单类型所需 quote/mark price 或 instrument identity
不匹配时，本次运行进入 `HALTED`。对正常场景禁止提交；对 cleanup 也不得凭旧价格猜测提交，必须保留已证明的
exposure 和 `cleanup_incomplete` 诊断交给操作者。不得用最近一次 DataTester run、ready 时缓存但在
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
operator token。TraceQuant 薄封装只负责冻结配置、启动/停止两个 attempt、读取 §8.1 cache graph
和输出证据；所有订单动作必须由官方 ExecTester 执行。

固定 rc4 在构造 `ExecTesterConfig` 时就固定 `order_qty` 和 `open_position_on_start_qty`；收到首个
quote 时只提交该预存 quantity，`instrument.make_qty` 仅量化它，不会按 `min_qty`、step、
`min_notional` 和 §1.3 的 order-type-specific price 求最小值。启用 limit leg 时，同一 `on_quote`
还会进入 order maintenance。其 `on_stop` 还会同步发出 cancel-active-orders 和 close-positions，
不会在二者之间等待原订单终态与零 active/inflight proof。该行为既不能满足 §1.3，也不能满足
§5 的 cleanup 顺序。因此固定 rc4 的 ExecTester order-enabled attempt 必须在 client/order 创建前
以稳定 failure code `exec_tester_order_safety_capability_unavailable` 进入 `HALTED`；不得把 DataTester
或另一 node 的快照、运行前 quantity、日志/private hook、自定义 order owner 或 tester 旁路下单
当作替代。

解除该 gate 必须与 §1.2 一样先批准并锁定新的官方 runtime。它必须在官方 ExecTester 内同时提供：

1. typed、不可注入 callback 的 `MIN_VALID_FROM_ORDER_PRICE_INPUT` quantity mode：tester 自身读取
   已加载 instrument；limit 先冻结精确提交价格，market 读取公开 mark-price input，再按 §1.3 得到
   quantity 并紧接着构造和提交；计算/校验失败必须在任何订单创建前 fatal；
2. typed `STAGED_TERMINAL_RECONCILE_THEN_CLEANUP` stop/failure mode：先请求 cancel/query，再等待
   原订单进入 terminal，证明 active/inflight count 为零，重新读取确定的实际 position，最后仅在
   position 非零时严格按 §1.3 以 `abs(position)` 原值提交恰好一笔 reduce-only cleanup；tester 的
   public instrument surface 必须提供该段冻结的 `MARKET_LOT_SIZE` 与 `MIN_NOTIONAL` 输入，不能提供、
   精确 exposure 不可无损构造或不通过 filter 时保持 `HALTED`/`cleanup_incomplete` 且不提交。
   任何 proof 超过 §3 deadline 都必须保持 `HALTED` 且不提交 cleanup。

这些能力不得允许任意 sizing function、plugin/hook 或第二个 order owner。只增加 deferred quantity
而没有官方 staged-cleanup capability 不足以解除 gate。

能力到位后的唯一计划由两个新的、互斥的 order-enabled attempt 组成；每个 attempt 使用独立
LiveNode、ExecTester、repo 外 evidence partition 和唯一 strategy/order tag，开始前都证明账户 flat、
无活动/未决订单，并各自重新执行 §1.2 admission：

1. **market-close attempt**：关闭全部 limit/stop legs，使用
   `open_position_on_first_price_input=true` 与 `MIN_VALID_FROM_ORDER_PRICE_INPUT` 提交唯一 market buy；
   完整 fill 且该订单 terminal 后停止 tester，由 `STAGED_TERMINAL_RECONCILE_THEN_CLEANUP` 先证明
   active/inflight count 为零，再按最新确定 long position 和合格 mark price 提交唯一 reduce-only
   market sell，等待完整成交并证明 flat；
2. **passive-cancel attempt**：不启用 open-position/stop/sell legs，只启用一个 post-only limit buy；
   tester 先从首个合格 quote 得到并校验 `best bid - 1 price increment`，再以该精确提交价格计算最小
   quantity；提交后不得追价、modify 或补单。确定 accepted 后停止 tester，由 staged mode 请求
   cancel 并等待该订单 terminal、active/inflight count 为零；随后重新读取 position，若 flat 则不
   提交 cleanup，若 cancel/fill race 形成确定非零 position 则该场景失败并只提交一笔 reduce-only
   cleanup，无法在 deadline 内完成任何 proof 时不提交 cleanup。

两个 attempt 的证据共同构成一个 `exec_tester` required scenario；任一个失败都会使该 scenario
`FAIL/HALTED`，后一个不得用来弥补前一个。两者绝不同时运行，所以 active/pending order cap 仍为
1。每个 attempt 都是独立运行，并分别生成一份 §9.1 canonical evidence record；不得把两次运行
包进一个无 child identity/digest 的 ExecTester envelope。rc4 的
`LiveNode.add_builtin_strategy("ExecTester", config)` 返回 `None`，也没有公开 strategy
getter；薄封装不得声称持有 tester 或读取其 callback 参数。场景边界只能由薄封装按 §8.1 只读
轮询 `LiveNode.cache` 识别：market leg 是唯一新增且完整 filled 的 market order；close leg 是唯一
新增且完整 filled 的 reduce-only order 且 `positions_open()` 为空；passive leg 是唯一新增、先
accepted 后 canceled 且没有 `OrderFilled` 的 post-only order。多余/无法归属的 order、deadline、
缺失 history 或不相容 terminal fact 均进入非 `consistent` 分类。

只有该独立入口可以使用官方 tester 所需的最小 risk bypass；能力必须在入口内封闭且不能由普通
Strategy import、配置或调用。post-only order 若 reject、partial fill 或在 cancel 前/期间成交，
场景立即判失败；只有在原 order terminal、零 active/inflight 且 position 重算为确定非零后，才能按
§1.3 以 `abs(position)` 原值做唯一一笔 reduce-only cleanup；原值不通过 market filters 时不得提交。
不得为了
让场景通过而改成 aggressive limit、重发、在 tester 外提交订单或扩大 tester 功能。

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

在同一 source commit/source-tree digest、dependency lock、已通过 §1.2/§4.2 gates 的精确 runtime
和 frozen config identity 上，使用四个
全新且初始为空的 repo 外分区依次运行 DataTester、ExecTester market-close attempt、ExecTester
passive-cancel attempt 和 Strategy。四个分区的逻辑 ID 必须分别为 `data_tester`、
`exec_tester_market_close`、`exec_tester_passive_cancel`、`demo_strategy`，每个分区恰好产生一份
§9.1 canonical evidence record。聚合器只验证既有
证据，不创建客户端或订单。所有 required scenario 为 `PASS`、所有 identity/digest 一致且最终
order/position/unknown 清场后，才可原子发布
`docs/product/stage4-binance-demo-acceptance.json`。任何失败不得创建新成功文件，也不得覆盖
既有成功文件。固定 rc4 capability gates 未解除时，本节没有成功发布路径；聚合器必须保留
`HALTED` 诊断，不能用 DataTester 或离线测试通过替代缺失的 order-enabled evidence。

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
5. 只有 position 为确定非零时，才按 §1.3/§3.1 冻结 `close_quantity = abs(position)`；仅当它可无损
   构造并原值通过 reduce-only market filters 时提交恰好一笔 cleanup，否则不提交并记录
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
| ambiguous acknowledgement | 等待 acceptance 超时或 transport unknown | Strategy 最多发出一次公开 `query_order` 并等待其 profile 更新；ExecTester 不代替官方 tester 发 command，只由官方 staged mode 按 §5 等待 cache graph 收敛 | 把 query 的 `None` 返回值当 report、当作 reject、用新 ID 重发、继续开仓、原 order 未 terminal 或 active/inflight 非零时 cleanup | `HALTED` | command/entry identity、timeout/transport fact、terminal/active proof、后续 entry-profile result |
| duplicate / out-of-order event | 任一 order state | 忽略重复的状态推进并 reconcile；记录冲突 | 二次推进、二次提交 | `HALTED` | event identity、expected/observed state |
| cancel/fill race | 等待 cancel | reconcile；严格按 §5 证明原 order terminal 与零 active/inflight 后重算 position；仅当 `abs(position)` 原值通过 §1.3 market filters 时提交唯一 cleanup | 同时假定 canceled 和 filled、重发 cancel/order、用当前 cumulative fill 提前 cleanup、调整 exposure 以通过 filter | `HALTED` | §8 entry profile 的 cancel/fill facts、terminal/active proof、冻结 position/filter 结果、最终 position |
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
| `ST4-SAFE-005` | ExecTester 的 reject、ambiguous ACK、unexpected/partial fill 和 cancel/fill race 禁止盲目重发；只有官方 staged mode 按 §5 证明原订单 terminal、零 active/inflight 并重算确定 position，且精确 `abs(position)` 原值通过 §1.3 market filters 后才可执行唯一 cleanup。 |
| `ST4-SAFE-006` | Strategy 对 ambiguous、duplicate、out-of-order、timeout 和 conflict 设置不可逆 `HALTED` latch，禁止新开仓。 |
| `ST4-SAFE-007` | partial fill、late fill 或 passive limit 成交必定使验收失败；不得按中间 cumulative fill 提前清场，只能在原订单 terminal、零 active/inflight 后以 Nautilus 重算的精确 `abs(position)` 原值有限清场；不得为满足 minimum/step/notional 取整或放大，原值不合法时禁止提交并保持 `cleanup_incomplete`。 |
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
- **Cleanup**：cache 的 open/in-flight order counts、open positions 和当前 entry profile 的 terminal
  facts 同时为零/closed，且无 deadline 或其他 unresolved unknown。发生过非零 exposure 时还必须记录
  §1.3 冻结的 exact position、close quantity、逐项 market-filter 判定和最终 flat proof；若精确
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

#387 必须提交至少一个包含非 ASCII/NFC、nullable、decimal string 和 nested key ordering 的 evidence
golden vector，固定 canonical UTF-8 bytes、`run_id` 与 `evidence_digest`；#391 必须提交 acceptance
golden vector，证明 `generated_at` 改变不改变 `acceptance_digest`，任一其他输入改变会改变 digest。
schema 只允许以下顶层事实：

| 字段 | 内容 |
| --- | --- |
| `schema` | 固定 `tracequant-stage4-demo-evidence-v1` |
| `run_id` | 由本记录稳定 payload 导出的不可逆 ID |
| `source_commit` | 运行源码 commit |
| `source_tree` | §1.4 的 Git tree object ID、`source_tree_digest`、零 dirty/untracked-executable counts 与 import-origin digest；不含绝对路径 |
| `dependency_lock_digest` | `uv.lock` digest |
| `runtime` | §1.4 的 distribution/version、upstream commit、实际 wheel filename/hash/tag、installed-tree digest 和按冻结投影得到的 identity digest |
| `environment` | 固定 `BINANCE_DEMO_USD_M` |
| `config_digest` | §9.3 脱敏冻结配置 digest |
| `acceptance_batch_id` | §9.3 batch key 按冻结投影导出的 64 位小写 SHA-256；四份 run record 必须完全相同 |
| `account_reference_digest` | DataTester 为 `null`；三个 order-enabled 运行严格按 §9.3 从各自公开 Account identity 和同一 batch key 导出；不得保存 account ID |
| `instrument` | 固定 `BTCUSDT-PERP.BINANCE` 及本次 constraints digest |
| `scenario` | 恰为 `data_tester`、`exec_tester_market_close`、`exec_tester_passive_cancel` 或 `demo_strategy`，并与分区逻辑 ID 相同 |
| `started_at` / `ended_at` | 运行边界 |
| `result` / `terminal_state` | `PASS`/`FAIL` 与 `COMPLETE`/`HALTED` |
| `observations` | §8 最小 order/fill/position/balance 分类和 scenario-specific counts/digests |
| `cleanup` | active、pending、position、unknown counts 与分类 |
| `failure` | 成功时 `null`；失败时稳定 code、阶段和脱敏 diagnostic digest |
| `evidence_digest` | canonical payload（排除本字段）的 digest |

`run_id` 和 `evidence_digest` 严格按上述两个投影依序导出，避免循环身份。
DataTester 的 order/fill/position/balance observations 必须明确为 `not_applicable`；这是合同明确
排除该事实的 marker，不是 §8 reconciliation 分类，也不能伪造空成功。两个 ExecTester attempt
和 Strategy 必须包含全部四类观察及 cleanup。三个 order-enabled record 的
`account_reference_digest` 必须逐 byte 相同，DataTester 必须为 `null`；最终 tracked record 不保存
该 digest 或任何 account identity。

### 9.2 `tracequant-stage4-demo-acceptance-v1`

最终 tracked record 只包含：schema、`acceptance_batch_id`、source commit/source-tree digest、
dependency lock digest、runtime identity、environment、config/instrument constraints digest、按
`data_tester`、`exec_tester_market_close`、`exec_tester_passive_cancel`、`demo_strategy` 固定 key
保存的四个 source evidence digest、同样四项的 required run matrix（其中两个 ExecTester 项再共同
归约为 `exec_tester` required scenario）、最终 order/fill/position/balance 分类、cleanup counts、
`LIVE_NOT_APPROVED`、生成时间和 acceptance digest。两个 ExecTester run 必须各自存在且为 `PASS`；
不得用一个 digest 填两个 key，也不得用 envelope 或 scenario-level digest 替代任一 run digest。
它不得嵌入原始事件、日志、batch key 或 account reference。相同输入按 §9.1 的
acceptance 投影必须得到相同 acceptance digest。

### 9.3 分区、batch account reference、脱敏和发布

- batch initializer 在任何联网运行前创建一个全新、初始为空、repo 外且仅当前用户可访问的 batch
  root；在其中以 OS CSPRNG 生成恰好 32 bytes 的 `batch_key`，并用 exclusive create 写入权限为
  `0600` 的 `account-reference.key`，文件内容只含这 32 个原始 bytes。已存在、长度/权限不符或无法
  独占创建的文件必须使 batch 在创建任何 client 前失败；key 不得来自 CLI、credential、account ID、
  source/config digest 或可预测 PRNG。
- `acceptance_batch_id = SHA256(UTF8("tracequant-stage4-acceptance-batch-v1") || 0x00 || batch_key)`，输出
  64 位小写 hex。initializer 通过 `TRACEQUANT_STAGE4_BATCH_ID` 向四个顺序运行的进程传递该值；只向
  三个 order-enabled 进程另传 `TRACEQUANT_STAGE4_ACCOUNT_REFERENCE_KEY_FILE`，其值是上述 repo 外
  key file 的路径。batch ID 只写入 schema 指定的 `acceptance_batch_id` 字段；key bytes、key-file
  path 和两个环境变量名不得写入 frozen config、evidence、日志或 tracked record。order-enabled
  进程必须读取恰好 32 bytes，并复算 batch ID，一旦缺失或不相等就在创建 order 前 `HALTED`。
- 每个 order-enabled 进程从当前入口 §8 public observation profile 的唯一 Account 对象取得
  `account_reference = NFC(str(account.id)).encode("utf-8")`；空值、多 Account、不可编码或不同
  observation surface 的 identity 均为 `HALTED`。该明文只可在进程内用于下式，禁止写入磁盘、
  evidence 或日志：
  `account_reference_digest = HMAC-SHA256(batch_key, UTF8("tracequant-stage4-account-reference-v1") || 0x00 || UTF8("BINANCE_DEMO_USD_M") || 0x00 || account_reference)`，结果编码为 64 位小写 hex。
- 聚合器要求四份 record 的 `acceptance_batch_id` 相同、DataTester 的
  `account_reference_digest` 为 `null`，并以 constant-time equality 比较三个 order-enabled digest；
  任一缺失或不相等都拒绝发布。聚合完成、拒绝或正常 failure 后，batch owner 必须在 `finally`
  路径关闭 handle、清除持有的 byte buffer 并 unlink key file；进程崩溃留下的 key/root 使该 batch
  永久失效，只能由操作者删除，任何新 batch 都不得读取或复用。batch key 只用于本批 HMAC，不是
  venue credential，生命周期不得跨 batch。
- 每次联网运行使用新的、显式命名且初始为空的 repo 外 evidence partition；四个逻辑 partition ID
  固定为 `data_tester`、`exec_tester_market_close`、`exec_tester_passive_cancel`、`demo_strategy`，
  并与 record 的 `scenario` 一致。运行只记录逻辑 partition ID，任何 tracked record 不得出现绝对
  本机路径；
- frozen config digest 的 payload 包含 runtime、environment、product、instrument、account-mode
  declaration、limits 和 deadlines；只记录 credential **变量名**，不含值；batch ID、key、key-file
  path 和 account reference 都不属于 config payload；
- secret、完整账户标识、签名请求、认证 header/cookie、可重放认证材料和未脱敏 raw payload
  不得进入配置文件、Issue、日志或 tracked record；
- 原始成功和失败证据都留在外部分区；仓库只发布通过 §9.2 的脱敏 acceptance record；
- required evidence 缺失、identity/config drift、digest 不匹配、非 `consistent` 分类或清场不完整
  时，不得创建或覆盖成功记录。写入必须先完整验证临时 payload，再做单文件原子替换。

### 9.4 证据要求编号

| ID | 冻结要求 |
| --- | --- |
| `ST4-EVID-001` | 准入成功输出不含 secret 的 frozen config payload 和 digest，失败不输出可冒充成功的 digest。 |
| `ST4-EVID-002` | 所有 run evidence 使用 `tracequant-stage4-demo-evidence-v1`，并绑定 §1.4/§9.1 对实际 source、lock、wheel 和 installed distribution bytes 验证得到的完整 identity；预期常量不能自证。 |
| `ST4-EVID-003` | order/fill/position/balance 只用 §8 为当前入口冻结的 rc4 public observation profile 分类；ExecTester 使用可检索 cache object graph，Strategy 使用 callback/cache/account；非 `consistent` 必定阻止成功。 |
| `ST4-EVID-004` | 原始证据只写四个全新 repo 外分区；batch key 按 §9.3 生成、传递、使用和销毁，tracked 内容必须脱敏、只含逻辑引用和 digest。 |
| `ST4-EVID-005` | 跨运行 identity、config 或 digest 不一致时禁止拼接证据，失败证据必须保留在外部分区。 |
| `ST4-EVID-006` | DataTester 证据包含 quote/trade counts、instrument constraints 和 timestamp 检查结果，不含执行事实。 |
| `ST4-EVID-007` | capability gate 解除后，两个 ExecTester attempt 各自产生独立 record/partition/digest，用 §8.1 的可检索 cache object graph 分别绑定 market fill/reduce-only flat 与 passive accepted/canceled，并共同证明最终清场；gate 未解除时只允许 `FAIL/HALTED` capability diagnostic。 |
| `ST4-EVID-008` | Strategy 证据绑定固定状态序列、每个 order/fill、fault protection 和最终 reconciliation/cleanup。 |
| `ST4-EVID-009` | 最终 record 使用 `tracequant-stage4-demo-acceptance-v1`，完整聚合四个新分区/四个 source digest，验证同一 `acceptance_batch_id` 与三个 order-enabled account digest 相等，并声明 `LIVE_NOT_APPROVED`。 |
| `ST4-EVID-010` | acceptance digest 对同一输入稳定；任何失败、漂移或未清场不得创建或覆盖成功记录。 |

## 10. #386–#391 唯一映射

每个编号只由一项叶子 Issue 实现。依赖方可以读取其输出，但不能复制编号所有权。

| 叶子 Issue | 唯一承接编号 | 有界交付物 |
| --- | --- | --- |
| [#386](https://github.com/PhoenixSss/tracequant/issues/386) | `ST4-REQ-001`–`006`；`ST4-SAFE-001`；`ST4-EVID-001` | typed config、凭据/账户准入、constraints/quantity/deadline 合同、frozen config digest；在固定 rc4 上实现 §1.2 capability fail-closed，不修改上游包 |
| [#387](https://github.com/PhoenixSss/tracequant/issues/387) | `ST4-REQ-007`；`ST4-SAFE-002`；`ST4-EVID-002`–`005` | evidence schema、Nautilus-owned 状态分类、外部分区与脱敏/digest 行为 |
| [#388](https://github.com/PhoenixSss/tracequant/issues/388) | `ST4-REQ-008`；`ST4-SAFE-003`；`ST4-EVID-006` | 官方 DataTester 的固定 plan、薄入口和 data evidence |
| [#389](https://github.com/PhoenixSss/tracequant/issues/389) | `ST4-REQ-009`；`ST4-SAFE-004`–`005`；`ST4-EVID-007` | 固定 rc4 上只交付 §4.2 capability diagnostic/离线 plan；新官方 runtime 经修订批准后才交付双 attempt ExecTester evidence |
| [#390](https://github.com/PhoenixSss/tracequant/issues/390) | `ST4-REQ-010`–`011`；`ST4-SAFE-006`–`009`；`ST4-EVID-008` | 固定 Demo Strategy、完整 handler 清单与逐 handler fault protection、reconciliation/cleanup evidence；credentialed run 受 §1.2 gate 阻止 |
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
- 禁止在仓库中 vendor、fork、monkeypatch 或复制官方 `nautilus_trader` namespace，也禁止通过
  解析 warning/log、私有 Rust/Python 对象或 tester 外下单绕过 §1.2/§4.2 capability gates；
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

阶段 4 只有在 §1.2/§4.2 所需能力已进入 maintainer 批准并精确锁定的新官方 runtime、本文档
完成版本修订、#386–#391 各自通过、#391 发布有效 acceptance record 且 Feature completion audit
通过后才完成。当前固定 rc4 下 order-enabled capability blocked，因此不能达到 Feature completion，
也不得把 capability diagnostic 或离线 plan 解释为下列接受状态。最终成功状态仍是：

```text
NAUTILUS_PRIMARY
BINANCE_DEMO_BASIC_LOOP_ACCEPTED
LIVE_NOT_APPROVED
```

## 13. 规范来源

- [TraceQuant 分阶段推进计划](<../research/foundation-selection/TraceQuant 分阶段推进计划.md>)，阶段 4/5；
- [ADR-0001：NautilusTrader primary runtime](../architecture/adr-0001-nautilustrader-primary-runtime.md)；
- [NautilusTrader rc4 Binance integration](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/docs/integrations/binance.md)；
- [rc4 Binance Futures config application](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/adapters/binance/src/futures/execution.rs#L1114-L1162)；
- [rc4 Binance Futures hedge-mode initialization](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/adapters/binance/src/futures/execution.rs#L1596-L1609)；
- [Binance USD-M order filters](https://developers.binance.com/en/docs/derivatives/usds-margined-futures/common-definition#filters)；
- [rc4 ExecTester fixed quantity config](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/testkit/src/testers/exec/config.rs#L47-L98)；
- [rc4 ExecTester quote/order behavior](https://github.com/nautechsystems/nautilus_trader/blob/a0400251110653b6d8ae6a9b5b89c4543fa85a2d/crates/testkit/src/testers/exec/strategy.rs#L264-L278)；
- [nautilus_trader #5039](https://github.com/nautechsystems/nautilus_trader/issues/5039)。
