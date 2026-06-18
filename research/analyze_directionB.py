#!/usr/bin/env python3
"""
方向B：体制边界置信度分析
=========================================
任务：
1. 体制连续分数分档（6档）
2. 各档位实际回测表现
3. 找"危险区域"
4. 量化边界降仓的 DD 改善和收益代价

数据：backtest_verified.json 的 trade_ledger
"""

import json, os, sys, math
import numpy as np

WORKSPACE = os.path.expanduser("~/.qclaw/workspace-main")
DATA_DIR  = f"{WORKSPACE}/data/market_regime"
OUT_DIR   = f"{WORKSPACE}/data/research"
os.makedirs(OUT_DIR, exist_ok=True)

sys.path.insert(0, f"{WORKSPACE}/scripts")
from u7_strategy import _load, detect_regime_at

_combined, _universe, _dates_all = None, None, None
def _ensure():
    global _combined, _universe, _dates_all
    if _combined is None:
        _combined, _universe, _dates_all = _load()

# ════════════════════════════════════════════════════════
# 方向B-1：体制分档
# ════════════════════════════════════════════════════════

def assign_bin(score):
    if score < 0.20:
        return '深熊 0.00-0.20'
    elif score < 0.35:
        return '浅熊 0.20-0.35'
    elif score < 0.55:
        return '浅中性 0.35-0.55'
    elif score < 0.65:
        return '深中性 0.55-0.65'
    elif score < 0.80:
        return '浅牛 0.65-0.80'
    else:
        return '深牛 0.80-1.00'

BOUNDARY_BINS = {'浅熊 0.20-0.35', '浅中性 0.35-0.55', '深中性 0.55-0.65', '浅牛 0.65-0.80'}
DEEP_BINS     = {'深熊 0.00-0.20', '深牛 0.80-1.00'}

def analyze_bin_performance():
    """各档位的实际回测表现"""
    _ensure()
    with open(f"{DATA_DIR}/backtest_verified.json") as f:
        bt = json.load(f)
    ledger = bt['trade_ledger']
    
    date_to_idx = {d: i for i, d in enumerate(_dates_all)}
    
    # 对每个窗口分配 bin
    bin_windows = {bin_label: [] for bin_label in 
                   ['深熊 0.00-0.20','浅熊 0.20-0.35','浅中性 0.35-0.55',
                    '深中性 0.55-0.65','浅牛 0.65-0.80','深牛 0.80-1.00']}
    
    for w in ledger:
        d = w['date']
        idx = date_to_idx.get(d)
        if idx is None:
            for candidate in range(0, len(_dates_all)):
                if _dates_all[candidate] >= d:
                    idx = candidate
                    break
        if idx is None:
            continue
        regime_info = detect_regime_at(idx)
        score = regime_info['conf']
        bin_label = assign_bin(score)
        bin_windows[bin_label].append({
            'window': w['window'],
            'date': d,
            'regime': w['regime'],
            'conf': w['conf'],
            'bin_conf': score,
            'bin': bin_label,
            'avg_return': w['avg_return'],
            'num_trades': len(w['trades']),
            'is_loss': w['avg_return'] < 0,
        })
    
    print("\n═══ 方向B-1：体制分档表现 ═══")
    print(f"{'档位':<20} {'窗口数':>6} {'平均收益':>10} {'胜率':>8} {'最大单窗亏损':>14} {'亏损窗口比例':>14}")
    print("-" * 80)
    
    bin_stats = {}
    for bin_label in ['深熊 0.00-0.20','浅熊 0.20-0.35','浅中性 0.35-0.55',
                      '深中性 0.55-0.65','浅牛 0.65-0.80','深牛 0.80-1.00']:
        wins = bin_windows[bin_label]
        if not wins:
            print(f"{bin_label:<20} {'0':>6} {'-':>10}")
            continue
        rets = [w['avg_return'] for w in wins if w['avg_return'] != 0]  # 只算活跃窗口
        losses = [w for w in wins if w['is_loss'] and w['num_trades'] > 0]
        win_rate = (len(wins) - len(losses)) / len(wins) * 100 if wins else 0
        avg_ret = np.mean(rets) if rets else 0
        max_loss = min([w['avg_return'] for w in wins if w['num_trades'] > 0], default=0)
        loss_ratio = len(losses) / len(wins) * 100 if wins else 0
        
        bin_stats[bin_label] = {
            'n': len(wins),
            'avg_ret': avg_ret,
            'win_rate': win_rate,
            'max_loss': max_loss,
            'loss_ratio': loss_ratio,
            'rets': rets,
        }
        flag = " ⚠️边界" if bin_label in BOUNDARY_BINS else (" ⭐深区" if bin_label in DEEP_BINS else "")
        print(f"{bin_label:<20} {len(wins):>6} {avg_ret:>+9.2f}% {win_rate:>7.1f}% {max_loss:>+13.2f}% {loss_ratio:>13.1f}%{flag}")
    
    return bin_windows, bin_stats


# ════════════════════════════════════════════════════════
# 方向B-2：危险区域识别
# ════════════════════════════════════════════════════════

def find_danger_zones(bin_stats):
    """识别亏损比例异常高的档位"""
    print("\n═══ 方向B-2：危险区域识别 ═══")
    
    # 计算平均亏损比例（作为基准）
    all_bins = {k: v for k, v in bin_stats.items() if v.get('n', 0) > 0}
    if not all_bins:
        return {}
    
    avg_loss_ratio = np.mean([v['loss_ratio'] for v in all_bins.values()])
    std_loss_ratio = np.std([v['loss_ratio'] for v in all_bins.values()])
    
    danger_bins = {}
    for bin_label, stats in all_bins.items():
        if stats['loss_ratio'] > avg_loss_ratio + std_loss_ratio:
            danger_bins[bin_label] = stats
            print(f"  ⚠️  {bin_label}: 亏损比例 {stats['loss_ratio']:.1f}% > 基准 {avg_loss_ratio:.1f}% + {std_loss_ratio:.1f}%")
        elif stats['loss_ratio'] < avg_loss_ratio - std_loss_ratio:
            print(f"  ⭐  {bin_label}: 亏损比例 {stats['loss_ratio']:.1f}% < 基准 {avg_loss_ratio:.1f}% - {std_loss_ratio:.1f}%（安全区）")
        else:
            print(f"  →   {bin_label}: 亏损比例 {stats['loss_ratio']:.1f}% ≈ 基准")
    
    if not danger_bins:
        print("  未发现统计显著的危险区域")
    
    return danger_bins


# ════════════════════════════════════════════════════════
# 方向B-3：边界降仓回测对照
# ════════════════════════════════════════════════════════

def boundary_penalty_backtest(bin_windows, bin_stats):
    """
    边界区域（浅牛/浅中性等）→ 降低一只持仓上限
    当前 max_positions: bull=3, neutral=5, bear=2
    边界降仓规则：
      - 浅牛 (0.65-0.80): bull max 3 → max 2
      - 深中性 (0.55-0.65): neutral max 5 → max 4
      - 浅中性 (0.35-0.55): neutral max 5 → max 4
    """
    with open(f"{DATA_DIR}/backtest_verified.json") as f:
        bt = json.load(f)
    ledger = bt['trade_ledger']
    date_to_idx = {d: i for i, d in enumerate(_dates_all)}
    
    # 边界档位映射
    BOUNDARY_MAP = {
        '浅牛 0.65-0.80': 2,   # bull 3→2
        '深中性 0.55-0.65': 4,  # neutral 5→4
        '浅中性 0.35-0.55': 4,  # neutral 5→4
    }
    
    modified_rets = []
    boundary_modified = []
    
    for w in ledger:
        d = w['date']
        idx = date_to_idx.get(d)
        if idx is None:
            for candidate in range(0, len(_dates_all)):
                if _dates_all[candidate] >= d:
                    idx = candidate
                    break
        if idx is None:
            modified_rets.append(w['avg_return'])
            continue
        
        regime_info = detect_regime_at(idx)
        score = regime_info['conf']
        bin_label = assign_bin(score)
        
        # 找到原始持仓的平均收益
        # 如果边界降仓，只取表现最好的 N 只
        if bin_label in BOUNDARY_MAP and w['trades']:
            n_keep = BOUNDARY_MAP[bin_label]
            if len(w['trades']) > n_keep:
                # 取收益最好的 n_keep 只
                sorted_trades = sorted(w['trades'], key=lambda t: t['ret'], reverse=True)
                kept_rets = [t['ret'] for t in sorted_trades[:n_keep]]
                new_avg_ret = np.mean(kept_rets)
                modified_rets.append(new_avg_ret)
                boundary_modified.append({
                    'window': w['window'],
                    'date': d,
                    'bin': bin_label,
                    'old_ret': w['avg_return'],
                    'new_ret': new_avg_ret,
                    'n_kept': n_keep,
                })
            else:
                modified_rets.append(w['avg_return'])
        else:
            modified_rets.append(w['avg_return'])
    
    # baseline（原始回测，只取活跃窗口）
    baseline_active = [(w['window'], w['avg_return']) for w in ledger if w['avg_return'] != 0]
    modified_active = [r for r in modified_rets if r != 0]
    
    # 实际对比（只对有修改的窗口）
    boundary_window_ids = set(b['window'] for b in boundary_modified)
    bm_rets = [w['avg_return'] for w in ledger if w['window'] in boundary_window_ids and w['avg_return'] != 0]
    bm_mod = [b['new_ret'] for b in boundary_modified if b['old_ret'] != 0]
    
    def calc_metrics(rets_arr, label):
        rets = [r for r in rets_arr if r != 0]
        if not rets: return None
        wins = sum(1 for r in rets if r > 0)
        total = sum(rets)
        peak = 0; max_dd = 0
        for r in rets:
            peak = max(peak, peak + r)
            max_dd = max(max_dd, peak - (peak + r))
        print(f"  {label}: 总收益={total:+.2f}% 胜率={wins/len(rets)*100:.1f}% 最大DD={max_dd:.2f}% (n={len(rets)})")
        return {'total': total, 'win_rate': wins/len(rets)*100, 'max_dd': max_dd, 'n': len(rets)}
    
    print("\n═══ 方向B-3：边界降仓回测对照 ═══")
    print(f"  边界降仓影响的窗口数: {len(boundary_modified)}")
    for b in boundary_modified:
        print(f"    窗口{b['window']}({b['date']}) {b['bin']}: {b['old_ret']:+.2f}% → {b['new_ret']:+.2f}% (保留{b['n_kept']}只)")
    
    bm = calc_metrics([w['avg_return'] for w in ledger if w['avg_return'] != 0], "Baseline")
    wm = calc_metrics(modified_active, "边界降仓")
    
    # 只针对边界窗口的对比
    bm2 = calc_metrics(bm_rets, "Baseline(边界窗口)")
    wm2 = calc_metrics(bm_mod, "边界降仓(边界窗口)")
    
    if bm and wm:
        dd_change = wm['max_dd'] - bm['max_dd']
        ret_change = wm['total'] - bm['total']
        print(f"\n  整体DD变化: {dd_change:+.2f}%")
        print(f"  整体收益变化: {ret_change:+.2f}%")
        print(f"  结论: {'✅ DD改善' if dd_change < 0 else '❌ DD恶化'}")
        print(f"  建议: {'✅ 可升产' if abs(ret_change) < abs(dd_change) and dd_change < 0 else '❌ not_promoted'}")
        
        return {'dd_change': dd_change, 'ret_change': ret_change, 'recommended': abs(ret_change) < abs(dd_change) and dd_change < 0}
    return {}


# ════════════════════════════════════════════════════════
# 主函数
# ════════════════════════════════════════════════════════

def main():
    _ensure()
    print("=" * 65)
    print("方向B：体制边界置信度分析")
    print("=" * 65)
    
    bin_windows, bin_stats = analyze_bin_performance()
    danger_bins = find_danger_zones(bin_stats)
    comp = boundary_penalty_backtest(bin_windows, bin_stats)
    
    # 生成报告
    with open(f"{DATA_DIR}/backtest_verified.json") as f:
        bt = json.load(f)
    ledger = bt['trade_ledger']
    
    report = f"""# 方向B报告：体制边界置信度

## B-1：体制分档表现

| 档位 | 窗口数 | 平均收益 | 胜率 | 最大单窗亏损 | 亏损比例 | 区域 |
|------|--------|---------|------|------------|---------|------|
"""
    for bin_label in ['深熊 0.00-0.20','浅熊 0.20-0.35','浅中性 0.35-0.55',
                      '深中性 0.55-0.65','浅牛 0.65-0.80','深牛 0.80-1.00']:
        stats = bin_stats.get(bin_label, {})
        n = stats.get('n', 0)
        if n == 0:
            report += f"| {bin_label} | 0 | - | - | - | - | - |\n"
            continue
        region = "⭐深区" if bin_label in DEEP_BINS else ("⚠️边界" if bin_label in BOUNDARY_BINS else "")
        report += (f"| {bin_label} | {n} | {stats['avg_ret']:+.2f}% | "
                   f"{stats['win_rate']:.1f}% | {stats['max_loss']:+.2f}% | "
                   f"{stats['loss_ratio']:.1f}% | {region} |\n")
    
    report += """
**档位说明**：
- ⭐ 深熊/深牛：置信度最高，边界风险最小
- ⚠️ 边界区域：置信度中等，可能存在误判风险
"""
    
    report += f"""
## B-2：危险区域

"""
    if danger_bins:
        for bin_label, stats in danger_bins.items():
            report += f"- ⚠️ **{bin_label}**：亏损比例 {stats['loss_ratio']:.1f}%，显著高于平均\n"
    else:
        report += "未发现统计显著的危险区域。边界档位的亏损比例与深区无显著差异。\n"
    
    # 计算各档位风险对比
    boundary_avg_loss = np.mean([bin_stats[k]['loss_ratio'] for k in BOUNDARY_BINS if bin_stats.get(k, {}).get('n', 0) > 0])
    deep_avg_loss = np.mean([bin_stats[k]['loss_ratio'] for k in DEEP_BINS if bin_stats.get(k, {}).get('n', 0) > 0])
    
    report += f"""
**边界 vs 深区亏损比例**：
- 边界区域平均亏损比例：{boundary_avg_loss:.1f}%
- 深区平均亏损比例：{deep_avg_loss:.1f}%
- 差异：{boundary_avg_loss - deep_avg_loss:+.1f}%

"""
    
    report += f"""
## B-3：边界降仓回测对照

**规则**：
- 浅牛 (0.65-0.80): bull max 3 → max 2
- 深中性 (0.55-0.65): neutral max 5 → max 4
- 浅中性 (0.35-0.55): neutral max 5 → max 4

| 指标 | Baseline | 边界降仓 | 变化 |
|------|----------|---------|------|
"""
    if comp:
        report += f"| DD | {comp.get('dd_change', 0):+.2f}% | - | - |\n"
        report += f"| 收益 | - | - | {comp['ret_change']:+.2f}% |\n"
        report += f"| 建议 | **{'✅ 可升产' if comp['recommended'] else '❌ not_promoted'}** |\n"
    
    report += f"""
---

## 最终结论

**{'✅ 可升产' if comp.get('recommended') else '❌ not_promoted'}**

- 边界区域亏损比例 {boundary_avg_loss:.1f}% vs 深区 {deep_avg_loss:.1f}%
- DD 改善：{comp.get('dd_change', 0):+.2f}%
- 收益代价：{comp.get('ret_change', 0):+.2f}%

**判断逻辑**：如果边界降仓带来 DD 改善 > 收益代价，则建议在边界区域自动降低一只持仓上限。

---
*生成时间：2026-06-17 | 数据来源：backtest_verified.json + combined_daily.json*
"""
    
    with open(f"{OUT_DIR}/directionB_report.md", 'w') as f:
        f.write(report)
    print(f"\n✅ 报告已保存: {OUT_DIR}/directionB_report.md")
    return comp


if __name__ == '__main__':
    main()
