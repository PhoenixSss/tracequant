# Task Workflow 第一轮优化前基准：Task #63 与 #64

## 目的

本文记录 `[Feature] 建立 Task Workflow Token 基准并完成第一轮优化 #62` 的两个
优化前功能代码样本，以及本轮允许实施的结构性优化边界。

## 样本

| 项目 | 基准 A | 基准 B |
| --- | --- | --- |
| Task | #63 基础配置管理与环境变量加载 | #64 结构化日志与敏感信息保护 |
| PR | #67 | #71 |
| Run ID | `tw-63-20260726T060745-18ab2950` | `tw-64-20260726T093923-7073320a` |
| Workflow main SHA | `158c92140b75f00d35c16f905a7b3eccb05d4403` | `331df18e23f4a2c8677021e04205f61337427746` |
| 分类 | `feature-code / M / high-correctness / task-only` | 同左 |
| 变更规模 | 4 files / 427 lines / 22 criteria | 5 files / 451 lines / 24 criteria |
| phase | delivery / review / closeout | delivery / review / closeout |
| validate | valid / complete / no missing phase | valid / complete / no missing phase |

两个样本均为干净成功路径：没有 Blocking、High、Medium finding，没有修复 commit、
head 变化、review invalidation 或额外维护者设计决策；全部适用验证通过，独立审查允许
人工合并，closeout 完成。

## Numeric usage 限制

两个 Run 的所有阶段均为：

```text
usage.source = unavailable
input/output/reasoning/total tokens = null
```

因此本轮不得报告：

- 真实总 Token；
- phase Token 占比；
-统计显著性；
-实际 Token 降幅；
-#63 与 #64 的绝对 Token 差异。

`telemetry_complete=true` 只证明必需 phase/event 结构完整，不证明 numeric usage
完整。优化后若 #65 仍没有 numeric usage，只能使用“代理指标改善，Token 效果未验证”。

## 共同结构性证据

两个样本共同显示：

- 三个阶段共执行 23 / 24 个验证命令；
- 已记录 Git、GitHub 和 Telemetry 命令约占 command category 的 81% / 82%；
- 在无 finding、无重审的路径上，review + closeout 仍记录 69 / 87 个 command
  category；
- 同一 Task、PR、SHA、checks、threads、Project 和 branch 事实跨阶段重复采集；
- 成功 validation 输出、阶段报告和 handoff 存在重复回显；
- context bytes、previous handoff、report size 和 numeric usage 未被可靠记录；
- 两个 workflow main SHA 不同，绝对差异不能解释为优化或回归。

## 批准的第一轮优化

本轮只处理两个样本共同支持的 workflow 结构成本：

1. 统一、默认只读的 Workflow Evidence CLI；
2. delivery/review/closeout/Feature audit 的阶段 snapshot 与 recheck；
3. 统一 Validation runner，成功输出紧凑、失败诊断有界；
4. trusted base / locked main runner bootstrap；
5. 四个 Skills 使用脚本替代机械命令编排；
6. 成功路径 compact report 和 handoff；
7. Telemetry 增加 numeric usage coverage、report/handoff、Evidence/Validation、fallback
   和 drift 聚合指标。

## 不变门禁

本轮不得削弱或自动化：

- 新会话独立 PR Review；
- trusted-base control plane；
- base/head/diff 锁定和变化后的重新审查；
- 当前全部适用验证；
-人工 Squash Merge；
- Issue 自动关闭门禁；
- post-merge main 同步和精确 branch cleanup；
- Feature Completion Audit 和 Feature 人工收尾；
- credentials、UTC、data、financial 和 live-mode 安全规则。

Evidence 只提供机械事实；实现、语义审查、验收映射、finding、severity 和 verdict 仍由
Skill/Agent 负责。

## #65 优化后验证

Task #65 继续使用：

```text
feature-code / M / high-correctness / task-only
```

至少比较：

- Skill 与 policy 字符/行数；
- Evidence snapshot/recheck 调用；
- Git/GitHub/validation 聚合操作；
- report 与 previous handoff 字符、行数、估算 Token；
- fallback、retry、snapshot drift、review invalidation；
- findings、验证、维护者决策与最终质量；
- numeric usage coverage（若客户端可提供）。

#65 与 #63/#64 的业务实现复杂度并不完全相同，因此代理指标只能做方向性、结构性比较。
若质量门禁保持且机械操作、原始输出和报告体积下降，可以报告代理指标改善；没有
numeric usage 时不得报告实际 Token 节省百分比。
