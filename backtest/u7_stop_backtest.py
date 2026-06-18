#!/usr/bin/env python3
"""U7 backtest: flat 8% stop vs ATR dynamic stop"""
import json, math, numpy as np
from collections import Counter, defaultdict
from datetime import datetime, timedelta

DATA_DIR = "data/market_regime"

# Load data
with open(f"{DATA_DIR}/combined_daily.json") as f:
    combined = json.load(f)
with open(f"{DATA_DIR}/etf_universe.json") as f:
    universe = json.load(f)

USE_CODES = set(universe['etfs'].keys())
TOTAL_CAPITAL = 100000
REBALANCE_DAYS = 10  # every 2 weeks (U7 actual)  | 60 to stress-test stops

# ── helpers ──
def get_closes(code, end_idx, need=120):
    r = []
    for i in range(max(0, end_idx - need + 1), end_idx + 1):
        if 0 <= i < len(combined) and code in combined[i]['etfs']:
            r.append(combined[i]['etfs'][code]['close'])
    return r

def get_volumes(code, end_idx, need=120):
    r = []
    for i in range(max(0, end_idx - need + 1), end_idx + 1):
        if 0 <= i < len(combined) and code in combined[i]['etfs']:
            v = combined[i]['etfs'][code].get('volume', 0) or 0
            r.append(v)
    return r

def etf_sector(code):
    m = {
        '510050': '上证50', '510300': '沪深300', '510500': '中证500', '510880': '红利',
        '512100': '中证1000', '512480': '半导体', '512660': '军工', '512670': '国防',
        '512690': '酒', '512800': '银行', '512880': '证券', '515030': '新能源车',
        '515050': '通信', '515210': '钢铁', '515220': '煤炭', '515700': '新能车',
        '515790': '光伏', '515880': '通信', '516010': '游戏', '516020': '化工',
        '516150': '稀土', '516160': '新能源', '516550': '农业', '516970': '基建',
        '517180': '红利低波', '517200': '碳中和', '518880': '黄金', '588000': '科创50',
        '159513': '纳指100', '159605': '中概互联', '159766': '旅游', '159780': '双创50',
        '159792': '港股科技', '159915': '创业板', '159920': '恒生', '159941': '纳指',
        '159949': '创业板50', '159967': '创成长', '159980': '有色', '159985': '豆粕',
        '159996': '家电', '511010': '国债', '511260': '十年国债', '512010': '医药',
        '512290': '生物医药', '512560': '军工龙头', '512580': '碳中和', '512710': '军工龙头',
        '513050': '中概互联', '513100': '纳指100', '513500': '标普500', '515220': '煤炭',
        '515050': '通信', '516110': '汽车', '516750': '建材', '516970': '基建50',
        '517050': '科技', '517090': '央企ETF', '562500': '机器人',
        '159869': '游戏', '159892': '恒生医药',
    }
    return m.get(code, code[:6])

# ── regime detection (same as u7 script) ──
def detect_regime(idx):
    ar60s, p60s = [], []
    for code in USE_CODES:
        closes = get_closes(code, idx, 120)
        if len(closes) < 61: continue
        ar60s.append((closes[-1] / closes[-61] - 1) * 100)
        p60s.append(1 if closes[-1] > np.mean(closes[-60:]) else 0)
    if not ar60s: return {'regime': 'neutral', 'conf': 0.5}
    ar60 = np.mean(ar60s)
    p60 = sum(p60s) / len(p60s) * 100
    rets = []
    for code in USE_CODES:
        closes = get_closes(code, idx, 22)
        if len(closes) < 22: continue
        r = np.diff(np.array(closes)) / np.array(closes[:-1])
        rets.extend(r.tolist())
    vol = np.std(rets) * math.sqrt(252) * 100 if rets else 30
    score = (p60 / 100) * 0.4 + (1 if ar60 > 0 else (0.5 if ar60 > -3 else 0)) * 0.3 + (1 - min(vol / 50, 1)) * 0.3
    if score > 0.55: regime = 'bull'
    elif score > 0.35: regime = 'neutral'
    else: regime = 'bear'
    return {'regime': regime, 'conf': score, 'ar60': ar60, 'p60': p60, 'vol': vol}

# ── U7 selection ──
def u7_select(idx):
    r = detect_regime(idx)
    candidates = []
    for code in USE_CODES:
        info = universe['etfs'][code]
        ac = info.get('assetClass', '')
        if r['regime'] == 'bear' and ac in ['gold', 'bond']: continue
        closes = get_closes(code, idx, 120)
        volumes = get_volumes(code, idx, 120)
        if len(closes) < 61: continue
        ma20 = np.mean(closes[-20:])
        ma60 = np.mean(closes[-60:])
        if not (ma20 > ma60): continue
        if len(volumes) >= 22:
            avg_vol = np.mean(volumes[-22:-2])
            if avg_vol > 0 and volumes[-1] < avg_vol * 1.2: continue
        ret60 = (closes[-1] / closes[-61] - 1) * 100
        ret5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
        r20a = np.array(closes[-21:])
        r20_r = np.diff(r20a) / r20a[:-1]
        vol20 = np.std(r20_r) * math.sqrt(252) * 100 if len(r20_r) > 0 else 30
        score = ret60 - vol20 * 0.2 + (20 if ma20 > ma60 else 10) * 0.2
        candidates.append({'code': code, 'score': score, 'sector': etf_sector(code), 'ret60': ret60, 'ret5': ret5})
    candidates.sort(key=lambda x: x['score'], reverse=True)
    mp = {'bull': 5, 'neutral': 3, 'bear': 2}
    n = mp.get(r['regime'], 3)
    sel = []
    used = set()
    for c in candidates:
        if len(sel) >= n: break
        if c['sector'] not in used or len(used) >= 3:
            sel.append(c)
            used.add(c['sector'])
    # Position sizing
    regime_alloc = {'bull': 0.90, 'neutral': 0.75, 'bear': 0.30}
    max_pp = {'bull': 0.20, 'neutral': 0.25, 'bear': 0.20}
    if len(sel) > 0:
        pp = min(TOTAL_CAPITAL * regime_alloc.get(r['regime'], 0.5) / len(sel),
                 TOTAL_CAPITAL * max_pp.get(r['regime'], 0.25))
    else:
        pp = 0
    return sel, pp, r['regime']

# ── ATR calculator ──
def calc_atr_pct(code, end_idx, period=14):
    closes = get_closes(code, end_idx, period + 5)
    if len(closes) < period + 1: return 8.0  # fallback to 8%
    trs = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    atr = np.mean(trs[-period:])
    if closes[-1] == 0: return 8.0
    atr_pct = (atr / closes[-1]) * 100
    return max(3.0, min(10.0, atr_pct * 2.0))

# ── Run backtest ──
def run_backtest(stop_mode='atr'):
    """
    stop_mode: 'atr' = dynamic ATR stop, 'flat' = flat 8% stop
    """
    positions = {}  # code -> {entry_price, shares, stop_loss_pct, entry_idx}
    cash = TOTAL_CAPITAL
    equity_curve = []
    trades = []
    # Start from index where we have enough data (day 200)
    START_IDX = 200
    END_IDX = len(combined) - 1

    for idx in range(START_IDX, END_IDX + 1):
        date = combined[idx]['date']
        
        # Check stops on existing positions
        for code in list(positions.keys()):
            pos = positions[code]
            closes = get_closes(code, idx, 5)
            if len(closes) < 1: continue
            current_price = closes[-1]
            loss_pct = (current_price / pos['entry_price'] - 1) * 100
            
            if loss_pct <= -pos['stop_loss_pct']:
                # Stop hit
                proceeds = pos['shares'] * current_price
                cash += proceeds
                trades.append({
                    'code': code, 'entry_date': combined[pos['entry_idx']]['date'],
                    'exit_date': date, 'entry_price': pos['entry_price'],
                    'exit_price': current_price, 'shares': pos['shares'],
                    'return_pct': loss_pct, 'held_days': idx - pos['entry_idx'],
                    'stop_type': stop_mode
                })
                del positions[code]
        
        # Rebalance every REBALANCE_DAYS trading days
        if (idx - START_IDX) % REBALANCE_DAYS == 0:
            # Sell all current positions at market
            for code in list(positions.keys()):
                pos = positions[code]
                closes = get_closes(code, idx, 5)
                if len(closes) < 1: continue
                current_price = closes[-1]
                proceeds = pos['shares'] * current_price
                cash += proceeds
                ret = (current_price / pos['entry_price'] - 1) * 100
                trades.append({
                    'code': code, 'entry_date': combined[pos['entry_idx']]['date'],
                    'exit_date': date, 'entry_price': pos['entry_price'],
                    'exit_price': current_price, 'shares': pos['shares'],
                    'return_pct': ret, 'held_days': idx - pos['entry_idx'],
                    'stop_type': f'{stop_mode}_rebalance'
                })
                del positions[code]
            
            # Select new positions
            sel, pp, regime = u7_select(idx)
            
            # Buy
            for s in sel:
                code = s['code']
                closes = get_closes(code, idx, 5)
                if len(closes) < 1: continue
                entry_price = closes[-1]
                
                if stop_mode == 'atr':
                    sl_pct = calc_atr_pct(code, idx)
                else:
                    sl_pct = 8.0  # flat 8%
                
                shares = int(pp / entry_price / 100) * 100
                cost = shares * entry_price
                if cost <= cash and shares > 0:
                    cash -= cost
                    positions[code] = {
                        'entry_price': entry_price,
                        'shares': shares,
                        'stop_loss_pct': sl_pct,
                        'entry_idx': idx
                    }
        
        # Mark-to-market equity
        mkt_value = cash
        for code, pos in positions.items():
            closes = get_closes(code, idx, 5)
            if len(closes) > 0:
                mkt_value += pos['shares'] * closes[-1]
        equity_curve.append(mkt_value)
    
    # Close remaining positions at last price
    for code, pos in positions.items():
        closes = get_closes(code, END_IDX, 1)
        if len(closes) > 0:
            proceeds = pos['shares'] * closes[-1]
            cash += proceeds
            ret = (closes[-1] / pos['entry_price'] - 1) * 100
            trades.append({
                'code': code, 'entry_date': combined[pos['entry_idx']]['date'],
                'exit_date': combined[END_IDX]['date'], 'entry_price': pos['entry_price'],
                'exit_price': closes[-1], 'shares': pos['shares'],
                'return_pct': ret, 'held_days': END_IDX - pos['entry_idx'],
                'stop_type': f'{stop_mode}_final'
            })
    
    return equity_curve, trades

# ── Metrics ──
def calc_metrics(equity, trades):
    if not equity or len(equity) < 2: return {}
    start_val = equity[0]
    final_val = equity[-1]
    total_return = (final_val / start_val - 1) * 100
    # Daily returns
    daily_ret = np.diff(equity) / np.array(equity[:-1])
    daily_ret = daily_ret[~np.isnan(daily_ret)]
    if len(daily_ret) > 1:
        sharpe = np.mean(daily_ret) / np.std(daily_ret) * math.sqrt(252) if np.std(daily_ret) > 0 else 0
    else:
        sharpe = 0
    # Max drawdown
    peak = equity[0]
    mdd = 0
    for v in equity:
        if v > peak: peak = v
        dd = (v / peak - 1) * 100
        if dd < mdd: mdd = dd
    # Trade stats
    stops = [t for t in trades if 'stop' in t['stop_type'] and 'rebalance' not in t['stop_type']]
    rebal = [t for t in trades if 'rebalance' in t['stop_type']]
    all_trades = stops + rebal
    wins = [t for t in all_trades if t['return_pct'] > 0]
    return {
        'total_return': total_return,
        'sharpe': sharpe,
        'max_dd': mdd,
        'n_trades': len(all_trades),
        'n_stops': len(stops),
        'n_rebalance': len(rebal),
        'win_rate': len(wins) / len(all_trades) * 100 if all_trades else 0,
        'avg_return': np.mean([t['return_pct'] for t in all_trades]) if all_trades else 0,
        'avg_held': np.mean([t['held_days'] for t in all_trades]) if all_trades else 0,
    }

# ── Main ──
print("⏳ 回测中...")
print(f"   数据范围: {combined[200]['date']} → {combined[-1]['date']}")
print(f"   调仓频率: 每{REBALANCE_DAYS}个交易日\n")

eq_flat, trades_flat = run_backtest('flat')
eq_atr, trades_atr = run_backtest('atr')

m_flat = calc_metrics(eq_flat, trades_flat)
m_atr = calc_metrics(eq_atr, trades_atr)

print("=" * 60)
print(f"{'指标':<20s} {'8%硬止损':>15s} {'ATR动态':>15s}")
print("=" * 60)
print(f"{'总收益':<20s} {m_flat['total_return']:>+14.1f}% {m_atr['total_return']:>+14.1f}%")
print(f"{'夏普比率':<20s} {m_flat['sharpe']:>15.2f} {m_atr['sharpe']:>15.2f}")
print(f"{'最大回撤':<20s} {m_flat['max_dd']:>14.1f}% {m_atr['max_dd']:>14.1f}%")
print(f"{'总交易笔数':<20s} {m_flat['n_trades']:>15d} {m_atr['n_trades']:>15d}")
print(f"{'  止损触发':<20s} {m_flat['n_stops']:>15d} {m_atr['n_stops']:>15d}")
print(f"{'  调仓退出':<20s} {m_flat['n_rebalance']:>15d} {m_atr['n_rebalance']:>15d}")
print(f"{'胜率':<20s} {m_flat['win_rate']:>14.1f}% {m_atr['win_rate']:>14.1f}%")
print(f"{'平均收益/笔':<20s} {m_flat['avg_return']:>+14.2f}% {m_atr['avg_return']:>+14.2f}%")
print(f"{'平均持仓天数':<20s} {m_flat['avg_held']:>14.0f}天 {m_atr['avg_held']:>14.0f}天")
print("=" * 60)

# Stop hit analysis for ATR mode
atr_stops = [t for t in trades_atr if 'stop' in t['stop_type'] and 'rebalance' not in t['stop_type'] and 'final' not in t['stop_type']]
if atr_stops:
    print(f"\n🔍 ATR止损触发明细:")
    for t in atr_stops:
        print(f"   {t['code']} {t['entry_date']}→{t['exit_date']} {t['return_pct']:+.1f}% ({t['held_days']}天)")
    print(f"   共 {len(atr_stops)} 笔, 平均亏损 {np.mean([t['return_pct'] for t in atr_stops]):+.2f}%")

# ATR vs flat: which had fewer premature stops?
flat_stops = [t for t in trades_flat if 'stop' in t['stop_type'] and 'rebalance' not in t['stop_type'] and 'final' not in t['stop_type']]
print(f"\n📊 止损效率对比:")
print(f"   8%硬止损: {len(flat_stops)} 笔触发, 8%一刀切")
print(f"   ATR动态:  {len(atr_stops)} 笔触发, 范围3%-10%根据波动调整")
if len(atr_stops) > 0 and len(flat_stops) > 0:
    print(f"   ATR比flat {'少' if len(atr_stops) < len(flat_stops) else '多'} {abs(len(atr_stops) - len(flat_stops))} 笔止损")

# Summary card
print(f"\n🏆 结论:")
diff = m_atr['total_return'] - m_flat['total_return']
if diff > 0:
    print(f"   ATR动态止损优于8%硬止损: 收益 +{diff:.1f}%")
elif diff < 0:
    print(f"   8%硬止损优于ATR动态止损: 收益 +{-diff:.1f}%")
else:
    print(f"   两者收益相同")
print(f"   夏普差距: {m_atr['sharpe'] - m_flat['sharpe']:+.2f}")
print(f"   回撤差距: {m_atr['max_dd'] - m_flat['max_dd']:+.1f}% ({'ATR更小' if m_atr['max_dd'] > m_flat['max_dd'] else 'ATR更大' if m_atr['max_dd'] < m_flat['max_dd'] else '相同'})")
