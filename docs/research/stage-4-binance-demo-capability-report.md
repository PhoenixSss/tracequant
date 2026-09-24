# 阶段 4 Binance USD-M Demo 只读能力探查报告（#399）

- 探查日期：2026-09-24 UTC。
- TraceQuant 探查时源码提交：`1b1e466554e3b5a9a0c05910464c6c5c8dc0d91a`（#399 的 LCK Delivery Prepare 基线）；Python 3.13.14；实际导入的 `nautilus_trader.__version__` 为 `2.0.0rc4`。
- 固定环境：`BinanceProductType.USD_M`、`BinanceEnvironment.DEMO`、`BTCUSDT-PERP.BINANCE`；产品状态 **DEMO_ONLY、LIVE_NOT_APPROVED**。
- 范围：[#399](https://github.com/PhoenixSss/tracequant/issues/399) 的 C01–C04 只读探查，以及本次实际出现的 C12 异常。未读取 Demo 凭据，显式传入 `api_key=None`、`api_secret=None`；未创建 Binance execution client，未提交或撤销订单。
- 原始日志仅留在仓库外的 `/tmp/tracequant-399-*.log`（见逐次记录）；本报告只保留公开行情样本及脱敏结论。`/tmp` 是临时目录，不保证长期保存。

## 环境、路由及操作边界

实际执行 `git rev-parse HEAD` 得到上述提交；`uv run --frozen python -c 'import nautilus_trader as n; print(n.__version__); print(n.__file__)'` 得到 `2.0.0rc4`，模块位于本工作区 `.venv` 的 `site-packages`。探针从标准 `https_proxy` 或 `HTTPS_PROXY` 环境变量选取代理，只输出 `proxy_configured=True`，没有输出代理值。`BinanceDataClientConfig` 实际报告 `product_type=USD_M`、`environment=DEMO`、`base_url_http=None`、`base_url_ws=None`、`has_proxy_url=True`；未覆盖 endpoint，也未启用 `us`。rc4 固定源码中的 [URL 选择](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc4/crates/adapters/binance/src/common/urls.rs) 与 [常量](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc4/crates/adapters/binance/src/common/consts.rs) 将此配置映射到 `https://demo-fapi.binance.com` 和 `wss://demo-fstream.binance.com/ws`；这是对固定源码的路由推断，探针未单独捕获网络层目标地址。rc4 日志确实报告 `environment=Demo`、两条公开 WebSocket 已连接，并收到目标标的公开数据。

一次性探针位于仓库外：`/tmp/tracequant-399-instrument.py` 与 `/tmp/tracequant-399-stream.py`。前者调用官方 `load_binance_instruments(config)`；后者注册官方 `DataTesterConfig` 内置 actor，并由最小 `DataActor` 通过公开 `subscribe_instrument`、`subscribe_quotes`、`subscribe_trades` 回调计数。两者都使用 `BinanceInstrumentProviderConfig(load_all=False, load_ids=["BTCUSDT-PERP.BINANCE"], query_commission_rates=False)`。DataTester 设 `subscribe_instrument=True`、`subscribe_quotes=True`、`subscribe_trades=True`、`log_data=False`；最后一次另设 `request_instruments=True`。`LiveNode` 只注册 `BinanceDataClientFactory`，没有注册执行客户端。最终临时脚本 SHA-256 分别为 `c616349a75eb6eded58339c0505eaa76d52086e0b387f29cb05c1462ad4f14e5` 与 `5a2a02f168801c636f608cf1c7037d866687a6310b3b7e3f6bd1f9f06a7f79ed`；流探针在各次尝试之间按下文修正过，最终哈希不能代表较早版本。

未做操作者 Demo UI 核对，也没有账户、持仓、活动订单或未决订单的交易所事实。只读节点本地启动时的零持仓/订单初始化日志不证明 Demo 账户 flat。本次探查结束时，这些账户状态仍为**未知**；探针自身没有产生订单。

## 逐次记录

### R399-01：公开 instrument 加载（C01、C02、C04）

- UTC 起止：`2026-09-24T07:07:00.368510Z` 至 `07:07:03.522187Z`；上述 TraceQuant 提交与 rc4。
- 实际命令：`uv run --frozen python /tmp/tracequant-399-instrument.py`。脚本把 `USD_M`、`DEMO`、目标 ID、空凭据和当前 HTTPS 代理传给官方 `load_binance_instruments`，设置 90 秒上限。
- 预期：返回目标 instrument 及公开精度、数量和价格约束。
- 实际：3.15 秒内返回 1 个 `CryptoPerpetual`，`id=BTCUSDT-PERP.BINANCE`。公开对象字段如下；`symbol` 属性未暴露，不能把 ID 与另一独立的 symbol 字段混为一谈。

| 字段 | 本次公开对象值 |
| --- | --- |
| `price_precision` / `size_precision` | `2` / `4` |
| `price_increment` / `size_increment` | `0.10` / `0.0001` |
| `min_quantity` / `max_quantity` | `0.0001` / `1000` |
| `min_notional` | `50.00000000 USDT` |
| `min_price` / `max_price` | `261.10` / `809484` |
| `ts_event` / `ts_init` | `0` / `0` |

- 首个失败点：无加载错误；**instrument 时间字段为 0，不能解释为这次加载或交易所更新时间**。
- 结束状态：探针只读；账户活动/未决订单与净持仓均未观察，未知。
- 结论：公开 rc4 Demo instrument 加载**可用**，但 instrument 时间解释**不可用**。上述约束只是当次公开对象观察，订单前须重新取得约束与有效价格，并完成账户前置核对。
- 原始日志：本次只在工具终端显示，未另存文件；临时脚本位于上述仓库外路径。

### R399-02：DataTester 首轮订阅，探针收尾失败（C03、C04、C12）

- UTC 起止：`2026-09-24T07:08:45.074416Z` 至约 `07:09:40.558Z`；上述提交与 rc4。
- 实际命令：`uv run --frozen python /tmp/tracequant-399-stream.py`。当时脚本的观察睡眠为 45 秒，DataTester 与只读 observer 同时订阅目标 instrument、quote、trade；`LiveNode.run_async()` 运行。
- 预期：观察公开事件，然后正常结束。
- 实际：约 `07:08:51.654Z` 发出订阅；日志显示 Demo 数据客户端与公开 WebSocket 已连接。至 `07:09:30.467Z`，observer 收到 **87 quote、54 trade、0 instrument 回调**。quote 首/末事件时间为 `07:08:53.432Z` / `07:09:28.254Z`，trade 为 `07:08:53.678Z` / `07:09:28.254Z`；在各自流内未计到时间倒序，也未计到事件时间比本机观察时钟未来超过 1 秒的事件。
- 首个失败点：采集窗口结束时，探针误对由 `run_async()` 运行的节点调用 `LiveNode.stop()`；rc4 抛出 `RuntimeError`，要求使用 `node.handle().stop()`。这属于一次性探针收尾错误，并非 Demo 行情连接失败；异常后的 asyncio 清理仍触发了节点停止。此轮进程退出码为 1。另有 `UnsubscribeInstrument ... handler not implemented` 停止阶段警告。
- 结束状态：公开节点最终停止；账户活动/未决订单与净持仓未观察，未知；没有订单请求。
- 结论：quote/trade 流实际到达，但本轮正常收尾**不可用**。已识别新条件：改为 handle 停止；后续单独追加尝试，不覆盖本轮失败。
- 原始日志：本次只在工具终端显示，未另存文件。

### R399-03：修正 handle 后的行情窗口（C03、C04、C12）

- UTC 起止：`2026-09-24T07:10:22.467315Z` 至 `07:11:02.592547Z`；订阅 `07:10:26.284881Z`，采集窗口至 `07:10:52.571397Z`；上述提交与 rc4。
- 实际命令：`uv run --frozen python /tmp/tracequant-399-stream.py > /tmp/tracequant-399-stream-retry.log 2>&1`。相对 R399-02，脚本改为 `handle = node.handle()`、`handle.stop()`，窗口改为 30 秒；其余公开订阅不变。
- 预期：在有界窗口计数并正常停机。
- 实际：`run_async()` 节点运行中，公开 WebSocket 和目标 instrument/quote/trade 订阅报告成功。observer 收到 **98 quote、42 trade、0 instrument 回调**；采集窗口首/末 quote 事件时间 `07:10:27.030Z` / `07:10:52.392Z`，trade `07:10:27.048Z` / `07:10:52.156Z`。进程退出码 0，停止完成。样本均为 `BTCUSDT-PERP.BINANCE`：

| 类型 | 价格 / 数量 | `ts_event` UTC | `ts_init` UTC | observer 收到 UTC |
| --- | --- | --- | --- | --- |
| quote | bid `84047.50`，ask `84103.80` | `07:10:27.030` | `07:10:27.158703` | `07:10:27.159750` |
| quote | bid `84047.40`，ask `84103.80` | `07:10:27.048` | `07:10:27.182128` | `07:10:27.182801` |
| trade | price `84047.50`，size `0.0012` | `07:10:27.048` | `07:10:27.423481` | `07:10:27.424540` |
| trade | price `84047.50`，size `0.0008` | `07:10:27.292` | `07:10:27.565995` | `07:10:27.566532` |

- 时间检查：98 个 quote、42 个 trade 在各自回调顺序中 `ts_event` 倒序计数均为 0；`ts_event` 比本机回调时刻未来超过 1 秒的计数均为 0。样本中的 `ts_event` 早于 `ts_init`，`ts_init` 又早于 observer 收到时刻。此处只比较本机时钟与 rc4 公开字段，没有独立校准交易所时钟，不能据此保证长期时延或无缺口。
- 首个失败点：无本轮连接、订阅或停止失败；停止阶段仍有 `UnsubscribeInstrument ... handler not implemented` 警告。该警告未阻止本轮进程正常结束，但 instrument 订阅的取消能力未由此证明。
- 结束状态：节点停止；账户活动/未决订单与净持仓未观察，未知；没有订单请求。
- 结论：本次窗口的 quote/trade 接收**可用**；时间解释对 quote/trade **有条件可用**，instrument 时间仍不能解释。0 instrument 回调留下 DataTester instrument 请求行为待查。
- 仓库外原始日志：`/tmp/tracequant-399-stream-retry.log`，SHA-256 `3c3470da8281c9e5694a9827c81601c0a43fb9ef2db046ccfdc03a0d81faffab`。

### R399-04：显式 DataTester instrument 请求（C02、C03、C04、C12）

- UTC 起止：`2026-09-24T07:12:19.361625Z` 至 `07:12:44.527666Z`；订阅 `07:12:23.320767Z`，采集窗口至 `07:12:34.470785Z`；上述提交与 rc4。
- 实际命令：`uv run --frozen python /tmp/tracequant-399-stream.py > /tmp/tracequant-399-instrument-request.log 2>&1`。相对 R399-03，在官方 `DataTesterConfig` 中增加 `request_instruments=True`，窗口缩为 15 秒；只读 observer 仍订阅 quote/trade。
- 预期：澄清 instrument 订阅 0 回调时，DataTester 的显式请求是否拿到对象。
- 实际：日志显示 DataTester 发出 `RequestInstruments`；约 `07:12:23.687Z`，其 timestamp 检查对返回的 instrument 分别警告 `ts_event=0`、`ts_init=0`。这与 R399-01 官方加载对象的两个 0 字段一致，说明**请求路径拿到了 instrument 对象，但对象没有可用时间戳**。observer 仍为 0 instrument 回调；显式请求的响应不等同于其订阅回调。同期还收到 **19 quote、12 trade**，事件时间范围 quote `07:12:24.376Z`–`07:12:34.298Z`、trade `07:12:24.376Z`–`07:12:32.182Z`；倒序与未来超过 1 秒计数均为 0。进程退出码 0。
- 首个失败点：无请求失败；DataTester 对 instrument 的时间尺度报上述警告，停止时继续出现 `UnsubscribeInstrument ... handler not implemented`。没有发现能够从这次 instrument 对象恢复真实更新时间的公开字段。
- 结束状态：节点停止；账户活动/未决订单与净持仓未观察，未知；没有订单请求。
- 结论：DataTester 显式请求 instrument **有条件可用**，但订阅本身未产生 observer instrument 回调，instrument `ts_event` / `ts_init` 不可作时间证据；quote/trade 再次到达。
- 仓库外原始日志：`/tmp/tracequant-399-instrument-request.log`，SHA-256 `e8f94578ca383f96294259530de51c8c378a57d0aa2631af66fe002b8b58befe`。

## Checklist 结论与后续订单探查前置事实

| ID | 结论 | 本次依据与界限 |
| --- | --- | --- |
| C01 | 可用 | 固定提交、安装 rc4、`USD_M` + `DEMO`、无 URL 覆盖、代理已配置；rc4 固定源码路由与 Demo 连接日志互相支持。目标 host 未单独做网络层捕获。 |
| C02 | 有条件可用 | 官方加载器返回一个目标 instrument 与上述约束；DataTester 显式请求拿到时间字段为 0 的 instrument，普通订阅没有 observer 回调。须重新加载最新约束，不能使用 instrument 时间戳判断新鲜度。 |
| C03 | 可用 | 修正探针后 30 秒窗口中 98 quote、42 trade，另一次 15 秒窗口中 19 quote、12 trade，均有目标 ID 与价格样本。仅证明这些窗口，不证明持续可用。 |
| C04 | 有条件可用 | 本次 quote/trade 的事件、初始化及观察时间顺序可解释，所计倒序及未来超过 1 秒为 0；instrument 两个时间字段均为 0，未完成独立时钟校准或长期过期测试。 |
| C05–C11 | 未尝试 | #399 限定只读；未做账户 UI 核对或创建 execution client，故账户模式、初始订单/持仓及带订单能力没有证据。 |
| C12 | 有条件可用 | 实际遇到探针异步停止 API 错误、instrument 0 时间戳警告、`UnsubscribeInstrument` 未实现警告；未进行任何订单异常探查。 |

已排除本次行情不可达、目标 instrument 不存在或 quote/trade 全无事件作为**这几个观察窗口**的失败原因。后续带订单探查仍缺少操作者 Demo UI 的 one-way、isolated、1x、flat、零活动订单核对；缺少公开账户/持仓/活动订单事实及将这些事实与 UI 对齐的确认；还需在下单前用当时有效的 instrument 约束与行情计算极小合法数量。本报告没有为任何订单、撤单、仓位或账户状态提供授权，也不能将 `ts_event=0` 解释为 instrument 更新时刻。阶段 4 尚未完成；**LIVE_NOT_APPROVED**。
