### 背景

Feature #2 已经建立 Python 工程、严格质量工具、CI 和 UTC 时间约束，但尚未定义
供后续历史数据、研究、因子和回测模块共享的第一批领域对象。

如果后续模块直接使用松散 dict、tuple 或各自定义的数据类，容易产生：

- symbol 命名和规范不一致；
- 无时区或非 UTC datetime 混入；
- OHLC 关系、数值范围和有限性未校验；
- 序列化格式不稳定；
- 测试样本在多个模块重复手工构造；
- fixture 隐藏关键业务值或共享可变状态；
- 提前设计订单、账户、多交易所和事件平台；
- 公共 API 过多，形成不必要兼容负担。

Task #5 只建立了最小包导入测试，没有定义 Feature 正文要求的小型测试 fixture 规范。
本 Task 应通过真实领域模型及其测试建立最小、可复用的 fixture/factory 约定，而不是
先建设抽象 fixture 平台。

---

### 目标

定义第一批边界清晰、不可变、类型完整、可校验和可序列化的公共领域模型，并建立
服务于真实测试的共享 Fixtures 规范。

模型范围固定为：

```text
InstrumentId
TimeRange
OHLCVBar
```

其中：

- `InstrumentId` 表示当前单一市场数据语境下的标准化 instrument/symbol 标识；
- `TimeRange` 表示 UTC 的半开时间区间 `[start, end)`；
- `OHLCVBar` 表示一个 instrument 在确定时间区间内的基础 OHLCV bar。

本 Task 不设计订单、账户、仓位、策略、交易所抽象或回测事件系统。

---

### 输入

- 当前 `main`；
- #9 已有 UTC 时间工具和公开约束；
- #5 的 pytest、Ruff、mypy 配置；
- 当前 `src/quant_system` 包结构；
- 当前 tests 布局；
- Feature #2 正文；
- 后续历史数据和研究模块的最小近期需求；
- 第一轮 Token 优化的当前结果或维护者不实施决定；
- 当前 Telemetry 使用指南。

---

### 输出

- `InstrumentId`；
- `TimeRange`；
- `OHLCVBar`；
- 明确、最小的公共导入路径；
- 正常和失败路径校验；
- 确定性 JSON-compatible 序列化与反序列化；
- 使用现有 UTC 工具的时间校验；
- 共享 fixture/factory 的放置和命名规范；
- 至少一组有效对象和非法输入 fixture/factory；
- 模型校验、序列化、round-trip、不可变性和 fixture 隔离测试；
- 最小领域模型和 fixture 使用说明；
- 优化后的 `baseline-only` Telemetry run。

---

### 行为要求

#### 实现技术

- 使用 Python 标准库 `dataclasses` 或当前已有的等价最小机制；
- 模型优先使用 `frozen=True` 或等价不可变语义；
- 不为三类基础值对象引入 Pydantic、ORM 或大型 validation 框架；
- 公共字段和方法具有完整类型；
- 不在模型导入时读取配置、初始化日志或访问文件；
- 模型层不得依赖 Telemetry 或 workflow 工具。

#### `InstrumentId`

最小语义：

- 封装单个标准化标识，不建立多交易所抽象；
- 输入去除首尾空白；
- 规范化为大写；
- 仅允许 ASCII 大写字母和数字；
- 长度使用明确、保守的边界；
- 空值、空白、非法字符和超长值明确失败；
- `str()` 返回规范化标识；
- 可比较、可哈希、不可变；
- 序列化为字符串。

不包含：

- venue；
- exchange；
- market type；
- tick size；
- lot size；
- base/quote 解析；
- 多交易所映射。

### `TimeRange`

固定语义：

```text
[start, end)
```

要求：

- start 和 end 都必须是 aware datetime；
- 必须复用 #9 的 UTC 校验或转换公共 API；
- 不复制第二套 timezone 判断；
- start 必须严格早于 end；
- 提供明确 duration 或 containment 行为时，语义必须测试；
- 序列化为 UTC ISO 8601；
- 反序列化拒绝 naive 和非 UTC 输入，除非 #9 当前公共 API 明确负责转换；
- 不包含交易日历、节假日或时区数据库逻辑。

#### `OHLCVBar`

字段固定为：

```text
instrument
start
end
open
high
low
close
volume
```

要求：

- instrument 使用 `InstrumentId`；
- `[start, end)` 为 aware UTC，且 start < end；
- 价格和 volume 使用 Python `float`；
- 所有数值必须 finite，拒绝 NaN 和 infinity；
- volume 必须非负；
- high 必须不小于 open、low、close；
- low 必须不大于 open、high、close；
- 不要求价格严格为正，以免提前排除某些研究数据，但文档必须记录此边界；
- 模型不可变；
- JSON-compatible 序列化必须稳定；
- round-trip 后对象相等；
- 不加入 timeframe enum、trade count、VWAP 或 exchange metadata。

#### 序列化

- 提供显式 `to_dict` / `from_dict` 或等价最小 API；
- 输出只包含稳定公开字段；
- datetime 使用当前 UTC ISO 8601 规范；
- 非 finite float 不得被序列化；
- 不依赖 `pickle`；
- 不将内部 dataclass 实现细节作为协议；
- 不承诺长期外部 API 兼容版本体系；
- 文档说明这是 Research MVP 的初始内部公共模型。

#### Fixture 规范

推荐结构：

```text
tests/
├─ conftest.py
└─ fixtures/
   ├─ __init__.py
   └─ domain.py
```

规则：

- `conftest.py` 只放多个测试模块真实复用的 pytest fixture；
- 可参数化的确定性构造逻辑放在 `tests/fixtures/domain.py` factory；
- fixture 默认 function scope；
- 不共享可变对象；
- 不隐藏关键价格、时间和 symbol；
- 时间样本固定且为 UTC；
- 不使用当前时间、随机数、网络、环境变量或文件系统；
- 有效和非法样本命名清楚；
- 只有至少两个测试模块复用时才进入共享 fixture；
- 单个测试专用数据保留在测试文件；
- 测试不能依赖执行顺序；
- factory 必须允许显式覆盖字段；
- 不建立通用 mega-fixture 或 object mother 平台。

#### 公共 API

- 只暴露上述三类模型和必要异常；
- 不在包根目录暴露大量未来类型；
- 导入路径稳定且在文档中明确；
- 不建立抽象基类；
- 不建立 repository、service、event bus 或 registry。

---

### 异常与边界情况

- 空或空白 instrument；
- 小写 instrument；
- 非 ASCII 或分隔符；
- 过长 instrument；
- naive datetime；
- 非 UTC aware datetime；
- start == end；
- start > end；
- 跨日、跨月和闰日区间；
- NaN、正负 infinity；
- volume 为负；
- high/low 与 open/close 关系非法；
- 价格为 0 或负值；
- JSON 中字段缺失、额外字段或类型错误；
- datetime ISO 字符串不带时区；
- round-trip 浮点语义；
- fixture 被测试修改；
- factory 默认对象在多次调用间共享；
- 并行测试；
- Windows/Linux；
- dataclass repr；
- 哈希和集合行为；
- 后续模块需要额外字段；
- 旧序列化数据与未来模型变化。

---

### 范围外

- 订单、成交、账户、余额和仓位；
- 策略、信号、因子和标签模型；
- 回测事件；
- 交易所适配器；
- 多交易所抽象；
- Kline 下载或存储；
- Pandas、Polars、Arrow 或 Parquet schema；
- ORM 或数据库实体；
- Pydantic；
- protobuf、Avro 或 schema registry；
- timeframe enum；
- 交易日历；
- 货币精度和 Decimal 体系；
- tick/lot 校验；
- API DTO；
- 插件和继承层次；
- 大型 fixture 平台；
- property-based testing 依赖；
- 修改 CI workflow；
- 配置或日志重新实现；
- Token workflow 优化本身。

---

### 验收标准

- [ ] 存在 `InstrumentId`、`TimeRange`、`OHLCVBar`
- [ ] 三类模型具有明确、最小的公共导入路径
- [ ] 模型不可变、可比较且类型完整
- [ ] `InstrumentId` 规范化和非法输入测试完整
- [ ] `TimeRange` 使用 #9 当前 UTC 公共工具
- [ ] 没有复制第二套 timezone 校验
- [ ] naive、非 UTC 和非法区间明确失败
- [ ] `OHLCVBar` 拒绝 NaN、infinity 和负 volume
- [ ] OHLC 关系得到校验
- [ ] 价格为 0 或负值的当前语义已测试和记录
- [ ] 序列化输出 JSON-compatible 且稳定
- [ ] 反序列化非法数据明确失败
- [ ] round-trip 测试通过
- [ ] 不使用 pickle
- [ ] 共享 fixture/factory 结构和命名规则明确
- [ ] fixture 默认 function scope
- [ ] fixture 不读取当前时间、随机数、环境、网络或文件
- [ ] 多次 factory 调用不共享可变状态
- [ ] 至少两个测试模块真实复用共享 fixture/factory
- [ ] 没有 mega-fixture 或未来领域抽象
- [ ] 没有新增大型 validation/ORM 依赖
- [ ] 文档说明当前模型边界和已知限制
- [ ] 当前完整 CI 等价命令通过
- [ ] `git diff --check` 通过
- [ ] 原始 Telemetry 和本地数据未进入 PR

---

### 验证命令

```powershell
uv lock --check
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src tests
git diff --check
git status --short --branch --untracked-files=all
```

还必须执行结构性核验：

- `quant_system` 公共导入成功；
- naive datetime 失败；
- 非 UTC 输入按 #9 既有规范处理；
- NaN、infinity、负 volume 和非法 OHLC 失败；
- 序列化结果可由标准 JSON 处理；
- fixture/factory 多次调用结果相互隔离；
- 模型 import 不产生 I/O 或全局配置 side effect；
- changed files 与批准范围一致。

---

### Telemetry 运行要求

本 Task 是第一轮优化后的功能代码验证样本。

```text
mode: baseline-only
task_kind: feature-code
size: M
risk_class: high-correctness
workflow_shape: task-only
```

要求：

- 在满足“计划顺序门禁”后、`task-delivery` 前启动；
- 使用实际 Task 编号和当前优化后的 workflow main SHA；
- delivery、review、manual merge、closeout 使用同一 run；
- 新 head 和重审不新建 run；
- closeout 后 `finish`、`validate`、`summarize`；
- 与配置、日志基准比较时同时报告复杂度差异；
- 比较 findings、validation、review invalidation 和维护者决策；
- 不能仅用总 Token 判断优化成功；
- 若不可比，明确 limitations。

---

### 依赖 Issue

```text
#5
#7
#9
```

计划顺序关联：

```text
[Feature] 建立 Task Workflow Token 基准并完成第一轮优化
```

该关联不设置正式 `Blocked by`。

---

### 预计规模

```text
M：新增三类基础领域模型、校验、序列化、共享 Fixtures 和完整测试
```

---

### Ready 检查

- [x] 任务只有一个主要目标
- [x] 模型集合固定且最小
- [x] UTC 复用边界明确
- [x] 数值和 OHLC 校验明确
- [x] 序列化语义明确
- [x] Fixtures 规范与真实模型绑定
- [x] 已避免大型 fixture 平台
- [x] 已明确计划顺序但不制造虚假依赖
- [x] 已明确范围外领域对象
- [x] 验收标准可测试
- [x] Telemetry 分类明确
- [x] 不需要实现者猜测关键架构决定
