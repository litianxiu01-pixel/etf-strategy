#!/usr/bin/env python3
"""Compare v1 vs v2 regime detector outputs + backtest U7 with both."""
import json, sys, os, math
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")

# ── Load both regime histories ───────────────────────────────────────────────

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

v1 = load_regime(os.path.join(DATA_DIR, 'regime_v1_daily.jsonl'))
v2 = load_regime(os.path.join(DATA_DIR, 'regime_v2_daily.jsonl'))

# ── Day-by-day comparison ───────────────────────────────────────────────────

agree = 0; disagree = 0
transitions = {'v1': [], 'v2': []}  # regime transitions
changes = defaultdict(int)  # v1→v2 change type
regime_counts_v1 = defaultdict(int)
regime_counts_v2 = defaultdict(int)
v2_types = defaultdict(int)  # pure/transitional/mixed counts

# Only compare valid regime days (skip insufficient_data)
valid_dates = sorted(set(v1.keys()) & set(v2.keys()))

for date in valid_dates:
    r1 = v1[date]['regime']
    r2 = v2[date]['regime']
    
    if r1 == 'insufficient_data' or r2 == 'insufficient_data':
        continue
    
    regime_counts_v1[r1] += 1
    regime_counts_v2[r2] += 1
    
    if r1 == r2:
        agree += 1
    else:
        disagree += 1
        changes[f"{r1}→{r2}"] += 1
    
    # v2 meta
    rt = v2[date].get('regime_type', '?')
    v2_types[rt] += 1

total = agree + disagree
print("=" * 60)
print("V1 vs V2 体制对比")
print("=" * 60)
print(f"\n总有效天数: {total}")
print(f"一致: {agree} ({agree/total*100:.1f}%)")
print(f"分歧: {disagree} ({disagree/total*100:.1f}%)")

print(f"\n体制分布:")
print(f"  V1: {dict(regime_counts_v1)}")
print(f"  V2: {dict(regime_counts_v2)}")
print(f"  V2 subtypes: {dict(v2_types)}")

print(f"\n分歧类型 (V1→V2):")
for k, v in sorted(changes.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v} 天 ({v/disagree*100:.1f}%)")

# ── Show sample disagreements ───────────────────────────────────────────────

print(f"\n分歧样例 (前10条):")
shown = 0
for date in valid_dates:
    r1 = v1[date]['regime']
    r2 = v2[date]['regime']
    if r1 == 'insufficient_data' or r2 == 'insufficient_data':
        continue
    if r1 != r2:
        print(f"  {date}: V1={r1:7s}({v1[date]['rawScore']:+.3f}) → V2={r2:7s}({v2[date]['rawScore']:+.3f})  type={v2[date].get('regime_type','?')}")
        shown += 1
        if shown >= 10:
            break

# ── Print monthly regime comparison ──────────────────────────────────────────

print(f"\n月度体制占比:")
months = defaultdict(lambda: {'v1_bull': 0, 'v2_bull': 0, 'v1_neutral': 0, 'v2_neutral': 0, 'v1_bear': 0, 'v2_bear': 0, 'cnt': 0})
for date in valid_dates:
    r1 = v1[date]['regime']
    r2 = v2[date]['regime']
    if r1 == 'insufficient_data' or r2 == 'insufficient_data':
        continue
    m = date[:7]
    months[m][f'v1_{r1}'] += 1
    months[m][f'v2_{r2}'] += 1
    months[m]['cnt'] += 1

print(f"{'Month':<8} {'V1_B':>5} {'V1_N':>5} {'V1_R':>5} {'V2_B':>5} {'V2_N':>5} {'V2_R':>5}  Note")
for m in sorted(months.keys())[-12:]:  # last 12 months
    d = months[m]
    c = d['cnt']
    v1b = d['v1_bull']/c*100; v1n = d['v1_neutral']/c*100; v1r = d['v1_bear']/c*100
    v2b = d['v2_bull']/c*100; v2n = d['v2_neutral']/c*100; v2r = d['v2_bear']/c*100
    diff = abs(v1b-v2b) + abs(v1n-v2n) + abs(v1r-v2r)
    note = "⚠️ 偏离" if diff > 20 else ""
    print(f"{m:<8} {v1b:4.0f}% {v1n:4.0f}% {v1r:4.0f}% {v2b:4.0f}% {v2n:4.0f}% {v2r:4.0f}% {note}")

print("\n✅ 对比完成")
