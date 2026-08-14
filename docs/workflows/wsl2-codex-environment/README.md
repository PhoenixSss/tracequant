# 可复现的 WSL2 Codex 开发环境

## 目的与边界

本指南定义项目支持的 VS Code Remote WSL + Codex 开发基线，并把环境能力拆分为：

```text
系统与工具可用性
仓库本地读写能力
Git / GitHub 远程能力
Codex sandbox / approval 行为
```

WSL2 是目标开发环境，但不是“无需审批”的信任边界。当前实测表明，WSL2 解决了
WindowsApps Python alias、WinGet 用户目录和宿主 keyring 不可见等问题；Codex sandbox
对用户缓存、仓库 Git 元数据和网络仍保持隔离，因此部分命令继续需要 approval。

本指南不实现 Validation Runner、GitHub Evidence Runner、最终 `default.rules`，不修改
Task Workflow Skills，也不执行 Task #65 候选基准。

## 固定基线

| 项目 | 目标或已验证值 |
| --- | --- |
| WSL 发行版 | Ubuntu 24.04 LTS，发行版名 `Ubuntu-24.04` |
| 仓库位置 | `/home/<user>/code/tracequant`，Linux 文件系统；不使用 `/mnt/<drive>` |
| 系统 Python | Ubuntu 原生 `python3`；裸 `python` 命令不是要求 |
| 项目 Python | `.python-version` 固定为 Python 3.13，与 CI 一致 |
| uv | `0.12.1`，与 CI 一致 |
| Git | WSL 内原生 `/usr/bin/git` |
| GitHub CLI | WSL 内原生 `$HOME/.local/bin/gh`，WSL 内独立登录 |
| 网络 | 当前可靠路径为批准执行下的代理连接；无代理路径只部分可用 |
| Merge | 维护者人工执行 |

Ubuntu 24.04 自带的 `/usr/bin/python3` 可用于诊断脚本。项目命令使用 `uv run python`
和 `uv run <tool>`；不安装 `python-is-python3`，也不创建全局软链接。

## 安装与复现

### 1. Windows 和 WSL2

以管理员 PowerShell 安装明确发行版：

```powershell
wsl --install -d Ubuntu-24.04
wsl --update
wsl --status
wsl --list --verbose
```

完成系统重启和发行版首次初始化后，确认发行版运行在 WSL 2。不要将仓库默认克隆到
`/mnt/c`、`/mnt/d` 等 Windows 挂载盘。

### 2. WSL 内基础工具

在 Ubuntu 中安装基础包：

```bash
sudo apt update
sudo apt install --yes ca-certificates curl git gh python3
```

只对明确的软件安装使用 `sudo`。诊断、项目验证和日常仓库操作不应依赖宽泛 `sudo`。

### 3. 固定 uv

安装项目与 CI 使用的版本：

```bash
curl -LsSf https://astral.sh/uv/0.12.1/install.sh | sh
exec "$SHELL" -l
uv --version
```

预期：

```text
uv 0.12.1
```

不要依赖 Windows WinGet 的 `uv.exe` 或 Windows 用户私有目录。

### 4. 固定项目 Python

仓库的 `.python-version` 固定为 `3.13`。让 uv 安装并选择项目解释器：

```bash
uv python install 3.13
uv sync --locked --dev
uv run python --version
```

系统 Python 与项目 Python 的职责不同：

```text
python3                  系统级诊断和引导
uv run python            项目虚拟环境
uv run pytest/ruff/mypy  项目验证
```

### 5. GitHub CLI 认证

在 WSL 内独立认证，不复制 Windows 的完整凭据目录：

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status
```

要求：

- 使用可撤销的 GitHub CLI 登录；
- scopes 只覆盖当前仓库流程所需能力；
- 不提交 `~/.config/gh`、credential helper 数据或 token；
- scope 调整使用 `gh auth refresh`，并在变更后重新诊断；
- 退出或回滚使用 `gh auth logout --hostname github.com`。

当前仓库需要读取 repo、Issue、PR 和 checks，并在正式 Delivery 中创建分支和 PR。任何
GitHub 写操作继续属于 approval 边界。

### 6. VS Code Remote WSL 与 Codex

1. Windows VS Code 安装 Microsoft WSL 扩展。
2. 在 WSL 仓库目录运行 `code .`。
3. 确认左下角显示 `WSL: Ubuntu-24.04`。
4. 在 WSL 扩展主机中安装/启用 Codex/OpenAI 扩展。
5. 在 WSL 窗口中完成 Codex 登录。
6. 确认终端中的 `pwd` 位于 `/home/<user>/code/tracequant`。

Windows 和 WSL 的 VS Code 设置作用域必须分开检查。不要通过复制完整认证目录来“同步”
登录状态。

## 网络、代理、DNS 和证书

当前已验证环境使用 WSL mirrored networking，并通过 loopback 代理访问 GitHub。代理端口
属于机器本地配置，不作为仓库固定值。只在确有需要时设置：

```bash
export HTTP_PROXY="http://127.0.0.1:<port>"
export HTTPS_PROXY="http://127.0.0.1:<port>"
```

敏感认证信息不得写入仓库、诊断输出或 shell 历史。代理诊断至少包括：

```bash
getent hosts github.com
getent hosts api.github.com
curl -I --max-time 15 https://github.com
curl -I --max-time 15 https://api.github.com
gh auth status
git ls-remote --heads origin main
```

当前实测结论：

- 代理开启且命令获批时，GitHub、API、gh 和远程 Git 可用；
- 临时移除代理后，`api.github.com` 可达，但 `github.com` 与 `git ls-remote` 超时；
- 未批准 sandbox 既可能无法访问 loopback 代理 socket，也可能无法完成无代理 DNS，因此
  不能把 sandbox 网络失败直接归因于 WSL 宿主网络；
- 正式工作流应依赖“批准执行 + 已验证代理路径”，而不是假定无代理直连。

证书使用 Ubuntu CA bundle。检查路径：

```bash
python3 - <<'PY'
import ssl
print(ssl.get_default_verify_paths())
PY
```

## 日常项目命令

```bash
uv lock --check
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src tests
git diff --check
```

诊断脚本：

```bash
python3 tools/wsl2_codex_diagnostic.py --help
```

本地和项目检查：

```bash
python3 tools/wsl2_codex_diagnostic.py \
  --profile local \
  --json
```

加入 GitHub 只读和正式 fetch：

```bash
python3 tools/wsl2_codex_diagnostic.py \
  --profile remote-read \
  --github-repo PhoenixSss/tracequant \
  --formal-fetch \
  --json
```

完整网络对比：

```bash
python3 tools/wsl2_codex_diagnostic.py \
  --profile full \
  --github-repo PhoenixSss/tracequant \
  --formal-fetch \
  --json
```

受控写探针不会默认运行。正式仓库 disposable worktree 探针需要：

```bash
python3 tools/wsl2_codex_diagnostic.py \
  --formal-write-probe \
  --confirm-formal-write-probe DELETE_LOCAL_PROBE
```

GitHub 可逆写探针只创建并删除唯一临时远程 ref：

```bash
python3 tools/wsl2_codex_diagnostic.py \
  --github-repo PhoenixSss/tracequant \
  --github-write-probe \
  --confirm-github-write-probe DELETE_REMOTE_REF
```

这些探针仍需由当前 Skill 授权；命令行确认字符串只防止误触，不提供权限。

## 输出与敏感信息边界

默认输出位置：

```text
.agents/evidence.local/wsl2-environment-diagnostic/<UTC_RUN_ID>/
```

输出包括环境、命令、能力矩阵、网络摘要、Git/GitHub 摘要、验证摘要和 Markdown 报告。
该目录被 Git ignore，不得 stage、commit 或附加到 PR。

脚本：

- 限制 stdout/stderr 摘要长度；
- 将仓库和 home 路径规范化；
- 脱敏代理 URL 的认证和查询参数；
- 屏蔽常见 GitHub token 格式；
- 不读取认证文件、SSH 私钥或完整环境变量；
- 不能从进程内部判断 Codex 的 Guardian/approval 路由，因此必须结合 rollout/执行日志分类。

## 回滚

从轻到重：

1. 删除并重新克隆 WSL 内仓库，不影响 Windows 仓库；
2. `gh auth logout --hostname github.com` 撤销 WSL CLI 会话；
3. 删除项目 `.venv` 后执行 `uv sync --locked --dev`；
4. 删除 `~/.local/bin/uv` 并按固定版本重新安装；
5. 导出必要的非敏感资料后，在 Windows 中执行：

```powershell
wsl --unregister Ubuntu-24.04
```

最后一步会永久删除该发行版文件系统，必须由维护者明确确认，且不能作为普通故障排查步骤。

## 故障排查

### `python` 不存在

这是预期状态。使用 `python3` 或 `uv run python`。不要创建不受控全局 alias。

### `uv` 在 Codex sandbox 中无法访问缓存

当前 sandbox 不能直接访问 `$HOME/.cache/uv`。保持 approval 记录；不要在本 Task 中添加
宽泛 Rules。后续最小权限 Task 可评估受控缓存路径或精确命令路由。

### `gh auth status` 未登录

在 WSL 终端执行 `gh auth login`，不要复制 Windows 凭据目录。登录后重新运行 repo、Issue、
PR 和 checks 的只读诊断。

### `git fetch` 提示凭据或无法写 `FETCH_HEAD`

分别确认：

- `gh auth status` 与 `gh auth setup-git`；
- origin 使用预期 HTTPS URL；
- 代理路径可用；
- 失败是否来自 sandbox 对 Git metadata 的只读隔离。

网络、认证和 metadata 写入是三个不同故障域。

### loopback 代理在 sandbox 中不可用

先在普通 WSL shell 验证代理，再在获批执行上下文中验证。不要用未经批准 sandbox 的失败证明
宿主代理失效。

### 仓库位于 `/mnt/<drive>`

停止正式开发，重新克隆到 `/home/<user>/code/tracequant`。不要直接移动包含未提交修改的
工作区；先保存 patch 或创建受控提交。

### CRLF、file mode 或大小写变化

检查：

```bash
git config --show-origin --get core.autocrlf
git config --show-origin --get core.filemode
git config --show-origin --get core.ignorecase
git status --short --branch --untracked-files=all
```

不要为隐藏真实 diff 全局关闭检查。

## 当前限制

- 用户缓存、正式仓库 metadata 和 GitHub 网络在 Codex sandbox 中仍需 approval；
- 无代理直连只部分可用；
- Guardian 路由只能从外层执行日志精确分类；
- 本 Task 不提供最终最小权限 Rules；
- 本 Task 不实现统一 Validation/Evidence Runner；
- WSL2 环境就绪不等于 Task #65 candidate 已执行或已通过。

详细矩阵见 [能力与审批矩阵](capability-matrix.md)，后续 Runner 前置条件见
[环境契约](runner-environment-contract.md)。

## 最终文档素材归档

面向《代理开发工作流设计指导手册》和《代理工作流 Token 优化技术分享文章》的权威素材入口：

- `../publication-materials/task-material-register.md`：跨 Task 的产出材料来源、状态、用途和归档待办；
- `publication-materials.json`：六类材料、逐章节映射、claim/source、案例、决策和研究边界；
- `evidence-index.json`：公开安全的仓库证据 SHA，以及三轮仓库外 evidence 的 Run ID；
- `publication-readiness.md`：可公开结论、统计口径、迁移成本和合并前最终化清单；
- `decisions-and-cases.md`：人类可读的方案选择、否决理由、恢复和反直觉案例；
- `visuals/`：能力矩阵 CSV、Windows/WSL2 对照 CSV 和 Mermaid 可编辑图源。

`article-materials.json` 仅保留为简要摘要；`publication-materials.json` 是公共材料的单一权威来源。
原始 rollout 和 `.agents/evidence.local/` 不得提交。合并前必须按 `publication-readiness.md`
完成 external evidence SHA manifest 和 Delivery/Review 身份字段。
