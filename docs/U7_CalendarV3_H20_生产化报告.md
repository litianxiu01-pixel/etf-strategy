# U7_CalendarV3_H20 生产化报告

**日期**: 2026-06-17  
**版本**: V3_H20 (final)  
**签名**: `1fc9f9f956d9977f`

---

## 1. 策略参数（已冻结）

| 参数 | 值 | 说明 |
|------|-----|------|
| `hold_days` | 20 | 持仓 20 个交易日 |
| `min_sectors` | 4 | 至少 4 个板块分散 |
| `cold_threshold` | -3.0 | 板块冷却阈值 |
| `max_positions` | bull=3, neutral=5, bear=2 | 体制仓位上限 |
| `stop_loss_pct` | **null** | 不止损 |
| `exit_rules` | pure_rebalance | 无额外退出 |
| `disaster_exit_pct` | -12 | 已实现并验证，当前不启用 |
| `cost_bps` | 20 | 交易成本 20 bps |
| `liquidity_filter` | ≥¥200万 日均 | 流动性过滤 |

## 2. 回测验证

### 窗口级（选股能力）

| 指标 | 数值 |
|------|------|
| 累计收益 | +163.5% |
| 最大回撤 | 9.7% |
| 夏普比率 | 1.55 |
| 年化收益 | 63.0% |
| 持仓窗口 | 25 / 37 |
| 胜率 | 72.0% |

### 日级模拟（可执行表现）

| 指标 | 数值 |
|------|------|
| 累计收益 | +55.2% |
| 最大回撤 | 13.9% |
| 夏普比率 | 0.91 |
| 年化收益 | 16.2% |
| 胜率 | 67.9% |
| 交易次数 | 53 |
| 总费用 | ¥4,406 |

### 灾难退出对照（-12%）

| 口径 | 累计收益 | 最大回撤 | 夏普 | 胜率 | 事件 |
|------|:---:|:---:|:---:|:---:|:---:|
| 未启用（生产主口径） | +55.2% | 13.9% | 0.91 | 67.9% | 0 |
| 启用 -12% | +49.4% | 13.2% | 0.83 | 66.0% | 4 |
| 差异 | -5.8pp | -0.7pp | -0.08 | -1.9pp | +4 |

结论：`-12%` 已进入 `PortfolioSim` 并可复算，但收益和夏普损耗大于回撤改善，当前不升为生产生效规则。

### 流动性过滤

| 项目 | 数量 |
|------|------|
| 通过 | 31 只 |
| 剔除 | 25 只 |

### 验证签名

- **窗口级验证签名**: `6f9c6d17121c6b88`
- **生产签名**: `1fc9f9f956d9977f`

## 3. 文件清单

| 文件 | 用途 | 状态 |
|------|------|------|
| `scripts/u7_strategy.py` | 策略定义 + 选股引擎 (SSoT) | ✅ 生产 |
| `scripts/backtest_verify.py` | 审计验证器 v2 (H20) | ✅ 生产 |
| `scripts/verify_production.py` | 生产验证器 (日级+流动性+签名) | ✅ 生产 |
| `scripts/u7_weekly_signal.py` | 周度信号 (import u7_strategy) | ✅ 生产 |
| `scripts/u7_v3_exit_rules.py` | PortfolioSim (日级模拟引擎) | 📊 被引用 |
| `data/market_regime/combined_daily.json` | ETF 日线数据 | ✅ 生产 |
| `data/market_regime/etf_universe.json` | ETF 候选池 | ✅ 生产 |
| `data/market_regime/u7_v3_h20_state.json` | 状态文件 (始终创建) | ✅ 运行时 |
| `data/market_regime/weekly_signal.txt` | 当前信号 (H20 格式) | ✅ 运行时 |
| `data/market_regime/production_signature.json` | 生产签名 (可复算) | ✅ 生产 |
| `data/market_regime/backtest_verified.json` | 审计验证报告 | ✅ 生产 |
| `data/market_regime/backtest_report.md` | 审计报告 | ✅ 生产 |

## 4. Cron 调度

| 任务 | 时间 | 推送 |
|------|------|------|
| U7_CalendarV3_H20 周度监控 | 周五 08:30 CST | 微信 71311164 |
| 信号脚本 | `python3 scripts/u7_weekly_signal.py` | → weekly_signal.txt |

## 5. 信号模式

### 换仓周（≥20 个交易日距上次换仓）
- ✅ 推送完整换仓指令
- 含：买入区间、仓位金额、份数、MA20 关注线
- 更新 `u7_v3_h20_state.json`

### 监控周（不满足换仓条件）
- 👀 推送持仓监控 + 参考选股
- 显示当前持仓盈亏、距离下次换仓天数
- 更新 `last_check_date`，保留原持仓入场价

## 6. 核心设计原则

1. **不止损** — 回测证明每加一层退出规则都降 Sharpe
2. **纯 rebalance** — 定期重排是最好的退出机制
3. **交易日计数** — 非日历周，20 个交易日才触发换仓
4. **状态持久** — 每次换仓记录签名，可审计
5. **双模式推送** — 换仓/监控两套文案，信号清晰
6. **灾难退出不升产** — `-12%` 已实现但对照结果未通过

## 7. 审计链

```
策略定义 (u7_strategy.py: U7_CALENDAR_V3_H20_CONFIG)
  → 窗口级回测 (backtest_verify.py → 6f9c6d17121c6b88)
    → 日级模拟 (PortfolioSim: 55.2%/13.9%/0.91)
      → 灾难退出对照 (-12%: 49.4%/13.2%/0.83, not_promoted)
      → 流动性过滤 (31/56 pass)
        → 生产签名 (verify_production.py → 1fc9f9f956d9977f)
          → 周度信号 (u7_weekly_signal.py, import u7_strategy)
```

审计复现命令:
```bash
python3 scripts/backtest_verify.py        # 窗口级 H20 验证
python3 scripts/verify_production.py       # 日级 + 流动性 + 签名
python3 scripts/u7_weekly_signal.py        # 当前信号 (BEAR, 无候选)
```
