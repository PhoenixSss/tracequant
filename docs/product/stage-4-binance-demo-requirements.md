# 阶段 4：单 Strategy Binance Demo 交互验证

| 项目 | 边界 |
| --- | --- |
| Feature | [#384](https://github.com/PhoenixSss/tracequant/issues/384) |
| Runtime | 固定 NautilusTrader `2.0.0rc4` |
| Venue | Binance USD-M Futures Demo；不得连接 Live 或 legacy Testnet |
| Instrument | `BTCUSDT-PERP.BINANCE`，不扩展 symbol |
| 产品状态 | `DEMO_ONLY`、`LIVE_NOT_APPROVED` |

本基线按维护者重新确定的 #384 范围，取代历史《TraceQuant 分阶段推进计划》§5 中将 DataTester、ExecTester 和多场景矩阵列为阶段退出条件的安排；原研究文件保留为历史记录。

## 目标和运行路径

本阶段只验证 NautilusTrader 的官方 Binance data/execution 接口能让一个简单 Strategy 与 Demo 交易所完成基础交互。策略可在首个合格行情后触发一次交易；不评价 alpha、收益率或长期稳定性。

1. 操作者在 Demo UI 核对 one-way、isolated、1x、flat 和零活动订单。Demo 凭据只从本地 Demo 专用环境变量读取，不进入仓库、Issue、日志或验收摘要。请求的配置和人工核对如实记录，不伪称 rc4 提供了逐次 typed venue 模式成功回执。
2. Strategy 通过 Nautilus 公开 instrument 和行情取得有效价格及最小合法订单量。一个运行最多提交一次 market 开仓；完整成交且公开净持仓确定后，按该持仓精确数量最多提交一次 reduce-only market 平仓。每时刻最多一个活动或未决订单。
3. 只使用 Nautilus Strategy/order API 与公开订单、成交、持仓、账户观察核对结果。不使用 raw Binance client 或独立订单账本。订单去向、持仓或配置出现未知/冲突时停止新增提交，不盲目重试，也不宣称已清场；由操作者检查 Demo 账户。
4. 从真实带凭据运行保存脱敏摘要：运行时间、源码与 rc4 identity、Demo 环境、instrument、行情、相关订单 ID/状态/成交、最终公开持仓和活动订单状态。原始运行日志留在仓库外。只有完整的实际运行事实支持成功；离线测试或 CI 不能代替实跑。

## 完成条件

- 一个 Nautilus Strategy 确实连接 Binance Demo 并观察到有效行情。
- 一次极小 market 开仓完整成交；一次 exact reduce-only 平仓完整成交；公开事实支持最终 flat、零活动订单，且订单归属可关联。
- 失败或未知状态不会被写成成功；摘要脱敏并声明 `LIVE_NOT_APPROVED`。

## 范围边界

DataTester、ExecTester、双方向交易、post-only limit/cancel、四场景矩阵、batch 聚合、EvidenceV1/digest、全流行情证明、通用恢复、soak、阶段 3 模型与 Live 授权均不是本阶段完成条件。固定 rc4 无法给出的 typed venue 回执也不是成功条件。

旧代码已由 [#397](https://github.com/PhoenixSss/tracequant/issues/397) 撤回。当前交付依次为 [#399](https://github.com/PhoenixSss/tracequant/issues/399) 实现单 Strategy、[#400](https://github.com/PhoenixSss/tracequant/issues/400) 在合并后的 main 实际运行和核对。历史 #385–#391 不构成新的前置矩阵。
