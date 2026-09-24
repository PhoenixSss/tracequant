# 阶段 4 Binance USD-M Demo 只读能力探查报告（#399）

- 探查日期及复测日期：2026-09-24 UTC。
- TraceQuant 探查时源码提交：`1b1e466554e3b5a9a0c05910464c6c5c8dc0d91a`（#399 的 LCK Delivery Prepare 基线）；Python 3.13.14；实际导入的 `nautilus_trader.__version__` 为 `2.0.0rc4`。R399-05 复测时源码提交另见该次记录。
- 固定环境：`BinanceProductType.USD_M`、`BinanceEnvironment.DEMO`、`BTCUSDT-PERP.BINANCE`；产品状态 **DEMO_ONLY、LIVE_NOT_APPROVED**。
- 范围：[#399](https://github.com/PhoenixSss/tracequant/issues/399) 的 C01–C04 只读探查，以及本次实际出现的 C12 异常。未读取 Demo 凭据，显式传入 `api_key=None`、`api_secret=None`；未创建 Binance execution client，未提交或撤销订单。
- 原始日志仅留在仓库外的 `/tmp/tracequant-399-*.log`（见逐次记录）；本报告只保留公开行情样本及脱敏结论。`/tmp` 是临时目录，不保证长期保存。R399-01–04 的原始脚本与日志已无法在本次修复环境中取得，不能追认其探针内容或完整原始输出；当前 C01–C04 结论以新增 R399-05 的可核对探针与观察为主要依据。

## 环境、路由及操作边界

实际执行 `git rev-parse HEAD` 得到上述提交；`uv run --frozen python -c 'import nautilus_trader as n; print(n.__version__); print(n.__file__)'` 得到 `2.0.0rc4`，模块位于本工作区 `.venv` 的 `site-packages`。探针从标准 `https_proxy` 或 `HTTPS_PROXY` 环境变量选取代理，只输出 `proxy_configured=True`，没有输出代理值。`BinanceDataClientConfig` 实际报告 `product_type=USD_M`、`environment=DEMO`、`base_url_http=None`、`base_url_ws=None`、`has_proxy_url=True`；未覆盖 endpoint，也未启用 `us`。rc4 固定源码中的 [URL 选择](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc4/crates/adapters/binance/src/common/urls.rs) 与 [常量](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc4/crates/adapters/binance/src/common/consts.rs) 将此配置映射到 `https://demo-fapi.binance.com` 和 `wss://demo-fstream.binance.com/ws`；这是对固定源码的路由推断，探针未单独捕获网络层目标地址。rc4 日志确实报告 `environment=Demo`、两条公开 WebSocket 已连接，并收到目标标的公开数据。

R399-01–04 的一次性探针位于仓库外：`/tmp/tracequant-399-instrument.py` 与 `/tmp/tracequant-399-stream.py`。前者调用官方 `load_binance_instruments(config)`；后者注册官方 `DataTesterConfig` 内置 actor，并由最小 `DataActor` 通过公开 `subscribe_instrument`、`subscribe_quotes`、`subscribe_trades` 回调计数。两者都使用 `BinanceInstrumentProviderConfig(load_all=False, load_ids=["BTCUSDT-PERP.BINANCE"], query_commission_rates=False)`。DataTester 设 `subscribe_instrument=True`、`subscribe_quotes=True`、`subscribe_trades=True`、`log_data=False`；最后一次另设 `request_instruments=True`。`LiveNode` 只注册 `BinanceDataClientFactory`，没有注册执行客户端。最终临时脚本 SHA-256 分别为 `c616349a75eb6eded58339c0505eaa76d52086e0b387f29cb05c1462ad4f14e5` 与 `5a2a02f168801c636f608cf1c7037d866687a6310b3b7e3f6bd1f9f06a7f79ed`；流探针在各次尝试之间按下文修正过，最终哈希不能代表较早版本。
这些临时脚本在本次修复工作区无法取得；R399-05 的[逐字探针](stage-4-binance-demo-public-data-probe-2026-09-24.txt)留仓库作为版本证据。

未做操作者 Demo UI 核对，也没有账户、持仓、活动订单或未决订单的交易所事实。只读节点本地启动时的零持仓/订单初始化日志不证明 Demo 账户 flat。本次探查结束时，这些账户状态仍为**未知**；探针自身没有产生订单。

## 逐次记录

### R399-01：公开 instrument 加载（C01、C02、C04）

- UTC 起止：`2026-09-24T07:07:00.368510Z` 至 `07:07:03.522187Z`；上述 TraceQuant 提交与 rc4。
- 实际命令：`uv run --frozen python /tmp/tracequant-399-instrument.py`。脚本把 `USD_M`、`DEMO`、目标 ID、空凭据和当前 HTTPS 代理传给官方 `load_binance_instruments`，设置 90 秒上限。
- 探针版本：原记录只留下最终哈希；脚本内容与终端原始输出未保留，现无法独立核对其执行版本。
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
- 探针版本：当次脚本内容及哈希未保留，现无法独立核对；下述失败仍按原记录保留，不用后续成功覆盖。
- 预期：观察公开事件，然后正常结束。
- 实际：约 `07:08:51.654Z` 发出订阅；日志显示 Demo 数据客户端与公开 WebSocket 已连接。至 `07:09:30.467Z`，observer 收到 **87 quote、54 trade、0 instrument 回调**。quote 首/末事件时间为 `07:08:53.432Z` / `07:09:28.254Z`，trade 为 `07:08:53.678Z` / `07:09:28.254Z`；在各自流内未计到时间倒序，也未计到事件时间比本机观察时钟未来超过 1 秒的事件。
- 首个失败点：采集窗口结束时，探针误对由 `run_async()` 运行的节点调用 `LiveNode.stop()`；rc4 抛出 `RuntimeError`，要求使用 `node.handle().stop()`。这属于一次性探针收尾错误，并非 Demo 行情连接失败；异常后的 asyncio 清理仍触发了节点停止。此轮进程退出码为 1。另有 `UnsubscribeInstrument ... handler not implemented` 停止阶段警告。
- 结束状态：公开节点最终停止；账户活动/未决订单与净持仓未观察，未知；没有订单请求。
- 结论：quote/trade 流实际到达，但本轮正常收尾**不可用**。已识别新条件：改为 handle 停止；后续单独追加尝试，不覆盖本轮失败。
- 原始日志：本次只在工具终端显示，未另存文件。

### R399-03：修正 handle 后的行情窗口（C03、C04、C12）

- UTC 起止：`2026-09-24T07:10:22.467315Z` 至 `07:11:02.592547Z`；订阅 `07:10:26.284881Z`，采集窗口至 `07:10:52.571397Z`；上述提交与 rc4。
- 实际命令：`uv run --frozen python /tmp/tracequant-399-stream.py > /tmp/tracequant-399-stream-retry.log 2>&1`。相对 R399-02，脚本改为 `handle = node.handle()`、`handle.stop()`，窗口改为 30 秒；其余公开订阅不变。
- 探针版本：当次脚本内容及哈希未保留；最终流脚本哈希不适用于此轮，现无法独立核对。
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
- 探针版本：原记录有最终流脚本哈希，但脚本内容未保留，现无法独立核对。
- 预期：澄清 instrument 订阅 0 回调时，DataTester 的显式请求是否拿到对象。
- 实际：日志显示 DataTester 发出 `RequestInstruments`；约 `07:12:23.687Z`，其 timestamp 检查对返回的 instrument 分别警告 `ts_event=0`、`ts_init=0`。这与 R399-01 官方加载对象的两个 0 字段一致，说明**请求路径拿到了 instrument 对象，但对象没有可用时间戳**。observer 仍为 0 instrument 回调；显式请求的响应不等同于其订阅回调。同期还收到 **19 quote、12 trade**，事件时间范围 quote `07:12:24.376Z`–`07:12:34.298Z`、trade `07:12:24.376Z`–`07:12:32.182Z`；倒序与未来超过 1 秒计数均为 0。进程退出码 0。
- 首个失败点：无请求失败；DataTester 对 instrument 的时间尺度报上述警告，停止时继续出现 `UnsubscribeInstrument ... handler not implemented`。没有发现能够从这次 instrument 对象恢复真实更新时间的公开字段。
- 结束状态：节点停止；账户活动/未决订单与净持仓未观察，未知；没有订单请求。
- 结论：DataTester 显式请求 instrument **有条件可用**，但订阅本身未产生 observer instrument 回调，instrument `ts_event` / `ts_init` 不可作时间证据；quote/trade 再次到达。
- 仓库外原始日志：`/tmp/tracequant-399-instrument-request.log`，SHA-256 `e8f94578ca383f96294259530de51c8c378a57d0aa2631af66fe002b8b58befe`。

### R399-05：固定脚本版本的公开数据复测（C01–C04、C12）

- UTC 起止：`2026-09-24T13:16:24.613083Z` 至 `13:16:56.727057Z`；复测时 TraceQuant 提交 `2910a4b99c1a49ca29b35ca31894087b9e8f73b0`（已包含 #406 的基线路径修正，尚未提交本次报告补充）；实际导入 NautilusTrader `2.0.0rc4`。
- 实际命令：`timeout 150s env -u BINANCE_DEMO_API_KEY -u BINANCE_DEMO_API_SECRET -u BINANCE_API_KEY -u BINANCE_API_SECRET uv run --frozen python /tmp/tracequant-399-rerun.py > /tmp/tracequant-399-rerun.log 2>&1`。四个已知的 Binance 凭据环境变量从该子进程移除；脚本显式使用空凭据，只注册 Demo USD-M 数据客户端与官方 `DataTester`，未创建 execution client。所用 `/tmp` 脚本与[仓库内逐字保存的探针](stage-4-binance-demo-public-data-probe-2026-09-24.txt) SHA-256 均为 `df3fe8dd513f6e1f45eeb1fe91a08197276df96c127813798364637b6f15f307`；可从该 `.txt` 原样复制到 `/tmp/tracequant-399-rerun.py` 后核对哈希。它仅是本次探查的版本证据，不是固定 runner。
- 预期：以官方 `load_binance_instruments` 取得公开 instrument，再用官方 DataTester 加只读 observer 在有界窗口观察 instrument 请求、quote/trade 与时间。代理只从当前标准 HTTPS 代理环境变量取得，不记录其值；配置观察为 `USD_M`、`DEMO`、目标 ID、`has_proxy=true`、无 HTTP/WS URL 覆盖。
- 实际 instrument：`13:16:28.496673Z` 返回目标 `BTCUSDT-PERP.BINANCE`；price/size precision 为 `2`/`4`，increment 为 `0.10`/`0.0001`，最小/最大数量为 `0.0001`/`1000`，最小名义金额 `50.00000000 USDT`，最小/最大价格 `261.10`/`809484`，`ts_event=0`、`ts_init=0`。这些是当次公开对象值，不能用 0 时间戳证明新鲜度。
- 实际订阅：`13:16:28.603549Z` 启动只读节点；日志报告 `environment=Demo`，两条公开 WebSocket 于 `13:16:32.869878Z`、`13:16:33.322727Z` 连接。`13:16:33.323033Z` observer 开始，DataTester 发出 `RequestInstruments`，instrument 时间检查仍分别警告 `ts_event=0`、`ts_init=0`。observer 共收到 **120 quote、52 trade、0 instrument 回调**，进程退出码 0，节点正常停止并释放。
- 行情窗口：quote `ts_event` 首/末为 `13:16:33.306Z`/`13:16:53.803Z`；trade 为 `13:16:34.384Z`/`13:16:53.799Z`。各流回调顺序中的事件时间倒序计数均为 0；事件时间比本机收到时刻未来超过 1 秒的计数均为 0。脱敏公开样本：

| 类型 | 目标 ID | 价格 / 数量 | `ts_event` UTC | `ts_init` UTC | observer 收到 UTC |
| --- | --- | --- | --- | --- | --- |
| quote | `BTCUSDT-PERP.BINANCE` | bid `83724.50`，ask `83726.70` | `13:16:33.306` | `13:16:34.211315` | `13:16:34.212595` |
| quote | `BTCUSDT-PERP.BINANCE` | bid `83724.50`，ask `83727.20` | `13:16:34.384` | `13:16:34.820662` | `13:16:34.820918` |
| trade | `BTCUSDT-PERP.BINANCE` | price `83726.70`，size `0.0010` | `13:16:34.384` | `13:16:34.840287` | `13:16:34.840650` |
| trade | `BTCUSDT-PERP.BINANCE` | price `83727.20`，size `0.0009` | `13:16:34.384` | `13:16:34.993206` | `13:16:34.993455` |

- 首个失败点/限制：无本轮 instrument 加载、连接、quote/trade 订阅或停止失败。DataTester 报 instrument 两个 0 时间字段警告，停止阶段仍有 `UnsubscribeInstrument ... handler not implemented` 警告；observer 没有 instrument 订阅回调。没有独立校准交易所时钟，窗口结果不能推出长期无缺口或延迟上限。
- 结束状态：只读节点已停止；未观察 Demo 账户、净持仓及活动/未决订单，这些事实仍未知；探针没有发送订单请求。
- 结论：本次真实 Demo 窗口的 instrument 加载与 quote/trade 接收可用；instrument 时间不可用，quote/trade 时间解释有条件可用。此轮可核对脚本与脱敏输出构成当前 C01–C04 的主要依据；R399-01–04 的探针版本仍不可复核；旧失败记录继续保留。
- 仓库外原始日志：`/tmp/tracequant-399-rerun.log`，156 行，SHA-256 `d73e6d4ffce14a36db1a057051dcd9e82f8c03b994119ae280860da1c46d4981`。原始日志只在本机 `/tmp`；关键非敏感结果已摘录于本记录，日志消失不影响核对脚本版本及所报告样本的含义，但无法独立重算这次的全部 120/52 个事件。

## Checklist 结论与后续订单探查前置事实

| ID | 结论 | 本次依据与界限 |
| --- | --- | --- |
| C01 | 可用 | R399-05 固定脚本核对复测提交、实际 rc4、`USD_M` + `DEMO`、无 URL 覆盖与已配置代理；日志显示 Demo 公开 WebSocket 连接。目标 host 仍只由固定 rc4 源码路由推断，未单独网络层捕获。 |
| C02 | 有条件可用 | R399-05 官方加载器返回一个目标 instrument 与上述约束；DataTester 显式请求触发两个 0 时间字段警告，observer 订阅没有 instrument 回调。订单前须重新加载约束，不能用 instrument 时间戳判断新鲜度。 |
| C03 | 可用 | R399-05 可核对探针在约 23 秒 observer 窗口中收到 120 quote、52 trade，均有目标 ID 与价格样本；只证明该窗口，不证明持续可用。旧窗口计数保留为历史记录，不作为当前主要依据。 |
| C04 | 有条件可用 | R399-05 quote/trade 样本的事件、初始化及观察时间顺序可解释；各流所计倒序及未来超过 1 秒均为 0。instrument 时间仍为 0，未独立校准交易所时钟或测试长期过期。 |
| C05–C11 | 未尝试 | #399 限定只读；未做账户 UI 核对或创建 execution client，故账户模式、初始订单/持仓及带订单能力没有证据。 |
| C12 | 有条件可用 | R399-05 再次观察到 instrument 0 时间戳与 `UnsubscribeInstrument` 未实现警告；R399-02 的异步停止 API 错误按版本缺失的历史记录保留，未进行订单异常探查。 |

已排除本次行情不可达、目标 instrument 不存在或 quote/trade 全无事件作为**R399-05 等已记录窗口**的失败原因。后续带订单探查仍缺少操作者 Demo UI 的 one-way、isolated、1x、flat、零活动订单核对；缺少公开账户/持仓/活动订单事实及将这些事实与 UI 对齐的确认；还需在下单前用当时有效的 instrument 约束与行情计算极小合法数量。本报告没有为任何订单、撤单、仓位或账户状态提供授权，也不能将 `ts_event=0` 解释为 instrument 更新时刻。阶段 4 尚未完成；**LIVE_NOT_APPROVED**。
