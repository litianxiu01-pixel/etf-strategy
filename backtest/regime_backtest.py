"""
Regime-Aware Strategy Backtest
基于 regime_detector.js 输出，模拟三种策略模式切换的完整回测
"""
import json, os, math
import numpy as np
from collections import defaultdict
from datetime import datetime

SAVE_DIR = os.path.expanduser("~/.qclaw/workspace-main/data/market_regime")

# ─── Load Data ───
with open(os.path.join(SAVE_DIR, "combined_daily.json")) as f:
    combined = json.load(f)

# Run regime detector through Node and capture output
import subprocess, sys
result = subprocess.run(
    ["node", os.path.expanduser("~/.qclaw/workspace-main/scripts/regime_detector.js"), "--monthly"],
    capture_output=True, text=True, cwd=os.path.expanduser("~/.qclaw/workspace-main")
)
# Parse the last valid JSON line
lines = result.stdout.strip().split('\n')
# Find JSON array start
json_lines = []
in_json = False
for line in lines:
    if line.strip().startswith('['):
        in_json = True
    if in_json:
        json_lines.append(line)
monthly_regimes_raw = json.loads('\n'.join(json_lines))

# Get daily regime results
result2 = subprocess.run(
    ["node", os.path.expanduser("~/.qclaw/workspace-main/scripts/regime_detector.js")],
    capture_output=True, text=True, cwd=os.path.expanduser("~/.qclaw/workspace-main")
)
lines2 = result2.stdout.strip().split('\n')
json_lines2 = []
in_json2 = False
brace_count = 0
for line in lines2:
    if line.strip().startswith('{'):
        in_json2 = True
    if in_json2:
        json_lines2.append(line)
full_output = json.loads('\n'.join(json_lines2))
regime_daily = {}  # date -> regime
# We need to re-run without aggregation to get daily regime
# Actually let me get it from the backtest directly

# ─── Build daily regime map ───
# Use monthly regime as the dominant regime for the whole month
# This simulates using the detector at month-end for rebalancing decisions
monthly_regime = {}
for m in monthly_regimes_raw:
    monthly_regime[m['month']] = m['dominantRegime']

# ─── Prepare price data ───
# Build date-indexed price dict
prices = {}  # code -> {date: close}
dates = []
for row in combined:
    date = row['date']
    dates.append(date)
    for code, data in row['etfs'].items():
        if code not in prices:
            prices[code] = {}
        prices[code][date] = data['close']

dates.sort()

# Defensive ETF pool (for bear mode)
defensive_etfs = {'518880', '511010', '513820', '159985'}

# ─── Strategy Functions ───

def get_momentum_score(prices_series, lookback=60):
    """60-day return"""
    if len(prices_series) < lookback + 1:
        return None
    return (prices_series[-1] / prices_series[-lookback-1] - 1) * 100

def get_composite_score(prices_series):
    """Multi-factor: 60d return*100 - 20d vol*20 + trend*20"""
    if len(prices_series) < 61:
        return None
    returns = []
    for i in range(1, len(prices_series)):
        if prices_series[i-1] > 0:
            returns.append((prices_series[i] - prices_series[i-1]) / prices_series[i-1])
    
    ret60 = (prices_series[-1] / prices_series[-61] - 1) * 100
    vol20 = np.std(returns[-20:]) * math.sqrt(252) * 100 if len(returns) >= 20 else 30
    
    # Trend: MA20 vs MA60
    if len(prices_series) >= 60:
        ma20 = np.mean(prices_series[-20:])
        ma60 = np.mean(prices_series[-60:])
        trend = 20 if ma20 > ma60 else (15 if prices_series[-1] > ma60 else 5)
    else:
        trend = 10
    
    return ret60 - vol20 * 0.2 + trend * 0.2

# ─── Backtest Engine ───

def run_backtest(strategy_name, select_etfs_fn, rebalance_freq='monthly'):
    """
    select_etfs_fn(date, available_codes, prices_history) -> [(code, weight), ...]
    """
    initial_capital = 100000
    capital = initial_capital
    positions = {}  # code -> shares
    cash = initial_capital
    
    daily_values = []
    monthly_returns = []
    trades = []
    
    # Track for rebalancing
    last_rebalance_month = None
    
    for i, date in enumerate(dates):
        # Get current prices
        current_prices = {}
        for code in prices:
            if date in prices[code]:
                current_prices[code] = prices[code][date]
        
        # Calculate portfolio value
        portfolio_value = cash
        for code, shares in positions.items():
            if code in current_prices:
                portfolio_value += shares * current_prices[code]
        
        daily_values.append({'date': date, 'value': portfolio_value})
        
        # Monthly rebalancing
        month_key = date[:7]
        if month_key != last_rebalance_month:
            last_rebalance_month = month_key
            
            # Get regime for this month
            regime = monthly_regime.get(month_key, 'neutral')
            
            # Build price history up to this date
            history = {}
            for code in current_prices:
                history[code] = [prices[code][d] for d in dates[:i+1] if d in prices.get(code, {})]
            
            # Select ETFs
            selected = select_etfs_fn(date, list(current_prices.keys()), history, regime)
            
            if selected:
                # Liquidate positions not in selection
                selected_codes = {s[0] for s in selected}
                for code in list(positions.keys()):
                    if code not in selected_codes and code in current_prices:
                        cash += positions[code] * current_prices[code]
                        trades.append({'date': date, 'code': code, 'action': 'SELL', 'shares': positions[code], 'price': current_prices[code]})
                        del positions[code]
                
                # Allocate capital
                total_alloc = portfolio_value
                for code, weight in selected:
                    if code not in current_prices:
                        continue
                    target_value = total_alloc * weight
                    if code in positions:
                        current_value = positions[code] * current_prices[code]
                        diff = target_value - current_value
                        if abs(diff) > 100:  # Min trade size
                            shares_to_trade = int(diff / current_prices[code])
                            if shares_to_trade != 0:
                                cash -= shares_to_trade * current_prices[code]
                                positions[code] = positions.get(code, 0) + shares_to_trade
                                trades.append({'date': date, 'code': code, 'action': 'BUY' if shares_to_trade > 0 else 'SELL', 
                                              'shares': abs(shares_to_trade), 'price': current_prices[code]})
                    else:
                        shares_to_buy = int(target_value / current_prices[code])
                        if shares_to_buy > 0:
                            cash -= shares_to_buy * current_prices[code]
                            positions[code] = shares_to_buy
                            trades.append({'date': date, 'code': code, 'action': 'BUY', 'shares': shares_to_buy, 'price': current_prices[code]})
        
        # Last day: liquidate all
        if i == len(dates) - 1:
            for code in list(positions.keys()):
                if code in current_prices:
                    cash += positions[code] * current_prices[code]
                    del positions[code]
    
    # ─── Metrics ───
    values = [d['value'] for d in daily_values]
    peak = values[0]
    max_dd = 0
    dd_start = dd_end = None
    for i, v in enumerate(values):
        if v > peak:
            peak = v
        dd = (v - peak) / peak
        if dd < max_dd:
            max_dd = dd
    
    # Monthly returns
    monthly_vals = {}
    for d in daily_values:
        month = d['date'][:7]
        monthly_vals[month] = d['value']
    
    months_sorted = sorted(monthly_vals.keys())
    month_rets = []
    for j in range(1, len(months_sorted)):
        prev = monthly_vals[months_sorted[j-1]]
        curr = monthly_vals[months_sorted[j]]
        if prev > 0:
            month_rets.append((curr - prev) / prev)
    
    # Total return
    total_ret = (values[-1] - initial_capital) / initial_capital
    
    # Annualized return
    n_days = len(values)
    n_years = n_days / 252
    ann_ret = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0
    
    # Annualized volatility
    if len(month_rets) > 1:
        ann_vol = np.std(month_rets) * math.sqrt(12)
    else:
        ann_vol = 0
    
    # Sharpe ratio (assuming 3% risk-free)
    rf = 0.03
    sharpe = (ann_ret - rf) / ann_vol if ann_vol > 0 else 0
    
    # Calmar ratio
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0
    
    # Win rate (monthly)
    win_months = sum(1 for r in month_rets if r > 0)
    win_rate = win_months / len(month_rets) if month_rets else 0
    
    # Max consecutive losing months
    max_consec_loss = 0
    consec = 0
    for r in month_rets:
        if r < 0:
            consec += 1
            max_consec_loss = max(max_consec_loss, consec)
        else:
            consec = 0
    
    # Information ratio vs equal-weight benchmark
    # (computed later after benchmark is available)
    
    return {
        'name': strategy_name,
        'totalReturn': total_ret * 100,
        'annualizedReturn': ann_ret * 100,
        'annualizedVolatility': ann_vol * 100,
        'maxDrawdown': max_dd * 100,
        'sharpeRatio': sharpe,
        'calmarRatio': calmar,
        'winRate': win_rate * 100,
        'maxConsecutiveLosses': max_consec_loss,
        'totalMonths': len(month_rets),
        'nTrades': len(trades),
        'dailyValues': daily_values,
        'monthlyReturns': list(zip(months_sorted[1:], [r * 100 for r in month_rets])),
    }

# ─── Strategy Definitions ───

def strategy_baseline(date, codes, history, regime):
    """Equal weight all available ETFs"""
    n = len(codes)
    if n == 0:
        return []
    w = 1.0 / n
    return [(c, w) for c in codes]

def strategy_momentum(date, codes, history, regime):
    """Always momentum: top 5 by 60d return, equal weight"""
    scored = []
    for code in codes:
        if code in history and len(history[code]) >= 61:
            score = get_momentum_score(history[code])
            if score is not None:
                scored.append((code, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:5]
    if not top:
        return []
    w = 1.0 / len(top)
    return [(c, w) for c, _ in top]

def strategy_regime_aware(date, codes, history, regime):
    """Regime-aware switching"""
    if regime == 'bull':
        # Momentum: top 5
        scored = []
        for code in codes:
            if code in history and len(history[code]) >= 61:
                score = get_momentum_score(history[code])
                if score is not None:
                    scored.append((code, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:5]
        if not top:
            return []
        w = 1.0 / len(top)
        return [(c, w) for c, _ in top]
    
    elif regime == 'neutral':
        # Multi-factor: top 3
        scored = []
        for code in codes:
            if code in history and len(history[code]) >= 61:
                score = get_composite_score(history[code])
                if score is not None:
                    scored.append((code, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:3]
        if not top:
            return []
        w = 1.0 / len(top)
        return [(c, w) for c, _ in top]
    
    elif regime == 'bear':
        # Defensive: only defensive ETFs, top 2, with 50% cash
        scored = []
        for code in codes:
            if code not in defensive_etfs:
                continue
            if code in history and len(history[code]) >= 61:
                score = get_composite_score(history[code])
                if score is not None:
                    scored.append((code, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:2]
        if not top:
            return []
        w = 0.25  # 50% total / 2 ETFs = 25% each
        return [(c, w) for c, _ in top]
    
    return []

# ─── Run Backtests ───

print("=== Running Backtests ===\n")

benchmark = run_backtest("等权基准 (Buy & Hold)", strategy_baseline)
print(f"Benchmark: {benchmark['totalReturn']:.2f}% | Sharpe {benchmark['sharpeRatio']:.2f} | DD {benchmark['maxDrawdown']:.2f}%")

momentum = run_backtest("纯动量 (Always Top 5)", strategy_momentum)
print(f"Momentum:  {momentum['totalReturn']:.2f}% | Sharpe {momentum['sharpeRatio']:.2f} | DD {momentum['maxDrawdown']:.2f}%")

regime = run_backtest("Regime-Aware (四维牛熊)", strategy_regime_aware)
print(f"Regime:    {regime['totalReturn']:.2f}% | Sharpe {regime['sharpeRatio']:.2f} | DD {regime['maxDrawdown']:.2f}%")

# IR vs benchmark
benchmark_monthly = {m: v for m, v in benchmark['monthlyReturns']}
regime_monthly = {m: v for m, v in regime['monthlyReturns']}

excess_rets = []
common_months = set(benchmark_monthly.keys()) & set(regime_monthly.keys())
for m in sorted(common_months):
    excess_rets.append(regime_monthly[m] - benchmark_monthly[m])

if len(excess_rets) > 1:
    ir = (np.mean(excess_rets) / np.std(excess_rets)) * math.sqrt(12) if np.std(excess_rets) > 0 else 0
    regime['informationRatio'] = ir
    regime['excessReturn'] = regime['totalReturn'] - benchmark['totalReturn']
else:
    regime['informationRatio'] = 0
    regime['excessReturn'] = 0

momentum_monthly = {m: v for m, v in momentum['monthlyReturns']}
excess_mom = []
common_mom = set(benchmark_monthly.keys()) & set(momentum_monthly.keys())
for m in sorted(common_mom):
    excess_mom.append(momentum_monthly[m] - benchmark_monthly[m])
if len(excess_mom) > 1:
    momentum['informationRatio'] = (np.mean(excess_mom) / np.std(excess_mom)) * math.sqrt(12) if np.std(excess_mom) > 0 else 0
    momentum['excessReturn'] = momentum['totalReturn'] - benchmark['totalReturn']
else:
    momentum['informationRatio'] = 0
    momentum['excessReturn'] = 0

# ─── Yearly Breakdown ───
def yearly_breakdown(result):
    yearly = defaultdict(lambda: {'ret': 0, 'months': 0})
    for m, r in result['monthlyReturns']:
        yr = m[:4]
        yearly[yr]['ret'] += r
        yearly[yr]['months'] += 1
    return dict(yearly)

print("\n=== Yearly Returns ===")
for name, result in [('Benchmark', benchmark), ('Momentum', momentum), ('Regime', regime)]:
    yb = yearly_breakdown(result)
    parts = []
    for yr in sorted(yb.keys()):
        parts.append(f"{yr}: {yb[yr]['ret']:+.1f}%")
    print(f"  {name:<12s} {' | '.join(parts)}")

# ─── Output Table ───
print("\n" + "=" * 90)
print("=== COMPLETE BACKTEST TABLE ===")
print("=" * 90)

strategies = [benchmark, momentum, regime]

# Header
header = f"{'Strategy':<30s} {'Cum':>8s} {'Ann':>8s} {'Vol':>8s} {'DD':>8s} {'Sharpe':>7s} {'Calmar':>7s} {'IR':>7s} {'Win%':>6s} {'Excess':>8s} {'Trades':>7s}"
print(header)
print("-" * 90)

for s in strategies:
    ir_str = f"{s.get('informationRatio', 0):.2f}" if s.get('informationRatio') is not None else 'N/A'
    excess_str = f"{s.get('excessReturn', 0):+.1f}%" if s.get('excessReturn') is not None else 'N/A'
    print(f"{s['name']:<30s} {s['totalReturn']:>+7.1f}% {s['annualizedReturn']:>+7.1f}% {s['annualizedVolatility']:>7.1f}% {s['maxDrawdown']:>7.1f}% {s['sharpeRatio']:>6.2f} {s['calmarRatio']:>6.2f} {ir_str:>7s} {s['winRate']:>5.1f}% {excess_str:>8s} {s['nTrades']:>7d}")

print("=" * 90)

# ─── Save Results ───
output = {
    'generatedAt': datetime.now().isoformat(),
    'strategies': [
        {k: v for k, v in s.items() if k not in ['dailyValues', 'monthlyReturns']}
        for s in strategies
    ],
    'yearlyReturns': {
        'benchmark': yearly_breakdown(benchmark),
        'momentum': yearly_breakdown(momentum),
        'regime': yearly_breakdown(regime),
    }
}

out_path = os.path.join(SAVE_DIR, 'backtest_results.json')
with open(out_path, 'w') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\nResults saved to: {out_path}")
