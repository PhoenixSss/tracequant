# TraceQuant v1 仓外保留资产记录

Issue #320 已按 Issue #319 的批准清单完成仓外迁移。机器可读的 Git 安全记录见
[`tracequant-v1-retained-assets-manifest.json`](./tracequant-v1-retained-assets-manifest.json)；
逐文件路径、mode、大小和 SHA-256 只存在于权限受控的仓外完整清单中，不进入 Git 或
GitHub artifact。

## 1. 迁移身份与责任

| 项目 | 已验证值 |
|---|---|
| Recovery owner | `maple / TraceQuant maintainer` |
| 仓外根 | `/home/maple/tracequant-v1-retained-assets/issue-320` |
| 仓外根权限 | `0700` |
| 保护归档 | `protected/tracequant-v1-retained-assets.tar`（`0600`） |
| 完整仓外清单 | `protected/retained-assets-manifest.full.json`（`0600`） |
| 迁移前 HEAD | `db785105f9cbfc20061627a798c7e74b8bf3f53f` |
| 迁移前 tree | `f38ce544e087d5dc71d5a83c20015d8381b20cc4` |
| 完成时间 | `2026-09-12T08:24:51.945447Z` |

迁移没有修改迁移前 tree 中的任何 tracked 文件。本记录及其 JSON 是 Issue #320
有意新增的 documentation candidate，后续由 LCK 绑定到最终提交。

## 2. 保留资产处置

| 类别 | 源根 | 普通文件 | 逻辑字节 | 结果 |
|---|---|---:|---:|---|
| historical Evidence | `.agents/evidence.local` | 2,687 | 103,915,023 | 目标复验后移除源 |
| Validation evidence | `.agents/validation.local` | 2,938 | 7,523,098 | 目标复验后移除源 |
| LCK evidence/state | `.workflow.local/lck` | 2,527 | 63,921,614 | writer 静止、目标复验后移除源 |
| Task #86 freeze package | `artifacts/task-86-global-freeze-20260813` | 14 | 153,465 | 目标复验后移除源 |
| **合计** | 4 个根 | **8,166** | **175,513,200** | **全部完成** |

迁移前后分别计算了每个普通文件的 SHA-256、mode 与逻辑大小；复制后的 payload 与源
清单完全一致，源在复制期间保持稳定。随后从目标 payload 生成 tar，并逐 member 重新
计算 SHA-256 与完整清单比对。所有检查通过后才逐个移除四个明确源根；没有执行通配或
未验证批量删除。

完整性身份：

- tar 大小：`184320000` bytes
- tar SHA-256：`03bfb00e9fc0165e68def8c3c5daa4839cac5e64088d7869e1c9c90c1efec0b0`
- 仓外完整清单 SHA-256：`c7c3a33fc67161e17db259cc467cdc3f247e3fd2cae95174747719e0e82cc92a`
- 仓外源移除状态 SHA-256：`2c56225c213f4309c01af5bb1edb249d352a1020de400b26fb3abcc5144bcb14`

Issue #319 的时间点清单与迁移时实况存在受控漂移：保留集合增加 20 个 LCK 文件与
421,872 bytes。Issue #320 没有沿用旧计数作为删除授权，而是重新枚举、重新计算并在
完整清单中固定实际迁移集合。

## 3. Secret 与隐私处置

四个保留根使用 `detect-secrets 1.5.0` 做不回显值的内容筛查。历史 evidence 中包含大量
SHA、对象 ID、高熵测试材料以及可能带私密上下文的响应；候选没有逐项解除保护，也没有
把候选值写入日志或本记录。因此本次采用保守处置：全部 8,166 个文件都进入 `0700`
父目录下的保护区，普通档案为空。tar、完整清单和源移除状态均为 `0600`；Git 中只保留
聚合计数与摘要。恢复或发布前仍须由 recovery owner 在受控环境中复核，不能把本记录
解释为公开发布许可。

## 4. 可重建与一次性资产

迁移完成后的 Git 原生扫描剩余 8,236 个 ignored 条目，全部命中 Issue #319 已批准的
可重建分类：

| 类别 | 条目 | 处置 / 恢复来源 |
|---|---:|---|
| `.workflow.local/uv-cache/**` | 5,364 | 不归档；由锁文件与配置的软件源重建 |
| `.venv/**` | 2,699 | 不归档；由 `.python-version`、`pyproject.toml`、`uv.lock` 重建 |
| mypy / pytest / Ruff caches | 35 | 不归档；由对应验证命令重建 |
| Python bytecode | 135 | 不归档；由 tracked Python source 重建 |
| `dist/**` | 3 | 不归档；由 tracked source 与构建配置重建 |

迁移结束时没有 ordinary untracked 资产、没有未分类 ignored 条目、没有遗留的四个 retained
source root。Issue #320 不删除上述 8,236 个 disposable 条目；其删除仍需后续明确授权。
本次临时扫描/迁移脚本及其 bytecode 在最终扫描前移除，不构成保留资产。

后续 LCK 或 Validation invocation 可能重新创建各自 ignored 运行目录；这些是迁移边界之后
的新 workflow-owned artifact，不能静默加入本次不可变内容清单或改变上述摘要。

## 5. 恢复与下游消费

恢复时由 recovery owner 先核对仓外根权限、完整清单摘要与 tar 摘要，再在新的受控目录
检查 tar member，最后解包并按仓外完整清单逐文件复验。任何摘要不一致、目标缺失或权限
扩大都必须停止恢复；不得转而使用仓库内 cache 或 GitHub artifact 作为替代副本。

本记录供 Feature #315 的后续 [Issue #318](https://github.com/PhoenixSss/tracequant/issues/318)
（不可变 v1 archive）和 [Issue #314](https://github.com/PhoenixSss/tracequant/issues/314)
（v1 retirement gate）消费。它不创建 tag/Release、不清理可重建 cache，也不证明最终
`V1_RETIREMENT_COMPLETE`。
