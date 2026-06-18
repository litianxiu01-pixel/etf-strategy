#!/usr/bin/env python3
"""
Round 2: RSI过滤 + MA排列 + 双周调仓 优化引擎
目标: 夏普≥1.0 | 胜率≥70% | 跑赢基准
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
    ("半导体/芯片", ["芯片","半导体","集成电路"]), ("军工/国防",["军工","国防"]),
    ("证券/券商",["证券","券商"]), ("银行",["银行"]), ("通信/5G",["通信","5G","电信"]),
    ("医疗/医药",["医疗","医药","创新药","生物医药"]), ("新能源",["新能源","光伏","碳中和"]),
    ("电池/锂电",["电池","锂电"]), ("传媒/游戏",["传媒","游戏","影视"]),
    ("纳指(美股)",["纳指","纳斯达克"]), ("港股",["港股","恒生","港股通"]),
    ("黄金",["黄金","金"]), ("国债/债券",["国债","债券","债"]),
    ("科创50",["科创50","科创ETF"]), ("创业板50",["创业板50","创业板ETF"]),
    ("中概互联",["中概互联","中概"]), ("房地产",["房地产","地产"]),
]
DEF_GOLD = ['518880','518660','518680','159812','159830','159831','159322','159937']
DEF_BOND = ['511010','511020','511030','511260','511090','511220','511180','159649','159650','159651']
US_CODES = ['159501','159941','159696','159660','159659','159655','159632','159612','159513','513500','513100','513300','513050']
HK_CODES = ['159740','513060','513820','513090','513180','159605','159607','513130','159726','513770']

def etf_sector(code):
    name = universe['etfs'].get(code,{}).get('name','')
    for sn,kw in SECTOR_RULES:
        if any(k in name for k in kw): return sn
    return '其他'

def get_closes(code, end_idx, need=120):
    r = []
    for i in range(end_idx-need+1, end_idx+1):
        if 0<=i<len(combined) and code in combined[i]['etfs']:
            r.append(combined[i]['etfs'][code]['close'])
    return r

def calc_rsi(closes, period=14):
    if len(closes) < period+1: return 50
    gains, losses = [], []
    for i in range(-period, 0):
        chg = closes[i] - closes[i-1]
        gains.append(max(chg, 0))
        losses.append(max(-chg, 0))
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0: return 100
    return 100 - (100 / (1 + avg_gain/avg_loss))

def sector_heatmap_at(idx):
    sectors = defaultdict(list)
    for code, info in universe['etfs'].items():
        if info['status']!='active': continue
        closes = get_closes(code, idx, 120)
        if len(closes)<61: continue
        sectors[etf_sector(code)].append({
            'ret5': (closes[-1]/closes[-6]-1)*100 if len(closes)>=6 else 0,
            'ret20': (closes[-1]/closes[-21]-1)*100 if len(closes)>=21 else 0,
        })
    result = {}
    for sname, etfs in sectors.items():
        result[sname] = {
            'avg_5d': float(np.mean([e['ret5'] for e in etfs])),
            'avg_20d': float(np.mean([e['ret20'] for e in etfs])),
        }
    return result

def detect_regime_at(idx):
    cn_codes = [c for c,info in universe['etfs'].items() if info['status']=='active'
                and c not in US_CODES+HK_CODES+DEF_GOLD+DEF_BOND]
    perf = []
    for code in cn_codes:
        closes = get_closes(code, idx, 120)
        if len(closes)<61: continue
        ma60 = np.mean(closes[-60:]); ma20 = np.mean(closes[-20:])
        perf.append({'abv60': closes[-1]>ma60, 'abv20': closes[-1]>ma20,
                     'r60': (closes[-1]/closes[-61]-1)*100, 'r20': (closes[-1]/closes[-21]-1)*100})
    if len(perf)<5: return {'regime':'neutral','conf':0.3}
    p60 = sum(e['abv60'] for e in perf)/len(perf)*100
    ar60 = np.mean([e['r60'] for e in perf])
    p20 = sum(e['abv20'] for e in perf)/len(perf)*100
    ar20 = np.mean([e['r20'] for e in perf])
    all_rets = []
    for code in cn_codes:
        closes = get_closes(code, idx, 30)
        if len(closes)>=14:
            r = np.diff(closes)/closes[:-1]; all_rets.extend(r)
    vol = np.std(all_rets)*math.sqrt(252)*100 if all_rets else 30
    trend = 1.0 if ar60>5 and p60>50 else (0.5 if ar60>0 or p60>40 else 0.0)
    breadth = 1.0 if p20>60 else (0.5 if p20>40 else 0.0)
    vol_s = 1.0 if vol<20 else (0.5 if vol<30 else 0.0)
    flow = 1.0 if ar20>0 else (0.5 if ar20>-3 else 0.0)
    s = trend*0.35+breadth*0.25+vol_s*0.20+flow*0.20
    return {'regime':'bull' if s>=0.55 else ('neutral' if s>=0.35 else 'bear'), 'conf':s}

def select_at(idx, regime, config):
    hm = sector_heatmap_at(idx)
    cold = {n for n,p in hm.items() if p['avg_5d'] < config.get('cold_threshold', -2)}
    
    candidates = []
    for code, info in universe['etfs'].items():
        if info['status']!='active': continue
        sec = etf_sector(code); ac = info['assetClass']
        
        # Defense mode
        if regime=='bear' and ac not in ['gold','bond']: continue
        # Sector filter
        if config.get('sector_filter','always')=='always' and regime!='bear' and sec in cold:
            continue
        if config.get('sector_filter','always')=='only_neutral':
            if regime=='neutral' and sec in cold: continue
        # bear_only: no sector filter except in bear
        
        closes = get_closes(code, idx, 120)
        if len(closes)<61: continue
        
        # RSI filter
        if config.get('rsi_max'):
            rsi = calc_rsi(closes, 14)
            if rsi > config['rsi_max']: continue
        
        # MA alignment
        if config.get('ma_align'):
            ma5 = np.mean(closes[-5:]) if len(closes)>=5 else 0
            ma20 = np.mean(closes[-20:]) if len(closes)>=20 else 0
            ma60 = np.mean(closes[-60:]) if len(closes)>=60 else 0
            requires = config['ma_align']
            ok = True
            if '5>20' in requires and not (ma5 > ma20): ok = False
            if '20>60' in requires and not (ma20 > ma60): ok = False
            if '5>60' in requires and not (ma5 > ma60): ok = False
            if not ok: continue
        
        # Momentum score
        ret60 = (closes[-1]/closes[-61]-1)*100
        r = np.array(closes[-21:]); r20 = np.diff(r)/r[:-1]
        vol20 = np.std(r20)*math.sqrt(252)*100 if len(r20)>0 else 30
        ma20 = np.mean(closes[-20:]) if len(closes)>=20 else 0
        ma60 = np.mean(closes[-60:]) if len(closes)>=60 else 0
        trend_b = 20 if ma20>ma60 else 10
        
        mw = config.get('momentum_weights', {'ret60':1.0,'vol20':0.2,'trend':0.2})
        score = ret60*mw['ret60'] - vol20*mw['vol20'] + trend_b*mw['trend']
        candidates.append({'code':code, 'score':score, 'sector':sec})
    
    candidates.sort(key=lambda x: x['score'], reverse=True)
    mp = config.get('max_positions', {'bull':5,'neutral':3,'bear':2})
    return candidates[:mp.get(regime, 3)]


def run_backtest(config):
    hold = config.get('hold_days', 10)
    rebalance = list(range(start_idx, len(combined)-hold, hold))
    if rebalance and rebalance[-1] < len(combined)-hold:
        rebalance.append(len(combined)-hold-1)
    
    strat_rets, bench_rets = [], []
    win_count, total_trades = 0, 0
    regime_counts = defaultdict(int)
    regime_perf = defaultdict(list)
    
    for idx in rebalance:
        regime = detect_regime_at(idx)
        regime_counts[regime['regime']] += 1
        picks = select_at(idx, regime['regime'], config)
        end_idx = min(idx+hold, len(combined)-1)
        
        pick_rets = []
        for p in picks:
            sc = combined[idx]['etfs'].get(p['code'],{}).get('close',0)
            ec = combined[end_idx]['etfs'].get(p['code'],{}).get('close',0)
            if sc>0 and ec>0: pick_rets.append((ec/sc-1)*100)
        
        if pick_rets:
            avg = np.mean(pick_rets)
            strat_rets.append(avg)
            regime_perf[regime['regime']].append(avg)
            total_trades += 1
            if avg > 0: win_count += 1
        
        bench_r = []
        for code,info in universe['etfs'].items():
            if info['status']!='active' or info['assetClass']!='cn': continue
            sc = combined[idx]['etfs'].get(code,{}).get('close',0)
            ec = combined[end_idx]['etfs'].get(code,{}).get('close',0)
            if sc>0 and ec>0: bench_r.append((ec/sc-1)*100)
        if bench_r: bench_rets.append(np.mean(bench_r))
    
    n = len(strat_rets)
    if n < 10: return {'composite':0, 'config':config.get('name','?'), 'error':'too few trades'}
    
    eq = [100]; beq = [100]
    for sr,br in zip(strat_rets, bench_rets):
        eq.append(eq[-1]*(1+sr/100)); beq.append(beq[-1]*(1+br/100))
    
    total = (eq[-1]/100-1)*100; bench_t = (beq[-1]/100-1)*100
    periods_per_year = 252/hold
    n_years = n/periods_per_year
    ann = ((eq[-1]/100)**(1/n_years)-1)*100 if n_years>0 else 0
    
    peak=eq[0]; dd=0
    for v in eq: peak=max(peak,v); dd=max(dd,(peak-v)/peak*100)
    
    excess = np.array(strat_rets)-2.0/periods_per_year
    sharpe = np.mean(excess)/np.std(strat_rets)*math.sqrt(periods_per_year) if np.std(strat_rets)>0 else 0
    
    wr = win_count/total_trades*100
    alpha = total - bench_t
    
    # ─── composite score with updated targets ───
    def sc_tr(r): return max(0,min(100, r/50*100))  # 50%=100
    def sc_sr(s): return max(0,min(100, s/1.2*100))  # 1.2=100  ← raised bar
    def sc_dd(d): return max(0,min(100, (25-d)/20*100))
    def sc_wr(w): return max(0,min(100, w/75*100))  # 75%=100  ← raised bar
    def sc_al(a): return max(0,min(100, (a+10)/50*100))  # +40%=100
    def sc_rd(sd,bd): return max(0,min(100, (bd-sd+5)/20*100))
    
    composite = (sc_tr(total)*0.15 + sc_sr(sharpe)*0.30 + sc_dd(dd)*0.15
                 + sc_wr(wr)*0.15 + sc_al(alpha)*0.10 + sc_rd(dd,25)*0.15)
    
    return {
        'config': config.get('name','?'),
        'composite': round(composite,1),
        'metrics': {
            'total_ret': round(total,1), 'ann': round(ann,1), 'dd': round(dd,1),
            'sharpe': round(sharpe,2), 'win_rate': round(wr,1), 'alpha': round(alpha,1),
        },
        'benchmark': {'total_ret': round(bench_t,1)},
        'trades': total_trades,
        'regime_stats': {
            reg: {'count': regime_counts[reg], 'avg_ret': round(float(np.mean(regime_perf[reg])),2) if regime_perf[reg] else 0}
            for reg in ['bull','neutral','bear']
        },
    }


# ═══════════════════════════════════════════════════
# Config Grid
# ═══════════════════════════════════════════════════

CONFIGS = []

def c(name, **kwargs):
    base = {
        'name': name, 'hold_days': 10,
        'sector_filter': 'always', 'cold_threshold': -2.0, 'hot_threshold': 2.0,
        'rsi_max': None, 'ma_align': None,
        'momentum_weights': {'ret60':1.0,'vol20':0.2,'trend':0.2},
        'max_positions': {'bull':5,'neutral':3,'bear':2},
    }
    base.update(kwargs)
    return base

# R1: Baseline (双周, no new filters)
CONFIGS.append(c('R1_Baseline'))

# R2: RSI ≤75
CONFIGS.append(c('R2_RSI75', rsi_max=75))

# R3: RSI ≤70
CONFIGS.append(c('R3_RSI70', rsi_max=70))

# R4: RSI ≤65
CONFIGS.append(c('R4_RSI65', rsi_max=65))

# R5: MA5>MA20 only
CONFIGS.append(c('R5_MA5gt20', ma_align='5>20'))

# R6: MA20>MA60 only
CONFIGS.append(c('R6_MA20gt60', ma_align='20>60'))

# R7: Full MA alignment (5>20>60)
CONFIGS.append(c('R7_MAfull', ma_align='5>20,20>60'))

# R8: RSI≤70 + MAfull
CONFIGS.append(c('R8_RSI70+MAfull', rsi_max=70, ma_align='5>20,20>60'))

# R9: RSI≤65 + MA5>20 + reduced vol penalty
CONFIGS.append(c('R9_LowRSI+MA+LowVol', rsi_max=65, ma_align='5>20',
                  momentum_weights={'ret60':1.0,'vol20':0.1,'trend':0.3}))

# R10: RSI≤70 + bull no sector filter
CONFIGS.append(c('R10_RSI70+BullNoFilter', rsi_max=70, sector_filter='only_neutral'))

# R11: RSI≤75 + trend-heavy score
CONFIGS.append(c('R11_RSI75+Trend', rsi_max=75,
                  momentum_weights={'ret60':0.7,'vol20':0.2,'trend':0.5}))

# R12: RSI≤70 + 大仓位 (bull 8)
CONFIGS.append(c('R12_LargePos', rsi_max=70,
                  max_positions={'bull':8,'neutral':5,'bear':3}))

# R13: 全不filter + RSI≤70 + MAfull
CONFIGS.append(c('R13_NoFilter+RSI+MA', rsi_max=70, ma_align='5>20',
                  sector_filter='never'))

print("=" * 92)
print(f"🧬 优化 Round 2 — 双周调仓 + RSI/MA过滤")
print("=" * 92)
print(f"{'版本':<22s} {'综合':>5s} {'总收益':>7s} {'年化':>6s} {'回撤':>5s} {'夏普':>5s} {'胜率':>4s} {'超额':>7s} {'交易':>4s}")
print(f"{'-'*70}")

results = []
for cfg in CONFIGS:
    res = run_backtest(cfg)
    results.append(res)
    m = res['metrics']
    print(f"  {res['config']:<20s} {res['composite']:>5.1f} {m['total_ret']:>+6.1f}% {m['ann']:>+5.1f}% {m['dd']:>4.1f}% {m['sharpe']:>.2f}  {m['win_rate']:>3.0f}% {m['alpha']:>+6.1f}% {res['trades']:>4d}")

# Rank
results.sort(key=lambda x: x['composite'], reverse=True)
print(f"\n{'='*92}")
print(f"🏆 排名")
print(f"{'='*92}")
for i, r in enumerate(results[:8]):
    m = r['metrics']
    star = '⭐' if m['sharpe']>=1.0 else ('✅' if m['sharpe']>=0.7 else '  ')
    print(f"  {i+1}. {r['config']:<20s} {r['composite']:>5.1f}  "
          f"夏普{m['sharpe']:>.2f} 胜率{m['win_rate']:.0f}% α{m['alpha']:+.1f}% {star}")

# Detail best
best = results[0]
m = best['metrics']
print(f"\n{'='*92}")
print(f"👑 最优: {best['config']} (综合{best['composite']:.1f})")
print(f"{'='*92}")
print(f"  总收益: {m['total_ret']:+.1f}% | 年化: {m['ann']:+.1f}% | 回撤: {m['dd']:.1f}%")
print(f"  夏普: {m['sharpe']:.2f} | 胜率: {m['win_rate']:.0f}% | 超额: {m['alpha']:+.1f}%")
print(f"  交易: {best['trades']}次")
for reg, stat in best['regime_stats'].items():
    print(f"  {reg.upper():8s} {stat['count']:>2d}次  平均收益 {stat['avg_ret']:>+6.2f}%")
print(f"\n  参数:")
print(f"    RSI上限: {CONFIGS[[c['name'] for c in CONFIGS].index(best['config'])].get('rsi_max','无')}")
print(f"    MA排列:  {CONFIGS[[c['name'] for c in CONFIGS].index(best['config'])].get('ma_align','无')}")
print(f"    板块过滤: {CONFIGS[[c['name'] for c in CONFIGS].index(best['config'])].get('sector_filter','?')}")

# Save
out = {
    'timestamp': __import__('datetime').datetime.now().isoformat(),
    'round': 2,
    'hold_days': 10,
    'results': results,
}
with open(f"{DATA_DIR}/optimization_results_r2.json", 'w') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\n📁 → {DATA_DIR}/optimization_results_r2.json")
