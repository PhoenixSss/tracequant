# TraceQuant 产品重新校准与分级实现基线 v1

- **状态：** Research MVP 范围判断基线
- **基准日期：** 2026-09-11
- **适用范围：** TraceQuant 的产品、数据、研究、Shadow 与 Live 规划
- **不是：** 已实现能力清单、对现有 Feature 的重写，或进入 Live 的授权

## 1. 产品目的与当前判断

TraceQuant 的最终目的是建立一个**可审计的加密货币永续合约研究到实盘系统**。
“研究到实盘”表示同一套数据、时间、特征、策略、成本、风险与证据语义能够逐级受验；
它不表示现阶段应同时建造一套完整交易平台。

当前唯一产品主线是 **Research MVP**。Shadow 和 Live 只定义后续门禁与接口方向，
不因本基线而变成当前实现承诺。

### Research MVP 成功标准

Research MVP 成功当且仅当能产生一个可独立复核的离线研究闭环：

1. 从显式选定的公共历史来源获取数据，以 UTC、不可变 Raw 制品和可验证 manifest 复现输入。
2. 将 Raw 数据确定性转换为 canonical 数据，报告重复、缺失、乱序、覆盖与质量决策，不静默修复。
3. 生成无未来泄漏、可版本化的特征、标签与时序切分，预处理只拟合训练数据。
4. 对可解释基线策略先做向量化筛选，再做事件驱动验真；同时报告 gross/net，并纳入手续费、
   资金费率、滑点、换手和非完全成交假设。
5. 使用带 purging 与 embargo 的 chronological walk-forward 验证，最终测试集不用于调参或选阈值。
6. 产出包含数据区间、数据指纹、参数、代码版本、成本假设、局限与样本外结果的 Research Evidence Bundle，
   并给出“进入 Shadow”、“继续研究”或“停止”的明确建议。

未满足上述闭环时，不以模块数量、抽象层数量、模型复杂度或运行时基础设施规模作为产品进展。

## 2. Research / Shadow / Live 分级规格

| 能力边界 | Research（当前） | Shadow（Research 门禁后） | Live（Shadow 门禁后） |
|---|---|---|---|
| 数据来源 | 显式选定的 Binance USDⓈ-M 公共历史数据；可重现、无凭据 | 实时公共流与历史回填；检测 gap 并可回放 | 与 Shadow 同语义，加生产级时效、可用性与审计保留 |
| Raw 与 canonical | 不可变 Raw；确定性 canonical 与质量报告 | 批/流一致，有 journal/checkpoint 和恢复语义 | 受控发布、保留期、运维 SLO；原始事实仍不可变 |
| 特征、标签、模型 | point-in-time 安全数据集与离线复现 | 在线/离线一致性、版本化信号，不产生真实订单 | 只加载人工批准的不可变 release，可回滚 |
| 策略与回测 | 规则基线、受控候选生成、成本感知的样本外验证 | 信号旁路运行，比较离线与在线偏差 | 策略只产生意图；不能越过风险和执行权限 |
| 组合、订单、账户 | 只有回测状态，无私有 API | Shadow 虚拟账户与 Demo 订单语义；状态可对账恢复 | 小额固定资金；幂等提交、UNKNOWN 先对账、真相账本 |
| 风险权限 | 研究约束和假设检查 | 风险模块可拒绝/缩减；HALT/REDUCE_ONLY/Kill Switch 演练 | 风险保持最终裁决权；本地/交易所状态不一致时停止开仓 |
| 运行、部署、可观测 | 本地可重现命令与版本化制品 | 单机受控运行，liveness/readiness、告警和 Runbook | 凭据/环境隔离，生产 preflight、日终审计、故障恢复演练 |
| 阶段输出 | Research Evidence Bundle 和 Shadow 建议 | 实时/执行/风险/恢复证据和 Live 建议 | 受限实盘证据；不自动扩大资金或范围 |

后一级只能复用前一级已验证的语义，不能用未实现的未来接口反向增加当前 Research 复杂度。

## 3. 模块边界与建造决策

| 模块 | 责任 | 输入 → 输出 | Research 决策 | 后续决策 |
|---|---|---|---|---|
| 公共历史采集 | 从调用方显式选定的公共来源获取字节并记录来源 | 来源、标的、UTC 区间 → 经校验的 Raw 对象与结果 | **自建最小适配器**，因为来源语义和审计是核心 | 实时/私有接口延期到相应阶段 |
| Raw 存储 | 不可变保存下载事实、checksum/响应摘要与 manifest | 采集响应 → 原子发布的制品及标识 | **保留并简化**，不升级为全局账本 | Shadow/Live 的运行真相不复用 Raw store 充当 |
| canonical / data quality | 标准化 schema，检测缺失/重复/乱序，比较重叠来源，产生品质决策 | 一个或多个 Raw 来源 → canonical 制品与质量报告 | **下一个必需自建边界**；在对应 Feature 前不塞入采集层 | Shadow 增加流式一致与恢复 |
| 查询与计算 | 读取 Parquet，做列式变换与可复现查询 | canonical 制品 → 确定性表/数据集 | **复用 Polars/DuckDB**，不自建数据库或调度平台 | 只在真实并发/运维需求出现时升级 |
| 特征、标签、切分 | 维护 point-in-time 语义、数据集 manifest 和泄漏检查 | canonical 数据+定义 → 版本化研究数据集 | **自建语义，复用列式计算** | 在线计算延期到 Shadow |
| 回测与策略验证 | 快速筛选后进行订单级验真 | 数据集+策略+成本假设 → 绩效与差异报告 | **复用成熟引擎**；只自建 TraceQuant 的语义和适配 | 不把回测引擎演化为交易网关 |
| 实验与制品 | 绑定数据、参数、代码和结果 | 一次研究运行 → 可复核证据包 | **复用轻量跟踪工具**，先满足最小证据契约 | 模型发布/审批延期到 Live 门禁 |
| 实时、执行、账户与对账 | 只在非研究阶段维护运行事实与安全状态 | 信号/账户/交易所事实 → 受风险裁决的状态转移 | **DEFER** | Shadow 先实现虚拟/Demo；Live 再实现小额真实执行 |
| 风险与安全 | 与策略独立，对订单意图有最终拒绝/缩减权 | 状态+意图+限额 → 许可/缩减/拒绝 | 只定义研究限制，运行时引擎 **DEFER** | Shadow 建立并演练；Live 不降级其权限 |

**自建**只适用于 TraceQuant 必须拥有的产品语义、安全裁决和审计边界。通用存储、计算、回测、
实验跟踪和可观测优先复用经验证的组件，但外部工具不代替 TraceQuant 对时间、风险和证据语义的责任。

## 4. Binance 公共历史数据的 Research-grade 规格

当前公共历史采集层只负责 **Research-grade acquisition**，必须同时满足：

- **显式来源选择：** 调用方必须选定 archive 或 REST 及具体 endpoint/dataset；采集层不自动回退、
  不比较来源优劣、不合并冲突结果。
- **UTC：** 请求边界、对象覆盖和 manifest 使用 timezone-aware UTC；无时区时间不得进入领域或存储边界。
- **Raw 不可变：** 已发布的原始字节、来源事实和修订标识不覆写；同一身份的不同内容是显式冲突或新修订。
- **checksum 和 manifest：** archive 验证上游 checksum 与压缩包内容；REST 保存响应 digest 和必要来源事实；
  manifest 绑定身份、覆盖、内容摘要和状态。
- **原子发布：** 先完成下载、解析、校验和临时写入，再使制品与 manifest 以一个完整单元可见；
  失败不得暴露为完整数据。
- **幂等重跑：** 相同请求与相同内容可重用已验证制品；冲突、不完整与未知状态必须显式返回。
- **有限且可解释的重试：** 只对列明的短暂故障在固定预算内重试，记录尝试结果，
  遵循可校验的 `Retry-After`；不做无界重试或猜测性 URL/endpoint 切换。

“显式来源选择”是采集请求的输入要求，不是采集层的自动策略权。当多来源可用时，
**来源优先级与选用规则、重叠区间比较、数据质量判定、冲突报告和 canonical 发布**由后续
canonical / data-quality Feature 负责。Raw 采集层只陈述发生了什么，不判定哪份数据代表研究真相。

以下能力**不属于当前公共历史采集层**：

- 全局执行账本；
- evidence-bound planner；
- obligation protocol；
- 自动来源回退和冲突裁决；
- 跨根目录自动审计。

前三者是不同生命周期/证据边界的能力，不得以“方便重跑采集”为由加入该层；
后两者属于 canonical/data-quality 决策或仓库工作流，不属于数据下载。

## 5. `tracequant.data` 模块处置表

本节是后续规划与删减的决策输入，**不授权在本 Issue 中更改运行时代码**。处置含义如下：

- `KEEP`：当前阶段需要该语义与支持路径；
- `SIMPLIFY`：保留能力，但后续以更小内部结构或公共面实现；
- `INTERNALIZE`：仍可作为实现细节，但不应继续是 `tracequant.data` 公共契约；
- `REMOVE`：不具有当前或已批准后续职责，在完成依赖核验和兼容处理后删除；
- `DEFER`：冻结扩展，直到对应阶段需求与验证路径被批准。

### 5.1 实现模块

| 模块 | 处置 | 理由 |
|---|---|---|
| `tracequant.data.__init__` | `SIMPLIFY` | 保留高层 Research 入口，后续不再把 HTTP、parser、adapter 等低层 seam 全部上提。 |
| `tracequant.data.public_history` | `KEEP` | 显式来源、标的、数据类型、区间与序列化是可审计采集契约。 |
| `tracequant.data.raw_store` | `SIMPLIFY` | 不可变、manifest、原子发布和幂等必须保留，但不扩成全局证据/执行协议。 |
| `tracequant.data.binance_public_archive` | `SIMPLIFY` | 保留有限下载、checksum 和原子持久化；将通用适配 seam 收回内部。 |
| `tracequant.data.binance_contract_kline` | `KEEP` | 合约 Kline 是 Research MVP 的基础价格/成交量输入。 |
| `tracequant.data.binance_mark_price_kline` | `KEEP` | 标记价格是永续合约成本、风险与研究验证的必要公共输入。 |
| `tracequant.data.binance_index_price_kline` | `KEEP` | 指数价格用于解释标记价格与基础市场变动。 |
| `tracequant.data.binance_funding_rate` | `KEEP` | 已结算资金费率是 net performance 不可缺少的成本事实。 |
| `tracequant.data.public_history_rest` | `KEEP` | 显式 REST 请求、页身份和来源事实可支持受控的 Research 来源，但不拥有自动选源权。 |
| `tracequant.data.binance_kline_rest` | `SIMPLIFY` | 保留显式、覆盖受限的 Kline REST 采集；内部化 HTTP/parser/generic-page seam。 |
| `tracequant.data.binance_funding_rate_rest` | `DEFER` | 已结算月度 archive 是当前基线；REST 路径不作默认来源，在 canonical 对时效/覆盖有可验证需求前冻结扩展。 |
| `tracequant.data._binance_rest_http_worker` | `INTERNALIZE` | 子进程 HTTP worker 是超时/隔离实现细节，不是产品接口。 |

### 5.2 `tracequant.data` 公开导出

以基准日 `src/tracequant/data/__init__.py` 的 89 个 `__all__` 成员为完整集合。每行是一个导出，
因此每项只有一个处置结论。

| 公开导出 | 处置 | 简短理由 |
|---|---|---|
| `ArchiveHttpResponse` | `INTERNALIZE` | archive HTTP 测试/传输 seam，不是研究语义。 |
| `BinanceArchiveAcquisitionOutcome` | `INTERNALIZE` | 通用 archive 引擎结果；调用方应消费具体数据集结果。 |
| `BinanceArchiveAcquisitionStatus` | `INTERNALIZE` | 通用 archive 引擎状态，由数据集状态封装。 |
| `BinanceArchiveCoverageGapPlan` | `KEEP` | 显式表达合约 Kline archive 覆盖缺口。 |
| `BinanceArchiveDatasetAdapter` | `INTERNALIZE` | parser/persistence 适配协议是内部扩展 seam。 |
| `BinanceArchiveObjectPlan` | `INTERNALIZE` | 通用单对象下载计划是采集引擎细节。 |
| `BinanceArchiveParseResult` | `INTERNALIZE` | 解析器与存储间的中间结果。 |
| `BinanceArchiveObjectBoundary` | `KEEP` | archive 对象的 UTC 覆盖是请求与 manifest 的审计边界。 |
| `BinanceArchiveObjectGranularity` | `KEEP` | daily/monthly 是显式来源身份的一部分。 |
| `BinanceContractKlineBackfill` | `KEEP` | 合约 Kline archive 的高层 Research 入口。 |
| `BinanceContractKlineObjectResult` | `KEEP` | 提供逐对象完成/缺口/失败可见性。 |
| `BinanceContractKlineRunResult` | `KEEP` | 批次是否完整不得由调用方猜测。 |
| `BinanceContractKlineStatus` | `KEEP` | 合约 Kline 结果状态是公开失败语义。 |
| `BinanceFundingRateBackfill` | `KEEP` | 已结算 funding archive 的高层 Research 入口。 |
| `BinanceFundingRateCoverageGapPlan` | `KEEP` | 显式表达 funding archive 覆盖缺口。 |
| `BinanceFundingRateCoverageStatus` | `KEEP` | 覆盖是否足以研究需显式表达。 |
| `BinanceFundingRateObjectResult` | `KEEP` | 保留逐月对象的可审计结果。 |
| `BinanceFundingRateRunResult` | `KEEP` | 保留 funding 批次完整性。 |
| `BinanceFundingRateStatus` | `KEEP` | funding archive 失败/不完整状态不得隐藏。 |
| `BinanceFundingRateRestAcquisition` | `DEFER` | 冻结 REST funding 高层入口，等待 canonical 的明确覆盖/时效需求。 |
| `BinanceFundingRateRestAttemptResult` | `DEFER` | 随 REST funding 路径一并冻结。 |
| `BinanceFundingRateRestBudget` | `DEFER` | 随 REST funding 路径一并冻结，不扩展新预算策略。 |
| `BinanceFundingRateRestCoverage` | `DEFER` | 覆盖契约等待后续来源决策。 |
| `BinanceFundingRateRestCoverageStatus` | `DEFER` | 覆盖状态等待后续来源决策。 |
| `BinanceFundingRateRestPageResult` | `DEFER` | 页结果不在当前 archive 基线上扩展。 |
| `BinanceFundingRateRestRunResult` | `DEFER` | 运行结果随 REST funding 路径一并冻结。 |
| `BinanceFundingRateRestStatus` | `DEFER` | 状态集随 REST funding 路径一并冻结。 |
| `BinanceKlineInterval` | `KEEP` | 受支持的时间粒度是请求契约。 |
| `BinanceKlineRestAcquisition` | `KEEP` | 显式、覆盖受限的 Kline REST Research 入口。 |
| `BinanceKlineRestAttemptResult` | `KEEP` | 有限重试必须可解释。 |
| `BinanceKlineRestBudget` | `KEEP` | 请求/页/时间预算使采集有界。 |
| `BinanceKlineRestCoverage` | `KEEP` | REST 不能在无覆盖证据时自行扩展。 |
| `BinanceKlineRestCoverageStatus` | `KEEP` | 覆盖决策需可见。 |
| `BinanceKlineRestHttpGet` | `INTERNALIZE` | HTTP 注入 seam 应留在实现/测试边界。 |
| `BinanceKlineRestHttpResponse` | `INTERNALIZE` | 传输层响应容器不是产品数据契约。 |
| `BinanceKlineRestPageResult` | `KEEP` | 逐页完成/失败与 Raw 修订必须可见。 |
| `BinanceKlineRestRunResult` | `KEEP` | 批次完整性与未满足区间必须可见。 |
| `BinanceKlineRestStatus` | `KEEP` | 有界采集的终止原因是公开语义。 |
| `BinanceRestPageAcquisition` | `INTERNALIZE` | 通用分页编排基类不应是产品 API。 |
| `BinanceRestPageAdapter` | `INTERNALIZE` | 数据集 parser/identity 适配协议是内部 seam。 |
| `BinanceRestPageParseError` | `INTERNALIZE` | 解析细节由高层结果状态对外表达。 |
| `BinanceRestPageParsed` | `INTERNALIZE` | parser 中间容器不是调用方输出。 |
| `BinanceIndexPriceKlineBackfill` | `KEEP` | 指数价格 archive 的高层 Research 入口。 |
| `BinanceIndexPriceKlineCoverageGapPlan` | `KEEP` | 明确表达已知覆盖边界与缺口。 |
| `BinanceIndexPriceKlineObjectResult` | `KEEP` | 保留逐对象结果和不完整证据。 |
| `BinanceIndexPriceKlineRunResult` | `KEEP` | 保留批次完整性。 |
| `BinanceIndexPriceKlineStatus` | `KEEP` | 指数价格采集状态是公开失败语义。 |
| `BinanceMarkPriceKlineBackfill` | `KEEP` | 标记价格 archive 的高层 Research 入口。 |
| `BinanceMarkPriceKlineCoverageGapPlan` | `KEEP` | 明确表达已知覆盖边界与缺口。 |
| `BinanceMarkPriceKlineObjectResult` | `KEEP` | 保留逐对象结果和不完整证据。 |
| `BinanceMarkPriceKlineRunResult` | `KEEP` | 保留批次完整性。 |
| `BinanceMarkPriceKlineStatus` | `KEEP` | 标记价格采集状态是公开失败语义。 |
| `BinanceMarket` | `KEEP` | 市场类型是来源身份的显式部分。 |
| `BinancePriceIndexId` | `KEEP` | 非可交易指数不得与 `InstrumentId` 混用。 |
| `BinanceRestEndpoint` | `KEEP` | REST endpoint 必须显式选定。 |
| `BinanceRestPageIdentity` | `KEEP` | 分页请求身份支持幂等 Raw 修订。 |
| `BinanceRestPageProvenance` | `KEEP` | 响应摘要与受限 headers 是来源审计事实。 |
| `BinanceRestPageRequest` | `KEEP` | 显式页请求是有界 REST 采集契约。 |
| `BinanceRestRequestBounds` | `KEEP` | 每页和区间边界防止无界获取。 |
| `BinancePublicHistoryDataType` | `KEEP` | 数据类型是来源和存储身份的必需维度。 |
| `BinancePublicHistoryRequest` | `KEEP` | 调用方显式选源的核心请求契约。 |
| `BinancePublicHistorySourceIdentity` | `KEEP` | 使 archive/REST 来源可追溯且不被 canonical 决策覆盖。 |
| `BinancePublicHistorySourceKind` | `KEEP` | archive/REST 类型必须显式。 |
| `BinancePublicHistorySubjectKind` | `KEEP` | instrument/price index 的语义边界必须显式。 |
| `BinancePublicArchiveAcquisition` | `INTERNALIZE` | 具体 backfill 是公开入口，通用 archive 引擎不需对调用方暴露。 |
| `PublicHistoryContractError` | `KEEP` | 请求/身份违约需要稳定、可区分的错误。 |
| `RawArtifact` | `KEEP` | 读取方需要绑定路径、manifest 和修订身份的结果。 |
| `RawArtifactAmbiguousError` | `KEEP` | 多修订不得静默选择。 |
| `RawArtifactConflictError` | `KEEP` | 同一身份的内容冲突必须显式失败。 |
| `RawArtifactIncompleteError` | `KEEP` | 未原子完成的制品不得当作可用数据。 |
| `RawArtifactNotFoundError` | `KEEP` | 未找到与不完整/冲突需可区分。 |
| `RawArtifactValidationError` | `KEEP` | checksum/manifest/content 校验失败不得降级。 |
| `RawAcquisitionManifest` | `KEEP` | 采集请求、响应与内容摘要的持久审计契约。 |
| `RawAcquisitionResponse` | `INTERNALIZE` | 临时响应组装容器是采集器到 store 的内部 seam。 |
| `RawManifest` | `KEEP` | 发布后的身份、schema、覆盖与摘要是 Raw 真相。 |
| `RawObjectIdentity` | `KEEP` | 确定性定位一个 Raw 逻辑对象。 |
| `RawRevisionEvidenceKind` | `KEEP` | 区分上游 checksum 与 REST 响应 digest，不冒充等价证据。 |
| `RawRevisionIdentity` | `KEEP` | 内容变化不覆写旧 Raw，需要可选的精确修订。 |
| `RawRestPageSourceObject` | `INTERNALIZE` | REST 页到 Raw 写入的中间容器。 |
| `RawSourceProvenance` | `KEEP` | 来源 URL、对象与摘要是长期审计事实。 |
| `RawSourceObject` | `INTERNALIZE` | archive parser 到 Raw store 的中间写入容器。 |
| `RawStore` | `KEEP` | 不可变、原子、幂等 Raw 存取的高层入口。 |
| `RawStoreError` | `KEEP` | Raw 存储失败的稳定基类。 |
| `plan_binance_contract_kline_archives` | `KEEP` | 先显式规划对象/缺口，不猜测 URL。 |
| `plan_binance_funding_rate_archives` | `KEEP` | 先显式规划已结算 funding 对象/缺口。 |
| `plan_binance_index_price_kline_archives` | `KEEP` | 先显式规划指数价格对象/缺口。 |
| `plan_binance_mark_price_kline_archives` | `KEEP` | 先显式规划标记价格对象/缺口。 |
| `next_binance_funding_cursor` | `INTERNALIZE` | funding 分页游标算法是 REST 实现细节。 |
| `next_binance_kline_cursor` | `INTERNALIZE` | Kline 分页游标算法是 REST 实现细节。 |

本表中没有任何当前导出被标为 `REMOVE`。这表示基准还没有足够证据判定它们“无任何职责”；
不表示它们都应继续公开。`INTERNALIZE` 和 `SIMPLIFY` 已给出下一步收缩方向，具体兼容和删减必须由后续
Issue 核对实际依赖后实施。

## 6. 当前阶段的保留、延期与拒绝清单

### 必须保留

- timezone-aware UTC、原始数据不可变、checksum/响应 digest、manifest、原子发布与幂等重跑；
- 显式来源、标的、区间、覆盖缺口、部分失败和有界重试结果；
- canonical/data-quality 与 Raw 采集分层；
- point-in-time 安全、按时间顺序的研究验证和完整成本假设；
- 可复现证据包和人工阶段决策。

### 延期，直到有对应阶段证据

- 实时事件管线、Shadow/Demo 订单、账户、对账恢复与运行时风险引擎；
- 私有 API、实盘凭据、生产发布、真相账本和长期运维能力；
- 辅助数据、L2/L3、多交易所、资金放量、HA/DR 与节点拓扑；
- 公共历史来源的自动选择/回退，以及 REST funding 作为默认研究来源。

### 当前直接拒绝

- 默认或自动启用 Live，测试中提交真实订单，取款功能，或绕过风险最终裁决权；
- 在未对账 UNKNOWN 订单或本地/交易所状态不一致时继续开仓；
- 在采集层建全局执行账本、evidence-bound planner、obligation protocol、自动冲突裁决或跨根审计；
- 无批准架构 Issue 的微服务、Kubernetes、分布式消息队列、多交易所抽象、高频做市或强化学习；
- 将缺失值静默转为零、对原始数据就地修改、在最终测试集调参，或假设所有限价单完全成交。

## 7. 新需求的准入、延期和拒绝规则

一项新需求只有在以下条件全部满足时才准入当前 Research 阶段：

1. 它直接关闭本文 Research MVP 成功标准中的一个可指明缺口。
2. 它有可观测结果和可自动化或可复核的验证方法，而不只是未来可能有用。
3. 它位于正确责任边界，不把 canonical、策略、风险、执行或工作流职责塞入数据采集。
4. 它是满足目标的最小完整变更，不以通用平台、全局抽象或未来阶段接口作为前置。
5. 它符合时间、数据、风险、凭据和人工授权不变式。

如果需求有价值，但只对 Shadow、Live 或 Post-MVP 的门禁产生可验证结果，则标记为对应阶段的
`DEFER`，不为它预建骨架。如果需求触发上节拒绝条件、重复已有责任权威、无法验证，或扩张范围但无安全迁移路径，
则拒绝进入当前计划。

**任何新增复杂度都必须由可验证的当前需求，或一个明确批准该复杂度与迁移/失败边界的架构决策支持。**
“以后会需要”、“更通用”、“更像平台”或已有代码的沉没成本，都不构成支持。

## 8. 使用与后续变更

新的 Research MVP 规划、范围收缩和代码删减应引用本基线，但仍须在各自 Issue 中给出具体对象、兼容边界和验证。
本文不直接改变现有 GitHub Issue/PR/Project 状态，也不以表格中的未来能力作为已实现事实。
