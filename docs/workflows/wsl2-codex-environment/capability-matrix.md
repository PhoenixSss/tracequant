# WSL2 Codex 能力与审批矩阵

## 结论

当前环境为 `ready-with-gaps`：本地项目开发和验证可用，Git/GitHub 正式能力可用但在
Codex 中需要 approval。需要审批不是失败；它是后续最小权限 Rules 和 Runner 必须保留的
安全边界。

## 当前 WSL2 能力

| 能力 | 首次 sandbox 观察 | 获批后的结果 | 最终分类 | 安全边界 |
| --- | --- | --- | --- | --- |
| WSL 文件与源码读取 | 成功 | 不需要 | direct | 只读 |
| `/tmp` 文件和临时 Git | 成功 | 不需要 | direct | disposable 目录 |
| 仓库 ignored evidence 写入 | read-only filesystem | 成功 | approval-required | 仅 `.agents/evidence.local/` |
| `python3` | 成功 | 不需要 | direct | 系统诊断 |
| `uv` / 项目验证 | 无法访问用户缓存 | 成功 | approval-required | `$HOME/.cache/uv` 与项目环境 |
| Git status/diff/log | 成功 | 不需要 | direct | 只读 |
| 正式仓库 fetch | 无法写 `FETCH_HEAD` / 访问网络 | 成功 | approval-required | Git metadata + network |
| disposable 正式 worktree add/commit/switch | Git metadata 只读 | 成功并清理 | approval-required | 无 push、唯一临时分支 |
| `gh` repo/Issue/PR 只读 | 代理/网络不可用 | 成功 | approval-required | 认证 + network |
| GitHub 临时 ref 创建/删除 | 需要批准 | 成功并清理 | approval-required | 可逆唯一 ref；禁止 PR/Issue 副作用 |
| 代理开启 GitHub 访问 | sandbox 不可靠 | 获批后通过 | approval-required | 当前正式路径 |
| 临时无代理访问 | 部分超时 | 部分通过 | partial | 不能作为正式依赖 |
| Merge / protection bypass | 未测试 | 不允许 | prohibited | 维护者人工门禁 |

## Windows 与 WSL2 对比

| 维度 | 原生 Windows 观察 | WSL2 观察 | 结论 |
| --- | --- | --- | --- |
| Python | WindowsApps alias 在 sandbox 中不可稳定使用 | `/usr/bin/python3` 可直接使用；项目使用 uv | improved |
| uv | WinGet 用户私有路径不可稳定使用 | WSL 原生 uv 0.11.28 可用；cache 仍需 approval | improved |
| 仓库位置 | Windows NTFS 工作区 | WSL ext4 `/home/<user>/code/...` | improved |
| 临时 Git | 正式环境中大量写操作触发 elevated | `/tmp` 临时 Git 全流程 direct | improved |
| 正式 Git metadata | 需要 elevated | 仍需 approval | same safety boundary |
| GitHub 凭据 | 宿主 keyring 对 sandbox 不可见 | WSL 独立 gh 登录可撤销 | improved |
| GitHub 网络 | 依赖宿主代理与 elevated | 代理 + approval 路径稳定 | improved but not direct |
| 无代理网络 | 不稳定 | API 部分可达，GitHub/git 超时 | partial / not-comparable |
| Guardian 频率 | 正常开发命令频繁触发 | 本地读和 `/tmp` 写减少；远程与 metadata 仍触发 | improved, not eliminated |

该矩阵比较能力类别，不比较 Token 数值。原始 rollout 和外部 Token 报告不得提交仓库。

## 保留 approval 的操作

后续 Rules 设计不应直接放宽以下类别：

- GitHub 对象或远程 ref 写入；
- `git push`；
- 正式仓库 commit、branch 和 worktree metadata 写入；
- 访问用户级认证或缓存目录；
- 网络访问和代理 socket；
- 任何 Merge、Issue close、Project lifecycle 写入；
- `sudo` 和系统软件安装。

可评估精确授权的候选：

- 固定 `uv lock --check`、pytest、Ruff、Mypy 命令；
- 精确的只读 Git/GitHub 查询；
- 受控 `.agents/evidence.local/` 与 `.agents/validation.local/` 写入；
- 当前 Skill 已明确授权的普通 fetch、commit、push，但仍需阶段和 argv 约束。

本文件只记录候选边界，不配置最终 Rules。
