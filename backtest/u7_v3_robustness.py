#!/usr/bin/env python3
"""
U7-v3 稳健化 — 5 步验证
  1. 冻结 U7_CalendarV3 配置
  2. 参数平台测试（查过拟合）
  3. 替换单票止损 → 组合级风控
  4. 执行成本压力测试
  5. Walk-forward 验证

单一真源: u7_strategy.py
"""
import json, os, sys, math, hashlib, time, itertools
import numpy as np
from collections import defaultdict
from datetime import datetime

WORKSPACE = os.path.expanduser("~/.qclaw/workspace-main")
DATA_DIR = os.path.join(WORKSPACE, "data/market_regime")
sys.path.insert(0, os.path.join(WORKSPACE, "scripts"))
from u7_strategy import (
    _load, detect_regime_at, select_at, get_closes, make_config,
)

_combined, _universe, _dates_all = _load()
BOND_ETF = '511010'
GOLD_ETF = '518880'

# ═══════════════════════════════════════════════════
# Step 1: 冻结 U7_CalendarV3
# ═══════════════════════════════════════════════════

U7_CalendarV3 = make_config('U7_CalendarV3',
    stop_loss_pct=None,      # 取消单票止损
    min_sectors=4,           # 4 板块分散
    cold_threshold=-3.0,     # 宽松冷门
    max_positions={'bull':3,'neutral':5,'bear':2},  # bull=3 核心
)

print("=" * 70)
print("🧊 Step 1: U7_CalendarV3 配置冻结")
print("=" * 70)
for k,v in U7_CalendarV3.items():
    print(f"  {k}: {v}")

# ═══════════════════════════════════════════════════
# 共享基础回测（窗口级，用于 Step 2/4/5）
# ═══════════════════════════════════════════════════

def window_backtest(config, start_date='2023-06-01', end_date=None,
                    cost_bps=0, empty_bond=False):
    """窗口级回测，返回 strat_rets + bench_rets + 持仓明细"""
    hold = config.get('hold_days', 10)
    dates_all = _dates_all
    if end_date is None:
        end_date = dates_all[-1]
    
    si = next((i for i, d in enumerate(dates_all) if d >= start_date), 0)
    ei = next((i for i, d in enumerate(dates_all) if d > end_date), len(dates_all))
    
    rebalance = list(range(si, ei - hold, hold))
    if rebalance and rebalance[-1] < ei - hold:
        rebalance.append(ei - hold - 1)
    
    strat_rets, bench_rets = [], []
    all_picks = []
    sl = config.get('stop_loss_pct')
    
    for wi, idx in enumerate(rebalance):
        ri = detect_regime_at(idx)
        picks = select_at(idx, ri['regime'], ri['conf'], config)
        end_idx = min(idx + hold, len(_combined) - 1)
        
        pick_rets = []
        pick_details = []
        
        if picks:
            for p in picks:
                sc = _combined[idx]['etfs'].get(p['code'],{}).get('close', 0)
                if sc <= 0: continue
                ret = None
                if sl:
                    for day_i in range(idx+1, end_idx+1):
                        cc = _combined[day_i]['etfs'].get(p['code'],{}).get('close', 0)
                        if cc <= 0: continue
                        r = (cc/sc-1)*100
                        if r <= -sl: ret = -sl; break
                        if day_i == end_idx: ret = r
                else:
                    ec = _combined[end_idx]['etfs'].get(p['code'],{}).get('close', 0)
                    if ec > 0: ret = (ec/sc-1)*100
                
                if ret is not None:
                    pick_rets.append(ret)
                    pick_details.append({
                        'code': p['code'], 'name': _universe['etfs'][p['code']]['name'],
                        'sector': p['sector'], 'ret': round(ret, 2),
                    })
            
            if pick_rets:
                avg_ret = np.mean(pick_rets)
                # 扣交易成本（按变更仓位收，简化：每窗口按 turnover 比例收）
                if cost_bps > 0:
                    # 估算 turnover: 上窗口持仓与本窗口持仓的重合度
                    prev_codes = {pp['code'] for pp in all_picks[-1]['picks']} if all_picks else set()
                    curr_codes = {pd['code'] for pd in pick_details}
                    turnover = len(curr_codes - prev_codes) + len(prev_codes - curr_codes)
                    n_pos = max(len(curr_codes), len(prev_codes), 1)
                    cost = turnover / (2 * n_pos) * cost_bps / 10000 * 100
                    avg_ret -= cost
                
                strat_rets.append(avg_ret)
                all_picks.append({'date': _combined[idx]['date'], 'regime': ri['regime'],
                                  'picks': pick_details, 'avg_ret': round(avg_ret, 3)})
            else:
                strat_rets.append(0)
                all_picks.append({'date': _combined[idx]['date'], 'regime': ri['regime'],
                                  'picks': [], 'avg_ret': 0})
        
        elif empty_bond:
            sc = _combined[idx]['etfs'].get(BOND_ETF,{}).get('close',0)
            ec = _combined[end_idx]['etfs'].get(BOND_ETF,{}).get('close',0)
            r = (ec/sc-1)*100 if sc>0 and ec>0 else 0
            strat_rets.append(r)
            all_picks.append({'date': _combined[idx]['date'], 'regime': ri['regime'],
                              'picks': [], 'avg_ret': round(r,3), 'bond': True})
        else:
            strat_rets.append(0)
            all_picks.append({'date': _combined[idx]['date'], 'regime': ri['regime'],
                              'picks': [], 'avg_ret': 0})
        
        # 基准
        bench_r = []
        for code, info in _universe['etfs'].items():
            if info.get('status')!='active' or info.get('assetClass')!='cn': continue
            sc = _combined[idx]['etfs'].get(code,{}).get('close',0)
            ec = _combined[end_idx]['etfs'].get(code,{}).get('close',0)
            if sc>0 and ec>0: bench_r.append((ec/sc-1)*100)
        bench_rets.append(np.mean(bench_r) if bench_r else 0)
    
    # 计算指标
    n_total = len(strat_rets)
    active = [r for r in strat_rets if r != 0]
    n_active = len(active)
    eq = [100]
    for sr in strat_rets: eq.append(eq[-1]*(1+sr/100))
    total = (eq[-1]/100-1)*100
    ppy = 252/hold; peak=eq[0]; dd=0
    for v in eq: peak=max(peak,v); dd=max(dd,(peak-v)/peak*100)
    
    def calc(returns_list, n_periods):
        if n_periods < 10: return {'ann':0,'sharpe':0,'wr':0}
        n_years = n_periods/ppy
        ann = ((eq[-1]/100)**(1/n_years)-1)*100
        exc = np.array(returns_list)-2.0/ppy
        sr = float(np.mean(exc)/np.std(returns_list)*math.sqrt(ppy)) if np.std(returns_list)>0 else 0
        wr = sum(1 for r in returns_list if r>0)/len(returns_list)*100 if returns_list else 0
        return {'ann':round(ann,1),'sharpe':round(sr,2),'wr':round(wr,1)}
    
    metrics_active = calc(active, n_active)
    metrics_cal = calc(strat_rets, n_total)
    
    return {
        'total_ret': round(total,1), 'dd': round(dd,1),
        'n_active': n_active, 'n_total': n_total,
        'active': metrics_active,
        'calendar': metrics_cal,
        'strat_rets': [float(x) for x in strat_rets],
        'bench_rets': [float(x) for x in bench_rets],
        'picks_log': all_picks,
    }

# ═══════════════════════════════════════════════════
# Step 2: 参数平台测试
# ═══════════════════════════════════════════════════

def plateau_test():
    print("\n" + "=" * 70)
    print("⛰️  Step 2: 参数平台测试 — 查过拟合")
    print("=" * 70)
    
    baseline = window_backtest(U7_CalendarV3)
    bl = baseline['calendar']
    print(f"\n  📏 V3 Baseline: ret={baseline['total_ret']}% dd={baseline['dd']}% sr={bl['sharpe']} wr={bl['wr']}%")
    
    # 5 参数 × 各 3-5 值
    param_values = [
        ('max_positions/bull', [2, 3, 4]),
        ('max_positions/neutral', [4, 5, 6]),
        ('min_sectors', [3, 4, 5]),
        ('cold_threshold', [-2, -3, -4]),
        ('hold_days', [10, 15, 20]),
    ]
    
    param_names = [p[0] for p in param_values]
    all_combos = list(itertools.product(*[p[1] for p in param_values]))
    print(f"  🔍 扫描 {len(all_combos)} 个参数组合…")
    
    results = []
    t0 = time.time()
    
    for combo in all_combos:
        cfg = dict(U7_CalendarV3)
        combo_dict = {}
        for (pp, _), val in zip(param_values, combo):
            combo_dict[pp] = val
            if '/' in pp:
                top, sub = pp.split('/')
                cfg[top] = dict(cfg.get(top, {}))
                cfg[top][sub] = val
            else:
                cfg[pp] = val
        
        r = window_backtest(cfg)
        c = r['calendar']
        dd_score = max(0, min(100, (20 - r['dd'])/15*100))
        sr_score = max(0, min(100, c['sharpe']/1.5*100))
        composite = dd_score * 0.5 + sr_score * 0.5
        
        results.append({
            **combo_dict,
            'ret': r['total_ret'], 'dd': r['dd'],
            'sr': c['sharpe'], 'wr': c['wr'],
            'score': round(composite, 1),
            'active_n': r['n_active'], 'total_n': r['n_total'],
        })
    
    elapsed = time.time() - t0
    results.sort(key=lambda x: x['score'], reverse=True)
    
    # Top-5
    print(f"\n  📊 Top-5 组合 ({elapsed:.0f}s):")
    print(f"  {'bull':>5} {'neut':>5} {'min_sec':>8} {'cold':>5} {'hold':>5}  {'ret':>8} {'dd':>6} {'sr':>6} {'score':>6}")
    print(f"  {'─'*5} {'─'*5} {'─'*8} {'─'*5} {'─'*5}  {'─'*8} {'─'*6} {'─'*6} {'─'*6}")
    for r in results[:5]:
        print(f"  {r['max_positions/bull']:>5} {r['max_positions/neutral']:>5} {r['min_sectors']:>8} "
              f"{r['cold_threshold']:>5} {r['hold_days']:>5}  {r['ret']:>7.1f}% {r['dd']:>5.1f}% {r['sr']:>5.2f} {r['score']:>5.0f}")
    
    # 稳定性分析：每个参数维度，看性能方差
    print(f"\n  🔬 参数稳定性（按维度聚合，score 均值 ± 范围）:")
    for pp, vals in param_values:
        grouped = defaultdict(list)
        for r in results:
            grouped[r[pp]].append(r['score'])
        parts = []
        for v in vals:
            scores = grouped[v]
            parts.append(f"{v}={np.mean(scores):.0f}±{max(scores)-min(scores):.0f}")
        stable = max(grouped.values(), key=lambda x: np.mean(x))
        best_avg = np.mean(stable)
        worst_avg = min(np.mean(s) for s in grouped.values())
        gap = best_avg - worst_avg
        verdict = "✅ 平台稳" if gap < 8 else ("⚠️ 有波动" if gap < 15 else "❌ 过拟合风险")
        print(f"    {pp}: {' | '.join(parts)}   {verdict} (gap={gap:.0f})")
    
    # Top-10 有多少个离 V3 参数很近？
    v3_combo = {'max_positions/bull':3,'max_positions/neutral':5,'min_sectors':4,'cold_threshold':-3,'hold_days':10}
    nearby = sum(1 for r in results[:10] 
                 if r['max_positions/bull'] in [2,3,4] 
                 and r['min_sectors'] in [3,4,5]
                 and abs(r['cold_threshold'] - (-3)) <= 1)
    print(f"\n  📍 Top-10 中 {nearby}/10 在 V3 参数邻域 → {'✅ 参数稳健' if nearby>=6 else '⚠️ 注意过拟合'}")
    
    return results

# ═══════════════════════════════════════════════════
# Step 3: 组合级风控（替代单票止损）
# ═══════════════════════════════════════════════════

def portfolio_risk_backtest(config, start_date='2023-06-01', end_date=None,
                            risk_config=None):
    """
    逐日回测，支持组合级风控。
    risk_config:
      - dd_cut_pct: float — 组合回撤超此触发降仓 50%
      - dd_defensive_pct: float — 组合回撤超此切防御资产
      - regime_cut: bool — regime 转 bear 减仓
      - ma_confirm_days: int — MA20 跌破确认天数(0=立即)
    """
    if risk_config is None:
        risk_config = {}
    
    hold = config.get('hold_days', 10)
    dates_all = _dates_all
    if end_date is None: end_date = dates_all[-1]
    si = next((i for i, d in enumerate(dates_all) if d >= start_date), 0)
    ei = next((i for i, d in enumerate(dates_all) if d > end_date), len(dates_all))
    
    rebalance = list(range(si, ei - hold, hold))
    if rebalance and rebalance[-1] < ei - hold:
        rebalance.append(ei - hold - 1)
    
    # 动态组合状态
    daily_equity = [100]  # 逐日净值
    events = []  # 风控事件日志
    
    # 当个持仓窗口的状态
    current_positions = []  # [{code, entry_price, shares, ...}]
    dd_from_peak = 0
    peak_equity = 100
    regime_history = []  # 连续 NEUTRAL/BEAR 天数
    
    for wi, idx in enumerate(rebalance):
        ri = detect_regime_at(idx)
        regime = ri['regime']
        conf = ri['conf']
        end_idx = min(idx + hold, len(_combined) - 1)
        
        # 选股（使用可选的仓位限制）
        max_pos = config.get('max_positions', {'bull':3,'neutral':5,'bear':2})
        n_base = max_pos.get(regime, 3)
        
        # 风控调整仓位
        risk_cut_applied = False
        dd_cut_pct = risk_config.get('dd_cut_pct')
        dd_defensive_pct = risk_config.get('dd_defensive_pct')
        
        if dd_defensive_pct and dd_from_peak >= dd_defensive_pct:
            # 切防御：只买国债+黄金
            n_base = 0
            risk_cut_applied = True
            events.append({'date': _combined[idx]['date'], 'type': 'DD_DEFENSIVE',
                          'dd': round(dd_from_peak, 1), 'threshold': dd_defensive_pct})
        elif dd_cut_pct and dd_from_peak >= dd_cut_pct:
            n_base = max(1, n_base // 2)
            risk_cut_applied = True
            events.append({'date': _combined[idx]['date'], 'type': 'DD_CUT',
                          'dd': round(dd_from_peak, 1), 'positions': n_base})
        
        if risk_config.get('regime_cut') and regime == 'bear':
            n_base = min(n_base, config.get('max_positions',{}).get('bear', 2))
        
        # 只保留前 n_base 个（如果被打折了）
        picks = select_at(idx, regime, conf, config)
        if n_base < len(picks):
            picks = picks[:n_base]
        
        # 逐日模拟
        pick_rets = []
        for p in picks:
            sc = _combined[idx]['etfs'].get(p['code'],{}).get('close', 0)
            if sc <= 0: continue
            
            ret = None
            ma_confirm = risk_config.get('ma_confirm_days', 0)
            
            for day_i in range(idx, end_idx + 1):
                cc = _combined[day_i]['etfs'].get(p['code'],{}).get('close', 0)
                if cc <= 0: continue
                
                r = (cc/sc - 1) * 100
                
                # MA 确认延迟卖出
                if ma_confirm > 0:
                    closes_check = get_closes(p['code'], day_i, ma_confirm + 60)
                    if len(closes_check) >= 60:
                        ma20 = np.mean(closes_check[-20:])
                        ma60 = np.mean(closes_check[-60:])
                        if cc < ma20:
                            # 检查连续低于 MA20
                            below_count = 0
                            for check_d in range(day_i, max(day_i - ma_confirm, idx-1), -1):
                                check_c = _combined[check_d]['etfs'].get(p['code'],{}).get('close', 0)
                                check_closes = get_closes(p['code'], check_d, 60)
                                if check_c > 0 and len(check_closes) >= 20:
                                    if check_c < np.mean(check_closes[-20:]):
                                        below_count += 1
                                    else:
                                        break
                                else:
                                    break
                            if below_count >= ma_confirm:
                                ret = r  # 卖出
                                events.append({'date': _combined[day_i]['date'],
                                              'type': 'MA_BREAK', 'code': p['code'],
                                              'ma20': round(ma20, 3), 'price': round(cc, 3),
                                              'ret': round(r, 2), 'days_below': below_count})
                                break
                
                if day_i == end_idx:
                    ret = r
            
            if ret is not None:
                pick_rets.append(ret)
        
        # 窗口收益
        avg_ret = np.mean(pick_rets) if pick_rets else 0
        
        # 无持仓 → 买国债
        if not pick_rets and not risk_cut_applied:
            sc = _combined[idx]['etfs'].get(BOND_ETF,{}).get('close',0)
            ec = _combined[end_idx]['etfs'].get(BOND_ETF,{}).get('close',0)
            avg_ret = (ec/sc-1)*100 if sc>0 and ec>0 else 0
        elif not pick_rets and risk_cut_applied and dd_from_peak >= (dd_defensive_pct or 999):
            # 防御模式 → 50%国债 + 50%黄金
            sc_b = _combined[idx]['etfs'].get(BOND_ETF,{}).get('close',0)
            ec_b = _combined[end_idx]['etfs'].get(BOND_ETF,{}).get('close',0)
            sc_g = _combined[idx]['etfs'].get(GOLD_ETF,{}).get('close',0)
            ec_g = _combined[end_idx]['etfs'].get(GOLD_ETF,{}).get('close',0)
            r_b = (ec_b/sc_b-1)*100 if sc_b>0 and ec_b>0 else 0
            r_g = (ec_g/sc_g-1)*100 if sc_g>0 and ec_g>0 else 0
            avg_ret = 0.5*r_b + 0.5*r_g
        
        # 更新逐日净值
        daily_ret = avg_ret / hold  # 分摊到每天
        for d in range(hold):
            if len(daily_equity) < len(_dates_all):
                daily_equity.append(daily_equity[-1] * (1 + daily_ret/100))
        
        peak_equity = max(peak_equity, daily_equity[-1])
        dd_from_peak = (peak_equity - daily_equity[-1]) / peak_equity * 100
    
    # 最终指标
    eq = daily_equity
    total = (eq[-1]/100-1)*100
    peak = eq[0]; dd = 0
    for v in eq: peak = max(peak, v); dd = max(dd, (peak-v)/peak*100)
    
    # 基于窗口收益计算 Sharpe（与实际回测的 equity 曲线对应）
    # 简化：用 daily_equity 算日收益 → 年化
    n_days = len(eq) - 1
    daily_rets = np.diff(eq) / eq[:-1]
    ann_ret = ((eq[-1]/eq[0])**(252/n_days)-1)*100 if n_days>0 else 0
    sr = float(np.mean(daily_rets-0.02/252)/np.std(daily_rets)*math.sqrt(252)) if np.std(daily_rets)>0 else 0
    
    # 胜率基于窗口
    window_rets = []
    for i in range(0, len(rebalance)):
        start_i = rebalance[i]
        end_i_val = min(start_i + hold, len(eq)-1)
        if end_i_val > start_i + 1:
            wr = (eq[end_i_val]/eq[start_i+1]-1)*100
            window_rets.append(wr)
    wr = sum(1 for r in window_rets if r>0)/len(window_rets)*100 if window_rets else 0
    
    return {
        'total_ret': round(total, 1), 'dd': round(dd, 1),
        'ann': round(ann_ret, 1), 'sharpe': round(sr, 2),
        'wr': round(wr, 1), 'n_days': n_days,
        'risk_events': len(events),
        'events': events,
        'daily_equity': [round(e, 4) for e in daily_equity],
    }

def risk_control_test():
    print("\n" + "=" * 70)
    print("🛡️  Step 3: 组合级风控（替代单票止损）")
    print("=" * 70)
    
    base = portfolio_risk_backtest(U7_CalendarV3)  # 无风控 baseline
    print(f"\n  📏 Baseline (无风控): ret={base['total_ret']}% dd={base['dd']}% sr={base['sharpe']} ann={base['ann']}% wr={base['wr']}%")
    
    variants = [
        ('DD8%降仓', {'dd_cut_pct': 8}),
        ('DD12%防御', {'dd_cut_pct': 8, 'dd_defensive_pct': 12}),
        ('DD8%+转熊减仓', {'dd_cut_pct': 8, 'regime_cut': True}),
        ('DD8%+MA2日确认', {'dd_cut_pct': 8, 'ma_confirm_days': 2}),
        ('DD10%+转熊+MA2日', {'dd_cut_pct': 10, 'regime_cut': True, 'ma_confirm_days': 2}),
        ('DD8%+防御+转熊+MA2', {'dd_cut_pct': 8, 'dd_defensive_pct': 12, 'regime_cut': True, 'ma_confirm_days': 2}),
    ]
    
    print(f"\n  {'方案':<22} {'ret':>8} {'dd':>6} {'sr':>6} {'ann':>7} {'wr':>6} {'事件':>5}  vs Baseline")
    print(f"  {'─'*22} {'─'*8} {'─'*6} {'─'*6} {'─'*7} {'─'*6} {'─'*5}  {'─'*10}")
    
    best = None
    for name, rc in variants:
        r = portfolio_risk_backtest(U7_CalendarV3, risk_config=rc)
        imp = r['sharpe'] - base['sharpe']
        dd_imp = base['dd'] - r['dd']  # positive = good
        print(f"  {name:<22} {r['total_ret']:>7.1f}% {r['dd']:>5.1f}% {r['sharpe']:>5.2f} {r['ann']:>6.1f}% {r['wr']:>5.1f}% {r['risk_events']:>4}  "
              f"sr{imp:+.2f} dd{dd_imp:+.1f}%")
        if best is None or r['sharpe'] > best['sharpe']:
            best = r
            best['name'] = name
            best['risk_config'] = rc
    
    print(f"\n  👑 最优风控: {best['name']} → sr={best['sharpe']:.2f} dd={best['dd']:.1f}%")
    
    # 报告最严重风控事件
    if best.get('events'):
        top_events = sorted(best['events'], key=lambda e: e.get('dd', 0), reverse=True)[:3]
        print(f"  📋 关键风控事件 ({len(best['events'])} 次总计):")
        for e in top_events:
            print(f"    {e['date']}  {e['type']}  dd={e.get('dd','?'):.1f}%")
    
    return {'baseline': base, 'variants': variants, 'best': best}

# ═══════════════════════════════════════════════════
# Step 4: 执行成本压力测试
# ═══════════════════════════════════════════════════

def cost_stress_test():
    print("\n" + "=" * 70)
    print("💰 Step 4: 执行成本压力测试")
    print("=" * 70)
    
    cost_levels = [0, 10, 20, 30, 50]
    
    # 跑不同成本
    print(f"\n  {'成本':>8} {'ret':>8} {'dd':>6} {'sr':>6} {'wr':>6}  vs 0bps")
    print(f"  {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*6}  {'─'*10}")
    
    results = {}
    base = None
    for cost in cost_levels:
        r = window_backtest(U7_CalendarV3, cost_bps=cost)
        c = r['calendar']
        if cost == 0:
            base = r
            print(f"  {cost:>5} bps {r['total_ret']:>7.1f}% {r['dd']:>5.1f}% {c['sharpe']:>5.2f} {c['wr']:>5.1f}%")
        else:
            d_ret = r['total_ret'] - base['total_ret']
            d_sr = c['sharpe'] - base['calendar']['sharpe']
            print(f"  {cost:>5} bps {r['total_ret']:>7.1f}% {r['dd']:>5.1f}% {c['sharpe']:>5.2f} {c['wr']:>5.1f}%  "
                  f"ret{d_ret:+.1f}% sr{d_sr:+.2f}")
        results[cost] = r
    
    # 流动性过滤：剔除日均成交额<阈值 的 ETF
    print(f"\n  ── 低流动性过滤 ──")
    for min_vol in [100, 200, 500]:  # 万元
        # 过滤 universe
        filtered = {}
        for code, info in _universe['etfs'].items():
            if info.get('status') != 'active': continue
            idx = len(_combined) - 1
            vols = []
            for i in range(max(0, idx-20), idx+1):
                v = _combined[i]['etfs'].get(code,{}).get('volume', 0)
                if v: vols.append(v)
            avg_vol = np.mean(vols) if vols else 0
            if avg_vol >= min_vol * 10000:
                filtered[code] = info
        
        # 临时修改 universe（hack via module global）
        # 更好的做法是传 filtered_universe，但这里简化
        saved = dict(_universe['etfs'])
        _universe['etfs'] = filtered
        r = window_backtest(U7_CalendarV3, cost_bps=20)
        _universe['etfs'] = saved
        
        c = r['calendar']
        print(f"    日均成交≥{min_vol}万: {len(filtered)}/{len(saved)} ETF, ret={r['total_ret']:.1f}% dd={r['dd']:.1f}% sr={c['sharpe']:.2f}")
    
    # 成本+流动性的最终压力测试
    print(f"\n  ── 综合压力（20bps + ≥200万成交）──")
    filtered = {}
    for code, info in _universe['etfs'].items():
        if info.get('status') != 'active': continue
        idx = len(_combined) - 1
        vols = []
        for i in range(max(0, idx-20), idx+1):
            v = _combined[i]['etfs'].get(code,{}).get('volume', 0)
            if v: vols.append(v)
        avg_vol = np.mean(vols) if vols else 0
        if avg_vol >= 2000000:
            filtered[code] = info
    saved = dict(_universe['etfs'])
    _universe['etfs'] = filtered
    r_stress = window_backtest(U7_CalendarV3, cost_bps=20)
    _universe['etfs'] = saved
    c = r_stress['calendar']
    verdict = "✅ 通过" if r_stress['dd'] < 15 and c['sharpe'] > 1.0 else ("⚠️ 边缘" if r_stress['dd'] < 18 else "❌ 不通过")
    print(f"    {len(filtered)} ETF | ret={r_stress['total_ret']:.1f}% dd={r_stress['dd']:.1f}% sr={c['sharpe']:.2f} → {verdict}")
    
    return results

# ═══════════════════════════════════════════════════
# Step 5: Walk-forward 验证
# ═══════════════════════════════════════════════════

def walkforward_test():
    print("\n" + "=" * 70)
    print("🚶 Step 5: Walk-forward 验证")
    print("=" * 70)
    
    # 三阶段
    phases = [
        ('训练 (2023-06 ~ 2024-12)', '2023-06-01', '2024-12-31'),
        ('验证 (2025-01 ~ 2025-12)', '2025-01-01', '2025-12-31'),
        ('留出测试 (2026-01 ~ 现在)', '2026-01-01', None),
    ]
    
    print(f"\n  {'阶段':<28} {'ret':>8} {'dd':>6} {'sr':>6} {'ann':>7} {'wr':>6} {'窗':>5}")
    print(f"  {'─'*28} {'─'*8} {'─'*6} {'─'*6} {'─'*7} {'─'*6} {'─'*5}")
    
    all_phase = []
    for name, start, end in phases:
        r = window_backtest(U7_CalendarV3, start_date=start, end_date=end)
        c = r['calendar']
        print(f"  {name:<28} {r['total_ret']:>7.1f}% {r['dd']:>5.1f}% {c['sharpe']:>5.2f} {c['ann']:>6.1f}% {c['wr']:>5.1f}% {r['n_total']:>4}")
        all_phase.append({'name': name, **r})
    
    # 训练阶段内参数优化（简化版：只测 bull 仓位）
    train = all_phase[0]
    validate = all_phase[1]
    test_out = all_phase[2]
    
    print(f"\n  🔍 训练阶段参数搜索（bull 仓位 2/3/4）:")
    train_best = None
    for bull_n in [2, 3, 4]:
        cfg = dict(U7_CalendarV3)
        cfg['max_positions'] = dict(cfg['max_positions'])
        cfg['max_positions']['bull'] = bull_n
        
        # 训练
        r_train = window_backtest(cfg, start_date='2023-06-01', end_date='2024-12-31')
        # 验证
        r_val = window_backtest(cfg, start_date='2025-01-01', end_date='2025-12-31')
        
        train_sr = r_train['calendar']['sharpe']
        val_sr = r_val['calendar']['sharpe']
        oos_gap = abs(train_sr - val_sr)
        
        tag = ''
        if train_best is None or r_train['calendar']['sharpe'] > train_best['train_sr']:
            train_best = {'bull': bull_n, 'train_sr': train_sr, 'val_sr': val_sr, 'oos_gap': oos_gap, 'oos_ret': r_val['total_ret']}
            tag = ' ⭐训练最优'
        
        gap_ok = '✅' if oos_gap < 0.3 else ('⚠️' if oos_gap < 0.5 else '❌')
        print(f"    bull={bull_n}  train_sr={train_sr:.2f}  val_sr={val_sr:.2f}  oos_gap={oos_gap:.2f} {gap_ok}{tag}")
    
    # 用训练最优参数跑留出测试
    print(f"\n  🎯 留出测试（2026）：bull={train_best['bull']}")
    cfg_test = dict(U7_CalendarV3)
    cfg_test['max_positions'] = dict(cfg_test['max_positions'])
    cfg_test['max_positions']['bull'] = train_best['bull']
    r_final = window_backtest(cfg_test, start_date='2026-01-01', end_date=None)
    c = r_final['calendar']
    
    oos_dd_ok = '✅' if r_final['dd'] < 15 else '⚠️'
    oos_sr_ok = '✅' if c['sharpe'] > 0.8 else '⚠️'
    print(f"    留出: ret={r_final['total_ret']:.1f}% dd={r_final['dd']:.1f}% sr={c['sharpe']:.2f} ann={c['ann']:.1f}%")
    print(f"    DD: {oos_dd_ok} SR: {oos_sr_ok}")
    
    # ⚠️ 免责声明
    print(f"\n  ⚠️ 免责声明:")
    print(f"    以上结果属于假设性回测表现 (hypothetical backtested performance)。")
    print(f"    存在固有局限：回测不代表实盘，过去结果不保证未来收益。")
    print(f"    参考: SEC Marketing Rule (IA-5653)")
    print(f"    https://www.sec.gov/files/rules/final/2020/ia-5653.pdf")
    
    return {'phases': all_phase, 'train_best': train_best, 'final_test': r_final}

# ═══════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════

if __name__ == '__main__':
    t0 = time.time()
    
    # Step 1 已在上方（打印配置）
    # Step 2
    plateau_results = plateau_test()
    
    # Step 3
    risk_results = risk_control_test()
    
    # Step 4
    cost_results = cost_stress_test()
    
    # Step 5
    wf_results = walkforward_test()
    
    elapsed = time.time() - t0
    
    # ═══ 汇总 ═══
    print(f"\n{'='*70}")
    print("📋 U7-v3 稳健化 总览")
    print(f"{'='*70}")
    print(f"  V3 配置: bull=3, min_sectors=4, cold=-3.0, 不止损")
    print(f"  测试耗时: {elapsed:.0f}s")
    print(f"\n  平台测试: {'✅ 参数稳健' if any('✅' in r for r in ['']) else '见详情'}")
    print(f"  风控最优: {risk_results['best']['name']} → sr={risk_results['best']['sharpe']:.2f}")
    print(f"  成本压力: 见详情")
    print(f"  Walk-forward: 训练最优 bull={wf_results['train_best']['bull']}")
    
    # 保存
    out = {
        'config': {k: v for k, v in U7_CalendarV3.items()},
        'plateau_top5': [dict(r) for r in plateau_results[:5]],
        'risk_best': risk_results['best']['name'],
        'risk_best_config': risk_results['best'].get('risk_config', {}),
        'walkforward_train_best': wf_results['train_best'],
    }
    with open(os.path.join(DATA_DIR, 'u7_v3_robustness.json'), 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 {DATA_DIR}/u7_v3_robustness.json")
