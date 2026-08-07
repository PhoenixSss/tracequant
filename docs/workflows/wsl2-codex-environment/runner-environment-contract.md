# Validation / Evidence Runner 环境前置契约

## 目的

后续 Runner 必须在不扩大当前 Skill 权限的前提下复用已经验证的 WSL2 环境。Runner 是
命令编排和证据压缩层，不是授权层。

## 必需环境

| 项目 | 契约 |
| --- | --- |
| OS | WSL2 Ubuntu 24.04 LTS 或经独立验证的兼容 Linux |
| 文件系统 | 仓库位于 Linux 文件系统，不在 `/mnt/<drive>` |
| Python 引导 | `python3` 可用；不要求裸 `python` |
| 项目 Python | `.python-version` 的 Python 3.11，由 uv 管理 |
| uv | 精确 `0.12.1` |
| Git | WSL 原生 Git，支持 worktree 与普通分支操作 |
| gh | WSL 原生 GitHub CLI `2.97.0`，独立、可撤销登录 |
| 输出目录 | 仅 `.agents/evidence.local/` 和 `.agents/validation.local/` |
| 网络 | 正式远程操作使用已验证代理路径，并遵循 approval |
| 凭据 | 不读取、不复制、不输出认证文件或 token |

## 启动门禁

Runner 开始前至少验证：

1. `git rev-parse --show-toplevel` 与预期仓库一致；
2. 仓库路径不在 `/mnt/`；
3. `git status` 与当前 Skill 的工作区约束一致；
4. `python3`、`uv`、`git` 和适用时的 `gh` 路径均为 Linux 原生路径；
5. `uv --version` 为 `0.12.1`；
6. 输出路径被 Git ignore；
7. 网络、Git/GitHub 写入已经由当前 Skill 授权；
8. Skill/Runner 内容身份与 workflow 对象 SHA 锁定由现有 workflow evidence policy 处理。

任一关键事实未知时 fail closed，不得自动安装、登录、修改代理或放宽 Rules。

## 命令与结果模型

每项命令至少记录：

```text
command id
argv（非 shell 字符串）
cwd（规范化）
start/end 或 duration
exit code
bounded stdout/stderr summary
execution route（由外层 Codex/Guardian 记录）
retry relationship
final status
```

状态必须区分：

```text
direct-success
guardian-approved-success
sandbox-failure
elevated-first
elevated-retry
failed
not-tested
unknown
```

Runner 进程自身无法可靠观察 Guardian route；必须由外层执行日志补充，不能根据 exit code
猜测 approval 类型。

## 安全约束

- 不打印完整环境变量；
- 不读取 `~/.config/gh`、credential helper 数据、SSH 私钥或 token；
- 代理 URL 只保留协议、主机和端口；
- stdout/stderr 必须有界并脱敏；
- 失败不得被压成 pass；
- 不重试真实测试失败、认证失败或业务失败；
- 只有执行上下文隔离失败才可按 command-execution policy 重试；
- 不自动写回 execution profile 或 Rules；
- 不执行 Merge、Issue close 或 Project 收敛；
- 不把原始 rollout 或外部 Token 报告作为仓库产物。

## 能力分层

### 可直接运行候选

- OS、工具版本、仓库只读检查；
- 源码读取；
- `/tmp` 临时文件和临时 Git 仓库；
- 不触及用户缓存的纯 Python 检查。

### 需要精确审批或精确 Rules 候选

- uv cache 和项目虚拟环境访问；
- `.agents/evidence.local/` / `.agents/validation.local/` 写入；
- 正式仓库 fetch、index、object、refs 和 worktree metadata；
- gh 和远程 Git 网络；
- 当前 Skill 授权的 commit、push 和 PR 创建。

### 必须保持人工门禁

- GitHub lifecycle 写入超出当前 Skill；
- Rules/protection bypass；
- force push、reset/clean；
- Merge；
- Feature/Epic 人工 closeout；
- 系统安装和宽泛 sudo。

## 回滚与降级

Runner 遇到环境漂移时输出结构化 `blocked` 或 `unknown`，并保留已完成的只读事实。不得：

- 自动切换到 Windows 工具；
- 将仓库移动到 `/mnt`；
- 自动改用无代理；
- 自动复制宿主凭据；
- 自动放宽 Rules；
- 在不可信 head 上运行自身审查控制平面。
