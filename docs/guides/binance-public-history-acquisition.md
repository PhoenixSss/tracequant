# Binance 公共历史数据有限获取与来源编排

`BinancePublicHistoryAcquisition` 是 BTCUSDT/ETHUSDT USDⓈ-M 公共历史数据的同步库入口。
它编排 contract、mark-price、index-price 1m Kline 与 settled funding，复用现有 archive
backfill、REST page acquisition 和不可变 Raw/revision；它不扫描数据湖、不刷新来源证据，
也不实现 CLI 或后台 checkpoint。

## 输入与纯计划

调用者显式提供一个有限 `Sequence[BinancePublicHistoryAcquisitionRequest]`。每项包含：

- typed `InstrumentId`，或仅对 index-price 使用的 `BinancePriceIndexId`；
- 数据族、UTC 半开 `TimeRange` 和 `backfill`、`recent` 或显式 `gap` 用途；
- 本地 `output_root`；路径只由 typed source identity 生成，任何上游 URL/object key 都不能
  决定本地相对路径；
- `preserve_and_report` 冲突策略；gap 还必须包含非空的来源原因。

`BinancePublicHistoryCoverage` 将可用来源限制为调用者提交的证据。Archive 证据必须逐字段匹配
已核定来源报告的 exact cell：#279 manifest 提供 14 个固定成功对象，原始 source-contract
manifest 还提供 BTCUSDT/ETHUSDT 三类 Kline 在 2026-08-30 的实测 404，以及 mark/index
在 2019-12-23 的首日 partial。匹配字段包括数据族、typed subject、日/月 boundary、证据
版本/reference/manifest digest、观测时间、状态、object digest 和实际范围；复制旧证据元数据到
另一个 boundary 不会获得网络访问授权。报告绑定的 `not_found`/`partial` 会保留为明确的
未满足或仅使用已证实 REST fallback 的来源决策，不会因对象名存在而变成成功。REST 直接复用 `BinanceKlineRestCoverage` 或
`BinanceFundingRateRestCoverage` 的逐 endpoint/subject/window 合同。`unknown`、`partial`、
错 endpoint/subject 或超出观测窗口的证据不会授权远程访问。旧 probe 截止也不会按今天的
日期自动延长。

`BinancePublicHistoryAcquisitionBudget` 同时限制 archive object 数、REST page 数、每对象/
每页尝试、HTTP 总请求、单响应与累计下载 bytes、单次 timeout 以及总耗时；所有 archive、
fallback 和 REST page 共享同一份剩余预算。Funding page 数无法事先精确确定，因此计划只
记录调用者给定的有限 page 上限，不宣称精确页数。

```python
plan = acquisition.plan(requests, coverage, budget)
```

`plan()` 只做确定性划分和校验，不访问网络，也不创建 `output_root`/Raw 目录。计划 identity
绑定原请求、证据、预算和候选步骤，包括 archive 的 object key、URL、checksum URL、ZIP member
以及每个 REST step 的精确 coverage。`run()` 在执行前从原请求、证据和预算重新派生受控计划并
做结构化比较；任何执行定位、framing 或 step evidence 被替换都会在 I/O 前拒绝。Backfill 的
日/月 source-window 数会先以常量时间日历计算与显式月证据折叠核对预算，超限请求不会先展开
逐日或逐月 obligation 列表。

## 来源选择与 fallback

- Backfill 的完整、已有证据的关闭月份优先 monthly；三类 Kline 的月边缘使用 daily。若
  monthly 在执行时 404/checksum 404，计划只会逐日切换到同一月份内预先绑定且已证实的 daily
  cells；这些 daily objects 在计划时计入共享 object/HTTP 上限。Funding 只允许 monthly，
  绝不构造 `daily/fundingRate`。
- `recent` 和显式 `gap` 只在匹配的 frozen REST coverage 内生成请求；未知边界形成初始
  unmet obligation。
- 计划内 archive 404/checksum 404 或完整性 gap 可以进入已预先绑定、仍在预算内的 REST
  fallback，并同时保留原 archive 状态与实际替代来源。
- Checksum mismatch、非法 ZIP/schema、Raw 本地损坏或内容冲突不会通过切换来源变成成功，
  也不会临时追加未规划窗口。

```python
result = acquisition.run(plan)
```

Archive 下载、REST 内部 retry/backoff、page 发布和 revision 判断仍由既有消费者的内部受控
执行 seam 负责；调用者不能向单个适配器提交自造 object key/URL plan。
统一层只分配共享剩余预算、按计划调用它们并聚合结果；不会在上层叠加 REST 尝试次数或重置
总耗时。取消、无进展、预算耗尽和局部失败只停止受影响的剩余 obligations，已经发布的不可变
Raw 仍可精确读取。

## 结果、重叠与精确读取

`BinancePublicHistoryRunResult` 保留计划 identity、每个原请求、逐 obligation/来源状态与理由、
实际记录范围、满足/未满足范围、HTTP/object/page/bytes/time 用量和终止原因。每个 REST 来源
还通过 `BinancePublicHistorySourceResult.rest_pages` 原样保留有界的逐页 request、attempts、状态、
detail、response digest、观测时间、实际范围与 Raw revision/path；因此前页成功、后页失败时不会
丢失局部成功或失败语义。每个成功来源返回 `BinancePublicHistoryRawReference`，其中包含完整
`RawObjectIdentity`、
`RawRevisionIdentity`、精确 artifact path、记录数和实际范围，可用：

```python
artifact = RawStore(request.output_root).read_revision(
    reference.object_identity,
    reference.revision,
)
```

同一个 family/typed subject/output root 中，本次 run 触及的每个逻辑对象都会枚举 RawStore
里全部已验证 revisions（包括先前 run 保存的相同来源 revision），再按真实语义 key 比较；
相同 object/revision identity 只比较一次。Kline
使用 `open_time`，funding 使用 `calc_time`/`fundingTime`。Contract 比较真实 OHLC/volume/
count/taker 字段；mark/index 只比较价格语义，不把 placeholder/schema 差异当冲突；funding
只比较已确认的 `calc_time ↔ fundingTime` 与 `last_funding_rate ↔ fundingRate`。相同记录只在
结果中计数，Raw 不删除；不同内容返回双方精确 revision 引用并使整体保持 `conflict`，同时把
包含冲突语义 key 的 obligation/range 从 satisfied 改为明确 unmet。
运行时发现 Kline archive 覆盖缺口时，对象及统一来源结果仍保留已观测记录的首末范围；若枚举
或比较历史 revision 时发现本地损坏，`run()` 返回受影响来源的 `local_failure` 和未满足范围，
同时保留本次已验证的 Raw 引用，而不会让异常逃逸或覆盖损坏内容。

只有全部显式请求义务成立且不存在 gap、failure 或 conflict 时，整体状态才是 `completed`。
合法 REST empty、未知 coverage、funding point 实际范围和正常 endpoint 终止始终保持各自语义，
不会被扩大为连续日历覆盖或“全历史完整”。

## 限制

本入口不实现 source catalog/refresh、全湖 gap discovery、scheduler、服务化恢复、canonical/DQ、
跨源最终合并、USDC/COIN-M/Spot 或其他 provider。重复执行会让既有适配器核对 upstream revision
并复用相同已验证内容；新内容保留为新 revision，冲突不会覆盖。L07 可在这个同步入口之上提供
用户命令、摘要和指定范围恢复，但不能绕过这里的显式证据与预算边界。
