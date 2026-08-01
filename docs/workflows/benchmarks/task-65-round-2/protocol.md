# Task #65 第二轮 Task Workflow Token 实验协议

> Protocol ID: `task-65-round-2-v1`
> Freeze time: `2026-08-01T13:55:00+08:00`
> Canonical text: UTF-8, LF, no BOM

## 1. 目的与边界

本协议以同一份 Task #65 业务规格，对比当前原生 Windows 工作流与后续 WSL2
优化候选的人工 Merge 前成本和质量。协议只覆盖：

```text
task-delivery + 独立 task-pr-review
```

任何实验臂都不得自动 Merge。人工 Merge、`task-closeout`、Merge 后 CI、Issue/Project
状态收敛、labels 和 branch cleanup 均不属于本轮正式比较。

本任务不实现 `InstrumentId`、`TimeRange`、`OHLCVBar` 或共享 Fixtures，不运行
Windows/candidate 基准，不恢复仓库内 Telemetry，也不决定获胜实验臂。

## 2. 规范性冻结输入

严格可比实验必须同时匹配 `benchmark-manifest.json` 中的：

- Task #65 冻结正文 SHA-256；
- Parent、依赖与关系快照；
- repository base SHA；
- workflow main SHA；
- `AGENTS.md`、三项 Task Skills、政策和 workflow 工具 SHA；
- CI、`pyproject.toml`、`uv.lock` 和 repository-side config SHA；
- 模型、Codex、Guardian、用户级 rules、local execution profile 和环境摘要。

Task #65 冻结正文保留原有领域范围、验收难度、异常输入、序列化、Fixtures 和验证
要求，只移除或改写失效的运行期 Telemetry 内容。原文、冻结稿和统一 diff 均在本目录。

## 3. 基线定义

### 3.1 `historical-baseline`

Task #63 和 #64 的历史完整工作流：

```text
task-delivery + task-pr-review + task-closeout
```

历史报告中的完整 Task 总量不得直接与本轮 `merge-pre` 总量比较。允许的共同阶段重算：

| Task | Delivery | Review | historical merge-pre | Full workflow |
| --- | ---: | ---: | ---: | ---: |
| #63 | 4,448,125 | 1,485,001 | 5,933,126 | 8,692,744 |
| #64 | 6,670,691 | 1,846,095 | 8,516,786 | 11,969,449 |

历史数据仅用于提供规模、阶段、actor 和命令画像，不作为 Task #65 两臂之间的因果
对照。报告和原始 rollout 保存在仓库外。

### 3.2 `current-baseline`

Task #65 在冻结业务正文、冻结 base、当前 Windows 环境、当前 workflow、当前 Skills、
当前 Codex/model/config 下完成：

```text
task-delivery + 新会话独立 task-pr-review
```

在固定 Review verdict 后停止，不人工 Merge，不执行 Closeout。

### 3.3 `candidate`

Task #65 在同一冻结业务正文和同一业务 base 上，使用已批准的 WSL2 环境、统一 Runner、
最小权限 Rules 和更新后的 Skills 完成相同 `merge-pre`。除候选变量外，其余必需输入
必须与 `current-baseline` 一致；无法一致的字段必须列为不可比因素。

### 3.4 正式统计量

```text
merge_pre_total = delivery_total + review_total
uncached_input = input - cached_input
total = input + output
```

`reasoning_output` 已包含在 `output`，`cached_input` 已包含在 `input`，不得重复累计。

## 4. 实验臂身份和命名

| 对象 | Windows current-baseline | WSL2 candidate |
| --- | --- | --- |
| arm ID | `task65-current-windows` | `task65-candidate-wsl2` |
| branch | `experiment/task65-current-windows` | `experiment/task65-candidate-wsl2` |
| worktree/clone | 独立 | 独立 |
| PR title | `[Experiment] Task #65 current Windows merge-pre` | `[Experiment] Task #65 WSL2 candidate merge-pre` |
| PR body linkage | `Refs #65` | `Refs #65` |
| Delivery session | `task65-current-windows-delivery-<utc>` | `task65-candidate-wsl2-delivery-<utc>` |
| Review session | `task65-current-windows-review-<utc>` | `task65-candidate-wsl2-review-<utc>` |
| rollout prefix | `task65-current-windows-*` | `task65-candidate-wsl2-*` |

PR 不得包含 `Closes #65`、`Fixes #65`、`Resolves #65` 或等价关闭引用。最终只有维护者
选中的实现可以在单独受控步骤中改为关闭引用并人工 Squash Merge。

## 5. 隔离与信息防泄漏

1. 两臂从同一 base SHA 建立独立 worktree 或 clone；
2. 分支、PR、Codex 会话、Review 会话、rollout、缓存目录和外部归档目录独立；
3. 任一臂不得读取另一臂的代码、diff、commit、PR、Review findings、verdict、总结、
   rollout、Token 报告或维护者评价；
4. 独立 Review 只可接收 Task/PR 身份和 expected base/head SHA，不接收实施结论；
5. 当前臂不得读取 `.agents/evidence.local/`、`.agents/validation.local/` 中其他臂产物；
6. Candidate 设计者可以读取冻结协议和 Windows 环境诊断，但 Candidate 执行会话不得读取
   Windows 业务实现或结果；
7. 缓存是否共享必须在两臂保持一致并记录；不能证明一致时降低可比性；
8. 发现泄漏后立即中止该臂，不得通过删除聊天或文件来“恢复”严格可比性。

## 6. 每臂执行顺序

1. 验证冻结正文、base、workflow、Skill/config SHA；
2. 记录环境、Codex/model、Guardian、rules、execution profile 和 tool versions；
3. 创建隔离 worktree/clone、分支、会话和外部归档目录；
4. 运行 `task-delivery` 至 PR 已通过全部适用验证并准备独立审查；
5. 确认 PR 只使用 `Refs #65`，Task #65 未被关闭；
6. 在全新会话中运行严格只读 `task-pr-review`；
7. 若 head/diff/base/check/thread 漂移，旧 Review 失效，并按现行规则重新独立审查；
8. 固定 Review verdict 和 reviewed SHA 后停止；
9. 仓库外解析 rollout，生成指标和材料记录；
10. 不 Merge、不运行 Closeout、不修改 Task #65 lifecycle 状态。

## 7. 指标

### 7.1 Token 与 actor

- Total/Input/Cached/Uncached/Output Tokens；
- Delivery/Review Tokens；
- Root/Guardian/其他 subagent Tokens；
- Guardian turns；
- model/token-count events；
- cache hit ratio；
- 每阶段和每 actor 占比。

### 7.2 命令与环境

- tool、shell、Git、GitHub CLI、validation calls；
- sandbox/session-isolation failures；
- sandbox-first 成功、elevated-first、elevated retry、approval 和 denial 次数；
- Evidence/Validation Runner 调用及 fallback；
- duration；
- 失败命令、重试、恢复和人工介入。

### 7.3 业务与质量

- changed files/insertions/deletions；
- acceptance criteria 总数、pass/fail/not-applicable 和证据映射；
- CI-equivalent validation 结果；
- Review Blocking/High/Medium/Low/Nit findings；
- rework、修复 commit 和重复 Review；
- Review invalidation；
- Required Checks 配置与实际 check runs；
- 维护者最终采用、调整、否决或不可决定结论；
- limitations 和不可比因素。

任何 Token 降低都不能以减少验收覆盖、适用验证、独立 Review、缺陷发现能力、人工 Merge
硬门禁或安全边界为代价。

## 8. 质量门槛

实验臂只有同时满足下列条件，才可作为候选比较样本：

- 冻结 Task #65 业务验收覆盖 100%；
- 当前全部适用本地验证和 CI check runs 成功；
- 没有未解决 Blocking、High 或 Medium finding；
- Review verdict 绑定最终 base/head SHA，且未失效；
- PR 未自动关闭 Task #65；
- 没有自动 Merge、Closeout 或 lifecycle 越权；
- 原始 rollout、凭据、本地实验数据未进入业务 PR；
- 输出可由独立读者按 manifest 和原始日志复核。

出现业务返工不自动使样本无效，但必须完整计入 Token、时长、质量和人工介入，并在比较
中说明复杂度差异。

## 9. 可比性等级

### 严格可比

全部冻结字段一致；只有预先声明的候选变量不同；日志完整；无跨臂泄漏；质量门槛通过。

### 有条件可比

业务正文和 base 一致，但存在不能消除的工具小版本、缓存、网络、GitHub 服务状态、
重试或可观测性差异。可以报告方向性结果，但不得宣称纯因果降幅。

### 不可比

任一情况成立即不可比：

- Task 正文、Parent、依赖、base 或主要业务输入不同；
- workflow/Skill/config/model 变化未被声明为候选变量；
- 任一臂读取另一臂实现、findings、结果或 rollout；
- PR 使用关闭引用并改变 Task 状态；
- 缺失、截断、损坏或无法唯一映射关键 rollout；
- `merge-pre` 混入 Merge、Closeout 或 Merge 后活动；
- head/diff 漂移后未重新独立 Review；
- Task #65 业务要求被误删或验收覆盖下降；
- 无法证明实际执行来自冻结 base 和冻结正文。

## 10. 中止、回滚与异常

- **正文漂移**：停止全部未开始臂；创建新 freeze 版本，不覆盖旧 manifest；
- **base/workflow/Skill 漂移**：停止，重新选择共同 base 或将结果降级为不可比；
- **误关闭 Task**：立即停止写操作，记录事件，由维护者恢复状态；该臂不可作为严格样本；
- **Review 失效**：保留旧记录，开启新独立会话审查新 head；
- **日志缺失**：先尝试从原始会话定位；仍缺失则标记 `incomplete`，不得补猜 Token；
- **环境中断**：保留已产生材料和 checkpoint；只有能证明输入未漂移时才可恢复；
- **质量失败**：不为降低 Token 弱化测试或门禁；记录失败并由维护者决定是否允许重做；
- **无获胜臂**：关闭全部实验 PR，保留证据，Task #65 不被实验自动关闭；
- **回滚**：删除非获胜 worktree/clone 和远端实验分支前先归档必要证据；不得删除原始 rollout。

## 11. 非获胜实验臂处理

1. 固定最终 head、verdict、指标和维护者决定；
2. 关闭 PR，原因使用 `not-selected`、`invalid`、`incomparable` 或 `superseded`；
3. 不将关闭引用添加到 PR；
4. 在维护者确认归档完整后删除远端实验分支和本地 worktree/clone；
5. 保留仓库外 rollout、报告、manifest 副本和公开材料摘录；
6. 不把非获胜实现复制到获胜臂，除非启动一个新的、明确标记为非对照的实施流程。

## 12. 材料归档

原始材料位于仓库外：

```text
<external-archive>/task-65-round-2/<arm-id>/<run-id>/
```

每臂至少保存：

- `record.json`：使用 `materials/experiment-record.example.json` 的字段；
- `environment/`：脱敏环境和 config 摘要、版本、hash；
- `rollouts/`：原始 root/Guardian rollout 和 SHA-256；
- `reports/`：Token、质量、命令和比较报告；
- `evidence/`：Task/PR/SHA/check/thread/Project 快照；
- `validation/`：命令、退出码、digest 和必要失败日志；
- `publication/publication-materials.json`：使用
  `materials/publication-materials.example.json` 的结构，建立两篇公共文档的素材索引。

公共文档目标固定为：

1. `代理开发工作流设计指导手册`：需要前置条件、冻结输入、阶段边界、可执行步骤、
   命令和预期输出、检查清单、常见失败、诊断、恢复、回滚、安全边界与可复用案例；
2. `代理工作流 Token 优化技术分享文章`：需要问题与基线、假设、实验变量与控制变量、
   隔离和统计口径、前后流程图、量化结果、成功/失败/反直觉案例、采用或否决决策、
   可推广结论、项目限制与研究边界。

每个实验必须同步沉淀六类材料，不得在最后写作阶段凭记忆补齐：

- **事实材料**：snapshot、validation digest、rollout 定位、Task/PR/SHA/config manifest；
- **对比材料**：两臂指标、Skill/Runner/报告体积、命令调用、质量与时长对照；
- **异常材料**：sandbox、权限、unknown gate、head drift、review invalidation、fallback、
  中断与恢复时间线；
- **决策材料**：候选方案、采用/调整/否决结论、理由、证据、可逆性和回滚触发器；
- **传播材料**：前后工作流图、状态矩阵、可编辑图表源、截图、典型成功与失败案例；
- **研究材料**：cohort、可比性、证据强度、反直觉发现、被否决假设、未知项、
  通用原则与项目特定限制。

所有准备进入公共文档的 claim、案例、图表或截图必须记录：

- 稳定 source reference、SHA 或事件/行号/JSON pointer；
- `fact / derived / inference / recommendation / hypothesis` 类型；
- 证据强度；
- 脱敏状态、公开状态和必要授权/署名；
- 目标文档、目标章节和预期用途。

`publication-materials.json` 只有在以下条件全部满足时才可标记 complete：

- 两篇文档均有逐章节素材映射；
- 至少覆盖事实、对比、异常、决策、传播和研究六类材料；
- 每个公开 claim 都有证据引用；
- 每个视觉资产都有可编辑源、标题、数据来源、口径和脱敏状态；
- 至少记录一个成功、一个失败或恢复、一个与预期不一致的案例；若某类事件没有发生，
  必须显式记录 `not-observed`，不得省略；
- 被否决方案和剩余未知项均有记录。

Task #65 本轮只覆盖 `merge-pre`。指导手册中的 Merge/Closeout 示例必须引用经过核验的
历史样本（例如 Task #63/#64）或后续独立材料，不得把本轮缺失的 Closeout 伪造为零成本
或完整案例。

任何原始凭据、token、keyring、完整用户级配置或敏感日志必须脱敏或排除。

## 13. 独立核验

在仓库根目录执行：

```bash
python - <<'PY'
import hashlib, json
from pathlib import Path

root = Path('docs/workflows/benchmarks/task-65-round-2')
manifest = json.loads((root / 'benchmark-manifest.json').read_text(encoding='utf-8'))
for item in manifest['artifacts'] + manifest['repository_files']:
    path = Path(item['path'])
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    assert actual == item['sha256'], (path, actual, item['sha256'])
print('manifest paths and SHA-256: pass')
PY
```

还必须确认：

- `task-65-telemetry-only.diff` 不改变三类领域模型、Fixtures、序列化、异常输入或验证要求；
- `merge-pre` 只包含 Delivery 和独立 Review；
- branch/PR 模板不含关闭引用；
- `benchmark-manifest.json` 不包含凭据或原始 rollout；
- `experiment-record.example.json` 明确记录实验变量、控制变量和公共材料索引；
- `publication-materials.example.json` 同时覆盖两篇公共文档、六类材料、证据强度、
  claim/source 映射、决策、案例和前后流程视觉资产；
- 材料示例能由未参与实现的读者理解和复核。
