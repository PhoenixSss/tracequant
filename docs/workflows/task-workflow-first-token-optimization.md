# Task Workflow 第一轮 Token 优化实现记录

## 结论边界

本轮依据 Task #63、#64 的结构性代理证据实施。两个 Run 的 numeric model usage 均为
`unavailable`，因此本文不报告真实 Token 降幅或统计显著性。

本轮只优化四个 workflow Skills 及其直接依赖的只读 Evidence、Validation 和
Telemetry 聚合能力；不修改 Issue 模板或既有 Issue 正文。

## 实现

新增统一工具：

```text
tools/agent_workflow/workflow_common.py
tools/agent_workflow/workflow_evidence.py
tools/agent_workflow/workflow_validation.py
tools/agent_workflow/trusted_runner.py
```

Evidence 提供 delivery、review、closeout 和 Feature audit 的 snapshot/recheck；
Validation 统一编排当前适用检查并压缩成功输出；Trusted Runner 从 locked commit
提取同版本工具，避免 PR head 控制自己的审查。

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
- trusted-base / locked-main control plane；
- base/head/diff/main/direct-child stability；
- 每阶段当前全部适用验证；
- findings 和固定 verdict；
- 人工 Squash Merge；
- Issue 自动关闭和 post-merge main 核验；
- 精确分支清理规则；
- Feature Completion Audit 和维护者人工收尾。

## Task #65 验证规则

Task #65 继续使用与 #63/#64 相同的：

```text
feature-code / M / high-correctness / task-only
```

至少比较：

- Skill / governance bytes；
- Evidence、Validation、Git、GitHub 操作计数；
- report 和 previous handoff 字符、行数与估算 Token；
- fallback、retry、drift、review invalidation；
- validation、findings、维护者决策和最终质量。

若 numeric usage 仍不可获得，只能写“代理指标改善，Token 效果未验证”。不同业务规模、
workflow main SHA 或返工路径的绝对差异不能被解释为纯优化效果。
