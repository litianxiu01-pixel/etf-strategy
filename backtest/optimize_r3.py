#!/usr/bin/env python3
"""
Round 3: 量能确认 + 最低RSI + 动态仓位 + 止损
Target: 夏普≥1.0 | 胜率≥70% | 跑赢基准
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

def get_volumes(code, end_idx, need=120):
    r = []
    for i in range(end_idx-need+1, end_idx+1):
        if 0<=i<len(combined) and code in combined[i]['etfs']:
            r.append(combined[i]['etfs'][code].get('volume', 0))
    return r

def calc_rsi(closes, period=14):
    if len(closes) < period+1: return 50
    gains, losses = [], []
    for i in range(-period, 0):
        chg = closes[i] - closes[i-1]
        gains.append(max(chg, 0)); losses.append(max(-chg, 0))
    avg_gain = np.mean(gains); avg_loss = np.mean(losses)
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
    return {sn: {'avg_5d': float(np.mean([e['ret5'] for e in etfs])),
                 'avg_20d': float(np.mean([e['ret20'] for e in etfs]))} for sn, etfs in sectors.items()}

def detect_regime_at(idx):
    cn_codes = [c for c,info in universe['etfs'].items() if info['status']=='active'
                and c not in US_CODES+HK_CODES+DEF_GOLD+DEF_BOND]
    perf = []
    for code in cn_codes:
        closes = get_closes(code, idx, 120)
        if len(closes)<61: continue
        ma60=np.mean(closes[-60:]); ma20=np.mean(closes[-20:])
        perf.append({'abv60':closes[-1]>ma60,'abv20':closes[-1]>ma20,
                     'r60':(closes[-1]/closes[-61]-1)*100,'r20':(closes[-1]/closes[-21]-1)*100})
    if len(perf)<5: return {'regime':'neutral','conf':0.3}
    p60=sum(e['abv60'] for e in perf)/len(perf)*100; ar60=np.mean([e['r60'] for e in perf])
    p20=sum(e['abv20'] for e in perf)/len(perf)*100; ar20=np.mean([e['r20'] for e in perf])
    all_rets=[]
    for code in cn_codes:
        closes=get_closes(code,idx,30)
        if len(closes)>=14: r=np.diff(closes)/closes[:-1]; all_rets.extend(r)
    vol=np.std(all_rets)*math.sqrt(252)*100 if all_rets else 30
    trend=1.0 if ar60>5 and p60>50 else (0.5 if ar60>0 or p60>40 else 0.0)
    breadth=1.0 if p20>60 else (0.5 if p20>40 else 0.0)
    vol_s=1.0 if vol<20 else (0.5 if vol<30 else 0.0)
    flow=1.0 if ar20>0 else (0.5 if ar20>-3 else 0.0)
    s=trend*0.35+breadth*0.25+vol_s*0.20+flow*0.20
    return {'regime':'bull' if s>=0.55 else ('neutral' if s>=0.35 else 'bear'), 'conf':s}

def select_at(idx, regime, conf, config):
    hm = sector_heatmap_at(idx)
    cold = {n for n,p in hm.items() if p['avg_5d'] < config.get('cold_threshold', -2)}
    
    candidates = []
    for code, info in universe['etfs'].items():
        if info['status']!='active': continue
        sec = etf_sector(code); ac = info['assetClass']
        
        if regime=='bear' and ac not in ['gold','bond']: continue
        sf = config.get('sector_filter','always')
        if sf=='always' and regime!='bear' and sec in cold: continue
        if sf=='only_neutral' and regime=='neutral' and sec in cold: continue
        
        closes = get_closes(code, idx, 120)
        volumes = get_volumes(code, idx, 120)
        if len(closes)<61: continue
        
        # === RSI min (排除超卖) ===
        if config.get('rsi_min'):
            rsi = calc_rsi(closes, 14)
            if rsi < config['rsi_min']: continue
        
        # === RSI max (排除超买) ===
        if config.get('rsi_max'):
            rsi = calc_rsi(closes, 14)
            if rsi > config['rsi_max']: continue
        
        # === Volume confirmation ===
        if config.get('vol_min_ratio'):
            if len(volumes) >= 22:
                avg_vol_20 = np.mean(volumes[-22:-2])  # exclude last 2 days
                if avg_vol_20 > 0 and volumes[-1] < avg_vol_20 * config['vol_min_ratio']:
                    continue
        
        # === MA alignment ===
        if config.get('ma_align'):
            ma5 = np.mean(closes[-5:]) if len(closes)>=5 else 0
            ma20 = np.mean(closes[-20:]) if len(closes)>=20 else 0
            ma60 = np.mean(closes[-60:]) if len(closes)>=60 else 0
            requires = config['ma_align']
            ok = True
            if '5>20' in requires and not (ma5 > ma20): ok = False
            if '20>60' in requires and not (ma20 > ma60): ok = False
            if not ok: continue
        
        # === Momentum score ===
        ret60 = (closes[-1]/closes[-61]-1)*100
        r = np.array(closes[-21:]); r20 = np.diff(r)/r[:-1]
        vol20 = np.std(r20)*math.sqrt(252)*100 if len(r20)>0 else 30
        ma20 = np.mean(closes[-20:]) if len(closes)>=20 else 0
        ma60 = np.mean(closes[-60:]) if len(closes)>=60 else 0
        
        mw = config.get('momentum_weights', {'ret60':1.0,'vol20':0.2,'trend':0.2})
        score = ret60*mw['ret60'] - vol20*mw['vol20'] + (20 if ma20>ma60 else 10)*mw['trend']
        candidates.append({'code':code, 'score':score, 'sector':sec})
    
    candidates.sort(key=lambda x: x['score'], reverse=True)
    
    # === Dynamic position sizing ===
    mp = config.get('max_positions', {'bull':5,'neutral':3,'bear':2})
    n = mp.get(regime, 3)
    if config.get('dynamic_positions'):
        # Scale by regime confidence
        n = max(2, int(n * (0.6 + conf))) if regime in ['bull','neutral'] else n
    
    return candidates[:n]

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
        regime_info = detect_regime_at(idx)
        regime = regime_info['regime']
        conf = regime_info['conf']
        regime_counts[regime] += 1
        
        picks = select_at(idx, regime, conf, config)
        end_idx = min(idx+hold, len(combined)-1)
        
        pick_rets = []
        for p in picks:
            sc = combined[idx]['etfs'].get(p['code'],{}).get('close',0)
            ec = combined[end_idx]['etfs'].get(p['code'],{}).get('close',0)
            if sc>0 and ec>0:
                ret = (ec/sc-1)*100
                pick_rets.append(ret)
        
        if pick_rets:
            avg = np.mean(pick_rets)
            strat_rets.append(avg)
            regime_perf[regime].append(avg)
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
    if n < 10: return {'composite':0}
    
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
    
    wr = win_count/n*100; alpha = total - bench_t
    
    def sc_tr(r): return max(0,min(100, r/50*100))
    def sc_sr(s): return max(0,min(100, s/1.2*100))
    def sc_dd(d): return max(0,min(100, (25-d)/20*100))
    def sc_wr(w): return max(0,min(100, w/75*100))
    def sc_al(a): return max(0,min(100, (a+10)/50*100))
    
    composite = (sc_tr(total)*0.15 + sc_sr(sharpe)*0.30 + sc_dd(dd)*0.15
                 + sc_wr(wr)*0.15 + sc_al(alpha)*0.10 + max(0,min(100,(25-dd+5)/20*100))*0.15)
    
    return {
        'config': config.get('name','?'),
        'composite': round(composite,1),
        'metrics': {
            'total_ret': round(total,1), 'ann': round(ann,1), 'dd': round(dd,1),
            'sharpe': round(sharpe,2), 'win_rate': round(wr,1), 'alpha': round(alpha,1),
        },
        'bench_total': round(bench_t,1),
        'trades': n,
        'regime_stats': {
            reg: {'count': regime_counts[reg], 'avg_ret': round(float(np.mean(regime_perf[reg])),2) if regime_perf[reg] else 0}
            for reg in ['bull','neutral','bear']
        },
    }


# ═══════════════════════════════════════════════════
# Config Grid — Round 3
# ═══════════════════════════════════════════════════

CONFIGS = []

def c(name, **kw):
    base = {
        'name': name, 'hold_days': 10,
        'sector_filter': 'always', 'cold_threshold': -2.0,
        'rsi_min': None, 'rsi_max': None, 'ma_align': None,
        'vol_min_ratio': None, 'dynamic_positions': False,
        'momentum_weights': {'ret60':1.0,'vol20':0.2,'trend':0.2},
        'max_positions': {'bull':5,'neutral':3,'bear':2},
    }
    base.update(kw)
    return base

# R6_MA20gt60 is our baseline from Round 2
CONFIGS.append(c('S0_R6baseline', ma_align='20>60'))

# Volume filters
CONFIGS.append(c('S1_Vol0.8x', ma_align='20>60', vol_min_ratio=0.8))
CONFIGS.append(c('S2_Vol1.0x', ma_align='20>60', vol_min_ratio=1.0))
CONFIGS.append(c('S3_Vol1.2x', ma_align='20>60', vol_min_ratio=1.2))

# Min RSI (prevent catching falling knives)
CONFIGS.append(c('S4_RSImin30', ma_align='20>60', rsi_min=30))
CONFIGS.append(c('S5_RSImin40', ma_align='20>60', rsi_min=40))

# Volume + Min RSI combos
CONFIGS.append(c('S6_Vol1.0x+RSImin30', ma_align='20>60', vol_min_ratio=1.0, rsi_min=30))
CONFIGS.append(c('S7_Vol0.8x+RSImin30', ma_align='20>60', vol_min_ratio=0.8, rsi_min=30))
CONFIGS.append(c('S8_Vol0.8x+RSImin40', ma_align='20>60', vol_min_ratio=0.8, rsi_min=40))

# Dynamic positions
CONFIGS.append(c('S9_MA20gt60+DynPos', ma_align='20>60', dynamic_positions=True))
CONFIGS.append(c('S10_Vol1.0x+DynPos', ma_align='20>60', vol_min_ratio=1.0, dynamic_positions=True))

# Trend-heavy scoring
CONFIGS.append(c('S11_Vol0.8x+Trend', ma_align='20>60', vol_min_ratio=0.8,
                  momentum_weights={'ret60':0.8,'vol20':0.2,'trend':0.4}))

# Combined best
CONFIGS.append(c('S12_Premium', ma_align='20>60', vol_min_ratio=0.8, rsi_min=30,
                  momentum_weights={'ret60':0.8,'vol20':0.15,'trend':0.35}))

# Bidirectional RSI (30-75)
CONFIGS.append(c('S13_RSI30to75', ma_align='20>60', rsi_min=30, rsi_max=75))

# Full clean pack
CONFIGS.append(c('S14_FullPack', ma_align='20>60', vol_min_ratio=0.8, rsi_min=30,
                  rsi_max=80, dynamic_positions=True))


print("═" * 94)
print("🧬 优化 Round 3 — 量能 + 最低RSI + 动态仓位")
print("═" * 94)
print(f"{'版本':<22s} {'综合':>5s} {'总收益':>7s} {'年化':>6s} {'回撤':>5s} {'夏普':>5s} {'胜率':>4s} {'超额':>7s}")
print(f"{'-'*66}")

results = []
for cfg in CONFIGS:
    res = run_backtest(cfg)
    results.append(res)
    m = res['metrics']
    stars = '⭐' if m['sharpe']>=1.0 else ('✅' if m['sharpe']>=0.8 else '  ')
    print(f"  {res['config']:<20s} {res['composite']:>5.1f} {m['total_ret']:>+6.1f}% {m['ann']:>+5.1f}% {m['dd']:>4.1f}% {m['sharpe']:>.2f}  {m['win_rate']:>3.0f}% {m['alpha']:>+6.1f}% {stars}")

# Rank
results.sort(key=lambda x: x['composite'], reverse=True)
print(f"\n{'═'*94}")
print("🏆 排名")
print(f"{'═'*94}")
for i, r in enumerate(results):
    m = r['metrics']
    print(f"  {i+1:>2}. {r['config']:<20s} {r['composite']:>5.1f}  夏普{m['sharpe']:.2f} 胜率{m['win_rate']:.0f}% α{m['alpha']:+.1f}%")

# Best
best = results[0]; m = best['metrics']
print(f"\n{'═'*94}")
print(f"👑 最优: {best['config']} (综合{best['composite']:.1f})")
print(f"{'═'*94}")
print(f"  总收益: {m['total_ret']:+.1f}% | 年化: {m['ann']:+.1f}% | 回撤: {m['dd']:.1f}%")
print(f"  夏普: {m['sharpe']:.2f} | 胜率: {m['win_rate']:.0f}% | 超额: {m['alpha']:+.1f}%  | 交易: {best['trades']}次")
for reg,stat in best['regime_stats'].items():
    print(f"  {reg.upper():8s} {stat['count']:>2d}次  平均{stat['avg_ret']:>+6.2f}%")

# Compare all rounds best
print(f"\n{'═'*94}")
print("📈 三轮进化对比")
print(f"{'═'*94}")
print(f"  Round 1 (V1月频):    夏普0.66  胜率64%  α -3.8%")
print(f"  Round 2 (R6双周):    夏普1.00  胜率69%  α+42.6%")
print(f"  Round 3 (最优):      {best['composite']:.0f}分  VS R6的82.1分")

out = {
    'timestamp': __import__('datetime').datetime.now().isoformat(),
    'round': 3,
    'results': results,
    'best': best,
}
with open(f"{DATA_DIR}/optimization_results_r3.json", 'w') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\n📁 → {DATA_DIR}/optimization_results_r3.json")
