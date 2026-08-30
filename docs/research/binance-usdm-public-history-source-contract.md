# Binance USDⓈ-M 公共历史数据 source contract

> 研究结论：**ARCHITECTURE DECISION**
>
> 核验日期：2026-08-31（UTC）；Binance REST probe 的 `serverTime` 为
> `2026-08-30T11:30:01.759Z`。Binance 的对象、symbol、接口行为和发布政策可能变化；
> 本报告冻结的是当前可重复观察到的 contract，不是永久不变的交易所事实。

## 1. 决策摘要

Feature #11 首版应采用“双层 source policy”：

1. 对已关闭且已发布的历史区间，优先使用 Binance Data Collection 的完整 ZIP
   object；大区间按月度 object，最近已关闭日期按日度 object。
2. 对当前未发布日期、月度 object 尚未出现的月份、archive 首个 partial day，或
   已确认的 archive 缺口，使用对应 USDⓈ-M REST endpoint 的有界分页结果补采。
3. 不把 REST 请求区间当作 Raw object identity，也不把 archive 的日期文件名当作
   “整日完整”的证明；两者都必须保留实际响应/文件 SHA-256、请求参数和观测时间。
4. archive 缺失、checksum 不匹配、REST 空响应和网络/上游错误是不同状态。不得
   静默把其中一种转换成另一种；无法完整满足请求时必须产生显式 gap 或 fail-closed。

首批四类数据均有官方 REST endpoint。1m contract/mark/index kline 的 archive
同时有 daily 与 monthly；settled funding-rate archive 当前只有 monthly，REST 是
其 daily/current 补采路径。

## 2. 核验方法与官方来源

本次 probe 只使用 Binance 官方 Developer Docs、官方 `binance-public-data` 仓库、
`data.binance.vision` 对象以及对应公开 REST endpoint：

- [USDⓈ-M Futures REST market-data 文档](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)，包含 Exchange Information、四个 endpoint 的参数与响应 schema。
- [Exchange Information 文档](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)。
- [Kline 文档](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data)、[Mark Price Kline 文档](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price-Kline-Candlestick-Data)、[Index Price Kline 文档](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Index-Price-Kline-Candlestick-Data)、[Funding Rate History 文档](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History)。
- [Binance Public Data README](https://github.com/binance/binance-public-data/blob/master/README.md)：daily/monthly 发布说明、USD-M contract-kline schema、checksum 和 archive update 语义。
- [Public Data Python README](https://github.com/binance/binance-public-data/blob/master/python/README.md)：USD-M、mark/index price kline 的对象目录和下载参数。
- [Data Collection](https://data.binance.vision/) 及其 S3 listing。示例 listing：[`BTCUSDT` monthly 1m klines](https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=data/futures/um/monthly/klines/BTCUSDT/1m/)。

重复 probe 使用 `GET /fapi/v1/exchangeInfo`、四个 market-data endpoint、对象
`HEAD`/`GET`、S3 prefix listing，并下载了少量 ZIP 到临时目录检查成员名、CSV header、
首尾记录和 checksum。没有调用私有 API、API key 或交易接口。

## 3. 当前 instrument 状态

`GET /fapi/v1/exchangeInfo` 的当前快照对四个首批 instrument 均返回
`status=TRADING`、`contractType=PERPETUAL`：

| Instrument | quote / margin | onboardDate (UTC) | 当前状态 |
|---|---|---:|---|
| BTCUSDT | USDT / USDT | 2019-09-08 17:55 | TRADING / PERPETUAL |
| ETHUSDT | USDT / USDT | 2019-11-27 07:45 | TRADING / PERPETUAL |
| BTCUSDC | USDC / USDC | 2024-01-03 12:30 | TRADING / PERPETUAL |
| ETHUSDC | USDC / USDC | 2024-01-03 12:35 | TRADING / PERPETUAL |

`onboardDate` 只描述当前 exchange-info symbol 生命周期，不能作为每个数据族的
历史覆盖边界。下表中的 archive 范围来自 `.../daily|monthly/.../1m/` 或
`fundingRate/` 的对象 listing；首日的括号是实际检查到的 partial/full 状态。
`M` 是 monthly，`D` 是 daily；`→2026-07` / `→2026-08-29` 是本次 listing 的
最新已存在对象，不代表未来最终边界。

| 数据族 | BTCUSDT | ETHUSDT | BTCUSDC | ETHUSDC |
|---|---|---|---|---|
| 1m contract kline | M 2020-01→2026-07；D 2019-12-31（1,439 行，首行 00:01）→2026-08-29 | M 2020-01→2026-07；D 2019-12-31（1,439 行，首行 00:01）→2026-08-29 | M 2024-01→2026-07；D 2024-01-04（689 行，首行 12:31）→2026-08-29 | M 2024-01→2026-07；D 2024-01-04（684 行，首行 12:36）→2026-08-29 |
| 1m mark-price kline | M 2020-01→2026-07；D 2019-12-23（722 行，首行 11:58）→2026-08-29 | M 2020-01→2026-07；D 2019-12-23（722 行，首行 11:58）→2026-08-29 | M 2024-01→2026-07；D 2024-01-03（1,440 行）→2026-08-29 | M 2024-01→2026-07；D 2024-01-03（1,440 行）→2026-08-29 |
| 1m index-price kline | M 2020-01→2026-07；D 2019-12-23（722 行，首行 11:58）→2026-08-29 | M 2020-01→2026-07；D 2019-12-23（722 行，首行 11:58）→2026-08-29 | M 2024-01→2026-07；D 2024-01-03（1,440 行）→2026-08-29 | M 2024-01→2026-07；D 2024-01-03（1,440 行）→2026-08-29 |
| settled funding rate | M 2020-01→2026-07；无 daily object | M 2020-01→2026-07；无 daily object | M 2024-01→2026-07；无 daily object | M 2024-01→2026-07；无 daily object |

关键解释：

- 2026-08-29 的三类 1m daily object 均为 HTTP 200；2026-08-30 对四个 instrument
  均为 HTTP 404，符合“daily 次日发布”的观察。2026-08 monthly object 尚未出现。
- USDC 的 mark/index 及 funding archive 存在早于当前 `onboardDate` 的记录；这类
  记录不能被下载器自动删除。应同时保留 source coverage、exchange-info snapshot
  和实际 row time，由上层决定是否纳入某个 tradability universe。
- `indexPriceKlines` 的参数是 `pair`，不是 `symbol`。对 `BTCUSDC` 的 `startTime=0`
  probe 返回了 2019-12-23 的 BTC index rows，对 `ETHUSDC` 返回了 2022-01-17 的
  ETH index rows；这证明 index pair history 不能直接解释为对应 USDC perpetual
  已在那些日期可交易。

## 4. Source matrix：schema、边界与 endpoint

| 数据族 | 官方 archive object | archive schema（实际 CSV header） | REST endpoint | REST response |
|---|---|---|---|---|
| Contract kline | `data/futures/um/{daily,monthly}/klines/{SYMBOL}/1m/{SYMBOL}-1m-...zip` | `open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore` | `/fapi/v1/klines?symbol=...&interval=1m` | 12-element array；contract volume、quote volume、trade count、taker volumes 有业务语义 |
| Mark-price kline | `data/futures/um/{daily,monthly}/markPriceKlines/{SYMBOL}/1m/{SYMBOL}-1m-...zip` | 同样的 12 列名；实际 1m 样本中 volume/quote/taker 字段为 `0`，count 为 `60` | `/fapi/v1/markPriceKlines?symbol=...&interval=1m` | 12-element array；`[0..4]` 是 mark OHLC，`[5]`、`[7]`、`[8]`、`[9..11]` 是 Ignore，占位字段 |
| Index-price kline | `data/futures/um/{daily,monthly}/indexPriceKlines/{SYMBOL}/1m/{SYMBOL}-1m-...zip` | 同样的 12 列名；1m 样本为 price OHLC + 零/占位字段 | `/fapi/v1/indexPriceKlines?pair=...&interval=1m` | 12-element array；语义同 mark kline，但输入参数是 `pair` |
| Settled funding rate | `data/futures/um/monthly/fundingRate/{SYMBOL}/{SYMBOL}-fundingRate-YYYY-MM.zip` | `calc_time,funding_interval_hours,last_funding_rate` | `/fapi/v1/fundingRate?symbol=...` | object：`symbol,fundingRate,fundingTime,markPrice,rateType` |

所有 futures 时间戳 probe 都是 Unix milliseconds。2026-08-29 的 1m daily kline
样本有 1,440 行，open time 为 `[00:00, 24:00)` 的每分钟网格，close time 为每个
分钟的最后 1ms；首个历史 object 可以是 partial day。Funding archive 的 2026-07
BTCUSDT 样本为 93 行、8 小时一条，`calc_time` / `last_funding_rate` 与同时间的
REST `fundingTime` / `fundingRate` probe 相符，但 archive 不含 REST 的 markPrice、
rateType 和 symbol 字段，不能把两者当作同一 schema。

### REST 请求语义

官方文档与 probe 给出的实现约束如下：

- contract、mark、funding 用 `symbol`；index 用 `pair`。
- contract/mark/index 的 `limit` 默认 500、最大 1,500；funding 默认 100、最大
  1,000。`limit=1501` 的 kline probe 返回 `-1130`。
- 参数时间单位为 milliseconds。Funding 文档明确 `startTime` 和 `endTime` 均为
  inclusive；在 kline probe 中，`endTime=00:01:59.999` 返回 open time `00:00`
  和 `00:01`，`endTime=00:02:00` 开始再包含 `00:02`，因此实现应按 open time
  `<= endTime` 处理并自行做边界归一化。
- Funding 文档明确结果 ascending，且超过 `limit` 时从 `startTime + limit` 返回。
  Kline probe 也为 ascending；下载器仍必须检查 open time 单调性、重复和缺口，
  不应仅依赖未经验证的排序假设。
- 对内部请求 `[start, end)`，发送 `startTime=start`、`endTime=end-1`。完整页的
  下一页对 kline 使用 `last_open_time + 60_000`；funding 使用严格大于上一条
  `fundingTime` 的 cursor（例如 `last_funding_time + 1`），并检测不前进的响应。
- 四个 endpoint 对未来空区间返回 HTTP 200、JSON `[]`。这表示“该请求范围无
  返回记录”，不是 archive 404、REST 不支持或上游故障。
- 不带时间的 kline 请求返回最近 kline；funding 不带时间时返回最近记录。最新
  未关闭 candle 不应被当作已冻结历史；应保存抓取时刻并在后续重采时按新 digest
  形成新 snapshot。

## 5. Archive 发布、checksum 与修订

Public Data README 声明：新 daily 数据在次日可用，新 monthly 数据在每月第一个
星期一可用；每个 ZIP 同目录有 `.CHECKSUM`。本次观察到：

- `BTCUSDT-1m-2026-08-29.zip` 的 `Last-Modified` 为
  `2026-08-30T08:41:07Z`；同日 mark/index object 也已发布。
- `...-2026-08-30.zip` 对四个 instrument 的三类 kline 都是 404；monthly
  `...-2026-08.zip` 也尚未出现。
- 四个 data family、四个 instrument 的 1m monthly prefix 都同时列出 ZIP 与
  `.CHECKSUM`；funding-rate prefix 也如此，但没有任何 daily funding-rate
  object。

代表性 ZIP 下载后的 SHA-256 与官方 `.CHECKSUM` 完全相同：

| object | SHA-256 |
|---|---|
| [BTCUSDT daily contract kline](https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-08-29.zip) | `41e554b2a312bfadb74e4865c5cbef8dd401586c7d973c623d0915869eb81ebc` |
| [BTCUSDT daily mark kline](https://data.binance.vision/data/futures/um/daily/markPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2026-08-29.zip) | `2146dff9db1e1ddc3af33f00696f735d0076174602e659377532db817860f4ac` |
| [BTCUSDT daily index kline](https://data.binance.vision/data/futures/um/daily/indexPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2026-08-29.zip) | `26241ffe071d9019db8302b9e1765fd05160b9db5dd8704df6fb2cecdb79a2d1` |
| [BTCUSDT monthly funding rate](https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-07.zip) | `e36fcc66f493d7d9ec348c852fc22e9f318c79cf7adae17398a3994ae0adc41e` |

README 还明确写出 archived files 可能因发现问题而在以后更新，并提供 replacement
update changelog。S3 listing 也显示旧 object 的 `Last-Modified` 发生在其数据月份
之后，例如 BTCUSDT 2022-01 monthly kline 在 2023-12 被修改、2022-05 object 在
2024-08 被修改。因此：

- `object_key` 不能单独充当不可变身份；Raw identity 至少为
  `(source, market=um, family, instrument/pair, interval, object_key, upstream_sha256)`。
- 同一个 key 出现新 SHA-256 时必须保留为新版本或冲突分支，不得原地覆盖旧 Raw
  bytes，也不得用 S3 ETag 代替 `.CHECKSUM`。
- 下载器每次 materialize 前都要先取得 ZIP 和 `.CHECKSUM`，校验通过后才允许进入
  parser；checksum mismatch 应 quarantine/fail-closed。

## 6. Raw object identity 与 `[start, end)` 映射

### Archive

Archive ZIP 是上游完整 object，Raw 层以 ZIP bytes 为不可变 payload。日/月文件名
只表示上游 object 粒度，不表示用户请求范围。一个用户请求可以映射为多个 archive
objects：

```text
requested [start, end)
  -> closed monthly objects for full covered months
  -> daily objects for remaining closed days
  -> bounded REST pages for unpublished/partial/gap intervals
```

每个 object 的 manifest 至少记录 `object_key`、source URL、取得时间、HTTP metadata、
upstream checksum、实际 ZIP SHA-256、CSV member、首尾 observed timestamp、row count
和验证结果。按 `[start, end)` 裁出的数据只是 view/derived slice，不能生成一个新的
“看起来像完整 archive”的 Raw identity。

### REST

REST 每个 page 是独立的原始响应；其 identity 应包含：

```text
(endpoint, normalized params, response_body_sha256, observed_at, server_time)
```

其中 normalized params 必须保留 `symbol` 或 `pair`、interval、startTime、endTime、
limit；请求区间只是 provenance，不是稳定内容身份。同一请求后续可能返回不同 bytes
（最新 candle、上游历史修订或 API 语义变化），新 digest 必须形成新 snapshot。一个
逻辑 `[start, end)` acquisition manifest 可以引用多个 page，但不应拼接覆盖原始 page。

## 7. 首版 source-selection decision table

| 场景 | 首选来源 | 处理规则 |
|---|---|---|
| 大区间回填、完整已关闭月份 | monthly archive | 逐 object listing、下载、checksum、schema/timestamp 校验；缺 object 不得用空文件代替 |
| 月份边缘的已关闭日期、月度尚未发布 | daily archive（contract/mark/index） | 只选已存在的 UTC 日 object；funding 没有 daily archive，直接使用 REST |
| 当前日、未关闭 candle、当前 funding month | REST | `[start,end)` 转成 `startTime=start,endTime=end-1`，按 cursor 分页；保存每个 page 的请求和 digest |
| archive 首个 partial day | REST 补齐或保留显式 partial | 先用实际 row time 判断覆盖；不得因为文件存在就声称整日完整 |
| archive 中已确认的 row gap/duplicate | REST bounded supplement | REST 结果作为不同 source provenance；合并前按 dataset-specific key 去重并保留冲突；REST 为空时产生 gap |
| object 404，但按发布日历应已可用 | REST 仅作为显式 fallback | 记录 `archive_unavailable`；REST 能补齐则标记 fallback，不能补齐则 completeness fail/gap |
| checksum mismatch、ZIP 损坏或 schema 不符 | fail-closed | quarantine archive；不得静默用另一来源伪装成 archive 修复。若业务允许 REST 补采，也必须作为独立 source 记录 |
| REST 200 `[]` | 不自动 fallback | 区分“请求范围为空/该 instrument 无历史”与网络/上游错误；对要求完整的区间产生显式 gap |
| REST 429/5xx/超时 | 有界重试后 fail/gap | 错误分类必须保留；不能把未取得的数据变成零值或空成功 |

archive 与 REST 有重叠时，历史冻结优先 archive，REST 主要承担 unpublished/current
和 gap handling；不因为两者重叠就静默选择“看起来更完整”的一方。

## 8. 对后续实现 Task 的硬约束

1. market 固定为 `um`；contract、mark、index、funding 四个 data family 必须有
   独立 source type，不能用一个 generic kline parser 把 placeholder 当成交量。
2. 内部时间接口使用 timezone-aware UTC 和 `[start,end)`；上游 API 的 inclusive
   endTime 只在 adapter 边界转换一次。Raw 保留原始毫秒值。
3. `fundingRate` archive 的 `calc_time`、`funding_interval_hours`、
   `last_funding_rate` 是当前已证实的三列；REST 的 markPrice/rateType 不可从 archive
   推导，必须缺失即缺失或另行 REST 取得。
4. `indexPriceKlines` 必须使用 `pair`，且 pair history 不得自动解释为该 symbol
   的可交易历史。instrument eligibility 需要独立的 exchange-info snapshot。
5. 用实际 row timestamps 检查 daily/monthly object 的 partial、missing、duplicate、
   out-of-order；文件名和 HTTP 200 不足以证明完整覆盖。
6. Raw object 的 bytes、upstream checksum、response body 和 source metadata 保持
   immutable；archive 同 key 的替换必须形成可审计的新版本/冲突。
7. 任何 source fallback、gap、空响应、checksum mismatch、上游错误都进入 manifest
   状态，不能转换为零值、成功但少数据，或没有 provenance 的拼接结果。
8. Funding archive 当前没有 daily 发布 object；不要实现一个假定其存在的
   `daily/fundingRate` 路径。运行时先 listing/HEAD，再决定等待、REST 补采或 gap。

## 9. 已确认风险与 unknowns

- Public Data README 给出发布和 update 规则，但没有 archive completeness manifest；
  该仓库的官方公开对象存在 partial first days、历史 object 后续修改以及可观测的
  行级质量风险，后续必须做完整性检查。
- `fundingRate` archive object 已被官方 bucket 发布且可校验，但当前 README 的
  futures-only downloader 列表没有同等详细的 funding archive schema/发布说明；
  因此 funding 的发布状态应按实时 listing 观察，不把当前目录结构写成永恒保证。
- Kline 文档对 contract/mark/index 的 `startTime`/`endTime` 参数没有像 funding
  文档一样完整声明 inclusive 文字；本报告的 `[start,end)` adapter 规则来自实际
  boundary probe，后续必须保留回归 probe。
- 用 `startTime=0` 探测 funding 的“最早记录”不应作为边界 authority；有无 endTime、
  limit 和服务端窗口会影响结果。funding 边界应以 archive listing + 有界 historical
  query + 实际返回校验共同确定。
- 本次只做了小范围 row/schema/checksum probe，没有完成四个 instrument、四个 data
  family 全历史的缺口统计。因此后续实现必须把全量连续性检查作为数据质量门禁，不能
  将本报告的 coverage range 解释为无缺口保证。

## 10. 结论

结论为 **ARCHITECTURE DECISION**：Feature #11 后续实现可以直接按本报告的
archive-first、REST-supplement、content-addressed Raw、显式 gap/fallback policy
拆分 Task，不需要重新猜测四类 Binance USDⓈ-M source 的基本路径和时间语义；但
“完整历史无缺口”仍不是本研究声称已经证明的事实，必须由实现期的 manifest 和
连续性校验逐对象证明。
