#!/usr/bin/env python3
"""
U7 预警 DD 偏差诊断脚本
=======================
问题: ma20_decline 预警实施后，实际回测 DD 从 9.7% 升到 14.0%
根因: 研究脚本修改 trade_ledger（不改选股），生产回测重新选股后结果不同

诊断:
  Step 1: 找出预警触发窗口，逐窗口对比模式A（无预警 bull=3）vs 模式B（有预警 bull=1）
  Step 2: 定位 DD 扩大的窗口和 ETF
  Step 3: 测试中间方案 max=2
  Step 4: 输出诊断报告
"""
import sys, os, json, math
import numpy as np
from collections import defaultdict

# 添加父目录以导入 u7_strategy
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import u7_strategy

WORKSPACE = os.path.expanduser("~/.qclaw/workspace-main")
DATA_DIR = os.path.join(WORKSPACE, "data")
RESEARCH_DIR = os.path.join(DATA_DIR, "research")

# ═══ 常量 ═══
MA20_DECLINE_THRESHOLD = 60.0  # 预警触发阈值
BASE_CONFIG = {
    'name': 'U7_CalendarV3_H20',
    'hold_days': 20,
    'sector_filter': 'always',
    'cold_threshold': -3.0,
    'ma_align': '20>60',
    'vol_min_ratio': 1.2,
    'rsi_min': None,
    'stop_loss_pct': None,
    'stop_ma': None,
    'min_sectors': 4,
    'conf_scale': False,
    'momentum_weights': {'ret60': 1.0, 'vol20': 0.2, 'trend': 0.2},
    'max_positions': {'bull': 3, 'neutral': 5, 'bear': 2},
    'exit_rules': 'pure_rebalance',
    'disaster_exit_pct': -12,
}


def compute_dd_from_rets(rets):
    """从收益率序列计算最大回撤和 DD 曲线"""
    eq = [100.0]
    for r in rets:
        eq.append(eq[-1] * (1 + r / 100))
    peak = eq[0]
    max_dd = 0.0
    dd_curve = []
    dd_peak_idx = 0
    for i, v in enumerate(eq):
        peak = max(peak, v)
        dd = (peak - v) / peak * 100
        dd_curve.append(dd)
        if dd > max_dd:
            max_dd = dd
            dd_peak_idx = i
    return max_dd, dd_curve, dd_peak_idx


def select_at_custom(idx, regime, conf, config, ma20_ratio=None, warning_max_positions=None):
    """
    自定义 select_at，支持预警时指定最大持仓数。
    
    当 warning_max_positions 不为 None 且预警触发时，
    跳过 select_at 内部的 n_base=1 硬编码，改用指定值。
    """
    hm = u7_strategy.sector_heatmap_at(idx)
    cold = {n for n, p in hm.items() if p['avg_5d'] < config.get('cold_threshold', -2)}

    candidates = []
    _combined, _universe, _ = u7_strategy._load()
    for code, info in _universe['etfs'].items():
        if info['status'] != 'active': continue
        sec = u7_strategy.etf_sector(code)
        ac = info['assetClass']
        if regime == 'bear' and ac not in ['gold', 'bond']: continue
        if config.get('sector_filter', 'always') == 'always' and regime != 'bear' and sec in cold: continue
        if config.get('sector_filter', 'only_neutral') == 'only_neutral' and regime == 'neutral' and sec in cold: continue

        closes = u7_strategy.get_closes(code, idx, 120)
        volumes = u7_strategy.get_volumes(code, idx, 120)
        if len(closes) < 61: continue

        ma20 = np.mean(closes[-20:]) if len(closes) >= 20 else 0
        ma60 = np.mean(closes[-60:]) if len(closes) >= 60 else 0
        if config.get('ma_align'):
            if '20>60' in config['ma_align'] and not (ma20 > ma60): continue
        if config.get('vol_min_ratio'):
            if len(volumes) >= 22:
                avg_vol = np.mean(volumes[-22:-2])
                if avg_vol > 0 and volumes[-1] < avg_vol * config['vol_min_ratio']: continue
        if config.get('rsi_min'):
            rsi = u7_strategy.calc_rsi(closes, 14)
            if rsi < config['rsi_min']: continue

        ret60 = (closes[-1] / closes[-61] - 1) * 100
        r = np.array(closes[-21:])
        r20 = np.diff(r) / r[:-1]
        vol20 = np.std(r20) * math.sqrt(252) * 100 if len(r20) > 0 else 30
        mw = config.get('momentum_weights', {'ret60': 1.0, 'vol20': 0.2, 'trend': 0.2})
        trend_strength = (ma20 / ma60 - 1) * 100
        score = ret60 * mw['ret60'] - vol20 * mw['vol20'] + trend_strength * mw['trend']
        candidates.append({'code': code, 'score': score, 'sector': sec})

    candidates.sort(key=lambda x: x['score'], reverse=True)

    mp = config.get('max_positions', {'bull': 5, 'neutral': 3, 'bear': 2})
    n_base = mp.get(regime, 3)

    # 预警机制：自定义 max_positions
    warning_triggered = False
    if regime == 'bull' and ma20_ratio is not None and ma20_ratio < MA20_DECLINE_THRESHOLD:
        if warning_max_positions is not None:
            n_base = warning_max_positions
        else:
            n_base = 1  # 默认行为
        warning_triggered = True

    if config.get('conf_scale') and regime in ['bull', 'neutral']:
        if conf < config.get('conf_scale_low', 0.5):
            n_base = max(2, n_base - 2)
        elif conf < config.get('conf_scale_mid', 0.65):
            n_base = max(2, n_base - 1)

    min_sectors = config.get('min_sectors', 0)
    if min_sectors > 0:
        selected = []
        used_sectors = set()
        for cand in candidates:
            if len(selected) >= n_base: break
            if cand['sector'] not in used_sectors or len(used_sectors) >= min_sectors:
                selected.append(cand)
                used_sectors.add(cand['sector'])
        return selected, warning_triggered

    return candidates[:n_base], warning_triggered


def run_backtest_custom(config_override=None, ma20_warning_mode=None, warning_max_positions=1):
    """
    自定义回测，支持三种预警模式:
      - None: 不传 ma20_ratio（无预警），bull 仓位保持原始配置
      - 'max1': 预警触发时 bull 仓位降为 1
      - 'max2': 预警触发时 bull 仓位降为 2
    """
    cfg = dict(BASE_CONFIG)
    if config_override:
        cfg.update(config_override)

    _combined, _universe, _dates_all = u7_strategy._load()
    start_idx = next((i for i, d in enumerate(_dates_all) if d >= '2023-06-01'), 120)

    hold = cfg.get('hold_days', 20)
    rebalance = list(range(start_idx, len(_combined) - hold, hold))
    if rebalance and rebalance[-1] < len(_combined) - hold:
        rebalance.append(len(_combined) - hold - 1)

    strat_rets = []
    window_details = []

    for wi, idx in enumerate(rebalance):
        regime_info = u7_strategy.detect_regime_at(idx)
        regime = regime_info['regime']
        conf = regime_info['conf']
        ma20_ratio = regime_info.get('ma20_ratio', 100.0)

        # 判断预警模式和选股
        warning_triggered = False
        if ma20_warning_mode is not None and regime == 'bull':
            effective_ma20_ratio = ma20_ratio
            wmp = warning_max_positions if ma20_ratio < MA20_DECLINE_THRESHOLD else None
            picks, warning_triggered = select_at_custom(
                idx, regime, conf, cfg,
                ma20_ratio=effective_ma20_ratio,
                warning_max_positions=wmp,
            )
        else:
            picks = u7_strategy.select_at(idx, regime, conf, cfg, ma20_ratio=None)
            warning_triggered = False
        end_idx = min(idx + hold, len(_combined) - 1)

        pick_rets = []
        trades = []

        for p in picks:
            code = p['code']
            sc = _combined[idx]['etfs'].get(code, {}).get('close', 0)
            if sc <= 0:
                continue
            ec = _combined[end_idx]['etfs'].get(code, {}).get('close', 0)
            if ec > 0:
                ret = (ec / sc - 1) * 100
                pick_rets.append(ret)
                trades.append({
                    'code': code,
                    'name': _universe['etfs'][code]['name'],
                    'sector': u7_strategy.etf_sector(code),
                    'ret': round(ret, 2),
                    'score': p['score'],
                })

        avg_ret = float(np.mean(pick_rets)) if pick_rets else 0.0
        strat_rets.append(avg_ret)

        window_details.append({
            'window': wi,
            'date': _combined[idx]['date'],
            'regime': regime,
            'conf': round(conf, 2),
            'ma20_ratio': round(ma20_ratio, 1),
            'warning_triggered': warning_triggered,
            'n_picks': len(picks),
            'picks': trades,
            'avg_return': round(avg_ret, 2),
        })

    max_dd, dd_curve, dd_peak_idx = compute_dd_from_rets(strat_rets)
    total_ret = (100 * np.prod([1 + r / 100 for r in strat_rets]) / 100 - 1) * 100

    return {
        'total_ret': round(total_ret, 1),
        'max_dd': round(max_dd, 1),
        'dd_peak_window': dd_peak_idx,
        'strat_rets': strat_rets,
        'dd_curve': dd_curve,
        'window_details': window_details,
        'n_windows': len(strat_rets),
    }


def find_warning_windows(result):
    """找出所有预警触发的窗口"""
    return [w for w in result['window_details'] if w['warning_triggered']]


def compare_window(w_a, w_b):
    """对比两个模式在同一窗口的表现"""
    diff = {
        'window': w_a['window'],
        'date': w_a['date'],
        'ma20_ratio': w_a['ma20_ratio'],
        'mode_a_picks': [(t['code'], t['name'], t['ret']) for t in w_a['picks']],
        'mode_b_picks': [(t['code'], t['name'], t['ret']) for t in w_b['picks']],
        'mode_a_ret': w_a['avg_return'],
        'mode_b_ret': w_b['avg_return'],
        'ret_diff': round(w_b['avg_return'] - w_a['avg_return'], 2),
        'mode_a_n': w_a['n_picks'],
        'mode_b_n': w_b['n_picks'],
    }
    return diff


def main():
    print("=" * 70)
    print("U7 预警 DD 偏差诊断")
    print("=" * 70)

    # ─── Step 1: 跑三种模式的回测 ───
    print("\n[Step 1] 运行回测...")

    result_a = run_backtest_custom(ma20_warning_mode=None)  # 无预警
    result_b = run_backtest_custom(ma20_warning_mode='max1', warning_max_positions=1)  # 有预警 max=1
    result_c = run_backtest_custom(ma20_warning_mode='max2', warning_max_positions=2)  # 有预警 max=2

    print(f"  模式A（无预警）: 总收益={result_a['total_ret']}%, DD={result_a['max_dd']}%")
    print(f"  模式B（预警max1）: 总收益={result_b['total_ret']}%, DD={result_b['max_dd']}%")
    print(f"  模式C（预警max2）: 总收益={result_c['total_ret']}%, DD={result_c['max_dd']}%")

    # ─── Step 2: 找预警触发窗口并逐窗口对比 ───
    print("\n[Step 2] 预警触发窗口分析...")

    warning_windows = find_warning_windows(result_b)
    print(f"  预警触发窗口数: {len(warning_windows)}")

    # 逐窗口对比
    window_diffs = []
    for wa in result_a['window_details']:
        wb = result_b['window_details'][wa['window']]
        if wa['regime'] == 'bull':  # 只对比 bull 窗口
            window_diffs.append(compare_window(wa, wb))

    # DD 峰值窗口定位
    print(f"\n  模式A DD峰值窗口: {result_a['dd_peak_window']}")
    print(f"  模式B DD峰值窗口: {result_b['dd_peak_window']}")

    # 找 DD 扩大最严重的窗口
    dd_diffs = []
    eq_a = [100.0]
    eq_b = [100.0]
    for i in range(len(result_a['strat_rets'])):
        eq_a.append(eq_a[-1] * (1 + result_a['strat_rets'][i] / 100))
        eq_b.append(eq_b[-1] * (1 + result_b['strat_rets'][i] / 100))

    # 逐窗口计算累计 DD 差异
    peak_a, peak_b = 100.0, 100.0
    max_dd_a_per_window = []
    max_dd_b_per_window = []
    for i in range(len(eq_a)):
        peak_a = max(peak_a, eq_a[i])
        peak_b = max(peak_b, eq_b[i])
        max_dd_a_per_window.append((peak_a - eq_a[i]) / peak_a * 100)
        max_dd_b_per_window.append((peak_b - eq_b[i]) / peak_b * 100)

    # ─── Step 3: 根因分析 ───
    print("\n[Step 3] 根因分析...")

    # 对比每个 bull 窗口的选股差异
    warning_diffs = [d for d in window_diffs if d['ma20_ratio'] < MA20_DECLINE_THRESHOLD]

    # 模式B DD峰值窗口详情
    dd_peak_w = result_b['dd_peak_window']
    wb_peak = result_b['window_details'][dd_peak_w] if dd_peak_w < len(result_b['window_details']) else None
    wa_peak = result_a['window_details'][dd_peak_w] if dd_peak_w < len(result_a['window_details']) else None

    # ─── Step 4: 输出报告 ───
    print("\n[Step 4] 生成诊断报告...")

    report_lines = []
    report_lines.append("# U7 预警 DD 偏差诊断报告")
    report_lines.append("")
    report_lines.append("## 问题陈述")
    report_lines.append("")
    report_lines.append("ma20_decline 预警实施后，实际回测 DD 从 9.7% 升到 14.0%（研究预测应降到 9.2%）。")
    report_lines.append("根因：研究脚本修改的是 `trade_ledger`（不改选股），生产回测重新选股后结果不同。")
    report_lines.append("")

    report_lines.append("## 总体回测对比")
    report_lines.append("")
    report_lines.append("| 模式 | 说明 | 总收益 | 最大回撤 | DD峰值窗口 |")
    report_lines.append("|------|------|--------|----------|------------|")
    report_lines.append(f"| A | 无预警（bull=3） | {result_a['total_ret']}% | {result_a['max_dd']}% | W{result_a['dd_peak_window']} |")
    report_lines.append(f"| B | 预警 max=1 | {result_b['total_ret']}% | {result_b['max_dd']}% | W{result_b['dd_peak_window']} |")
    report_lines.append(f"| C | 预警 max=2 | {result_c['total_ret']}% | {result_c['max_dd']}% | W{result_c['dd_peak_window']} |")
    report_lines.append("")

    # 预警触发窗口列表
    report_lines.append("## 预警触发窗口")
    report_lines.append("")
    report_lines.append("| 窗口 | 日期 | MA20占比 | 模式A选股 | 模式A收益 | 模式B选股 | 模式B收益 | 收益差 |")
    report_lines.append("|------|------|----------|-----------|----------|-----------|----------|--------|")

    for d in warning_diffs:
        a_picks = ", ".join([f"{c}({r:+.1f}%)" for c, _, r in d['mode_a_picks']])
        b_picks = ", ".join([f"{c}({r:+.1f}%)" for c, _, r in d['mode_b_picks']])
        report_lines.append(
            f"| W{d['window']} | {d['date']} | {d['ma20_ratio']:.1f}% | "
            f"{a_picks} | {d['mode_a_ret']:+.2f}% | {b_picks} | {d['mode_b_ret']:+.2f}% | "
            f"{d['ret_diff']:+.2f}% |"
        )
    report_lines.append("")

    # 所有 bull 窗口对比（含非触发）
    report_lines.append("## 所有 Bull 窗口对比")
    report_lines.append("")
    report_lines.append("| 窗口 | 日期 | MA20占比 | 预警 | 模式A(n) | 模式A收益 | 模式B(n) | 模式B收益 | 差异 |")
    report_lines.append("|------|------|----------|------|----------|----------|----------|----------|------|")

    for d in window_diffs:
        triggered = "⚠️" if d['ma20_ratio'] < MA20_DECLINE_THRESHOLD else ""
        report_lines.append(
            f"| W{d['window']} | {d['date']} | {d['ma20_ratio']:.1f}% | {triggered} | "
            f"{d['mode_a_n']} | {d['mode_a_ret']:+.2f}% | {d['mode_b_n']} | {d['mode_b_ret']:+.2f}% | "
            f"{d['ret_diff']:+.2f}% |"
        )
    report_lines.append("")

    # DD 峰值窗口详情
    report_lines.append("## DD 峰值窗口详情")
    report_lines.append("")
    if wb_peak and wa_peak:
        report_lines.append(f"### 模式B DD 峰值窗口: W{dd_peak_w} ({wb_peak['date']})")
        report_lines.append(f"- 体制: {wb_peak['regime']}, 置信度: {wb_peak['conf']}")
        report_lines.append(f"- MA20占比: {wb_peak['ma20_ratio']:.1f}%")
        report_lines.append(f"- 预警触发: {'是' if wb_peak['warning_triggered'] else '否'}")
        report_lines.append("")
        report_lines.append("**模式A 选股:**")
        for t in wa_peak['picks']:
            report_lines.append(f"- {t['code']} {t['name']} ({t['sector']}): {t['ret']:+.2f}%")
        report_lines.append(f"  窗口平均收益: {wa_peak['avg_return']:+.2f}%")
        report_lines.append("")
        report_lines.append("**模式B 选股:**")
        for t in wb_peak['picks']:
            report_lines.append(f"- {t['code']} {t['name']} ({t['sector']}): {t['ret']:+.2f}%")
        report_lines.append(f"  窗口平均收益: {wb_peak['avg_return']:+.2f}%")
    report_lines.append("")

    # 根因分析
    report_lines.append("## 根因分析")
    report_lines.append("")

    # 分析预警窗口的选股差异
    warning_only_codes = defaultdict(list)  # 只在模式B出现的 ETF
    for d in warning_diffs:
        a_codes = set(c for c, _, _ in d['mode_a_picks'])
        b_codes = set(c for c, _, _ in d['mode_b_picks'])
        # 模式B比模式A少选的ETF（被砍掉的分散）
        removed = a_codes - b_codes
        # 模式B选的唯一ETF
        only_b = b_codes - a_codes
        for c in removed:
            for code, name, ret in d['mode_a_picks']:
                if code == c:
                    warning_only_codes[f"{c}({name})_removed"].append(ret)
        for c in only_b:
            for code, name, ret in d['mode_b_picks']:
                if code == c:
                    warning_only_codes[f"{c}({name})_only_b"].append(ret)

    # 统计被砍掉的 ETF 的平均收益
    removed_rets = []
    kept_rets = []
    for d in warning_diffs:
        a_picks_dict = {c: r for c, _, r in d['mode_a_picks']}
        b_picks_dict = {c: r for c, _, r in d['mode_b_picks']}
        for code, ret in a_picks_dict.items():
            if code not in b_picks_dict:
                removed_rets.append(ret)
        for code, ret in b_picks_dict.items():
            kept_rets.append(ret)

    avg_removed = np.mean(removed_rets) if removed_rets else 0
    avg_kept = np.mean(kept_rets) if kept_rets else 0

    report_lines.append("### 核心发现")
    report_lines.append("")
    report_lines.append(f"1. **预警触发窗口数**: {len(warning_diffs)} / {len([w for w in result_a['window_details'] if w['regime']=='bull'])} bull 窗口")
    report_lines.append(f"2. **被砍掉的 ETF 平均收益**: {avg_removed:+.2f}%")
    report_lines.append(f"3. **保留的 ETF 平均收益**: {avg_kept:+.2f}%")
    report_lines.append(f"4. **DD 变化**: 模式A {result_a['max_dd']}% → 模式B {result_b['max_dd']}%（差异: {result_b['max_dd']-result_a['max_dd']:+.1f}pp）")
    report_lines.append("")

    # 逐预警窗口，DD 贡献分析
    report_lines.append("### 逐预警窗口 DD 贡献")
    report_lines.append("")
    report_lines.append("| 窗口 | 日期 | 模式A收益 | 模式B收益 | 模式A累计DD | 模式B累计DD | DD差异 |")
    report_lines.append("|------|------|-----------|-----------|-------------|-------------|--------|")

    for d in warning_diffs:
        wi = d['window']
        dd_a = max_dd_a_per_window[wi + 1] if wi + 1 < len(max_dd_a_per_window) else 0
        dd_b = max_dd_b_per_window[wi + 1] if wi + 1 < len(max_dd_b_per_window) else 0
        report_lines.append(
            f"| W{wi} | {d['date']} | {d['mode_a_ret']:+.2f}% | {d['mode_b_ret']:+.2f}% | "
            f"{dd_a:.2f}% | {dd_b:.2f}% | {dd_b-dd_a:+.2f}pp |"
        )
    report_lines.append("")

    # 分析：模式B 集中持仓在 DD 期间的表现
    report_lines.append("### 集中持仓风险分析")
    report_lines.append("")
    if removed_rets:
        positive_removed = sum(1 for r in removed_rets if r > 0)
        negative_removed = sum(1 for r in removed_rets if r < 0)
        report_lines.append(f"- 被砍掉的 ETF 中，正收益 {positive_removed} 只，负收益 {negative_removed} 只")
        report_lines.append(f"- 如果保留被砍掉的 ETF（分散），它们会**{'减轻' if avg_removed > 0 else '加重'}** DD 压力")
    report_lines.append("")

    # 变体方案对比
    report_lines.append("## 变体方案对比（max=1 vs max=2）")
    report_lines.append("")
    report_lines.append("| 指标 | 无预警 | 预警max=1 | 预警max=2 |")
    report_lines.append("|------|--------|-----------|-----------|")
    report_lines.append(f"| 总收益 | {result_a['total_ret']}% | {result_b['total_ret']}% | {result_c['total_ret']}% |")
    report_lines.append(f"| 最大回撤 | {result_a['max_dd']}% | {result_b['max_dd']}% | {result_c['max_dd']}% |")
    report_lines.append(f"| DD vs 基线 | - | {result_b['max_dd']-result_a['max_dd']:+.1f}pp | {result_c['max_dd']-result_a['max_dd']:+.1f}pp |")
    report_lines.append("")

    # max=2 模式下预警窗口的选股
    report_lines.append("### 预警 max=2 选股对比")
    report_lines.append("")
    warning_windows_c = [w for w in result_c['window_details'] if w['warning_triggered']]
    report_lines.append("| 窗口 | 日期 | 模式A选股 | 模式B(1)选股 | 模式C(2)选股 | A收益 | B收益 | C收益 |")
    report_lines.append("|------|------|-----------|-------------|-------------|-------|-------|-------|")

    for wd in warning_diffs:
        wi = wd['window']
        wc = result_c['window_details'][wi] if wi < len(result_c['window_details']) else None
        a_str = ", ".join([f"{c}" for c, _, _ in wd['mode_a_picks']])
        b_str = ", ".join([f"{c}" for c, _, _ in wd['mode_b_picks']])
        c_str = ", ".join([f"{t['code']}" for t in wc['picks']]) if wc else "?"
        c_ret = wc['avg_return'] if wc else 0
        report_lines.append(
            f"| W{wi} | {wd['date']} | {a_str} | {b_str} | {c_str} | "
            f"{wd['mode_a_ret']:+.2f}% | {wd['mode_b_ret']:+.2f}% | {c_ret:+.2f}% |"
        )
    report_lines.append("")

    # 结论
    report_lines.append("## 结论与建议")
    report_lines.append("")
    if result_b['max_dd'] > result_a['max_dd']:
        report_lines.append("**预警 max=1 反而增大了 DD**，原因：")
        report_lines.append(f"- 预警时将 bull=3 降为 bull=1，集中持仓于单只 ETF")
        report_lines.append(f"- 被砍掉的 ETF 平均收益 {avg_removed:+.2f}%，分散本可以{'减轻' if avg_removed > 0 else '加重'}损失")
        report_lines.append("- 集中持仓在市场下跌时缺乏对冲，单只 ETF 的大幅回撤直接贡献了 DD 峰值")
        report_lines.append("")
    else:
        report_lines.append("预警 max=1 确实降低了 DD。")
        report_lines.append("")

    if result_c['max_dd'] < result_b['max_dd']:
        report_lines.append(f"**预警 max=2 是更优折中**: DD={result_c['max_dd']}%（vs max=1 的 {result_b['max_dd']}%），保留部分分散同时减少暴露。")
    elif result_c['max_dd'] < result_a['max_dd']:
        report_lines.append(f"**预警 max=2 比 max=1 好，且比无预警 DD 更低**: DD={result_c['max_dd']}%")
    else:
        report_lines.append(f"预警 max=2 DD={result_c['max_dd']}%，仍不如无预警的 {result_a['max_dd']}%。")
    report_lines.append("")

    # 保存报告
    report = "\n".join(report_lines)
    os.makedirs(RESEARCH_DIR, exist_ok=True)
    report_path = os.path.join(RESEARCH_DIR, "dd_investigation_report.md")
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"\n报告已保存: {report_path}")

    # 同时保存详细数据
    data_path = os.path.join(RESEARCH_DIR, "dd_investigation_data.json")
    with open(data_path, 'w') as f:
        json.dump({
            'mode_a': {k: v for k, v in result_a.items() if k != 'dd_curve'},
            'mode_b': {k: v for k, v in result_b.items() if k != 'dd_curve'},
            'mode_c': {k: v for k, v in result_c.items() if k != 'dd_curve'},
            'warning_diffs': warning_diffs,
        }, f, ensure_ascii=False, indent=2)
    print(f"数据已保存: {data_path}")


if __name__ == '__main__':
    main()
