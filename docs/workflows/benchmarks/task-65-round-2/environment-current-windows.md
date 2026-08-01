# Task #65 current-baseline Windows 环境摘要

## 证据时间与用途

- 诊断时间：`2026-07-30T15:09:36.6643443Z`；
- 用途：冻结 Task #65 Windows `current-baseline` 的环境类别与已知限制；
- 该摘要不包含凭据值、用户级规则原文或可用于认证的材料；
- 运行实验臂时仍必须在独立外部记录中重新采集版本与有效配置。

## 主机与工作区

| 字段 | 冻结值 |
| --- | --- |
| 环境类别 | 原生 Windows，非 WSL，非容器 |
| Repository | `D:\workspace\program\quant-system` |
| 文件系统 | Windows drive-letter workspace |
| 架构 | `X64` |
| Shell | Windows PowerShell 5.1 |
| Windows build | `10.0.26200.8875` |
| sandbox | `workspace-write` |
| approval | managed approvals / `auto_review` |
| sandbox network | restricted |

## 工具链

| 工具 | 冻结值 | sandbox 行为 |
| --- | --- | --- |
| Git | `2.53.0.windows.1` | 只读命令可运行；正式仓库部分写操作可能需要审批 |
| GitHub CLI | `2.96.0` | 可启动，但 sandbox 身份无法读取宿主 keyring；elevated 后可认证 |
| Python | elevated 路径验证为 `3.11.9` | WindowsApps alias 在 sandbox 中因登录会话隔离失败 |
| uv | `0.11.28` | WinGet 用户目录中的 `uv.exe` 在 sandbox 中拒绝访问；elevated 后成功 |

## 执行路由与 rules 摘要

- 默认路由为 `sandbox-first`；
- Python 入口和全部 `uv` 命令在本地 profile 中使用 `elevated-first`；
- `gh` 使用 `adaptive`；
- `git status`、`git diff`、`git log` 使用 `sandbox-first`；
- 用户级 `default.rules` 只有四条既有 allow，未覆盖本项目常用 Python、uv、gh、
  Git workflow 和 CI-equivalent 验证；
- 当前环境因此存在重复 approval/Guardian、细粒度命令和凭据隔离成本。

## 已知未知项

以下值不能仅由仓库文件可靠冻结，必须在每个实验臂开始前记录：

- Codex IDE/CLI 精确版本；
- root model、reasoning effort 和 context window；
- Guardian/auto-review 模型与配置；
- 生效的隐藏或更高优先级 Codex 配置；
- 用户级 `default.rules` 完整内容哈希；
- `.agents/execution-profile.local.toml` 实际内容哈希；
- GitHub 登录 scopes 的当次状态；
- 代理、DNS、证书和网络的当次状态。

任一必填值缺失时，实验可以继续用于诊断，但不得标记为严格可比样本。
