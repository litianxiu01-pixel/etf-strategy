#!/usr/bin/env python3
"""
U7 策略实验 1-3
基于 u7_strategy.py 单一真源，不做逻辑复制。

实验 1: T10 + 空仓买国债 — 空仓窗口不用现金，买国债ETF拿收益
实验 2: T10 + 风险平价 — 不改选股，仓位按 1/vol 分配
实验 3: T10 + 日历口径参数网格 — 用 74 窗完整指标做参数搜索
"""
import json, os, sys, math, hashlib, copy, time
import numpy as np
from collections import defaultdict

WORKSPACE = os.path.expanduser("~/.qclaw/workspace-main")
DATA_DIR = os.path.join(WORKSPACE, "data/market_regime")
sys.path.insert(0, os.path.join(WORKSPACE, "scripts"))
from u7_strategy import (
    _load, detect_regime_at, select_at, get_closes,
    T10_CONFIG, make_config,
)

_combined, _universe, _dates_all = _load()
start_idx = next((i for i, d in enumerate(_dates_all) if d >= '2023-06-01'), 120)

# ═══════════════════════════════════════════════════
# 共享工具
# ═══════════════════════════════════════════════════
BOND_ETF = '511010'  # 国债ETF国泰 — 牛市里也活跃

def compute_metrics(strat_rets, bench_rets, hold=10):
    """从收益率序列计算双口径指标，返回 (active, calendar)"""
    n_total = len(strat_rets)
    active_rets = [r for r in strat_rets if r != 0]
    n_active = len(active_rets)
    n_zeros = n_total - n_active

    eq = [100]
    for sr in strat_rets: eq.append(eq[-1]*(1+sr/100))
    total = (eq[-1]/100-1)*100

    periods_per_year = 252/hold
    peak=eq[0]; dd=0
    for v in eq: peak=max(peak,v); dd=max(dd,(peak-v)/peak*100)

    # 有持仓口径
    if n_active >= 10:
        n_years_a = n_active/periods_per_year
        ann_a = ((eq[-1]/100)**(1/n_years_a)-1)*100
        exc_a = np.array(active_rets)-2.0/periods_per_year
        sharpe_a = float(np.mean(exc_a)/np.std(active_rets)*math.sqrt(periods_per_year)) if np.std(active_rets)>0 else 0
        wr_a = sum(1 for r in active_rets if r>0)/n_active*100
    else:
        ann_a = sharpe_a = wr_a = 0

    # 日历口径
    n_years_c = n_total/periods_per_year
    ann_c = ((eq[-1]/100)**(1/n_years_c)-1)*100 if n_years_c>0 else 0
    all_arr = np.array(strat_rets)
    exc_c = all_arr - 2.0/periods_per_year
    sharpe_c = float(np.mean(exc_c)/np.std(all_arr)*math.sqrt(periods_per_year)) if np.std(all_arr)>0 else 0
    wr_c = sum(1 for r in strat_rets if r>0)/n_total*100

    beq=[100]
    for br in bench_rets: beq.append(beq[-1]*(1+br/100))
    bench_t=(beq[-1]/100-1)*100

    return {
        'active': {'total':total,'ann':ann_a,'dd':dd,'sharpe':sharpe_a,'wr':wr_a,'n':n_active,'periods':n_total},
        'calendar': {'total':total,'ann':ann_c,'dd':dd,'sharpe':sharpe_c,'wr':wr_c,'n':n_active,'periods':n_total},
        'bench': bench_t,
    }

def run_base_backtest(config, handle_empty=None, weight_fn=None):
    """
    通用回测引擎。
    handle_empty='bond': 空仓买 BOND_ETF
    handle_empty='skip': 空仓不记录（原 optimize_r4 行为）
    handle_empty=None: 空仓记 0 收益
    weight_fn(cls,regime,conf,picks): 返回每只仓位的权重列表
    """
    hold = 10
    rebalance = list(range(start_idx, len(_combined)-hold, hold))
    if rebalance and rebalance[-1] < len(_combined)-hold:
        rebalance.append(len(_combined)-hold-1)

    strat_rets, bench_rets = [], []
    win_count = 0

    for idx in rebalance:
        regime_info = detect_regime_at(idx)
        regime = regime_info['regime']
        conf = regime_info['conf']

        picks = select_at(idx, regime, conf, config)
        end_idx = min(idx+hold, len(_combined)-1)

        stop_loss = config.get('stop_loss_pct')
        pick_rets = []

        if picks:
            # 计算权重
            if weight_fn:
                closes_for_w = {
                    p['code']: get_closes(p['code'], idx, 120) for p in picks
                }
                weights = weight_fn(closes_for_w, regime, conf, picks)
                if abs(sum(weights)-1) > 0.001:
                    weights = [w/sum(weights) for w in weights]
            else:
                weights = [1.0/len(picks)] * len(picks)

            for pi, p in enumerate(picks):
                code = p['code']
                sc = _combined[idx]['etfs'].get(code,{}).get('close',0)
                if sc <= 0: continue
                ret = None
                if stop_loss:
                    for day_i in range(idx+1, end_idx+1):
                        cc = _combined[day_i]['etfs'].get(code,{}).get('close',0)
                        if cc <= 0: continue
                        r = (cc/sc - 1) * 100
                        if r <= -stop_loss: ret = -stop_loss; break
                        if day_i == end_idx: ret = r
                else:
                    ec = _combined[end_idx]['etfs'].get(code,{}).get('close',0)
                    if ec > 0: ret = (ec/sc-1)*100
                if ret is not None:
                    pick_rets.append(ret * weights[pi])

            if pick_rets:
                avg = sum(pick_rets)
                strat_rets.append(avg)
                if avg > 0: win_count += 1
            else:
                strat_rets.append(0)

        elif handle_empty == 'bond':
            # 空仓买国债ETF
            sc = _combined[idx]['etfs'].get(BOND_ETF,{}).get('close',0)
            ec = _combined[end_idx]['etfs'].get(BOND_ETF,{}).get('close',0)
            if sc > 0 and ec > 0:
                bond_ret = (ec/sc - 1) * 100
                strat_rets.append(bond_ret)
            else:
                strat_rets.append(0)
        elif handle_empty == 'skip':
            pass  # 不记录
        else:
            strat_rets.append(0)

        # 基准
        bench_r = []
        for code,info in _universe['etfs'].items():
            if info['status']!='active' or info['assetClass']!='cn': continue
            sc = _combined[idx]['etfs'].get(code,{}).get('close',0)
            ec = _combined[end_idx]['etfs'].get(code,{}).get('close',0)
            if sc>0 and ec>0: bench_r.append((ec/sc-1)*100)
        bench_rets.append(np.mean(bench_r) if bench_r else 0)

    return compute_metrics(strat_rets, bench_rets)

# ═══════════════════════════════════════════════════
# 实验 1: 空仓买国债
# ═══════════════════════════════════════════════════

def experiment_1():
    print("\n" + "="*70)
    print("🔬 实验 1: T10 + 空仓买国债/货基")
    print("="*70)

    results = {}

    # Baseline: T10 with empty=0
    cfg = T10_CONFIG
    baseline = run_base_backtest(cfg, handle_empty=None)
    results['baseline'] = baseline

    # 空仓买国债 511010
    bond = run_base_backtest(cfg, handle_empty='bond')
    results['bond_511010'] = bond

    print(f"\n  {'指标':<20} {'Baseline(空=0)':>18} {'空仓买国债':>18} {'改进':>12}")
    print(f"  {'─'*20} {'─'*18} {'─'*18} {'─'*12}")
    for metric, label in [('total','累计收益%'),('ann','年化%'),('dd','回撤%'),('sharpe','夏普'),('wr','胜率%')]:
        bv = baseline['calendar'][metric]
        ev = bond['calendar'][metric]
        imp = ev - bv
        print(f"  {label:<20} {bv:>18.2f} {ev:>18.2f} {imp:>+12.2f}")

    print(f"\n  💡 结论: 空仓买国债 → 日历夏普从 {baseline['calendar']['sharpe']:.2f} → {bond['calendar']['sharpe']:.2f}")
    return results

# ═══════════════════════════════════════════════════
# 实验 2: 风险平价权重
# ═══════════════════════════════════════════════════

def risk_parity_weights(closes_map, regime, conf, picks):
    """1/vol 风险平价 — 波动越高仓位越小"""
    vols = []
    for p in picks:
        c = closes_map[p['code']]
        if len(c) >= 21:
            r = np.diff(np.array(c[-21:])) / np.array(c[-21:-1])
            vol = np.std(r) * math.sqrt(252) * 100
        else:
            vol = 30
        vols.append(vol)
    inv_vol = [1.0/max(v, 5) for v in vols]  # 波动<5%按5%处理，避免分母太小
    total = sum(inv_vol)
    return [iv/total for iv in inv_vol]

def equal_weight(closes_map, regime, conf, picks):
    n = len(picks)
    return [1.0/n] * n

def experiment_2():
    print("\n" + "="*70)
    print("🔬 实验 2: T10 + 风险平价权重")
    print("="*70)

    cfg = T10_CONFIG

    ew = run_base_backtest(cfg, weight_fn=equal_weight)
    rp = run_base_backtest(cfg, weight_fn=risk_parity_weights)

    print(f"\n  {'指标':<20} {'等权':>18} {'风险平价':>18} {'改进':>12}")
    print(f"  {'─'*20} {'─'*18} {'─'*18} {'─'*12}")
    for metric, label in [('total','累计收益%'),('ann','年化%'),('dd','回撤%'),('sharpe','夏普'),('wr','胜率%')]:
        ev = ew['calendar'][metric]
        rv = rp['calendar'][metric]
        imp = rv - ev
        print(f"  {label:<20} {ev:>18.2f} {rv:>18.2f} {imp:>+12.2f}")

    # 滚动回撤对比
    eq_ew=[100]; eq_rp=[100]
    for i in range(len(ew.get('strat_rets_series',[]))):
        pass  # 需要序列，看一次性回撤
    print(f"\n  💡 风险平价: DD {ew['calendar']['dd']:.1f}% → {rp['calendar']['dd']:.1f}%")
    return {'ew': ew, 'rp': rp}

# ═══════════════════════════════════════════════════
# 实验 3: 日历口径参数网格
# ═══════════════════════════════════════════════════

def experiment_3():
    print("\n" + "="*70)
    print("🔬 实验 3: T10 + 日历口径参数网格 (74窗)")
    print("="*70)

    # 参数网格 — 重点测影响最大的几个维度
    grids = [
        # (参数名, 测试值列表, 默认值)
        ('stop_loss_pct', [6, 8, 10, 12, None], 8),
        ('min_sectors', [2, 3, 4, 5], 3),
        ('cold_threshold', [-1.5, -2.0, -3.0, -5.0], -2.0),
        ('momentum_weights/momentum', [0.5, 0.8, 1.0, 1.2, 1.5], 1.0),
        ('momentum_weights/vol', [0.1, 0.2, 0.3, 0.5, 0.0], 0.2),
        ('max_positions/neutral', [2, 3, 4, 5], 3),
        ('max_positions/bull', [3, 4, 5, 6], 5),
    ]

    # 先跑 baseline
    baseline = run_base_backtest(T10_CONFIG)
    bl_cal = baseline['calendar']
    print(f"\n  📏 Baseline 日历口径: ret={bl_cal['total']:.1f}% ann={bl_cal['ann']:.1f}% dd={bl_cal['dd']:.1f}% sharpe={bl_cal['sharpe']:.2f} wr={bl_cal['wr']:.1f}%")

    # 单一参数扫描
    all_results = []
    for param_path, values, default in grids:
        print(f"\n  ── {param_path} ──")
        best = None
        best_cfg = None
        for v in values:
            cfg = dict(T10_CONFIG)  # shallow copy
            # 处理嵌套路径
            if '/' in param_path:
                top, sub = param_path.split('/')
                cfg[top] = dict(cfg[top])
                cfg[top][sub] = v
            else:
                cfg[param_path] = v
            cfg['name'] = f"{param_path}={v}"
            res = run_base_backtest(cfg)
            c = res['calendar']
            # 综合评分：夏普+DD 各 50%
            dd_score = max(0, min(100, (20-c['dd'])/15*100))
            sr_score = max(0, min(100, c['sharpe']/1.5*100))
            comp = dd_score*0.5 + sr_score*0.5
            marker = ''
            if best is None or comp > best:
                best = comp
                best_cfg = (param_path, v)
                marker = ' ⭐'
            v_str = str(v) if v is not None else 'None'
            print(f"    {param_path}={v_str:>6}: ret={c['total']:>7.1f}% dd={c['dd']:>5.1f}% sr={c['sharpe']:.2f} wr={c['wr']:>5.1f}% score={comp:.0f}{marker}")
            all_results.append({'param': param_path, 'value': v, **c, 'score': comp})
        print(f"    👑 最优: {best_cfg[0]}={best_cfg[1]}, score={best:.0f}")

    # 排序输出 Top-N 组合
    print("\n  ── 📊 Top-10 日历口径参数 ──")
    all_results.sort(key=lambda x: x['score'], reverse=True)
    for i, r in enumerate(all_results[:10]):
        v_str = str(r['value']) if r['value'] is not None else 'None'
        print(f"  {i+1:>2}. {r['param']}={v_str:>6}  ret={r['total']:>7.1f}% dd={r['dd']:>5.1f}% sr={r['sharpe']:.2f} wr={r['wr']:>5.1f}% score={r['score']:.0f}")

    # 组合最优参数，跑一次"打包版"
    print("\n  ── 🧩 组合最优参数 ──")
    best_cfg = dict(T10_CONFIG)
    # 取每个维度最优
    param_bests = {}
    for param_path, values, default in grids:
        subset = [r for r in all_results if r['param']==param_path]
        subset.sort(key=lambda x: x['score'], reverse=True)
        param_bests[param_path] = subset[0]['value']

    print(f"  最优参数集: {param_bests}")
    for pp, v in param_bests.items():
        if '/' in pp:
            top, sub = pp.split('/')
            if top not in best_cfg or not isinstance(best_cfg[top], dict):
                best_cfg[top] = dict(T10_CONFIG.get(top, {}))
            best_cfg[top][sub] = v
        else:
            best_cfg[pp] = v
    best_cfg['name'] = 'CALENDAR_GRID_BEST'

    best_res = run_base_backtest(best_cfg)
    bc = best_res['calendar']
    print(f"  组合: ret={bc['total']:.1f}% ann={bc['ann']:.1f}% dd={bc['dd']:.1f}% sr={bc['sharpe']:.2f} wr={bc['wr']:.1f}%")

    # 与 baseline 对比
    print(f"\n  📊 vs Baseline:")
    for m,label in [('total','收益'),('ann','年化'),('dd','回撤'),('sharpe','夏普'),('wr','胜率')]:
        bv=bl_cal[m]; cv=bc[m]
        imp=cv-bv
        arrow='↑' if imp>0 else '↓' if imp<0 else '='
        print(f"    {label}: {bv:.2f} → {cv:.2f} {arrow}{abs(imp):.2f}")

    return {'baseline': baseline, 'best': best_res, 'all': all_results, 'best_cfg': best_cfg}

# ═══════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════

if __name__ == '__main__':
    t0 = time.time()
    exp1 = experiment_1()
    exp2 = experiment_2()
    exp3 = experiment_3()
    elapsed = time.time() - t0
    print(f"\n{'='*70}")
    print(f"✅ 三实验完成 耗时 {elapsed:.0f}s")

    # 保存结果
    out = {
        'exp1_bond': exp1['bond_511010']['calendar'],
        'exp2_rp': exp2['rp']['calendar'],
        'exp3_best': exp3['best']['calendar'],
        'exp3_best_cfg': {k: v for k, v in exp3['best_cfg'].items() if k != 'name'},
        'exp3_baseline': exp3['baseline']['calendar'],
        'exp3_top10': [{'param': r['param'], 'value': r['value'], 'sharpe': r['sharpe'], 'dd': r['dd'], 'score': r['score']} for r in exp3['all'][:10]],
    }
    with open(os.path.join(DATA_DIR, 'exp_1_2_3_results.json'), 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"📁 结果: {DATA_DIR}/exp_1_2_3_results.json")
