# 阶段 4 Binance USD-M Demo 能力探查报告（#399、#400）

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

## #400：带凭据 Demo 订单与状态研究（R400-01–04，安全前置阻塞结论）

本节承接 #399 的公开行情结果，不改写其历史结论。#400 的 LCK Delivery Prepare 基线为 TraceQuant 提交 `d6f7017c4b9aaefe0d22bcf6c5e66976852627c2`；实际导入 NautilusTrader `2.0.0rc4`，Python 3.13.14。以下探针固定 `BinanceProductType.USD_M`、`BinanceEnvironment.DEMO` 和 `BTCUSDT-PERP.BINANCE`，不覆盖 HTTP/WS endpoint；HTTP 和 WebSocket 使用本机已配置的 HTTPS 代理，不记录代理值。凭据由仓库外权限 `600` 的本地文件按 `BINANCE_DEMO_API_KEY`、`BINANCE_DEMO_API_SECRET` 读取，工具未回显值；原始日志在 `/tmp`，均为权限 `600`，检查未发现密钥值。`/tmp` 不保证长期保存。**DEMO_ONLY、LIVE_NOT_APPROVED**。

本节 R400-01–04 均未提交、撤销或修改订单，未调用自动清场。操作者尚未提供本轮 Demo UI 的 one-way、isolated、1x、flat、零活动订单核对，因此本轮不允许带订单动作。rc4 签名查询的 `dual_side_position=false` 只是该次 API 观察，不能代替 UI 对全部前置状态的核对。

### R400-01：最小执行客户端认证（C01、C05、C12）

- UTC 日志窗口：`2026-09-26T04:37:14.154508574Z`–`04:37:39.276709494Z`；上述提交与 rc4。命令：`timeout 85s uv run --frozen python /tmp/tracequant-400-auth-probe.py`；脚本 SHA-256 `b27d34eb289b0c7d6d2db56dc2b741262c1cc87768487d98ea133fd4f33a2008`。
- 配置与预期：仅注册官方 `BinanceExecutionClientFactory`，关闭 WS trading 下单入口，不注册策略；期望只读认证并取得账户状态。凭据格式、非占位符和文件权限检查通过；之前对公开 `https://demo-fapi.binance.com/fapi/v1/ping` 的请求返回 HTTP 200，精确 UTC 未记录。
- 事实：执行客户端报告 `Hedge mode (dual side position): false`。固定 rc4 源码在此日志前调用签名的 `query_hedge_mode()`；因此本次 Demo 私有 REST 签名查询返回成功。未见账户状态或完整执行客户端 `Connected`。
- 首个失败点与结束状态：探针的 25 秒执行连接上限触发 `exec-connect timeout`，外层探针最终报 `RuntimeError`；节点日志显示停止和释放。未观察到订单、成交或持仓快照；探针没有订单命令。不能将此轮归为完整账户连接成功，也没有凭据拒绝证据。
- 仓库外原始日志：`/tmp/tracequant-400-auth-h0lvv9qk.log`，SHA-256 `f006346917f8df5f34c02df54f3807cdc96dfe14665cfd78749e4aac7f9ac7a5`。

### R400-02：限定目标合约的认证复核（C01、C05、C12）

- UTC 日志窗口：`2026-09-26T04:41:26.946643410Z`–`04:42:03.833082478Z`；命令：`timeout 110s uv run --frozen python /tmp/tracequant-400-auth-probe-scoped.py`；脚本 SHA-256 `86530e94aee18346eb7c7e80aee2f39ce4bd900efdafc76437e28325dd2ff564`。
- 相对 R400-01：`BinanceInstrumentProviderConfig(load_all=False, load_ids=["BTCUSDT-PERP.BINANCE"])`；连接上限 60 秒；仍只读、无策略或订单命令。
- 事实：签名持仓模式查询再次返回 `dual_side_position=false`，执行客户端报告 `Connected`。[固定 rc4 的连接顺序](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc4/crates/adapters/binance/src/futures/execution.rs)先建立私有 user stream，随后 `refresh_account_state()` 并等待账户注册，才从 `connect()` 返回；因此此日志支持这次 Demo 私有流及账户读取/注册已完成。该来源与人工 UI 观察分别保留，不推断 isolated、1x 或 flat。
- 首个失败点与结束状态：一次性探针的外层观察/收尾报 `RuntimeError`，未得到结构化 cache 摘要；不能把脚本进程退出视为正常收尾。日志显示执行客户端断开并释放，无订单命令；此轮没有可复核的订单/持仓计数。
- 仓库外原始日志：`/tmp/tracequant-400-auth-16gdy6x_.log`，SHA-256 `ab7886459c7080379e4aab00ee917812749d9188c1a93f837dc5915495419770`。

### R400-03：官方 ExecTester 的首次 dry-run（C05、C11、C12）

- UTC 日志窗口：`2026-09-26T04:56:38.501437129Z`–`04:57:58.586810901Z`；命令：`timeout 115s uv run --frozen python /tmp/tracequant-400-exec-dry-run.py`；脚本 SHA-256 `caf5bfcaede9f85712afe2c045a6c5a2abbcdeb938460f8225d32be9231fab67`。
- 配置与预期：同时注册官方 Demo 数据与执行客户端，目标合约过滤，执行对账开启；`ExecTesterConfig(dry_run=True)`，以目标合约首条 quote 触发一次开仓意图，但 dry-run 在订单创建/提交前跳过；关闭 limit、停止撤单和停止平仓。期望观察 tester 链路而不产生交易所订单。
- 事实与失败点：Demo 数据客户端 `Connected`；一次性探针的 55 秒停止计时在执行客户端连接完成前介入，随后出现 `exec-connect timeout` 和外层 `RuntimeError`。没有足够事实判断执行客户端本身能否在更长窗口内连接；未见账户或订单/持仓快照，未见订单命令。
- 结束状态：进程退出，无残留探针进程；Demo 账户状态仍未知。仓库外原始日志：`/tmp/tracequant-400-exec-dry-gnedtvne.log`，SHA-256 `4ca7a1661d2e9ebfdd12cfe6caab3ba815c250ba3669cceb1e6fc5c57b6d0ea7`。

### R400-04：延长窗口的官方 ExecTester dry-run（C05、C11、C12）

- UTC 日志窗口：`2026-09-26T04:58:44.012757113Z`–`05:00:31.365287978Z`；命令：`timeout 160s uv run --frozen python /tmp/tracequant-400-exec-dry-run-long.py`，外层退出码 124；脚本 SHA-256 `6441ddd52e05fcb322399011185cbb428602167f8dc7321fbf2be8ef9bb1e321`。精确进程终止 UTC 未由探针记录。
- 相对 R400-03：内部停止计时改为 100 秒、连接上限 110 秒；其余 Demo、目标合约、dry-run、无停止清场配置相同。未配置 leverage 或 margin-type 写请求，WS trading 下单入口关闭。
- 实际公开事实：数据与执行客户端均报告 `Connected`；账户 `AccountState` 被接收并注册。启动 `ExecutionMassStatus` 得到 **0 个 OrderStatusReports、0 个 FillReports、0 个 PositionReports**；reconciliation 摘要为 `reconciled=0, external=0, open=0, fills=0, positions=0, skipped=0, filtered=0`，portfolio 初始化 **0 个活动订单、0 个持仓**。这些是该次公开接口快照，不是持续 flat 保证，也不能代替 UI 核对。ExecTester 启动并订阅目标 quote/trade，随后明确记录 `Dry run, skipping open position`；没有订单提交命令。
- 首个失败点与结束状态：在 `Dry run, skipping open position` 后，探针未于外层 160 秒上限前完成正常收尾；进程被 `timeout` 终止，已核对无残留探针进程。无订单动作，不存在本探针引入的未知订单去向；进程终止后未再取得交易所快照，故账户当前状态仍需重新核对。此轮证明了认证、连接、账户读取、启动对账和 ExecTester 的 dry-run 路径，不证明真实订单或正常停止。
- 仓库外原始日志：`/tmp/tracequant-400-exec-dry-q6fnm781.log`，SHA-256 `7465fb770c2d004e68d91b7c4b6e48293ead8dfdf45b15a9c56978f8ca451fd2`。

### #400 本轮结论与安全停止点

| ID | 当前结论 | 证据和限制 |
| --- | --- | --- |
| C01 | 有条件可用 | rc4、源码提交、Demo/USD-M 配置已核对；公开 Demo HTTP ping 为 200，签名账户相关查询与私有流连接成功。代理已配置但未记录其值；没有捕获网络层目标地址。 |
| C02–C04 | 参见 #399 | #399 的有界公开行情/instrument 观察仍是历史依据；下单前必须重新取得当次约束与价格。 |
| C05 | 无法判断 | R400-04 的公开 API 快照显示 0 活动订单和 0 持仓，R400-01/02 签名查询显示 one-way；操作者尚未完成本轮 UI 的 isolated、1x、flat 和零活动订单核对，也没有可核对的逐次 typed venue 模式成功回执。 |
| C06–C10 | 未尝试 | UI 前置核对未完成，不进行真实 market、成交、reduce-only、limit 或 cancel 探查。dry-run 不证明任何真实订单能力。 |
| C11 | 未尝试 | R400-04 的官方 ExecTester 已连接、订阅并走到 dry-run 跳过开仓；实际 Strategy→订单→成交→持仓端到端能力未尝试。 |
| C12 | 有条件可用 | 实际观察到探针连接上限、收尾 RuntimeError 与外层 timeout；这属于本次探针/启动窗口限制，没有真实订单拒绝、部分成交或撤单竞态证据。 |

本轮带订单研究的停止点是缺少操作者 Demo UI 前置核对。取得该核对后，还要重新验证当时的 API 订单/持仓快照与目标 instrument 约束、有效价格，并坚持一次最多一个活动或未决订单；任一状态不明即停止，不重发或自动清场。**LIVE_NOT_APPROVED**。

本轮已确认 Demo 公开 HTTP、签名持仓模式查询、私有执行连接、账户状态读取和启动对账在记录窗口内可观察；ExecTester 的 dry-run 路径也到达预期的跳过下单点。失败/限制包括较短连接窗口、一次性探针的外层 `RuntimeError` 与超时后未正常收尾，均按原始尝试保留。没有观察到本轮产生的未知订单或持仓，因为所有探针均无订单命令；但结束后的外部账户实时状态未复查。缺少 UI 安全前置时，本研究不能给真实 market、成交、reduce-only、limit/cancel 或最小 Strategy 端到端能力发布成功结论；需由操作者核对后另起有界尝试。#400 本轮研究在此安全停止，阶段 4 的真实订单能力仍未证实，**LIVE_NOT_APPROVED**。

## #400 后续 Demo 实测（R400-05–19；2026-09-26 UTC）

以下记录接续 R400-01–04 的安全停止；前述“未尝试”只描述当时窗口，不代表后续最终状态。20x 全仓记录保留首次模式冲突，1x 逐仓复测的最终能力矩阵在后文。所有动作均限 Demo。

TraceQuant HEAD: `86653b50dcd02ba5769fd30fe3015094f4dc0ab7`；实际导入 NautilusTrader `2.0.0rc4`。仅 Binance USD-M Demo，`BTCUSDT-PERP.BINANCE`。**DEMO_ONLY，LIVE_NOT_APPROVED**。凭据仅从仓库外本地文件读取；原始日志仓库外且权限 600，已扫描未包含凭据值。

## R400-05：单次真实 market 开仓（C05–C07、C11、C12）

- 操作者在本次尝试前于 Demo UI 确认同一账户 BTCUSDT 的 one-way、isolated、1x、无持仓、零活动订单。此为操作者陈述；不是 rc4 的逐字段交易所模式回执。
- 命令：`timeout 310s uv run --frozen python /tmp/tracequant-400-market-once.py --ui-verified-submit-one-market`；脚本 SHA-256 `3a683604f6c8d4b45bca95be362287431ae164317804b6b8d92786b32a7fb37b`。
- 启动策略时 `account_present=true`、缓存中 `open_orders=0`、`open_positions=0`。下单前收到目标 quote：bid 83858.80、ask 83860.50 USDT，事件年龄 192 ms；当次 instrument `min_notional=50 USDT`、`min_quantity=0.0001 BTC`、`size_increment=0.0001 BTC`。计算并固定数量 0.0007 BTC，按 ask 计名义金额 58.702350 USDT；脚本硬上限 0.001 BTC/100 USDT。
- `2026-09-26T05:37:31.589946Z` 提交意图；`client_order_id=O-20260926-053731-M001-000-1`，单笔 BUY MARKET IOC，`reduce_only=false`。随后公开事件依次为 `OrderInitialized`、`OrderSubmitted`、`OrderAccepted`（venue order ID `28603563072`）、`OrderFilled`（0.0007 BTC），`PositionOpened`（LONG 0.0007 BTC）。成交价 83,860.50 USDT，手续费 0.02348094 USDT。
- 订单后约 45 秒，探针在构造最终 cache 摘要时抛出 `RuntimeError`，进程退出码 1；节点停机日志提示残留 LONG 0.0007 BTC。脚本不重试、不自动平仓。停机后的实时活动订单与净持仓未得到完整快照，要求操作者复查 UI。
- 原始日志 `/tmp/tracequant-400-market-gioy76ae.log`；SHA-256 `388c59bde61eb48142ca6559aa4aa5ee36351ba658ac950c11834658b9a2fdb1`。

## R400-06：只读私有账户复查与模式冲突（C05、C07、C12）

- 命令：`timeout 190s uv run --frozen python /tmp/tracequant-400-position-recheck.py`；脚本 SHA-256 `3f3fa972ff8ad695eb626199638ee89081b845d318fcc7c86eb314a862de5171`。仅注册官方执行客户端并开启 reconciliation；没有订单命令。
- `2026-09-26T05:44:50Z` 收到 Demo 账户 `AccountState`，其中 `MarginBalance.initial=2.93597150 USDT`、`maintenance=0.23487772 USDT`；执行客户端随后报告 `Connected`。脚本在启动时机/收尾处遇到 `RuntimeError` 和 `TimeoutError`，未取得结构化订单或持仓摘要。
- 该初始保证金约为开仓名义金额的 5%，**提示可能约 20x，不能据此单独断定**。Binance 官方账户字段把 initial margin 定义为保证金要求，持仓记录另有显式 leverage、isolated 字段；rc4 本次公开 `AccountState` 日志没有保留该持仓逐字段值。此事实与操作者下单前 UI 所报 1x 有潜在冲突，故立即停止进一步发单并请求操作者查看当前 BTCUSDT 持仓行。
- 原始日志 `/tmp/tracequant-400-position-recheck-inpsgc59.log`；SHA-256 `838b3c63edae2bf3f46b423f33fce9cc3766b614ff159015e557325ea22e10ad`。

## R400-07：操作者实时 Demo UI 复核与安全停止（C05、C08–C10、C12）

- 约 2026-09-26T05:51Z，操作者报告同一 Demo 账户当前 BTCUSDT 持仓为 **全仓、20x、0.0007 BTC 多头、零活动委托**。该值与下单前操作者所报逐仓、1x 不符；后者不能作为已实现的交易所状态。API 账户初始保证金约为成交名义金额 5% 的线索，与当前 UI 20x 一致，但保证金比率本身并非逐字段模式回执。
- 固定 rc4 官方执行配置文档说明 futures_leverages=None、futures_margin_types=None 是默认值；本次一次性探针未配置这两个写入选项，也未对交易所设置杠杆/保证金模式。它只依赖操作者事前 UI 核对。未能证明 UI 事前看到的 1x/逐仓为何没有成为成交持仓模式；可能涉及 UI 核对范围或交易所既有 BTCUSDT 模式，具体原因未知。不能伪称 Nautilus 已收到逐次模式设置成功回执。参考：https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc4/docs/integrations/binance.md
- 在这一时间点，按 #400 及阶段 4 基线的“观察到相反事实时停止下单”暂停所有 API 订单动作；没有自动清场或盲目重试。随后维护者明确指示保留现有 20x 全仓 Demo 环境继续验证能力；这一指示改变后续探查条件，但不改写先前的模式冲突，也不证明原定 1x 逐仓场景。

## R400-08：当前环境 exact reduce-only 平仓（C07、C08、C11）

- 维护者在 R400-07 后明确指示保留当前 20x 全仓 Demo 环境继续能力验证。此前操作者 UI 复核显示唯一 0.0007 BTC 多头、零活动委托；脚本又在策略启动时从官方 cache 取得账户存在、零活动订单、一个活动持仓，并在提交前核对目标 instrument、LONG 方向与数量恰为 0.0007 BTC。
- 命令：timeout 310s uv run --frozen python /tmp/tracequant-400-reduce-once.py --ui-reconfirmed-reduce-only-exact；脚本 SHA-256 4c935604e32857fc179497024ec43c475edf21237896a0d0b82b7a6818458c7b。
- 2026-09-26T05:58:22.814746Z，新鲜目标 quote 为 bid 83882.20、ask 83886.70 USDT，事件年龄 227 ms；按已确认净持仓量提交单笔 SELL MARKET IOC，数量 0.0007 BTC、reduce_only=true、position_id=BTCUSDT-PERP.BINANCE-MarketOnce-000。client_order_id=O-20260926-055822-M001-000-1。
- 公开事件依次 OrderSubmitted、OrderAccepted（venue order ID 28603577277）、OrderFilled(0.0007 BTC，83,882.20 USDT，手续费 0.02348701 USDT)、PositionClosed(0.0000 BTC)。2026-09-26T05:59:08Z 策略停机 cache 快照显示零活动订单、零持仓，进程退出码 0。根据两次成交价格与手续费计算，本次往返毛收益 0.015190 USDT、费用 0.04696795 USDT、净变化 -0.03177795 USDT；随后只读账户总额与该变化一致。这里是记账交叉核对，不评价策略收益。
- 原始日志 /tmp/tracequant-400-reduce-perlzxuy.log，SHA-256 1623e7bc2473c67839b6d1dea8a2c6f65cc5cc5e6a475cdb6ed1b382820d0020，权限 600，已扫描不含凭据值。

## R400-09：limit 探针首次连接失败（C09、C10、C12）

- 操作者在 R400-08 后于 Demo UI 确认 flat、零活动委托。首次执行一次性 limit/cancel 探针时，2026-09-26T06:02:01Z 数据客户端报告 failed to request Binance Futures instruments；执行客户端约 06:02:44Z 报 Connected，账户状态已读取且无保证金占用；最终连接等待 150 秒超时。策略未启动，order_id=null，进程 status=NO_ORDER_ATTEMPT；没有提交订单或撤单。
- 命令：timeout 340s uv run --frozen python /tmp/tracequant-400-limit-cancel.py --ui-flat-zero-confirmed；实际脚本 SHA-256 d594eddccef6ac50f85362bab75622a569d85f4d02de6864cea8cea54ae9d446。原始日志 /tmp/tracequant-400-limit-cancel-bdd9q4fl.log，SHA-256 da2014decca1cba4cbc34bbeab610e4372e51b86f99435f1081bd4a0812b42fa。连接失败没有更细的底层错误回执；不能断言为特定 HTTP 错误。因确认无订单动作，后续另起第二次尝试，不覆盖本失败记录。

## R400-10：被动 post-only limit 接受与定向 cancel（C09、C10、C11）

- 第二次运行同一脚本、同一固定 Demo/USD-M/BTCUSDT、同一脚本哈希。2026-09-26T06:06:54Z 策略启动，官方 cache 有账户、零活动订单、零持仓。
- 2026-09-26T06:06:55.587754Z 收到目标 quote：bid 83858.20、ask 83892.70 USDT，事件年龄 220 ms；构造 BUY LIMIT GTC、post_only=true，价格 83358.20 USDT（比提交时买一低 500 USDT），数量 0.0007 BTC，限价名义金额 58.350740 USDT。client_order_id=O-20260926-060655-L001-000-1。交易所约 06:06:55.694Z 返回 OrderAccepted，venue order ID 28603583182。
- 接受后先读取公开事件；未见成交或持仓变化。手动触发一次性撤单信号前，策略再次确认缓存仅有该单一个活动订单、零持仓。2026-09-26T06:07:39.200352Z 发送对该 client_order_id 的 cancel，随后收到 OrderPendingCancel、OrderCanceled（约 06:07:39.326Z）。未见 OrderFilled 或成交/撤单竞态。本轮没有尝试重发或自动清场。
- 2026-09-26T06:07:49Z 策略停机 cache 快照为零活动订单、零持仓，进程退出码 0。约 2026-09-26T06:11Z，操作者再次在同一 Demo UI 确认无持仓、零活动委托；本次最终 cache 与 UI 一致。原始日志 /tmp/tracequant-400-limit-cancel-wq679pp5.log，SHA-256 4907cf713b8c3af4edee73d47c5119f89739e0cceb7b5b58b2ae2d85b7818d48；权限 600，已扫描不含凭据值。

## 20x 全仓阶段性能力边界

| ID | 结论 | 实际证据与适用边界 |
| --- | --- | --- |
| C05 | 受限/安全前置冲突 | 下单前操作者报 1x 逐仓，成交后 UI 实际为 20x 全仓；后续维护者明确要求在此当前模式继续研究。rc4 signed query 显示 one-way；无逐次 typed 杠杆/保证金模式回执。原定 1x 逐仓场景未验证。 |
| C06 | 有条件可用 | R400-05 单笔极小 market BUY 获接受和全额成交；实际 20x 全仓。 |
| C07 | 有条件可用 | 开/平仓 fill、费用和持仓事件可关联；往返费用与账户变化一致。 |
| C08 | 有条件可用 | R400-08 对已确认 0.0007 BTC 多头提交 exact reduce_only SELL，交易所接受并全额成交，PositionClosed；最终 cache flat。 |
| C09 | 有条件可用 | R400-10 被动 post_only LIMIT 获接受；约 44 秒观察窗口未成交。 |
| C10 | 有条件可用 | R400-10 定向 cancel 返回 OrderCanceled，最终 cache 与操作者 UI 均为零活动订单、零持仓；未触发成交/撤单竞态。 |
| C11 | 有条件可用 | 一次性最小 Strategy 的 quote→订单→公开事件→持仓路径在真实 Demo 成立；R400-05 的探针最终快照异常、R400-09 连接失败按原样保留。 |
| C12 | 已观察限制 | R400-05 收尾 RuntimeError、R400-06 读回收尾异常、R400-09 数据端合约请求失败/连接等待超时；无拒单、部分成交或撤单竞态实际证据。 |

**DEMO_ONLY，LIVE_NOT_APPROVED。**本次成功只证明当前 20x 全仓、固定 rc4、单账户/单标的、有限观察窗口的行为；不外推到 1x 逐仓、Live 或长期稳定性。以下为 R400-05–10 的阶段性观察；后续 1x 逐仓复测更新最终能力判断。


## R400-11：当前 BTCUSDT 杠杆和保证金模式只读复核

- 2026-09-26T06:19:47Z，用户称已更改杠杆和保证金模式。使用仓库外权限 600 的一次性脚本，对固定 `https://demo-fapi.binance.com` 发起仅一次签名 `GET /fapi/v1/symbolConfig?symbol=BTCUSDT`；脚本 SHA-256 `1fd272613961f93658db0ac1b2dd789a22d4285de2a59716e975dc887e7196de`。响应 HTTP 200；只输出选定字段，无原始响应、签名、密钥或其他账户字段。
- 交易所返回 `symbol=BTCUSDT`、`marginType=ISOLATED`、`leverage=1`。这是 **当前时点的 Demo 交易对配置**，确认用户刚完成的设置在账户 API 中生效；不改变 R400-05 成交时由操作者 UI 报告的 20x 全仓历史条件，也不证明新的 1x 逐仓条件下已完成下单。
- 此核对是为响应用户的模式验证请求而执行的直接交易所只读查询，不属于固定 rc4 官方执行客户端的公开模式回读能力；没有任何订单、撤单或模式写操作。


## R400-12：1x 逐仓后的 rc4 只读账户前置快照

- 2026-09-26T06:27:32Z–06:28:29Z，`timeout 235s uv run --frozen python /tmp/tracequant-400-flat-preflight.py`，脚本 SHA-256 `cd8116f29e25366cdd6a0638f5bf169fcca83e900d78aa79b732d7323d4c8367`；固定 rc4 Demo/USD-M/BTCUSDT，仅注册官方 execution client 和不发订单的最小 Strategy。
- 06:28:25Z `on_start` 观察到账户存在、`open_orders=[]`、`open_positions=[]`；06:28:29Z `on_stop` 再次为零订单、零持仓。进程退出码 0。此为两次公开 cache 快照，不是长期状态保证；后续下单仍需操作者当时在 Demo UI 核对五项前置条件。
- 原始日志 `/tmp/tracequant-400-flat-preflight-ghpvc6oj.log`，权限 600，SHA-256 `59924c983c922c8805fa3c8a8a76e3ffd63571fbc3c45d2c946110c17bdefb49`，凭据扫描为阴性。没有下单或模式写操作。


## R400-13：1x 逐仓单次 market 开仓

- 操作者于本轮下单前在 Demo UI 确认 one-way、isolated、1x、flat、零活动订单；此前 06:19:47Z 签名只读 `symbolConfig` 返回 `ISOLATED/1`，06:28:25Z–06:28:29Z rc4 只读账户快照显示零订单、零持仓。
- 命令：`timeout 310s uv run --frozen python /tmp/tracequant-400-market-1x.py --ui-verified-submit-one-market`；脚本 SHA-256 `af7c2e95a37dc24975e1a85e330e1781ff80581ac9df4e538d6bc61097253d46`。与 R400-05 的一次性官方最小 Strategy 相比，仅把 cache 停机快照移入 Strategy `on_stop`，并避免从外层异步轮询读取 cache；保留单次下单与数量/名义金额上限，不配置模式写操作。
- 06:35:04.955Z 策略启动：账户存在、零活动订单、零持仓。06:35:05.157Z 新鲜 quote：bid 83,875.30、ask 83,888.60 USDT，事件年龄 267 ms；当次 instrument `min_notional=50 USDT`，计算 BUY MARKET IOC 0.0007 BTC，按 ask 计 58.722020 USDT，硬上限 0.001 BTC/100 USDT。client order ID `O-20260926-063505-M001-000-1`。
- 公开事件依次 `OrderInitialized`、`OrderSubmitted`、`OrderAccepted`（venue order ID `28603601264`）、`OrderFilled`（0.0007 BTC，83,905.40 USDT，手续费 0.02349351 USDT，TAKER）、`PositionOpened`（LONG 0.0007 BTC）。成交价高于提交前 ask 16.80 USDT，属于此次观察到的市价执行价差，不据此推断长期滑点。
- 成交后的 06:35:37Z 直接交易所签名只读 `symbolConfig` 再次返回 BTCUSDT `ISOLATED/1`，HTTP 200。该直接查询仅作为交易所配置交叉核对，不计作 rc4 公开模式回执。06:35:50Z 策略停机 cache 快照为零活动订单、唯一 BTCUSDT 多头 0.0007 BTC；脚本退出码 0。此时存在已知敞口，等待操作者 Demo UI 核对后再做 exact reduce-only，不自动平仓或重试。
- 原始日志 `/tmp/tracequant-400-market-v69jrcpm.log`，权限 600，SHA-256 `3b605529d037870bfb9d536d399f59066caddae7061bbeeda7c9e86f343cf9bb`，凭据扫描为阴性。


## R400-14：开仓后的只读重连对账遗漏活仓

- 已知 R400-13 停机 cache 为 0.0007 BTC 多头、零活动订单。随后只读重连探针 `/tmp/tracequant-400-flat-preflight.py` 在 06:39:42Z 收到 `0 OrderStatusReports`、`3 FillReports`、`1 PositionReports`，账户 `initialMargin≈58.71 USDT`；rc4 对账器报告 `Bounded reconciliation report set is incomplete; projecting 3 historical order(s) without position or portfolio effects`，摘要为 `positions=0`，策略 cache 则显示零持仓、零活动订单。脚本退出码 0；这只是 cache 投影缺失，不能视为交易所已平仓。日志 `/tmp/tracequant-400-flat-preflight-n6j431i4.log`，权限 600、SHA-256 `cb71bdb866c981f930a1cb36d252c8891f775d876fa132d000b95b452512148c`，凭据扫描阴性。
- 为排除仅因未认领外部订单导致投影缺失，使用同一 TraderId、开仓 StrategyId、官方 `external_order_claims=[BTCUSDT-PERP.BINANCE]` 的只读脚本再查；脚本 `/tmp/tracequant-400-claimed-preflight.py` SHA-256 `5488bf43352af21d786d603ed9546b59bd83a5c648f9127eb42da06b15a5bfc4`。06:42:27Z 同样收到 `0 OrderStatusReports`、`3 FillReports`、`1 PositionReports`，同一不完整报告错误，cache 仍显示零持仓；账户初始保证金约 58.71 USDT。没有订单命令。日志 `/tmp/tracequant-400-claimed-preflight-s9lmtyb2.log`，权限 600、SHA-256 `0acfcd99d69d7313d9eb0ebbeb5283c8aa7ad4d2f7f7af6d97298bfdf5fc072b`，凭据扫描阴性。
- 这是固定 rc4 在本账户既有 3 笔历史成交且零 OrderStatusReports 的重连场景中可复现的对账/缓存限制。交易所报告有持仓，策略 cache 没有；此时停止后续下单，等待操作者 UI 明确确认实际净仓与活动委托。不可用空 cache 推断 flat。


## R400-15：1x 逐仓 exact reduce-only 平仓与后续只读复核

- 在 R400-13 的 0.0007 BTC 多头及零活动订单停机快照后，操作者在同一 Demo UI 确认当前仍为 0.0007 BTC 多头、零活动委托。06:53:01Z 交易所直接只读 `symbolConfig` 再次返回 `BTCUSDT/ISOLATED/1`，HTTP 200。
- 命令：`timeout 310s uv run --frozen python /tmp/tracequant-400-reduce-1x-known-venue.py --ui-reconfirmed-reduce-only-exact`；脚本 SHA-256 `610ac787e308c1bcfff63782a4b7fb03032539358c3903b98344df2ebdf0b412`。该脚本在先前只读缓存遗漏活仓后允许已获操作者确认的精确 reduce-only；实际本次启动 cache 已认领到唯一 0.0007 BTC 多头，按原有最严格分支下单，未使用空 cache 分支。
- 06:54:59Z 策略启动有账户、零活动订单、一个活动持仓。06:55:00Z 新鲜 quote bid 83,879.50、ask 83,879.60 USDT，年龄 309 ms；按缓存 LONG 0.0007 BTC 以及已确认净仓，提交唯一 SELL MARKET IOC，`reduce_only=true`、`position_id=BTCUSDT-PERP.BINANCE-MarketOnce-000`。client order ID `O-20260926-065500-M001-000-1`。
- 公开事件依次为 `OrderInitialized`、`OrderSubmitted`、`OrderAccepted`（venue order ID `28603615199`）、`OrderFilled`（0.0007 BTC，83,862.40 USDT，手续费 0.02348147 USDT）、`PositionClosed`（0.0000 BTC）。06:55:45Z 停机 cache 零活动订单、零持仓，脚本退出码 0。原始日志 `/tmp/tracequant-400-reduce-26y7j1g8.log`，权限 600，SHA-256 `1d1b0bd3d16d0d8439803bd3b42906697f0d2ff19eb65d4c6842ff460d77fbda`，凭据扫描阴性。
- 平仓后用官方 rc4 只读执行客户端重连：06:57:19Z–06:57:22Z 启动和停机 cache 均为零订单、零持仓；交易所质量状态报告数量为 `0 OrderStatusReports`、`0 FillReports`、`0 PositionReports`。脚本 `/tmp/tracequant-400-flat-preflight.py`，原始日志 `/tmp/tracequant-400-flat-preflight-ettpdq8p.log`，权限 600，SHA-256 `a0b1f6d220a2617ca0be6d400d90ab16c7f9fa4a1b5c3995bc431186435432c7`，凭据扫描阴性。仍待操作者 UI 核对 flat、零委托后继续下一笔。


## R400-16：1x 逐仓被动 limit 与定向 cancel

- R400-15 平仓后，操作者在同一 Demo UI 确认无持仓、零活动委托；07:02:47Z 交易所直接签名只读 `symbolConfig` 返回 BTCUSDT `ISOLATED/1`。
- 命令：`timeout 340s uv run --frozen python /tmp/tracequant-400-limit-cancel-1x.py --ui-flat-zero-confirmed`；脚本 SHA-256 `5cf731d364aa7a6557be5817f0cc8cb51066d745c8c03a72b7e8cd5efb827b4f`。与 R400-10 同一单笔限价及手动信号定向撤单逻辑，只更新节点名与操作者所报模式字段，未设置 leverage/margin-type 写请求。
- 07:04:47Z Strategy 启动时账户存在、零活动订单、零持仓。07:04:48Z quote bid 83,915.90、ask 83,919.50 USDT，事件年龄 276 ms；提交 BUY LIMIT GTC、`post_only=true`，数量 0.0007 BTC，价格 83,415.90 USDT（低于买一 500 USDT），名义金额 58.391130 USDT。client order ID `O-20260926-070448-L001-000-1`；交易所返回 `OrderAccepted`，venue order ID `28603622176`。
- 接受后未见成交或持仓事件。约 32 秒后，在公开 cache 再次确认唯一活动订单是该 client order ID 且零持仓，手动发送一次性本地撤单信号。07:05:20Z 依次收到 `OrderPendingCancel`、`OrderCanceled`；无 `OrderFilled` 或成交/撤单竞态。07:05:30Z 策略停机 cache 零活动订单、零持仓，进程退出码 0。原始日志 `/tmp/tracequant-400-limit-cancel-4i5g7iq4.log`，权限 600，SHA-256 `3824cd001fdc5ba1257a6b5e0ba0f398f70fa842c26e5a8cddfb29b235f148b6`，凭据扫描阴性。
- 最后一次官方 rc4 只读重连在 07:07:00Z–07:07:03Z 启动和停机 cache 均为零订单、零持仓；交易所返回 `0 OrderStatusReports`、`0 FillReports`、`0 PositionReports`。脚本 `/tmp/tracequant-400-flat-preflight.py`，日志 `/tmp/tracequant-400-flat-preflight-yb7fvmmk.log`，权限 600，SHA-256 `2222152a5842110ff6fb8669f39a81c35e4b5ef2043753839c813f92f5291601`，凭据扫描阴性。操作者亦在同一 Demo UI 最后确认无持仓、零活动委托。

## R400-05–16 能力更新（截至 07:07 UTC；取代 20x 全仓阶段性矩阵）

| ID | 结论 | 1x 逐仓条件下的事实与限制 |
| --- | --- | --- |
| C05 | 有条件可用 | 操作者在下单前确认 one-way、isolated、1x、flat、零委托；直接只读交易所 `symbolConfig` 在开仓前、开仓后、reduce-only 前、limit 前分别返回 BTCUSDT `ISOLATED/1`。rc4 自身没有本次公开的 typed venue 模式设置成功回执；直接查询不算 rc4 模式能力。 |
| C06 | 有条件可用 | R400-13 的唯一极小 BUY MARKET 0.0007 BTC 获接受并全额成交；无拒绝或部分成交样本。 |
| C07 | 有条件可用 | R400-13、R400-15 的订单、成交、费用、持仓打开与关闭可关联；最终交易所报告和 UI 均为 flat。原始测试有行情报价与实际成交价差，不能外推滑点。 |
| C08 | 有条件可用 | R400-15 在 UI 与策略 cache 明确 0.0007 BTC LONG/零订单后，以 `reduce_only=true` 卖出精确 0.0007 BTC 获接受、全额成交、PositionClosed。 |
| C09 | 有条件可用 | R400-16 被动 `post_only=true` LIMIT 获接受并约 32 秒未成交；无长期挂单稳定性证据。 |
| C10 | 有条件可用 | R400-16 对唯一已知 client order ID 发一次定向 cancel，返回 OrderCanceled，最终交易所报告、cache、操作者 UI 均为零订单/零持仓；没有实际成交/撤单竞态样本。 |
| C11 | 有条件可用 | 最小 Strategy 实际贯通新鲜 quote、数量计算、market/limit 提交、交易所订单事件、成交、持仓关闭与定向撤单。 |
| C12 | 已观察限制 | R400-14 在 `0 OrderStatusReports / 3 FillReports / 1 PositionReports` 的只读重连里，rc4 报 bounded reconciliation 不完整并投影零持仓；不能以新进程空 cache 推断交易所 flat。R400-05 的早期外层快照异常、R400-09 的数据客户端合约请求失败仍保留；本轮没有拒单、部分成交或撤单竞态样本。 |

**DEMO_ONLY、LIVE_NOT_APPROVED。**所有成功结论仅适用于固定 rc4、单 Demo 账户、BTCUSDT、记录窗口及极小订单。交易所模式直接只读核对与 rc4 官方接口观察分开归因。R400-17–19 的官方 ExecTester 结果另列于后，不能归入最小 Strategy 的 limit/cancel 能力。


## R400-17–19：官方 ExecTester 补充实测

时间：2026-09-26 07:19–07:31 UTC。固定 `nautilus_trader==2.0.0rc4`，TraceQuant 分支 `research/400-rc4-binance-demo` 提交 `86653b50dcd02ba5769fd30fe3015094f4dc0ab7`。仅 Binance USD-M Futures **Demo**，单账户、`BTCUSDT-PERP.BINANCE`。操作者在真实下单前于同一 Demo UI 确认 one-way、isolated、1x、flat、零活动委托；07:22:14Z 和 07:26:53Z 的交易所直接只读 `symbolConfig` 均返回 BTCUSDT `ISOLATED/1`。凭据只由仓库外权限 600 文件读取，未回显。**DEMO_ONLY、LIVE_NOT_APPROVED。**

rc4 内置 `nautilus_trader.testkit.ExecTesterConfig`，可通过 `LiveNode.add_builtin_strategy("ExecTester", config)` 使用，见[固定 rc4 的执行测试规范](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc4/docs/developer_guide/spec_exec_testing.md)。本轮刻意限定为一个 0.0007 BTC market 开仓、一次按已知持仓 reduce-only 停机平仓；关闭双侧 limit、stop/bracket 和停机自动撤单，保留正常风险引擎。固定 rc4 [测试器源码](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc4/crates/testkit/src/testers/exec/strategy.rs) 显示 dry-run 在创建订单前跳过开仓，`on_stop` 按配置调用 `close_positions_on_stop`。这些是源码行为；下表只把实测事件计为运行证据。

| 尝试 | 实际动作与首个失败点 | 订单、持仓和结束状态 |
| --- | --- | --- |
| R400-17：官方 dry-run | 07:19:44Z 启动对账返回 0 OrderStatusReports、0 FillReports、0 PositionReports；官方测试器订阅目标 quote 后记录 `Dry run, skipping open position`，节点正常停机。 | 无 `OrderSubmitted` 或 `OrderFilled`；证明官方 dry-run 链路可达，不证明真实订单。 |
| R400-18：首次真实尝试 | 配置唯一 0.0007 BTC BUY MARKET IOC、成交后停机以 reduce-only 关闭。数据客户端约 66 秒后连接；总连接上限 110 秒触发 `exec-connect timeout`，策略未启动。 | 无任何订单初始化、提交、接受或成交事件。后续官方只读重连于 07:26:09Z–07:26:12Z 返回 0 订单报告、0 持仓报告；此轮是连接窗口限制，无订单动作。 |
| R400-19：延长连接窗口后有因重试 | 仅将总连接上限延至 200 秒、探针观察窗口延至 270 秒，保留同一单笔订单配置。07:29:06Z 启动对账返回 0 订单报告、0 成交报告、0 持仓报告；`ExecTester` 自己提交 BUY MARKET IOC 0.0007 BTC，`OrderAccepted` venue ID `28603640804`，`OrderFilled` 0.0007 BTC，价 83,972.50 USDT，手续费 0.02351230 USDT，随后 `PositionOpened` LONG 0.0007 BTC。 | 开仓能力由交易所回执和持仓事件证实。探针在完整买入成交后约 8 秒触发测试器停机；见下方关闭单限制。进程正常退出并不等于关闭单完成。 |

R400-19 停机时，官方测试器在 07:29:14.995Z 识别到唯一 0.0007 BTC 活动持仓，创建 SELL MARKET 0.0007 BTC、`reduce_only=true` 的关闭单，client order ID `O-20260926-072914-001-RETRY-2`。日志显示执行客户端 07:29:16.103Z `Disconnected`，而关闭单的本地 `OrderSubmitted` 事件直到 07:29:16.151Z 才出现。探针结束前**没有**这笔关闭单的 `OrderAccepted`、`OrderFilled`、`OrderRejected` 或 `PositionClosed`。这段顺序提示停机与命令处理存在竞态，是基于日志的推断；不能据此断定关闭单已被交易所接受或成交。

随后不再下单。07:31:06Z–07:31:10Z 的官方 Nautilus 只读重连返回 0 OrderStatusReports、0 FillReports、0 PositionReports，账户保证金列表为空，缓存零订单、零持仓；操作者在同一 Demo UI 最终确认无持仓、零活动委托。**最终账户 flat 已证实，但本轮公开事件不能把 flat 唯一归因于测试器那笔停机关闭单，也不能给该单补造终态回执。**未运行官方默认的双侧 limit smoke 或完整测试矩阵；它们会超出 #400 的一次最多一个活动/未决订单约束。先前最小 Strategy 的 limit/cancel 成功不能算作 `ExecTester` 自身的结果。

结论：固定 rc4 的官方 `ExecTester` dry-run **可用**；在本 Demo、BTCUSDT、1x 逐仓、0.0007 BTC 的记录窗口内，官方测试器的真实 market 开仓 **有条件可用**；官方测试器的停机 reduce-only 关闭单**已创建并本地提交，交易所终态无法判断**。首次真实尝试的连接超时和有因重试的停机回执缺口均按原样保留。后续使用它做自动开平仓 smoke 时，不能把节点正常退出或空缓存当作关闭单成功的证明。

证据：

- R400-17 脚本 `/tmp/tracequant-400-exec-dry-retry.py` SHA-256 `15a9e04dcb6bdd1711dd86be86914a0204bd5628713a90d9106204d12cde92dd`；日志 `/tmp/tracequant-400-exec-dry-retry-9ze_5q4m.log` SHA-256 `b3f80a0199e001a93357860f61cd641dc3b034673e7929b277b22877a04d280e`。
- R400-18 脚本 `/tmp/tracequant-400-exec-real-once.py` SHA-256 `1d7b59498f7b89021b24360dc07885a4dd362bf9e3e5c12ca40e449992e046c2`；日志 `/tmp/tracequant-400-exec-real-64bz0ee7.log` SHA-256 `80e69542302c00b361c33d1b3a570666987d80a9f49cfff4dd34ba2102028fd2`；随后只读日志 `/tmp/tracequant-400-flat-preflight-1ee83mws.log` SHA-256 `56773f54346789959068b23c70baffa727f53923c77770d841da8bf010b1b087`。
- R400-19 脚本 `/tmp/tracequant-400-exec-real-retry.py` SHA-256 `ce95a017ce1eb10620311500fdd57c5b47ba3dee9a65b456a2be1abcd7f9fefd`；日志 `/tmp/tracequant-400-exec-real-retry-bz0lii21.log` SHA-256 `14ea1c3639477cbd05c04b1550fe56d8ae37d4ac29cf9df6f2ea9f65774896b0`；最终只读日志 `/tmp/tracequant-400-flat-preflight-7fdj5cor.log` SHA-256 `e37ddfd0fff2bff3d504542c6696bc7bf5e458e8691c663f28472f035c1c3864`。

所有原始日志均位于仓库外、权限 600，扫描未发现 API key/secret 值；`/tmp` 不保证长期保存。这些临时证据若失去原始文件，不能仅凭哈希重建完整日志；本报告保留可复核的关键事实和结论边界。
