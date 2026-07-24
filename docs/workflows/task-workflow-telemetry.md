# Task Workflow Telemetry 开发人员使用指南

## 目的与适用范围

本文档面向使用、维护或扩展 Task Workflow Telemetry 的开发人员，说明如何在
不改变正常开发流程的前提下，为指定 Task 建立 token 与流程消耗基准，或执行
一次信息性消耗抽查。

Telemetry 是本地旁路测量能力，不是新的 workflow Skill，也不是任何正确性、
合并或 Feature 完成证据：

```text
Observation
!= workflow authorization
!= correctness evidence
!= merge authorization
!= Feature completion evidence
```

规范性规则位于：

```text
.agents/policies/task-workflow-telemetry.md
.agents/policies/command-execution.md
```

四个 workflow Skills 只负责在 active run 存在时追加聚合事件：

```text
.agents/skills/task-delivery/SKILL.md
.agents/skills/task-pr-review/SKILL.md
.agents/skills/task-closeout/SKILL.md
.agents/skills/feature-completion-audit/SKILL.md
```

本文档是开发人员使用指南。出现冲突时，以 `AGENTS.md`、适用
`AGENTS.override.md`、上述 policy 和当前 Skill 为准。

## 文件结构

仓库提交的文件：

```text
.agents/policies/task-workflow-telemetry.md
.agents/task-workflow-telemetry.example.toml
tools/agent_workflow/telemetry.py
tests/tools/test_task_workflow_telemetry.py
```

维护者本地文件：

```text
.agents/task-workflow-telemetry.local.toml
.agents/telemetry.local/
```

本地配置和数据目录必须保持 Git ignored，不得暂存、提交或进入 PR。

一次 run 的默认目录结构：

```text
.agents/telemetry.local/
├─ active/
│  └─ task-123.json
└─ runs/
   └─ tw-123-<timestamp>-<random>/
      ├─ manifest.json
      ├─ events.jsonl
      └─ summary.json
```

- `manifest.json`：run 身份、分类、模式、workflow SHA 和状态；
- `events.jsonl`：append-only 阶段事件；
- `summary.json`：`finish` 生成的确定性聚合结果；
- `active/task-<number>.json`：当前 Task 的 active pointer，`finish` 后删除。

不要手工修改这些文件。使用 CLI 写入、验证和汇总。

## 前置条件

在启动测量前确认：

1. 当前工作目录是仓库根目录；
2. 已经存在有效的本地配置；
3. local config 与 output directory 均被 `.gitignore` 精确覆盖；
4. 已知 Task 编号和 canonical title；
5. 已知当前 workflow 所依据的 `main` SHA；
6. 已为 Task 选择合理的分类；
7. 本次 Task 不会同时修改被测 workflow；否则基准会被污染。

Telemetry CLI 不访问网络，也不会自行执行 Git、GitHub 或验证命令。Task、
repository slug 和 SHA 必须由开发人员使用正常流程已经获得的事实提供。

## 第一次配置

复制 example config：

```powershell
Copy-Item `
  .agents/task-workflow-telemetry.example.toml `
  .agents/task-workflow-telemetry.local.toml
```

默认配置：

```toml
schema_version = 1
output_dir = ".agents/telemetry.local"
default_mode = "baseline-only"
store_raw_transcript = false
store_command_output = false
store_file_contents = false
allow_usage_patch = true
```

schema v1 中以下字段必须保持 `false`：

```toml
store_raw_transcript = false
store_command_output = false
store_file_contents = false
```

验证忽略规则：

```powershell
git check-ignore -v .agents/task-workflow-telemetry.local.toml
git check-ignore -v .agents/telemetry.local/example.jsonl
```

任何一条命令没有命中预期 ignore rule 时，不要启动 Telemetry。

## 选择测量模式

### `baseline-only`

用于建立指定类型 Task 的完整基准。

适合：

- 首次测量一种 Task 类型；
- workflow 规则发生较大变化后重新建立基准；
- 完成 token 优化后建立新的对照基准。

该模式不会自动比较历史样本，也不会自动提出优化。

### `spot-check`

用于抽查指定 Task 是否存在异常消耗。

CLI 默认只比较以下四个分类完全一致的历史 run：

```text
task_kind
size
risk_class
workflow_shape
```

少于 3 个同类已完成样本时，只做结构性对比，不生成统计异常结论。
异常 flag 仅供分析，不改变 Task、PR 或 Feature verdict。

## 选择 Task 分类

推荐使用以下值：

```text
task_kind:
  feature-code
  bugfix
  refactor
  governance-docs
  dependency-maintenance
  data-pipeline
  research
  other

size:
  S
  M
  L

risk_class:
  normal
  high-correctness
  financial-safety

workflow_shape:
  task-only
  task-with-re-review
  task-plus-feature-audit
```

`task_kind`、`risk_class` 和 `workflow_shape` 必须是小写 slug。为了保证历史
数据可比较，应优先使用上述固定值，不要为同一含义创建多个近义分类。

`workflow_shape` 的选择：

- `task-only`：一次 review，通过后 closeout；
- `task-with-re-review`：预期或实际发生了修复、新 head 和重新独立审查；
- `task-plus-feature-audit`：Task closeout 后，同一 run 还要记录
  `feature-completion-audit`。

若 Task 在运行中从一次审查变为重新审查，不要新建 run。继续记录 rework 和
review-run 事件；在分析中说明实际 workflow shape。不要为了修正分类而改写
append-only 历史。

## 快速开始

下面示例为 Task #123 建立一个普通功能开发基准：

```powershell
python tools/agent_workflow/telemetry.py start `
  --task 123 `
  --task-title "[Task] 实现示例功能" `
  --mode baseline-only `
  --task-kind feature-code `
  --size M `
  --risk-class normal `
  --workflow-shape task-only `
  --repository PhoenixSss/quant-system `
  --workflow-main-sha <current-main-sha> `
  --model <model-identifier>
```

`--model` 可省略。`--mode` 省略时使用 local config 的 `default_mode`。

成功输出示例：

```json
{
  "active": true,
  "mode": "baseline-only",
  "run_id": "tw-123-20260725T120000-1a2b3c4d",
  "run_path": ".agents/telemetry.local/runs/tw-123-20260725T120000-1a2b3c4d",
  "task_number": 123
}
```

查看状态：

```powershell
python tools/agent_workflow/telemetry.py status --task 123 --json
```

没有 local config 或 active run 时，`status` 返回非阻塞 JSON，而不是使正常
workflow 失败。

## 标准运行时序

### 1. 在 `task-delivery` 前启动

必须先由维护者显式 `start`。Skills 不会隐式创建 run。

启动后再进入正常流程：

```text
task-delivery
→ task-pr-review
→ manual merge
→ task-closeout
```

四个 Skills 只使用正常工作已经产生的事实维护聚合计数，不得为 Telemetry
额外查询 GitHub、重新读取文件或重跑验证。

### 2. 每个新会话先检查状态

Task 阶段：

```powershell
python tools/agent_workflow/telemetry.py status --task 123 --json
```

Feature audit 阶段：

```powershell
python tools/agent_workflow/telemetry.py status --feature 2 --json
```

没有 active run 时，不要创建隐式 run，也不要增加报告字段。

### 3. 阶段结束时追加 summary

正常情况下，workflow Skill 会按照 policy 追加一条 `phase-summary`。需要手工
补录时使用：

```powershell
python tools/agent_workflow/telemetry.py record `
  --task 123 `
  --phase task-delivery `
  --data-file .agents/telemetry.local/input/task-delivery.json
```

输入文件可以放在 ignored telemetry 目录中。不要把输入 JSON 暂存或提交。

### 4. 人工 Merge 阶段

人工 Merge 没有模型 usage 也可以记录。使用 `manual-merge` phase，token 字段
保持 `null`，source 使用 `unavailable`。

```powershell
python tools/agent_workflow/telemetry.py record `
  --task 123 `
  --phase manual-merge `
  --data-file .agents/telemetry.local/input/manual-merge.json
```

### 5. 会话结束后补录 usage

客户端在会话结束后才显示 token 时，使用：

```powershell
python tools/agent_workflow/telemetry.py patch-usage `
  --task 123 `
  --phase task-pr-review `
  --data-file .agents/telemetry.local/input/review-usage.json
```

同一 phase 有多个可补录事件时，使用 `record` 输出的 `event_id` 精确指定：

```powershell
python tools/agent_workflow/telemetry.py patch-usage `
  --task 123 `
  --phase task-pr-review `
  --event-id ev-000003-1a2b3c4d `
  --data-file .agents/telemetry.local/input/review-usage.json
```

已有 `runtime-exact` 或 `client-export` usage 时，CLI 会拒绝覆盖。

### 6. 完成 run

普通 Task 在 `task-closeout` 后完成：

```powershell
python tools/agent_workflow/telemetry.py finish --task 123
```

取消或失败：

```powershell
python tools/agent_workflow/telemetry.py finish --task 123 --status cancelled
python tools/agent_workflow/telemetry.py finish --task 123 --status failed
```

`task-plus-feature-audit` 不应在 closeout 后立即 finish。保持同一 run active，
等待 Feature audit 追加阶段事件，再执行：

```powershell
python tools/agent_workflow/telemetry.py finish --feature 2
```

不要为 Feature audit 创建第二个 run。

### 7. 验证和查看摘要

```powershell
python tools/agent_workflow/telemetry.py validate --task 123
python tools/agent_workflow/telemetry.py summarize --task 123 --format markdown
```

指定历史 run：

```powershell
python tools/agent_workflow/telemetry.py validate `
  --task 123 `
  --run-id tw-123-20260725T120000-1a2b3c4d

python tools/agent_workflow/telemetry.py summarize `
  --task 123 `
  --run-id tw-123-20260725T120000-1a2b3c4d `
  --format json
```

## Phase summary JSON 模板

下面是可直接复制的完整模板。未知值使用 `null`，不得使用 `0` 假装已经测量。

```json
{
  "schema_version": 1,
  "event_type": "phase-summary",
  "identity": {
    "task_canonical_title": "[Task] 实现示例功能",
    "pr_number": 124,
    "feature_number": null,
    "base_sha": "abcdef1234567",
    "head_sha": "1234567abcdef",
    "workflow_main_sha": "fedcba7654321",
    "model": "model-identifier",
    "changed_files_count": 5,
    "changed_lines": 220,
    "acceptance_criteria_count": 8
  },
  "usage": {
    "source": "unavailable",
    "input_tokens": null,
    "cached_input_tokens": null,
    "output_tokens": null,
    "reasoning_tokens": null,
    "total_tokens": null,
    "model": "model-identifier"
  },
  "context": {
    "governance": {
      "files_read": 4,
      "bytes_read": 18000,
      "lines_read": 420,
      "repeated_bytes_estimate": 6000
    },
    "source": {
      "files_read": 7,
      "bytes_read": 32000,
      "lines_read": 900,
      "repeated_bytes_estimate": null
    },
    "tests": {
      "files_read": 3,
      "bytes_read": 9000,
      "lines_read": 260,
      "repeated_bytes_estimate": 0
    }
  },
  "operations": {
    "tool_calls": 18,
    "github_queries": 5,
    "git_commands": 8,
    "validation_commands": 5,
    "sandbox_attempts": 20,
    "elevated_attempts": 3,
    "retries": 3,
    "retry_categories": {
      "credential-session": 1,
      "filesystem-isolation": 2
    },
    "command_categories": {
      "git-read": 5,
      "git-write-authorized": 3,
      "github-read": 4,
      "github-write-authorized": 1,
      "test": 1,
      "lint": 2,
      "format": 1,
      "type-check": 1
    }
  },
  "report": {
    "report_characters": 3600,
    "report_lines": 90,
    "report_estimated_tokens": 900,
    "report_estimation_method": "chars-div-4",
    "copied_to_next_phase": false
  },
  "rework": {
    "commits_added_after_first_handoff": 0,
    "head_sha_changes": 0,
    "independent_review_runs": 0,
    "review_invalidations": 0,
    "maintainer_decisions": 0,
    "interruptions": 0,
    "findings_by_severity": {
      "blocking": 0,
      "high": 0,
      "medium": 0,
      "low": 0,
      "nit": 0
    }
  },
  "outcome": {
    "phase_result": "pass",
    "workflow_result": null,
    "review_verdict": null,
    "feature_audit_verdict": null,
    "validation_passed": true,
    "telemetry_complete": true
  },
  "limitations": [
    "client-usage-not-exported"
  ]
}
```

注意：

- `limitations` 只写简短、聚合、非敏感标识；
- 不写完整命令、文件内容、绝对路径、prompt 或 stdout；
- `repeated_bytes_estimate` 是估算；无法判断时使用 `null`；
- 明确确认没有发生的计数可以是 `0`；
- 未测量或未知的计数应为 `null`；
- `identity` 中的 Task title、Feature number 和 workflow SHA 不得与 manifest 冲突。

## Usage patch JSON

支持只传 usage object：

```json
{
  "source": "client-export",
  "input_tokens": 125000,
  "cached_input_tokens": 80000,
  "output_tokens": 7600,
  "reasoning_tokens": null,
  "total_tokens": 132600,
  "model": "model-identifier"
}
```

也支持带 schema wrapper：

```json
{
  "schema_version": 1,
  "usage": {
    "source": "client-export",
    "input_tokens": 125000,
    "cached_input_tokens": 80000,
    "output_tokens": 7600,
    "reasoning_tokens": null,
    "total_tokens": 132600,
    "model": "model-identifier"
  }
}
```

允许的 source：

```text
runtime-exact
client-export
estimated-external
unavailable
```

- `runtime-exact`：runtime 直接提供的精确聚合计数；
- `client-export`：受支持客户端导出的精确聚合计数；
- `estimated-external`：仓库外固定方法产生的估算；
- `unavailable`：无法取得 usage。

不得根据字符数将 usage 标为 exact。字符估算只属于报告量或
`estimated-external`。

## 记录中断、返工和重审

一条 phase 只能有一个主要 `phase-summary`。额外情况使用不同 event type：

```text
interruption
rework
review-run
manual-merge
```

例如 review 因新 head 失效时，可追加 `review-run` 或 `rework` 事件，并在
`rework.review_invalidations`、`head_sha_changes` 和
`independent_review_runs` 中记录数量。

不要删除旧 review event，也不要覆盖第一次 head SHA。append-only 历史用于
计算新 SHA 和完整重审的放大成本。

## 三种常见 workflow 示例

### 普通一次通过 Task

```text
start(task-only)
→ task-delivery phase-summary
→ task-pr-review phase-summary
→ manual-merge event
→ task-closeout phase-summary
→ finish
→ validate
→ summarize
```

### 修复后重新审查

```text
start(task-with-re-review)
→ task-delivery
→ review-run #1
→ rework / new head
→ review-run #2
→ manual-merge
→ task-closeout
→ finish
```

保持同一 run，不要在产生新 head 后重新 `start`。

### Task 后执行 Feature audit

```text
start(task-plus-feature-audit, --feature 2)
→ task-delivery
→ task-pr-review
→ manual-merge
→ task-closeout
→ status --feature 2
→ feature-completion-audit
→ finish --feature 2
```

Feature audit 会话不能把前序 Task telemetry 当作 Feature 完成证据。

## 读取摘要

Markdown 摘要包含：

- run 身份和分类；
- workflow / telemetry 版本；
-各 phase usage；
- exact / estimated / unavailable coverage；
-上下文量；
-工具、重试和报告量；
-返工与 review invalidation；
- findings、validation、维护者决策和 workflow result；
-缺失 phase 与 limitations；
- spot-check 模式下的同类样本数量、历史中位数、delta 和 anomaly flags。

典型 anomaly flags：

```text
total-token-high
phase-token-high
repeated-context-high
tool-output-high
review-restart-high
retry-high
report-size-high
```

这些标记只说明值得进一步分析，不表示 workflow 失败，也不应自动触发 Skill
修改。

## 如何使用测量结果

推荐长期循环：

```text
baseline-only
→ 去敏聚合分析
→ 维护者创建独立 Token Optimization Task
→ 优化合并
→ 使用相近 Task 再次 baseline-only 或 spot-check
```

对日常抽查：

```text
spot-check
→ 与同类样本比较
→ 定位异常 phase 和成本来源
→ 维护者决定是否进入优化循环
```

不要把不同 Task 类型、Size、风险或 workflow shape 的样本合并为一个全局
平均值。不同模型或 workflow main SHA 的结果也应明确分开解释。

## 常见错误与处理

### `local telemetry config is missing`

`status` 会把它作为非阻塞 unavailable 返回。需要测量时，先复制 example config。
不需要测量时无需处理。

### `not covered by an exact .gitignore rule`

local config 或 output directory 没有被精确忽略。检查 `.gitignore`，不要改为
写入其他未忽略目录，也不要通过 elevation 绕过安全检查。

### `Task #... already has active run`

该 Task 已经存在 active pointer。先执行 `status`，恢复原 run。只有确认原 run
应结束时才执行 `finish`；不要删除 pointer 伪造新 run。

### `primary phase summary already exists`

同一 phase 已经有主要 summary。新增中断、返工或 review 信息时使用对应
event type，不要覆盖原 summary。

### `existing exact usage cannot be overwritten`

目标事件已有 exact usage。保留现有记录；估算值不能覆盖精确值。

### `no target event found for usage patch`

目标 phase 尚无可补录事件，或 `--event-id` 不正确。先检查 `record` 输出和
`status`，不要创建虚假 phase。

### `telemetry_complete: false`

查看 `missing_phases` 和每个 event 的 `outcome.telemetry_complete`。这只表示
测量不完整，不改变正常 workflow 结果。

### spot-check 样本不足

同类完成 run 少于 3 个。只做结构性比较，继续积累样本；不要从一个样本推导
统计异常。

## 隐私与安全检查

绝对禁止记录：

- token、cookie、密码、私钥、认证 header；
- 完整 prompt、assistant response、会话或私有 reasoning；
-源码、测试、文档内容；
-完整 stdout / stderr；
-完整环境变量；
-用户主目录或敏感绝对路径；
-包含敏感参数的完整命令行。

允许记录：

- Task / PR / Feature 编号；
- repository slug；
- Git SHA；
-仓库相对路径类别；
-计数、时间、阶段结果和聚合 token usage；
-非敏感 retry / limitation 分类。

CLI 会拒绝已知敏感字段和值，但调用方仍必须先进行最小化和去敏。

## 开发与扩展要求

修改 Telemetry CLI、schema 或 workflow hook 时：

1. 先更新规范性 policy；
2. schema 不兼容变化必须提升 `schema_version`；
3. 旧 run 保持只读，不静默迁移；
4. 保持 events append-only；
5. 保持 deterministic summary；
6. 不新增网络访问；
7. 不让 CLI 执行 Git、GitHub 或验证命令；
8. 不新增第三方依赖，除非另有批准；
9. 四个 Skills 只保留最小 hook，不复制 schema；
10. 不扩大 review / Feature audit 的只读例外；
11. 新增字段必须经过敏感数据审查；
12. 更新测试、example config、本指南和 `agent-skills.md`；
13. 重新运行完整 CI 等价验证和 Skill validators。

重点测试：

```text
start / duplicate start
valid / invalid config
ignored / unignored storage
append-only record
duplicate phase summary
usage patch
exact usage overwrite rejection
finish / validate / summarize
schema version rejection
sensitive field rejection
incomplete run
spot-check sample insufficiency
deterministic summary
```

## 开发人员操作清单

开始前：

- [ ] local config 存在且合法；
- [ ] local config 与 data directory 均被 Git ignored；
- [ ] Task 编号和 canonical title 正确；
- [ ] workflow main SHA 已确认；
- [ ] Task 分类已确定；
- [ ] 测量模式已确定；
- [ ] 被测 Task 不同时修改被测 workflow。

运行中：

- [ ] 每个会话只检查一次 status；
- [ ] 不为测量增加 GitHub 查询或验证；
- [ ] 每个 phase 最多一个主要 summary；
- [ ] 未知值使用 `null`；
- [ ] 不记录敏感内容；
- [ ] 新 head、重审和中断追加事件，不覆盖历史；
- [ ] telemetry 失败不改变 workflow verdict。

结束后：

- [ ] 必要 phase 均已记录；
- [ ] usage 已按实际来源补录；
- [ ] `finish` 已执行；
- [ ] `validate` 通过；
- [ ] 已查看 sanitized summary；
- [ ] 原始 telemetry 未进入 Git index；
- [ ] 是否创建独立 Token Optimization Task 由维护者决定。
