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
```

本文档是使用指南，不替代上述规则。出现冲突时，以更高优先级和更具体的
规范性文件为准。

## 当前活动 Skills

| Skill | 用途 | 正常终点 | 明确不做 |
| --- | --- | --- | --- |
| `task-delivery` | 完整处理一个维护者指定的已创建 Task | PR 已通过本地验证、Required Checks 配置核验和适用 check runs，准备进入独立审查 | 不做独立审查、不 Merge、不关闭 Issue、不清理分支、不判断 Feature 完成 |
| `task-pr-review` | 在新会话中对指定 Task PR 做独立只读合并前审查 | 输出绑定 reviewed base/head SHA 的三选一 verdict | 不修复、不提交 GitHub Review、不 Merge、不改 Issue/PR/Project、不判断 Feature 完成 |
| `task-closeout` | 维护者人工 Merge 后完成核验、状态收敛、验证和分支清理 | Task、Project、`main`、验证和精确分支均完成收尾 | 不 Merge、不手动关闭 Issue、不修复代码、不判断 Feature 完成 |

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

## 执行环境说明

当前 Task 工作流 Skills 保留 normal-first elevated fallback：

1. 普通执行；
2. 仅在明确的权限、凭据或登录会话隔离失败时，elevated 重试同一命令；
3. elevated 仍失败后才诊断真实环境或凭据问题。

elevated 只改变执行环境，不会赋予生命周期权限。

环境感知的 `sandbox-first`、`elevated-first`、`adaptive` execution profile
属于后续计划，当前尚未实现。

## 后续计划

以下能力尚未作为活动 Skill 提供：

- `feature-completion-audit`；
- 环境感知的 command-execution policy 和本地 execution profile。

在这些能力正式合并前，不要使用对应 Skill 名称假定其已经可用。
