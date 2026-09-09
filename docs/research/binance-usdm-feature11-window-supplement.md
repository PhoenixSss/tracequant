# Binance USDⓈ-M Feature #11 有限验收窗口补充

Research Outcome: NEEDS MORE EVIDENCE

> Issue #279 的固定 archive 窗口有充分官方对象、checksum、格式与行级证据；但
> `GET https://fapi.binance.com/fapi/v1/time` 在本次执行环境返回 HTTP 451，未取得
> 官方 `serverTime`。因此没有用本机时间代替 T0，也没有构造或执行 recent/gap REST
> 请求。下游可以实现本文明确支持的 archive 路径，但不能据此宣称 recent/gap REST
> 路径已验收。

本报告与 [probe manifest](./binance-usdm-feature11-window-probes.json) 相互引用；两者以
`issue-279-probe-run-2026-09-09T10:58:43.717234Z` 作为共同 observation set ID。manifest
中的 `artifact_binding.report_path` 反向指向本报告，实际响应由其中的 response SHA-256
和 archive checksum 字段绑定。精确 URL、UTC 观测时间、响应 bytes/SHA-256/header、
ZIP 成员、首尾 bounded sample 和序列检查均在 manifest。完整响应保留在忽略的本地
证据目录，未版本化提交。

## 1. 有限请求清单与预算

请求清单在第一次 HTTP 请求前固定：1 个 `serverTime`、14 个 archive ZIP、14 个
`.CHECKSUM`、8 个 recent REST、8 个 gap REST，共 45 个主请求，另留 19 个受限重试槽。
硬上限为 64 次 HTTP（含重试与 redirect）、128 MiB 累计响应、单响应 16 MiB、从首个
请求起 15 分钟、单次 timeout 10 秒、同一语义请求最多 2 次；不跟随 redirect。

实际执行于 `2026-09-09T10:58:43.717234Z` 至 `2026-09-09T10:59:19.501987Z`，墙钟
区间为 35.785 秒；预算计时采用 monotonic clock，耗时 33.619 秒。两者均明确记录在
manifest，且远低于 900 秒上限。共 29 次 HTTP、0 次重试、0 次 redirect、累计
7,917,924 bytes。未触及任何预算上限。archive 的 28 个请求全部 HTTP
200；`serverTime` 为 HTTP 451。由于 T0 不存在，其余 16 个 REST 计划项不是 HTTP
空响应或 endpoint unsupported，而是 `unknown / not_executed`。

## 2. T0 与窗口

`serverTime` 请求观测于 `2026-09-09T10:58:43.717243Z`，响应 224 bytes，SHA-256
`79c1db471a4cdc1c8565b5dfc52e0ed2d29962738ee4d28597696fcd0920e348`，响应体为 Binance 的 restricted-location 错误；
没有 `serverTime` 字段。T0、M、下列四类绝对窗口均为 unknown：

- kline recent `[M-60min,M)`；
- funding recent `[T0-7days,T0)`；
- kline gap（recent 的前 5 个完整分钟）；
- funding gap（recent 第一条 settled record 起最多 1 小时且不超过 T0）。

没有使用本机时钟、fixture、旧 serverTime 或 `startTime=0` 冒充本轮绝对边界；也没有
换域、无限重试或自动开启第二轮。

## 3. 八个核心单元结论

| 数据族 | subject | 固定 archive | recent REST | gap REST | 消费者 |
|---|---|---|---|---|---|
| contract_kline | BTCUSDT | supported（2026-07 monthly + 2026-08-29 daily） | unknown | unknown | archive planner/parser plus REST recent/gap fallback |
| contract_kline | ETHUSDT | supported（2026-07 monthly + 2026-08-29 daily） | unknown | unknown | archive planner/parser plus REST recent/gap fallback |
| mark_price_kline | BTCUSDT | supported（2026-07 monthly + 2026-08-29 daily） | unknown | unknown | archive planner/parser plus REST recent/gap fallback |
| mark_price_kline | ETHUSDT | supported（2026-07 monthly + 2026-08-29 daily） | unknown | unknown | archive planner/parser plus REST recent/gap fallback |
| index_price_kline | BTCUSDT pair | supported（2026-07 monthly + 2026-08-29 daily） | unknown | unknown | archive planner/parser plus REST recent/gap fallback |
| index_price_kline | ETHUSDT pair | supported（2026-07 monthly + 2026-08-29 daily） | unknown | unknown | archive planner/parser plus REST recent/gap fallback |
| settled_funding_rate | BTCUSDT | supported（2026-07 monthly） | unknown | unknown | archive planner/parser plus REST recent/gap fallback |
| settled_funding_rate | ETHUSDT | supported（2026-07 monthly） | unknown | unknown | archive planner/parser plus REST recent/gap fallback |

这里的 `supported` 只覆盖两个固定 archive 窗口：所有单元的 monthly 基准
`[2026-07-01T00:00:00Z,2026-08-01T00:00:00Z)`，以及三类 kline 的 daily 边缘
`[2026-08-29T00:00:00Z,2026-08-30T00:00:00Z)`。Funding 没有把 daily 路径列为候选。

## 4. 固定 archive 实测

### 2026-07 monthly

| 数据族 | subject | 行数 | 首尾实际时间 | ZIP SHA-256 | ZIP/checksum | duplicate / non-increasing / missing grid |
|---|---|---:|---|---|---|---|
| contract_kline | BTCUSDT | 44640 | 2026-07-01T00:00:00Z → 2026-07-31T23:59:00Z | `f18440bb58f0…` | 200 / 匹配 | 0 / 0 / 0 |
| contract_kline | ETHUSDT | 44640 | 2026-07-01T00:00:00Z → 2026-07-31T23:59:00Z | `5c98ad0cfa15…` | 200 / 匹配 | 0 / 0 / 0 |
| index_price_kline | BTCUSDT | 44640 | 2026-07-01T00:00:00Z → 2026-07-31T23:59:00Z | `80f701b6e752…` | 200 / 匹配 | 0 / 0 / 0 |
| index_price_kline | ETHUSDT | 44640 | 2026-07-01T00:00:00Z → 2026-07-31T23:59:00Z | `dd2524b6c7fb…` | 200 / 匹配 | 0 / 0 / 0 |
| mark_price_kline | BTCUSDT | 44640 | 2026-07-01T00:00:00Z → 2026-07-31T23:59:00Z | `5bb16a0707ee…` | 200 / 匹配 | 0 / 0 / 0 |
| mark_price_kline | ETHUSDT | 44640 | 2026-07-01T00:00:00Z → 2026-07-31T23:59:00Z | `641ce19380be…` | 200 / 匹配 | 0 / 0 / 0 |
| settled_funding_rate | BTCUSDT | 93 | 2026-07-01T00:00:00Z → 2026-07-31T16:00:00Z | `e36fcc66f493…` | 200 / 匹配 | 0 / 0 / 0 |
| settled_funding_rate | ETHUSDT | 93 | 2026-07-01T00:00:00Z → 2026-07-31T16:00:00Z | `ece7f6c64d0e…` | 200 / 匹配 | 0 / 0 / 0 |

### 2026-08-29 daily kline

| 数据族 | subject | 行数 | 首尾实际时间 | ZIP SHA-256 | ZIP/checksum | duplicate / non-increasing / missing grid |
|---|---|---:|---|---|---|---|
| contract_kline | BTCUSDT | 1440 | 2026-08-29T00:00:00Z → 2026-08-29T23:59:00Z | `41e554b2a312…` | 200 / 匹配 | 0 / 0 / 0 |
| contract_kline | ETHUSDT | 1440 | 2026-08-29T00:00:00Z → 2026-08-29T23:59:00Z | `6d64864dff24…` | 200 / 匹配 | 0 / 0 / 0 |
| index_price_kline | BTCUSDT | 1440 | 2026-08-29T00:00:00Z → 2026-08-29T23:59:00Z | `26241ffe071d…` | 200 / 匹配 | 0 / 0 / 0 |
| index_price_kline | ETHUSDT | 1440 | 2026-08-29T00:00:00Z → 2026-08-29T23:59:00Z | `c15bec355619…` | 200 / 匹配 | 0 / 0 / 0 |
| mark_price_kline | BTCUSDT | 1440 | 2026-08-29T00:00:00Z → 2026-08-29T23:59:00Z | `2146dff9db1e…` | 200 / 匹配 | 0 / 0 / 0 |
| mark_price_kline | ETHUSDT | 1440 | 2026-08-29T00:00:00Z → 2026-08-29T23:59:00Z | `ba0613355d8a…` | 200 / 匹配 | 0 / 0 / 0 |

14 个 ZIP 均为单一 CSV member、带 header；每个 actual ZIP SHA-256 均与对应官方
`.CHECKSUM` 声明一致。Kline 每行 12 列，monthly 每对象 44,640 行、daily 每对象
1,440 行，1 分钟网格内未观察到 duplicate、逆序或缺格。Funding 每对象 93 行、每行
3 列，实际时间从 `2026-07-01T00:00:00Z` 到 `2026-07-31T16:00:00Z`，8 小时网格内
未观察到 duplicate、逆序或缺格。上述检查不证明其他对象或全历史连续。

## 5. Schema 与 funding 差异

- Contract archive 的 12 列按
  `open_time,open,high,low,close,volume,close_time,quote_volume,count,`
  `taker_buy_volume,taker_buy_quote_volume,ignore` 解析。
- Mark/index archive 的物理布局也是 12 列，但本轮样本再次显示 volume/quote/taker
  为 `0`、`count=60`；这些是 placeholder，必须映射为 ignore/null，不能作为成交量或
  成交笔数。Index REST 的 subject 合同仍是独立的 `pair`，不是 perpetual `symbol`。
- Funding archive 的三列 `calc_time,funding_interval_hours,last_funding_rate` 是必需字段；
  本轮 BTC/ETH 真实时间值均覆盖 93 个 8 小时 settled slot。REST 侧用于 identity/value
  的字段是 `symbol,fundingRate,fundingTime`；`markPrice`、`rateType` 只应在存在时保留。
  #191 曾描述五个字段，但 #279 没有取得当前 REST body，因此字段当前存在性、nullability
  与额外字段全部保持 unknown。Archive 不含 `symbol`、`markPrice`、`rateType`，不得推导。

## 6. 与 #191 snapshot 的关系及 USDC 边界

旧报告与 manifest 的核验日期为 2026-08-30；本轮 14 个固定对象的 digest 与旧 manifest
逐一相同（14 unchanged、0 changed），没有静默覆盖旧结论，也没有新增冲突。本轮
REST 因 T0 缺失而无法形成新旧 response 比较。

USDC 只保留 #191 的有日期结论，没有发起新请求：BTCUSDC/ETHUSDC contract archive
从 2024-01 观察到，首个 daily 是 2024-01-04 的 partial；mark/index 从 2024-01 观察到，
首个 daily 是 2024-01-03；funding 仅观察到 monthly、没有 daily；四个 USDC funding
REST earliest boundary 均为 unknown。Index pair 历史早于 symbol onboardDate 不能证明
对应 USDC perpetual 当时可交易。本项不新增 USDC 实现。

## 7. 消费者决策

| 消费者 | 决策 | 可用的确切输入 | 停止条件/限制 |
|---|---|---|---|
| archive planner/downloader | 可实现固定窗口 | manifest 中 14 个精确 ZIP + checksum URL | 不外推全历史或未来发布 |
| contract/mark/index parser | 可按族实现 | 12 列布局；mark/index placeholder 显式忽略 | 每个新 object 仍须做 schema/order/duplicate/gap 检查 |
| funding archive parser | 仅 monthly 可实现 | 三列 archive schema 与 8 小时真实样本 | 不构造 daily funding；不推导 REST-only 字段 |
| recent/gap REST acquisition | 暂不可验收 | 无 | T0 缺失，窗口与正向 gap 样本均 unknown |
| index REST adapter | 参数合同已知、窗口未验收 | `pair=BTCUSDT` / `pair=ETHUSDT` | pair history 与 symbol eligibility 分离 |

状态必须分开：archive 404 是 `archive_missing`；checksum/ZIP/schema 失败是
`checksum_or_format_failure`；REST HTTP 200 `[]` 才是 `legal_empty`；除 archive 404
特例外，收到非成功 HTTP 响应是 `http_error`，必须保留 `http_status`、有响应 body 时的
实际 bytes/digest 和有界 header/body。HTTP 5xx 仅可在单语义请求与总预算都允许时
重试一次；HTTP 429 和非 transient 4xx（包括本次 451）不重试，并停止相关语义请求；
无法取得 T0 时同时停止依赖 T0 的 recent/gap 计划。

DNS、连接、TLS、timeout 等在收到 HTTP 响应前失败是独立的 `network_failure`：此时
`http_status` 必须为 null，记录有界 transport error，且不得伪造响应 bytes 或 digest。
它同样最多在预算内重试一次，第二次失败或任一全局上限到达即停止。两类失败都不得换域
绕过限制或开启无限重试；未执行仍是 `unknown`。本轮只观察到 archive success、
checksum/格式 success 和 serverTime `http_error` 451，未观察到 network failure 或其余状态。

## 8. 限制与有界处置建议

本轮不能提供 current REST availability、合法空数组、正向 gap、funding 当前字段或
absolute recent/gap 边界的证据。因此 downstream 不得宣告这些来源可用，也不得把旧
`serverTime` 滚动为本轮 T0。

唯一建议是：由维护者另行决定是否在允许访问 Binance USDⓈ-M REST 的网络位置执行
一次仅含 `serverTime + 8 recent + 最多 8 gap` 的补证；最多 17 个主请求、每项最多一次
transient retry（总计最多 34）、32 MiB、5 分钟，任一上限或无 T0/429 立即停止。该建议
不是自动重试，也不在本轮扩展预算。

## 9. 结论

最终 Research Outcome 为 **NEEDS MORE EVIDENCE**。固定 archive 证据足以支持
BTCUSDT/ETHUSDT 的三类 kline archive 与 monthly funding archive 的受限实现；REST
recent/gap 路径仍必须 fail closed / 保持 unknown，直至取得按本 Issue 窗口合同绑定的
官方 T0 和实际响应证据。
