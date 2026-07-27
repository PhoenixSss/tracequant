# Agent Skills 工作流使用指南

## 目的

本文档面向仓库维护者，说明当前可用的 Task 工作流 Skills、典型调用方式、
人工门禁、身份校验、暂停恢复和常见异常。

规范性执行规则位于：

```text
AGENTS.md / AGENTS.override.md
.agents/skills/task-delivery/SKILL.md
.agents/skills/task-pr-review/SKILL.md
.agents/skills/task-closeout/SKILL.md
.agents/skills/feature-completion-audit/SKILL.md
.agents/policies/command-execution.md
```

本文档是使用指南，不替代上述规则。出现冲突时，以更高优先级和更具体的
规范性文件为准。


## Workflow Evidence 与紧凑验证

四个活动 Skills 现在使用统一的只读 Evidence 和 Validation 工具替代重复的机械命令链：

```text
tools/agent_workflow/workflow_evidence.py
tools/agent_workflow/workflow_validation.py
tools/agent_workflow/trusted_runner.py
```

规范性规则位于 `.agents/policies/workflow-evidence.md`，详细使用方法见
`docs/workflows/workflow-evidence.md`。

Evidence 只收敛身份、SHA、checks、threads、Project、Relationships、branch 和 direct
child 等元数据。Task/Feature 正文、PR 完整 diff、源码、测试与文档仍由 Agent 进行
语义读取。脚本输出 `pass/fail/unknown`，`unknown` 不能当作成功。

正常流程不再完整执行旧机械查询链后再叠加脚本。工具失败时使用 Skill 允许的安全只读
fallback，并在报告中记录 limitation。

## 当前活动 Skills

| Skill | 用途 | 正常终点 | 明确不做 |
| --- | --- | --- | --- |
| `task-delivery` | 完整处理一个维护者指定的已创建 Task | PR 已通过本地验证、Required Checks 配置核验和适用 check runs，准备进入独立审查 | 不做独立审查、不 Merge、不关闭 Issue、不清理分支、不判断 Feature 完成 |
| `task-pr-review` | 在新会话中对指定 Task PR 做独立只读合并前审查 | 输出绑定 reviewed base/head SHA 的三选一 verdict | 不修复、不提交 GitHub Review、不 Merge、不改 Issue/PR/Project、不判断 Feature 完成 |
| `task-closeout` | 维护者人工 Merge 后完成核验、状态收敛、验证和分支清理 | Task、Project、`main`、验证和精确分支均完成收尾 | 不 Merge、不手动关闭 Issue、不修复代码、不判断 Feature 完成 |
| `feature-completion-audit` | 在新会话中对维护者指定的开放 Feature 和当前 `main` 做独立只读完成审计 | 输出绑定 `Audited main SHA` 的三选一 Feature verdict | 不创建 Task、不修改 GitHub 状态、不关闭 Feature、不设置 Project `Done`、不判断 Epic 完成 |

旧的 `task-lifecycle` 已退役，不再作为活动入口。

## 标准 Task 流程

```text
维护者创建并指定一个 Task
        ↓
task-delivery
        ↓
PR 准备好接受独立审查
        ↓
新会话 task-pr-review 独立只读审查
        ↓
维护者人工 Squash Merge
        ↓
task-closeout
        ↓
Task 完成
```

人工 Merge 是 `task-pr-review` 与 `task-closeout` 之间的硬边界。三个 Task
workflow Skills 均不得执行 Merge。

## Task 身份格式

生产使用建议同时提供完整当前标题和 Issue 编号：

```text
[Task] <当前完整标题> #<编号>
```

规则：

- Issue 编号是主键；
- GitHub Issue 当前标题是 canonical title；
- 标题是防止误选的辅助校验值；
- 标题与编号明显不一致时，Skill 必须在写操作前停止；
- 只提供编号时，Skill 可以读取并回显 canonical title 后继续；
- ProjectV2 派生 `Title` 可能滞后，不作为身份权威来源。

可以忽略首尾空格、连续空格、大小写、常见全角/半角标点和 Markdown
转义等表面差异。

明显不一致示例：

```text
用户：
#49 [Task] 实现基础配置管理

GitHub：
#49 [Task] 收窄 task-lifecycle Skill 的职责起点
```

此时不得继续写入。

## 使用 `task-delivery`

### 标准提示词

```text
[Task] <当前完整标题> #<编号> 已创建完成。

请不要停在 Task 创建阶段。
请按 task-delivery 完整处理该 Task，直到 PR 准备好接受独立审查；
只有遇到必须由我决策的事项或工作流门禁失败时才中断并报告。
```

也可以直接写：

```text
请按 task-delivery 完整处理
[Task] <当前完整标题> #<编号>，
直到 PR 准备好接受独立审查。
```

### `task-delivery` 会完成

- Task 身份和规格门禁；
- 必要的 `Ready`、`In Progress`、`Review` 状态转换；
- 精确 Task 分支创建或安全恢复；
- 范围内实施；
- 当前 CI 等价验证；
- 显式路径暂存、commit 和普通 push；
- 唯一非 Draft PR 创建或恢复；
- Required Checks 配置读取/核验，以及实际适用 check runs 等待与结论核验；
- 实施者 self-check；
- 独立审查交接包。

### `task-delivery` 会暂停

典型暂停原因：

- Task 标题与编号明显不一致；
- 规格不完整或需要未授权的正文修改；
- blocker、Parent、依赖或 Relationship 不清楚；
- 工作区存在无关改动；
- 需要范围外文件或关键决策；
- 本地验证或 CI 失败；
- branch、commit 或 PR 不能证明属于当前 Task；
- 出现 Blocking、High 或未解决的 Medium 自检问题；
- 需要 Merge、force push、`--admin`、reset、clean 或保护规则绕过。

### 交接包

完成时至少应看到：

```text
Task number
Task canonical title
Task URL
PR number / title / URL
Task branch
Base SHA
Head SHA
Changed files
Local validation results
Required Checks configuration and status
Actual check runs and conclusions
Project Status
Codex label
Unresolved review threads
Known limitations
Ready for independent review
```

## 独立 PR 审查门禁

真正的独立审查需要：

```text
新的会话
+
严格只读
+
不采用实施会话结论作为证据
```

标准提示词：

```text
请使用 task-pr-review，独立只读审查
[Task] <当前完整标题> #<Task编号>
对应的 PR #<PR编号>。

Expected base SHA: <base SHA>
Expected head SHA: <head SHA>
```

`task-pr-review` 必须自行重新读取 Task、PR、完整 diff、commits、Checks、
reviews、threads、受影响文件上下文和当前仓库规则。`task-delivery` 的
handoff 只用于定位对象和 expected SHA，不是审查证据。

最终 verdict 只能是：

- `通过，可以人工合并`
- `有条件通过，不得合并`
- `不通过，需要修复`

任何新的 commit、head SHA 变化、base 或有效 diff 变化都会使旧 review
结论失效，并终止当前审查。修复 finding 时返回 `task-delivery` 或单独授权
的实施流程；修复产生新 commit 后，当前审查会话不得继续给出新版本 verdict，
必须开启新的 Codex 会话，对新的 expected base/head SHA 从头执行
`task-pr-review`。旧 findings 可作为线索，旧 verdict 和已完成步骤不能继承。

用户只提供 PR number 时，`task-pr-review` 可以从 PR closing linkage 解析
Task，但必须先回显 Task number 和 canonical title。closing linkage 缺失、
指向多个不明确 Issue、跨仓库或不是 Task 时停止。生产提示词仍推荐完整 Task
title、Task number、PR number 和 expected SHA。

Checks 报告必须区分 branch protection 的 Required Checks configuration 和
实际 workflow 产生的 check runs / conclusions。没有 Required Checks 时不得
虚构 required gate；但任一适用 CI check 失败、取消或未完成时，仍不得输出
`通过，可以人工合并`。

若专用 branch-protection 或 ruleset endpoint 仅因 GitHub 套餐限制返回 `403`，
该永久服务限制本身不自动导致 `有条件通过`。Reviewer 必须独立核验
`gh pr checks --required`、base branch 的 `protected` 状态、可读取的 ruleset /
mergeability 事实以及全部适用 check runs。只有这些事实一致表明当前 PR 没有
可执行的 Required Check、没有相互矛盾的保护证据且实际 CI 全部成功时，才可
在报告限制后给出通过；证据缺失、冲突，或受保护分支的门禁无法确定时仍必须
`有条件通过，不得合并`。

`task-pr-review` 不提交 GitHub Review、Approve 或 Request Changes，不
resolve thread，不修改代码或 GitHub 状态，也不 Merge。

维护者人工 Squash Merge 前必须核对当前 PR head SHA 等于 review 报告中的
`Reviewed head SHA`，并确认 Required Checks 配置和实际适用 check runs 仍成功、
没有新的阻塞 thread。

### `task-pr-review` 自举规则

Reviewer 不得使用“正在被审查的规则”证明自身正确。

审查控制平面必须运行在受信任的 PR base context。系统、开发者和当前用户
明确指令仍高于 base 仓库规则。

当 PR 修改任何适用的 `AGENTS.md`、`AGENTS.override.md`、`task-pr-review`
或 Review Skill 引用的共享治理 policy 时，使用 PR base commit 中的版本作为
本次审查的受信任规则；PR head 中的新版本只能作为审查对象，不得作为本次
审查授权来源。报告中记录使用的 base SHA 和受信任治理文件。可以读取 head
diff、blob 或临时 worktree，但不得让 head worktree 中的治理文件接管流程。
若无法保证 base control plane 与 head review object 隔离，停止并报告，不得
输出通过结论。

当 PR 不修改适用治理规则时，使用当前已合并版本作为受信任规则。

当 PR 修改已有 `task-pr-review` 时，使用 PR base commit 中的已合并版本
审查，并在报告中记录该 base 版本或 SHA。

首次引入 `task-pr-review` 时，base 中还不存在该 Skill，因此对应 PR 必须
继续使用维护者提供的临时独立只读审查提示词；新 Skill 只能作为审查对象。
只有合并进入 `main` 后，后续 Task 才正式使用它。

临时首次引入审查提示词示例：

```text
请在本会话中对
[Task] <当前完整标题> #<Task编号>
对应的 PR #<PR编号>
执行独立、严格只读的合并前审查。

不要采用实施会话的结论作为证据，不要修改代码或 GitHub 状态，不要 Merge。
请输出 reviewed head SHA、按严重度分类的发现、验收标准覆盖情况和明确结论。
```

## 维护者人工 Squash Merge

独立审查通过后，在 GitHub 页面人工执行 Squash Merge。

Merge 前核对：

- 当前 PR head SHA 等于独立审查通过的 head SHA；
- Required Checks configuration 已核对，实际适用 check runs 仍成功；
- 没有新的 unresolved review thread；
- PR 仍可合并；
- Merge method 是 Squash。

三个 Task workflow Skills 均不得代替该人工操作。

## 使用 `task-closeout`

### 标准提示词

```text
PR #<PR编号> 已由我人工 Squash Merge。

请使用 task-closeout，完成
[Task] <当前完整标题> #<Task编号>
及 PR #<PR编号> 的合并后核验与分支清理。
```

用户声明不是证明。`task-closeout` 会自行读取 GitHub 和 Git 事实。

### `task-closeout` 会完成

- Task 与 PR 身份和关闭引用核验；
- PR `MERGED`、Merge 方法、head SHA 和 merge commit 核验；
- Task 是否自动 `CLOSED`；
- fast-forward-only 同步 `main`；
- 合并后 CI 等价验证和 Skill validator；
- remote `main` Required Checks；
- Project Status `Done` 和最终 `codex:ready`；
- 精确远端 Task 分支删除；
- 精确本地 Task 分支删除；
- 最终 refs 和 clean status 报告。

### `task-closeout` 不会

- Merge；
- 手动关闭未自动关闭的 Task；
- 创建修复 commit；
- 修改 Parent、Priority、Size、Phase、Target 或 Relationships；
- 判断或建议 Feature 是否完成；
- 删除其他分支。

## 使用 `feature-completion-audit`

Feature Completion Audit 是独立于单个 Task 流程的维护者显式门禁。它不会在
每次 `task-closeout` 后自动运行，也不能根据 `n / n closed` 自动判断 Feature
完成。

适合在维护者认为一个 Feature 的必要子工作已经全部完成，并准备人工关闭
Feature 之前运行。

### 前置条件

- Feature 是当前仓库中明确指定的开放 `type:feature` Issue；
- 使用一个没有参与相关直接子 Task 拆分、实施、修复、review verdict 或
  closeout 的新 Codex 会话；
- 维护者提供或允许 Skill 读取当前 `origin/main` SHA；
- Feature 正文、直接子 Issue 和正式 Relationships 可以被读取；
- audit 过程严格只读。

### 标准提示词

```text
请使用 feature-completion-audit，独立只读审计
[Feature] <当前完整标题> #<Feature编号>。

Expected main SHA: <当前 main SHA>
```

Issue 编号是主键，GitHub Issue 当前标题是 canonical title。只提供编号时，
Skill 可以读取并回显标题。Expected main SHA 与实际 `origin/main` 不一致时停止。

### 审计内容

`feature-completion-audit` 会独立读取并核验：

- Feature body、comments、fields、Parent、blockers、dependencies 和正式
  Relationships；
- 全部直接子 Issue，并区分 direct child、indirect descendant、related 和
  unrelated Issue；
- 必要直接子 Task 的 Issue、merged PR、checks、自动关闭和 closeout 状态；
- Feature 每条验收标准在当前 `main` 中的源码、测试、文档、ADR 或批准决策
  证据；
- 跨 Task 集成、端到端行为、兼容性、操作文档和适用安全边界；
- 当前 `main` 的 Feature 相关验证、完整 CI 等价验证和实际远端 check runs；
- 未拆分、遗漏、重开、blocked、orphaned 或证据不足的工作。

所有子 Issue 关闭只是库存事实，不是充分的 Feature 完成证明。审计聚焦当前
`main` 的 Feature 级结果，不默认逐行重新审查所有历史 PR。

### 固定 verdict

只能输出：

```text
Feature 已完成，可以由维护者人工收尾

Feature 尚未完成，需要补充或修复 Task

证据不足，暂不能判定 Feature 完成
```

任何未解决的 Blocking、High 或 Medium finding 都阻止通过。Skill 可以提出
候选 gap-to-Task 标题和最小范围，但不会创建、重开或修改 Issue。

### `main` SHA 和失效规则

报告必须绑定：

```text
Audited main SHA: <actual main SHA>
```

`origin/main` SHA、Feature 范围或验收标准、直接子 Issue 集合、必要子 Issue
状态、blocker 或关键验证事实发生变化时，旧 audit 失效。必须在新会话中对新的
`main` SHA 重新审计，不能继承旧 verdict 或验收矩阵。

### 维护者人工收尾

通过 verdict 只表示可以进入维护者人工门禁。操作前仍需确认当前 `main` 等于
`Audited main SHA`、Feature 和直接子 Issue 集合未变化、没有新的 blocker 或
失败检查。

随后由维护者人工决定是否关闭 Feature、将 Project Status 设置为 `Done`，或
执行其他单独批准的元数据收敛。`feature-completion-audit` 不执行这些写操作。

## Done 后保留 `codex:ready`

最终状态：

```text
Issue: CLOSED
Project Status: Done
Codex lifecycle label: codex:ready
```

`codex:ready` 表示 Task 已通过实施前规格门禁，并作为审计标记持续保留到
Done。它不表示该 Task 当前等待实施，也不单独授权任何修改。

实施仍要求：

- Issue 是 `OPEN`；
- Project Status 是 `Ready` 或 `In Progress`；
- 存在 `codex:ready`；
- 不存在 `codex:blocked`；
- 当前工作流请求已授权实施。

因此，寻找待实施 Task 时不得只查询：

```text
label:codex:ready
```

至少应限制：

```text
is:open label:codex:ready
```

并在写入前核验 Project Status。

## Squash Merge 后的本地分支清理

Squash Merge 会让 Task 分支原 commit 不在 `main` ancestry 中，即使最终树
内容已经完全进入 `main`，`git branch -d` 仍可能拒绝。

`task-closeout` 必须先确认：

- PR 已 Merge；
- Task 已关闭；
- 当前分支是 `main`；
- `main == origin/main`；
- 工作区、index 和 untracked 均为空；
- exact Task branch 已从 PR head ref 确定；
- 分支没有 worktree 依赖；
- 远端分支已删除或安全删除；
- `git diff --quiet main <exact-task-branch>` 成功。

然后先尝试：

```text
git branch -d <exact-task-branch>
```

只有当失败原因仅是 Squash ancestry，并且全部门禁重新通过时，才允许：

```text
git branch -D <exact-task-branch>
```

不得使用通配符、模糊分支名、批量强删，也不得在 tree diff 非零或工作区
不干净时删除。

## 暂停和恢复

`task-delivery` 与 `task-closeout` 都从当前事实恢复，不假定每次从头开始。
`task-pr-review` 也会重新读取当前事实；但任何新 commit、head SHA 变化、
base 或有效 diff 变化都要求新会话从头审查新的 expected SHA，不继承旧
verdict 或已完成步骤。

`task-delivery` 可以恢复：

- 已是 `Ready`、`In Progress` 或 `Review`；
- Task branch 已存在；
- 已有经过核验的范围内改动；
- commit 已创建但未 push；
- branch 已 push 但 PR 尚未创建；
- PR 已存在；
- CI pending 或已完成。

`task-closeout` 可以恢复：

- PR 已 Merge；
- Issue 已关闭；
- Project 已 Done；
- Codex 标签已正确；
- `main` 已同步；
- 部分合并后验证已完成；
- 本地或远端 Task 分支已删除。

已完成步骤只核验，不重复写入。

## 常见异常

### Task 与标题不匹配

停止并报告 requested / actual identity，不执行写操作。

### ProjectV2 Title 与 Issue title 不一致

以 Issue title 为准，报告同步滞后，不尝试使用 DraftIssue 更新方法。

### Task 没有自动关闭

`task-closeout` 停止，不手动关闭 Issue。检查 PR 的 `Closes #<Task>` 和仓库
链接关系。

### 人工 Merge 方法不是 Squash

报告流程偏差，不重写历史。继续只读核验，等待维护者确认是否继续状态写入和
分支清理。

### `main` 与 `origin/main` 分叉

停止，不 merge、rebase、reset 或 clean。

### 合并后验证失败

停止，不在 closeout 中创建修复 commit。

### 远端或本地 Task 分支已经不存在

视为幂等完成，不重新创建。

## 环境感知的命令执行

三个 Task workflow Skills 与 `feature-completion-audit` 共同使用规范性 policy：

```text
.agents/policies/command-execution.md
```

仓库提供可复制的示例：

```text
.agents/execution-profile.example.toml
```

维护者可以在本机创建：

```text
.agents/execution-profile.local.toml
```

PowerShell 示例：

```powershell
Copy-Item .agents/execution-profile.example.toml .agents/execution-profile.local.toml
```

local profile 已被 `.gitignore` 忽略，不得提交，也不得包含凭据、token、私钥、
用户名或敏感本地路径。这些 Skills 不会自动创建、修改或学习并写回该文件。

### 三种 route

- `sandbox-first`：先在普通 sandbox 执行；仅在明确属于执行环境隔离时，使用
  完全相同的命令 elevated 重试。
- `elevated-first`：当前 Skill 已授权命令且本地 profile 精确匹配后，直接在
  elevated 上下文执行，避免制造已知 sandbox 失败。
- `adaptive`：结合有效 profile 和本次 workflow run 中相同精确命令的隔离
  证据选择初始上下文；不会自动把观察写回 profile。

profile 缺失、无法解析、schema 不支持、规则冲突或命令无匹配时，安全回退
到 `sandbox-first`。profile 只能选择执行上下文，不能授权命令，更不能扩大
Task lifecycle、GitHub、review、merge、metadata 或 branch cleanup 权限。

保持：

```text
Lifecycle authorization
!= command execution routing
!= operating-system elevation
```

### 当前机器的推荐起点

- `python -X utf8 ...quick_validate.py`：`elevated-first`；
- `uv` toolchain：`elevated-first`；
- `gh`：`adaptive`，不得把所有 GitHub 操作视为已授权写入；
- `git status` 和 `git diff`：`sandbox-first`。

这些只是机器级路由建议。任何命令都必须先通过当前 Skill 的权限与门禁。
`gh auth login`、Merge、`--admin`、force push、`git reset --hard`、
`git clean` 和其他禁止操作不会因 profile 获得授权。

### 路由报告

使用 elevated-first、sandbox 失败后 elevated retry、profile 无效、adaptive
切换、elevated 仍失败或凭据隔离判断时，报告至少记录：

```text
Command
Lifecycle authorization source
Selected route
Route source
Sandbox result, if attempted
Elevated result, if attempted
Final interpretation
```

普通低风险命令以 sandbox-first 一次成功时可以汇总，不需要制造冗长日志。

### Review 自举边界

当 PR 修改 command-execution policy、profile example、`AGENTS.md`、
`task-pr-review` 或其他治理规则时，`task-pr-review` 继续使用 PR base 中的
受信任规则作为 control plane。PR head 文件只能作为审查对象。local profile
可以影响已授权命令的执行上下文，但不能改变 reviewed SHA、严重度、验收覆盖
或 verdict。对 `feature-completion-audit`，local profile 同样不能改变 Feature
身份、直接子 Issue 分类、`Audited main SHA`、验收覆盖、findings 或 verdict。

## 仓库外 Token 消耗分析边界

项目不再运行 Task Workflow Token telemetry，也不维护运行状态、阶段事件、usage
补丁或仓库内分析摘要。正常 delivery、review、closeout 和 Feature audit 不执行任何
Token 测量命令，也不因外部分析缺失而增加字段、fallback 或改变 verdict。

Token 分析由维护者在仓库外完成：

```text
Codex rollout JSONL
+ Task / Workflow 元数据
→ 版本化 Task 分析报告
→ 基准与优化后报告对比
```

原始 rollout 日志可能包含 prompt、response、源码、命令输出、本地路径或认证相关
信息，不得提交本仓库。仓库外生成的 Token 分析模板、基准报告和对比报告也不属于
项目运行产物。

保留在本机的旧 `.agents/task-workflow-telemetry.local.toml` 或
`.agents/telemetry.local/` 仅是历史私有数据；当前工具和 Skills 不读取、不写入，也不
依赖它们。仓库不再提供 Telemetry 专属 ignore 规则；如果这些历史文件仍位于工作区，
它们可能作为 untracked 文件出现在 `git status` 中。维护者应在提交前自行将其移出仓库、
安全归档或删除。仓库 Workflow 不执行清理，也不得通过新增宽泛 ignore 规则重新隐藏
这些文件。
