# Workflow Evidence 与 Validation Runner

## 当前模型

Task Runner 工作流使用两个固定入口：

```text
tools/agent_workflow/wsl2_github_evidence_runner.py
tools/agent_workflow/wsl2_validation_runner.py
```

```text
Skill：权限、阶段、语义判断、finding、verdict
Evidence Runner：确定性 Git/GitHub 事实、snapshot/recheck
Validation Runner：确定性命令计划、退出码、有界诊断
Maintainer：人工 Merge 与 Feature closeout
```

当前显式调用的 Skill 与当前仓库 Runner 是实际执行版本。结果记录它们的路径、
内容哈希、profile/schema、仓库 head 与对象 SHA。不会从 `main`、PR base 或其他
commit 提取控制面。

继续锁定的是被处理对象：Task base、PR base/head/effective diff、audited main 和
merge SHA。

## Profile 映射

| 阶段 | Evidence | Validation |
| --- | --- | --- |
| Runner Delivery preflight | `delivery` | 按需 `targeted*` |
| Runner Delivery final | `delivery-readiness` | `workflow-delivery --base-sha <base>` |
| Independent Runner Review | `review` + `recheck` | `workflow-review --base-sha <base>` |
| Closeout | `closeout-readonly` + `recheck` | `workflow-closeout --base-sha <PR base>` |

Runner 是其覆盖机械事实的唯一来源。成功后不得重跑同义完整 Git/`gh`/`uv` 链。

## Validation Runner

通用 profiles：

```text
current-ci-equivalent
targeted
targeted:tools-tests
targeted:workflow-tests
post-merge
```

工作流 profiles：

```text
workflow-delivery --base-sha <base>
workflow-review --base-sha <base>
workflow-closeout --base-sha <PR base>
```

Delivery/Review 最终验证要求 clean committed head。Closeout 还要求
`branch == main` 且 `local main == origin/main`。成功 stdout 仅返回紧凑 digest；
完整脱敏结果位于 `.agents/validation.local/`。

## Evidence Runner

Profiles：

```text
delivery
delivery-readiness
review
pre-merge
closeout-readonly
recheck
```

Runner 固定仓库、参数 schema、GitHub 查询和只读 Git 操作，不接受任意 repo、
API path、raw `gh`/Git argv、shell、cwd 或输出路径。结果位于
`.agents/evidence.local/`。

退出码：

```text
0 = pass
3 = partial
4 = evidence fail
2 = invocation / integrity / schema error
```

`partial`、`unknown`、截断或 endpoint failure 不能视为成功。只展开结果点名的
gate/fact，不恢复完整查询链。

## Independent Review

Review 独立性来自：

- 未参与实现或修复的新会话；
- 严格只读；
- 锁定 PR base/head/effective diff；
- 完整语义审查；
- 独立 Validation；
- stability recheck；
- 维护者人工 Merge。

PR 修改 Skill、Runner、Rules 或 workflow governance 时，Reviewer 直接审查这些
变更的行为、测试、权限与失败路径。Runner pass 不是自证，也不授权 Merge。

## Review remediation

非通过 Review 输出有界 remediation handoff：Task/PR、reviewed head、导致结论的
Blocking/High/Medium finding、客观 gate 和需要维护者决定的事项。

`task-delivery-runner` 在同一 PR 上完成最小修复、回归测试、final Runner、push、
checks 和 readiness。任何新 commit 都使旧 verdict 失效，必须由新的独立 Review
会话审查。

## 本地产物与 Token

只允许写入并保持 Git ignored：

```text
.agents/evidence.local/
.agents/validation.local/
```

Token 分析在仓库外使用 Codex rollout JSONL 完成，不改变任何工作流权限或结论。

## Provenance 与回滚

当前 Codex / Claude Skill 路径、共享语义引用、文件哈希与历史基准分类由
`tools/agent_workflow/skill_path_audit.py` 统一验证。历史 Skill 只作为维护者明确
点名的 frozen benchmark 对照，不是 Runner 失败回退路径。

回滚当前 Runner 机制时，必须在一个变更中同步回滚：消费 Skill、Runner、profiles、
Rules、tests 和文档。回滚后仍只能存在一条完整机械路径；不得同时运行两套完整
Validation/Evidence 链，也不得跳过独立 Review、人工 Merge 或对象 SHA 锁定。
