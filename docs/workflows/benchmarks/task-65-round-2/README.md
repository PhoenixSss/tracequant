# Task #65 第二轮 Workflow Token 实验冻结包

本目录是 Task #65 `[Task] 定义初始公共领域模型与共享测试 Fixtures` 的第二轮
Workflow Token 实验契约。它只冻结业务规格、仓库/工作流输入和比较口径，不实现
Task #65 的领域代码，也不执行任何实验臂。

## 目录

- `protocol.md`：规范性实验协议；
- `task-65-original.md`：收到的 Task #65 原正文，UTF-8/LF 规范化副本；
- `task-65-frozen.md`：只移除或改写失效 Telemetry 内容的冻结正文；
- `task-65-telemetry-only.diff`：原文与冻结正文的统一差异；
- `environment-current-windows.md`：当前 Windows 基准环境的脱敏摘要；
- `benchmark-manifest.json`：机器可解析的冻结标识、SHA 和协议字段；
- `materials/experiment-record.example.json`：仓库外实验归档的最小记录示例；
- `materials/publication-materials.example.json`：面向“代理开发工作流设计指导手册”和技术分享文章的素材索引、案例、决策、图表与证据映射示例。

## 冻结状态

`task-65-frozen.md` 是实验输入的唯一业务规格副本。GitHub Issue #65 正文只有在
维护者确认并应用该副本后才视为线上冻结。应用后必须重新读取 Issue 正文，按
UTF-8/LF 规范化计算 SHA-256，并与 `benchmark-manifest.json` 中的冻结哈希一致。

任何正文、Parent、依赖、base SHA、workflow SHA、Skill、模型、Codex 配置或环境
变化，都必须按 `protocol.md` 的 drift 规则处理，不得静默继续比较。

## 数据边界

原始 rollout、外部 Token 报告、凭据、本机用户级规则和本地 Evidence/Validation
目录不得提交到仓库。仓库只保留可公开的协议、哈希、脱敏摘要和归档 Schema。
