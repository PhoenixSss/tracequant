# Workflow Evidence 与统一 Validation Runner

## 目的

Task 工作流使用两个固定入口替代重复的 `gh`、Git 和验证命令链：

```text
tools/agent_workflow/wsl2_github_evidence_runner.py
tools/agent_workflow/wsl2_validation_runner.py
```

职责边界：

```text
Skill：权限、阶段、语义判断、finding、verdict
Evidence Runner：确定性 Git/GitHub 事实、snapshot/recheck、partial/unknown
Validation Runner：targeted/CI-equivalent/阶段验证、退出码、有界诊断
Maintainer：人工 Merge 与 Feature closeout
```

规范性规则：

```text
.agents/policies/workflow-evidence.md
.agents/policies/command-execution.md
```

本地产物只允许写入并保持 Git ignored：

```text
.agents/evidence.local/
.agents/validation.local/
```

## Task Skills 的唯一正常路径

| 阶段 | Evidence | Validation |
| --- | --- | --- |
| Delivery preflight | `delivery` | 按需 named `targeted*` |
| Delivery final | `delivery-readiness` | `workflow-delivery --base-sha <base>` |
| Independent Review | trusted-base `review` + `recheck` | trusted-base `workflow-review --base-sha <base>` |
| Closeout | `closeout-readonly` + `recheck` | `workflow-closeout --base-sha <PR base>` |

新 Runner 必须替代旧路径，而不是叠加。正常 Task Skill 不再直接调用
`workflow_evidence.py`、`workflow_validation.py`，也不在固定 Runner 之后重复完整
`gh`/Git 查询链或全套 `uv` 验证。

底层 CLI 仍是 Runner 的版本化实现与测试对象：

```text
tools/agent_workflow/workflow_evidence.py
tools/agent_workflow/workflow_validation.py
```

只有内部工具测试、Task #85 一次性迁移自举，或 Skill 明确授权的单个
partial/unknown/fail 目标诊断可以直接使用；不得恢复为并行正常路径。

## Evidence Runner

查看帮助：

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py --help
```

Profiles：

```text
delivery
delivery-readiness
review
pre-merge
closeout-readonly
recheck
```

Delivery 示例：

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py delivery \
  --task 85 \
  --expected-main-sha <main-sha>
```

Readiness 示例：

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py delivery-readiness \
  --task 85 \
  --pr <pr> \
  --expected-base-sha <base-sha> \
  --expected-head-sha <head-sha>
```

Closeout 示例：

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py closeout-readonly \
  --task 85 \
  --pr <pr> \
  --expected-head-sha <reviewed-head> \
  --expected-merge-sha <merge-sha>
```

Recheck：

```bash
tools/agent_workflow/wsl2_github_evidence_runner.py \
  recheck --snapshot-id <snapshot-id>
```

Runner 固定仓库、profile、参数 schema、GitHub 查询和只读 Git 操作；不接受
任意 repo、REST/GraphQL path、`gh`/Git 参数、Shell 字符串、cwd 或输出路径。
它在 read-only mode 下不执行 `git fetch`，使用固定 `git ls-remote --heads`
核验远端 refs。

退出码：

```text
0 = pass
3 = partial
4 = evidence fail
2 = invocation / integrity / schema error
```

`partial`、`unknown`、plan-limit `403`、截断或 endpoint 失败不能当作完整成功。
Snapshot 只保存有界规范化事实，不保存完整 Task/PR body、完整 diff 或源码。
Agent 仍需读取完整规格和代码做语义判断。

### Capability-limited cleanup eligibility

GitHub Required Checks 配置查询若被明确分类为 plan-limit `403`，Evidence 必须继续
报告 `required_checks_configuration = unknown`，整体结果保持 `partial`。Closeout
可额外计算 `cleanup_eligibility.status =
eligible-under-capability-limited-policy`，但该字段只允许作为精确任务分支清理输入，
不得用于 Merge、push、Issue、Project、label、Review 或其他写操作。

该资格要求已合并 PR、正确 closing linkage、Task 最终元数据、稳定 final recheck、
至少一个实际 check run 且全部为成功终态、`main == origin/main == merge SHA`、
精确 local/remote 分支名和 tip、PR head tree 与 merge tree 相等、工作区干净、目标
分支未被 worktree 占用，且 cleanup plan 只包含该 Task 分支。认证、scope、权限、
rate limit、网络、schema、服务或未知错误，以及无 checks、失败/等待/stale checks、
ref drift、tree drift 或 worktree 冲突，均保持 cleanup blocked。

## Validation Runner

查看帮助：

```bash
tools/agent_workflow/wsl2_validation_runner.py --help
```

通用 profiles：

```text
current-ci-equivalent
targeted
targeted:tools-tests
targeted:workflow-tests
post-merge
```

Task Skill 阶段 profiles：

```text
workflow-delivery --base-sha <base>
workflow-review --base-sha <base>
workflow-closeout --base-sha <PR base>
```

示例：

```bash
tools/agent_workflow/wsl2_validation_runner.py \
  workflow-delivery --base-sha <task-base-sha>
```

阶段 profile 只通过一个固定入口调用底层 workflow validator，执行当前
CI-equivalent checks，并根据 `base...HEAD` 检测治理变化，强制运行全部 Skill
validators。`workflow-closeout` 还要求：

```text
branch == main
working tree clean
local main == origin/main
```

成功 stdout 只有紧凑 digest。完整脱敏结果和有界失败诊断位于：

```text
.agents/validation.local/wsl2-runs/<run-id>/
```

Targeted 结果不得冒充 CI-equivalent。Delivery、Review、Closeout 分别运行，不能
跨阶段复用旧结果。

## Trusted Review front door

Independent Review 不能使用 PR head 的治理工具证明自身。Bootstrap
`trusted_runner.py` 必须来自 locked base 或 detached trusted-base worktree。

正常 post-Task-85 Review：

```bash
tools/agent_workflow/trusted_runner.py \
  --tool evidence-runner \
  --trusted-sha <base-sha> -- \
  review --task <task> --pr <pr> \
  --expected-base-sha <base-sha> \
  --expected-head-sha <head-sha>

tools/agent_workflow/trusted_runner.py \
  --tool validation-runner \
  --trusted-sha <base-sha> -- \
  workflow-review --base-sha <base-sha>
```

`trusted_runner.py` 从同一个 commit 提取完整 Runner、profiles、Rules 和底层工具
bundle，验证 manifest/digest，再对目标 worktree 执行。输出记录 trusted SHA、
entry digest 和 bundle files。

Task #85 自身 PR 的 base 尚不支持新 front-door tool choices，因此使用 predecessor
base raw trusted control plane 做一次性独立审查。这是迁移自举，不是永久 fallback；
Task #85 合并后后续 Task 必须使用固定 trusted front doors。

## Gate 与失败展开

Evidence gate：

```text
pass
fail
unknown
```

固定 Runner 还可能返回：

```text
partial
drift
schema/version/integrity blocked
```

处理原则：

1. 保留原始 snapshot/result identity 和状态；
2. 只展开命名的 unknown/fail/truncated/conflicting fact 或 failed command；
3. 记录 fallback 与 limitation；
4. 不执行完整旧链；
5. 未解决时停止 dependent write/verdict。

Runner unavailable、schema/version mismatch 或 trusted bundle failure 是 control-plane
门禁失败，不是自动恢复 legacy path 的理由。回滚必须是维护者授权的仓库变更。

## 写操作边界

Evidence/Validation Runner 都是只读或仅写 ignored local artifacts。以下行为始终
留在 Skill 的明确权限和审批门禁中：

```text
Project / label / Issue / PR writes
git fetch / switch / add / commit / push
branch deletion
worktree creation/removal
manual Merge
```

`task-pr-review` 始终只读。人工 Merge 约束不变。

## Compact report

只有在所需 evidence/validation 完整通过、无 drift、finding、fallback、冲突或待
决策时，Skill 才输出紧凑成功报告。报告仍需包含 identity、URL、branch/SHA、scope、
validation/checks、lifecycle、threads、limitations、未执行操作和下一步。

异常路径输出详细报告。Handoff 不复制完整正文、diff、成功日志或上一阶段报告；
Delivery handoff 不是 Review evidence。

## 回滚与兼容

回滚顺序：

1. 停止新 Skill 调用；
2. 在单独维护 Task 中恢复 predecessor Skills/policy；
3. 同步 compatible Runner/profile/Rules versions；
4. 完整验证后合并；
5. 不在运行中静默并行旧路径。

兼容门禁至少检查 Runner version、profile set、schema、trusted bundle manifest 和
Codex Rules activation。任何不匹配都 fail closed。

## 仓库外 Token 分析

Runner 记录调用点、内部 operation counts、duration 和 output size，但不执行运行期
Token 测量。Token 分析继续由维护者在仓库外使用 rollout JSONL 和 Task metadata
完成。Task #85 只归档静态命令路径收敛与机制材料；Task #86 负责受控 Candidate
对照和 Token 结论。
