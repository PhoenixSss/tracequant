# Binance USDⓈ-M Feature #11 REST recent/gap 有限窗口补证

Research Outcome: IMPLEMENT

> 本次正式 observation 通过维护者当前配置的 HTTPS 代理、且禁止直连回退，取得合法
> Binance USDⓈ-M `serverTime`，并实际执行 BTCUSDT/ETHUSDT 的 contract、mark、index
> 1m Kline 与 settled funding 共 8 个 recent 和 8 个 gap 请求。全部 16 个核心请求均为
> HTTP 200、非空且通过有界 schema/窗口校验；因此 #282 与 #283 所需实际来源证据已足够，
> 后续实现建议确定为 **IMPLEMENT**。

本报告与 [probe manifest](./binance-usdm-feature11-rest-window-follow-up-probes.json)
共享 observation set ID
`issue-295-probe-run-2026-09-09T18:33:30.868474Z`。manifest 保存每次请求的精确 URL、
规范参数、UTC observation、HTTP 状态、允许的响应 headers、实际 bytes、response SHA-256、
bounded first/last sample、行数、首尾时间、校验结果和逐次预算。完整响应只保留在 Git 忽略的
本地证据目录；版本化产物不包含代理 URL、凭据或完整响应库。

## 1. 冻结请求与预算结果

请求清单在首个 HTTP 请求前于 `2026-09-09T18:33:30.868474Z` 冻结：1 个
`serverTime`、8 个 recent、最多 8 个派生 gap，另留 17 个合规重试槽，总请求上限 34。
观测器显式读取 `HTTPS_PROXY`，禁用 curl 用户配置与 `no_proxy` 绕过，禁止直连 fallback，
不跟随 redirect。非 serverTime 请求只有 transport/timeout/5xx 才可重试；429 和其他 4xx
停止对应路径。

实际从 `2026-09-09T18:33:30.868907Z` 执行到
`2026-09-09T18:33:40.919630Z`，monotonic elapsed 为 10.050724 秒：

- 17 次 HTTP 请求，17 次 HTTP 200，0 次重试，0 次 redirect；
- 累计响应 53,577 bytes，最大单响应 7,935 bytes；
- 未触及 34 次、32 MiB、单响应 16 MiB、5 分钟或单次 10 秒上限；
- 没有 transport failure、HTTP error、legal empty、unsupported 或未执行项。

首次 `GET https://fapi.binance.com/fapi/v1/time` 即返回唯一合法 int64
`serverTime=1788978812658`（`2026-09-09T18:33:32.658Z`），response bytes 为 28，
SHA-256 为 `851a5d31443821735ad0bec300b011143e432f1ffd24d22c3d08fb4e73d2420d`。
该响应冻结为 T0；M 为 `1788978780000`（`2026-09-09T18:33:00.000Z`）。未使用
HTTP Date、本机时间、Kline 时间或旧 `serverTime` 替代 T0。

## 2. 十六个核心请求

Kline recent 固定为 `[1788975180000,1788978780000)`，向 endpoint 发送
`startTime=1788975180000,endTime=1788978779999,limit=60,interval=1m`；gap 固定为
`[1788975180000,1788975480000)`，发送
`startTime=1788975180000,endTime=1788975479999,limit=5,interval=1m`。Contract/mark
使用 `symbol`，index 使用独立 `pair`。

| 数据族 | subject 参数 | recent 结果（rows；首尾 UTC；SHA-256） | gap 结果（rows；首尾 UTC；SHA-256） |
|---|---|---|---|
| contract kline | `symbol=BTCUSDT` | supported（60；17:33→18:32；`a253a934a1ba…`） | supported（5；17:33→17:37；`1087f26b529e…`） |
| contract kline | `symbol=ETHUSDT` | supported（60；17:33→18:32；`82d2a49a6751…`） | supported（5；17:33→17:37；`2f73448e36f9…`） |
| mark-price kline | `symbol=BTCUSDT` | supported（60；17:33→18:32；`32c273674980…`） | supported（5；17:33→17:37；`f82e8808b2d7…`） |
| mark-price kline | `symbol=ETHUSDT` | supported（60；17:33→18:32；`c7163b4f125e…`） | supported（5；17:33→17:37；`24de765be36e…`） |
| index-price kline | `pair=BTCUSDT` | supported（60；17:33→18:32；`e66727e9d38c…`） | supported（5；17:33→17:37；`8e28c4123a10…`） |
| index-price kline | `pair=ETHUSDT` | supported（60；17:33→18:32；`e87aad187eae…`） | supported（5；17:33→17:37；`c8703db8d702…`） |

表内 Kline 时间均为 `2026-09-09` UTC。每个响应的全部行均为 12 元素数组；open time
分钟对齐、close time 为 `open+59999ms`，严格升序，无重复、缺分钟或越界。Contract 的
OHLC、volume、quote volume、trade count 与 taker volume 字段均为实际数值。Mark/index
的 `[0..4]` 为对应价格 OHLC；本轮所有行的 `[5]`、`[7]`、`[9..11]` 均为字符串 `"0"`，
`[8]` 均为整数 `60`，继续按 placeholder/ignore 处理，不能发布为成交量或成交笔数。

Funding recent 固定为 `[1788374012658,1788978812658)`，发送
`startTime=1788374012658,endTime=1788978812657,limit=1000`。BTCUSDT 与 ETHUSDT 各返回
21 条严格升序 settled record，实际首尾为 `2026-09-03T00:00:00.000Z` 至
`2026-09-09T16:00:00.002Z`。从各自第一条 `fundingTime=1788393600000` 派生的 gap 为
`[1788393600000,1788397200000)`，发送 `endTime=1788397199999,limit=100`，各返回 1 条：

| subject | recent SHA-256 | gap SHA-256 | schema 结果 |
|---|---|---|---|
| `symbol=BTCUSDT` | `2bd472efeb5b2d6336fda340914d77770151589cb3a14ff419e1ed149ef41f71` | `39ac0b55d6acae3de0e0be633edbfabdc37a227fa913027fe2e63c547abda401` | supported |
| `symbol=ETHUSDT` | `368852dd6686fce1fac1d940f7153dc5099acc8125c7ae8bac4d4b0ece24cace` | `b862855ea7296ca550dc3aac55c34e2e907cb993aa5749efaedd4409b4cf1ac1` | supported |

每条 funding object 的 `symbol` 与请求一致，`fundingTime` 为窗口内 int64，
`fundingRate` 为有限数值字符串；无重复、逆序或越界。`markPrice` 与 `rateType` 在两个
recent 的 42 条及两个 gap 的 2 条记录中全部存在且非 null，`markPrice` 为有限数值字符串，
`rateType` 实测为字符串 `"Regular"`；未观察到额外字段。它们是实际可选字段，不得由 archive
补造；settled identity/value 仍由 `symbol,fundingTime,fundingRate` 构成，也不从返回间隔推导
永久 8 小时日历。

## 3. 旧证据保持与对照

本轮不重新下载 #279 的 14 个 ZIP/CHECKSUM，也不改写旧报告、manifest、response 或
`NEEDS MORE EVIDENCE` 结论：

- #279 observation set ID 为
  `issue-279-probe-run-2026-09-09T10:58:43.717234Z`；旧 serverTime HTTP 451 response
  SHA-256 `79c1db471a4cdc1c8565b5dfc52e0ed2d29962738ee4d28597696fcd0920e348` 继续保留。
  其 manifest/report 当前 SHA-256 分别为
  `c4080de0dff862cebd1ad3363c8048c67805316a1700c4ff9e2b1621eaeb64d6` 与
  `930338128b144bccedbc052553b8b64e7ce3ea2831333daca5c3e360cd7dbfa2`。
- #191 REST-boundary observation 为 `2026-08-30T18:34:10.828640Z`，旧 serverTime
  response SHA-256 为
  `61f71a5c2de47e7bb153b7b61bc7a9a2ee2f40234bd56d51c36895c0bd27b186`；其 probe manifest
  当前 SHA-256 为 `cffaed42b5b5e65d58c67052e3517db56f54b7e9a06b0d6c8c2a7e0960c310c4`。

上述旧 digest 仅用于身份/语义对照。本轮的 current availability、funding REST-only 字段与
recent/gap 覆盖全部来自本轮实际 REST response，不从 archive 或旧窗口外推。新的正向结论是
独立补证，不追溯修改 #279 在其执行环境与时点下的准确结论。

## 4. 下游可执行 coverage 决策

| 消费者 | 决策 | 可接受的 coverage 输入 | 必须保留的限制 |
|---|---|---|---|
| #282 contract kline | IMPLEMENT | endpoint `/fapi/v1/klines`；`instrument/symbol` 为 BTCUSDT 或 ETHUSDT；1m；本轮 exact recent/gap；本 observation ID + manifest 引用 | 仅 exact observed window，不随当前日期延长；12 元素 contract 交易字段语义 |
| #282 mark-price kline | IMPLEMENT | endpoint `/fapi/v1/markPriceKlines`；`instrument/symbol` 为 BTCUSDT 或 ETHUSDT；1m；同上 | `[5,7,8,9,10,11]` 均为 ignore/placeholder，不发布成交语义 |
| #282 index-price kline | IMPLEMENT | endpoint `/fapi/v1/indexPriceKlines`；`price-index pair/pair` 为 BTCUSDT 或 ETHUSDT；1m；同上 | pair 与 perpetual symbol eligibility 分离；placeholder 同 mark |
| #283 settled funding | IMPLEMENT | endpoint `/fapi/v1/fundingRate`；`instrument/symbol` 为 BTCUSDT 或 ETHUSDT；本轮 exact recent/gap；本 observation ID + manifest 引用 | point-event coverage 不冒充连续日历；核心三字段，实际 optional 字段保留原值 |

实现方应把 manifest 中对应 family/subject/window 的 exact normalized params、response digest、
观测时间和实际范围绑定到 coverage；缺失引用、错 endpoint/subject、超出观测窗口或把一个族的
证据借给另一族时仍须在访问前返回 `rest_boundary_unknown`/unsupported。合成 fixture 只能验证
代码行为，不能替代这里的实际来源 evidence。

## 5. 结论与限制

最终唯一 Research Outcome 为 **IMPLEMENT**。本轮不存在因未执行或环境失败造成的必需
unknown，也未发现请求、subject、12 元素 Kline schema、funding schema、1m 时间或窗口合同
冲突；#282 与 #283 可以按上表的精确 coverage 决策实施。

该结论只证明本 observation 绑定的 BTCUSDT/ETHUSDT exact recent/gap 窗口与当前实际响应，
不证明全历史无缺口、未来 endpoint/schema 永不变化、其他 symbol/interval、USDC、COIN-M、
Spot 或其他域名。HTTP 200 `[]` 若未来出现仍必须记录为 `legal_empty`，不得当作正向覆盖；
transport、429、其他 4xx、5xx、schema 或边界失败仍须保持独立状态并按有限预算 fail closed。
