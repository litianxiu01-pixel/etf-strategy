# 生产定义

## 当前生产规则
- **主策略**: core/u7_strategy.py（U7_CalendarV3_H20）
- **出场规则**: pure_rebalance（无止损、无额外卖点、信号自然退出）
- **体制检测器**: 内置 detect_regime_at()（四维+滞后滤波）
- **数据源**: data/combined_daily.json + data/etf_universe.json
- **信号脚本**: core/u7_weekly_signal.py（周五 08:30 cron）
- **生产签名**: ba67563bf99cedd2

## 已否决项目
- ❌ **BearB_full** — research_only（跳过 MA+vol 过滤，+188.5% 但 SR 1.51 ↓0.10，低流动性仓位暴露增加，非干净 alpha。保留为 future work 参考。）
- ❌ **灾难退出** — not_promoted（回测收益代价 5.8pp > 回撤收益 0.7pp）
- ❌ **V2 regime detector** — research_only（待同一框架重新对比，样本量不足不推广）
- ❌ **Direction A / MA20 decline warning** — not_promoted（W33 预警将回撤放大 4.3pp，已撤回）

## 版本历史
| 签名 | 版本 | 收益 | 状态 |
|------|------|------|------|
| ba67563bf99cedd2 | H20 patch1 | +181.1% / DD 9.7% / SR 1.61 | 🟢 当前生产（仓库自洽修复后） |
| e3e937da0aeeb8a1 | H20 final | +181.1% / DD 9.7% / SR 1.61 | 📜 旧签名（代码重构前） |
| 1fc9f9f956d9977f | H20 + 灾难退出 | ~+175% | ❌ 已否决 |
| 11f4c876182caf19 | H20 + MA20预警 | ~+167% | ❌ 已否决 |
| 6f9... | H20（旧签名） | +163.5% | 📜 历史版本 |
