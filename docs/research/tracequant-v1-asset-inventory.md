# TraceQuant v1 仓库资产清单与处置建议

Research Outcome: IMPLEMENT

本报告回答 Issue #319：在当前 TraceQuant 仓库根目录内，哪些对象进入不可变 v1 Git
快照，哪些进入仓外保留资产档，哪些必须按 Secret 级别保护，以及哪些是可重建缓存。
机器可读清单见
[`tracequant-v1-asset-inventory.json`](./tracequant-v1-asset-inventory.json)。结论只授权
后续 leaf item 按下述门禁实施外部化；本次没有移动、复制、删除、清理、打 tag、创建
Release 或关闭 Issue。

## 1. 观测边界与方法

观测时间为 `2026-09-12T07:43:46Z`。基线 HEAD 是
`a1fb5d4b87c12a0e2bd1fada12e6d4c46076d3b1`，tree 是
`5a9dce8f97592e95d5b2ef33525defa9a80e12fa`。观测开始时工作树干净。

盘点严格限制在仓库根目录，使用 `git ls-files`、`git ls-files --others
--exclude-standard`、`git ls-files --others --ignored --exclude-standard` 建立 tracked、
ordinary untracked 与 ignored 集合；使用 `find`、`stat`、`du` 和 `file` 取得类型与逻辑
大小。没有读取或输出凭据值，也没有扫描 home、其他磁盘或仓库外目录。

大小均为普通文件 `st_size` 的逻辑字节总和，不含目录项开销；symlink 单独计数且没有
跟随。`.git/**` 不属于 Git 的 tracked/untracked/ignored 工作树集合，故作为 VCS 管理类
单列。LCK 和验证工具仍可能写入 ignored evidence 目录，因此此处的数字是时间点快照；
真正迁移前必须在写入者静止后重新枚举并校验，不能把本报告的数字当作删除授权。
本报告及其 JSON 是观测完成后产生的两个预期 candidate 文件，未计入基线的
ordinary-untracked 数量；它们通过 LCK 提交后属于 tracked snapshot。

## 2. Git 原生清单结果

| 集合 | 条目 | 普通文件 | symlink | 逻辑大小 | 结论 |
|---|---:|---:|---:|---:|---|
| tracked | 179 | 179 | 0 | 2,826,029 B | 由最终 v1 tag/Release 保留 |
| ordinary untracked | 0 | 0 | 0 | 0 B | 无待分类对象 |
| ignored | 16,372 | 16,199 | 173 | 922,800,026 B | 分为仓外保留与可重建两类 |

tracked 文件模式为 174 个 `100644` 与 5 个 `100755`。当前没有 tracked symlink。
最终不可变快照应绑定本 Research 产物合入后的精确 tag commit/tree；本表的基线 SHA
只证明盘点起点，不预先猜测未来 merge identity。

## 3. 仓外保留资产

#312 要求保留完整 v1 代码、Issue 与证据历史。因此以下历史输出不能因“某些命令可
重跑”就推定可删除；应完整复制到明确的仓库外 v1 资产目录。

| 路径 | 普通文件 | 逻辑大小 | Owner / 内容类 | 处置 |
|---|---:|---:|---|---|
| `.agents/evidence.local/**` | 2,687 | 103,915,023 B | historical Evidence；acquisition、review、planning、probe | 筛查后仓外保留 |
| `.agents/validation.local/**` | 2,938 | 7,523,101 B | Validation Runner 与历史验证生产者 | 静止后筛查并仓外保留 |
| `.workflow.local/lck/**` | 2,507 | 63,499,739 B | LCK audit receipts、review evidence/findings 与本地状态 | 不得在活跃 LCK 操作中移动；静止后完整保留 |
| `artifacts/task-86-global-freeze-20260813/**` | 14 | 153,465 B | Task #86 run-locked freeze evidence | 完整保留 |
| **合计** | **8,146** | **175,091,328 B** | 0 个 symlink | 普通档案前均按 protected-until-screened 处理 |

这些路径可能包含 HTTP headers/body、GitHub 获取结果、绝对路径、评审输入或其他私密
上下文。分类为“保留证据”不等于“可公开”：迁移实现必须先做不回显值的 Secret/隐私
筛查；任何命中项从普通档案剔除并进入单独受控 Secret 存储。为避免选择性删除破坏
证据链，`.workflow.local/lck/**` 即使含 transient marker，也按完整根保留。

### Task #86 当前逐文件 SHA-256

此小型冻结包已在只读条件下计算现状校验值；迁移时仍须重新计算并比对：

| 相对路径 | SHA-256 |
|---|---|
| `arm-identities.json` | `9c19501120b482a6fcc44f31699f744635b7d281021d8c0b80c0a718f0d4d3cc` |
| `business-snapshot.json` | `998ebe9ec3b438002ca3374f01f13a771639124e19896c9522c36e5b3c8bf208` |
| `entry-verification.json` | `5a878cbfb86b320c85865922ee2c9eff046bf4dc1ae2d73c26bd9452a15f4e7e` |
| `environment-baseline.json` | `91236176e415f0c852bd77901dbe978cf335ff5bd0ec14e98172ef528de314d9` |
| `evaluation-definition.md` | `0c69b0e0ece54dbf527d2d802fdcc5713f901da5504ee4d66ca5b554986d8383` |
| `freeze-package-identity.txt` | `74467aa50bfd7eb683e5dcde921330053db777ddd0a04ed943d8d015bcb9a1eb` |
| `freeze-report.md` | `6e63220dec286be6f24e67862955aa471030fac080db481d4fad86f785505b04` |
| `information-boundary.json` | `47704a14e95c7bd54e5a60c5895dfb4a04fa57b12b6f5f987aa7ae6b2421fb8c` |
| `run-locked/cd-file-identity-report-recomputed.json` | `895603d6bcb9482ddf9c15e4a636400c2ea56b821a27fe0711620ba461bb20c9` |
| `run-locked/cd-file-identity-report.json` | `895603d6bcb9482ddf9c15e4a636400c2ea56b821a27fe0711620ba461bb20c9` |
| `run-locked/generation-c-run-locked-manifest.json` | `753b1050c0e15c5420d6bfc6176ac71b096c12827953caf761c04d1412081db0` |
| `run-locked/generation-d-run-locked-manifest.json` | `6dcde9dd547d54399b8ad1bec6dd48ff4741312b3ae27dcfb764347dce2075ad` |
| `shared-identity-mapping.json` | `a91757d48929332d7542180261f9eb15e651ca782b0e9b320e91a630a5d1dae4` |
| `validation-record.json` | `b8093b82dff905f15988abe387c2b000030f8d9ac811aaf8e275041061497bbb` |

## 4. 可重建环境、构建与缓存

| 路径 | ignored 条目 | 普通文件大小 | Owner | 恢复来源 |
|---|---:|---:|---|---|
| `.workflow.local/uv-cache/**` | 5,354（含 169 symlink） | 405,157,547 B | uv | `pyproject.toml`、`uv.lock`、配置的软件源 |
| `.venv/**` | 2,699（含 4 symlink） | 324,768,467 B | Python/uv | `.python-version`、`pyproject.toml`、`uv.lock` |
| `.mypy_cache/**`, `.pytest_cache/**`, `.ruff_cache/**` | 35 | 12,183,622 B | mypy/pytest/Ruff | 对应验证命令 |
| `src/**/__pycache__/**`, `tests/**/__pycache__/**`, `tools/**/__pycache__/**` | 135 | 5,582,799 B | Python | tracked Python source |
| `dist/**` | 3 | 16,263 B | Python build tooling | tracked source 与锁定构建配置 |
| `tmp/` | 0 | 0 B | local runtime | 按需重建空目录 |
| **合计** | **8,226** | **747,708,698 B** | 含 173 symlink | 不进入 Git 或仓外保留档 |

这些对象可以在“仓外保留资产已完成复制且目标校验通过”后由另一个明确授权的清理
leaf item 删除。本 Research 不提供删除授权。`.venv` 中的绝对解释器 symlink 与
`.workflow.local/uv-cache` 的内部 symlink 还说明它们是机器相关环境，而不是可移植资产。

## 5. Secret 与 Git 管理元数据

路径名检查仅发现 tracked 的 `.env.example`（326 B）；它是配置示例，不是凭据存储。
当前没有 `.env`、`.env.*`（排除 example）、`secrets/**`、`*.pem` 或 `*.key` 路径对象。
该结果只说明没有命中约定命名，不能证明证据文件内容已经通过发布审查。

`.git/**` 当前约 20,144,464 B，归本地 Git clone 所有。它不属于工作树清单，不得进入
GitHub Release 或普通仓外资产档：远端 Git 历史与最终不可变 tag 才是 tracked snapshot
的恢复权威。`.git/config`、reflog 与未发布状态可能是机器相关或敏感信息；Phase 0 期间
也不得删除当前活跃 clone。

Secret 处置规则是 fail closed：对仓外保留候选做只报告路径/规则、不输出值的筛查；
任何实际 Secret 从普通 archive 排除，单独放入访问控制、加密的 Secret 存储，并记录
独立 checksum 与责任人。筛查结果 unknown、工具失败或新路径出现时，只阻止受影响的
复制/清理，不扩大扫描范围或猜测安全性。

## 6. 后续外部化的校验和计划与停止条件

后续 leaf item 只能按以下顺序执行：

1. 取得明确的外部化授权并让 LCK/Validation 写入者静止；再次运行 Git-native 三集合
   清单。任何新增、消失或重分类路径都必须先更新本清单，不能静默吸收。
2. 对四个仓外保留根做不回显内容的 Secret/隐私筛查；拒绝 symlink、socket、device、
   FIFO 等特殊对象。命中 Secret 时分流到受保护存储。
3. 在不修改源文件的前提下复制，保留相对路径与 mode；对每个普通文件计算 SHA-256，
   以 UTF-8 相对路径排序生成 manifest，同时记录文件数与逻辑字节数。
4. 对最终 archive 本身再计算 SHA-256；在仓库外目标重新计算每文件 digest、总数与总
   大小并与源 manifest 比对。
5. 只有目标验证完全通过且最终 v1 tag/Release 已绑定精确 commit/tree 后，后续清理项
   才能讨论删除。`partial`、`unknown`、checksum mismatch、writer 未静止或 Secret
   分流未完成都必须停止受影响的 mutation。

## 7. 结论

当前仓库内所有可见对象类已有 owner 与拟议处置：179 个基线 tracked 文件进入最终 v1
Git tag/Release；8,146 个本地证据文件（175,091,328 B）进入筛查后的仓外保留档；
8,053 个普通缓存/环境/构建文件与 173 个 cache symlink（747,708,698 B）不归档；空
`tmp/` 无迁移内容；`.git/**` 由远端与 tag 替代，且不作为普通资产发布。没有 ordinary
untracked 对象，也没有路径级实际 Secret 候选。

因此 Research Outcome 为 **IMPLEMENT**：#312 的外部资产迁移 leaf item 可以使用本清单
作为唯一分类输入，但必须执行第 6 节的静止、重枚举、Secret 分流、逐文件 checksum、
目标复验与 fail-closed 门禁。本结论不授权本 Issue 内进行任何复制、移动或删除。
