# Agent Workflow Skills

本文件是 Agent-neutral workflow Skills 的**注册表 / 导航 / 兼容性入口**：
列出当前 Skills、共享语义 owner 与验证入口。共享生命周期语义由
`docs/workflows/lck/lifecycle.md`（lifecycle 规范）与
`docs/workflows/lck/review-and-remediation.md`（Independent Review 规范）权威定义；
本文件不再复制生命周期语义或执行 procedure。

## Skill 集合

当前 Runner 工作流：

```text
.agents/skills/task-delivery-runner/SKILL.md
.agents/skills/task-pr-review-runner/SKILL.md
.agents/skills/task-closeout/SKILL.md
.agents/skills/feature-completion-audit/SKILL.md
```

Claude Code 对应 Skill：

```text
.claude/skills/task-delivery-runner/SKILL.md
.claude/skills/task-pr-review-runner/SKILL.md
.claude/skills/task-closeout/SKILL.md
.claude/skills/feature-completion-audit/SKILL.md
```

## 共享语义与导航

- Agent-neutral lifecycle 规范：`docs/workflows/lck/lifecycle.md`
  （readiness、Delivery、PR/CI、Independent Review 位置、Human Gate、
  remediation、manual Squash Merge、Closeout、Feature Completion、
  natural-language entry、source-of-truth 模型）。
- Independent Review 规范：`docs/workflows/lck/review-and-remediation.md`
  （fresh session、head lock、verdict semantics、remediation handoff）。
- Issue specification authoring：`docs/workflows/lck/issue-contracts.md`。
- Typed leaf routing / profile ownership：
  `docs/workflows/lck/typed-profiles.md`。
- 自然语言入口（无需维护者提供内部 Skill 名称）：

```text
实现 Issue #N
审查 PR #N
PR #N 已人工合并，请完成 closeout
```

- Delivery 根入口按 Initial Delivery / explicit Remediation 选择一个 supporting
  instruction；Feature audit 按当前 Phase 1–6 逐步读取。Review 和 Closeout 保持线性。
  每个分支只加载当前需要的规范；生命周期语义按需读取最小必要 section。

## Runner 与证据

规范见：

```text
.agents/policies/workflow-evidence.md
.agents/policies/command-execution.md
```

本地 artifacts 按 ownership 分区：

```text
.agents/evidence.local/      # historical/Feature-audit Evidence output
.agents/validation.local/    # ordinary Validation Runner workspace output
.workflow.local/lck/         # LCK runtime state / preserved Review evidence
```

不得提交。Independent Review 在 source repository 仅写 `.workflow.local/lck/`；
`.agents/validation.local/` 如被 formal Review validation 使用，只存在于 disposable
standalone clone 内。

## Skill identity 验证

当前 Codex / Claude Skill 路径、共享语义引用、单一机械入口与每个文件的
SHA-256 由以下只读审计统一验证：

```bash
uv run --frozen python -m tools.lck.skill_audit
```

每个 canonical Skill 的 `package.json` 声明 root、supporting instructions、直接引用的
shared owner 文件及逐文件 SHA-256，并声明 context-loading routes。路径必须是规范的
仓库内 POSIX 相对路径，禁止 traversal、symlink、未声明 reference、缺失文件和 digest
不符。inventory 限制为 32 文件、单文件 256 KiB、总计 1 MiB。shared owner 文件完整
哈希，但其中指向其它业务或生命周期文档的导航不是递归加载命令。

`tools/lck/skill_package.py` 是 audit 和两个 Validation Runner 共用的身份解析器。
`instruction_package.inventory` / validation `execution_identity.skill.inventory`
保存排序后的文件哈希；`canonical_package_sha256` 绑定 canonical inventory 与 manifest，
`package_sha256` 额外绑定实际 Claude adapter 和 permissions（适用时）。SHA-256 使用
UTF-8 JSON（sort_keys、紧凑 separators）生成，因此与绝对工作区路径无关。单文件
`path` / `sha256` 保留兼容用途，不能替代包级身份。验证开始和结束都检查完整性；LCK
Receipt 保留 formal validation payload，所以自然包含同一身份，无新增 authorization gate。

编辑规范文件时，同步更新所有引用它的 manifest 中对应 SHA-256；增加 supporting file
还须加入 route 并从根或当前分支显式链接。审计只读，不会自动重写 manifest 使失败变通过。
identity 是可重现的字节证据，不是信任签名、lifecycle authority 或实际上下文读取日志。
包级哈希覆盖所有可选分支；`selected_instructions` 只选择当前分支，不要求模型读取其它分支。

设计依据：[OpenAI skills / prompts 指导](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)：
精确使用条件、按需加载多分支说明，并为简单流程保留单一入口。

审计输出只覆盖 `canonical_skills` 与 `adapters`。已退役 Legacy Skill 不再位于
active discovery namespace，也不再作为 current routing、失败回退或 competing
semantic owner；其历史内容仅通过 Git 历史恢复。

## Final source-of-truth matrix

| Artifact family | Classification | Durable responsibility |
| --- | --- | --- |
| `AGENTS.md` | ACTIVE | repository invariants、leaf-first retrieval 与 natural-language workflow entry |
| `docs/workflows/lck/lifecycle.md` | ACTIVE | shared lifecycle、readiness、Delivery、Closeout 与 Feature audit semantics |
| `docs/workflows/lck/review-and-remediation.md` | ACTIVE | Independent Review semantics 与 verdict/remediation contract |
| `CLAUDE.md` | ACTIVE | Claude-specific thin adapter 与 Skill discovery |
| `.agents/skills/*-runner/`、`.agents/skills/task-closeout/`、`.agents/skills/feature-completion-audit/` | ACTIVE | Codex executable procedures |
| `.claude/skills/` current four Skills | ACTIVE | Claude executable procedures |
| `tools/lck/shared_facts.py` | ACTIVE | authoritative profile-neutral Git/GitHub fact acquisition and normalization |
| `tools/lck/wsl2_validation_runner.py`、`validation_runner.py`、validation profiles 与 current tests | ACTIVE | deterministic validation plans、exit codes 与 bounded diagnostics |
| `tools/lck/feature_audit.py` | AUDIT-ONLY | Feature audit evidence and adapter over shared facts；不具备 Task lifecycle authority |
| pre-LCK Task Evidence Runner、Task profiles、Codex Rules、dedicated Runner/Rules tests 与 `self_review.py` binder/test | REMOVED | 不属于当前 workflow entry point；需要时仅从 Git 历史恢复 |
| retired `.agents/skills/task-delivery/`、`.agents/skills/task-pr-review/` | DEAD / ABSENT | Legacy executable Skills 已退役；历史内容由 Git 历史及 frozen evidence 保留 |
| Claude current Skills 中的 Codex/Claude permission-boundary 说明 | COMPATIBILITY ONLY | cross-agent adapter guidance; retained intentionally while both agents are supported |
| retired Skill-variant provenance JSON/doc/tool/test bundle | DEAD / ABSENT | replaced by `skill_path_audit.py`; all stale current references removed |
| removed trusted-runner、runtime usage-measurement 与 runtime manifest machinery | DEAD / ABSENT | no current responsibility; absence is regression-tested |

当前没有 `UNCERTAIN` artifact。`COMPATIBILITY ONLY` 项仍有明确的双 Agent
文档用途，未满足删除条件，因此有意保留。Legacy executable Skills 已完成 caller /
routing / validator 收敛后删除，不再作为 compatibility runtime artifact 保留。

Current reference graph：

```text
Issue body (business specification)
  -> AGENTS.md (repository invariants and entry resolution)
  -> shared development docs (lifecycle / review semantics)
  -> agent-specific current Skills (executable procedure)
  -> LCK + Validation Runner (Task lifecycle control and deterministic validation)
  -> tools/lck/shared_facts.py (authoritative profile-neutral Git/GitHub facts)
  -> Git / GitHub Issues, relationships, Projects, PRs and CI (durable state)
```

Feature audit consumes `shared_facts.py` through the bounded
`feature_audit.py` adapter; LCK core never imports the audit module.

`.agents/evidence.local/`、`.agents/validation.local/` 与 `.workflow.local/lck/` 都是
Git-ignored local artifacts，但 ownership 不同：前两者属于历史 Evidence / ordinary
Validation Runner，后者属于 LCK runtime 与需要跨 temporary Review clone 生命周期保留的
Review evidence。它们都不是 Git/GitHub authority 或新的 source of truth。

## 仓库外 Token 消耗分析边界

Codex rollout JSONL、Token 报告和会话级比较数据只在仓库外分析，且不得提交本仓库。外部分析不改变 Skill 权限、质量门禁、Review verdict、人工 Merge 或完成证据。
