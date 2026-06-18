#!/usr/bin/env python3
"""
Round 4: 止损 + 跨板块分散 + 置信度仓位
Target: 最大回撤 <15% | 夏普≥1.0 | 胜率≥65%
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

def sector_heatmap_at(idx):
    sectors = defaultdict(list)
    for code, info in universe['etfs'].items():
        if info['status']!='active': continue
        closes = get_closes(code, idx, 120)
        if len(closes)<61: continue
        sectors[etf_sector(code)].append({
            'ret5': (closes[-1]/closes[-6]-1)*100 if len(closes)>=6 else 0,
        })
    return {sn: {'avg_5d': float(np.mean([e['ret5'] for e in etfs]))} for sn, etfs in sectors.items()}

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
    """Select with diversification constraint"""
    hm = sector_heatmap_at(idx)
    cold = {n for n,p in hm.items() if p['avg_5d'] < config.get('cold_threshold', -2)}
    
    candidates = []
    for code, info in universe['etfs'].items():
        if info['status']!='active': continue
        sec = etf_sector(code); ac = info['assetClass']
        
        if regime=='bear' and ac not in ['gold','bond']: continue
        if config.get('sector_filter','always')=='always' and regime!='bear' and sec in cold: continue
        if config.get('sector_filter','always')=='only_neutral' and regime=='neutral' and sec in cold: continue
        
        closes = get_closes(code, idx, 120)
        volumes = get_volumes(code, idx, 120)
        if len(closes)<61: continue
        
        # MA alignment
        ma20 = np.mean(closes[-20:]) if len(closes)>=20 else 0
        ma60 = np.mean(closes[-60:]) if len(closes)>=60 else 0
        if config.get('ma_align'):
            requires = config['ma_align']
            if '20>60' in requires and not (ma20 > ma60): continue
        
        # Volume
        if config.get('vol_min_ratio'):
            if len(volumes) >= 22:
                avg_vol = np.mean(volumes[-22:-2])
                if avg_vol > 0 and volumes[-1] < avg_vol * config['vol_min_ratio']:
                    continue
        
        # Min RSI
        if config.get('rsi_min'):
            rsi = calc_rsi(closes, 14)
            if rsi < config['rsi_min']: continue
        
        ret60 = (closes[-1]/closes[-61]-1)*100
        r = np.array(closes[-21:]); r20 = np.diff(r)/r[:-1]
        vol20 = np.std(r20)*math.sqrt(252)*100 if len(r20)>0 else 30
        mw = config.get('momentum_weights', {'ret60':1.0,'vol20':0.2,'trend':0.2})
        score = ret60*mw['ret60'] - vol20*mw['vol20'] + (20 if ma20>ma60 else 10)*mw['trend']
        candidates.append({'code':code, 'score':score, 'sector':sec})
    
    candidates.sort(key=lambda x: x['score'], reverse=True)
    
    # Base position count
    mp = config.get('max_positions', {'bull':5,'neutral':3,'bear':2})
    n_base = mp.get(regime, 3)
    
    # Regime confidence scaling
    if config.get('conf_scale') and regime in ['bull','neutral']:
        if conf < config.get('conf_scale_low', 0.5):
            n_base = max(2, n_base - 2)  # half positions
        elif conf < config.get('conf_scale_mid', 0.65):
            n_base = max(2, n_base - 1)  # reduced
    
    # Cross-sector diversification
    min_sectors = config.get('min_sectors', 0)
    if min_sectors > 0:
        selected = []
        used_sectors = set()
        for cand in candidates:
            if len(selected) >= n_base:
                break
            # Allow if sector not yet used, or we already have all available sectors
            if cand['sector'] not in used_sectors or len(used_sectors) >= min_sectors:
                selected.append(cand)
                used_sectors.add(cand['sector'])
        return selected
    
    return candidates[:n_base]

def calc_rsi(closes, period=14):
    if len(closes) < period+1: return 50
    gains, losses = [], []
    for i in range(-period, 0):
        chg = closes[i] - closes[i-1]
        gains.append(max(chg, 0)); losses.append(max(-chg, 0))
    avg_gain = np.mean(gains); avg_loss = np.mean(losses)
    if avg_loss == 0: return 100
    return 100 - (100 / (1 + avg_gain/avg_loss))


def run_backtest(config):
    hold = 10
    rebalance = list(range(start_idx, len(combined)-hold, hold))
    if rebalance and rebalance[-1] < len(combined)-hold:
        rebalance.append(len(combined)-hold-1)
    
    strat_rets, bench_rets = [], []
    win_count = 0
    regime_counts = defaultdict(int)
    regime_perf = defaultdict(list)
    
    # For stop-loss tracking: within a hold period, track each position
    for idx in rebalance:
        regime_info = detect_regime_at(idx)
        regime = regime_info['regime']
        conf = regime_info['conf']
        regime_counts[regime] += 1
        
        picks = select_at(idx, regime, conf, config)
        end_idx = min(idx+hold, len(combined)-1)
        
        # Stop loss tracking
        stop_loss = config.get('stop_loss_pct')
        stop_ma = config.get('stop_ma')
        
        pick_rets = []
        for p in picks:
            sc = combined[idx]['etfs'].get(p['code'],{}).get('close',0)
            if sc <= 0: continue
            
            ret = None
            # Check each day within hold period for stop
            if stop_loss or stop_ma:
                for day_i in range(idx+1, end_idx+1):
                    cc = combined[day_i]['etfs'].get(p['code'],{}).get('close',0)
                    if cc <= 0: continue
                    
                    r = (cc/sc - 1) * 100
                    
                    # Stop loss trigger
                    if stop_loss and r <= -stop_loss:
                        ret = -stop_loss  # assume we exit at stop level
                        break
                    
                    # MA stop
                    if stop_ma:
                        closes_check = get_closes(p['code'], day_i, stop_ma+1)
                        if len(closes_check) >= stop_ma + 1:
                            sma = np.mean(closes_check[1:])  # exclude last
                            if cc < sma:
                                ret = r
                                break
                    
                    if day_i == end_idx:
                        ret = r
            else:
                ec = combined[end_idx]['etfs'].get(p['code'],{}).get('close',0)
                if ec > 0:
                    ret = (ec/sc - 1) * 100
            
            if ret is not None:
                pick_rets.append(ret)
        
        if pick_rets:
            avg = np.mean(pick_rets)
            strat_rets.append(avg)
            regime_perf[regime].append(avg)
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
    
    eq = [100]
    for sr in strat_rets: eq.append(eq[-1]*(1+sr/100))
    
    total = (eq[-1]/100-1)*100
    periods_per_year = 252/hold
    n_years = n/periods_per_year
    ann = ((eq[-1]/100)**(1/n_years)-1)*100 if n_years>0 else 0
    
    peak=eq[0]; dd=0
    for v in eq: peak=max(peak,v); dd=max(dd,(peak-v)/peak*100)
    
    beq = [100]
    for br in bench_rets: beq.append(beq[-1]*(1+br/100))
    bench_t = (beq[-1]/100-1)*100
    
    excess = np.array(strat_rets)-2.0/periods_per_year
    sharpe = np.mean(excess)/np.std(strat_rets)*math.sqrt(periods_per_year) if np.std(strat_rets)>0 else 0
    
    wr = win_count/n*100; alpha = total - bench_t
    
    # ─── Composite with DD heavily weighted ───
    def sc_tr(r): return max(0,min(100, r/50*100))
    def sc_sr(s): return max(0,min(100, s/1.2*100))
    def sc_dd(d): return max(0,min(100, (20-d)/15*100))  # 5%=100, 20%=0
    def sc_wr(w): return max(0,min(100, w/75*100))
    def sc_al(a): return max(0,min(100, (a+10)/50*100))
    
    composite = (sc_tr(total)*0.10 + sc_sr(sharpe)*0.25 + sc_dd(dd)*0.30
                 + sc_wr(wr)*0.15 + sc_al(alpha)*0.10 + max(0,min(100,(25-dd+5)/20*100))*0.10)
    
    return {
        'config': config.get('name','?'),
        'composite': round(composite,1),
        'metrics': {
            'total_ret': round(total,1), 'ann': round(ann,1), 'dd': round(dd,1),
            'sharpe': round(sharpe,2), 'win_rate': round(wr,1), 'alpha': round(alpha,1),
        },
        'bench_total': round(bench_t,1),
        'trades': n,
    }


# ═══════════════════════════════════════════════════
# Config Grid — Round 4
# ═══════════════════════════════════════════════════

CONFIGS = []

def c(name, **kw):
    base = {
        'name': name, 'hold_days': 10,
        'sector_filter': 'always', 'cold_threshold': -2.0,
        'ma_align': '20>60', 'vol_min_ratio': 1.2,
        'rsi_min': None,
        'stop_loss_pct': None, 'stop_ma': None,
        'min_sectors': 0, 'conf_scale': False,
        'momentum_weights': {'ret60':1.0,'vol20':0.2,'trend':0.2},
        'max_positions': {'bull':5,'neutral':3,'bear':2},
    }
    base.update(kw)
    return base

# S0: S3 baseline from Round 3
CONFIGS.append(c('T0_S3baseline'))

# ─── Stop Loss ───
CONFIGS.append(c('T1_SL5%', stop_loss_pct=5))
CONFIGS.append(c('T2_SL8%', stop_loss_pct=8))
CONFIGS.append(c('T3_SL10%', stop_loss_pct=10))
CONFIGS.append(c('T4_SL12%', stop_loss_pct=12))

# ─── MA Stop ───
CONFIGS.append(c('T5_MA20stop', stop_ma=20))
CONFIGS.append(c('T6_SL8%+MA20stop', stop_loss_pct=8, stop_ma=20))
CONFIGS.append(c('T7_SL10%+MA20stop', stop_loss_pct=10, stop_ma=20))

# ─── Cross-sector diversification ───
CONFIGS.append(c('T8_Min3sectors', min_sectors=3))
CONFIGS.append(c('T9_Min4sectors', min_sectors=4))
CONFIGS.append(c('T10_SL8%+Min3sec', stop_loss_pct=8, min_sectors=3))
CONFIGS.append(c('T11_SL10%+Min3sec', stop_loss_pct=10, min_sectors=3))

# ─── Confidence scaling ───
CONFIGS.append(c('T12_ConfScale', conf_scale=True,
                  conf_scale_low=0.5, conf_scale_mid=0.65))
CONFIGS.append(c('T13_ConfScale+SL8%', conf_scale=True,
                  conf_scale_low=0.5, conf_scale_mid=0.65, stop_loss_pct=8))

# ─── Combined shields ───
CONFIGS.append(c('T14_AllShields', stop_loss_pct=8, min_sectors=3,
                  conf_scale=True, conf_scale_low=0.5, conf_scale_mid=0.65))
CONFIGS.append(c('T15_ShieldsLite', stop_loss_pct=10, min_sectors=3,
                  conf_scale=True, conf_scale_low=0.45, conf_scale_mid=0.6))


print("═" * 96)
print("🛡️  优化 Round 4 — 止损 + 分散 + 仓位缩放")
print("═" * 96)
print(f"目标: DD<15% | 夏普≥1.0 | 胜率≥65%")
print(f"{'版本':<24s} {'综合':>5s} {'总收益':>7s} {'年化':>6s} {'回撤↓':>6s} {'夏普':>5s} {'胜率':>4s} {'超额':>7s}")
print(f"{'-'*70}")

results = []
for cfg in CONFIGS:
    res = run_backtest(cfg)
    results.append(res)
    m = res['metrics']
    dd_flag = '✅' if m['dd'] < 15 else ('⚠️' if m['dd'] < 18 else '❌')
    sr_flag = '⭐' if m['sharpe']>=1.0 else ('✅' if m['sharpe']>=0.8 else '  ')
    print(f"  {res['config']:<22s} {res['composite']:>5.1f} {m['total_ret']:>+6.1f}% {m['ann']:>+5.1f}% {m['dd']:>5.1f}%{dd_flag} {m['sharpe']:>.2f}{sr_flag}  {m['win_rate']:>3.0f}% {m['alpha']:>+6.1f}%")

results.sort(key=lambda x: x['composite'], reverse=True)
print(f"\n{'═'*96}")
print("🏆 排名 (回撤权重30%)")
print(f"{'═'*96}")
for i, r in enumerate(results):
    m = r['metrics']
    flags = ''
    if m['dd'] < 15: flags += ' 🛡️'
    if m['sharpe'] >= 1.0: flags += ' ⭐'
    print(f"  {i+1:>2}. {r['config']:<22s} {r['composite']:>5.1f}  总{m['total_ret']:>+5.1f}%  DD{m['dd']:>4.1f}%  夏普{m['sharpe']:.2f}  胜率{m['win_rate']:.0f}%{flags}")

best = results[0]
m = best['metrics']
print(f"\n👑 最优: {best['config']} (综合{best['composite']:.1f})")
print(f"   回撤: {m['dd']:.1f}% | 夏普: {m['sharpe']:.2f} | 胜率: {m['win_rate']:.0f}% | 超额: {m['alpha']:+.1f}%")

# Compare to T0
t0 = next(r for r in results if r['config']=='T0_S3baseline')
print(f"\n📊 vs S3基线:")
print(f"   回撤: {t0['metrics']['dd']:.1f}% → {m['dd']:.1f}%  ({-(t0['metrics']['dd']-m['dd']):.1f}pp)")
print(f"   夏普: {t0['metrics']['sharpe']:.2f} → {m['sharpe']:.2f}")
print(f"   收益: {t0['metrics']['total_ret']:.1f}% → {m['total_ret']:.1f}%")

out = {
    'timestamp': __import__('datetime').datetime.now().isoformat(),
    'round': 4, 'results': results, 'best': best,
}
with open(f"{DATA_DIR}/optimization_results_r4.json", 'w') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
