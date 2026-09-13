# TraceQuant Trading Runtime Read-Only Review

审计日期：2026-09-12（Asia/Shanghai）  
审计方式：官方仓库固定版本、只读源码审查、官方现成测试/示例/工具运行；没有修改候选项目源码，没有编写 PoC、适配器、测试桩或补丁。

## 1. Executive Verdict

**结论等级：`NAUTILUS_PRIMARY`。**

NautilusTrader v2 仍应作为 TraceQuant 的首选交易运行时，但结论的准确含义是：**立即用于研究、回测和随后较长时间的 Binance Demo 观察；实盘准入保持条件式关闭**。用户已明确项目开发本来需要时间，也会留出模拟盘观察期，因此 `v2.0.0rc4` 的 RC 身份不是“现在不能启动项目”的否决项；它是版本冻结、升级审查和实盘门禁项。

本次实测支持这一选择：Nautilus 官方 wheel 与所审计源码为同一提交，选定的官方 Python 测试最终得到 **318 passed、6 skipped、0 failed**，官方合成数据回测完整跑通订单、持仓、保证金、费用和 PnL。LEAN 官方源码与 Binance 插件均完整编译成功，官方 C# 回测也跑通；因此 LEAN 绝不是“不成熟”。真正拉开差距的是 Binance USDⓈ-M 的交易所语义：Nautilus 源码直接实现 GTX post-only、reduce-only、one-way/hedge、逐符号杠杆与 cross/isolated、mark/index/funding、原生改单和专门的对账路径；LEAN 插件当前仍把 Futures post-only 编成 Binance 不接受的 `LIMIT_MAKER`，且没有下单侧 `reduceOnly`/`positionSide`、交易所杠杆/保证金模式控制或原生改单。

同时，Nautilus **尚未获得实盘证明**：本机没有 Rust 工具链，未执行其 Rust Binance adapter 测试；无 Demo 凭据，未运行真实 USD-M 订单生命周期；且 2026-08 仍有一个 warm-restart NETTING 对账的公开未关闭缺陷。因此本报告选择的是开发期主运行时，不是宣称生产就绪。

## 2. Input Baseline

| 项目 | 审计输入 |
| --- | --- |
| TraceQuant 根目录 | `D:/workspace/program/基底选型` |
| 根目录版本身份 | 不是 Git 仓库；无 branch、HEAD 或 working-tree 状态 |
| 已完整阅读的根报告 | `TraceQuant 开源技术栈与自研边界深度研究.md`，是 |
| 根目录初始内容 | 只有上述研究报告；本次新增 `audit-sources/`、`audit-envs/` 与本报告 |
| 附件要求 | `pasted-text.txt`，已完整阅读并按只读审计边界执行 |
| 附件列出的原始研究材料 | 在当前目录均未找到，故没有用不存在的材料补强结论 |
| 项目定位 | 个人使用的 Research + Backtest + Demo + 最终 Live 系统 |
| 初期市场 | Binance USDⓈ-M，BTCUSDT/ETHUSDT 永续 |
| 时间尺度 | 15m–4h；不以微秒级 HFT 为目标 |
| 研究/GPU | Python、现代数据栈、PyTorch，RTX 5090 |
| 最高工程原则 | Order / Position / Portfolio / Execution 只有一个 primary owner；不复制交易域状态机 |
| 补充约束 | 不要求现在上实盘；允许正常开发周期和较长 Demo 观察期 |

输入优先级按附件执行：当前根报告 → 原始材料（缺失）→ 固定 checkout 的源码/测试/docs → 官方 issue/发布页。根报告的原首选没有被当作证据，只作为待验证假设。

## 3. Subject Identity

| 候选 | 官方来源与审计身份 | 状态 |
| --- | --- | --- |
| NautilusTrader | `https://github.com/nautechsystems/nautilus_trader.git`；tag `v2.0.0rc4`；commit `a0400251110653b6d8ae6a9b5b89c4543fa85a2d`；2026-09-02 | 固定 tag、detached HEAD；Python `2.0.0rc4`、Rust crates `0.63.0` |
| LEAN Engine | `https://github.com/QuantConnect/Lean.git`；master snapshot `6eb389012d73c364547d61546ff822fc8432dee2`；describe `18084`；2026-09-11 | 官方主干快照；不是有意义的当前 release tag；运行时打印 `v2.5.0.0` |
| LEAN Binance plugin | `https://github.com/QuantConnect/Lean.Brokerages.Binance.git`；master snapshot `9696fc025972314d73008f879d595df5c67bca28`；describe `17864`；2026-06-19 | 独立官方插件仓库；构建时 `QuantConnect.Brokerages 2.5.*` 实际解析为 `2.5.18042` |

三个 checkout 在运行测试/构建后均为 `git status --short` 空，候选源码无修改。Nautilus 官方发布页把 rc4 标为 release candidate，并明确不建议控制真实资本；这与本报告的 Demo-first 门禁一致：[v2.0.0rc4 release](https://github.com/nautechsystems/nautilus_trader/releases/tag/v2.0.0rc4)。

## 4. Evidence Method

证据标签严格采用附件定义：

- `RUNTIME_VERIFIED`：本次直接运行官方已有测试、示例或工具并观察到。
- `SOURCE_VERIFIED`：固定 checkout 的实现或官方测试源码明确存在。
- `DOC_VERIFIED`：固定 checkout 的官方文档明确说明。
- `ISSUE_EVIDENCE`：官方 GitHub issue 的报告与状态；不等同本机复现。
- `NOT_VERIFIED`：当前环境/凭据无法验证。
- `UNSUPPORTED`：源码明确拒绝或关键路径不存在，并有交叉证据。

### 实际运行记录

| 对象 | 官方命令/范围 | 结果 | 标签 |
| --- | --- | --- | --- |
| Nautilus wheel | 安装并导入官方 `nautilus_trader==2.0.0rc4` | Python 3.12.14；版本 rc4；wheel 的 core commit 为 `a04002511106` | `RUNTIME_VERIFIED` |
| Nautilus model funding tests | `python/tests/unit/model/test_funding.py` | 11 passed | `RUNTIME_VERIFIED` |
| Nautilus acceptance | `python/tests/acceptance_tests`，使用源码支持的 `TEST_DATA_ROOT_PATH` 指向 checkout | 33 passed、6 skipped | `RUNTIME_VERIFIED` |
| Nautilus selected units | backtest engine/config/node/model、execution/risk/portfolio/serialization、Binance factory/migration | 274 passed | `RUNTIME_VERIFIED` |
| Nautilus official example | `examples/backtest/synthetic_data_pnl_test.py` | 12 bars、2 market orders、1 position、逐合约费用、保证金和已实现 PnL `$70.00`；exit 0 | `RUNTIME_VERIFIED` |
| LEAN restore/build | `dotnet restore QuantConnect.Lean.sln`；`dotnet build ... -c Release --no-restore` | build succeeded；0 errors、7,785 warnings | `RUNTIME_VERIFIED` |
| LEAN official C# backtest | 编译后的默认 `BasicTemplateFrameworkAlgorithm` | 3,943 data points、3 orders、13/13 data requests、结果统计完成；exit 0 | `RUNTIME_VERIFIED` |
| Binance plugin restore/build | 官方 solution restore/build | build succeeded；0 errors、7,756 warnings | `RUNTIME_VERIFIED` |
| Binance plugin tests | 官方 `--list-tests` 可发现 Futures JSON、订单生命周期、partial fill、cancel/expired/dedup 及大量集成测试 | 测试源码编译且成功发现 | `SOURCE_VERIFIED`，不是运行通过 |
| Binance plugin 过滤测试 | 尝试仅运行 JSON/mock 类 | test assembly 初始化要求 LEAN Data 映射文件并触发 QuantConnect subscription 校验；`Invalid api user id or token`，host 中止 | `NOT_VERIFIED` |
| LEAN core Binance model tests | 尝试过滤 `BinanceBrokerageModelTests` | 官方 assembly 全局初始化 Python；本机只有 3.12，仓库要求 3.11.11，Python.Runtime finalizer/GIL 异常中止 | `NOT_VERIFIED` |

Nautilus 初次 acceptance 运行的 3 failures/11 errors 来自 wheel 默认寻找 venv 内 test data；源码 `python/nautilus_trader/testkit/providers.py:70-71` 明确支持 `TEST_DATA_ROOT_PATH`。设置该变量后官方测试通过，未改源码。LEAN 首次 Launcher 运行因提升权限构建出的输出目录写权限失败；以同一官方命令获准写日志后回测通过。两者都按环境问题处理，没有伪装为框架缺陷。

本机没有 `cargo/rustc`、Docker 或 Python 3.11；有 .NET SDK 10.0.201。没有 Binance Demo/API 凭据，也没有下单。restore 还报告若干传递/历史包漏洞告警（DotNetZip、System.Drawing.Common、System.Net.Http.WinHttpHandler、System.ServiceModel）；它们是升级清单，不应与 7,000 多条普通 analyzer warning 混为一谈。

## 5. Nautilus Architecture

Nautilus v2 是 Rust 核心 + PyO3 Python API 的事件驱动交易系统。`Strategy`、DataEngine、RiskEngine、ExecutionEngine、Portfolio、Cache、MessageBus 与 venue adapter 共用统一领域对象；回测和 live 复用策略回调与订单事件模型。Python strategy 可在 `python/nautilus_trader/trading/__init__.pyi:464-963` 看到 bars/ticks/mark/index/funding 回调、订阅、历史请求和 submit APIs；Rust 实现位于 `crates/trading`、`crates/backtest`、`crates/live`、`crates/execution`。

这满足 TraceQuant 的最高原则：Nautilus 是 Order / Position / Portfolio / Risk / Execution 的唯一 owner，TraceQuant 只拥有 alpha、特征、标签、模型与策略配置。它也原生支持多个 StrategyId 和单节点多策略（`python/nautilus_trader/trading/__init__.pyi:102-116`）。

弱点是 v2 仍在 RC，迁移面较大，Rust adapter 测试在本机未运行；Python API 友好不代表维护者可完全忽略 Rust backtrace、crate 版本和 PyO3 wheel 兼容性。

## 6. LEAN Architecture

LEAN 是 C#/.NET 10 的成熟事件驱动引擎：QCAlgorithm、Security/Portfolio/Transactions、BrokerageModel、DataFeed、History、Setup/Result/Transaction handlers 组成核心；Python 通过 Python.NET 驱动同一 C# 对象模型。Binance 是独立插件 assembly，而非 LEAN 主仓库内的执行实现。

LEAN 的 Algorithm Framework 把 Alpha (`Insight`) → PortfolioConstruction (`PortfolioTarget`) → Risk → Execution 明确分层；`Algorithm/QCAlgorithm.Framework.cs:169-231` 和 `CompositeAlphaModel` 提供多 alpha 组合。这比 Nautilus 更“研究平台化”，但多个 alpha 不是多个互相隔离的交易账户状态机：最终仍共享一个 Algorithm、Portfolio 和 ExecutionModel，符合单一 owner 原则。

开源边界需明确：LEAN engine 与 Binance plugin 源码都是开源；QuantConnect cloud、数据订阅、托管、官方 CLI 的某些 brokerage 安装/验证流程不是自动等同于开源引擎能力。本次插件的“离线”测试甚至在 assembly discovery 阶段触发 subscription 校验，说明官方运维工作流存在真实账户/商业边界，尽管插件源码本身可读可编译。

## 7. Research Comparison

| 维度 | NautilusTrader | LEAN |
| --- | --- | --- |
| Notebook/交互 | 普通 Python/Jupyter 直接使用对象与 ParquetDataCatalog；自由度高 | QuantBook 与官方 notebook 模板成熟，History 直接返回 multi-index pandas DataFrame |
| 查询体验 | catalog 的 typed query/streaming 强；跨表探索通常配 DuckDB/Polars | 统一 History/IndicatorHistory/UniverseHistory API 更完整 |
| 本地环境 | 官方 cp312 wheel 本次直接可用 | 官方源码文档钉住 Python 3.11.11、pandas 2.2.3、wrapt 1.16.0 与 `PYTHONNET_PYDLL`；本机 3.12 实际触发测试 host 异常 |
| 研究到策略 | 同语言、同领域对象，转换较薄 | QuantBook/QCAlgorithm 一致性好，但 Python↔C# interop 是额外边界 |
| Polars/DuckDB | 没有专有封装，但 Parquet/Arrow 很自然 | 没有一等 Polars/DuckDB API；从 pandas/文件转换可行 |
| 结论 | 对 TraceQuant 的现代 Python/GPU 栈约束更少 | 内建研究 UX 更强，但本地 Python 约束更重 |

证据：Nautilus `docs/how_to/loading_external_data.py:55`、`crates/persistence`；LEAN `Research/QuantBook.cs:98,235-257`、`Research/KitchenSinkQuantBookTemplate.ipynb`、`Algorithm.Python/readme.md:32-89`。本项不是简单胜负：如果研究 UI/统一 History API 权重最高，LEAN 更强；如果现有 Python/Polars/PyTorch 生态和个人可维护性权重最高，Nautilus 更合适。

附件列出的 10 个研究问题可归纳为：两者获取历史数据、构建 features/labels、训练 sklearn/LightGBM/XGBoost/PyTorch 都可行；LEAN 的 QuantBook→pandas 最方便，Nautilus 的 catalog→Python/Arrow→Polars 最自然。两者研究与交易共享各自领域对象，不必重复实现策略，但 LEAN Research API 对 QuantBook/History/Data Folder 绑定更强，Nautilus 对 Jupyter/外部库更少设限。Polars 在两者都不是一等 API；Nautilus 因 Parquet/Arrow 和无 Python.NET 边界，预期 glue 更薄。以上外部 ML 库均未在本次运行，不能标为 runtime 验证。

## 8. Data Comparison

Nautilus 对 Bar、Trade/Quote、L2/L3、MarkPriceUpdate、IndexPriceUpdate、FundingRateUpdate 都有一等类型；Binance adapter 支持 live mark/index/funding 和历史 funding 请求，ParquetDataCatalog 可以持久化并按 instrument/time 查询。`crates/persistence/src/backend/catalog.rs:398-484,2255-2266` 与 `crates/adapters/binance/src/futures/data.rs:3015-3040` 是直接实现证据。`RUNTIME_VERIFIED` 只覆盖本次合成 bars 和数据事件表面，不覆盖真实 Binance 数据。

LEAN 的数据模型、合并器、History 与 Data Folder 很成熟，并有 `MarginInterestRate` 作为资金费输入。但当前 Binance plugin 的 brokerage history 只接受 `TickType.Trade` 和 Minute/Hour/Daily，明确拒绝 Tick/Second、Quote/OpenInterest（`BinanceBrokerage.cs:373-443`；官方 Futures history tests 也编码相同边界）。15m 可从 1m、4h 可从 1h 合并；checkout 内未带 BTCUSDT/ETHUSDT Binance 历史样本。本地长周期研究需要另行取得/转换数据，官方 README 指向 QuantConnect 数据下载，这不能自动算成纯开源仓库自带数据。

Canonical schema 决策：**`RESEARCH_SCHEMA_ONLY`**。不为任何候选重建 Instrument/Order/Position/Account schema。

- 采用 Nautilus：交易数据以 Nautilus typed objects + ParquetDataCatalog 为运行时事实；TraceQuant 用 Polars/DuckDB 维护只读特征/标签/训练视图与模型元数据。
- 采用 LEAN：LEAN Data Folder/Security/Order/Portfolio 为运行时事实；外部 Parquet lake 仅作为研究/训练层，通过单向边界生成 LEAN 可消费数据。

## 9. ML / GPU Comparison

两者都没有负责 CUDA 驱动、RTX 5090 调度或通用训练生命周期；GPU 是 PyTorch/CUDA 的责任。

Nautilus 的优势是策略本身就在标准 Python 进程接口上，本次 cp312 wheel 可直接与普通 Python 包共存；模型推理可在 Actor/Strategy 外部服务或策略回调中接入。未找到官方模型 registry、feature store 或版本化模型 artifact contract，因此这些仍属于 TraceQuant。`SOURCE_VERIFIED` Python 开放性，`NOT_VERIFIED` RTX 5090 实际训练/推理。

LEAN 有官方 `Algorithm.Python/PytorchNeuralNetworkAlgorithm.py`、Keras 示例和 ObjectStore 模型文件路径，证明 ML 模式被认真考虑；但 Python.NET、固定 Python/pandas 版本和 C#↔Python 生命周期提高本地 GPU 调试复杂度。`SOURCE_VERIFIED` 示例，`NOT_VERIFIED` 本机 GPU 运行。

RTX 5090 架构摩擦评级：Nautilus **`LOW_FRICTION`**（cp312 native wheel 与普通 Python 环境；仍要匹配 CUDA/PyTorch wheel）；LEAN **`HIGH_FRICTION`**（当前源码明确钉 Python 3.11.11/pandas 2.2.3/wrapt 1.16.0，且本次错误版本已实际导致 host/GIL 失败）。若完全把训练放在独立进程，只向 LEAN 传预测结果，其运行时摩擦可降到 `MEDIUM_FRICTION`，代价是多一层服务与 artifact contract。

建议组合是 **Nautilus + Polars + DuckDB + PyTorch**。Polars/DuckDB/PyTorch 只拥有研究计算和 artifact，不拥有订单、持仓、组合或执行状态。

## 10. Strategy Flexibility

Nautilus：任意 Python/Rust event callbacks、custom data、多个策略、execution algorithm 和 emulator；同一策略类可进入 backtest/live。`SOURCE_VERIFIED`，且本次官方 Python Strategy 回测为 `RUNTIME_VERIFIED`。

LEAN：经典 QCAlgorithm 和 Algorithm Framework 均成熟；CompositeAlphaModel 支持多个 alpha，PortfolioConstruction/Risk/Execution 可替换，Python/C# 双语言。默认 Framework 算法本次实际跑通。对大规模资产 universe，LEAN 的现成抽象更丰富；对 BTC/ETH、15m–4h、深度 Python ML 的个人系统，两边都足够，Nautilus 的边界更薄。

多策略注意：Nautilus 可显式区分 StrategyId，但在一个 Binance NETTING 账户/品种上多个策略仍共享 venue 净头寸；必须指定一个 owner 或做上层分配。LEAN 多 alpha 最终天然汇聚为共享组合目标。不能把“支持多个策略/alpha”理解为交易所账户隔离。

外部 ML/DL/RL/AI 的自然边界：Nautilus 以不可变的预测/目标作为 custom data 或 Strategy/Actor 内部输入，再由 Strategy 使用官方 order factory/submit/risk 路径，预计 `SMALL` glue；它没有 LEAN 那样强制的 Alpha/target-weight 管线。LEAN 则由 `IAlphaModel` 输出 `Insight`，PortfolioConstruction 转成 `PortfolioTarget`，ExecutionModel 执行，预计同样 `SMALL` glue。任何方案都不允许模型直接成为第二个 order/position owner。

## 11. Backtest Comparison

| 能力 | NautilusTrader | LEAN |
| --- | --- | --- |
| 事件/订单域复用 | backtest/live 共用核心领域对象与策略接口 | algorithm/security/order 接口高度复用，brokerage/fill model 可替换 |
| 结果与成熟度 | 高性能 Rust engine、order-book 回放、资金费结算 | 长期成熟的 regression 系统、统计/report、universe/asset breadth |
| 永续专用 | `FundingRateUpdate` 进入 exchange settlement；`crates/backtest/src/exchange.rs:1127-1451` | `BinanceFutureMarginInterestRateModel` 从 `MarginInterestRate` 在 00/08/16 UTC 结算；`Common/Securities/CryptoFuture/BinanceFutureMarginInterestRateModel.cs:23-97` |
| 本次运行 | 官方示例 12 bars/2 orders/fees/margin/PnL，选定 tests 通过 | 官方 BasicTemplateFrameworkAlgorithm 3,943 points/3 orders，完整统计输出 |

两者回测核心都达到高水平。LEAN 的通用资产、分析报告和回归资产更成熟；Nautilus 对 crypto microstructure、typed mark/index/funding 及同一 adapter domain 的贴合更强。资金费模型都需要真实且时间对齐的数据；源码存在不等于数据质量已验证。

Fill realism：Nautilus 有 built-in/default/probabilistic fill models、L1/L2/L3 matching、one-tick slippage 与 `StaticLatencyModel`，官方 `examples/backtest/model_configs_example.py:46-114` 展示配置；LEAN 有可替换 `IFillModel`、Python wrapper、market/limit/stop fill 与独立 `ISlippageModel`（`Common/Orders/Fills`、`Common/Orders/Slippage`），但未找到与 Nautilus 同等级的一等网络命令 latency model。对 15m–4h BTC/ETH，两者均足够；关键是使用 trade/order-book 数据选择合理模型，而非按 HFT 标准扣分。partial fill 在两者模型/测试表面均存在，本次官方示例没有制造 partial fill。

## 12. Binance USD-M Comparison

| USD-M 能力 | Nautilus v2 rc4 | LEAN + Binance plugin |
| --- | --- | --- |
| BTCUSDT/ETHUSDT perpetual | `SOURCE_VERIFIED` `BinanceProductType::UsdM`、PERP instruments | `SOURCE_VERIFIED` `CryptoFuture`、`/fapi/v1`/`v2` |
| Market/Limit | `SOURCE_VERIFIED` | `SOURCE_VERIFIED` |
| Stop-market/stop-limit | `SOURCE_VERIFIED`，含 algo conditional paths | `SOURCE_VERIFIED`，含新 algo endpoint mapping |
| Trailing stop | `SOURCE_VERIFIED`，官方 Futures trailing-stop tester 存在 | `UNSUPPORTED`：Futures order body switch 无 `TrailingStopOrder`，plugin tests/实现未找到 |
| Post-only | `SOURCE_VERIFIED`：Limit + `GTX` (`execution.rs:574-575,1419-1420`) | `UNSUPPORTED` 当前实现：Limit + PostOnly → `LIMIT_MAKER` (`BinanceBaseRestApiClient.cs:299-306`)；官方 issue #45 未关闭 |
| Reduce-only | `SOURCE_VERIFIED`，one-way/hedge 分别编码 | `UNSUPPORTED` 下单 payload 未设置 `reduceOnly` |
| One-way / Hedge | `SOURCE_VERIFIED`：读取 dual-side，positionSide 与 canonical venue position id | `UNSUPPORTED` 下单/持仓模型没有 hedge leg identity；解析 fixture 里的 `BOTH` 不等于支持 hedge |
| Cross / Isolated | `SOURCE_VERIFIED`：逐 symbol `futures_margin_types` | `UNSUPPORTED` Futures client 无 margin-type API；通用 spot margin 的 isolated issue 仍未关闭 |
| 交易所杠杆 | `SOURCE_VERIFIED`：逐 symbol `futures_leverages` 并调用 set leverage | engine 可设置本地 buying-power leverage，默认 25x；plugin 不调用交易所 leverage API，故 live parity `UNSUPPORTED` |
| Mark / Index / Funding | live + historical funding、first-class types `SOURCE_VERIFIED` | core 有 `MarginInterestRate`，但 plugin live/history 只提供 trade/quote/klines；mark/index/funding adapter feed `UNSUPPORTED` |
| Demo | 当前 demo endpoints/config 一等支持：`DOC_VERIFIED`、`SOURCE_VERIFIED` | 工厂允许自定义 HTTP 和两个 WS host，见第 16 节；`NOT_VERIFIED` |
| Partial fill / cancel | fixtures、parser 与 exec-client tests `SOURCE_VERIFIED` | JSON fixtures/message lifecycle/cancel/dedup tests `SOURCE_VERIFIED` |
| Modify/amend | native Futures HTTP/WS modify `SOURCE_VERIFIED` | `UNSUPPORTED`，只允许 cancel/re-create |
| Balances/positions/account | Futures account/position risk、one-way/hedge reports `SOURCE_VERIFIED` | `/fapi/v2/account` balances/非零 holdings/open orders `SOURCE_VERIFIED`；hedge identity 缺失 |
| Binance Rust/C# adapter runtime | Rust tests 未运行；本次仅 Python factories/migration | plugin build 和 tests discovery 通过；实际 tests 被 auth/global setup 阻止 |

这是选择 Nautilus 的决定性证据。LEAN 可做一个受限的 one-way、市价/普通限价 MVP，但 TraceQuant 若需要安全 partial exit、maker-only、exchange leverage parity 或未来 hedge mode，就需要补执行层语义，不再是单纯配置。

## 13. Order / Execution Comparison

Nautilus Futures 下单映射读取 post-only/reduce-only/position side，支持 WebSocket/HTTP submit、native modify、cancel/batch paths，对模糊网络结果不立即伪造 reject，而等待 reconciliation（`crates/adapters/binance/src/futures/execution.rs:500-727,3322-3510`）。官方 Rust tests 覆盖 GTX、reduce-only、hedge side、改单与 partial fill fixture；本机未执行，故标 `SOURCE_VERIFIED`，不是 `RUNTIME_VERIFIED`。

LEAN plugin 支持 Market、Limit、StopLimit、StopMarket，WebSocket 消息代码处理 submitted/partial/filled/canceled/expired 与重复 cancel；官方 JSON fixtures 和 tests 很丰富。2026 年 market order 永远 Submitted 的 issue #63 已关闭，当前 messaging 源码包含新的动作/dedup 路径，属于已修复证据，但本机无法执行测试。缺口：`UpdateOrder` 直接抛 `NotSupportedException` 并要求 cancel/re-create（`BinanceBrokerage.cs:346-348`）；Futures post-only 已确认错误；没有 reduce-only/position-side。

Cancel/re-create 可以作为策略层可接受的普通改单语义，但必须保留 cancel acknowledgement、重新定价和重复提交防护；不能称为原子 amend。Nautilus 的 native modify 更贴近交易所，仍需 Demo 验证丢包/模糊结果。

## 14. Fees / Funding

Nautilus：instrument provider 可按账户/逐 symbol 查询 maker/taker commission，失败时 USD-M taker fallback 为 0.04%；fill 使用 venue trade id 与 commission；回测 exchange 直接结算 funding。`SOURCE_VERIFIED`；官方合成例子的 fee/PnL 为 `RUNTIME_VERIFIED`，但不是 Binance fee tier。

LEAN：`BinanceFuturesFeeModel` 默认 USDT maker 0.02%、taker 0.04%，允许构造自定义费率；核心对 perpetual 安装 `BinanceFutureMarginInterestRateModel`。这是可靠的 backtest model，但插件没有当前账户的动态 commission 查询，也没有 live funding/mark feed；固定 8 小时边界可能无法表达 Binance 可变 funding interval，需由数据/模型升级承担。

两边都不能在没有账户级折扣、BNB/credits、VIP tier、实际 funding 数据的情况下宣称财务精确。Nautilus 的 live 获取与 typed 数据路径更接近事实源。

按附件指定状态归类：Nautilus 的 historical funding representation、backtest accounting、live funding 均为 **`BUILT_IN`**（实际 Binance runtime 仍未验证）；LEAN 的 historical/backtest funding 为 **`BUILT_IN`**，但 Binance plugin live funding 是 **`MAJOR_GAP`**，若仅把 funding 当 custom data 输入则为 `CUSTOM_DATA_ONLY`。LEAN 的固定结算时点升级为可变 interval 估为 `SMALL_EXTENSION_EXPECTED`。

## 15. Risk

Nautilus 有独立 RiskEngine、pre-trade checks、throttles、notional/position/order controls，并能理解 reduce-only/whole-position exit；执行语义和风险语义在同一 domain 内。LEAN 有成熟 BuyingPowerModel、margin call、RiskManagementModel、order validation 和 brokerage model。

关键差异不是“有没有风险模块”，而是模型是否与 venue 同步：LEAN 本地 25x 或用户设置的 leverage 不会由当前 plugin 同步到 Binance，且无 isolated/hedge/reduce-only 下单语义，可能出现模型允许但交易所拒绝或退出单反向开仓的风险。对 TraceQuant USD-M，Nautilus 风险匹配度更高；两者都需要 TraceQuant 自己定义策略级 drawdown、kill switch 阈值、数据陈旧和异常行情策略。

## 16. Demo / Live

Nautilus 文档和配置明确区分 LIVE/TESTNET/DEMO，列出 `demo-fapi.binance.com` 与 `demo-fstream.binance.com`，Futures 官方 example 默认以测试环境为安全入口（`docs/integrations/binance.md:1291-1334`、`crates/adapters/binance/examples/futures`）。`DOC_VERIFIED`、`SOURCE_VERIFIED`；无凭据所以没有 live-like runtime 验证。

LEAN Futures factory 的 HTTP URL 可配置为 `https://demo-fapi.binance.com`，但 private WS 只白名单 `fstream.binance.com` 与 `fstream.binancefuture.com`，并强制形成 `/private/ws`（`BinanceFuturesBrokerageFactory.cs:31-61,105-121`）。这是一条源码可见的专用 paper 路径，但本次未验证它与 2026 Binance Demo 的当前协议是否一致，故标 `NOT_VERIFIED`，不能把 LEAN 内部 PaperBrokerage 与 Binance exchange Demo 混为一谈。

两者实盘均 `NOT_VERIFIED`。没有密钥、没有订单、没有资金风险。本报告明确把真实资金门禁放在 Demo soak、重启对账和账务核对之后。

## 17. Reconciliation / Recovery

Nautilus 的 LiveExecutionEngine 在启动时对 order/fill/position mass status 做 event-sourced reconciliation，能物化外部订单，并在启动后持续检查 in-flight 和 position discrepancy；Binance adapter 会查询 open orders、algo orders、trades、positions，WebSocket 重连时重建 data subscriptions/order-book snapshots。源码与文档非常完整（`docs/concepts/reconciliation.md:68-128,170-236,297-378`；`crates/adapters/binance/src/futures/execution.rs:1095-1307`；`data.rs:880-898`）。

但这是 Nautilus 当前最大实盘风险：官方 [issue #4736](https://github.com/nautechsystems/nautilus_trader/issues/4736) 报告 Binance USD-M NETTING warm restart 可能把已缓存的真实持仓合成冲平，2026-08-12 创建，审计时仍开放/处理中。它针对 1.230.0，报告者仅“概念上”对 rc1 复现，故对 rc4 是 `ISSUE_EVIDENCE`，不是本次复现；rc4 源码未找到 issue 编号对应的明确修复记录。历史 hedge 长持仓 #3104、instrument-cache partial fill #3775、symbol formatting #4223 已关闭，表明维护响应活跃，也说明这个区域近一年确有迁移风险。

LEAN 的 `BrokerageSetupHandler` 启动时获取 cash、open orders、holdings 并导入 transaction/portfolio 状态（`Engine/Setup/BrokerageSetupHandler.cs:375-488`）；Binance plugin 有 listen-key keepalive、close timer、reconnect 和消息 dedup（`BinanceBrokerage.cs:650-719,882-919`；`BinanceBrokerage.Messaging.cs:275-295`）。相较 Nautilus，它更像“启动快照 + 通用 brokerage synchronization”，没有看到 Binance 专用的历史 fill 重建、partial-window 调整、hedge-leg identity 或持续 position reconciliation。通用路径成熟，但 USD-M 恢复深度较浅。

结论：Nautilus 的设计能力更强，但当前公开 defect 使其重启场景必须成为实盘前硬门禁；LEAN 不能因为设计较简单就自动更安全。两边都未通过本次带凭据故障注入。

## 18. Extension Points

Nautilus 的扩展点包括 Strategy/Actor、custom data、execution algorithm、adapter factories、instrument provider、serialization/catalog 和 Rust crate；新交易所需 data/execution client、parsers、instrument mapping 和 tests。现有 Binance/Bybit/OKX/Deribit 等实现可作模板。对 TraceQuant 初版无需写 venue adapter。

LEAN 的扩展点包括 QCAlgorithm/Framework models、custom data、BrokerageModel、fill/fee/buying-power/margin-interest models、IDataQueueHandler、IBrokerage、HistoryProvider 与独立 brokerage plugin。接口清晰，但补齐 Binance USD-M 不只是一处 mapper：至少跨 order properties/payload、position identity、account mode/leverage、data feed、brokerage model 和 tests。

未来第二交易所方面，两边都具备扩展机制；本次没有测试第二交易所，不能用 adapter 数量代替质量。Nautilus 当前的 crypto-native typed 数据和多 venue adapters 更符合初期方向；LEAN 的跨资产通用性更强。

## 19. Recent Issues / Maintenance

### NautilusTrader

- `v2.0.0rc4` 于 2026-09-02 发布，checkout 与 wheel commit 完全一致；主项目有 build/test/nightly/performance/security/CodeQL/OpenSSF 等多套 CI。`SOURCE_VERIFIED`。
- v2 是明显的 Rust/Python 架构迁移期，RC 自己声明不建议生产真实资本；breaking-change 风险高于稳定版。
- [#4736 warm-restart NETTING](https://github.com/nautechsystems/nautilus_trader/issues/4736)：开放/处理中，关键 live 风险。
- [#3775 partial fill/instrument cache](https://github.com/nautechsystems/nautilus_trader/issues/3775)、[#3104 long-lived hedge reconciliation](https://github.com/nautechsystems/nautilus_trader/issues/3104)、[#4223 Futures symbol formatting](https://github.com/nautechsystems/nautilus_trader/issues/4223)：均已关闭；当前 rc4 源码已有更系统的 Rust adapter/reconciliation 实现，不能把旧 issue 当成仍存在。
- [#4043 Futures OCO/SL+TP contingency](https://github.com/nautechsystems/nautilus_trader/issues/4043)：审计时开放；如果策略要求交易所级 bracket/OCO，这是额外能力缺口，BTC/ETH 单腿策略可先不依赖。

### LEAN / Binance plugin

- LEAN master 审计提交为 2026-09-11，核心有 regression/research/syntax/API/benchmark CI，维护非常活跃。Binance plugin 审计提交为 2026-06-19，只有一套主 workflow，活动密度明显低于核心。
- 插件官方 issue 列表审计时仅 6 个 open，但其中 [#45 Futures 不支持 LIMIT_MAKER](https://github.com/QuantConnect/Lean.Brokerages.Binance/issues/45) 自 2025-01 开放至今，且本地当前源码仍精确命中错误实现。这是源码与 issue 的双重证据。
- [#60 Isolated Margin](https://github.com/QuantConnect/Lean.Brokerages.Binance/issues/60) 于 2026-01 创建仍开放；其描述针对 Binance margin 账户而非专门的 Futures isolated mode，但也证明 LEAN 缺少对应账户/保证金模型，不能拿它单独证明 Futures 行为。
- [#63 market order only Submitted](https://github.com/QuantConnect/Lean.Brokerages.Binance/issues/63) 与 LEAN [#9339](https://github.com/QuantConnect/Lean/issues/9339) 已关闭；当前源码和 fixtures 增加了 Futures user message lifecycle/expired/dedup。应视为已修复，而非当前缺陷。
- plugin 使用浮动 `QuantConnect.Brokerages 2.5.*`，本次解析 `2.5.18042`，而 LEAN checkout describe 为 `18084`；生产构建必须锁定并验证版本组合。

整体维护判断：LEAN core 最成熟；Nautilus 对 crypto/Binance 的近期投入和测试表面更深；LEAN Binance plugin 是整体栈中相对薄弱的一环。

## 20. License

| 组件 | checkout license | TraceQuant 含义（非法律意见） |
| --- | --- | --- |
| NautilusTrader | LGPL-3.0-only（`Cargo.toml:56`、Python metadata 同） | 作为外部依赖使用通常可与 TraceQuant 自有代码分离；若修改/分发 LGPL 组件，要保留许可并满足对应源码/可替换要求。维护私有 fork 会增加合规和升级成本。 |
| LEAN | Apache-2.0 | 宽松；保留 notice/license，修改与再分发约束相对少。 |
| LEAN Binance plugin | Apache-2.0 | 同上；但 NuGet/其他传递依赖仍需逐项看许可证。 |
| TraceQuant | 预期 Apache-2.0 | 可把两者作为外部依赖；不应把 Nautilus 源码直接复制进 Apache-only 核心而忽略 LGPL 边界。 |

许可证方面 LEAN 更简单，但不足以抵消其 USD-M 执行语义缺口。推荐 Nautilus 时，应优先依赖官方 wheel/crates，不维护长期私有 fork。

## 21. Gap Matrix — Nautilus

| Gap | Evidence | Level | 判断依据/影响 |
| --- | --- | ---: | --- |
| 稳定版 v2 尚未发布 | 官方 rc4 release 声明 | LEVEL 1 | 版本冻结与升级门禁；不是功能开发，但实盘前需重评稳定版/更新版 |
| TraceQuant 研究宽表、特征/标签 schema | catalog 是交易数据，不是 feature store | LEVEL 2 | 现有 Parquet/Arrow 接口，可用 Polars/DuckDB 建薄研究层 |
| 特征/标签 lineage 与防泄漏验证 | 未发现框架一等实现 | LEVEL 3 | 领域属于 TraceQuant；需数据时间语义、版本和测试 |
| 模型 artifact contract/registry | 未发现官方通用 ML registry | LEVEL 3 | 需模型版本、输入 schema、训练区间、hash、加载失败策略 |
| Binance 历史 bars + mark/index/funding 的完整落湖 | adapter/history/catalog 原语存在 | LEVEL 2 | 连接已有 request/typed catalog；主要是调度、质量与去重责任 |
| warm-restart NETTING 风险 | issue #4736 开放 | LEVEL 4 | 上游 execution reconciliation 已有大量接口/测试，预计是小 upstream patch；在确认前是 live blocker |
| 交易所级 Futures OCO/bracket | issue #4043 开放 | LEVEL 3 | 可先用独立保护单并做策略 OCO；若必须 venue atomic，则等待/扩展 |
| 监控、告警、部署、密钥运维 | 框架提供日志/事件，不是完整 ops 产品 | LEVEL 2 | 接现有 hooks；个人系统仍需健康、陈旧数据、PnL/position drift 监控 |
| RTX 5090 实际共存与推理延迟 | 未运行 | LEVEL 1 | 标准 Python 环境配置；若 callback 阻塞则改成外部服务为 LEVEL 2 |

## 22. Gap Matrix — LEAN

| Gap | Evidence | Level | 判断依据/影响 |
| --- | --- | ---: | --- |
| 本地 Python 3.11/Python.NET 环境 | 官方 readme + 本次 test host 中止 | LEVEL 1 | 安装固定 Python/pandas/wrapt 和设置 `PYTHONNET_PYDLL` 可解；维护成本持续存在 |
| BTC/ETH USD-M 本地历史数据 | repo 无样本；plugin history 仅 trade bars | LEVEL 3 | 需下载/转换/质量流水线，mark/index/funding 还需自定义数据 |
| Futures post-only GTX | 源码 `LIMIT_MAKER` + open #45 | LEVEL 4 | 订单映射和 brokerage capability/tests 的小 upstream patch；未修前不能安全启用 post-only |
| reduce-only | payload/OrderProperties 不存在 | LEVEL 4 | 需 order property、序列化、payload、capability 和生命周期 tests；安全退出的重要缺口 |
| Hedge mode / positionSide identity | holdings 只聚合 `PositionAmt`；payload 无 positionSide | LEVEL 5 | 触及持仓身份、reconciliation 与 order model，长期 fork 风险较高；one-way MVP 可延期 |
| Futures cross/isolated 与 exchange leverage 同步 | plugin 无对应 API；engine 仅本地 25x/model leverage | LEVEL 4 | 需 account config/API、初始化顺序、模型一致性和 tests |
| Mark/index/live funding adapter 数据 | plugin DQH 只有 trade/quote，history 只有 klines | LEVEL 3 | LEAN custom data/interest model 已有接口，但 Binance provider 子系统要补 |
| 可变 funding interval | 当前模型固定 00/08/16 | LEVEL 3 | 已有 IMarginInterestRateModel，可扩展；需 interval 数据源和 regression |
| Native amend | `UpdateOrder` 明确抛异常 | LEVEL 1 | 若接受 cancel/re-create 是策略配置/约束；要原生 amend 则 LEVEL 4 |
| Binance Demo private WS 当前兼容性 | 工厂强制旧式 host/path；未带凭据测试 | LEVEL 4 | 若当前 Demo 协议不兼容，需 factory/认证/stream patch 与测试；现阶段不能定性通过 |
| 启动/持续 USD-M 深度对账 | 只有通用 cash/open orders/holdings snapshot | LEVEL 5 | 若要求 Nautilus 级 fill reconstruction、position identity、continuous reconciliation，会跨 engine/plugin |
| plugin tests 的 subscription/global setup | 本次离线 filter 也被 auth 阻止 | LEVEL 1 | 可用有效订阅/官方 CI 环境；不是功能修复，但降低个人可验证性 |
| 版本组合浮动 | `2.5.*` → 2.5.18042 | LEVEL 1 | lock/构建清单即可，但每次升级要回归 |

## 23. Estimated Integration Surface

**Nautilus：`SMALL`（框架接入）/ `MEDIUM`（完整 TraceQuant 产品剩余工作）。** 需要连接约 6 类责任：数据入 catalog、research views、feature/label pipeline、模型 artifact loader、Strategy 配置、ops/部署。交易域、Binance adapter、risk、backtest、portfolio 不重建。

**LEAN：`LARGE`（满足同一 USD-M 要求）。** 除上述研究/模型/ops 责任外，还要连接或修补 6 类交易所责任：GTX、reduce-only、hedge/positionSide、margin/leverage、mark/index/funding feed、深度 reconciliation；并维护 Python.NET 与 plugin/package 版本边界。若产品明确永久限制为 one-way、普通 market/limit、交易所外部预设 cross/leverage，表面可降到 `MEDIUM`，代价是删减需求与安全语义，而不是框架已完整支持。

不统计 LOC；估算依据是需要触及的接口/责任数量、现有相似模块、公开 issue 和测试面。

## 24. Ownership Matrix — Nautilus Option

| Capability | Framework | Companion OSS | TraceQuant |
| --- | --- | --- | --- |
| market data | Binance adapter/DataEngine | 可选 Tardis | 源选择、质量策略 |
| historical storage | ParquetDataCatalog | DuckDB/对象存储 | 分区、保留、版本 |
| research query | typed catalog query | Polars/DuckDB | 研究 API/视图 |
| notebook | Python API | Jupyter | notebook 规范 |
| features | 数据回调/indicators 原语 | Polars | 特征定义、lineage |
| labels | 无专有 owner | Polars | 定义、防泄漏 |
| ML | Strategy/Actor 接点 | PyTorch/sklearn | 训练、评估、artifact |
| GPU/DL | 不管理 | CUDA/PyTorch | 环境、资源、推理 SLA |
| RL/AI | 可扩展事件接口 | Gymnasium/RLlib/LLM SDK 可选 | 环境、奖励、安全边界 |
| strategy | Strategy lifecycle | — | alpha 与参数 |
| instrument | **Nautilus** | — | 不复制；只做研究映射 |
| order | **Nautilus** | — | 不复制 |
| position | **Nautilus** | — | 不复制 |
| portfolio/account | **Nautilus** | — | 不复制 |
| risk | **Nautilus RiskEngine** | — | 策略级限额政策 |
| backtest | **Nautilus** | — | 场景与验收 |
| execution | **Nautilus** | — | 执行配置，不建 FSM |
| Binance | **Nautilus adapter** | — | 凭据、symbol allowlist |
| Demo/Live | **Nautilus node/adapter** | — | 门禁与审批 |
| reconciliation | **Nautilus LiveExecutionEngine** | — | 验收/监控，不另写 owner |
| monitoring | events/logging hooks | OTel/Prometheus 可选 | dashboards/alerts |
| deployment | node config | container/system service | 发布、密钥、回滚 |

## 25. Ownership Matrix — LEAN Option

| Capability | Framework | Companion OSS | TraceQuant |
| --- | --- | --- | --- |
| market data | LEAN DataFeed + Binance DQH | 自选 downloader | 缺口数据/质量 |
| historical storage | LEAN Data Folder/ObjectStore | Parquet/DuckDB | lake 与转换 |
| research query | QuantBook/History | Polars 可选 | 研究视图 |
| notebook | Research templates | Jupyter/Docker | 环境规范 |
| features | indicators/history | pandas/Polars | 特征/lineage |
| labels | 无专有 owner | pandas/Polars | 定义、防泄漏 |
| ML | Python algorithm hooks/ObjectStore | PyTorch/Keras | 训练/artifact |
| GPU/DL | 不管理 | CUDA/PyTorch | 环境与 SLA |
| RL/AI | custom model interfaces | 外部库 | 环境、奖励、安全 |
| strategy | QCAlgorithm/Framework | — | alpha 与参数 |
| instrument | **LEAN Security** | — | 不复制 |
| order | **LEAN Transactions** | — | 不复制；但需 upstream properties |
| position | **LEAN Portfolio/Holdings** | — | 不复制；hedge gap 待解 |
| portfolio/account | **LEAN** | — | 不复制 |
| risk | **LEAN models** | — | 策略限额政策 |
| backtest | **LEAN** | — | 数据、场景与验收 |
| execution | **LEAN + plugin** | — | 当前 USD-M patch 风险 |
| Binance | **official plugin** | — | 不另建第二 adapter；只 upstream/fork |
| Demo/Live | **LEAN/plugin** | — | 凭据与门禁 |
| reconciliation | **LEAN setup/transaction handlers** | — | USD-M 深度缺口不得另建平行状态机 |
| monitoring | result/message handlers | OTel/Prometheus 可选 | dashboards/alerts |
| deployment | Launcher/CLI/Docker | container tooling | 版本、密钥、回滚 |

若选择 LEAN，任何补丁都必须进入 LEAN/plugin 的既有领域对象，不能在 TraceQuant 再做一个影子订单/持仓系统。

## 26. Remaining TraceQuant Work

### If NautilusTrader selected

| TraceQuant 仍需拥有 | WHY | LEVEL |
| --- | --- | ---: |
| 研究 schema、特征/标签与 lineage | 框架不是 feature store；防止时间泄漏是研究责任 | 2–3 |
| BTC/ETH bars + mark/index/funding 数据获取/质检 | 原语存在但数据资产不随 repo 提供 | 2 |
| 模型训练、评估、artifact contract、GPU 环境 | 不应由交易 runtime 决定 | 3 |
| 策略 alpha、组合限额与 kill policy | 产品行为 | 1–3 |
| Demo 验收、账务核对、warm restart gate | 关键 live 证据缺失且有 #4736 | 1（测试）/4（若需上游修复） |
| 监控、告警、部署、密钥与回滚 | 框架不是完整 ops 平台 | 2 |

### If LEAN selected

除同样的研究/ML/ops 工作外，还需承担：GTX post-only（4）、reduce-only（4）、hedge/position identity（5）、exchange margin/leverage（4）、mark/index/funding provider（3）、Demo compatibility（4）与更深 reconciliation（5）。这正是它不适合当前首选的原因。

## 27. Explicitly Avoided Custom Development

采用 Nautilus 后，应明确删除/禁止以下自研计划：Trading Engine、订单状态机、Position/Portfolio/Account ledger、通用 RiskEngine、BacktestEngine、Binance REST/WS adapter、订单/成交序列化、通用 reconciliation engine。TraceQuant 只通过公开配置/Strategy/Actor/data APIs 使用它们；若关键缺陷需要修复，优先 upstream，不建平行系统。

采用 LEAN 后同样无需自研通用 QCAlgorithm/Framework、Security/Portfolio/Transactions、通用 backtester、QuantBook/History、result statistics、ObjectStore。问题是 Binance USD-M 缺口仍需在官方 plugin/engine 扩展点内补齐，不能声称这些专用能力已经解决。

本次实际遵守了这一原则：没有写 adapter、wrapper、harness、mock、PoC 或 patch；只运行候选自带资产。

## 28. Strongest Argument Against Nautilus

最强反对理由不是“RC 看起来不稳”，而是**最危险的 warm-restart 对账路径存在近期、具体、仍开放的 Binance USD-M NETTING 缺陷报告，而且本次不能用 Rust tests 或 Demo 凭据消除它**。该缺陷会让本地误以为已平仓，方向是危险的。v2 大迁移、LGPL fork 成本和个人排查 Rust/PyO3 的门槛进一步放大这一风险。

为什么仍选择它：用户不要求现在实盘，开发与 Demo 时间足够；该风险可以被明确隔离在实盘门禁，而研究、回测和 Binance 专用接口现在即可使用。相比之下，LEAN 的关键缺口发生在开始实现常见 USD-M 订单语义之前，不能只靠时间观察消失。

## 29. Strongest Argument Against LEAN

最强反对理由是**官方 Binance plugin 的 Futures 语义覆盖低于 TraceQuant 的安全基线**：公开 issue #45 所述的 post-only 错误在当前源码仍存在；reduce-only、hedge positionSide、exchange leverage/margin mode、mark/index/funding adapter feed 与 native amend 均缺失或受限。这不是文档措辞差异，而是本地固定源码的 payload 和 API 表面直接证明。

LEAN core 的研究、回测和多资产成熟度很高，Apache 许可也更宽松；如果 TraceQuant 未来转向多资产、QuantConnect 数据/云生态，或者只做受限 one-way 普通订单，它仍是强候选。但在当前 BTC/ETH USD-M + Python/GPU + research/live 一体化目标下，补齐面过大。

## 30. Future Validation Requiring Development

以下项目仅靠本次只读证据无法判断，本次没有执行：

| Gap | Why static evidence insufficient | Minimal future test | Would require |
| --- | --- | --- | --- |
| Nautilus Binance Demo 订单闭环 | Rust tests 未在本机跑；真实协议/权限/延迟不可静态证明 | 官方 Futures data/exec tester：BTC/ETH 最小量 market、limit、GTX、reduce-only、cancel、native modify、partial fill | Rust toolchain、Binance Demo credentials；先审查 tester 参数 |
| Nautilus warm/cold restart | #4736 未关闭且缓存/venue 状态组合复杂 | 持仓与挂单跨进程重启；逐项核对 venue/local order、fills、position、cash | Demo credential、持久 cache、受控故障注入；可能需要 upstream fix |
| 长时间连接恢复 | 24h、listen-key、网络抖动无法由短测试证明 | 7–30 天 Demo soak，注入断网/延迟/重复消息，检查漏单和陈旧数据告警 | Demo、运维监控；测试编排代码 |
| 资金费/费用账务一致性 | 本次只有模型/源码和合成 fee | 跨两个 funding boundary 对照 Binance income/commission 与本地 ledger | Demo 持仓、历史数据和对账工具 |
| RTX 5090 模型推理 | 两框架都未实际加载 GPU 模型 | 固定 artifact、warmup、同步/异步延迟和故障退化测量 | CUDA/PyTorch 环境、自定义模型代码 |
| LEAN Futures Demo compatibility | factory host/path、auth 与 2026 API 未运行 | 官方 plugin 最小 live/paper algorithm，仅普通 market/limit/cancel | 有效 QuantConnect subscription/token、Binance Demo credential、正确 Python/CLI 环境 |
| LEAN 修补成本 | 静态 Level 只能估算，不能证明维护成本 | 先分别 upstream GTX/reduce-only，运行全 plugin + regression tests；再决定是否碰 hedge/reconciliation | 源码开发，超出本次范围 |

这些是未来的验证门，不影响现在选择研究/回测主运行时；它们决定何时从 Demo 进入真实资金。

## 31. Recommendation

采用 **NautilusTrader v2 `v2.0.0rc4` / commit `a040025...` 作为 TraceQuant primary trading runtime**，搭配 Polars + DuckDB + PyTorch；交易领域 schema 和状态只归 Nautilus。开发期先锁版本，不追逐 nightly；研究层使用只读派生 schema，不复制 Order/Position/Portfolio。

推荐不是“rc4 已可实盘”。实盘准入条件至少包括：选定版本不再带官方 production 警告或经单独风险接受、Binance Demo 订单矩阵通过、warm/cold restart 与 partial fill 对账通过、连续观察期无未解释 drift、费用/资金费账务可核对。若 #4736 在目标版本仍可复现，保持 Demo-only，优先等待/推动 upstream；不要在 TraceQuant 私下 monkey-patch reconciliation。

LEAN 保留为技术基准和未来多资产备选，不在当前项目同时作为第二交易 owner，也不投入大规模 Binance patch，除非 Nautilus 的重启门禁最终失败且 LEAN 的缺口已经由 upstream 显著收敛。

### 1–5 评分（不机械求和）

| Criterion | Importance | Nautilus | LEAN | 关键理由 |
| --- | ---: | ---: | ---: | --- |
| Research usability | High | 4 | 5 | LEAN QuantBook 更完整；Nautilus 更自由 |
| Python fit | High | 5 | 3 | wheel 直用 vs Python.NET 3.11 pin |
| GPU/ML openness | High | 5 | 3 | 都外接；Nautilus 边界更薄 |
| Strategy diversity | High | 5 | 5 | 两者均强，抽象不同 |
| Binance USD-M | Critical | 5 | 2 | 专用语义覆盖差距最大 |
| Backtest | Critical | 5 | 5 | 两者官方回测均实跑通过 |
| Backtest→Live consistency | Critical | 5 | 3 | Nautilus domain/adapter 更统一；仍待 Demo |
| Orders/Positions/Portfolio | Critical | 5 | 4 | LEAN 通用模型强，hedge/reduce-only gap |
| Risk | High | 5 | 4 | LEAN venue leverage/mode 不同步 |
| Execution | High | 5 | 3 | GTX/reduce/amend 差距 |
| Demo | High | 4 | 2 | 两者未运行；Nautilus 当前路径更完整 |
| Reconciliation/restart | Critical | 3 | 3 | Nautilus 深但有关键 open issue；LEAN 浅而通用 |
| Integration surface | Critical | 5 | 2 | SMALL vs LARGE |
| Operational complexity | Critical | 4 | 3 | Rust/PyO3 vs .NET+Python.NET+plugin/subscription |
| Maintenance maturity | Critical | 4 | 4 | NT crypto 活跃但 RC；LEAN core 强、plugin 较薄 |
| License | High | 4 | 5 | LGPL 外部依赖可控，Apache 更简单 |
| Personal maintainability | Critical | 4 | 3 | NT 更贴 Python/crypto；LEAN 跨运行时与 patch 面更大 |

决定性维度是 Binance USD-M、backtest→live 语义和 integration surface，而不是总分。即使把 LEAN 的 Research/License 优势提高权重，也无法直接补上 execution payload 的缺失。

## 32. Immediate Next Step

1. 将 `v2.0.0rc4 / a040025...` 冻结为 TraceQuant 第一阶段研究与回测基线，并把 `NAUTILUS_PRIMARY, LIVE_NOT_APPROVED` 写入架构决策；只建立 research schema、数据与模型边界，不写交易域替代实现。
2. 准备 Binance Demo 凭据和 Rust 1.98 环境后，优先运行上游 Futures data/exec testers，再做 warm/cold restart、partial fill、GTX/reduce-only 与资金费账务验收；任一 position/order drift 未解释时继续 Demo-only。
