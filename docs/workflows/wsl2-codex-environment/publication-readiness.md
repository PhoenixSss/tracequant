# WSL2 Codex 环境 Task：最终文档素材就绪说明

## 目的

本文件说明本 Task 可以为以下两份最终文档提供什么材料，以及哪些结论必须留待后续
Task #65 WSL2 candidate 实验：

1. 《代理开发工作流设计指导手册》；
2. 《代理工作流 Token 优化技术分享文章》。

本 Task 只建立环境、诊断和安全边界，**不产生 Token 降幅、质量提升或因果优化结论**。

## 指导手册章节映射

| 目标章节 | 本 Task 可提供的材料 | 主要来源 |
| --- | --- | --- |
| 适用范围与前置条件 | Ubuntu 24.04、Linux 文件系统、Python/uv/Git/gh、代理和凭据要求 | `README.md`、`current-diagnostic.json` |
| 冻结输入与权威来源 | 版本固定、CI 命令、诊断 evidence 与仓库来源索引 | `evidence-index.json`、`.python-version`、CI |
| 标准执行步骤与阶段边界 | 安装、登录、诊断 profile、可逆探针和停止边界 | `README.md`、诊断工具 |
| 命令、预期输出与检查清单 | 环境命令、CI-equivalent、remote-read、full profile | `README.md` |
| 常见失败、诊断、恢复与回滚 | gh 未登录、uv 漂移、代理、Git metadata、裸 `python` | `decisions-and-cases.md`、`README.md` |
| 独立 Review、人工 Merge 与安全边界 | approval 分类、禁止自动 Merge、写操作边界 | `capability-matrix.md`、Runner 契约 |
| 可复用模板、案例与项目限制 | 三轮诊断案例、决策记录、剩余未知项 | `publication-materials.json` |

## Token 技术文章章节映射

| 目标章节 | 本 Task 可提供的材料 | 限制 |
| --- | --- | --- |
| 问题背景与历史基线 | WindowsApps、WinGet 私有路径、宿主 keyring 和 Git metadata 限制 | 只描述环境问题，不宣称 Token 因果 |
| 假设、变量与控制变量 | WSL2、Linux filesystem、WSL 原生工具、独立 gh 登录、代理路径 | Candidate control plane 需后续冻结 |
| 实验隔离和统计口径 | 环境前置契约、approval 分类和证据结构 | Token 指标由 Task #65 candidate 产生 |
| 前后流程与架构对照 | Windows → WSL2 图、approval boundary 图、能力 CSV | before/after 是能力对照，不是性能结果 |
| Token、Guardian、命令、质量与时长 | 只提供环境侧分类字段和后续采集接口 | 本 Task 没有可报告的 Token 降幅 |
| 成功、失败和反直觉案例 | 认证恢复、uv 固定、approval 保留、裸 `python` 案例 | 见 `decisions-and-cases.md` |
| 采用、调整和否决方案 | WSL2、ext4、Python 入口、代理、最小权限策略 | Rules 结论由后续 Task 验证 |
| 可推广结论与研究边界 | OS 边界与 sandbox 边界分离、显式 unknown | 只在一台机器和一次环境迁移上验证 |

## 图表统计口径

能力统计以 `capability-matrix.md` 的**能力行**为分母，不以命令调用次数为分母：

| 分类 | 能力行数 |
| --- | ---: |
| `direct` | 4 |
| `approval-required` | 7 |
| `partial` | 1 |
| `prohibited` | 1 |
| 合计 | 13 |

同一能力在多次诊断中出现时只计一行。该统计不得用于推断 Token、命令数或 Guardian turn
数量。命令次数和 Token 必须从后续实验 rollout 独立统计。

## 迁移与维护成本

### 已知事实

- 完成了三轮结构化诊断：初始诊断、认证/正式仓库写入补测、GitHub 可逆写与代理补测；
- 需要人工完成 WSL2/VS Code 环境搭建、仓库克隆、gh 登录和 uv 版本调整；
- 日常维护至少包括 Python/uv 版本漂移检查、gh 登录有效性、代理可用性和 WSL 更新检查；
- 正式 Git/GitHub、用户缓存和网络操作仍保留 approval 成本。

### 未测量项

以下项目没有共同起止边界或可靠采样，不应补猜：

- 总迁移工时；
- 每轮诊断的人工等待时间；
- WSL2、`.venv` 和 uv cache 的磁盘增量；
- 长期 CPU、内存和 I/O 成本；
- approval 对 Token 和时长的净影响；
- 跨机器复现成功率。

这些字段在文章中应标记为 `not-measured`，或由后续实验补采。

## 可公开结论边界

可以公开：

- WSL2 解决了本项目中部分 Windows 工具可见性和凭据边界问题；
- WSL2 没有消除 Codex sandbox/approval 边界；
- 代理开启且获批的路径是当前可靠远程路径；
- 裸 `python` 不存在不等于 Python 开发环境缺失；
- 环境迁移结果为 `ready-with-gaps`，不是“无需审批”。

暂时不能公开为已验证结论：

- WSL2 一定减少 Token；
- WSL2 一定减少所有 Guardian turn；
- 最小权限 Rules 已经安全且充分；
- Runner 已经降低上下文或命令成本；
- Candidate 优于 Windows baseline；
- 该环境在其他机器、发行版或网络条件下同样成立。

## Task 来源登记

跨 Task 材料来源以 `../publication-materials/task-material-register.md` 为权威清单。本 Task 合并前必须回填自己的 Issue、PR、Delivery HEAD、reviewed HEAD 和 verdict；后续 Task 产生新材料时应在同一清单新增或更新对应小节。

## 合并前最终化清单

- [ ] 更新 `../publication-materials/task-material-register.md`，回填本 Task 的 GitHub 和 Review 身份；
- [ ] 为三轮仓库外 evidence 生成 SHA-256 manifest，但不提交原始 evidence；
- [ ] 在 `evidence-index.json` 填入 Task Issue、分支、最终 HEAD 和 PR；
- [ ] 独立 Review 后填入 reviewed HEAD 和 verdict；
- [ ] 如引用源文件发生修改，重新计算其 SHA-256；
- [ ] 确认 `publication-materials.json` 中六类材料均有章节用途；
- [ ] 确认所有视觉资产有标题、来源、口径和脱敏状态；
- [ ] 确认 `.agents/evidence.local/` 没有进入 Git；
- [ ] 明确 Task #65 candidate 未在本 Task 中执行。
