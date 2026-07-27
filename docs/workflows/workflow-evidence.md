# Workflow Evidence 与紧凑验证使用指南

## 目的

Workflow Evidence 将四个仓库工作流 Skills 中可确定执行的 Git、GitHub、Project、
Checks、SHA、分支和稳定性事实收敛为有界 JSON。Validation Runner 负责执行当前适用
检查并压缩成功输出。

```text
Skill：权限、阶段、语义判断、finding、verdict
Evidence：确定性事实、规范化、snapshot/recheck
Validation：命令编排、退出码、有限摘要
```

规范性规则位于：

```text
.agents/policies/workflow-evidence.md
.agents/policies/command-execution.md
```

## 文件

```text
tools/agent_workflow/workflow_common.py
tools/agent_workflow/workflow_evidence.py
tools/agent_workflow/workflow_validation.py
tools/agent_workflow/trusted_runner.py
```

本地产物：

```text
.agents/evidence.local/
.agents/validation.local/
```

两者必须保持 Git ignored。

## Evidence CLI

查看帮助：

```powershell
python -X utf8 tools/agent_workflow/workflow_evidence.py --help
python -X utf8 tools/agent_workflow/workflow_evidence.py pr-review-snapshot --help
```

主要操作：

```text
delivery-preflight
delivery-readiness
pr-review-snapshot
pr-review-recheck
closeout-plan
closeout-final
feature-audit-snapshot
feature-audit-recheck
```

示例：

```powershell
python -X utf8 tools/agent_workflow/workflow_evidence.py `
  pr-review-snapshot `
  --task 123 `
  --pr 124 `
  --expected-title "[Task] 示例" `
  --expected-base-sha <base-sha> `
  --expected-head-sha <head-sha>
```

stdout 只包含紧凑 JSON。完整 Task/PR 正文、完整 diff 和源码不会写入 snapshot。
Agent 仍必须在语义审查阶段读取这些内容。

Snapshot 默认写入：

```text
.agents/evidence.local/snapshots/<snapshot-id>.json
```

Recheck：

```powershell
python -X utf8 tools/agent_workflow/workflow_evidence.py `
  pr-review-recheck `
  --snapshot-id <snapshot-id>
```

Recheck 会重新取证，再比较 base/head、effective diff、checks、threads、main 或 direct
child set。旧 snapshot 只作为比较基线，不作为当前事实。Feature snapshot 还会为 direct
child 的 closing PR 记录 merge SHA 与 check-run 摘要；该元数据不替代当前-main 语义审计。

## Validation Runner

```powershell
python -X utf8 tools/agent_workflow/workflow_validation.py run `
  --phase delivery
```

支持阶段：

```text
delivery
review
closeout
feature-audit
```

当治理文件发生变化时，可显式要求 Skill validators：

```powershell
python -X utf8 tools/agent_workflow/workflow_validation.py run `
  --phase review `
  --base-sha <reviewed-base-sha> `
  --include-skill-validators `
  --require-skill-validator
```

`--base-sha` 使 runner 使用 `<base-sha>...HEAD` 识别 PR 中的治理变更；不提供时只
检查当前工作树相对 `HEAD` 的变化。Review 应传入 locked PR base SHA。

Runner 根据当前 `uv.lock`、`pyproject.toml`、测试目录和治理变化选择命令。成功时
只输出命令 ID、exit code、duration 和结果摘要；失败时输出有界诊断。经过脱敏和大小
限制的日志位于：

```text
.agents/validation.local/<run-id>/
```

Delivery、Review、Closeout 和 Feature Audit 仍分别运行验证，不能跨阶段复用旧结果。

## Trusted Runner

PR 修改 governance、Evidence 或 Validation 时，PR head 中的工具不能控制自己的审查。
应从 locked base 获取并运行 `trusted_runner.py`，或直接使用 detached base worktree。

```powershell
python -X utf8 tools/agent_workflow/trusted_runner.py `
  --trusted-sha <base-sha> `
  --tool evidence -- `
  pr-review-snapshot `
  --task 123 `
  --pr 124 `
  --expected-base-sha <base-sha> `
  --expected-head-sha <head-sha>
```

重要：上面被执行的 `trusted_runner.py` 本身也必须来自 trusted base/main，不得用 PR
head 版本为同一 PR 建立信任。

Validation 也可从同一 trusted commit 执行：

```powershell
python -X utf8 <trusted-base>/tools/agent_workflow/trusted_runner.py `
  --trusted-sha <base-sha> `
  --tool validation -- `
  run --phase review --base-sha <base-sha> --include-skill-validators
```

Trusted Runner 将同一 commit 中的工具提取到 ignored evidence 目录，并在输出中记录：

```text
trusted SHA
runner source SHA
runner content SHA-256
```

## Gate 语义

Evidence gate 只有：

```text
pass
fail
unknown
```

- `pass`：当前规范化事实满足机械门禁；
- `fail`：当前事实明确不满足；
- `unknown`：endpoint、权限、套餐限制、数据缺失或冲突导致不能证明。

`unknown` 不得当作成功。GitHub plan-limit `403`、未配置 Required Checks 和普通
check runs 会分别记录，不合并为同一种状态。

## Compact report

只有在无 finding、无失败/未知关键门禁、无 drift、无 fallback 且验证全通过时，Skill
才使用紧凑成功报告。异常路径继续输出详细证据。

Handoff 不复制完整正文、diff、验证 stdout 或上一阶段完整报告。独立 Review 必须重新
取证，不能把 Delivery handoff 作为正确性证据。

## 仓库外 Token 分析

Workflow Evidence 和 Validation 不执行运行期 Token 测量，也不写入跨阶段 usage
事件。维护者可在仓库外使用 Codex rollout 日志和 Task 元数据生成版本化分析报告。
不得为了外部分析额外运行 Evidence、GitHub 查询或验证；原始 rollout 日志和外部
分析报告不得提交本仓库，分析是否可用也不影响 Skill verdict。

## Closeout 与 Feature Audit 说明

`closeout-plan` 以只读方式验证 merge commit 是否可从当前 `origin/main` 到达，不要求
`origin/main` 仍精确等于 merge commit，因此不会因后续正常 merge 产生虚假失败。它只
报告精确分支清理门禁，不删除分支。

`feature-audit-snapshot` 固定 audited `origin/main`、direct child set 与 child closing-PR
摘要；Agent 仍需逐项完成 acceptance coverage、current-main 集成和安全判断。
