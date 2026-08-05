# Task Workflow 第一轮 Token 优化实现记录

> 历史说明：本文记录当时的实现。当前运行规范以 `agent-skills.md`、
> `workflow-evidence.md` 和各 Runner 文档为准；跨 commit trusted-version
> 控制面已经退役，不得按本文的历史路径执行。

## 结论边界

本轮最初依据 Task #63、#64 的结构性代理证据实施；当时仓库内测量记录未提供
numeric model usage。后续维护者已从 Codex rollout JSONL 生成 Protocol v1 精确基准
报告，因此本文件只保留 #72 的实现历史，不再作为 Token 数值基准。

本轮当时只优化四个 workflow Skills 及其直接依赖的只读 Evidence、Validation、
控制面和紧凑报告；不修改 Issue 模板或既有 Issue 正文。运行期测量模块已在
后续治理变更中移除，Token 分析转移到仓库外。

## 实现

新增统一工具：

```text
tools/agent_workflow/workflow_common.py
tools/agent_workflow/workflow_evidence.py
tools/agent_workflow/workflow_validation.py
```

Evidence 提供 delivery、review、closeout 和 Feature audit 的 snapshot/recheck；
Validation 统一编排适用检查并压缩成功输出。当时还曾使用跨 commit 控制面；该设计
现已退役。

四个 Skills 改为调用工具替代机械命令链，并保留权限、停止条件、语义审查、findings、
verdict、人工 Merge 和 Feature 人工收尾规则。第一轮工具只读，不执行 lifecycle 写入、
Merge、Issue close 或分支删除。

## 静态上下文代理指标

| Skill | 优化前字符 | 优化后 Skill | 加共享 policy 后 | 净变化 |
| --- | ---: | ---: | ---: | ---: |
| `task-delivery` | 24,434 | 9,763 | 15,769 | -35.5% |
| `task-pr-review` | 21,791 | 9,451 | 15,457 | -29.1% |
| `task-closeout` | 18,431 | 8,672 | 14,678 | -20.4% |
| `feature-completion-audit` | 24,320 | 10,690 | 16,696 | -31.3% |

四个 Skill 文件本身由 88,976 字符降至 38,576 字符，约减少 56.6%。考虑新增的
6,006 字符共享 policy 后，单会话静态治理上下文仍约减少 20%–36%。这些是字符代理，
不是 model Token 测量；缓存、系统提示、Issue、代码、diff 和工具输出会影响实际结果。

## 质量门禁

没有优化掉以下门禁：

- 新会话独立 PR review；
- 被处理对象的 base/head/diff/main 身份锁定；
- base/head/diff/main/direct-child stability；
- 每阶段当前全部适用验证；
- findings 和固定 verdict；
- 人工 Squash Merge；
- Issue 自动关闭和 post-merge main 核验；
- 精确分支清理规则；
- Feature Completion Audit 和维护者人工收尾。

## 后续验证规则

后续相近 Task 继续使用与 #63/#64 相同的比较族：

```text
feature-code / M / high-correctness / task-only
```

维护者在仓库外使用 Codex rollout JSONL、Task 元数据和 Protocol v1 报告比较：

- Task 与阶段级 total/input/cached/uncached/output Token；
- Root、Guardian 和其他 Subagent 分布；
- Evidence、Validation、Git、GitHub 操作与重试；
- compact report 和 handoff 体积；
- validation、findings、返工、fallback 和最终质量。

不同业务规模、workflow main SHA、Codex/model 版本或异常路径必须降低可比性，不得
把绝对差异直接解释为纯优化效果。外部分析是否执行不影响仓库 Workflow verdict。
