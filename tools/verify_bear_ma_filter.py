#!/usr/bin/env python3
"""
验证 bear 体制防御资产 MA20 过滤器和 fallback 逻辑
"""
import sys
sys.path.insert(0, '/Users/lx/.qclaw/workspace-main/scripts')

from u7_strategy import detect_regime_at, select_at, U7_CALENDAR_V3_H20_CONFIG, _load, get_closes
import numpy as np

def verify_bear_ma_filter():
    """验证 bear 体制下的 MA20 > MA60 过滤器"""
    _combined, _universe, _dates_all = _load()
    config = U7_CALENDAR_V3_H20_CONFIG
    
    start_idx = next((i for i, d in enumerate(_dates_all) if d >= '2023-06-01'), 120)
    hold = config.get('hold_days', 20)
    rebalance = list(range(start_idx, len(_combined)-hold, hold))
    
    print("=" * 80)
    print("验证 Bear 体制 MA20 > MA60 过滤器")
    print("=" * 80)
    
    fallback_triggered = []
    ma_filter_passed = []
    empty_windows = []
    
    for wi, idx in enumerate(rebalance):
        regime_info = detect_regime_at(idx)
        if regime_info['regime'] != 'bear':
            continue
        
        date = _combined[idx]['date']
        picks = select_at(idx, 'bear', regime_info['conf'], config)
        
        # 检查防御资产的 MA20 和 MA60
        defensive_codes = ['518880', '511010', '511180', '513500']
        
        ma_status = []
        for code in defensive_codes:
            closes = get_closes(code, idx, 120)
            if len(closes) >= 60:
                ma20 = np.mean(closes[-20:])
                ma60 = np.mean(closes[-60:])
                ma_status.append({
                    'code': code,
                    'name': _universe['etfs'].get(code, {}).get('name', '?'),
                    'ma20': ma20,
                    'ma60': ma60,
                    'ma20>ma60': ma20 > ma60,
                    'ratio': ma20 / ma60,
                })
        
        if len(picks) == 0:
            empty_windows.append((wi, date, ma_status))
        elif len(picks) == 1 and len(ma_status) > 0:
            # 检查是否触发了 fallback（只有1只资产被选中，而且它的 MA20 <= MA60）
            picked_code = picks[0]['code']
            picked_status = [s for s in ma_status if s['code'] == picked_code]
            if picked_status and not picked_status[0]['ma20>ma60']:
                fallback_triggered.append((wi, date, picked_code, picked_status[0]['ratio']))
            else:
                ma_filter_passed.append((wi, date, len(picks)))
        else:
            ma_filter_passed.append((wi, date, len(picks)))
    
    print(f"\n✅ MA 过滤通过的窗口: {len(ma_filter_passed)}")
    for wi, date, n in ma_filter_passed[:5]:
        print(f"  窗口 {wi:2d} | {date} | 选中 {n} 只")
    
    print(f"\n🔄 Fallback 触发的窗口: {len(fallback_triggered)}")
    for wi, date, code, ratio in fallback_triggered:
        name = _universe['etfs'].get(code, {}).get('name', '?')
        print(f"  窗口 {wi:2d} | {date} | Fallback 到 {code} {name} (MA比率={ratio:.4f})")
    
    print(f"\n⚠️  空仓窗口 (防御资产数据不足): {len(empty_windows)}")
    for wi, date, ma_status in empty_windows[:5]:
        print(f"  窗口 {wi:2d} | {date}")
        if not ma_status:
            print(f"    (所有防御资产数据不足)")
    
    print("\n" + "=" * 80)
    print("验证结论:")
    print(f"  - MA 过滤正常工作的窗口: {len(ma_filter_passed)}")
    print(f"  - Fallback 触发的窗口: {len(fallback_triggered)}")
    print(f"  - 空仓窗口 (数据不足): {len(empty_windows)}")
    print("=" * 80)
    
    return {
        'ma_filter_passed': ma_filter_passed,
        'fallback_triggered': fallback_triggered,
        'empty_windows': empty_windows,
    }

if __name__ == '__main__':
    verify_bear_ma_filter()
