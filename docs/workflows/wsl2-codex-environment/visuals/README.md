# WSL2 环境可编辑视觉资产

## `capability-matrix.csv`

- **标题**：WSL2 Codex 能力分类
- **来源**：`../capability-matrix.md`
- **统计口径**：每个能力类别一行；同一能力的多次命令和多轮诊断不重复计数。
- **可用于**：能力分类表、柱状图、approval 边界说明。
- **脱敏状态**：公开安全。

## `windows-wsl2-comparison.csv`

- **标题**：原生 Windows 与 WSL2 能力对照
- **来源**：Task #65 Windows 环境基线、`../current-diagnostic.json`、`../capability-matrix.md`。
- **统计口径**：按能力维度进行定性分类；不包含 Token、命令次数或时长。
- **可用于**：before/after 对照表和迁移收益图。
- **脱敏状态**：公开安全。

## `workflow-before-after.mmd`

- **标题**：原生 Windows 到 WSL2 Codex 环境迁移
- **来源**：Windows 环境基线、当前 WSL2 诊断和能力矩阵。
- **口径**：描述架构和能力路径，不表示 Token 或性能结果。
- **脱敏状态**：公开安全。

## `approval-boundaries.mmd`

- **标题**：WSL2 Codex 执行与审批边界
- **来源**：能力矩阵和 Runner 环境前置契约。
- **口径**：描述 Skill、sandbox、Guardian、approval 与人工门禁的职责关系。
- **脱敏状态**：公开安全。
