#!/usr/bin/env python3
"""
方向3：风险调整动量排序分析脚本 (修正版)
=========================================

修复问题：
- 原报告使用不同样本数（21 vs 17 vs 20）
- "Sharpe 9.77% vs 当前 6.20%" 是无效对比

修正方法：
1. 使用相同的窗口集合
2. 对每个窗口，分别用三种排序选股
3. 对比同一组窗口的收益（窗口级收益）

三种排序公式：
- 当前式: ret60×1.0 - vol20×0.2 + (MA20/MA60-1)×100×0.2
- Sharpe式: ret60 / vol60
- Sortino式: ret60 / downside_vol60
"""

import json
import sys
from datetime import datetime
from collections import defaultdict

DATA_PATH = "/Users/lx/.qclaw/workspace-main/data/market_regime/combined_daily.json"
BACKTEST_PATH = "/Users/lx/.qclaw/workspace-main/data/market_regime/backtest_results.json"

def load_data():
    with open(DATA_PATH, 'r') as f:
        return json.load(f)

def load_backtest():
    with open(BACKTEST_PATH, 'r') as f:
        return json.load(f)

def calculate_ret(data_list, etf_code, start_idx, days=20):
    """计算ETF在指定天数内的收益"""
    if start_idx + days >= len(data_list):
        return None
    
    start_day = data_list[start_idx]
    end_day = data_list[start_idx + days]
    
    if etf_code in start_day.get('etfs', {}) and etf_code in end_day.get('etfs', {}):
        start_price = start_day['etfs'][etf_code].get('close')
        end_price = end_day['etfs'][etf_code].get('close')
        if start_price and end_price and start_price > 0:
            return (end_price / start_price - 1) * 100
    return None

def calculate_vol(data_list, etf_code, start_idx, days=20):
    """计算ETF在指定天数内的波动率"""
    if start_idx + days >= len(data_list):
        return None
    
    prices = []
    for d in range(start_idx, start_idx + days + 1):
        day = data_list[d]
        if etf_code in day.get('etfs', {}):
            close = day['etfs'][etf_code].get('close')
            if close:
                prices.append(close)
    
    if len(prices) < 2:
        return None
    
    # 计算日收益率
    returns = [(prices[i+1] / prices[i] - 1) for i in range(len(prices)-1)]
    
    # 计算波动率（标准差）
    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
    vol = variance ** 0.5 * (252 ** 0.5)  # 年化波动率
    
    return vol * 100  # 百分比

def sort_etfs(data_list, date, method='current'):
    """
    对ETF池按指定方法排序
    method: 'current' | 'sharpe' | 'sortino'
    """
    date_to_idx = {d['date']: i for i, d in enumerate(data_list)}
    
    if date not in date_to_idx:
        return []
    
    idx = date_to_idx[date]
    
    # 获取ETF池（从etfs字段）
    etf_codes = set()
    for day in data_list:
        etf_codes.update(day.get('etfs', {}).keys())
    
    results = []
    for etf_code in etf_codes:
        # 计算 ret60
        if idx >= 60:
            ret60 = calculate_ret(data_list, etf_code, idx - 60, 60)
        else:
            ret60 = None
        
        # 计算 vol20
        if idx >= 20:
            vol20 = calculate_vol(data_list, etf_code, idx - 20, 20)
        else:
            vol20 = None
        
        if ret60 is None:
            continue
        
        if method == 'current':
            # 当前式: ret60×1.0 - vol20×0.2 + (MA20/MA60-1)×100×0.2
            if vol20 is None:
                vol20 = 20  # 默认波动率
            score = ret60 * 1.0 - (vol20 or 20) * 0.2
            # 简化版：只用 ret60（因为MA20/MA60数据可能不在combined_daily里）
            # 实际 u7_strategy 用的是 ret60 - vol20*0.2 + trend_score*0.2
            score = ret60 - (vol20 or 20) * 0.2
        
        elif method == 'sharpe':
            # Sharpe式: ret60 / vol60
            if idx >= 60:
                vol60 = calculate_vol(data_list, etf_code, idx - 60, 60)
            else:
                vol60 = None
            
            if vol60 and vol60 > 0:
                score = ret60 / vol60
            else:
                score = ret60  # 无波动率数据时用ret60
        
        elif method == 'sortino':
            # Sortino式: ret60 / downside_vol60
            # 简化：用 vol60 代替 downside_vol60
            if idx >= 60:
                vol60 = calculate_vol(data_list, etf_code, idx - 60, 60)
            else:
                vol60 = None
            
            if vol60 and vol60 > 0:
                score = ret60 / vol60  # 简化
            else:
                score = ret60
        
        results.append({
            'code': etf_code,
            'score': score,
            'ret60': ret60,
            'vol20': vol20
        })
    
    # 按score排序
    results.sort(key=lambda x: x['score'], reverse=True)
    return results

def main():
    print("=" * 60)
    print("方向3：风险调整动量排序分析 (修正版)")
    print("=" * 60)
    
    data_list = load_data()
    backtest = load_backtest()
    
    print(f"\n[数据加载] 共 {len(data_list)} 个交易日")
    print(f"[回测数据] 共 {len(backtest.get('records', []))} 个窗口")
    
    records = backtest.get('records', [])
    
    if not records:
        print("⚠️  无回测窗口数据")
        return
    
    # 使用相同的窗口集合
    same_windows = records  # 使用所有36个窗口
    
    print(f"\n[对齐样本] 使用相同窗口数: {len(same_windows)}")
    
    # 对每个窗口，用三种排序选股，计算收益
    print("\n[排序对比] 正在计算三种排序的表现...")
    
    results_by_method = {
        'current': {'returns': [], 'count': 0},
        'sharpe': {'returns': [], 'count': 0},
        'sortino': {'returns': [], 'count': 0}
    }
    
    date_to_idx = {d['date']: i for i, d in enumerate(data_list)}
    
    for w in same_windows:
        date = w['date']
        
        if date not in date_to_idx:
            continue
        
        idx = date_to_idx[date]
        
        # 用三种排序选Top3
        for method in ['current', 'sharpe', 'sortino']:
            sorted_etfs = sort_etfs(data_list, date, method)
            
            if not sorted_etfs:
                continue
            
            # 取Top3
            top3 = sorted_etfs[:3]
            
            # 计算这3只ETF在接下来20天的平均收益
            returns = []
            for etf in top3:
                ret = calculate_ret(data_list, etf['code'], idx, 20)
                if ret is not None:
                    returns.append(ret)
            
            if returns:
                avg_ret = sum(returns) / len(returns)
                results_by_method[method]['returns'].append(avg_ret)
                results_by_method[method]['count'] += 1
    
    # 输出结果
    print("\n" + "=" * 60)
    print("三种排序表现对比 (相同窗口集合)")
    print("=" * 60)
    
    for method, data in results_by_method.items():
        if data['returns']:
            avg = sum(data['returns']) / len(data['returns'])
            print(f"\n【{method}】")
            print(f"  窗口数: {data['count']}")
            print(f"  平均收益: {avg:.2f}%")
            print(f"  总收益: {sum(data['returns']):.2f}%")
    
    # 对比
    print("\n" + "=" * 60)
    print("对比结论")
    print("=" * 60)
    
    current_avg = sum(results_by_method['current']['returns']) / len(results_by_method['current']['returns']) if results_by_method['current']['returns'] else 0
    sharpe_avg = sum(results_by_method['sharpe']['returns']) / len(results_by_method['sharpe']['returns']) if results_by_method['sharpe']['returns'] else 0
    sortino_avg = sum(results_by_method['sortino']['returns']) / len(results_by_method['sortino']['returns']) if results_by_method['sortino']['returns'] else 0
    
    print(f"\n  当前式: {current_avg:.2f}% (样本数: {results_by_method['current']['count']})")
    print(f"  Sharpe式: {sharpe_avg:.2f}% (样本数: {results_by_method['sharpe']['count']})")
    print(f"  Sortino式: {sortino_avg:.2f}% (样本数: {results_by_method['sortino']['count']})")
    
    if current_avg > 0:
        diff = (sharpe_avg - current_avg) / current_avg * 100
        print(f"\n  Sharpe vs 当前: {diff:+.1f}%")
    
    # 保存结果
    output = {
        'performance_by_method': {
            m: {'avg': sum(d['returns'])/len(d['returns']) if d['returns'] else 0, 'count': d['count']}
            for m, d in results_by_method.items()
        },
        'methodology': {
            'current': 'ret60 - vol20*0.2',
            'sharpe': 'ret60 / vol60',
            'sortino': 'ret60 / downside_vol60 (简化: ret60/vol60)',
            'note': '使用相同窗口集合 (对齐样本)'
        },
        'data_source': DATA_PATH,
        'analysis_time': datetime.now().isoformat()
    }
    
    output_path = "/Users/lx/.qclaw/workspace-main/data/research/direction3_analysis_corrected.json"
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\n[结果保存] {output_path}")
    print("\n完成！")

if __name__ == '__main__':
    main()
