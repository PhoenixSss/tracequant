# TraceQuant 产品重新校准与分级实现基线 v1

- **状态：** Research MVP 范围判断基线
- **基准日期：** 2026-09-11
- **适用范围：** TraceQuant 的产品、数据、研究、Shadow 与 Live 规划
- **明确豁免：** LCK 及仓库工程工作流不属于本次产品架构、代码删减或复杂度评估范围
- **不是：** 已实现能力清单、对现有 Feature 的重写，或进入 Live 的授权

## 1. 产品目的与当前判断

TraceQuant 的最终目的是建立一个**可审计的加密货币永续合约研究到实盘系统**。
“研究到实盘”表示同一套数据、时间、特征、策略、成本、风险与证据语义能够逐级受验；
它不表示现阶段应同时建造一套完整交易平台。

当前唯一产品主线是 **Research MVP**。Shadow 和 Live 只定义后续门禁与接口方向，
不因本基线而变成当前实现承诺。

### LCK 与工程工作流豁免

LCK、Delivery / Review / Closeout、工作区隔离、证据保存和其他仓库工程工作流，服务于软件交付治理，
不是 TraceQuant 量化产品的运行时模块。本基线不评价其必要性、实现规格或复杂度，也不授权删除、简化、
迁移或重规划相关代码、文档、测试、Issue 与 Project 配置。本文提到“跨根目录自动审计”时，仅禁止将这类
职责加入公共历史数据采集层；该表述不适用于 LCK 自身。

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

- **显式来源选择：** 调用方必须选定 archive 或 REST 及数据族；适配器据此确定官方 dataset/endpoint。
  采集层不自动回退、不比较来源优劣、不合并冲突结果。
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

- `KEEP`：存在当前 Research 调用方和可观察结果，必须维持该公共语义；
- `SIMPLIFY`：保留能力，但后续以更小内部结构或公共面实现；
- `INTERNALIZE`：仍可作为实现细节，但不应继续是 `tracequant.data` 公共契约；
- `REMOVE`：不具有当前或已批准后续职责，在完成依赖核验和兼容处理后删除；
- `DEFER`：只用于尚未实现的未来能力；已经存在的模块或导出不得用 `DEFER` 回避保留、收缩或删除决策。

处置采用“保留复杂度需要证据”的原则。仓库内只有测试直接从 `tracequant.data` facade 导入当前符号，
没有生产调用方证明 89 个符号构成稳定公共 API。因此测试覆盖或已有实现本身不是 `KEEP` 理由；
后续 Task 应先确定最小调用路径，再让测试跟随受支持行为，而不是让既有测试固化公共面。

### 5.1 实现模块

| 模块 | 处置 | 理由 |
|---|---|---|
| `tracequant.data.__init__` | `SIMPLIFY` | 保留高层 Research 入口，后续不再把 HTTP、parser、adapter 等低层 seam 全部上提。 |
| `tracequant.data.public_history` | `SIMPLIFY` | 保留显式来源、标的、数据类型和 UTC 区间；archive 对象与 REST 页细节不进入公共契约。 |
| `tracequant.data.raw_store` | `SIMPLIFY` | 不可变、manifest、原子发布和幂等必须保留，但不扩成全局证据/执行协议。 |
| `tracequant.data.binance_public_archive` | `SIMPLIFY` | 保留有限下载、checksum 和原子持久化；将通用适配 seam 收回内部。 |
| `tracequant.data.binance_contract_kline` | `SIMPLIFY` | 保留合约 Kline 获取能力，删除该数据族专属的 plan/result/status 公共层。 |
| `tracequant.data.binance_mark_price_kline` | `SIMPLIFY` | 保留标记价格获取能力，删除重复的 plan/result/status 公共层。 |
| `tracequant.data.binance_index_price_kline` | `SIMPLIFY` | 保留指数价格获取能力，删除重复的 plan/result/status 公共层。 |
| `tracequant.data.binance_funding_rate` | `SIMPLIFY` | 保留已结算资金费率获取能力，删除重复的 plan/result/status 公共层。 |
| `tracequant.data.public_history_rest` | `INTERNALIZE` | 页身份、游标和 provenance 是适配器与 manifest 的实现细节，不是调用方 API。 |
| `tracequant.data.binance_kline_rest` | `SIMPLIFY` | 保留显式、覆盖受限的 Kline REST 采集；内部化 HTTP/parser/generic-page seam。 |
| `tracequant.data.binance_funding_rate_rest` | `SIMPLIFY` | 保留显式 REST 获取能力并收敛为薄适配器；不得保留独立预算、覆盖和结果类型体系。 |
| `tracequant.data._binance_rest_http_worker` | `REMOVE` | 子进程 transport hard-kill 平台超出 Research 下载需要；使用受限超时与有限重试即可。 |

### 5.2 `tracequant.data` 公开导出

以基准日 `src/tracequant/data/__init__.py` 的 89 个 `__all__` 成员为完整集合。每行是一个导出，
因此每项只有一个处置结论。

| 公开导出 | 处置 | 简短理由 |
|---|---|---|
| `ArchiveHttpResponse` | `INTERNALIZE` | archive HTTP 传输 seam，不是研究语义。 |
| `BinanceArchiveAcquisitionOutcome` | `REMOVE` | 通用引擎 outcome 复制高层结果语义；最小入口直接返回 Raw 制品或明确失败。 |
| `BinanceArchiveAcquisitionStatus` | `REMOVE` | 不保留一套独立于调用结果和异常的通用状态机。 |
| `BinanceArchiveCoverageGapPlan` | `INTERNALIZE` | archive 对象枚举可在适配器内部完成，不作为调用方计划协议。 |
| `BinanceArchiveDatasetAdapter` | `REMOVE` | 当前数据族固定，不需要公开或通用化的插件式 adapter 协议。 |
| `BinanceArchiveObjectPlan` | `INTERNALIZE` | 单对象 URL 与区间是下载实现细节。 |
| `BinanceArchiveParseResult` | `REMOVE` | 解析中间结果不应形成长期模型层。 |
| `BinanceArchiveObjectBoundary` | `INTERNALIZE` | daily/monthly 对象边界由 archive 适配器从 UTC 请求区间确定。 |
| `BinanceArchiveObjectGranularity` | `INTERNALIZE` | 对象粒度属于 Binance archive 布局，不是产品调用参数。 |
| `BinanceContractKlineBackfill` | `SIMPLIFY` | 保留合约 Kline archive 获取能力，并收敛到统一薄入口。 |
| `BinanceContractKlineObjectResult` | `REMOVE` | 不为每个 archive 对象暴露专属结果类型。 |
| `BinanceContractKlineRunResult` | `REMOVE` | 批次结果由统一摘要、manifest 与失败返回表达。 |
| `BinanceContractKlineStatus` | `REMOVE` | 删除数据族专属状态枚举。 |
| `BinanceFundingRateBackfill` | `SIMPLIFY` | 保留 funding archive 获取能力，并收敛到统一薄入口。 |
| `BinanceFundingRateCoverageGapPlan` | `INTERNALIZE` | 月份枚举是 archive 适配器内部逻辑。 |
| `BinanceFundingRateCoverageStatus` | `REMOVE` | 来源覆盖判定不由采集层公共状态机拥有。 |
| `BinanceFundingRateObjectResult` | `REMOVE` | 不为每月对象暴露专属结果类型。 |
| `BinanceFundingRateRunResult` | `REMOVE` | 批次结果由统一摘要、manifest 与失败返回表达。 |
| `BinanceFundingRateStatus` | `REMOVE` | 删除数据族专属状态枚举。 |
| `BinanceFundingRateRestAcquisition` | `SIMPLIFY` | 保留显式 REST 获取能力，并收敛到统一薄入口。 |
| `BinanceFundingRateRestAttemptResult` | `REMOVE` | 重试尝试写入日志或统一摘要，不形成公共类型。 |
| `BinanceFundingRateRestBudget` | `INTERNALIZE` | 有限重试预算采用明确默认值或内部配置。 |
| `BinanceFundingRateRestCoverage` | `REMOVE` | 不在采集公共 API 中维护来源覆盖模型。 |
| `BinanceFundingRateRestCoverageStatus` | `REMOVE` | 覆盖决策由 canonical/data-quality 负责。 |
| `BinanceFundingRateRestPageResult` | `REMOVE` | REST 分页不暴露为产品结果层。 |
| `BinanceFundingRateRestRunResult` | `REMOVE` | 运行结果收敛为统一 Raw 获取摘要。 |
| `BinanceFundingRateRestStatus` | `REMOVE` | 删除 REST funding 专属状态枚举。 |
| `BinanceKlineInterval` | `KEEP` | 受支持的时间粒度是请求契约。 |
| `BinanceKlineRestAcquisition` | `SIMPLIFY` | 保留显式 Kline REST 获取能力，并收敛到统一薄入口。 |
| `BinanceKlineRestAttemptResult` | `REMOVE` | 重试可解释不要求公开逐次尝试类型。 |
| `BinanceKlineRestBudget` | `INTERNALIZE` | 页数、时间和响应大小限制是内部安全配置。 |
| `BinanceKlineRestCoverage` | `REMOVE` | 不把探测快照固化为采集层公共模型。 |
| `BinanceKlineRestCoverageStatus` | `REMOVE` | 来源覆盖决策由 canonical/data-quality 负责。 |
| `BinanceKlineRestHttpGet` | `INTERNALIZE` | HTTP 注入 seam 应留在实现/测试边界。 |
| `BinanceKlineRestHttpResponse` | `INTERNALIZE` | 传输层响应容器不是产品数据契约。 |
| `BinanceKlineRestPageResult` | `REMOVE` | 页级结果写入内部日志或统一摘要，不形成公共协议。 |
| `BinanceKlineRestRunResult` | `REMOVE` | 批次完整性由统一结果、manifest 与失败表达。 |
| `BinanceKlineRestStatus` | `REMOVE` | 删除 Kline REST 专属状态枚举。 |
| `BinanceRestPageAcquisition` | `REMOVE` | 不保留面向未来数据族的通用分页编排平台。 |
| `BinanceRestPageAdapter` | `REMOVE` | 当前固定数据族不需要插件式 adapter 协议。 |
| `BinanceRestPageParseError` | `INTERNALIZE` | 解析细节转换为稳定的高层失败，不形成公共异常协议。 |
| `BinanceRestPageParsed` | `REMOVE` | parser 中间容器无需独立模型。 |
| `BinanceIndexPriceKlineBackfill` | `SIMPLIFY` | 保留指数价格 archive 获取能力，并收敛到统一薄入口。 |
| `BinanceIndexPriceKlineCoverageGapPlan` | `INTERNALIZE` | 对象枚举与缺口计算留在适配器内部。 |
| `BinanceIndexPriceKlineObjectResult` | `REMOVE` | 不为每个对象暴露专属结果类型。 |
| `BinanceIndexPriceKlineRunResult` | `REMOVE` | 批次结果由统一摘要、manifest 与失败表达。 |
| `BinanceIndexPriceKlineStatus` | `REMOVE` | 删除数据族专属状态枚举。 |
| `BinanceMarkPriceKlineBackfill` | `SIMPLIFY` | 保留标记价格 archive 获取能力，并收敛到统一薄入口。 |
| `BinanceMarkPriceKlineCoverageGapPlan` | `INTERNALIZE` | 对象枚举与缺口计算留在适配器内部。 |
| `BinanceMarkPriceKlineObjectResult` | `REMOVE` | 不为每个对象暴露专属结果类型。 |
| `BinanceMarkPriceKlineRunResult` | `REMOVE` | 批次结果由统一摘要、manifest 与失败表达。 |
| `BinanceMarkPriceKlineStatus` | `REMOVE` | 删除数据族专属状态枚举。 |
| `BinanceMarket` | `KEEP` | 市场类型是来源身份的显式部分。 |
| `BinancePriceIndexId` | `KEEP` | 非可交易指数不得与 `InstrumentId` 混用。 |
| `BinanceRestEndpoint` | `INTERNALIZE` | endpoint 由显式 source 与 data type 确定，不要求调用方理解交易所路由。 |
| `BinanceRestPageIdentity` | `INTERNALIZE` | 页身份用于内部幂等和 manifest。 |
| `BinanceRestPageProvenance` | `INTERNALIZE` | 响应摘要与受限 headers 写入 manifest，不作为调用类型。 |
| `BinanceRestPageRequest` | `INTERNALIZE` | 页请求由适配器从高层 UTC 区间生成。 |
| `BinanceRestRequestBounds` | `INTERNALIZE` | 页大小和响应限制是适配器安全边界。 |
| `BinancePublicHistoryDataType` | `KEEP` | 数据类型是来源和存储身份的必需维度。 |
| `BinancePublicHistoryRequest` | `KEEP` | 调用方显式选源的核心请求契约。 |
| `BinancePublicHistorySourceIdentity` | `INTERNALIZE` | 来源身份必须进入 manifest，但调用方只需显式选择 source kind。 |
| `BinancePublicHistorySourceKind` | `KEEP` | archive/REST 类型必须显式。 |
| `BinancePublicHistorySubjectKind` | `INTERNALIZE` | subject 类型可从高层请求的数据族和标的确定。 |
| `BinancePublicArchiveAcquisition` | `REMOVE` | 不保留通用 archive 编排平台；薄适配器共享小型内部函数即可。 |
| `PublicHistoryContractError` | `KEEP` | 请求/身份违约需要稳定、可区分的错误。 |
| `RawArtifact` | `KEEP` | 读取方需要绑定路径、manifest 和修订身份的结果。 |
| `RawArtifactAmbiguousError` | `KEEP` | 多修订不得静默选择。 |
| `RawArtifactConflictError` | `KEEP` | 同一身份的内容冲突必须显式失败。 |
| `RawArtifactIncompleteError` | `KEEP` | 未原子完成的制品不得当作可用数据。 |
| `RawArtifactNotFoundError` | `KEEP` | 未找到与不完整/冲突需可区分。 |
| `RawArtifactValidationError` | `KEEP` | checksum/manifest/content 校验失败不得降级。 |
| `RawAcquisitionManifest` | `INTERNALIZE` | 采集响应细节持久化，但调用方读取统一 `RawManifest`。 |
| `RawAcquisitionResponse` | `REMOVE` | HTTP 响应可直接转换为内部 manifest 数据，不需要独立公共模型。 |
| `RawManifest` | `KEEP` | 发布后的身份、schema、覆盖与摘要是 Raw 真相。 |
| `RawObjectIdentity` | `KEEP` | 作为 Raw reference 的逻辑对象部分，支持确定性定位。 |
| `RawRevisionEvidenceKind` | `INTERNALIZE` | 证据种类必须持久化，但不要求调用方构造。 |
| `RawRevisionIdentity` | `KEEP` | 内容变化不覆写旧 Raw，需要可选的精确修订。 |
| `RawRestPageSourceObject` | `REMOVE` | REST 页到 Raw 的中间写入对象不形成长期模型。 |
| `RawSourceProvenance` | `INTERNALIZE` | 来源事实保留在 manifest，不要求调用方直接构造。 |
| `RawSourceObject` | `REMOVE` | archive parser 到 store 的中间对象不形成长期模型。 |
| `RawStore` | `SIMPLIFY` | 保留不可变发布、验证与精确重开，缩减写入模型和公共方法。 |
| `RawStoreError` | `KEEP` | Raw 存储失败的稳定基类。 |
| `plan_binance_contract_kline_archives` | `INTERNALIZE` | 调用方提交 UTC 区间，适配器内部确定官方对象列表。 |
| `plan_binance_funding_rate_archives` | `INTERNALIZE` | 调用方不消费独立规划阶段。 |
| `plan_binance_index_price_kline_archives` | `INTERNALIZE` | 调用方不消费独立规划阶段。 |
| `plan_binance_mark_price_kline_archives` | `INTERNALIZE` | 调用方不消费独立规划阶段。 |
| `next_binance_funding_cursor` | `INTERNALIZE` | funding 分页游标算法是 REST 实现细节。 |
| `next_binance_kline_cursor` | `INTERNALIZE` | Kline 分页游标算法是 REST 实现细节。 |

处置后的方向不是维护 89 个符号的兼容壳，而是删除重复的状态、计划、页、对象和运行结果体系，
内部化交易所布局与传输细节，只保留能够组成下述最小调用路径的公共语义。对仓库外调用方不作无证据假设；
需要保留兼容时，必须在代码收敛 Task 中指出具体调用方和迁移期限。

### 5.3 目标公共接口规格

`tracequant.data` facade 最终只服务两条调用路径：

1. `fetch`：输入显式 source kind、data type、subject 和 UTC range，返回已验证 Raw reference 与一个统一摘要；
2. `open/verify`：输入精确 Raw reference，校验 manifest/content 后返回 Raw artifact。

公共面可以包含完成上述路径所必需的请求枚举、Raw reference/artifact/manifest 和稳定错误；不得继续按
数据族、archive 对象或 REST 页分别公开 plan、budget、attempt、coverage、object result、page result、
run result、status、HTTP response、parser 或 adapter 类型。实现可以使用小型私有函数，但不得以兼容层、
重命名或新的统一框架恢复同等复杂度。

## 6. 当前阶段的保留、延期与拒绝清单

### 必须保留

- timezone-aware UTC、原始数据不可变、checksum/响应 digest、manifest、原子发布与幂等重跑；
- 显式来源、标的、UTC 区间，以及能报告最终覆盖、失败原因和重试次数的统一摘要；
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
这些后续变更不得把 LCK 或仓库工程工作流纳入产品代码删减范围。
