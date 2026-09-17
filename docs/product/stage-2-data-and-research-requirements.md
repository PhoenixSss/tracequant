# TraceQuant 阶段 2：Nautilus 同源数据详细需求

| 字段 | 值 |
| --- | --- |
| 文档状态 | 已批准并完成的实施基线（阶段 2 已验收） |
| 文档版本 | `0.4` |
| 日期 | `2026-09-14` |
| 产品状态 | `OFFLINE_BACKTEST_ONLY`、`LIVE_NOT_APPROVED` |
| 固定运行时 | NautilusTrader `2.0.0rc4` / `a0400251110653b6d8ae6a9b5b89c4543fa85a2d` |
| 输入数据集 | `binance-usdm-btceth-202001-202608-r1`（已验收，见 `stage2-btceth-dataset-acceptance.json`） |
| 上位计划 | [TraceQuant 分阶段推进计划](<../research/foundation-selection/TraceQuant 分阶段推进计划.md>) |

阶段 2 已按本文档 §10 的退出条件完成验收，冻结的数据身份、数据内容、时间切分和
acceptance digest 不再变更。后续阶段在本文档之上追加需求，不回写本文档的数据合同；
阶段 3 的策略与模型需求见
[阶段 3：策略与模型实施基线](stage-3-strategy-and-model-requirements.md) v0.1。

## 1. 目标

阶段 2 只解决一个问题：

> 将用于策略研究的历史市场数据转换为 Nautilus 原生数据，并由 Nautilus 自己持久化；
> 研究和回测从同一个 Nautilus catalog 读取，避免两套数据产生偏差。

阶段 2 不建设数据平台，不复制 Nautilus 的 catalog、回测、账户或 funding 能力。

```text
Binance 官方 public-data 归档（长历史主源）
  -> CSV / Polars DataFrame
  -> TraceQuant 清洗和字段映射
  -> Nautilus 原生 Instrument / Bar / MarkPriceUpdate / FundingRateUpdate
  -> Nautilus ParquetDataCatalog
       -> BacktestDataConfig / BacktestNode
       -> 只读研究视图（Polars）
```

导入成功后，Nautilus catalog 是市场数据的唯一 canonical copy。原始下载文件只用于来源追溯
和重新导入，不作为后续研究计算的另一个事实源。

## 2. Nautilus 持久化调研结论

本节结论来自项目锁定的 NautilusTrader `2.0.0rc4`，而不是较新版本的 API 假设。

### 2.1 常规数据

`ParquetDataCatalog` 已直接提供以下能力：

- `write_instruments(...)` / `instruments(...)`；
- `write_bars(...)` / `query_bars(...)`；
- `write_mark_price_updates(...)` / `query_mark_price_updates(...)`；
- `write_index_price_updates(...)` / `query_index_price_updates(...)`；
- `query_files(...)`、时间范围过滤和标的过滤；
- 默认拒绝与既有文件时间范围相交的写入。

因此，TraceQuant 不应自行定义 Parquet schema 或拼装 catalog 目录。正确方式是先构造
Nautilus 原生对象，再调用上述 writer。Nautilus 生成的 Bar Parquet 使用其内部定点数 Arrow
编码；用 Polars 直接仿写这些文件会耦合 Nautilus 的内部存储格式。

### 2.2 回测读取

`BacktestDataConfig` 通过 `catalog_path`、`data_type`、标的/Bar 类型和起止时间描述回测数据。
`BacktestNode` 从 catalog 查询并按 `ts_init` 合并排序后交给回测引擎。

所以阶段 2 不实现数据回放器。验收只需证明回测配置指向本阶段生成的同一个 catalog。

### 2.3 Funding 在 rc4 中的原生路径

rc4 的 Python `ParquetDataCatalog` 没有直接暴露
`write_funding_rates(...)` / `query_funding_rates(...)`，但这不要求 custom data：

1. 将输入行转换为原生 `FundingRateUpdate`；
2. 使用 `StreamingFeatherWriter` 写 Nautilus Feather stream；
3. 调用 `ParquetDataCatalog.convert_stream_to_data(...)`；
4. Nautilus 将其转换为 catalog 内的原生 `funding_rate_update` Parquet；
5. 回测使用 `BacktestDataConfig(data_type="FundingRateUpdate", ...)` 直接加载。

已使用项目锁定 wheel 完成最小 round-trip：两个 `FundingRateUpdate` 被写入 Feather、转换为
catalog Parquet，并由 `BacktestNode` 读入两个迭代。因此阶段 2 明确不引入自定义 funding
record、第二套事件或第二套 accounting。

rc4 的 Python typed query 尚不能直接查询 funding。研究侧必须通过
`catalog.query_files("funding_rate_update", ...)` 获取同一批 Nautilus Parquet 文件，再用
Polars 只读扫描。该文件的 rc4 schema 为：

```text
instrument_id: String
rate: String
interval: UInt64
next_funding_ns: UInt64
ts_event: UInt64
ts_init: UInt64
```

这是读取适配，不是另一份持久化数据。

### 2.4 Funding 时间语义

历史 funding 行必须映射为实际结算事件：

- `ts_event` 是交易所给出的 funding time；
- `ts_init` 是该历史记录对回测可见的时间，不得早于 `ts_event`；
- 若来源提供 funding interval，映射到 `interval`（分钟）；
- 若来源提供下一次结算时间，映射到 `next_funding_ns`；
- 若 `interval` 和 `next_funding_ns` 均缺失，该事件只能用于策略观察，不能声称已验证
  funding payment。

Nautilus 只有在事件含 `next_funding_ns`，或 `ts_event` 位于 `interval` 边界时才进行 funding
结算。数据导入不得猜测无法从来源证明的结算周期。

### 2.5 历史数据来源

阶段 2 的 USD-M 长历史主源固定为 Binance 官方
[`binance-public-data`](https://github.com/binance/binance-public-data) 归档和下载工具：

| 数据 | 官方脚本 | 参数 |
| --- | --- | --- |
| 合约 Kline | `download-kline.py` | `-t um` |
| Mark price Kline | `download-futures-markPriceKlines.py` | `-t um` |
| Index price Kline | `download-futures-indexPriceKlines.py` | `-t um` |
| Funding | 官方 `monthly/fundingRate` 归档 | 直接下载 ZIP 与 `.CHECKSUM` |

三个 Kline 脚本必须使用 `-c 1` 同时取得对应 `.CHECKSUM`；Funding 直接下载同目录的 ZIP
和 `.CHECKSUM`。所有 ZIP 均在解压或转换前执行 SHA-256 校验。长历史优先下载月度归档；
尚未进入月度归档的近期数据可使用官方日度归档。

Nautilus historical request 只允许用于：

1. **小范围交叉验证**：请求与归档重叠的短窗口，比较标的、时间戳和价格字段；验证结果不写入
   canonical catalog；
2. **尾部补数**：只补官方归档末端之后、Nautilus client 原生支持的数据类型和明确时间窗口，
   并经过与归档相同的校验、原生转换和 catalog 写入路径。

尾部补数必须显式配置，不能在归档下载失败、checksum 失败或 coverage 异常时自动触发。
归档数据与尾部数据允许保留一个小型重叠窗口用于比对，但同一时间戳只能选择一个 canonical
记录写入 catalog；来源和选择边界必须写入 manifest。

固定七日 cross-check 的 1h Bar 通过 Nautilus rc4 Binance adapter 请求。rc4 adapter 对
USD-M 历史 funding 请求返回空集合，因此同一窗口的 funding 对照通过 Binance 官方
`/fapi/v1/fundingRate` REST 端点读取，再转换为 `FundingRateUpdate` 后比较 instrument、
时间戳和 rate。该 REST 响应只用于交叉验证，不写入 canonical catalog，也不作为尾部补数。

Binance 说明归档文件可能因修正而在日后更新。因此 manifest 保存本次实际下载的 URL 和官方
checksum；同一路径的 checksum 后续发生变化时视为新的源版本，不覆盖已经验收的数据集。

官方 Python 工具没有 funding 专用脚本。Funding 长历史直接使用同一 public-data 服务中的
`data/futures/um/monthly/fundingRate/<symbol>/` ZIP 和 `.CHECKSUM`，不因此自建通用下载器。

### 2.6 官方依据

- [rc4 persistence Python API](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc4/python/nautilus_trader/persistence/__init__.pyi)
- [rc4 BacktestDataConfig API](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc4/python/nautilus_trader/backtest/__init__.pyi)
- [Nautilus external data loading guide](https://nautilustrader.io/docs/latest/how_to/loading_external_data/)
- [Nautilus data catalog documentation](https://nautilustrader.io/docs/latest/concepts/data/)
- [FundingRateUpdate documentation](https://nautilustrader.io/docs/latest/concepts/data/funding_rate_update/)
- [Binance public-data Python 工具说明](https://github.com/binance/binance-public-data/blob/master/python/README.md)
- [Binance public-data checksum 与归档更新说明](https://github.com/binance/binance-public-data#checksum)
- [Binance USD-M monthly fundingRate 归档](https://data.binance.vision/?prefix=data/futures/um/monthly/fundingRate/)

## 3. 数据范围

阶段 2 首版支持：

| 数据 | 标的 | 粒度/频率 | Catalog 表达 |
| --- | --- | --- | --- |
| 可交易 Bar | BTCUSDT、ETHUSDT | public-data Kline：15m、1h、4h | `Bar` / `LAST-EXTERNAL` |
| Mark price | BTCUSDT、ETHUSDT | public-data 15m markPriceKlines | `MarkPriceUpdate` |
| Index price | BTCUSDT、ETHUSDT | 验证 public-data 15m indexPriceKlines 覆盖 | 首版不写入 catalog |
| Funding | BTCUSDT、ETHUSDT | 交易所结算事件 | `FundingRateUpdate` |
| 合约定义 | BTCUSDT、ETHUSDT | 每个数据集一份快照 | `CryptoPerpetual` |

每一行已关闭的 15m mark Kline 只转换为一个 `MarkPriceUpdate`：价格取 `close`，`ts_event`
取该 Kline 的 close time。阶段 2 不把这种转换描述为完整的盘中 mark tick。Index 只确认来源、
时间范围和 checksum 能力；没有明确 basis/premium 研究需求前不下载、不转换、不写 catalog。

具体历史时间窗口是数据集配置，不在代码中硬编码。首个实现 Issue 必须给出明确的
`start`、`end`，且使用半开区间 `[start, end)`。

### 3.1 首个数据集建议配置

截至 `2026-09-14` 核查 Binance 官方归档，BTCUSDT 和 ETHUSDT 的首版五类序列及候选 15m
index 均连续列出 `2020-01` 至 `2026-08` 共 80 个月度对象。对象内的逐行完整性仍由导入
coverage 检查；首个数据集建议冻结为：

```toml
schema = "tracequant-stage2-dataset-v1"
dataset_id = "binance-usdm-btceth-202001-202608-r1"
nautilus_version = "2.0.0rc4"
environment = "offline"

source = "binance-public-data"
market = "futures/um"
archive_frequency = "monthly"
window_start = "2020-01-01T00:00:00Z"
window_end = "2026-09-01T00:00:00Z"

instrument_ids = [
  "BTCUSDT-PERP.BINANCE",
  "ETHUSDT-PERP.BINANCE",
]
bar_intervals = ["15m", "1h", "4h"]
mark_price_interval = "15m"
include_index_price = false
include_funding = true
mark_max_age_at_funding = "15m"

raw_root = "/absolute/external/raw-root"
catalog_path = "/absolute/external/catalog-root/binance-usdm-btceth-202001-202608-r1"

[nautilus_crosscheck]
enabled = true
start = "2026-08-25T00:00:00Z"
end = "2026-09-01T00:00:00Z"
series = ["1h-bars", "funding"]

[nautilus_tail]
enabled = false

[splits]
train_end = "2024-01-01T00:00:00Z"
validation_end = "2025-01-01T00:00:00Z"
```

切分由上述两个边界唯一推导：

```text
train      = [2020-01-01, 2024-01-01)
validation = [2024-01-01, 2025-01-01)
test       = [2025-01-01, 2026-09-01)
```

选择理由：

- `2020-01-01` 是两个标的、首版五类序列及候选 15m index 共同存在的首月边界；
- `2026-09-01` 是核查时最后一个完整月之后的边界，首版无需混入尾部数据；
- 15m/1h/4h 均保存 Binance direct Kline，不在导入阶段重新聚合；
- 15m mark 与最低 Bar 周期一致；每个 funding 时点使用此前最近的 mark，并以 15 分钟为最大
  允许陈旧度；
- rc4 funding 结算不读取 index；首版只验证 index 归档来源和覆盖，待明确的 basis/premium
  研究需求出现后再增加；
- 训练包含多个牛熊阶段，validation 使用完整自然年，test 保留 20 个最近完整月；
- 七日交叉验证足以检查转换语义，不把 Nautilus 请求变成长历史下载通道。

该配置共包含 `2 symbols x 5 series x 80 months = 800` 个数据 ZIP，以及对应 checksum。
五类序列是 15m/1h/4h Bar、15m mark 和 funding。核查时 ZIP 总量约 42 MiB；该大小只作
容量预估，不进入数据身份。Index coverage 核查不计入数据集文件数。

`binance-usdm-btceth-202001-202608-r1` 的 monthly 15m mark 归档存在已由对应官方 daily
ZIP 与 `.CHECKSUM` 复核的缺失区间。首个数据集额外固定 18 个 daily mark 来源对象：其中 16 个用于补齐
monthly 归档在 `2021-07-01`、`2021-07-24..27`、`2022-07-31`、`2022-10-02`、
`2023-02-24` 和 `2026-06-29` 的缺失记录；daily 与 monthly 重叠记录必须逐字段一致，否则
验收失败。两个来源都缺少 `2020-01-19T13:15Z` 和 `2023-11-10T03:45Z` 这两个 15 分钟
mark 区间，且两个区间内均无 funding 事件。这两处是该数据集唯一允许的、按精确前后时间戳
识别的 mark gap；任何其他 Bar、mark 或 funding gap 均失败。800 个 monthly 对象保持主来源
清单。另两个 `2019-12-31` daily 对象只为数据集左边界的首个 funding 提供前置 mark 新鲜度
证据，不写入 canonical catalog。18 个 daily 对象作为 `supplemental_sources` 单独进入 source
manifest 和数据集 digest。

首个数据集还冻结以下映射：

```text
Trade Kline:
  OHLCV       <- 源 open/high/low/close/volume
  ts_event    <- close_time
  ts_init     <- close_time

Mark Kline:
  price       <- close
  ts_event    <- close_time
  ts_init     <- close_time

Funding:
  rate            <- last_funding_rate
  interval        <- funding_interval_hours * 60
  ts_event        <- calc_time
  ts_init         <- calc_time
  next_funding_ns <- calc_time
```

Funding 的 `next_funding_ns <- calc_time` 是 rc4 回测映射：源字段明确给出本次实际结算时刻，
而近期归档的 `calc_time` 可能比整点多 1 ms，不能只依赖 rc4 的 interval 整除判断。该映射必须
由“一条源 funding 记录恰好产生一次账户结算”的测试保护，不能用于预测下一次 funding。

Instrument definition 从 Nautilus Binance instrument provider 获取一次并原生写入 catalog；
manifest 保存获取时间和序列化后 checksum。后续复现直接使用冻结快照，不重新获取当前定义。

解析器必须同时接受官方历史月包的无 header CSV 和近期月包的有 header CSV，但解析后的列数、
字段名和类型必须完全一致；不能通过猜测列偏移静默容错。每个 funding 事件之前必须存在一条
年龄不超过 15 分钟的 mark；否则 coverage 失败，不允许执行 funding-aware backtest。

### 3.2 已验收 catalog 的发布、恢复与保留

首个已验收 catalog 通过仓库内独立 artifact lock
`config/datasets/binance-usdm-btceth-202001-202608-r1.lock.json` 定位。该 lock 与 tracked
acceptance record 分离，绑定固定 GitHub Release tag、固定 `.catalog.tar.zst` asset、archive
大小与 SHA-256、唯一解压根、逐文件路径/大小/SHA-256，以及完整 acceptance、dataset、source
manifest、market-data manifest、Instrument snapshot 和 runtime identity；不得改用 `latest`
或本机路径。`docs/product/stage2-btceth-dataset-publication.json` 单独记录恢复方法、Release
identity、两次真实恢复和 Stage 3 正式 loader smoke，不包含 catalog bytes、raw 数据或本机路径。

正常消费者不需要保留或重新下载 800+18 个 raw ZIP。使用 Stage 2 专用入口下载、校验并原子
安装到显式仓库外路径：

```bash
uv run --frozen python -m tracequant.integrations.nautilus.stage2_artifact materialize \
  --lock config/datasets/binance-usdm-btceth-202001-202608-r1.lock.json \
  --staging-root /absolute/external/stage2-staging \
  --catalog-path /absolute/external/catalog-root/binance-usdm-btceth-202001-202608-r1

uv run --frozen python -m tracequant.integrations.nautilus.stage2_artifact verify \
  --lock config/datasets/binance-usdm-btceth-202001-202608-r1.lock.json \
  --catalog-path /absolute/external/catalog-root/binance-usdm-btceth-202001-202608-r1
```

materialize 在解压前验证 archive 大小和 hash，拒绝绝对路径、路径穿越、symlink、重复或未知
成员；解压后验证逐文件 hash、四个 catalog evidence 文件、完整 Stage 2 identity 和原生
Instrument 定义，最后才原子发布。目标已非空或 identity 不匹配时失败，不覆盖、合并或就地
修补。`verify` 完全离线。Release 是版本化恢复来源，但操作者仍负责在临时目录外保留至少一份
独立备份或可恢复副本；Stage 3 的 `catalog_path` 始终指向解包后的唯一外部 Nautilus catalog。

## 4. 最小数据合同

### ST2-REQ-001：导入输入

导入器接受由上述官方工具下载并通过 checksum 的 CSV，也可接受已有 Parquet 或 Polars
DataFrame。TraceQuant 不重写或 fork Binance 下载器。每类输入必须有明确 schema；首版至少
包含：

```text
Bar:
  instrument_id, bar_type, open, high, low, close, volume,
  ts_event, ts_init

Mark:
  instrument_id, price, ts_event, ts_init

Funding:
  instrument_id, rate, interval?, next_funding_ns?, ts_event, ts_init
```

价格、数量和 funding rate 不得先经二进制浮点数再格式化。必须从源字符串或 Decimal 构造
Nautilus `Price`、`Quantity` 和 rate。

### ST2-REQ-002：最小来源 manifest

每次导入只需保存一份小型 JSON manifest：

```json
{
  "schema": "tracequant-stage2-source-v1",
  "dataset_id": "btc-eth-research-r1",
  "nautilus_version": "2.0.0rc4",
  "sources": [
    {
      "path": "data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-08.zip",
      "source_url": "https://data.binance.vision/...",
      "sha256": "...",
      "checksum_url": "https://data.binance.vision/....CHECKSUM",
      "source_kind": "binance_public_data",
      "instrument_id": "BTCUSDT-PERP.BINANCE",
      "data_type": "bars",
      "start_ns": 0,
      "end_ns": 0,
      "rows": 0
    }
  ]
}
```

必要字段只有数据集身份、Nautilus 版本、来源位置、来源种类、官方 URL、checksum、类型、
标的、时间范围和行数。归档来源的 `path` 是相对于
`https://data.binance.vision/` 来源根的可移植路径，不得写入下载机器的绝对路径。Nautilus
尾部补数另记请求时间范围及其与归档的选择边界。不实现 schema registry、发布事务、对象
仓库、下载调度或自定义数据版本系统。

### ST2-REQ-003：输入校验

在调用 Nautilus writer 前必须拒绝：

- checksum 不匹配；
- 未经显式配置而尝试用 Nautilus request 替代归档；
- 尾部补数与已选 canonical 归档发生未解决的时间戳重叠；
- 未支持的 instrument、数据类型或 Bar interval；
- `ts_init < ts_event`；
- 主键重复；
- 同一序列时间倒序；
- OHLC 关系非法、负 volume 或非法数字；
- 数据超出 manifest 声明范围；
- funding 缺少能支持预期结算行为的时间语义。

按交易所日历判断“市场是否应有数据”不在通用导入器中实现。coverage report 只陈述输入实际
覆盖、相邻时间戳间隔、重复和缺口，由数据源合同决定允许的例外。

### ST2-REQ-004：Nautilus 原生转换与写入

Nautilus 集成边界必须提供薄转换器：

```text
Research rows -> list[Nautilus native data] -> Nautilus writer
```

写入规则：

- Instrument 使用 `ParquetDataCatalog.write_instruments`；
- Bar 使用 `write_bars`；
- mark 使用 `write_mark_price_updates`；
- 首版不写 index；后续数据集启用时才使用 `write_index_price_updates`；
- funding 使用 `StreamingFeatherWriter` 和 `convert_stream_to_data`；
- 不手工写 Nautilus Parquet；
- 不设置 `skip_disjoint_check=True`，除非独立 Issue 明确证明重叠写入是预期行为；
- 重跑到非空目标时 fail closed，不静默覆盖或合并。

所有 `nautilus_trader` import 继续只存在于 `tracequant.integrations.nautilus`。

### ST2-REQ-005：Research 只读视图

研究视图必须从 catalog 读取，而不是继续从导入前的 DataFrame 计算：

- Bar、mark：使用 catalog typed query 读取原生对象，再投影为 Polars DataFrame；
- funding：使用 `query_files` 定位 Nautilus 生成的 funding Parquet，再由 Polars 扫描；
- 投影只在内存中存在，不写回 catalog；
- 原始价格/数量保留精确字符串或 Decimal 语义；
- 特征、标签和模型产物允许写入 catalog 之外，但必须记录 `dataset_id` 和输入时间范围。

研究 API 首版只需支持：

```python
load_bars(catalog_path, bar_type, start, end) -> pl.DataFrame
load_mark_prices(catalog_path, instrument_id, start, end) -> pl.DataFrame
load_funding(catalog_path, instrument_id, start, end) -> pl.LazyFrame
```

### ST2-REQ-006：回测读取

阶段 2 回测必须使用 `BacktestDataConfig` / `BacktestNode` 读取相同 `catalog_path`。配置中的
instrument、Bar type、data type、start 和 end 必须与研究查询参数来自同一数据集配置。

不得为回测另行导出 CSV、研究 Parquet 或内存快照。

### ST2-REQ-007：时间切分

train、validation、test 只是对 catalog 查询的时间过滤，不生成三份市场数据：

```text
train:      [dataset_start, train_end)
validation: [train_end, validation_end)
test:       [validation_end, dataset_end)
```

必须拒绝边界倒置、重叠或超出数据集范围。特征计算只能使用决策时刻及以前的数据；阶段 2
只提供这一最小 guard，不建设通用 feature/label 框架。

## 5. 一致性定义与验收

“研究与回测一致”具体指以下四件事：

1. 两者使用同一个 `catalog_path`、instrument identity、data type、Bar type 和时间窗口；
2. 研究视图是对 catalog 原生数据的只读投影，不是导入前数据的持久化副本；
3. Bar 的价格、数量和时间戳在 source -> native -> catalog -> research round-trip 中精确
   相等；首个固定数据集对完整 funding 历史验证 source inventory、映射前字段，以及
   catalog、research 和 BacktestNode 的记录数与时间范围一致。Funding rate、interval、
   timestamp 的精确持久化与账户 exactly-once 语义由 `ST2-TEST-003`（Task #350）的代表性
   native round-trip 和持仓测试覆盖，不要求对完整历史逐事件重跑账户结算，也不以聚合余额
   变化替代 source completeness；
4. `BacktestNode` 实际加载的记录数和时间范围与研究查询一致。

必须有以下自动化测试：

### ST2-TEST-001：Bar round-trip

使用小型真实 fixture 导入 Instrument 和 Bar，随后从 catalog 查询。逐行断言
instrument、bar type、OHLCV、`ts_event`、`ts_init` 精确相等。

### ST2-TEST-002：研究/回测同源

同一 catalog 和窗口分别由研究 loader 与 `BacktestNode` 加载。断言行数、首尾时间戳和数据
身份一致，并断言配置未引用其他数据文件。

### ST2-TEST-003：Funding 原生 round-trip

用 `StreamingFeatherWriter` 写入至少两个原生 funding 事件，转换为 catalog Parquet：

- `query_files("funding_rate_update")` 能找到文件；
- Polars 能从同一文件读取精确 rate 和时间字段；
- `BacktestDataConfig(data_type="FundingRateUpdate")` 能加载相同事件数；
- 具备合法结算时间语义的事件在持仓场景中只结算一次。

### ST2-TEST-004：失败输入

至少覆盖 checksum 错误、重复、倒序、非法 OHLC、时间切分重叠和目标已存在。

### ST2-TEST-005：最小指标 parity

从 catalog 查询同一段 Bar：Polars 计算 SMA10/SMA20，Nautilus indicator 计算相同定义。
warm-up 后逐点比较；价格先按 instrument precision 量化，允许误差不超过一个 price tick。

不再做“direct 4h 对 15m 重新聚合 4h”的强制 parity。只要研究和回测都读取 catalog 内同一
个 4h Bar 序列，这种二次聚合不能增加同源性证明。

## 6. Coverage 输出

每次导入输出一个 JSON report，字段限定为：

```text
dataset_id
catalog_path
data_type
instrument_id
bar_type (适用时)
row_count
first_ts_event
last_ts_event
duplicate_count
out_of_order_count
gap_count
source_sha256
```

报告用于说明导入内容，不形成独立数据控制面。`duplicate_count` 或
`out_of_order_count` 非零时导入失败；`gap_count` 是否允许由该数据源的明确合同决定。

## 7. 建议实现边界

建议新增的产品模块保持很薄：

```text
src/tracequant/
  research/
    source_schema.py        # Polars 输入校验、coverage、时间切分
    views.py                # 对 Nautilus integration 返回值的研究投影
  integrations/nautilus/
    stage2_import.py        # rows -> Nautilus types -> catalog writers
    stage2_research.py      # catalog typed query / funding query_files
    stage2_backtest.py      # BacktestDataConfig 构造
```

具体命名应服从现有 Stage 1 代码结构；不要为了匹配本图引入无收益抽象。

## 8. 实施拆分

阶段 2 建议拆成四个叶子 Task：

1. **Bar 与 Instrument 导入**：输入 schema、最小 manifest、校验、Nautilus writer、coverage
   report 和 round-trip 测试；直接调用 Binance 官方下载工具，不自建下载器。
2. **研究只读视图**：从 catalog 生成 Polars 视图、时间切分和 SMA parity 测试。
3. **Mark / Funding 导入**：15m mark 使用 Nautilus 原生 writer；funding 使用 Feather
   conversion；完成 mark 陈旧度、funding 加载和一次结算测试；同时记录 index 来源覆盖结论，
   不导入 index。
4. **BTC/ETH 数据集验收**：导入 15m/1h/4h 数据，生成各序列 coverage，并运行研究/回测
   同源验收。

Index 只有在后续研究明确使用 basis/premium 特征时才进入新的数据集 revision，优先从 15m
开始；1m mark/index 只有在策略明确依赖分钟级参考价格变化时才增加。

## 9. 非目标

阶段 2 明确不实现：

- 自有 catalog、Parquet schema 或行情领域对象；
- 自有 Bar 聚合引擎、事件回放器、Portfolio、Account 或 funding accounting；
- custom funding data；
- raw data lake、schema registry、发布事务、scheduler 或 feature store；
- Binance public-data 已提供能力的重复下载器；
- DuckDB、MLflow、Optuna、Prefect、Dagster、Redis 或 PostgreSQL；
- 策略发现、参数搜索、模型训练、Demo 或 Live；
- 修改 Nautilus 生成的 catalog 文件；
- 将研究派生表写回 Nautilus catalog。

## 10. 阶段退出条件

以下条件全部满足时阶段 2 完成：

```text
[ ] BTCUSDT、ETHUSDT 的 15m/1h/4h Bar 已由 Nautilus 写入同一 catalog
[ ] Kline、15m mark 和 funding 长历史来自 Binance 官方 public-data 且官方 checksum 已验证
[ ] Nautilus request 只用于有记录的交叉验证或显式尾部补数，没有隐式 fallback
[ ] funding 已通过 Nautilus 原生 Feather -> Parquet 路径持久化并可由 BacktestNode 加载
[ ] 15m mark 已由 Nautilus 原生 writer 持久化，每个 funding 时点的 mark 年龄不超过 15 分钟
[ ] index 已完成官方来源、月度对象和 checksum 能力核查，但未写入首版 catalog
[ ] 每个序列有 checksum 和最小 coverage report
[ ] 重复、倒序、非法时间语义和不安全重跑会失败
[ ] 研究视图只从 Nautilus catalog 读取
[ ] train/validation/test 是不重叠的 catalog 时间视图
[ ] Bar 与 funding round-trip 测试通过
[ ] 研究 loader 与 BacktestNode 的数据身份、记录数和时间范围一致
[ ] SMA parity 在一个 price tick 阈值内
[ ] 未引入第二套持久化市场数据或交易领域状态
```
