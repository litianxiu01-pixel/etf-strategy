#!/usr/bin/env python3
"""
U7_CalendarV3_H20 生产验证器
  日级模拟 + 20bps 成本 + 流动性过滤 + production signature
  所有逻辑调用 u7_strategy.py (SSoT)
  签名不含 timestamp，可独立复算
"""
import json, os, sys, math, hashlib, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from u7_strategy import (
    U7_CALENDAR_V3_H20_CONFIG,
    _load, select_at, detect_regime_at, etf_sector,
)
from u7_v3_exit_rules import PortfolioSim

DATA_DIR = os.path.expanduser("~/.qclaw/workspace-main/data/market_regime")

print("═" * 70)
print("🔬 U7_CalendarV3_H20 生产验证")
print("═" * 70)
cfg = U7_CALENDAR_V3_H20_CONFIG
print(f"  策略: {cfg['name']}")
print(f"  参数: hold={cfg['hold_days']}d  bull={cfg['max_positions']['bull']}  neutral={cfg['max_positions']['neutral']}  bear={cfg['max_positions']['bear']}  min_sectors={cfg['min_sectors']}")
stop_desc = '否' if not cfg.get('stop_loss_pct') else f"{cfg['stop_loss_pct']}%"
print(f"  止损: {stop_desc}")

# ═══ 1. 日级模拟 (20bps 成本) ═══
print(f"\n{'─'*50}")
print("📈 日级模拟 (20bps 成本)")
t0 = time.time()
sim = PortfolioSim(cfg, cost_bps=20, 
                   ma_confirm_days=0, momentum_rank_threshold=1.0,
                   dd_cut_pct=None, dd_defensive_pct=None,
                   disaster_exit_pct=None)
result = sim.run()
elapsed = time.time() - t0
print(f"  执行: {elapsed:.1f}s")
print(f"  累计收益: {result['total_ret']:>8.1f}%")
print(f"  最大回撤: {result['dd']:>8.1f}%")
print(f"  夏普比率: {result['sharpe']:>8.2f}")
print(f"  年化收益: {result['ann_ret']:>8.1f}%")
print(f"  胜率:     {result['win_rate']:>8.1f}%")
print(f"  交易次数: {result['n_trades']:>8}")
print(f"  总费用:   ¥{result['total_cost']:>7.0f}")

disaster_pct = cfg.get('disaster_exit_pct')
disaster_result = None
if disaster_pct is not None:
    print(f"\n{'─'*50}")
    print(f"🧯 灾难退出对照 ({disaster_pct}%)")
    t0 = time.time()
    disaster_sim = PortfolioSim(cfg, cost_bps=20,
                                ma_confirm_days=0, momentum_rank_threshold=1.0,
                                dd_cut_pct=None, dd_defensive_pct=None,
                                disaster_exit_pct=disaster_pct)
    disaster_result = disaster_sim.run()
    elapsed_disaster = time.time() - t0
    print(f"  执行: {elapsed_disaster:.1f}s")
    print("  口径              累计收益     回撤   Sharpe    胜率  交易  事件")
    print(f"  {'未启用':<12} {result['total_ret']:>8.1f}% {result['dd']:>6.1f}% {result['sharpe']:>7.2f} {result['win_rate']:>6.1f}% {result['n_trades']:>5} {result['n_events']:>5}")
    print(f"  {'启用':<12} {disaster_result['total_ret']:>8.1f}% {disaster_result['dd']:>6.1f}% {disaster_result['sharpe']:>7.2f} {disaster_result['win_rate']:>6.1f}% {disaster_result['n_trades']:>5} {disaster_result['n_events']:>5}")
    print(f"  差异              {disaster_result['total_ret'] - result['total_ret']:>+7.1f}pp {disaster_result['dd'] - result['dd']:>+5.1f}pp {disaster_result['sharpe'] - result['sharpe']:>+7.2f} {disaster_result['win_rate'] - result['win_rate']:>+6.1f}pp")
    if disaster_result.get('events'):
        print("  触发明细:")
        for e in disaster_result['events']:
            if e.get('type') == 'DISASTER_EXIT':
                print(f"    {e['date']} {e['code']} {e['ret_pct']:+.2f}%")

# ═══ 2. 流动性过滤 ═══
print(f"\n{'─'*50}")
print("💧 流动性过滤 (日均成交量 ≥ ¥200万)")
_combined, _universe, _ = _load()
LIQ_THRESHOLD = 2_000_000

passed, failed = 0, 0
fail_list = []
for code in _universe['etfs']:
    if _universe['etfs'][code].get('status') != 'active':
        continue
    vols = []
    for row in _combined[-30:]:
        if code in row['etfs']:
            v = row['etfs'][code].get('volume', 0)
            if v > 0: vols.append(v)
    if len(vols) >= 15:
        avg_vol = np.mean(vols)
        if avg_vol >= LIQ_THRESHOLD:
            passed += 1
        else:
            failed += 1
            fail_list.append((code, avg_vol))

print(f"  通过: {passed}只")
print(f"  剔除: {failed}只")
if fail_list:
    fail_list.sort(key=lambda x: x[1], reverse=True)
    for code, vol in fail_list[:8]:
        name = _universe['etfs'][code]['name']
        print(f"    {code} {name}  ¥{vol/10000:.0f}万")

# ═══ 3. 窗口级回测 (from u7_strategy) ═══
print(f"\n{'─'*50}")
print("📊 窗口级回测 (SSoT)")
from u7_strategy import run_backtest
bt = run_backtest(cfg)
m_a = bt['metrics_active']
print(f"  有持仓窗口: {m_a['active_windows']}/{m_a['total_windows']}")
print(f"  累计收益:   {m_a['total_ret']:>8.1f}%")
print(f"  最大回撤:   {m_a['dd']:>8.1f}%")
print(f"  夏普比率:   {m_a['sharpe']:>8.2f}")
print(f"  胜率:       {m_a['win_rate']:>8.1f}%")
print(f"  体制分布:   {bt['regime_counts']}")

# ═══ 4. 生产签名 ═══
print(f"\n{'─'*50}")
print("🔑 生产签名")

# 计算三个输入文件的 SHA256
def sha256_file(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()

INPUT_MANIFEST = {
    'combined_daily': sha256_file(f"{DATA_DIR}/combined_daily.json"),
    'etf_universe': sha256_file(f"{DATA_DIR}/etf_universe.json"),
    'u7_strategy': sha256_file(os.path.dirname(os.path.abspath(__file__)) + '/u7_strategy.py'),
    'u7_v3_exit_rules': sha256_file(os.path.dirname(os.path.abspath(__file__)) + '/u7_v3_exit_rules.py'),
}

# 签名载体 = 输入指纹 + 交易日级指标 + 窗口级指标（不含 timestamp）
SIGNATURE_PAYLOAD = {
    'input_manifest': INPUT_MANIFEST,
    'strategy': cfg['name'],
    'params': {
        'hold_days': cfg['hold_days'],
        'min_sectors': cfg['min_sectors'],
        'cold_threshold': cfg['cold_threshold'],
        'max_positions': cfg['max_positions'],
        'stop_loss_pct': cfg.get('stop_loss_pct'),
        'disaster_exit_pct': cfg.get('disaster_exit_pct'),
        'disaster_exit_active': False,
        'exit_rules': cfg.get('exit_rules', 'pure_rebalance'),
    },
    'daily_sim': {
        'total_ret': round(result['total_ret'], 1),
        'dd': round(result['dd'], 1),
        'sharpe': round(result['sharpe'], 2),
        'ann_ret': round(result['ann_ret'], 1),
        'win_rate': round(result['win_rate'], 1),
        'n_trades': result['n_trades'],
        'total_cost': round(result['total_cost'], 0),
    },
    'disaster_exit_test': None if disaster_result is None else {
        'threshold': disaster_pct,
        'active_in_daily_sim': False,
        'base': {
            'total_ret': round(result['total_ret'], 1),
            'dd': round(result['dd'], 1),
            'sharpe': round(result['sharpe'], 2),
            'win_rate': round(result['win_rate'], 1),
            'n_events': result['n_events'],
        },
        'with_disaster': {
            'total_ret': round(disaster_result['total_ret'], 1),
            'dd': round(disaster_result['dd'], 1),
            'sharpe': round(disaster_result['sharpe'], 2),
            'win_rate': round(disaster_result['win_rate'], 1),
            'n_events': disaster_result['n_events'],
            'exit_reasons': disaster_result['exit_reasons'],
        },
        'delta': {
            'total_ret_pp': round(disaster_result['total_ret'] - result['total_ret'], 1),
            'dd_pp': round(disaster_result['dd'] - result['dd'], 1),
            'sharpe': round(disaster_result['sharpe'] - result['sharpe'], 2),
            'win_rate_pp': round(disaster_result['win_rate'] - result['win_rate'], 1),
        },
        'decision': 'not_promoted',
    },
    'window_backtest': {
        'total_ret': m_a['total_ret'],
        'dd': m_a['dd'],
        'sharpe': m_a['sharpe'],
        'win_rate': m_a['win_rate'],
    },
    'liquidity': {
        'threshold': LIQ_THRESHOLD,
        'pass': passed,
        'fail': failed,
    },
    'cost_bps': 20,
}

sig = hashlib.sha256(json.dumps(SIGNATURE_PAYLOAD, sort_keys=True).encode()).hexdigest()[:16]
print(f"  输入指纹: {hashlib.sha256(json.dumps(INPUT_MANIFEST,sort_keys=True).encode()).hexdigest()[:16]}")
print(f"  生产签名: {sig}")

# Write
with open(f"{DATA_DIR}/production_signature.json", 'w') as f:
    json.dump({'signature': sig, 'payload': SIGNATURE_PAYLOAD}, f, ensure_ascii=False, indent=2)
print(f"  📁 {DATA_DIR}/production_signature.json")

# ═══ 5. 复算验证 ═══
print(f"\n{'─'*50}")
print("✅ 自检：复算验证")
recompute = hashlib.sha256(json.dumps(SIGNATURE_PAYLOAD, sort_keys=True).encode()).hexdigest()[:16]
print(f"  存储签名: {sig}")
print(f"  复算签名: {recompute}")
print(f"  一致: {'✅' if sig == recompute else '❌'}")

# Read back freshly written file
with open(f"{DATA_DIR}/production_signature.json") as f:
    saved = json.load(f)
print(f"  文件签名: {saved['signature']}")
print(f"  读回一致: {'✅' if saved['signature'] == sig else '❌'}")

print(f"\n{'═'*70}")
print("🏁 生产验证完成")
print(f"{'═'*70}")
