# U7_CalendarV3_H20 — ETF 双周动量策略

A股 ETF 量化轮动策略，基于四维体制检测 + 双周动量选股。

## 核心指标

| 指标 | 窗口级 | 日级 (20bps) |
|:---|---:|---:|
| 累计收益 | +181.1% | +55.2% |
| 最大回撤 | 9.7% | 13.9% |
| Sharpe | 1.61 | 0.91 |
| 胜率 | 73.1% | 67.9% |
| 回测区间 | 2023-06 ~ 2026-06 | 同 |

## 策略参数

```
hold_days = 20         # 双周轮动
bull = 3 / neutral = 5 / bear = 2  # 体制仓位上限
min_sectors = 4        # 最少板块数
exit = pure_rebalance  # 纯重排出场（零额外退出）
```

## 评分公式

```
score = ret60 × 1.0 - vol20 × 0.2 + (MA20/MA60 - 1) × 100 × 0.2
```

## 候选过滤

- MA20 > MA60
- 成交量 ≥ 20日均量 × 1.2
- 板块5日收益 ≥ -3%
- 防御资产额外过滤 MA20 > MA60

## 体制仓位

| 体制 | 持仓上限 | 仓位比例 | 候选池 |
|:---|:---:|:---:|------|
| Bull | 3 | 90% | 全市场 |
| Neutral | 5 | 75% | 全市场 |
| Bear | 2 | 30% | 防御池 (511010/511180/518880/513500) |

## 目录结构

```
core/         核心策略 + 体制检测 + 验证
backtest/     参数优化 + 回测实验
tools/        数据工具 + 选股引擎
research/     方向研究 + 诊断分析
data/         56只ETF日线 + 回测结果 + 签名
docs/         生产化报告
```

## 数据源

- ETF 日线：腾讯财经 API (`qt.gtimg.cn`)
- 体制检测：自有价格计算（不依赖外部板块 API）

## 生产部署

- Cron: 每周五 08:30 Asia/Shanghai
- 信号脚本: `core/u7_weekly_signal.py`
- 推送目标: 企业微信 #71311164
- 状态文件: `data/u7_v3_h20_state.json`
- 签名链: `data/production_signature.json`

## 快速开始

```bash
# 运行信号生成
python3 core/u7_weekly_signal.py

# 运行生产验证
python3 core/verify_production.py

# 运行回测验证
python3 core/backtest_verify.py
```
