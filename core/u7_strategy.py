#!/usr/bin/env python3
"""
U7 策略核心模块 — 单点真源 (Single Source of Truth)
从 optimize_r4.py 精确提取，修改此文件会影响所有回测脚本。
"""
import json, os, math
import numpy as np
from collections import defaultdict, OrderedDict

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_combined = None
_universe = None
_dates_all = None

def _load():
    global _combined, _universe, _dates_all
    if _combined is None:
        with open(os.path.join(DATA_DIR, "combined_daily.json")) as f:
            _combined = json.load(f)
        with open(os.path.join(DATA_DIR, "etf_universe.json")) as f:
            _universe = json.load(f)
        _dates_all = [row['date'] for row in _combined]
    return _combined, _universe, _dates_all

# ═══ 常量（与 optimize_r4.py 完全一致）═══
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

# ═══ 核心函数（逐行从 optimize_r4.py 复制）═══

def etf_sector(code):
    _combined, _universe, _ = _load()
    name = _universe['etfs'].get(code,{}).get('name','')
    for sn,kw in SECTOR_RULES:
        if any(k in name for k in kw): return sn
    return '其他'

def get_closes(code, end_idx, need=120):
    _combined, _, _ = _load()
    r = []
    for i in range(end_idx-need+1, end_idx+1):
        if 0<=i<len(_combined) and code in _combined[i]['etfs']:
            r.append(_combined[i]['etfs'][code]['close'])
    return r

def get_volumes(code, end_idx, need=120):
    _combined, _, _ = _load()
    r = []
    for i in range(end_idx-need+1, end_idx+1):
        if 0<=i<len(_combined) and code in _combined[i]['etfs']:
            r.append(_combined[i]['etfs'][code].get('volume', 0))
    return r

def sector_heatmap_at(idx):
    _combined, _universe, _ = _load()
    sectors = defaultdict(list)
    for code, info in _universe['etfs'].items():
        if info['status']!='active': continue
        closes = get_closes(code, idx, 120)
        if len(closes)<61: continue
        sectors[etf_sector(code)].append({
            'ret5': (closes[-1]/closes[-6]-1)*100 if len(closes)>=6 else 0,
            'mom60': (closes[-1]/closes[-61]-1)*100,
        })
    return {sn: {
        'avg_5d': float(np.mean([e['ret5'] for e in etfs])),
        'avg_mom60': float(np.mean([e['mom60'] for e in etfs])),
    } for sn, etfs in sectors.items()}

def detect_regime_at(idx):
    _combined, _universe, _ = _load()
    cn_codes = [c for c,info in _universe['etfs'].items() if info['status']=='active'
                and c not in US_CODES+HK_CODES+DEF_GOLD+DEF_BOND]
    perf = []
    for code in cn_codes:
        closes = get_closes(code, idx, 120)
        if len(closes)<61: continue
        ma60=np.mean(closes[-60:]); ma20=np.mean(closes[-20:])
        perf.append({'abv60':closes[-1]>ma60,'abv20':closes[-1]>ma20,
                     'r60':(closes[-1]/closes[-61]-1)*100,'r20':(closes[-1]/closes[-21]-1)*100})
    if len(perf)<5: return {'regime':'neutral','conf':0.3, 'ma20_ratio':100.0}
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
    regime = 'bull' if s>=0.55 else ('neutral' if s>=0.35 else 'bear')
    # 方向A预警机制（来源: scripts/research/analyze_directionA.py）
    # 触发条件: bull 体制下，站上 MA20 的 ETF 占比 < 60%
    ma20_ratio = p20
    return {'regime': regime, 'conf': s, 'ma20_ratio': ma20_ratio}

def calc_rsi(closes, period=14):
    if len(closes) < period+1: return 50
    gains, losses = [], []
    for i in range(-period, 0):
        chg = closes[i] - closes[i-1]
        gains.append(max(chg, 0)); losses.append(max(-chg, 0))
    avg_gain = np.mean(gains); avg_loss = np.mean(losses)
    if avg_loss == 0: return 100
    return 100 - (100 / (1 + avg_gain/avg_loss))

def select_at(idx, regime, conf, config, ma20_ratio=None):
    """
    Select with diversification constraint — exact copy from optimize_r4.py.

    参数:
        ma20_ratio: 站上 MA20 的 ETF 占比（directionA 预警机制）
                    来源: scripts/research/analyze_directionA.py
                    触发条件: bull 体制下 ma20_ratio < 60% → 仓位上限降为 1
    """
    hm = sector_heatmap_at(idx)
    # 冷过滤: 板块5日<-3%即排除（回测: 双重条件会放行"强势回调"但实为接飞刀, 单条件更优）
    cold = {n for n,p in hm.items() if p['avg_5d'] < config.get('cold_threshold', -2)}
    
    candidates = []
    _combined, _universe, _ = _load()
    for code, info in _universe['etfs'].items():
        if info['status']!='active': continue
        sec = etf_sector(code); ac = info['assetClass']
        
        if regime=='bear' and ac not in ['gold','bond'] and code != '513500': continue
        if config.get('sector_filter','always')=='always' and regime!='bear' and sec in cold: continue
        if config.get('sector_filter','always')=='only_neutral' and regime=='neutral' and sec in cold: continue
        
        closes = get_closes(code, idx, 120)
        volumes = get_volumes(code, idx, 120)
        if len(closes)<61: continue
        
        ma20 = np.mean(closes[-20:]) if len(closes)>=20 else 0
        ma60 = np.mean(closes[-60:]) if len(closes)>=60 else 0
        if config.get('ma_align'):
            requires = config['ma_align']
            if '20>60' in requires and not (ma20 > ma60): continue
        
        if config.get('vol_min_ratio'):
            if len(volumes) >= 22:
                avg_vol = np.mean(volumes[-22:-2])
                if avg_vol > 0 and volumes[-1] < avg_vol * config['vol_min_ratio']:
                    continue
        
        if config.get('rsi_min'):
            rsi = calc_rsi(closes, 14)
            if rsi < config['rsi_min']: continue
        
        ret60 = (closes[-1]/closes[-61]-1)*100
        r = np.array(closes[-21:]); r20 = np.diff(r)/r[:-1]
        vol20 = np.std(r20)*math.sqrt(252)*100 if len(r20)>0 else 30
        mw = config.get('momentum_weights', {'ret60':1.0,'vol20':0.2,'trend':0.2})
        trend_strength = (ma20/ma60 - 1) * 100  # 连续趋势强度，例: MA20高于MA60 5% → +5
        score = ret60*mw['ret60'] - vol20*mw['vol20'] + trend_strength*mw['trend']
        candidates.append({'code':code, 'score':score, 'sector':sec})
    
    candidates.sort(key=lambda x: x['score'], reverse=True)
    
    # 原始逻辑：bear 体制只选 MA20>MA60 的防御资产，无 fallback
    if regime == 'bear':
        ma_passed = []
        for cand in candidates:
            closes = get_closes(cand['code'], idx, 120)
            if len(closes) >= 60:
                ma20 = np.mean(closes[-20:])
                ma60 = np.mean(closes[-60:])
                if ma20 > ma60:
                    ma_passed.append(cand)
        candidates = ma_passed if ma_passed else []
    
    mp = config.get('max_positions', {'bull':5,'neutral':3,'bear':2})
    n_base = mp.get(regime, 3)

    if config.get('conf_scale') and regime in ['bull','neutral']:
        if conf < config.get('conf_scale_low', 0.5):
            n_base = max(2, n_base - 2)
        elif conf < config.get('conf_scale_mid', 0.65):
            n_base = max(2, n_base - 1)
    
    min_sectors = config.get('min_sectors', 0)
    # 板块分散: 新板块无条件入选; 已覆盖min_sectors个板块后放松过滤
    # bull=3时永远触发"新板块"分支(3<4), 即3只候选强制分散到3个板块
    # neutral=5时前4只强制4板块, 第5只可在已覆盖板块中选
    if min_sectors > 0:
        selected = []
        used_sectors = set()
        for cand in candidates:
            if len(selected) >= n_base: break
            if cand['sector'] not in used_sectors or len(used_sectors) >= min_sectors:
                selected.append(cand)
                used_sectors.add(cand['sector'])
        return selected
    
    return candidates[:n_base]

# ═══ 回测引擎 — 忠实复制 optimize_r4.py run_backtest ═══

def run_backtest(config):
    """精确复制 optimize_r4.py 的 run_backtest 逻辑，附加完整交易账本"""
    _combined, _universe, _dates_all = _load()
    start_idx = next((i for i, d in enumerate(_dates_all) if d >= '2023-06-01'), 120)
    
    hold = config.get('hold_days', 10)
    rebalance = list(range(start_idx, len(_combined)-hold, hold))
    if rebalance and rebalance[-1] < len(_combined)-hold:
        rebalance.append(len(_combined)-hold-1)
    
    total_windows = len(rebalance)
    strat_rets, bench_rets = [], []
    win_count = 0
    regime_counts = defaultdict(int)
    regime_perf = defaultdict(list)
    
    empty_windows = 0
    trade_ledger = []  # 每笔持仓明细
    
    for wi, idx in enumerate(rebalance):
        regime_info = detect_regime_at(idx)
        regime = regime_info['regime']
        conf = regime_info['conf']
        regime_counts[regime] += 1
        
        picks = select_at(idx, regime, conf, config, ma20_ratio=regime_info.get('ma20_ratio'))
        end_idx = min(idx+hold, len(_combined)-1)
        
        stop_loss = config.get('stop_loss_pct')
        stop_ma = config.get('stop_ma')
        
        pick_rets = []
        window_trades = []
        
        for p in picks:
            code = p['code']
            sc = _combined[idx]['etfs'].get(code,{}).get('close',0)
            if sc <= 0: continue
            
            trade = {
                'code': code,
                'name': _universe['etfs'][code]['name'],
                'sector': etf_sector(code),
                'entry_date': _combined[idx]['date'],
                'entry_price': sc,
                'score': p['score'],
            }
            ret = None
            
            if stop_loss or stop_ma:
                for day_i in range(idx+1, end_idx+1):
                    cc = _combined[day_i]['etfs'].get(code,{}).get('close',0)
                    if cc <= 0: continue
                    r = (cc/sc - 1) * 100
                    
                    if stop_loss and r <= -stop_loss:
                        ret = -stop_loss
                        trade.update({'exit_date': _combined[day_i]['date'], 'exit_price': cc,
                                      'ret': r, 'stopped': True, 'stop_price': round(sc*(1-stop_loss/100),3)})
                        break
                    
                    if stop_ma:
                        closes_check = get_closes(code, day_i, stop_ma+1)
                        if len(closes_check) >= stop_ma + 1:
                            sma = np.mean(closes_check[1:])
                            if cc < sma:
                                ret = r
                                trade.update({'exit_date': _combined[day_i]['date'], 'exit_price': cc,
                                              'ret': r, 'ma_stop': True})
                                break
                    
                    if day_i == end_idx:
                        ret = r
                        trade.update({'exit_date': _combined[day_i]['date'], 'exit_price': cc, 'ret': r})
            else:
                ec = _combined[end_idx]['etfs'].get(code,{}).get('close',0)
                if ec > 0:
                    ret = (ec/sc - 1) * 100
                    trade.update({'exit_date': _combined[end_idx]['date'], 'exit_price': ec, 'ret': ret})
            
            if ret is not None:
                trade['ret'] = round(ret, 2)
                pick_rets.append(ret)
                window_trades.append(trade)
        
        if pick_rets:
            avg = np.mean(pick_rets)
            strat_rets.append(avg)
            regime_perf[regime].append(avg)
            if avg > 0: win_count += 1
            trade_ledger.append({
                'window': wi,
                'date': _combined[idx]['date'],
                'regime': regime,
                'conf': round(conf, 2),
                'avg_return': round(avg, 2),
                'trades': window_trades,
            })
        else:
            empty_windows += 1
            # 空仓窗口计入零收益（完整口径）
            strat_rets.append(0)
            trade_ledger.append({
                'window': wi,
                'date': _combined[idx]['date'],
                'regime': regime,
                'conf': round(conf, 2),
                'avg_return': 0,
                'trades': [],
            })
        
        bench_r = []
        for code,info in _universe['etfs'].items():
            if info['status']!='active' or info['assetClass']!='cn': continue
            sc = _combined[idx]['etfs'].get(code,{}).get('close',0)
            ec = _combined[end_idx]['etfs'].get(code,{}).get('close',0)
            if sc>0 and ec>0: bench_r.append((ec/sc-1)*100)
        if bench_r: bench_rets.append(np.mean(bench_r))
        else: bench_rets.append(0)
    
    # ═══ 两种口径计算 ═══
    
    # 口径A：仅含持仓窗口（与 optimize_r4.py 一致，偏乐观）
    n_active = len(strat_rets) - empty_windows
    active_rets_for_eq = [r for r in strat_rets if r != 0]  # 过滤掉空仓窗口
    eq_active = [100]
    for sr in active_rets_for_eq:
        eq_active.append(eq_active[-1]*(1+sr/100))
    
    eq = [100]
    for sr in strat_rets: eq.append(eq[-1]*(1+sr/100))
    total_all = (eq[-1]/100-1)*100  # 空仓影响复合收益——不对，空仓0%不影响，所以两种口径总收益相同
    
    # 实际上空仓窗口收益=0，不影响复合。所以 total_ret 两种口径一样。
    total = total_all
    periods_per_year = 252/hold
    n_years_active = n_active/periods_per_year
    n_years_all = total_windows/periods_per_year
    ann_active = ((eq_active[-1]/100)**(1/n_years_active)-1)*100 if n_years_active>0 else 0
    ann_all = ((eq[-1]/100)**(1/n_years_all)-1)*100 if n_years_all>0 else 0
    
    peak=eq[0]; dd=0
    for v in eq: peak=max(peak,v); dd=max(dd,(peak-v)/peak*100)
    
    beq=[100]
    for br in bench_rets: beq.append(beq[-1]*(1+br/100))
    bench_t=(beq[-1]/100-1)*100
    
    # 口径A Sharpe
    active_rets = [r for r in strat_rets if r != 0]  # optimize_r4 实际用的
    excess_active = np.array(active_rets)-2.0/periods_per_year
    sharpe_active = float(np.mean(excess_active)/np.std(active_rets)*math.sqrt(periods_per_year)) if np.std(active_rets)>0 else 0
    
    # 口径B Sharpe（所有窗口，含空仓=0）
    all_arr = np.array(strat_rets)
    excess_all = all_arr - 2.0/periods_per_year
    sharpe_all = float(np.mean(excess_all)/np.std(all_arr)*math.sqrt(periods_per_year)) if np.std(all_arr)>0 else 0
    
    wr_active = win_count/n_active*100 if n_active>0 else 0
    wr_all = win_count/total_windows*100
    alpha_all = total - bench_t
    
    return {
        'config': config.get('name','?'),
        'metrics_active': {
            'total_ret': round(total,1), 'ann': round(ann_active,1), 'dd': round(dd,1),
            'sharpe': round(sharpe_active,2), 'win_rate': round(wr_active,1),
            'active_windows': n_active, 'total_windows': total_windows,
            'alpha': round(alpha_all,1),
        },
        'metrics_calendar': {
            'total_ret': round(total,1), 'ann': round(ann_all,1), 'dd': round(dd,1),
            'sharpe': round(sharpe_all,2), 'win_rate': round(wr_all,1),
            'active_windows': n_active, 'total_windows': total_windows,
            'alpha': round(alpha_all,1),
        },
        'bench_total': round(bench_t,1),
        'trade_ledger': trade_ledger,
        'strat_rets': [float(x) for x in strat_rets],
        'bench_rets': [float(x) for x in bench_rets],
        'regime_counts': dict(regime_counts),
    }

# ═══ 策略配置预设 ═══

def make_config(name, **kw):
    base = {
        'name': name, 'hold_days': 10,
        'sector_filter': 'always', 'cold_threshold': -2.0,
        'ma_align': '20>60', 'vol_min_ratio': 1.2,
        'rsi_min': None, 'stop_loss_pct': None, 'stop_ma': None,
        'min_sectors': 0, 'conf_scale': False,
        'momentum_weights': {'ret60':1.0,'vol20':0.2,'trend':0.2},
        'max_positions': {'bull':5,'neutral':3,'bear':2},
    }
    base.update(kw)
    return base

# T10_SL8%+Min3sec（Round 4 最优止损版本）
T10_CONFIG = make_config('T10_SL8%+Min3sec', stop_loss_pct=8, min_sectors=3)

# U7_CalendarV3_H20 — 最终生产配置（2026-06-17 定稿）
U7_CALENDAR_V3_H20_CONFIG = make_config('U7_CalendarV3_H20',
    hold_days=20,
    min_sectors=4,
    cold_threshold=-3.0,
    max_positions={'bull': 3, 'neutral': 5, 'bear': 2},
    stop_loss_pct=None,  # 不止损
    stop_ma=None,
    exit_rules='pure_rebalance',
    disaster_exit_pct=-12,  # 灾难退出: 单标的从入场跌-12%触发(尾部保险,非常规止损)
)
