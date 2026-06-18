"""
全量选股引擎
从71只ETF中按当前regime选出最强候选池
"""
import json, os, math, sys
import numpy as np
from collections import defaultdict
from datetime import datetime

SAVE_DIR = os.path.expanduser("~/.qclaw/workspace-main/data/market_regime")

# ─── 加载数据 ───
with open(os.path.join(SAVE_DIR, 'combined_daily.json')) as f:
    combined = json.load(f)

with open(os.path.join(SAVE_DIR, 'etf_universe.json')) as f:
    universe = json.load(f)

print("=" * 70)
print("全量选股扫描")
print(f"数据: {len(combined)}天 × {len(universe['etfs'])}只 ETF")
print("=" * 70)

# ─── 构建价格序列 ───
prices = {}  # code -> [close_prices]
dates = []   # all dates
for row in combined:
    dates.append(row['date'])
    for code, data in row['etfs'].items():
        if code not in prices:
            prices[code] = []
        prices[code].append(data['close'])

# 日期索引映射
date_to_idx = {d: i for i, d in enumerate(dates)}

# ─── 辅助函数 ───
def get_series(code, end_idx, lookback):
    """获取某个ETF从 end_idx-lookback 到 end_idx 的价格序列"""
    if code not in prices:
        return None
    p = prices[code]
    start = max(0, end_idx - lookback)
    return p[start:end_idx+1]

def calc_returns(series):
    """计算日收益率序列"""
    rets = []
    for i in range(1, len(series)):
        if series[i-1] > 0:
            rets.append((series[i] - series[i-1]) / series[i-1])
    return rets

# ─── Step 1: 判定当前市场体制 ───
print("\n" + "=" * 70)
print("Step 1: 市场体制判定 (Regime Detection)")
print("=" * 70)

# 找沪深300对应的ETF或直接用指数判断
# 使用所有A股ETF的平均表现作为市场宽度
# 取最近60天

latest_idx = len(dates) - 1
print(f"最新日期: {dates[latest_idx]}")

# 计算关键指标
cn_etfs = [code for code, info in universe['etfs'].items() 
           if info['assetClass'] == 'cn' and info['status'] == 'active']

# 1. Trend: 价格 vs MA20, MA60, MA120
cn_perf = []
for code in cn_etfs:
    s = get_series(code, latest_idx, 120)
    if s and len(s) >= 60:
        cur = s[-1]
        ma20 = np.mean(s[-20:])
        ma60 = np.mean(s[-60:])
        ma120 = np.mean(s) if len(s) >= 120 else ma60
        ret_60d = (s[-1] / s[-61] - 1) * 100 if len(s) > 60 else 0
        cn_perf.append({
            'code': code,
            'name': universe['etfs'][code].get('name', code),
            'price': cur,
            'ma20': ma20,
            'ma60': ma60,
            'above_ma20': cur > ma20,
            'above_ma60': cur > ma60,
            'ret_20d': (s[-1] / s[-21] - 1) * 100 if len(s) > 20 else 0,
            'ret_60d': ret_60d,
        })

# 趋势维度
pct_above_ma60 = sum(1 for e in cn_perf if e['above_ma60']) / len(cn_perf) * 100
pct_above_ma20 = sum(1 for e in cn_perf if e['above_ma20']) / len(cn_perf) * 100
avg_ret_60 = np.mean([e['ret_60d'] for e in cn_perf])
avg_ret_20 = np.mean([e['ret_20d'] for e in cn_perf])

# 2. Breadth: ETF宽度
breadth_20 = pct_above_ma20
breadth_60 = pct_above_ma60

# 3. Volatility: 波动率
all_rets = []
for code in cn_etfs:
    s = get_series(code, latest_idx, 60)
    if s and len(s) >= 30:
        rets = calc_returns(s[-30:])
        all_rets.extend(rets)
vol_30d = np.std(all_rets) * math.sqrt(252) * 100 if all_rets else 0

# 4. Flow: 量价关系
avg_ret = np.mean([e['ret_20d'] for e in cn_perf])

# 四维评分 → regime
# trend (35%): 站上MA60 + 多头排列
trend_score = 1.0 if avg_ret_60 > 5 and pct_above_ma60 > 50 else (
    0.5 if avg_ret_60 > 0 or pct_above_ma60 > 40 else 0.0
)

# breadth (25%): ETF上涨广度
breadth_score = 1.0 if breadth_60 > 60 else (0.5 if breadth_60 > 40 else 0.0)

# volatility (20%): 越低越好
vol_score = 1.0 if vol_30d < 20 else (0.5 if vol_30d < 30 else 0.0)

# flow (20%): 量价配合
flow_score = 1.0 if avg_ret > 0 else (0.5 if avg_ret > -3 else 0.0)

total_score = trend_score * 0.35 + breadth_score * 0.25 + vol_score * 0.20 + flow_score * 0.20

if total_score >= 0.55:
    regime = 'bull'
elif total_score >= 0.35:
    regime = 'neutral'
else:
    regime = 'bear'

print(f"\n  趋势分: {trend_score:.2f}  (60d涨幅={avg_ret_60:.1f}%, 站上MA60={pct_above_ma60:.0f}%)")
print(f"  宽度分: {breadth_score:.2f}  (站上MA20={pct_above_ma20:.0f}%, MA60={pct_above_ma60:.0f}%)")
print(f"  波动分: {vol_score:.2f}  (30d年化波动={vol_30d:.1f}%)")
print(f"  量价分: {flow_score:.2f}  (20d平均涨幅={avg_ret_20:.1f}%)")
print(f"  → 总分: {total_score:.2f} → Regime: {regime.upper()}")

# ─── Step 2: 按体制选股 ───
print(f"\n{'='*70}")
print(f"Step 2: {regime.upper()} 模式选股")
print(f"{'='*70}")

def momentum_score(code, end_idx):
    """动量评分：60d涨幅×100 - 20d波动率×20 + 趋势×20"""
    s = get_series(code, end_idx, 120)
    if not s or len(s) < 61:
        return None
    
    ret_60d = (s[-1] / s[-61] - 1) * 100
    rets = calc_returns(s[-20:])
    vol_20d = np.std(rets) * math.sqrt(252) * 100 if rets else 30
    
    ma20 = np.mean(s[-20:]) if len(s) >= 20 else 0
    ma60 = np.mean(s[-60:]) if len(s) >= 60 else 0
    trend = 20 if ma20 > ma60 else 10
    
    return ret_60d * 1.0 - vol_20d * 0.2 + trend * 0.2

def rule_score(code, end_idx):
    """多因子评分：满足条件数量"""
    s = get_series(code, end_idx, 120)
    if not s or len(s) < 61:
        return None
    
    score = 0
    rets = calc_returns(s)
    
    # MA5 > MA20
    if len(s) >= 20:
        ma5 = np.mean(s[-5:])
        ma20 = np.mean(s[-20:])
        if ma5 > ma20:
            score += 1
    
    # 价格站上MA60
    if len(s) >= 60:
        ma60 = np.mean(s[-60:])
        if s[-1] > ma60:
            score += 1
    
    # RSI < 70 (不追超买)
    if len(rets) >= 14:
        up = sum(r for r in rets[-14:] if r > 0)
        down = sum(abs(r) for r in rets[-14:] if r < 0)
        rsi = 100 - 100 / (1 + up / down) if down > 0 else 100
        if rsi < 70:
            score += 1
        if 30 <= rsi <= 70:
            score += 1  # bonus for non-extreme
    
    # 波动率不高
    if len(rets) >= 20:
        vol = np.std(rets[-20:]) * math.sqrt(252)
        if vol < 0.35:
            score += 1
    
    return score

def defensive_score(code, end_idx):
    """防御评分（用于bear模式）"""
    s = get_series(code, end_idx, 120)
    if not s or len(s) < 61:
        return None
    
    ret_60d = (s[-1] / s[-61] - 1) * 100
    rets = calc_returns(s[-20:])
    vol_20d = np.std(rets) * math.sqrt(252) * 100 if rets else 30
    
    # 防御资产偏好：低波 + 正回报，给负波动的惩罚
    return ret_60d - vol_20d * 0.5  # 波动惩罚更重

# 运行选股
all_candidates = []

# 纯动量（用于对比）
momentum_candidates = []
for code in universe['etfs']:
    if universe['etfs'][code]['status'] != 'active':
        continue
    ms = momentum_score(code, latest_idx)
    if ms is not None:
        momentum_candidates.append((code, ms, universe['etfs'][code]))
momentum_candidates.sort(key=lambda x: x[1], reverse=True)

# 体制感知选股
if regime == 'bull':
    # 动量模式：top 5 + RSI过滤
    candidates = []
    for code in universe['etfs']:
        if universe['etfs'][code]['status'] != 'active':
            continue
        ms = momentum_score(code, latest_idx)
        if ms is not None:
            # RSI检查
            s = get_series(code, latest_idx, 20)
            if s:
                rets = calc_returns(s)
                if len(rets) >= 14:
                    up = sum(r for r in rets[-14:] if r > 0)
                    down = sum(abs(r) for r in rets[-14:] if r < 0)
                    rsi = 100 - 100 / (1 + up / down) if down > 0 else 100
                    if rsi < 75:  # 不过滤超买时也会选，但标记
                        candidates.append((code, ms, universe['etfs'][code], rsi))
    
    candidates.sort(key=lambda x: x[1], reverse=True)
    all_candidates = candidates[:10]  # Top 10
    mode = '动量模式 (Momentum)'
    max_positions = 5

elif regime == 'neutral':
    # 规则模式：满足多个条件
    candidates = []
    for code in universe['etfs']:
        if universe['etfs'][code]['status'] != 'active':
            continue
        rs = rule_score(code, latest_idx)
        if rs is not None and rs >= 2:  # 至少满足2个条件
            # 叠加动量看质量
            ms = momentum_score(code, latest_idx)
            candidates.append((code, rs, ms, universe['etfs'][code]))
    
    candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
    all_candidates = [(c[0], c[2], c[3]) for c in candidates[:10]]
    mode = '规则模式 (Multi-Factor)'
    max_positions = 3

else:  # bear
    # 防御模式：黄金/债券/防御性ETF
    defensive_classes = {'gold', 'bond'}
    candidates = []
    for code in universe['etfs']:
        info = universe['etfs'][code]
        if info['status'] != 'active':
            continue
        if info['assetClass'] in defensive_classes:
            ds = defensive_score(code, latest_idx)
            if ds is not None:
                candidates.append((code, ds, info))
    
    candidates.sort(key=lambda x: x[1], reverse=True)
    all_candidates = candidates[:5]
    mode = '防御模式 (Defensive)'
    max_positions = 2

# ─── Step 3: 输出结果 ───
print(f"\n  选择模式: {mode}")
print(f"  最大持仓: {max_positions} 只")
print(f"\n  {'排名':<4s} {'代码':<8s} {'名称':<18s} {'类型':<6s} {'得分':>8s} {'60d':>8s} {'20d':>8s} {'信号':<6s}")
print(f"  {'-'*70}")

for rank, item in enumerate(all_candidates[:10]):
    code = item[0]
    score = item[1]
    info = item[2]
    
    # 计算额外指标
    s = get_series(code, latest_idx, 120)
    ret_60d = (s[-1] / s[-61] - 1) * 100 if s and len(s) > 60 else 0
    ret_20d = (s[-1] / s[-21] - 1) * 100 if s and len(s) > 20 else 0
    
    # 信号
    if regime == 'bear':
        signal = '🛡️ 防御'
    elif ret_60d > 10:
        signal = '🔥 强势'
    elif ret_60d > 0:
        signal = '📈 观望'
    elif ret_60d > -5:
        signal = '⚠️ 谨慎'
    else:
        signal = '🛑 回避'
    
    class_emoji = {'us': '🇺🇸', 'hk': '🇭🇰', 'gold': '🥇', 'bond': '📜', 'commodity': '🛢️', 'cn': '🇨🇳'}
    
    print(f"  {rank+1:<4d} {code:<8s} {info.get('name', code):<18s} {class_emoji.get(info['assetClass'],'')}{info['assetClass']:<4s} {score:>+8.1f} {ret_60d:>+7.1f}% {ret_20d:>+7.1f}% {signal:<6s}")

# ─── 纯动量Top10对比 ───
print(f"\n{'='*70}")
print(f"对照：纯动量 Top 10（无 regime 感知）")
print(f"{'='*70}")
print(f"  {'排名':<4s} {'代码':<8s} {'名称':<18s} {'类型':<6s} {'得分':>8s} {'60d':>8s}")
print(f"  {'-'*58}")

for rank, (code, score, info) in enumerate(momentum_candidates[:10]):
    s = get_series(code, latest_idx, 60)
    ret_60d = (s[-1] / s[-61] - 1) * 100 if s and len(s) > 60 else 0
    class_emoji = {'us': '🇺🇸', 'hk': '🇭🇰', 'gold': '🥇', 'bond': '📜', 'commodity': '🛢️', 'cn': '🇨🇳'}
    print(f"  {rank+1:<4d} {code:<8s} {info.get('name', code):<18s} {class_emoji.get(info['assetClass'],'')}{info['assetClass']:<4s} {score:>+8.1f} {ret_60d:>+7.1f}%")

# ─── 保存选股结果 ───
signal_data = {
    'generatedAt': datetime.now().isoformat(),
    'dataDate': dates[latest_idx],
    'regime': regime,
    'regimeConfidence': round(total_score, 3),
    'mode': mode,
    'maxPositions': max_positions,
    'candidates': [],
    'momentumTop10': [],
}

for rank, item in enumerate(all_candidates[:10]):
    code = item[0]
    info = item[2]
    s = get_series(code, latest_idx, 120)
    ret_60d = (s[-1] / s[-61] - 1) * 100 if s and len(s) > 60 else 0
    ret_20d = (s[-1] / s[-21] - 1) * 100 if s and len(s) > 20 else 0
    
    signal_data['candidates'].append({
        'rank': rank + 1,
        'code': code,
        'name': info.get('name', code),
        'assetClass': info['assetClass'],
        'score': round(item[1], 2),
        'ret60d': round(ret_60d, 2),
        'ret20d': round(ret_20d, 2),
    })

for rank, (code, score, info) in enumerate(momentum_candidates[:10]):
    signal_data['momentumTop10'].append({
        'rank': rank + 1,
        'code': code,
        'name': info.get('name', code),
        'assetClass': info['assetClass'],
        'score': round(score, 2),
    })

out_path = os.path.join(SAVE_DIR, 'selector_results.json')
with open(out_path, 'w') as f:
    json.dump(signal_data, f, ensure_ascii=False, indent=2)

print(f"\n✅ 选股结果 → {out_path}")
