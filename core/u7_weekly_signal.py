#!/usr/bin/env python3
"""
U7_CalendarV3_H20 周度信号
  策略版本: V3_H20 (hold_days=20, bull=3, neutral=5, bear=2, min_sectors=4, 不止损)
  调度: 每周五 08:30 CST
  行为: 每周运行; 仅当 ≥20 个交易日距上次换仓时生成换仓建议
  审计: 所有策略逻辑调用 u7_strategy.py (SSoT)
"""
import json, os, sys, math, hashlib, requests, time
import numpy as np
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from u7_strategy import (
    _load, detect_regime_at, select_at, sector_heatmap_at,
    U7_CALENDAR_V3_H20_CONFIG, SECTOR_RULES, etf_sector,
    DEF_GOLD, DEF_BOND, US_CODES, HK_CODES,
)

DATA_DIR = os.path.expanduser("~/.qclaw/workspace-main/data/market_regime")

STRATEGY_VERSION = "U7_CalendarV3_H20"
HOLD_DAYS = U7_CALENDAR_V3_H20_CONFIG['hold_days']
MAX_POSITIONS = U7_CALENDAR_V3_H20_CONFIG['max_positions']
MIN_SECTORS = U7_CALENDAR_V3_H20_CONFIG['min_sectors']

# ═══ Data download ═══
def download_latest(code):
    market = 'sh' if code.startswith(('51','56','58','52')) else 'sz'
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    try:
        r = requests.get(url, params={'param': f'{market}{code},day,,,5,qfq'},
                         timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code != 200: return None
        data = r.json()
        klines = data.get('data',{}).get(f'{market}{code}',{}).get('day',[]) or \
                 data.get('data',{}).get(f'{market}{code}',{}).get('qfqday',[])
        if not klines: return None
        last = klines[-1]
        return {'date': last[0], 'close': float(last[2]), 'volume': float(last[5]) if len(last)>5 else 0,
                'open': float(last[1]), 'high': float(last[3]), 'low': float(last[4])}
    except: return None

# ═══ Update combined_daily ═══
with open(f"{DATA_DIR}/combined_daily.json") as f: combined = json.load(f)
with open(f"{DATA_DIR}/etf_universe.json") as f: universe = json.load(f)

latest_date_init = combined[-1]['date']
print(f"📥 当前最新: {latest_date_init} ({len(combined[-1]['etfs'])} 只)")

all_prev_codes = sorted(combined[-1]['etfs'].keys())
print(f"   昨日覆盖: {len(all_prev_codes)} 只 (Universe: {len(universe['etfs'])} 只)")

new_row = {'date': None, 'etfs': {}}
for code in all_prev_codes:
    result = download_latest(code)
    if result:
        if new_row['date'] is None: new_row['date'] = result['date']
        if result['date'] == new_row['date']:
            new_row['etfs'][code] = {k: v for k, v in result.items() if k != 'date'}
    time.sleep(0.15)

if new_row['date'] == latest_date_init:
    print(f"   ℹ️ 数据已是最新")
else:
    combined.append(new_row)
    combined.sort(key=lambda r: r['date'])
    with open(f"{DATA_DIR}/combined_daily.json", 'w') as f:
        json.dump(combined, f, ensure_ascii=False)
    print(f"   ✅ 追加 1 天, 共 {len(combined)} 天")

# Force reload (u7_strategy uses lazy cache)
import u7_strategy
u7_strategy._combined = None
u7_strategy._universe = None
u7_strategy._dates_all = None
_combined, _universe, _dates_all = _load()
latest_idx = len(_combined) - 1
latest_date = _combined[-1]['date']

# ═══ Run strategy via SSoT ═══
r = detect_regime_at(latest_idx)
picks = select_at(latest_idx, r['regime'], r['conf'], U7_CALENDAR_V3_H20_CONFIG, ma20_ratio=r.get('ma20_ratio'))

# ═══ Helper: get close/ATR ═══
def get_close(code, idx):
    for i in range(idx, -1, -1):
        if code in _combined[i]['etfs']: return _combined[i]['etfs'][code]['close']
    return 0

def calc_atr(code, idx, period=14):
    closes = []
    for i in range(idx-period-1, idx+1):
        if 0 <= i < len(_combined) and code in _combined[i]['etfs']:
            closes.append(_combined[i]['etfs'][code]['close'])
    if len(closes) < period+1: return 0
    trs = []
    for i in range(1, len(closes)):
        trs.append(abs(closes[i] - closes[i-1]))
    return float(np.mean(trs[-period:]))

# ═══ State management ═══
STATE_FILE = f"{DATA_DIR}/u7_v3_h20_state.json"

def load_state():
    if not os.path.exists(STATE_FILE): return None
    with open(STATE_FILE) as f: return json.load(f)

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def count_trading_days_since(start_date_str):
    start_d = datetime.strptime(start_date_str, '%Y-%m-%d')
    end_d = datetime.strptime(latest_date, '%Y-%m-%d')
    return sum(1 for row in _combined if start_d < datetime.strptime(row['date'], '%Y-%m-%d') <= end_d)

def compute_sig():
    payload = STRATEGY_VERSION + latest_date
    for p in picks:
        payload += f"{p['code']}:{p['score']:.1f}"
    payload += r['regime'] + f"{r['conf']:.2f}"
    return hashlib.sha256(payload.encode()).hexdigest()[:12]

# ═══ Determine rebalance eligibility ═══
state = load_state()
last_rebalance_date = state.get('last_rebalance_date') if state else None
trading_days_since = count_trading_days_since(last_rebalance_date) if last_rebalance_date else 0
is_rebalance = (trading_days_since >= HOLD_DAYS) if last_rebalance_date else True  # first run = rebalance

sig = compute_sig()

# ═══ Position sizing ═══
TOTAL_CAPITAL = 100000
regime_alloc = {'bull': 0.90, 'neutral': 0.75, 'bear': 0.30}
max_per_position = {'bull': 0.30, 'neutral': 0.25, 'bear': 0.30}

# 方向A预警机制（来源: scripts/research/analyze_directionA.py）
# bull 体制下，站上 MA20 的 ETF 占比 < 60% → 仓位降至 1
MA20_DECLINE_THRESHOLD = 60.0
ma20_ratio = r.get('ma20_ratio')
ma20_decline_triggered = r['regime'] == 'bull' and ma20_ratio is not None and ma20_ratio < MA20_DECLINE_THRESHOLD

actual_n = len(picks)
max_deployed = TOTAL_CAPITAL * regime_alloc.get(r['regime'], 0.50)
if actual_n > 0:
    per_position = min(max_deployed / actual_n, TOTAL_CAPITAL * max_per_position.get(r['regime'], 0.25))
else:
    per_position = 0
deployed = per_position * actual_n
cash = TOTAL_CAPITAL - deployed

for p in picks:
    close = get_close(p['code'], latest_idx)
    atr = calc_atr(p['code'], latest_idx, 14)
    atr_pct = (atr / close * 100) if close > 0 else 0
    buy_low = round(close - atr * 1.0, 3)
    buy_high = round(close + atr * 0.5, 3)
    
    # V3: MA20 警示线（非止损）
    from u7_strategy import get_closes
    cls = get_closes(p['code'], latest_idx, 25)
    ma_warn = round(np.mean(cls[-20:]), 3) if len(cls) >= 20 else close
    
    if atr_pct < 1.0: risk_label = '🐢低波'
    elif atr_pct < 2.5: risk_label = '🐇中波'
    else: risk_label = '🐎高波'
    
    shares = int(per_position / close / 100) * 100 if close > 0 else 0
    actual_amount = shares * close
    
    p['close'] = close
    p['atr'] = atr
    p['atr_pct'] = atr_pct
    p['buy_low'] = buy_low
    p['buy_high'] = buy_high
    p['ma20_warn'] = ma_warn
    p['risk_label'] = risk_label
    p['shares'] = shares
    p['amount'] = actual_amount
    p['alloc_pct'] = (actual_amount / TOTAL_CAPITAL * 100) if TOTAL_CAPITAL > 0 else 0

# ═══ Output ═══
WEEKDAY = ['一','二','三','四','五','六','日']
d = datetime.strptime(latest_date, '%Y-%m-%d')
wd = WEEKDAY[d.weekday()]

regime_emoji = {'bull':'🟢','neutral':'🟡','bear':'🔴'}
re = regime_emoji.get(r['regime'],'⚪')
action_type = "🔄 换仓信号" if is_rebalance else "👀 持仓监控"

# 方向A预警状态行
ma20_warning_line = ""
if ma20_ratio is not None:
    ma20_warning_line = f"\n   MA20覆盖: {ma20_ratio:.1f}% (阈值 <{MA20_DECLINE_THRESHOLD:.0f}%)"
    if ma20_decline_triggered:
        ma20_warning_line += "  ⚠️ BULL预警激活：仓位降至1只"

signal = f"""📊 {STRATEGY_VERSION} 周度信号
━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 {latest_date} 周{wd} | {action_type}
   Universe: {len(_universe['etfs'])}只 | 签名: {sig}

🔮 市场体制: {re} {r['regime'].upper()} (置信度 {r['conf']:.2f}){ma20_warning_line}

📋 换仓状态
   上次换仓: {last_rebalance_date or '无（首次运行）'}
   距上次: {trading_days_since} 交易日 / 需 ≥{HOLD_DAYS}"""

if not is_rebalance:
    days_remaining = HOLD_DAYS - trading_days_since
    signal += f" | ⏳ 距下次换仓约 {days_remaining} 个交易日"
    
    if state and state.get('positions'):
        signal += f"\n\n💤 当前持仓 ({len(state['positions'])}只):"
        for i, pos in enumerate(state['positions'], 1):
            code = pos['code']
            current_px = get_close(code, latest_idx)
            entry = pos.get('entry_price', 0)
            ret = (current_px / entry - 1) * 100 if entry and current_px else 0
            signal += f"\n  {i}. {code} {pos['name']}\n     ¥{entry:.3f} → ¥{current_px:.3f} ({ret:+.1f}%)"
    else:
        signal += "\n\n💤 当前无持仓"
    
    signal += "\n\n📋 参考选股 (不执行):"
    for i, p in enumerate(picks[:min(3, len(picks))], 1):
        signal += f"\n  {i}. {p['code']} {_universe['etfs'][p['code']]['name']} ({p['sector']}) 得分 {p['score']:+.1f}"
    if len(picks) == 0:
        signal += "\n  无候选"
else:
    signal += " ✅ 触发换仓"
    signal += f"\n\n💰 资金分配 (总 ¥{TOTAL_CAPITAL/10000:.0f}万)"
    signal += f"\n   部署: ¥{deployed:,.0f} ({deployed/TOTAL_CAPITAL*100:.0f}%) | 现金: ¥{cash:,.0f} ({cash/TOTAL_CAPITAL*100:.0f}%)"
    
    if actual_n == 0:
        signal += "\n\n🎯 无入选标的（全仓现金/国债）"
    else:
        signal += f"\n\n🎯 换仓 ({r['regime'].upper()}·{actual_n}只)"
        for i, p in enumerate(picks, 1):
            signal += f"""

  {i}. {p['code']} {_universe['etfs'][p['code']]['name']}
     板块: {p['sector']} | 得分: {p['score']:+.1f}
     📍 现价: ¥{p['close']:.3f}  {p.get('risk_label', '')} (ATR: {p.get('atr_pct', 0):.1f}%)
     🟢 买入区间: ¥{p['buy_low']:.3f} ~ ¥{p['buy_high']:.3f}
     ⚠️ MA20: ¥{p.get('ma20_warn', 0):.3f} (跌破为自然退出信号)
     💵 仓位: ¥{p['amount']:,.0f} ({p['alloc_pct']:.0f}%) ≈ {p['shares']:,}份"""

# Strategy footer
signal += f"\n\n📐 策略: {STRATEGY_VERSION}"
signal += f"  hold={HOLD_DAYS}d"
signal += f"  bull={MAX_POSITIONS['bull']}/neutral={MAX_POSITIONS['neutral']}/bear={MAX_POSITIONS['bear']}"
signal += f"  min_sectors={MIN_SECTORS}  不止损  退出=pure_rebalance"

print(signal)
print(f"\n─── {action_type} 生成完成 ───")

# ═══ Write outputs ═══
with open(f"{DATA_DIR}/weekly_signal.txt", 'w') as f:
    f.write(signal)
print(f"📁 信号写入 {DATA_DIR}/weekly_signal.txt")

# Always save state (even 0 positions, to record regime + date)
# 换仓: positions=新入选标的, entry_price=当前价; 监控: positions=原持仓保留entry_price
if is_rebalance:
    saved_positions = [{
        'code': p['code'], 'name': _universe['etfs'][p['code']]['name'],
        'sector': p['sector'],
        'entry_price': p.get('close', 0), 'score': round(p['score'], 1),
    } for p in picks] if picks else []
else:
    prev_positions = state.get('positions_this_run', []) if state else []
    prev_map = {pp['code']: pp for pp in prev_positions}
    saved_positions = []
    for p in picks:
        orig = prev_map.get(p['code'], {})
        saved_positions.append({
            'code': p['code'],
            'name': _universe['etfs'][p['code']]['name'],
            'sector': p['sector'],
            'entry_price': orig.get('entry_price', p.get('close', 0)),  # keep original entry
            'score': round(p['score'], 1),
        })

new_state = {
    'strategy': STRATEGY_VERSION,
    'last_check_date': latest_date,
    'last_rebalance_date': latest_date if is_rebalance else (state.get('last_rebalance_date') if state else None),
    'regime': r['regime'],
    'regime_conf': round(r['conf'], 2),
    'is_rebalance': is_rebalance,
    'trading_days_since_last': trading_days_since,
    'positions_this_run': saved_positions,
    'deployed': round(deployed, 0),
    'cash': round(cash, 0),
    'signature': sig,
    'updated_at': datetime.now().isoformat(),
}
save_state(new_state)
print(f"📁 状态写入 {STATE_FILE}")
