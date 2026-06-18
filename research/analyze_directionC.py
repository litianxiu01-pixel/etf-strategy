#!/usr/bin/env python3
"""
方向C：失败模式归因
=========================================
任务：
1. 取出所有亏损窗口
2. 归因三类：选错标的 / 体制风险 / 系统性崩盘
3. 统计占比，给出优化建议

数据：backtest_verified.json 的 trade_ledger
"""

import json, os, sys, math
import numpy as np

WORKSPACE = os.path.expanduser("~/.qclaw/workspace-main")
DATA_DIR  = f"{WORKSPACE}/data/market_regime"
OUT_DIR   = f"{WORKSPACE}/data/research"
os.makedirs(OUT_DIR, exist_ok=True)

sys.path.insert(0, f"{WORKSPACE}/scripts")
from u7_strategy import _load, detect_regime_at, get_closes

_combined, _universe, _dates_all = None, None, None
def _ensure():
    global _combined, _universe, _dates_all
    if _combined is None:
        _combined, _universe, _dates_all = _load()


# ════════════════════════════════════════════════════════
# 工具：获取候选池在该窗口的收益
# ════════════════════════════════════════════════════════

def get_window_pool_returns(idx, hold_days=20):
    """
    获取候选池中所有 ETF 在 [idx, idx+hold_days] 区间的收益。
    返回 {code: ret_pct}
    """
    _ensure()
    if idx + hold_days >= len(_combined):
        hold_days = len(_combined) - idx - 1
    if hold_days <= 0:
        return {}
    
    # 候选池：所有 active ETF
    pool_codes = [c for c, info in _universe['etfs'].items() if info['status'] == 'active']
    
    results = {}
    for code in pool_codes:
        entry_closes = get_closes(code, idx, 5)  #  entry 附近的价格
        exit_closes = get_closes(code, idx + hold_days, 5)
        if not entry_closes or not exit_closes:
            continue
        entry_price = entry_closes[-1]  # idx 当天的收盘价
        exit_price = exit_closes[-1]    # exit 天的收盘价
        if entry_price and exit_price and entry_price > 0:
            ret = (exit_price / entry_price - 1) * 100
            results[code] = ret
    return results


def get_pool_median(pool_rets):
    """候选池收益中位数"""
    if not pool_rets:
        return 0
    return float(np.median(list(pool_rets.values())))


def classify_loss_window(w, pool_rets):
    """
    对单个亏损窗口归因。
    
    归因逻辑：
    1. 选错标的：持仓 ETF 表现 < 候选池中位数（系统选股失误）
    2. 体制风险：本应低仓但满仓
       - bear 体制下，持仓 >= 2 只非避险 ETF
       - 浅中性/浅牛边界体制，持仓 > 基准 max 但实际亏钱
    3. 系统性崩盘：候选池 > 70% 的 ETF 在该窗口负收益
    
    优先级：系统性 > 选错标的 > 体制风险
    """
    if w['avg_return'] >= 0:
        return None, None  # 非亏损窗口
    
    # ── 系统性崩盘 ───────────────────────────────────────
    neg_count = sum(1 for r in pool_rets.values() if r < 0)
    total_count = len(pool_rets)
    neg_ratio = neg_count / total_count if total_count > 0 else 0
    
    if neg_ratio > 0.70:
        return 'systemic', {
            'neg_ratio': neg_ratio,
            'neg_count': neg_count,
            'total_count': total_count,
            'pool_median': get_pool_median(pool_rets),
        }
    
    # ── 选错标的 ─────────────────────────────────────────
    # 持仓中，表现最差的 ETF 是否显著落后于池中位数？
    if w['trades']:
        held_codes = [t['code'] for t in w['trades']]
        held_rets = {code: pool_rets.get(code, 0) for code in held_codes}
        median_ret = get_pool_median(pool_rets)
        
        # 如果任一持仓 ETF 的收益 < 候选池中位数，即选错
        for code, ret in held_rets.items():
            if ret < median_ret and ret < w['avg_return'] * 0.8:
                # 个股落后于中位数
                return 'wrong_pick', {
                    'held_rets': held_rets,
                    'pool_median': median_ret,
                    'worst_held': min(held_rets.items(), key=lambda x: x[1]),
                    'underperform': ret - median_ret,
                }
    
    # ── 体制风险 ─────────────────────────────────────────
    # bear 体制下持仓非避险资产
    if w['regime'] == 'bear' and w['trades']:
        non_hedge = [t for t in w['trades'] if t['sector'] not in ['黄金', '国债/债券']]
        if len(non_hedge) >= 2:
            return 'regime_risk', {
                'regime': w['regime'],
                'non_hedge_count': len(non_hedge),
                'non_hedge_sectors': [t['sector'] for t in non_hedge],
            }
    
    return 'other', {}


# ════════════════════════════════════════════════════════
# 主分析
# ════════════════════════════════════════════════════════

def main():
    _ensure()
    print("=" * 65)
    print("方向C：失败模式归因")
    print("=" * 65)
    
    with open(f"{DATA_DIR}/backtest_verified.json") as f:
        bt = json.load(f)
    config = bt['config']
    ledger = bt['trade_ledger']
    
    date_to_idx = {d: i for i, d in enumerate(_dates_all)}
    hold_days = config.get('hold_days', 20)
    
    # ── 亏损窗口归因 ──────────────────────────────────────
    loss_windows = [w for w in ledger if w['avg_return'] < 0 and len(w['trades']) > 0]
    print(f"\n═══ 方向C-1：亏损窗口概览 ═══")
    print(f"  总活跃窗口: {len([w for w in ledger if w['avg_return'] != 0])}")
    print(f"  亏损窗口: {len(loss_windows)} ({len(loss_windows)/len([w for w in ledger if w['avg_return'] != 0])*100:.1f}%)")
    
    attributions = []
    pool_stats = []
    
    for w in loss_windows:
        d = w['date']
        idx = date_to_idx.get(d)
        if idx is None:
            for candidate in range(0, len(_dates_all)):
                if _dates_all[candidate] >= d:
                    idx = candidate
                    break
        if idx is None:
            continue
        
        pool_rets = get_window_pool_returns(idx, hold_days)
        if not pool_rets:
            continue
        
        pool_stats.append({
            'window': w['window'],
            'date': d,
            'pool_median': get_pool_median(pool_rets),
            'neg_ratio': sum(1 for r in pool_rets.values() if r < 0) / len(pool_rets),
        })
        
        cause, details = classify_loss_window(w, pool_rets)
        attributions.append({
            'window': w['window'],
            'date': d,
            'regime': w['regime'],
            'conf': w['conf'],
            'avg_return': w['avg_return'],
            'num_trades': len(w['trades']),
            'trades': w['trades'],
            'cause': cause,
            'details': details,
        })
    
    # ── 统计三类原因 ──────────────────────────────────────
    cause_counts = {'wrong_pick': 0, 'regime_risk': 0, 'systemic': 0, 'other': 0}
    for a in attributions:
        cause_counts[a['cause']] = cause_counts.get(a['cause'], 0) + 1
    
    total_loss = len(attributions)
    
    print(f"\n═══ 方向C-2：失败原因占比 ═══")
    for cause, count in sorted(cause_counts.items(), key=lambda x: -x[1]):
        pct = count / total_loss * 100 if total_loss else 0
        bar = '█' * int(pct / 5)
        label = {'wrong_pick': '选错标的', 'regime_risk': '体制风险', 'systemic': '系统性崩盘', 'other': '其他'}.get(cause, cause)
        print(f"  {label:<10}: {count}/{total_loss} ({pct:5.1f}%) {bar}")
    
    # ── 系统性崩盘详情 ────────────────────────────────────
    systemic = [a for a in attributions if a['cause'] == 'systemic']
    if systemic:
        print(f"\n  系统性崩盘详情（{len(systemic)} 个窗口）：")
        for a in systemic:
            det = a['details']
            print(f"    窗口{a['window']}({a['date']}) {a['regime']} conf={a['conf']:.2f}")
            print(f"      池内负收益比例: {det['neg_ratio']*100:.1f}% ({det['neg_count']}/{det['total_count']} ETF)")
            print(f"      候选池中位数收益: {det['pool_median']:+.2f}%")
    
    # ── 选错标的详情 ───────────────────────────────────────
    wrong_pick = [a for a in attributions if a['cause'] == 'wrong_pick']
    if wrong_pick:
        print(f"\n  选错标的详情（{len(wrong_pick)} 个窗口）：")
        for a in wrong_pick:
            det = a['details']
            worst = det['worst_held']
            print(f"    窗口{a['window']}({a['date']}) {a['regime']} conf={a['conf']:.2f}")
            print(f"      最差持仓: {worst[0]} 收益={worst[1]:+.2f}% vs 池中位数{det['pool_median']:+.2f}% (落后{det['underperform']:+.2f}%)")
    
    # ── 体制风险详情 ───────────────────────────────────────
    regime_risk = [a for a in attributions if a['cause'] == 'regime_risk']
    if regime_risk:
        print(f"\n  体制风险详情（{len(regime_risk)} 个窗口）：")
        for a in regime_risk:
            det = a['details']
            print(f"    窗口{a['window']}({a['date']}) {det['regime']} conf={a['conf']:.2f}")
            print(f"      非避险持仓: {det['non_hedge_count']} 只 板块: {det['non_hedge_sectors']}")
    
    # ── 优化建议 ──────────────────────────────────────────
    top_cause = max(cause_counts, key=lambda k: cause_counts[k])
    top_count = cause_counts[top_cause]
    top_pct = top_count / total_loss * 100 if total_loss else 0
    
    print(f"\n═══ 方向C-3：诊断结论 ═══")
    suggestion = ""
    if top_cause == 'wrong_pick':
        suggestion = "→ 优化排序公式（引入更多阿尔法因子，或加强动量过滤）"
        priority = "高"
    elif top_cause == 'regime_risk':
        suggestion = "→ 优化体制判定逻辑，或在边界区域引入方向A预警"
        priority = "高"
    elif top_cause == 'systemic':
        suggestion = "→ 引入方向A/B预警机制，在系统性风险前减仓"
        priority = "高"
    else:
        suggestion = "→ 进一步细分样本"
        priority = "中"
    
    print(f"  首要原因: {top_cause} ({top_pct:.0f}% of losses)")
    print(f"  优化优先级: {priority}")
    print(f"  建议: {suggestion}")
    
    # ── 生成报告 ─────────────────────────────────────────
    report = f"""# 方向C报告：失败模式归因

## C-1：亏损窗口概览

- **总活跃窗口**: {len([w for w in ledger if w['avg_return'] != 0])}
- **亏损窗口**: {len(loss_windows)} ({len(loss_windows)/len([w for w in ledger if w['avg_return'] != 0])*100:.1f}%)

| 窗口 | 日期 | 体制 | 置信度 | 窗口收益 | 持仓数 | 归因 |
|------|------|------|--------|---------|--------|------|
"""
    for a in attributions:
        cause_label = {'wrong_pick': '选错标的', 'regime_risk': '体制风险', 'systemic': '系统性崩盘', 'other': '其他'}.get(a['cause'], a['cause'])
        report += f"| {a['window']} | {a['date']} | {a['regime']} | {a['conf']:.2f} | {a['avg_return']:+.2f}% | {a['num_trades']} | {cause_label} |\n"
    
    report += f"""
## C-2：失败原因统计

| 原因 | 次数 | 占比 | 建议 |
|------|------|------|------|
"""
    for cause, count in sorted(cause_counts.items(), key=lambda x: -x[1]):
        pct = count / total_loss * 100 if total_loss else 0
        label = {'wrong_pick': '选错标的', 'regime_risk': '体制风险', 'systemic': '系统性崩盘', 'other': '其他'}.get(cause, cause)
        if cause == 'wrong_pick':
            action = "优化排序公式"
        elif cause == 'regime_risk':
            action = "优化体制判定"
        elif cause == 'systemic':
            action = "方向A/B预警"
        else:
            action = "进一步分析"
        report += f"| {label} | {count} | {pct:.1f}% | {action} |\n"
    
    # 候选池系统性崩盘统计
    report += f"""
## C-3：候选池系统性风险（所有窗口）

| 窗口 | 日期 | 负收益ETF比例 | 候选池中位数收益 |
|------|------|------------|--------------|
"""
    for ps in pool_stats:
        report += f"| {ps['window']} | {ps['date']} | {ps['neg_ratio']*100:.1f}% | {ps['pool_median']:+.2f}% |\n"
    
    avg_neg_ratio = np.mean([ps['neg_ratio'] for ps in pool_stats]) if pool_stats else 0
    report += f"""
**候选池平均负收益比例**: {avg_neg_ratio*100:.1f}%

"""
    
    report += f"""
## C-4：诊断结论

**首要失败原因**: {top_cause}（{top_pct:.0f}% of losses, {top_count} 个窗口）

| 归因类型 | 占比 | 占比阈值 | 优化方向 |
|---------|------|---------|---------|
| 选错标的 | {cause_counts.get('wrong_pick',0)/total_loss*100:.1f}% | > 33% | 优化排序公式 |
| 体制风险 | {cause_counts.get('regime_risk',0)/total_loss*100:.1f}% | > 33% | 优化体制判定 |
| 系统性崩盘 | {cause_counts.get('systemic',0)/total_loss*100:.1f}% | > 33% | 方向A/B预警机制 |

**建议**: {suggestion}

**判断标准**：
- "选错标的" 占比高 → 优化排序公式（引入更多阿尔法因子，或加强动量过滤）
- "体制风险" 占比高 → 优化体制判定或仓位规则
- "系统性崩盘" 占比高 → 方向A/B预警机制（系统性风险前的仓位管理）

---
*生成时间：2026-06-17 | 数据来源：backtest_verified.json + combined_daily.json*
"""
    
    with open(f"{OUT_DIR}/directionC_report.md", 'w') as f:
        f.write(report)
    print(f"\n✅ 报告已保存: {OUT_DIR}/directionC_report.md")
    
    return {
        'cause_counts': cause_counts,
        'top_cause': top_cause,
        'suggestion': suggestion,
        'attributions': attributions,
    }


if __name__ == '__main__':
    main()
