#!/usr/bin/env python3
"""
Backtest U7 strategy with external regime histories (V1 vs V2).
Creates symlinks so u7_strategy.py finds data, then monkey-patches detect_regime_at.
"""
import sys, os, json, math
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, 'data')
CORE_DIR = os.path.join(REPO_ROOT, 'core')

# ── Load regime histories ────────────────────────────────────────────────────

def load_regime(path):
    records = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith('{'):
                continue
            try:
                r = json.loads(line)
                records[r['date']] = r
            except json.JSONDecodeError:
                pass
    return records

regime_v1 = load_regime(os.path.join(DATA_DIR, 'regime_v1_daily.jsonl'))
regime_v2 = load_regime(os.path.join(DATA_DIR, 'regime_v2_daily.jsonl'))

# ── Import u7_strategy (with symlinks in place it should work) ───────────────

sys.path.insert(0, CORE_DIR)
import u7_strategy as u7

# ── Monkey-patch detect_regime_at ─────────────────────────────────────────────

original_detect = u7.detect_regime_at

def make_patched_detect(regime_map):
    def patched_detect(idx):
        orig = original_detect(idx)
        _, _, dates_all = u7._load()
        if idx < len(dates_all):
            date = dates_all[idx]
            if date in regime_map:
                r = regime_map[date]
                if r['regime'] in ('bull', 'neutral', 'bear'):
                    return {
                        'regime': r['regime'],
                        'conf': abs(r.get('rawScore', 0.5)),
                        'ma20_ratio': orig.get('ma20_ratio', 100.0),
                    }
        return orig
    return patched_detect

# ── Run backtests ─────────────────────────────────────────────────────────────

CONFIG = u7.make_config('U7_CalendarV3_H20',
    hold_days=20, min_sectors=4, cold_threshold=-3.0,
    max_positions={'bull': 3, 'neutral': 5, 'bear': 2},
    conf_scale=False,
)

# V1 backtest
print("Running V1 backtest...")
u7.detect_regime_at = make_patched_detect(regime_v1)
result_v1 = u7.run_backtest(CONFIG)

# V2 backtest
print("Running V2 backtest...")
u7.detect_regime_at = make_patched_detect(regime_v2)
result_v2 = u7.run_backtest(CONFIG)

# ── Extract metrics ───────────────────────────────────────────────────────────

def extract_metrics(r):
    m = r.get('metrics_active', {})
    return {
        'total_return': m.get('total_ret', 0),
        'ann_return': m.get('ann', 0),
        'sharpe': m.get('sharpe', 0),
        'max_dd': m.get('dd', 0),
        'win_rate': m.get('win_rate', 0),
        'n_windows': m.get('active_windows', 0),
        'total_windows': m.get('total_windows', 0),
        'alpha': m.get('alpha', 0),
        'regime_counts': r.get('regime_counts', {}),
        'bench_total': r.get('bench_total', 0),
        'n_trades': len(r.get('trade_ledger', [])),
    }

m1 = extract_metrics(result_v1)
m2 = extract_metrics(result_v2)

print(f"\nActive metrics keys: {list(result_v1.get('metrics_active', {}).keys())}")
print(f"Calendar metrics keys: {list(result_v1.get('metrics_calendar', {}).keys())}")

print("\n" + "=" * 60)
print("U7 策略回测: V1 vs V2 体制检测器")
print("=" * 60)

metrics = [
    ('累计收益 (active)', 'total_return', '%'),
    ('年化收益', 'ann_return', '%'),
    ('Sharpe', 'sharpe', ''),
    ('最大回撤', 'max_dd', '%'),
    ('胜率', 'win_rate', '%'),
    ('Alpha', 'alpha', ''),
    ('窗口数', 'n_windows', ''),
    ('交易笔数', 'n_trades', ''),
    ('基准收益', 'bench_total', '%'),
]

print(f"\n{'指标':<16} {'V1 (原版)':>12} {'V2 (新版)':>12} {'变化':>10}")
print("-" * 54)
for name, key, unit in metrics:
    v1v = m1.get(key, 0)
    v2v = m2.get(key, 0)
    diff = v2v - v1v
    sign = '+' if diff > 0 else ''
    
    if key in ('total_return', 'sharpe', 'win_rate'):
        arrow = ' ✅' if diff > 0 else (' ❌' if diff < 0 else '')
    elif key == 'max_dd':
        arrow = ' ✅' if diff < 0 else (' ❌' if diff > 0 else '')
    else:
        arrow = ''
    
    v1s = f"{v1v:.2f}{unit}" if isinstance(v1v, float) else f"{v1v}{unit}"
    v2s = f"{v2v:.2f}{unit}" if isinstance(v2v, float) else f"{v2v}{unit}"
    diffs = f"{sign}{diff:.2f}{unit}" if isinstance(diff, float) else f"{sign}{diff}{unit}"
    print(f"{name:<16} {v1s:>12} {v2s:>12} {diffs:>10}{arrow}")

print(f"\n体制分布:")
print(f"  V1: {m1.get('regime_counts', {})}")
print(f"  V2: {m2.get('regime_counts', {})}")

# ── Window-level regime detail ────────────────────────────────────────────────

print(f"\n逐窗口体制差异:")
ledger_v1 = result_v1.get('trade_ledger', [])
ledger_v2 = result_v2.get('trade_ledger', [])
n = min(len(ledger_v1), len(ledger_v2))
regime_diffs = 0
return_diffs = []
for i in range(n):
    r1 = ledger_v1[i].get('regime', '?')
    r2 = ledger_v2[i].get('regime', '?')
    if r1 != r2:
        regime_diffs += 1
        ret1 = ledger_v1[i].get('avg_return', 0)
        ret2 = ledger_v2[i].get('avg_return', 0)
        date = ledger_v1[i].get('date', '?')
        return_diffs.append((date, r1, r2, ret1, ret2))
        r1s = f"{ret1*100:.1f}%" if isinstance(ret1, float) and abs(ret1) < 10 else f"{ret1:.3f}"
        r2s = f"{ret2*100:.1f}%" if isinstance(ret2, float) and abs(ret2) < 10 else f"{ret2:.3f}"
        return_diffs.append((date, r1, r2, ret1, ret2))

print(f"  体制分歧窗口数: {regime_diffs}/{n}")
if return_diffs:
    print(f"  {'Date':<12} {'V1':>8} {'V2':>8} {'V1_ret':>8} {'V2_ret':>8}  Delta")
    for date, r1, r2, ret1, ret2 in return_diffs:
        delta = (ret2 - ret1) * 100
        r1s = f"{ret1*100:.1f}%" if isinstance(ret1, float) else str(ret1)
        r2s = f"{ret2*100:.1f}%" if isinstance(ret2, float) else str(ret2)
        print(f"  {str(date)[:12]:<12} {r1:>8} {r2:>8} {r1s:>8} {r2s:>8}  {delta:+.1f}pp")

print("\n✅ 对比完成")
