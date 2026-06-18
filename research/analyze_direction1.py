#!/usr/bin/env python3
"""
方向1：体制转换预警信号分析脚本 (修正版v2)
=============================================

数据格式：combined_daily.json 是 list[dict]，每个dict有 'date' 和 'etfs' 字段
etfs: {etf_code: {date, open, close, high, low, volume}}
"""

import json
import sys
from datetime import datetime, timedelta
from collections import defaultdict

DATA_PATH = "/Users/lx/.qclaw/workspace-main/data/market_regime/combined_daily.json"

def load_data():
    with open(DATA_PATH, 'r') as f:
        return json.load(f)

def calculate_ma(prices, window):
    if len(prices) < window:
        return None
    return sum(prices[-window:]) / window

def compute_market_breadth(data_list, current_idx, lookback=60):
    """
    计算市场宽度指标
    data_list: list of daily records
    current_idx: 当前记录在 data_list 中的索引
    """
    # 收集当前及历史价格
    etf_prices = defaultdict(list)
    etf_volumes = defaultdict(list)
    
    start_idx = max(0, current_idx - lookback)
    for i in range(start_idx, current_idx + 1):
        day = data_list[i]
        date = day['date']
        for etf_code, etf_info in day.get('etfs', {}).items():
            if 'close' in etf_info:
                etf_prices[etf_code].append(etf_info['close'])
                if 'volume' in etf_info:
                    etf_volumes[etf_code].append(etf_info['volume'])
    
    total_etf = 0
    ma20_above = 0
    ma60_above = 0
    momentum_values = []
    
    for etf_code, prices in etf_prices.items():
        if len(prices) < 20:
            continue
        total_etf += 1
        current_price = prices[-1]
        
        ma20 = calculate_ma(prices, 20)
        if ma20 is not None and current_price > ma20:
            ma20_above += 1
        
        if len(prices) >= 60:
            ma60 = calculate_ma(prices, 60)
            if ma60 is not None and current_price > ma60:
                ma60_above += 1
        
        # 20日动量
        if len(prices) >= 20:
            ret20 = (prices[-1] / prices[-20] - 1) * 100
            momentum_values.append(ret20)
    
    ma20_ratio = (ma20_above / total_etf * 100) if total_etf > 0 else 50
    ma60_ratio = (ma60_above / total_etf * 100) if total_etf > 0 else 50
    
    momentum_values.sort(reverse=True)
    top5_mom = sum(momentum_values[:5]) / 5 if len(momentum_values) >= 5 else 0
    
    return {
        'ma20_ratio': ma20_ratio,
        'ma60_ratio': ma60_ratio,
        'top5_momentum_avg': top5_mom,
        'total_etf': total_etf
    }

def load_regime_labels(data_list):
    """
    从 u7_strategy.py 的逻辑重新计算体制标签
    或者从现有的 analysis json 文件加载
    """
    # 先尝试从 direction1_analysis.json 加载已有的体制标签
    import os
    regime_path = "/Users/lx/.qclaw/workspace-main/data/research/direction1_analysis.json"
    if os.path.exists(regime_path):
        with open(regime_path, 'r') as f:
            regime_data = json.load(f)
        # 提取 regime_evolution 里的转换点
        transitions = []
        if 'regime_evolution' in regime_data:
            for evo in regime_data['regime_evolution']:
                transitions.append({
                    'date': evo['transition_date'],
                    'from_regime': evo['from_regime'],
                    'to_regime': evo['to_regime']
                })
        return transitions
    return []

def main():
    print("=" * 60)
    print("方向1：体制转换预警信号分析 (修正版v2)")
    print("=" * 60)
    
    data_list = load_data()
    print(f"\n[数据加载] 共 {len(data_list)} 个交易日")
    
    # 加载体制转换点（来自之前的分析）
    transitions = load_regime_labels(data_list)
    print(f"[体制转换] 共 {len(transitions)} 个转换点")
    
    if not transitions:
        print("⚠️  未找到体制转换点，请先运行 u7_strategy 分析")
        return
    
    # 建立日期索引
    date_to_idx = {d['date']: i for i, d in enumerate(data_list)}
    transition_dates = set(t['date'] for t in transitions)
    
    # 计算每日信号
    print("\n[信号计算] 正在计算每日市场宽度信号...")
    
    signals = {
        'ma20_decline': {},
        'momentum_decay': {},
        'ma60_decline': {},
        'ma20+momentum': {},
        'ma20+ma60': {}
    }
    
    # 只计算有体制标签的日期范围内的信号
    all_dates = [d['date'] for d in data_list]
    # 找到转换点对应的索引范围
    if transitions:
        first_date = min(t['date'] for t in transitions)
        last_date = max(t['date'] for t in transitions)
        if first_date in date_to_idx and last_date in date_to_idx:
            start_idx = max(0, date_to_idx[first_date] - 30)
            end_idx = min(len(data_list), date_to_idx[last_date] + 5)
        else:
            start_idx = 0
            end_idx = len(data_list)
    else:
        start_idx = 0
        end_idx = len(data_list)
    
    for i in range(start_idx, end_idx):
        date = data_list[i]['date']
        breadth = compute_market_breadth(data_list, i)
        
        ma20_sig = breadth['ma20_ratio'] < 60
        mom_sig = breadth['top5_momentum_avg'] < 3.0
        ma60_sig = breadth['ma60_ratio'] < 55
        
        signals['ma20_decline'][date] = ma20_sig
        signals['momentum_decay'][date] = mom_sig
        signals['ma60_decline'][date] = ma60_sig
        signals['ma20+momentum'][date] = ma20_sig and mom_sig
        signals['ma20+ma60'][date] = ma20_sig and ma60_sig
    
    # 评估信号
    print("\n[信号评估] 评估各信号在体制转换前 N 天的触发情况...")
    
    LEAD_WINDOW = 5  # 提前N天
    
    results = {}
    for sig_name, sig_dict in signals.items():
        lead_times = []
        triggered = 0
        
        for t in transitions:
            t_date = t['date']
            if t_date not in date_to_idx:
                continue
            t_idx = date_to_idx[t_date]
            
            found = False
            lead_time = None
            for d in range(max(0, t_idx - LEAD_WINDOW), t_idx):
                check_date = all_dates[d]
                if sig_dict.get(check_date, False):
                    found = True
                    lead_time = t_idx - d
                    break
            
            if found:
                triggered += 1
                lead_times.append(lead_time)
        
        total = len(transitions)
        accuracy = (triggered / total * 100) if total > 0 else 0
        
        result = {
            'triggered': triggered,
            'total': total,
            'accuracy': round(accuracy, 2),
            'lead_times': lead_times
        }
        if lead_times:
            result['avg_lead_time'] = round(sum(lead_times)/len(lead_times), 2)
            result['min_lead_time'] = min(lead_times)
            result['max_lead_time'] = max(lead_times)
        else:
            result['avg_lead_time'] = 0
            result['min_lead_time'] = 0
            result['max_lead_time'] = 0
        
        results[sig_name] = result
    
    # 输出结果
    print("\n" + "=" * 60)
    print("信号评估结果 (提前窗口: {}天)".format(LEAD_WINDOW))
    print("=" * 60)
    
    for name, res in results.items():
        print(f"\n【{name}】")
        print(f"  触发: {res['triggered']}/{res['total']}  准确率: {res['accuracy']}%")
        if res['lead_times']:
            print(f"  提前量: 平均{res['avg_lead_time']}天 (范围{res['min_lead_time']}-{res['max_lead_time']}天)")
        else:
            print(f"  提前量: N/A (未触发)")
    
    # 逻辑一致性检查
    print("\n" + "=" * 60)
    print("逻辑一致性检查")
    print("=" * 60)
    
    mom = results['momentum_decay']
    combo = results['ma20+momentum']
    
    if mom['triggered'] == 0 and combo['triggered'] > 0:
        print("\n⚠️  逻辑矛盾确认！")
        print(f"  momentum_decay 单信号: 触发 {mom['triggered']}/{mom['total']}")
        print(f"  ma20+momentum 组合: 触发 {combo['triggered']}/{combo['total']}")
        print("\n  原因: 组合信号中的 'momentum' 与单信号 'momentum_decay' 定义不同")
        print("  单信号: top5_momentum_avg < 3.0 (20日动量)")
        print("  组合信号 (推测): 可能是其他定义，如 vol_s 或 breadth 的某种组合")
        print("\n  ⚡ 修复建议:")
        print("    1. 在报告中明确组合信号中 'momentum' 的精确定义")
        print("    2. 如果无法澄清，删除该组合信号的结果")
        print("    3. 或者重新定义 momentum 信号，使其与组合信号一致")
    elif mom['triggered'] > 0 and combo['triggered'] > 0:
        print("\n✅ 逻辑一致: 单信号有触发，组合信号也有触发 (AND 逻辑有效)")
    elif mom['triggered'] == 0 and combo['triggered'] == 0:
        print("\n✅ 逻辑一致: 两个信号都未触发")
    else:
        print(f"\n? 未预期的情况: mom={mom['triggered']}, combo={combo['triggered']}")
    
    # 保存结果
    output = {
        'signal_results': results,
        'transitions': transitions,
        'lead_window': LEAD_WINDOW,
        'methodology': {
            'ma20_decline': 'ma20_ratio < 60 (站上MA20的ETF比例低于60%)',
            'momentum_decay': 'top5_momentum_avg < 3.0 (前5强ETF的20日动量均值低于3%)',
            'ma60_decline': 'ma60_ratio < 55 (站上MA60的ETF比例低于55%)',
            'ma20+momentum': 'ma20_ratio < 60 AND top5_momentum_avg < 3.0 (AND)',
            'note': '如果 momentum_decay 触发次数=0 但 ma20+momentum>0，说明定义不一致'
        },
        'data_source': DATA_PATH,
        'analysis_time': datetime.now().isoformat()
    }
    
    output_path = "/Users/lx/.qclaw/workspace-main/data/research/direction1_analysis_corrected.json"
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\n[结果保存] {output_path}")
    print("\n完成！")

if __name__ == '__main__':
    main()
