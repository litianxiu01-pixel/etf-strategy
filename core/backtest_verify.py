#!/usr/bin/env python3
"""
U7 回测验证器 v2.0
- 唯一策略源: u7_strategy.py（禁止复制逻辑）
- 输入冻结: 数据文件 + 策略代码 全部 SHA256
- 输出签名: 完整交易账本 hash（不含时间戳等易变字段）
- 双口径: 日历 / 有持仓
- 可复现: 任何人输入不变→签名不变
"""
import json, os, sys, hashlib
from datetime import datetime

# ═══ 1. 冻结输入 ═══
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
CORE_DIR = os.path.join(REPO_ROOT, "core")

def sha256_file(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()

def sha256_json(obj):
    """哈希 JSON 对象，排空键、固定排序"""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',',':')).encode()
    ).hexdigest()

def sha256_list_of_dicts(lst):
    """哈希字典列表，每个字典排空键"""
    return hashlib.sha256(
        json.dumps(lst, sort_keys=True, ensure_ascii=False, separators=(',',':')).encode()
    ).hexdigest()

# 输入文件
data_path = os.path.join(DATA_DIR, "combined_daily.json")
universe_path = os.path.join(DATA_DIR, "etf_universe.json")
strategy_path = os.path.join(CORE_DIR, "u7_strategy.py")

INPUT = {
    "combined_daily": sha256_file(data_path),
    "etf_universe": sha256_file(universe_path),
    "u7_strategy": sha256_file(strategy_path),
}
INPUT_FINGERPRINT = sha256_json(INPUT)

# ═══ 2. 运行回测 ═══
sys.path.insert(0, CORE_DIR)
from u7_strategy import run_backtest, U7_CALENDAR_V3_H20_CONFIG

result = run_backtest(U7_CALENDAR_V3_H20_CONFIG)

# ═══ 3. 输出签名 ═══
# 签名字段：完整交易账本的 SHA256（不含时间戳等易变字段）
TRADE_HASH = sha256_list_of_dicts(result['trade_ledger'])

# 汇总签名字段：指标 + 交易哈希
SIGNATURE_PAYLOAD = {
    "input_fingerprint": INPUT_FINGERPRINT,
    "config_name": U7_CALENDAR_V3_H20_CONFIG['name'],
    "metrics_active": result['metrics_active'],
    "metrics_calendar": result['metrics_calendar'],
    "bench_total": result['bench_total'],
    "regime_counts": result['regime_counts'],
    "trade_ledger_hash": TRADE_HASH,
}
OUTPUT_SIGNATURE = sha256_json(SIGNATURE_PAYLOAD)

# ═══ 4. 组装完整报告 ═══
report = {
    "version": "2.0",
    "verified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "input_fingerprint": INPUT_FINGERPRINT,
    "input_hashes": INPUT,
    "config": {k: v for k, v in U7_CALENDAR_V3_H20_CONFIG.items() if k != 'name'},
    "output_signature": OUTPUT_SIGNATURE,
    "trade_ledger_hash": TRADE_HASH,
    "metrics_active": result['metrics_active'],
    "metrics_calendar": result['metrics_calendar'],
    "bench_total": result['bench_total'],
    "regime_counts": result['regime_counts'],
    "trade_count": sum(1 for w in result['trade_ledger'] if w['trades']),
    "total_windows": len(result['trade_ledger']),
    "trade_ledger": result['trade_ledger'],
}

# ═══ 5. 写入 ═══
json_path = os.path.join(DATA_DIR, "backtest_verified.json")
with open(json_path, 'w') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

# ═══ 6. 人类可读报告 ═══
m_a = result['metrics_active']
m_c = result['metrics_calendar']

print("=" * 70)
print("🔐 U7 回测验证 v2.0")
print("=" * 70)
print(f"  输入指纹:  {INPUT_FINGERPRINT[:16]}")
print(f"  输出签名:  {OUTPUT_SIGNATURE[:16]}")
print()
print("  📊 双口径指标:")
active_label = f"口径A (有持仓, {m_a['active_windows']}窗)"
calendar_label = f"口径B (日历, {m_c['total_windows']}窗)"
print(f"  {'指标':<16} {active_label:>20}  {calendar_label:>20}")
print(f"  {'─'*16} {'─'*20}  {'─'*20}")
print(f"  {'累计收益':<16} {m_a['total_ret']:>19.1f}%  {m_c['total_ret']:>19.1f}%")
print(f"  {'年化收益':<16} {m_a['ann']:>19.1f}%  {m_c['ann']:>19.1f}%")
print(f"  {'最大回撤':<16} {m_a['dd']:>19.1f}%  {m_c['dd']:>19.1f}%")
print(f"  {'夏普比率':<16} {m_a['sharpe']:>19.2f}   {m_c['sharpe']:>19.2f}")
print(f"  {'胜率':<16} {m_a['win_rate']:>19.1f}%  {m_c['win_rate']:>19.1f}%")
print(f"  {'超额 (vs 等权)':<16} {m_a['alpha']:>19.1f}%  {m_c['alpha']:>19.1f}%")
print()
print(f"  体制分布: {result['regime_counts']}")
print(f"  交易账本哈希: {TRADE_HASH[:16]}")
print()
print(f"  📁 报告: {json_path}")
print()
print("  🛡️ 审计方法:")
print(f"    python3 scripts/backtest_verify.py")
print(f"  → 输出签名相同 = 输入端到端可复现")

# ═══ 7. Markdown 报告 ═══
cfg = U7_CALENDAR_V3_H20_CONFIG
md_path = os.path.join(DATA_DIR, "backtest_report.md")
with open(md_path, 'w') as f:
    f.write(f"""# U7 回测验证报告 v2.0

> 验证时间: {report['verified_at']}
> 输入指纹: `{INPUT_FINGERPRINT[:16]}...`
> 输出签名: `{OUTPUT_SIGNATURE[:16]}...`

## 输入哈希
| 文件 | SHA256 |
|------|--------|
| combined_daily.json | `{INPUT['combined_daily'][:16]}...` |
| etf_universe.json | `{INPUT['etf_universe'][:16]}...` |
| u7_strategy.py | `{INPUT['u7_strategy'][:16]}...` |

## 策略配置
- **名称**: {cfg['name']}
- **持仓周期**: {cfg.get('hold_days',10)} 日
- **MA条件**: MA20 > MA60
- **量能**: ≥1.2x 20日均量
- **止损**: {'否（不止损）' if not cfg.get('stop_loss_pct') else f"{cfg['stop_loss_pct']}%"}
- **板块分散**: ≥{cfg.get('min_sectors',0)} 板块
- **仓位**: 牛市{cfg['max_positions']['bull']} / 中性{cfg['max_positions']['neutral']} / 熊市{cfg['max_positions']['bear']}

## 双口径指标

| 指标 | 口径A (有持仓) | 口径B (日历) |
|------|:---:|:---:|
| 窗口数 | 48 / 74 | 74 / 74 |
| 累计收益 | +{m_a['total_ret']}% | +{m_c['total_ret']}% |
| 年化收益 | +{m_a['ann']}% | +{m_c['ann']}% |
| 最大回撤 | {m_a['dd']}% | {m_c['dd']}% |
| 夏普比率 | {m_a['sharpe']} | {m_c['sharpe']} |
| 胜率 | {m_a['win_rate']}% | {m_c['win_rate']}% |
| 超额 α | +{m_a['alpha']}% | +{m_c['alpha']}% |

## 市场体制分布
{chr(10).join(f'- {k}: {v} 窗 ({v/74*100:.0f}%)' for k,v in result['regime_counts'].items())}

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
""")

print(f"  📁 报告: {md_path}")
