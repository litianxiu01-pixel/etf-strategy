#!/usr/bin/env python3
"""
调试 bear 体制下为什么某些窗口选中 0 只资产
"""
import sys
sys.path.insert(0, '/Users/lx/.qclaw/workspace-main/scripts')

from u7_strategy import detect_regime_at, select_at, U7_CALENDAR_V3_H20_CONFIG, _load, get_closes, etf_sector
import numpy as np

def debug_bear_window(idx):
    """调试单个 bear 窗口"""
    _combined, _universe, _dates_all = _load()
    config = U7_CALENDAR_V3_H20_CONFIG
    
    date = _combined[idx]['date']
    print(f"\n{'='*80}")
    print(f"调试窗口 @ {date} (idx={idx})")
    print(f"{'='*80}")
    
    # 检查所有防御资产
    from u7_strategy import DEF_GOLD, DEF_BOND
    defensive_codes = list(DEF_GOLD) + list(DEF_BOND) + ['513500']
    print(f"\n防御资产列表 ({len(defensive_codes)} 只):")
    for code in defensive_codes:
        info = _universe['etfs'].get(code, {})
        if not info:
            print(f"  {code} ❌ 不在 etf_universe.json 中")
            continue
        name = info.get('name', '?')
        ac = info.get('assetClass', '?')
        status = info.get('status', '?')
        print(f"  {code} {name} (assetClass={ac}, status={status})")
    
    print(f"\nMA20/MA60 检查:")
    ma_passed = []
    ma_failed = []
    no_data = []
    
    for code in defensive_codes:
        closes = get_closes(code, idx, 120)
        if len(closes) < 60:
            no_data.append((code, len(closes)))
            continue
        
        ma20 = np.mean(closes[-20:])
        ma60 = np.mean(closes[-60:])
        ratio = ma20 / ma60
        
        if ma20 > ma60:
            ma_passed.append((code, ma20, ma60, ratio))
        else:
            ma_failed.append((code, ma20, ma60, ratio))
    
    if no_data:
        print(f"\n  数据不足 ({len(no_data)} 只):")
        for code, n in no_data:
            print(f"    {code}: 只有 {n} 天数据")
    
    if ma_passed:
        print(f"\n  ✅ 通过 MA20 > MA60 ({len(ma_passed)} 只):")
        for code, ma20, ma60, ratio in ma_passed:
            name = _universe['etfs'][code]['name']
            print(f"    {code} {name}: MA20={ma20:.2f}, MA60={ma60:.2f}, 比率={ratio:.4f}")
    
    if ma_failed:
        print(f"\n  ⚠️  未通过 MA20 > MA60 ({len(ma_failed)} 只):")
        for code, ma20, ma60, ratio in sorted(ma_failed, key=lambda x: x[3], reverse=True):
            name = _universe['etfs'][code]['name']
            print(f"    {code} {name}: MA20={ma20:.2f}, MA60={ma60:.2f}, 比率={ratio:.4f} (最接近金叉)")
    
    # 检查 select_at 返回的结果
    regime_info = detect_regime_at(idx)
    picks = select_at(idx, 'bear', regime_info['conf'], config)
    
    print(f"\nselect_at 返回结果: {len(picks)} 只")
    for p in picks:
        print(f"  {p['code']} {_universe['etfs'][p['code']]['name']}")
    
    return ma_passed, ma_failed, no_data

def main():
    _combined, _universe, _dates_all = _load()
    config = U7_CALENDAR_V3_H20_CONFIG
    start_idx = next((i for i, d in enumerate(_dates_all) if d >= '2023-06-01'), 120)
    hold = config.get('hold_days', 20)
    rebalance = list(range(start_idx, len(_combined)-hold, hold))
    
    # 找到第一个选中 0 只资产的 bear 窗口
    for wi, idx in enumerate(rebalance):
        regime_info = detect_regime_at(idx)
        if regime_info['regime'] == 'bear':
            picks = select_at(idx, 'bear', regime_info['conf'], config)
            if len(picks) == 0:
                print(f"\n找到空窗口: 窗口 {wi}, {_combined[idx]['date']}")
                debug_bear_window(idx)
                break

if __name__ == '__main__':
    main()
