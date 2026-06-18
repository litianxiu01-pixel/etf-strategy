#!/usr/bin/env python3
"""
测试 bear 体制下的防御资产选择
"""
import sys, os
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_DIR = os.path.join(REPO_ROOT, 'core')
sys.path.insert(0, CORE_DIR)

from u7_strategy import detect_regime_at, select_at, U7_CALENDAR_V3_H20_CONFIG, _load
import json

def test_bear_selection():
    _combined, _universe, _dates_all = _load()
    
    config = U7_CALENDAR_V3_H20_CONFIG
    start_idx = next((i for i, d in enumerate(_dates_all) if d >= '2023-06-01'), 120)
    hold = config.get('hold_days', 20)
    rebalance = list(range(start_idx, len(_combined)-hold, hold))
    
    print("=" * 80)
    print("Bear 体制窗口防御资产选择检查")
    print("=" * 80)
    
    bear_windows = []
    for wi, idx in enumerate(rebalance):
        regime_info = detect_regime_at(idx)
        regime = regime_info['regime']
        
        if regime == 'bear':
            picks = select_at(idx, regime, regime_info['conf'], config)
            date = _combined[idx]['date']
            bear_windows.append({
                'window': wi,
                'date': date,
                'picks': picks,
            })
            
            print(f"\n窗口 {wi:2d} | {date} | bear 体制")
            print(f"  选中 {len(picks)} 只防御资产:")
            for p in picks:
                code = p['code']
                name = _universe['etfs'][code]['name']
                sector = p.get('sector', '?')
                print(f"    - {code} {name} (板块: {sector})")
    
    print("\n" + "=" * 80)
    print(f"总计 {len(bear_windows)} 个 bear 体制窗口")
    print("=" * 80)
    
    # 检查是否选到了标普500
    sp500_selected = False
    for w in bear_windows:
        for p in w['picks']:
            if p['code'] == '513500':
                sp500_selected = True
                print(f"\n✅ 标普500 (513500) 在窗口 {w['window']} ({w['date']}) 被选中")
    
    if not sp500_selected:
        print("\n⚠️  标普500 (513500) 未被任何 bear 窗口选中")
    
    return bear_windows

if __name__ == '__main__':
    test_bear_selection()
