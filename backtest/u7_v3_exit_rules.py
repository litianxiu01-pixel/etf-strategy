#!/usr/bin/env python3
"""
U7-v3 退出规则回测 — 逐日级组合模拟

三套规则并行：
  1. 正常退出: 每 N 日重新排名，掉池卖 / 前N留 / 新强替
  2. 信号失效: regime 转熊 / MA20 连破 2-3 日 / 60日动量排名滑出
  3. 组合风控: 组合 DD>8% 减仓 / >12% 切防御

真实度: 逐日 mark-to-market、交易成本、现金管理
"""
import json, os, sys, math, time, copy
import numpy as np
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
CORE_DIR = os.path.join(REPO_ROOT, "core")
sys.path.insert(0, CORE_DIR)
from u7_strategy import (
    _load, etf_sector, get_closes, detect_regime_at, select_at,
    make_config, SECTOR_RULES, DEF_GOLD, DEF_BOND, US_CODES, HK_CODES,
)

_combined, _universe, _dates_all = _load()
BOND_ETF = '511010'
GOLD_ETF = '518880'

# ═══════════════════════════════════════════════════
# 核心：逐日组合模拟
# ═══════════════════════════════════════════════════

class PortfolioSim:
    def __init__(self, config, start_date='2023-06-01', end_date=None,
                 cost_bps=20, ma_confirm_days=2, momentum_rank_threshold=0.5,
                 dd_cut_pct=8, dd_defensive_pct=12, disaster_exit_pct=None):
        """
        config: 策略配置（含 max_positions, hold_days 等）
        cost_bps: 每笔交易成本（买入+卖出各收一次）
        ma_confirm_days: MA20 跌破确认天数（0=不启用）
        momentum_rank_threshold: 动量排名阈值（跌出前 X% 则卖，0.5=前50%）
        dd_cut_pct: 组合回撤触发减仓（None=不启用）
        dd_defensive_pct: 组合回撤触发防御（None=不启用）
        disaster_exit_pct: 单标的灾难退出阈值，例 -12 表示从成本跌 12% 卖出（None=不启用）
        """
        self.config = config
        self.hold_days = config.get('hold_days', 10)
        self.cost_bps = cost_bps
        self.ma_confirm = ma_confirm_days
        self.mom_rank_threshold = momentum_rank_threshold
        self.dd_cut = dd_cut_pct
        self.dd_defense = dd_defensive_pct
        self.disaster_exit = disaster_exit_pct
        self.max_pos = config.get('max_positions', {'bull':3,'neutral':5,'bear':2})
        
        # 日期范围
        self.dates = _dates_all
        if end_date is None: end_date = self.dates[-1]
        self.start_idx = next((i for i, d in enumerate(self.dates) if d >= start_date), 0)
        self.end_idx = next((i for i, d in enumerate(self.dates) if d > end_date), len(_combined))
        self.start_date = start_date
        self.end_date = end_date
        
        # 组合状态
        self.cash = 100_000  # 初始 10 万
        self.positions = {}  # {code: {shares, avg_cost, entry_date}}
        self.equity_history = [(self.start_idx, 100_000)]
        self.trades_log = []  # 每笔交易
        self.events_log = []  # 风控事件
        self.daily_equity = []  # 逐日净值 (for DD calc)
        self.peak_equity = 0
        
        # 预计算 MA（加速）
        self._ma_cache = {}  # (code, day_idx) -> (ma20, ma60)
    
    def _get_ma(self, code, day_idx):
        key = (code, day_idx)
        if key in self._ma_cache:
            return self._ma_cache[key]
        closes = get_closes(code, day_idx, 61)
        ma20 = np.mean(closes[-20:]) if len(closes) >= 20 else 0
        ma60 = np.mean(closes[-60:]) if len(closes) >= 60 else 0
        self._ma_cache[key] = (ma20, ma60)
        return ma20, ma60
    
    def _get_price(self, code, day_idx):
        return _combined[day_idx]['etfs'].get(code, {}).get('close', 0)
    
    def _get_equity(self, day_idx):
        """当日组合总市值"""
        total = self.cash
        for code, pos in self.positions.items():
            px = self._get_price(code, day_idx)
            if px > 0:
                total += pos['shares'] * px
        return total
    
    def _buy(self, code, day_idx, amount, reason='rebalance'):
        px = self._get_price(code, day_idx)
        if px <= 0 or amount <= 0: return False
        cost = amount * self.cost_bps / 10000
        actual = amount - cost
        if actual > self.cash: return False
        shares = actual / px
        self.cash -= amount
        
        if code in self.positions:
            # 加仓
            old = self.positions[code]
            total_cost = old['shares'] * old['avg_cost'] + shares * px
            old['shares'] += shares
            old['avg_cost'] = total_cost / old['shares'] if old['shares'] > 0 else px
        else:
            self.positions[code] = {'shares': shares, 'avg_cost': px,
                                     'entry_date': self.dates[day_idx]}
        
        self.trades_log.append({
            'date': self.dates[day_idx], 'action': 'BUY', 'code': code,
            'name': _universe['etfs'][code]['name'], 'price': round(px, 3),
            'amount': round(amount, 0), 'shares': round(shares, 0),
            'cost': round(cost, 1), 'reason': reason,
        })
        return True
    
    def _sell(self, code, day_idx, reason='rebalance'):
        if code not in self.positions: return 0
        pos = self.positions[code]
        px = self._get_price(code, day_idx)
        if px <= 0:
            # 无价格，按成本卖出
            px = pos['avg_cost']
        proceeds = pos['shares'] * px
        cost = proceeds * self.cost_bps / 10000
        net = proceeds - cost
        
        entry_px = pos['avg_cost']
        ret_pct = (px / entry_px - 1) * 100
        hold_days = day_idx - next((i for i, d in enumerate(self.dates) if d == pos['entry_date']), day_idx)
        
        self.trades_log.append({
            'date': self.dates[day_idx], 'action': 'SELL', 'code': code,
            'name': _universe['etfs'][code]['name'],
            'entry_price': round(entry_px, 3), 'exit_price': round(px, 3),
            'ret_pct': round(ret_pct, 2), 'hold_days': hold_days,
            'proceeds': round(net, 0), 'reason': reason,
        })
        
        self.cash += net
        del self.positions[code]
        return net
    
    def _check_ma_break(self, code, day_idx):
        """检查 MA20 是否连续跌破"""
        if self.ma_confirm <= 0: return False
        ma20, _ = self._get_ma(code, day_idx)
        px = self._get_price(code, day_idx)
        if px <= 0 or ma20 <= 0: return False
        if px >= ma20: return False
        
        # 回溯检查连续跌破天数
        below_count = 1
        for d in range(day_idx - 1, max(self.start_idx, day_idx - self.ma_confirm), -1):
            p = self._get_price(code, d)
            m20, _ = self._get_ma(code, d)
            if p > 0 and m20 > 0 and p < m20:
                below_count += 1
            else:
                break
        return below_count >= self.ma_confirm
    
    def _check_momentum_rank(self, code, day_idx):
        """检查 60 日动量排名是否滑出阈值"""
        # 计算该 ETF 的 60 日收益
        closes = get_closes(code, day_idx, 61)
        if len(closes) < 61: return False
        ret60 = (closes[-1] / closes[-61] - 1) * 100
        
        # 计算所有 CN ETF 的 60 日收益
        all_ret60 = []
        for c, info in _universe['etfs'].items():
            if info.get('status') != 'active' or info.get('assetClass') != 'cn':
                continue
            c_closes = get_closes(c, day_idx, 61)
            if len(c_closes) < 61: continue
            all_ret60.append((c, (c_closes[-1]/c_closes[-61]-1)*100))
        
        if not all_ret60: return False
        all_ret60.sort(key=lambda x: x[1], reverse=True)
        
        # 找到该 ETF 的排名
        rank = next((i for i, (c, _) in enumerate(all_ret60) if c == code), len(all_ret60))
        rank_pct = rank / len(all_ret60) if all_ret60 else 1.0
        
        return rank_pct > self.mom_rank_threshold
    
    def _check_disaster_exit(self, code, day_idx):
        """检查单标的是否触发灾难退出"""
        if self.disaster_exit is None: return False
        if code not in self.positions: return False
        px = self._get_price(code, day_idx)
        if px <= 0: return False
        entry_px = self.positions[code].get('avg_cost', 0)
        if entry_px <= 0: return False
        
        threshold = self.disaster_exit
        if threshold > 0:
            threshold = -threshold
        ret_pct = (px / entry_px - 1) * 100
        return ret_pct <= threshold
    
    def _check_portfolio_dd(self, day_idx):
        """检查组合回撤是否触发风控"""
        if not self.daily_equity: return None
        eq = self._get_equity(day_idx)
        peak = max(e['eq'] for e in self.daily_equity) if self.daily_equity else eq
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        
        if self.dd_defense and dd >= self.dd_defense:
            return 'defensive'
        if self.dd_cut and dd >= self.dd_cut:
            return 'cut'
        return None
    
    def run(self):
        """主循环"""
        # 找到第一个 rebalance 日
        rebalance_indices = list(range(self.start_idx, self.end_idx, self.hold_days))
        
        next_rebalance_idx = 0  # pointer into rebalance_indices
        
        for day_idx in range(self.start_idx, self.end_idx):
            date = self.dates[day_idx]
            
            # 记录当日净值
            eq = self._get_equity(day_idx)
            self.daily_equity.append({'date': date, 'eq': eq})
            
            # ── 每日检查: 组合回撤 ──
            dd_action = self._check_portfolio_dd(day_idx)
            if dd_action:
                self.events_log.append({
                    'date': date, 'type': f'DD_{dd_action.upper()}',
                    'equity': round(eq, 0),
                    'dd': round((max(e['eq'] for e in self.daily_equity) - eq) / 
                               max(e['eq'] for e in self.daily_equity) * 100, 1) 
                               if self.daily_equity else 0,
                })
                
                if dd_action == 'defensive':
                    # 卖光风险资产，买国债+黄金
                    for code in list(self.positions.keys()):
                        self._sell(code, day_idx, f'DD_defensive_{self.dd_defense}%')
                    # 一半国债一半黄金
                    available = self.cash * 0.95  # 留 5% 现金
                    self._buy(BOND_ETF, day_idx, available * 0.5, 'defensive_bond')
                    self._buy(GOLD_ETF, day_idx, available * 0.5, 'defensive_gold')
                
                elif dd_action == 'cut':
                    # 减仓 50%: 卖一半持仓
                    for code in list(self.positions.keys()):
                        pos = self.positions[code]
                        px = self._get_price(code, day_idx)
                        if px <= 0: continue
                        sell_shares = pos['shares'] * 0.5
                        sell_amount = sell_shares * px
                        cost = sell_amount * self.cost_bps / 10000
                        self.cash += sell_amount - cost
                        pos['shares'] -= sell_shares
                        self.trades_log.append({
                            'date': date, 'action': 'SELL_HALF', 'code': code,
                            'name': _universe['etfs'][code]['name'],
                            'price': round(px, 3),
                            'shares_sold': round(sell_shares, 0),
                            'reason': f'DD_cut_{self.dd_cut}%',
                        })
                        if pos['shares'] <= 0:
                            del self.positions[code]
            
            # ── 每日检查: 单标的灾难退出 ──
            if self.disaster_exit is not None:
                threshold = self.disaster_exit if self.disaster_exit <= 0 else -self.disaster_exit
                for code in list(self.positions.keys()):
                    if self._check_disaster_exit(code, day_idx):
                        px = self._get_price(code, day_idx)
                        entry_px = self.positions[code].get('avg_cost', px)
                        ret_pct = (px / entry_px - 1) * 100 if entry_px > 0 else 0
                        self._sell(code, day_idx, f'disaster_exit_{threshold}%')
                        self.events_log.append({
                            'date': date, 'type': 'DISASTER_EXIT', 'code': code,
                            'ret_pct': round(ret_pct, 2),
                        })
            
            # ── 每日检查: MA20 确认跌破 ──
            if self.ma_confirm > 0:
                for code in list(self.positions.keys()):
                    if self._check_ma_break(code, day_idx):
                        self._sell(code, day_idx, f'MA20_break_{self.ma_confirm}d')
                        self.events_log.append({
                            'date': date, 'type': 'MA_BREAK', 'code': code,
                        })
            
            # ── 每日检查: 动量排名滑出 ──
            for code in list(self.positions.keys()):
                if self._check_momentum_rank(code, day_idx):
                    self._sell(code, day_idx, f'mom_rank_{self.mom_rank_threshold}')
                    self.events_log.append({
                        'date': date, 'type': 'MOM_RANK', 'code': code,
                    })
            
            # ── Rebalance 日 ──
            if next_rebalance_idx < len(rebalance_indices) and day_idx == rebalance_indices[next_rebalance_idx]:
                next_rebalance_idx += 1
                
                ri = detect_regime_at(day_idx)
                regime = ri['regime']
                conf = ri['conf']
                
                # 信号失效: regime 转 bear
                if self.ma_confirm <= 0 and regime == 'bear':
                    # 卖光风险资产
                    for code in list(self.positions.keys()):
                        ac = _universe['etfs'][code].get('assetClass', 'cn')
                        if ac not in ['gold', 'bond']:
                            self._sell(code, day_idx, 'regime_bear')
                    # 买国债
                    available = self.cash * 0.95
                    if available > 1000:
                        self._buy(BOND_ETF, day_idx, available, 'regime_bear_bond')
                    continue  # bear 不做正常选股
                
                # 选股
                picks = select_at(day_idx, regime, conf, self.config)
                n_target = self.max_pos.get(regime, 5)
                picks = picks[:n_target]
                target_codes = {p['code'] for p in picks}
                
                # 正常退出: 掉池的卖
                for code in list(self.positions.keys()):
                    if code not in target_codes:
                        self._sell(code, day_idx, 'fell_out')
                
                # 正常退出: 新标的替换
                current_codes = set(self.positions.keys())
                empty_slots = n_target - len(current_codes)
                
                if empty_slots > 0:
                    # 总可用资金（含现金） / 总仓位 = 每仓金额
                    total_eq = self._get_equity(day_idx)
                    # 仓位上限：个人设定的 25%
                    per_slot = min(total_eq / n_target, total_eq * 0.25)
                    
                    new_picks = [p for p in picks if p['code'] not in current_codes][:empty_slots]
                    available_per = self.cash / max(len(new_picks), 1)
                    
                    for p in new_picks:
                        amount = min(available_per, per_slot * 0.95)
                        if amount > 1000:
                            self._buy(p['code'], day_idx, amount, 'rebalance')
        
        # 最后一天: 平仓
        final_idx = self.end_idx - 1
        for code in list(self.positions.keys()):
            self._sell(code, final_idx, 'final_close')
        
        return self._compute_metrics()
    
    def _compute_metrics(self):
        """计算性能指标"""
        eq_series = [e['eq'] for e in self.daily_equity]
        if not eq_series: return {}
        
        total_ret = (eq_series[-1] / eq_series[0] - 1) * 100
        n_days = len(eq_series)
        
        # 回撤
        peak = eq_series[0]; dd = 0
        for v in eq_series:
            peak = max(peak, v)
            dd = max(dd, (peak - v) / peak * 100)
        
        # 日收益 → 年化
        daily_rets = np.diff(eq_series) / eq_series[:-1]
        ann_ret = ((eq_series[-1] / eq_series[0]) ** (252 / n_days) - 1) * 100 if n_days > 1 else 0
        sharpe = float(np.mean(daily_rets - 0.02/252) / np.std(daily_rets) * math.sqrt(252)) if np.std(daily_rets) > 0 else 0
        
        # 胜率（基于交易）
        sell_trades = [t for t in self.trades_log if t['action'] == 'SELL']
        wins = sum(1 for t in sell_trades if t['ret_pct'] > 0)
        wr = wins / len(sell_trades) * 100 if sell_trades else 0
        
        # 基准（等权 CN ETF 买入持有）
        bench_eq = 100_000
        bench_daily = []
        cn_codes = [c for c, i in _universe['etfs'].items()
                     if i.get('status') == 'active' and i.get('assetClass') == 'cn']
        for day_idx in range(self.start_idx, self.end_idx):
            rets = []
            for c in cn_codes:
                px = self._get_price(c, day_idx)
                px0 = self._get_price(c, self.start_idx)
                if px > 0 and px0 > 0:
                    rets.append((px/px0 - 1) * 100)
            bench_daily.append(np.mean(rets) if rets else 0)
        bench_total = bench_daily[-1] if bench_daily else 0
        
        # 统计
        buys = [t for t in self.trades_log if t['action'] == 'BUY']
        sells = [t for t in self.trades_log if t['action'] in ('SELL', 'SELL_HALF')]
        total_cost = sum(t.get('cost', 0) for t in self.trades_log)
        
        # 退出原因分布
        exit_reasons = defaultdict(int)
        for t in self.trades_log:
            if t['action'] in ('SELL', 'SELL_HALF'):
                exit_reasons[t['reason']] += 1
        
        return {
            'total_ret': round(total_ret, 1),
            'ann_ret': round(ann_ret, 1),
            'dd': round(dd, 1),
            'sharpe': round(sharpe, 2),
            'win_rate': round(wr, 1),
            'n_trades': len(sell_trades),
            'n_buys': len(buys),
            'total_cost': round(total_cost, 0),
            'bench_ret': round(bench_total, 1),
            'alpha': round(total_ret - bench_total, 1),
            'exit_reasons': dict(exit_reasons),
            'disaster_exit_pct': self.disaster_exit,
            'n_events': len(self.events_log),
            'events': self.events_log[:20],  # 截断
            'trades': self.trades_log[:50],  # 截断
        }

# ═══════════════════════════════════════════════════
# 实验矩阵
# ═══════════════════════════════════════════════════

def run_exit_experiment():
    print("=" * 70)
    print("🏃 U7-v3 退出规则回测")
    print("=" * 70)
    
    base_cfg = make_config('exit_test',
        stop_loss_pct=None, min_sectors=4, cold_threshold=-3.0,
        max_positions={'bull': 3, 'neutral': 5, 'bear': 2},
        hold_days=10,
    )
    
    variants = []
    
    # 基线: 纯 rebalance，无额外退出
    print("\n  ═══ 运行中 ═══")
    
    for label, hold, ma_conf, mom_thresh, dd_cut, dd_def, disaster in [
        # (标签, hold_days, ma确认天, 动量阈值, dd减仓, dd防御, 灾难退出)
        ("V3 base (纯rebalance)", 10, 0, 1.0, None, None, None),
        ("+ MA20连2日确认卖", 10, 2, 1.0, None, None, None),
        ("+ MA20连3日确认卖", 10, 3, 1.0, None, None, None),
        ("+ 动量跌出前50%卖", 10, 0, 0.5, None, None, None),
        ("+ DD8%减仓", 10, 0, 1.0, 8, None, None),
        ("+ DD8%减+12%防御", 10, 0, 1.0, 8, 12, None),
        ("+ MA2日+动量+DD8%+12%", 10, 2, 0.5, 8, 12, None),
        ("+ MA2日+动量 (无DD)", 10, 2, 0.5, None, None, None),
        # hold=20 版本
        ("hold20 pure rebalance", 20, 0, 1.0, None, None, None),
        ("hold20 disaster -12%", 20, 0, 1.0, None, None, -12),
        ("hold20 MA2日+动量", 20, 2, 0.5, None, None, None),
        ("hold20 MA3日+动量", 20, 3, 0.5, None, None, None),
        ("hold20 MA2日+动量+DD8", 20, 2, 0.5, 8, None, None),
    ]:
        cfg = dict(base_cfg)
        cfg['hold_days'] = hold
        cfg['name'] = label
        
        t0 = time.time()
        sim = PortfolioSim(cfg, cost_bps=20,
                          ma_confirm_days=ma_conf,
                          momentum_rank_threshold=mom_thresh,
                          dd_cut_pct=dd_cut,
                          dd_defensive_pct=dd_def,
                          disaster_exit_pct=disaster)
        result = sim.run()
        elapsed = time.time() - t0
        
        if result:
            result['label'] = label
            result['hold'] = hold
            result['ma_confirm'] = ma_conf
            result['mom_thresh'] = mom_thresh
            result['dd_cut'] = dd_cut
            result['dd_def'] = dd_def
            result['disaster_exit'] = disaster
            result['elapsed'] = elapsed
            variants.append(result)
            
            print(f"  [{elapsed:.0f}s] {label:<30} ret={result['total_ret']:>7.1f}% "
                  f"dd={result['dd']:>5.1f}% sr={result['sharpe']:>5.2f} "
                  f"wr={result['win_rate']:>5.1f}% trades={result['n_trades']:>3} "
                  f"cost=¥{result['total_cost']:>.0f}")
    
    # 排名
    print(f"\n{'='*70}")
    print("📊 排名（按 Sharpe）")
    print(f"{'='*70}")
    variants.sort(key=lambda x: x['sharpe'], reverse=True)
    
    print(f"  {'方案':<32} {'ret':>8} {'dd':>6} {'sr':>6} {'ann':>7} {'wr':>6} {'trades':>6} {'cost':>8} {'退出原因'}")
    print(f"  {'─'*32} {'─'*8} {'─'*6} {'─'*6} {'─'*7} {'─'*6} {'─'*6} {'─'*8} {'─'*20}")
    
    for v in variants:
        reasons = '|'.join(f"{k}:{c}" for k, c in sorted(v['exit_reasons'].items()) 
                          if not k.startswith('final'))
        if not reasons: reasons = 'rebalance_only'
        print(f"  {v['label']:<32} {v['total_ret']:>7.1f}% {v['dd']:>5.1f}% {v['sharpe']:>5.2f} "
              f"{v['ann_ret']:>6.1f}% {v['win_rate']:>5.1f}% {v['n_trades']:>5} "
              f"¥{v['total_cost']:>7.0f} {reasons[:20]}")
    
    # 基准
    if variants:
        bench = variants[0].get('bench_ret', 0)
        print(f"\n  📏 等权 CN ETF 基准: {bench}%")
    
    # 最佳
    best = variants[0]
    print(f"\n  🏆 最优: {best['label']}")
    print(f"     ret={best['total_ret']}% dd={best['dd']}% sr={best['sharpe']} "
          f"ann={best['ann_ret']}% wr={best['win_rate']}% trades={best['n_trades']}")
    if best.get('events'):
        print(f"     风控事件: {best['n_events']} 次")
        for e in best['events'][:5]:
            print(f"       {e['date']} {e['type']} {e.get('code','')} {e.get('dd','')}")
    
    # 保存
    out = [{'label': v['label'], 'total_ret': v['total_ret'], 'dd': v['dd'],
            'sharpe': v['sharpe'], 'ann_ret': v['ann_ret'], 'win_rate': v['win_rate'],
            'n_trades': v['n_trades'], 'total_cost': v['total_cost'],
            'exit_reasons': v['exit_reasons'], 'hold': v['hold']} for v in variants]
    with open(os.path.join(DATA_DIR, 'u7_exit_rules_results.json'), 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n📁 {DATA_DIR}/u7_exit_rules_results.json")
    
    return variants

if __name__ == '__main__':
    run_exit_experiment()
