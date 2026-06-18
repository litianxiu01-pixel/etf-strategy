# U7 回测验证报告 v2.0

> 验证时间: 2026-06-17 22:01:02
> 输入指纹: `3618e4ccdad355e8...`
> 输出签名: `6f9c6d17121c6b88...`

## 输入哈希
| 文件 | SHA256 |
|------|--------|
| combined_daily.json | `e9476a1a37839ef5...` |
| etf_universe.json | `4745f4bed663f1e8...` |
| u7_strategy.py | `ddc0b33ded61aff2...` |

## 策略配置
- **名称**: U7_CalendarV3_H20
- **持仓周期**: 20 日
- **MA条件**: MA20 > MA60
- **量能**: ≥1.2x 20日均量
- **止损**: 否（不止损）
- **板块分散**: ≥4 板块
- **仓位**: 牛市3 / 中性5 / 熊市2

## 双口径指标

| 指标 | 口径A (有持仓) | 口径B (日历) |
|------|:---:|:---:|
| 窗口数 | 48 / 74 | 74 / 74 |
| 累计收益 | +163.5% | +163.5% |
| 年化收益 | +63.0% | +39.1% |
| 最大回撤 | 9.7% | 9.7% |
| 夏普比率 | 1.55 | 1.21 |
| 胜率 | 72.0% | 48.6% |
| 超额 α | +146.0% | +146.0% |

## 市场体制分布
- bear: 16 窗 (22%)
- bull: 16 窗 (22%)
- neutral: 5 窗 (7%)

## 审计方法
```bash
# 1. 确认输入未变
sha256sum data/market_regime/combined_daily.json
sha256sum data/market_regime/etf_universe.json
sha256sum scripts/u7_strategy.py

# 2. 重新运行
python3 scripts/backtest_verify.py

# 3. 对比输出签名
python3 -c "
import json
r = json.load(open('data/market_regime/backtest_verified.json'))
print(r['output_signature'])
"
```

输出签名相同 → 端到端可复现。任何输入变化 → 签名不同。
