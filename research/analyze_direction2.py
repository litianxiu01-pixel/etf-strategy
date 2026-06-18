#!/usr/bin/env python3
"""
方向2：动量信号衰减曲线分析脚本 (修正版)
===========================================

修复问题：
- direction2_accurate.json 显示半衰期20天（可能是默认值/旧数据）
- direction2_analysis_v2.json 显示半衰期1天（需要验证）

本脚本：
1. 从 combined_daily.json 重新计算衰减曲线
2. 计算真实的半衰期
3. 明确半衰期计算公式

半衰期定义：
  信号触发后，累积收益衰减到峰值50%所需的天数
  公式：找到最小的 t，使得 return(t) <= peak_return * 0.5
"""

import json
import sys
from datetime import datetime
from collections import defaultdict

DATA_PATH = "/Users/lx/.qclaw/workspace-main/data/market_regime/combined_daily.json"

def load_data():
    with open(DATA_PATH, 'r') as f:
        return json.load(f)

def calculate_decay_curve(data_list, start_date, hold_days=20):
    """
    计算从 start_date 开始，持有 hold_days 天的累积收益曲线
    返回: list[(day, cumulative_return)]
    """
    date_to_idx = {d['date']: i for i, d in enumerate(data_list)}
    
    if start_date not in date_to_idx:
        return None
    
    start_idx = date_to_idx[start_date]
    etf_start_prices = {}
    
    # 获取起始日价格
    start_day = data_list[start_idx]
    for etf_code, etf_info in start_day.get('etfs', {}).items():
        if 'close' in etf_info:
            etf_start_prices[etf_code] = etf_info['close']
    
    # 计算持有期内每日的累积收益（等权重组合）
    curve = []
    for d in range(1, hold_days + 1):
        if start_idx + d >= len(data_list):
            break
        
        day = data_list[start_idx + d]
        day_returns = []
        
        for etf_code, start_price in etf_start_prices.items():
            if etf_code in day.get('etfs', {}):
                etf_day = day['etfs'][etf_code]
                if 'close' in etf_day:
                    ret = (etf_day['close'] / start_price - 1) * 100
                    day_returns.append(ret)
        
        if day_returns:
            avg_ret = sum(day_returns) / len(day_returns)
            curve.append((d, avg_ret))
    
    return curve

def calculate_half_life(curve):
    """
    计算半衰期：累积收益衰减到峰值50%所需天数
    curve: list[(day, return)]
    
    返回: 半衰期（天），如果20天内未衰减到50%，返回20
    """
    if not curve:
        return None
    
    # 找到峰值
    peak_day, peak_ret = max(curve, key=lambda x: x[1])
    
    if peak_ret <= 0:
        return 1  # 峰值就是负的，半衰期1天
    
    half_threshold = peak_ret * 0.5
    
    # 找峰值之后的衰减
    for day, ret in curve:
        if day > peak_day and ret <= half_threshold:
            return day - peak_day
    
    # 20天内未衰减到50%
    return 20

def calculate_decay_curve_for_window(data_list, start_date, selected_etfs, hold_days=20):
    """计算指定窗口（使用选中ETF）的衰减曲线"""
    date_to_idx = {d['date']: i for i, d in enumerate(data_list)}
    
    if start_date not in date_to_idx:
        return None
    
    start_idx = date_to_idx[start_date]
    
    # 获取起始日选中ETF的价格
    start_prices = {}
    start_day = data_list[start_idx]
    for etf in selected_etfs:
        etf_str = str(etf)
        if etf_str in start_day.get('etfs', {}):
            info = start_day['etfs'][etf_str]
            if 'close' in info:
                start_prices[etf_str] = info['close']
    
    if not start_prices:
        return None
    
    # 计算每日累积收益
    curve = []
    for d in range(1, hold_days + 1):
        if start_idx + d >= len(data_list):
            break
        
        day = data_list[start_idx + d]
        day_returns = []
        
        for etf_str, start_price in start_prices.items():
            if etf_str in day.get('etfs', {}):
                info = day['etfs'][etf_str]
                if 'close' in info:
                    ret = (info['close'] / start_price - 1) * 100
                    day_returns.append(ret)
        
        if day_returns:
            avg_ret = sum(day_returns) / len(day_returns)
            curve.append((d, avg_ret))
    
    return curve


def main():
    print("=" * 60)
    print("方向2：动量信号衰减曲线分析 (修正版)")
    print("=" * 60)
    
    data_list = load_data()
    print(f"\n[数据加载] 共 {len(data_list)} 个交易日")
    
    # 加载已有的回测窗口（从 backtest_results.json）
    import os
    backtest_path = "/Users/lx/.qclaw/workspace-main/data/market_regime/backtest_results.json"
    
    if not os.path.exists(backtest_path):
        print(f"⚠️  回测结果文件不存在: {backtest_path}")
        print("请先运行 u7_strategy.py 的回测")
        return
    
    with open(backtest_path, 'r') as f:
        backtest = json.load(f)
    
    # 检查实际字段名
    if 'records' in backtest:
        windows = backtest['records']
    elif 'windows' in backtest:
        windows = backtest['windows']
    else:
        # 尝试找到包含窗口数据的字段
        for k, v in backtest.items():
            if isinstance(v, list) and v and isinstance(v[0], dict) and 'start_date' in v[0]:
                windows = v
                break
        else:
            windows = []
    
    print(f"[回测窗口] 共 {len(windows)} 个窗口, 数据源字段: {list(backtest.keys())}")
    
    # 按体制分层，计算衰减曲线
    regime_curves = {
        'bull': defaultdict(list),
        'neutral': defaultdict(list),
        'bear': defaultdict(list)
    }
    
    print("\n[衰减曲线] 正在计算每个窗口的衰减曲线...")
    
    for w in windows:
        start_date = w.get('date')  # 注意：字段名是 'date' 不是 'start_date'
        regime = w.get('regime', 'neutral')  # 小写
        picks = w.get('picks', [])  # 字段名是 'picks' 不是 'selected_etfs'
        selected = [p['code'] for p in picks if 'code' in p]
        
        if not start_date or not selected:
            continue
        
        # 计算这个窗口的衰减曲线（使用选中ETF的平均收益）
        curve = calculate_decay_curve_for_window(data_list, start_date, selected)
        
        if curve:
            for day, ret in curve:
                regime_curves[regime][day].append(ret)
    
    # 计算每个体制的平均衰减曲线
    print("\n[平均曲线] 计算每个体制的平均衰减曲线...")
    
    avg_curves = {}
    half_lives = {}
    
    for regime in ['bull', 'neutral', 'bear']:
        daily_returns = regime_curves[regime]
        
        if not daily_returns:
            continue
        
        curve = []
        for day in sorted(daily_returns.keys()):
            vals = daily_returns[day]
            avg = sum(vals) / len(vals)
            curve.append((day, avg))
        
        avg_curves[regime] = curve
        
        # 计算半衰期
        half_life = calculate_half_life(curve)
        half_lives[regime] = half_life
        
        print(f"  {regime}: 峰值={max(curve, key=lambda x:x[1])[1]:.2f}%, 半衰期={half_life}天")
    
    # 输出结果
    print("\n" + "=" * 60)
    print("半衰期结果")
    print("=" * 60)
    
    for regime, hl in half_lives.items():
        print(f"  {regime}: {hl}天")
    
    # 与现有文件对比
    print("\n" + "=" * 60)
    print("与现有文件对比")
    print("=" * 60)
    
    import os
    v2_path = "/Users/lx/.qclaw/workspace-main/data/research/direction2_analysis_v2.json"
    acc_path = "/Users/lx/.qclaw/workspace-main/data/research/direction2_accurate.json"
    
    if os.path.exists(v2_path):
        with open(v2_path, 'r') as f:
            v2 = json.load(f)
        v2_hl = v2.get('regime_half_lives', {})
        print(f"\n  direction2_analysis_v2.json: {v2_hl}")
    
    if os.path.exists(acc_path):
        with open(acc_path, 'r') as f:
            acc = json.load(f)
        acc_hl = acc.get('regime_half_lives', {})
        print(f"  direction2_accurate.json: {acc_hl}")
    
    print(f"\n  重新计算结果: {half_lives}")
    
    # 判断哪个是正确的
    print("\n  => 建议: 以重新计算的结果为准")
    print(f"     bull半衰期: {half_lives.get('bull', 'N/A')}天")
    print(f"     neutral半衰期: {half_lives.get('neutral', 'N/A')}天")
    print(f"     bear半衰期: {half_lives.get('bear', 'N/A')}天")
    
    # 保存结果
    output = {
        'regime_curves': {k: [[d, r] for d, r in v] for k, v in avg_curves.items()},
        'regime_half_lives': half_lives,
        'methodology': {
            'half_life_definition': '信号触发后，累积收益衰减到峰值50%所需天数',
            'peak_definition': '持有期内的最大累积收益',
            'half_threshold': 'peak_return * 0.5',
            'note': '如果20天内未衰减到50%，返回20（未衰减）'
        },
        'data_source': DATA_PATH,
        'analysis_time': datetime.now().isoformat()
    }
    
    output_path = "/Users/lx/.qclaw/workspace-main/data/research/direction2_analysis_corrected.json"
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\n[结果保存] {output_path}")
    print("\n完成！")

def calculate_decay_curve_for_window(data_list, start_date, selected_etfs, hold_days=20):
    """计算指定窗口（使用选中ETF）的衰减曲线"""
    date_to_idx = {d['date']: i for i, d in enumerate(data_list)}
    
    if start_date not in date_to_idx:
        return None
    
    start_idx = date_to_idx[start_date]
    
    # 获取起始日选中ETF的价格
    start_prices = {}
    start_day = data_list[start_idx]
    for etf in selected_etfs:
        etf_str = str(etf)
        if etf_str in start_day.get('etfs', {}):
            info = start_day['etfs'][etf_str]
            if 'close' in info:
                start_prices[etf_str] = info['close']
    
    if not start_prices:
        return None
    
    # 计算每日累积收益
    curve = []
    for d in range(1, hold_days + 1):
        if start_idx + d >= len(data_list):
            break
        
        day = data_list[start_idx + d]
        day_returns = []
        
        for etf_str, start_price in start_prices.items():
            if etf_str in day.get('etfs', {}):
                info = day['etfs'][etf_str]
                if 'close' in info:
                    ret = (info['close'] / start_price - 1) * 100
                    day_returns.append(ret)
        
        if day_returns:
            avg_ret = sum(day_returns) / len(day_returns)
            curve.append((d, avg_ret))
    
    return curve

if __name__ == '__main__':
    main()
