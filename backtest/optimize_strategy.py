#!/usr/bin/env python3
"""
ETF Arsenal 策略优化引擎
- 评分体系: 总收益 + 夏普 + 回撤 + 胜率 + 超额 + 熊市防御
- 参数网格: 板块过滤/热度周期/体制权重/持仓数/动量公式
- 自动迭代: 每版对比评分，取最优
"""
import json, os, math, sys, copy
import numpy as np
from collections import defaultdict

DATA_DIR = os.path.expanduser("~/.qclaw/workspace-main/data/market_regime")

with open(f"{DATA_DIR}/combined_daily.json") as f:
    combined = json.load(f)
with open(f"{DATA_DIR}/etf_universe.json") as f:
    universe = json.load(f)

dates_all = [row['date'] for row in combined]
start_idx = next((i for i, d in enumerate(dates_all) if d >= '2023-06-01'), 120)

SECTOR_RULES = [
    ("半导体/芯片", ["芯片", "半导体", "集成电路"]),
    ("军工/国防",  ["军工", "国防"]),
    ("证券/券商",  ["证券", "券商"]),
    ("银行",       ["银行"]),
    ("通信/5G",   ["通信", "5G", "电信"]),
    ("医疗/医药",  ["医疗", "医药", "创新药", "生物医药"]),
    ("新能源",     ["新能源", "光伏", "碳中和"]),
    ("电池/锂电",  ["电池", "锂电"]),
    ("传媒/游戏",  ["传媒", "游戏", "影视"]),
    ("纳指(美股)",  ["纳指", "纳斯达克"]),
    ("港股",       ["港股", "恒生", "港股通"]),
    ("黄金",       ["黄金", "金"]),
    ("国债/债券",   ["国债", "债券", "债"]),
    ("科创50",     ["科创50", "科创ETF"]),
    ("创业板50",    ["创业板50", "创业板ETF"]),
    ("中概互联",    ["中概互联", "中概"]),
    ("房地产",     ["房地产", "地产"]),
]

DEFENSIVE_CODES = {
    'gold': ['518880','518660','518680','159812','159830','159831','159322','159937'],
    'bond': ['511010','511020','511030','511260','511090','511220','511180','159649','159650','159651'],
}
US_CODES = ['159501','159941','159696','159660','159659','159655','159632','159612','159513','513500','513100','513300','513050']
HK_CODES = ['159740','513060','513820','513090','513180','159605','159607','513130','159726','513770']

def etf_sector(code):
    name = universe['etfs'].get(code, {}).get('name', '')
    for sn, kw in SECTOR_RULES:
        if any(k in name for k in kw): return sn
    return '其他'

def get_closes(code, end_idx, need=120):
    result = []
    for i in range(end_idx - need + 1, end_idx + 1):
        if 0 <= i < len(combined) and code in combined[i]['etfs']:
            result.append(combined[i]['etfs'][code]['close'])
    return result

# =========================================================================
# Configurable Backtest Engine
# =========================================================================

DEFAULT_CONFIG = {
    'name': 'default',
    'sector_filter': 'always',       # always / only_nonneutral / never
    'heat_periods': [5],              # periods to evaluate sector heat (days)
    'cold_threshold': -2.0,           # sector avg return < this → cold
    'hot_threshold': 2.0,             # sector avg return > this → hot
    'regime_weights': {               # trend / breadth / volatility / flow
        'trend': 0.35, 'breadth': 0.25, 'volatility': 0.20, 'flow': 0.20,
    },
    'momentum_weights': {             # score = ret60*w_ret - vol*w_vol + trend*w_trend
        'ret60': 1.0, 'vol20': 0.2, 'trend_bonus': 0.2,
    },
    'max_positions': {'bull': 5, 'neutral': 3, 'bear': 2},
    'bull_threshold': 0.55,
    'bear_threshold': 0.35,
    'position_scale': 1.0,            # cash scaling factor
}


def sector_heatmap_at(idx, heat_periods):
    """Calculate sector heatmap with configurable periods"""
    sectors_data = defaultdict(list)
    for code, info in universe['etfs'].items():
        if info['status'] != 'active': continue
        closes = get_closes(code, idx, 120)
        if len(closes) < max(heat_periods) + 1: continue
        entry = {'code': code}
        for p in heat_periods:
            entry[f'ret{p}'] = (closes[-1]/closes[-(p+1)]-1)*100 if len(closes) > p else 0
        sectors_data[etf_sector(code)].append(entry)

    result = {}
    for sname, etfs in sectors_data.items():
        entry = {'count': len(etfs)}
        for p in heat_periods:
            entry[f'avg_{p}d'] = round(float(np.mean([e[f'ret{p}'] for e in etfs])), 2)
        result[sname] = entry
    return result


def detect_regime_at(idx, w):
    cn_etfs = [c for c, info in universe['etfs'].items()
               if info['status'] == 'active' and c not in
               US_CODES + HK_CODES + DEFENSIVE_CODES['gold'] + DEFENSIVE_CODES['bond']]

    perf = []
    for code in cn_etfs:
        closes = get_closes(code, idx, 120)
        if len(closes) < 61: continue
        perf.append({
            'above_ma60': closes[-1] > np.mean(closes[-60:]),
            'above_ma20': closes[-1] > np.mean(closes[-20:]),
            'ret60d': (closes[-1]/closes[-61]-1)*100,
            'ret20d': (closes[-1]/closes[-21]-1)*100,
        })

    if len(perf) < 5: return {'regime': 'neutral', 'confidence': 0.3}

    pct_ma60 = sum(1 for e in perf if e['above_ma60'])/len(perf)*100
    avg_ret_60 = np.mean([e['ret60d'] for e in perf])
    pct_ma20 = sum(1 for e in perf if e['above_ma20'])/len(perf)*100
    avg_ret_20 = np.mean([e['ret20d'] for e in perf])

    all_rets = []
    for code in cn_etfs:
        closes = get_closes(code, idx, 30)
        if len(closes) >= 14:
            r = np.diff(closes) / closes[:-1]
            all_rets.extend(r)
    vol = np.std(all_rets)*math.sqrt(252)*100 if all_rets else 30

    trend = 1.0 if avg_ret_60>5 and pct_ma60>50 else (0.5 if avg_ret_60>0 or pct_ma60>40 else 0.0)
    breadth = 1.0 if pct_ma20>60 else (0.5 if pct_ma20>40 else 0.0)
    vol_score = 1.0 if vol<20 else (0.5 if vol<30 else 0.0)
    flow = 1.0 if avg_ret_20>0 else (0.5 if avg_ret_20>-3 else 0.0)

    score = trend*w['trend'] + breadth*w['breadth'] + vol_score*w['volatility'] + flow*w['flow']
    regime = 'bull' if score>=0.55 else ('neutral' if score>=0.35 else 'bear')
    return {'regime': regime, 'confidence': score}


def momentum_score_at(code, idx, mw):
    closes = get_closes(code, idx, 120)
    if len(closes) < 61: return None
    ret60 = (closes[-1]/closes[-61]-1)*100
    r = np.array(closes[-21:])
    r20_ret = np.diff(r) / r[:-1]
    vol20 = np.std(r20_ret)*math.sqrt(252)*100 if len(r20_ret)>0 else 30
    ma20 = np.mean(closes[-20:]) if len(closes)>=20 else 0
    ma60 = np.mean(closes[-60:]) if len(closes)>=60 else 0
    trend_bonus = 20 if ma20>ma60 else 10
    return ret60*mw['ret60'] - vol20*mw['vol20'] + trend_bonus*mw['trend_bonus']


def select_at(idx, regime_info, config):
    heatmap = sector_heatmap_at(idx, config['heat_periods'])
    primary_period = config['heat_periods'][0]
    cold_sectors = {n for n, p in heatmap.items() if p.get(f'avg_{primary_period}d', 0) < config['cold_threshold']}
    hot_sectors = {n for n, p in heatmap.items() if p.get(f'avg_{primary_period}d', 0) > config['hot_threshold']}
    regime = regime_info['regime']

    candidates = []
    for code, info in universe['etfs'].items():
        if info['status'] != 'active': continue
        sector = etf_sector(code)
        aclass = info['assetClass']

        # Sector filtering
        skip = False
        sf = config['sector_filter']
        if sf == 'always':
            if regime == 'bear':
                if aclass not in ['gold', 'bond']: skip = True
            elif regime == 'neutral':
                if sector in cold_sectors: skip = True
            else:  # bull
                if sector in cold_sectors: skip = True
        elif sf == 'only_neutral':
            if regime == 'bear':
                if aclass not in ['gold', 'bond']: skip = True
            elif regime == 'neutral':
                if sector in cold_sectors: skip = True
            # bull: no filter
        elif sf == 'never':
            if regime == 'bear':
                if aclass not in ['gold', 'bond']: skip = True
            # else: no filter
        else:  # 'bear_only'
            if regime == 'bear':
                if aclass not in ['gold', 'bond']: skip = True

        if skip: continue

        ms = momentum_score_at(code, idx, config['momentum_weights'])
        if ms is not None:
            candidates.append({'code': code, 'score': ms, 'sector': sector})

    candidates.sort(key=lambda x: x['score'], reverse=True)
    max_n = config['max_positions'].get(regime, 3)
    return candidates[:max_n]


def run_backtest(config):
    """Run one backtest with given config"""
    MONTH_HOLD = 21
    rebalance_dates = []
    prev_month = ''
    for i in range(start_idx, len(combined) - MONTH_HOLD):
        d = combined[i]['date']
        month = d[:7]
        if month != prev_month and i > start_idx:
            rebalance_dates.append(i)
        prev_month = month
    if rebalance_dates and rebalance_dates[-1] < len(combined) - MONTH_HOLD:
        rebalance_dates.append(len(combined) - MONTH_HOLD - 1)

    strategy_rets = []
    benchmark_rets = []
    win_count = 0

    for idx in rebalance_dates:
        regime = detect_regime_at(idx, config['regime_weights'])
        picks = select_at(idx, regime, config)

        end_idx = min(idx + MONTH_HOLD, len(combined) - 1)
        pick_rets = []
        for pick in picks:
            code = pick['code']
            sc = combined[idx]['etfs'].get(code, {}).get('close', 0)
            ec = combined[end_idx]['etfs'].get(code, {}).get('close', 0)
            if sc > 0 and ec > 0:
                pick_rets.append((ec/sc-1)*100)

        if pick_rets:
            avg = np.mean(pick_rets) * config['position_scale']
            strategy_rets.append(avg)
            if avg > 0: win_count += 1

        bench_rets = []
        for code, info in universe['etfs'].items():
            if info['status'] != 'active' or info['assetClass'] != 'cn': continue
            sc = combined[idx]['etfs'].get(code, {}).get('close', 0)
            ec = combined[end_idx]['etfs'].get(code, {}).get('close', 0)
            if sc > 0 and ec > 0:
                bench_rets.append((ec/sc-1)*100)
        if bench_rets:
            benchmark_rets.append(np.mean(bench_rets))

    if not strategy_rets:
        return {'score': 0}

    n = len(strategy_rets)

    # Equity curve
    eq = [100.0]
    beq = [100.0]
    for sr, br in zip(strategy_rets, benchmark_rets):
        eq.append(eq[-1] * (1 + sr/100))
        beq.append(beq[-1] * (1 + br/100))

    total_ret = (eq[-1]/100 - 1) * 100
    bench_total = (beq[-1]/100 - 1) * 100
    n_years = n / 12
    ann_ret = ((eq[-1]/100)**(1/n_years)-1)*100 if n_years>0 else 0
    bench_ann = ((beq[-1]/100)**(1/n_years)-1)*100 if n_years>0 else 0

    # Max DD
    peak = eq[0]
    max_dd = 0
    for v in eq:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak-v)/peak*100)

    bpeak = beq[0]
    bmax_dd = 0
    for v in beq:
        bpeak = max(bpeak, v)
        bmax_dd = max(bmax_dd, (bpeak-v)/bpeak*100)

    # Sharpe
    excess = np.array(strategy_rets) - 2.0/12
    sharpe = np.mean(excess)/np.std(strategy_rets)*math.sqrt(12) if np.std(strategy_rets)>0 else 0

    # Win rate
    win_rate = win_count / n * 100

    # Alpha
    alpha = total_ret - bench_total

    # Bear performance
    bear_rets = []
    bench_bear_rets = []
    for i, idx in enumerate(rebalance_dates[:n]):
        regime = detect_regime_at(idx, config['regime_weights'])
        if regime['regime'] == 'bear':
            bear_rets.append(strategy_rets[i])
            bench_bear_rets.append(benchmark_rets[i])

    bear_perf = np.mean(bear_rets) if bear_rets else 0
    bench_bear = np.mean(bench_bear_rets) if bench_bear_rets else 0
    bear_alpha = bear_perf - bench_bear

    # ─── Composite Score ───
    # Normalize each metric to 0-100

    def score_total_ret(r):
        # 30%+ = 100, 0% = 50, -10% = 0
        return max(0, min(100, (r + 10) / 40 * 100))

    def score_sharpe(s):
        # 1.5+ = 100, 0.5 = 50, 0 = 0
        return max(0, min(100, s / 1.5 * 100))

    def score_dd(dd):
        # 5% = 100, 15% = 50, 25% = 0
        return max(0, min(100, (25 - dd) / 20 * 100))

    def score_win_rate(wr):
        return max(0, min(100, wr))

    def score_alpha(a):
        # +20% = 100, 0% = 50, -20% = 0
        return max(0, min(100, (a + 20) / 40 * 100))

    def score_relative_dd(s_dd, b_dd):
        # If strategy DD << benchmark DD → good
        improvement = b_dd - s_dd
        return max(0, min(100, (improvement + 5) / 20 * 100))

    s_total = score_total_ret(total_ret)
    s_sharpe = score_sharpe(sharpe)
    s_dd = score_dd(max_dd)
    s_win = score_win_rate(win_rate)
    s_alpha = score_alpha(alpha)
    s_rel_dd = score_relative_dd(max_dd, bmax_dd)

    composite = (
        s_total * 0.20 +
        s_sharpe * 0.25 +
        s_dd * 0.15 +
        s_win * 0.15 +
        s_alpha * 0.10 +
        s_rel_dd * 0.15
    )

    return {
        'config': config['name'],
        'composite': round(composite, 1),
        'metrics': {
            'total_ret': round(total_ret, 2),
            'annualized': round(ann_ret, 2),
            'max_dd': round(max_dd, 2),
            'sharpe': round(sharpe, 2),
            'win_rate': round(win_rate, 1),
            'alpha': round(alpha, 2),
        },
        'benchmark': {
            'total_ret': round(bench_total, 2),
            'annualized': round(bench_ann, 2),
            'max_dd': round(bmax_dd, 2),
        },
        'scores': {
            'total_ret': round(s_total, 1),
            'sharpe': round(s_sharpe, 1),
            'dd': round(s_dd, 1),
            'win_rate': round(s_win, 1),
            'alpha': round(s_alpha, 1),
            'relative_dd': round(s_rel_dd, 1),
        },
        'yearly': {},
        'bear_alpha': round(bear_alpha, 2),
    }


# =========================================================================
# Grid Search
# =========================================================================

CONFIGS = []

# V1: Baseline (current logic)
c = copy.deepcopy(DEFAULT_CONFIG)
c['name'] = 'V1_Baseline'
c['sector_filter'] = 'always'
CONFIGS.append(c)

# V2: 牛市不过滤板块
c = copy.deepcopy(DEFAULT_CONFIG)
c['name'] = 'V2_BullNoFilter'
c['sector_filter'] = 'only_neutral'
CONFIGS.append(c)

# V3: Bear-only filter (only filter in bear)
c = copy.deepcopy(DEFAULT_CONFIG)
c['name'] = 'V3_BearOnlyFilter'
c['sector_filter'] = 'bear_only'
CONFIGS.append(c)

# V4: 20日热度替代5日
c = copy.deepcopy(DEFAULT_CONFIG)
c['name'] = 'V4_Heat20d'
c['heat_periods'] = [20]
c['cold_threshold'] = -5.0
c['hot_threshold'] = 3.0
c['sector_filter'] = 'only_neutral'
CONFIGS.append(c)

# V5: 多周期热度 (5d+20d)
c = copy.deepcopy(DEFAULT_CONFIG)
c['name'] = 'V5_MultiHeat'
c['heat_periods'] = [5, 20]
c['cold_threshold'] = -2.0
c['hot_threshold'] = 2.0
c['sector_filter'] = 'only_neutral'
CONFIGS.append(c)

# V6: V2 + 降低波动惩罚
c = copy.deepcopy(DEFAULT_CONFIG)
c['name'] = 'V6_LowVolPenalty'
c['sector_filter'] = 'only_neutral'
c['momentum_weights'] = {'ret60': 1.0, 'vol20': 0.1, 'trend_bonus': 0.3}
CONFIGS.append(c)

# V7: V2 + 提高趋势奖励
c = copy.deepcopy(DEFAULT_CONFIG)
c['name'] = 'V7_HighTrend'
c['sector_filter'] = 'only_neutral'
c['momentum_weights'] = {'ret60': 0.8, 'vol20': 0.2, 'trend_bonus': 0.4}
CONFIGS.append(c)

# V8: 全牛市不过滤 + 动态仓位缩放
c = copy.deepcopy(DEFAULT_CONFIG)
c['name'] = 'V8_DynamicScale'
c['sector_filter'] = 'only_neutral'
c['position_scale'] = 1.0  # no scaling for now, just same as V2
CONFIGS.append(c)

# V9: 混合: 20d热度 + 牛市不过滤 + 调整仓位
c = copy.deepcopy(DEFAULT_CONFIG)
c['name'] = 'V9_Hybrid'
c['sector_filter'] = 'only_neutral'
c['heat_periods'] = [20]
c['cold_threshold'] = -5.0
c['hot_threshold'] = 3.0
c['momentum_weights'] = {'ret60': 1.0, 'vol20': 0.1, 'trend_bonus': 0.3}
CONFIGS.append(c)

# V10: 最激进 — 全不过滤+纯动量
c = copy.deepcopy(DEFAULT_CONFIG)
c['name'] = 'V10_PureMomentum'
c['sector_filter'] = 'never'
c['momentum_weights'] = {'ret60': 1.0, 'vol20': 0.0, 'trend_bonus': 0.2}
CONFIGS.append(c)


print("=" * 90)
print(f"🧬 策略优化迭代 — {len(CONFIGS)} 个版本")
print("=" * 90)
print()

results = []
for i, cfg in enumerate(CONFIGS):
    res = run_backtest(cfg)
    results.append(res)
    
    m = res['metrics']
    b = res['benchmark']
    print(f"  {cfg['name']:<20s}  comp={res['composite']:>5.1f}  "
          f"总{m['total_ret']:>+6.1f}%  年{m['annualized']:>+5.1f}%  "
          f"DD{m['max_dd']:>5.1f}%  Sr{m['sharpe']:>.2f}  "
          f"胜率{m['win_rate']:>.0f}%  α{m['alpha']:>+5.1f}%")

# Rank
print(f"\n{'='*90}")
print("🏆 排名")
print(f"{'='*90}")
results.sort(key=lambda x: x['composite'], reverse=True)
print(f"{'排名':<4s} {'版本':<20s} {'综合分':>6s} {'总收益':>7s} {'年化':>7s} {'回撤':>6s} {'夏普':>5s} {'胜率':>5s} {'超额':>7s}")
print(f"{'-'*70}")
for i, r in enumerate(results):
    m = r['metrics']
    print(f"  {i+1:<4d} {r['config']:<20s} {r['composite']:>6.1f} "
          f"{m['total_ret']:>+6.1f}% {m['annualized']:>+5.1f}% {m['max_dd']:>5.1f}% "
          f"{m['sharpe']:>.2f}  {m['win_rate']:>4.0f}% {m['alpha']:>+6.1f}%")

# Detail the winner
best = results[0]
m = best['metrics']
s = best['scores']
print(f"\n{'='*90}")
print(f"👑 最优策略: {best['config']}")
print(f"{'='*90}")
print(f"\n  综合评分: {best['composite']:.1f}/100")
print(f"\n  评分详情:")
print(f"    总收益 ({m['total_ret']:+.1f}%):  {s['total_ret']:.0f}/100")
print(f"    夏普   ({m['sharpe']:.2f}):     {s['sharpe']:.0f}/100")
print(f"    回撤   ({m['max_dd']:.1f}%):   {s['dd']:.0f}/100")
print(f"    胜率   ({m['win_rate']:.0f}%):  {s['win_rate']:.0f}/100")
print(f"    超额   ({m['alpha']:+.1f}%):   {s['alpha']:.0f}/100")
print(f"    相对回撤:                {s['relative_dd']:.0f}/100")

# Save
out = {
    'timestamp': __import__('datetime').datetime.now().isoformat(),
    'configs_tested': len(CONFIGS),
    'results': results,
    'best': best,
}
with open(f"{DATA_DIR}/optimization_results.json", 'w') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\n📁 结果 → {DATA_DIR}/optimization_results.json")
