"""
全量 Universe 扫描器
1. 从1490只ETF中筛选出可交易标的（无需行情数据）
2. 批量下载历史数据
3. 运行选股逻辑
"""
import akshare as ak
import json, os, time, sys
from datetime import datetime
from collections import defaultdict

SAVE_DIR = os.path.expanduser("~/.qclaw/workspace-main/data/market_regime")
os.makedirs(SAVE_DIR, exist_ok=True)

# ─── Step 1: 加载全量列表 ───
print("=" * 60)
print("Step 1: 加载全市场 ETF 列表")
print("=" * 60)

df = ak.fund_etf_category_sina(symbol="ETF基金")
print(f"全市场 ETF: {len(df)} 只")

# ─── Step 2: 非行情数据筛选 ───
print(f"\nStep 2: 非行情数据筛选")

# 2a. 排除特殊类型
exclude = ['增强', '联接', '杠杆', '反向', '两倍', '三倍', '拆分', '货币', '理财', '添益', '保证金', '币']
for kw in exclude:
    before = len(df)
    df = df[~df['名称'].str.contains(kw, na=False)]
    if len(df) < before:
        print(f"  排除 '{kw}': {before} → {len(df)}")

# 2b. 排除迷你ETF（昨收 < 0.5 的可能是问题ETF或净值异常）
# 市场未开盘时成交量=0，但昨收应该有值
df['has_yest_close'] = df['昨收'] > 0
print(f"  有昨收数据: {df['has_yest_close'].sum()}/{len(df)}")

# 用昨收过滤：保留昨收>0.3 OR 本次未提供昨收但代码有效的新ETF
df_filtered = df[(df['昨收'] > 0.3) | (~df['has_yest_close'])].copy()
print(f"  昨收>0.3 或 新ETF: {len(df_filtered)} 只")

# 2c. 资产分类
def classify(name, code):
    n = str(name)
    if any(k in n for k in ['纳指', '纳斯达克', '标普', '道琼斯', '标普生物', 'SPX', '费城']):
        return 'us'
    if any(k in n for k in ['恒生', '港股', '香港', 'HSI', '中概', '港股通']):
        return 'hk'
    if any(k in n for k in ['黄金', '金ETF', '上海金', 'Au99', '白银', '铂金']):
        return 'gold'
    if any(k in n for k in ['国债', '债券', '国开债', '信用债', '转债', '公司债', '地债', '政金债', '城投债', '可转债']):
        return 'bond'
    if any(k in n for k in ['豆粕', '原油', '有色', '能源', '煤炭', '钢铁', '化工', '稀土', '锂', '铜']):
        return 'commodity'
    return 'cn'

df_filtered['asset_class'] = df_filtered.apply(lambda r: classify(r['名称'], r['代码']), axis=1)

# 分布
print(f"\n  资产分布:")
for cls in ['cn', 'us', 'hk', 'gold', 'bond', 'commodity']:
    cnt = len(df_filtered[df_filtered['asset_class'] == cls])
    print(f"    {cls}: {cnt} 只")

# ─── Step 3: 精选高质量池 ───
print(f"\nStep 3: 构建精选 Universe（流动性优先）")

# 核心池规则：
# A股: 取规模大/知名度高的宽基+行业龙头（代码编号较小的一般上市更早更稳定）
# 跨市场: 全取（数量少）
# 债券: 全取（数量少）  
# 黄金: 全取（数量少）
# 商品: 取流动性好的（豆粕、有色、能源）

def select_quality(df_cls, max_n=50):
    """从资产类别中选最优标的"""
    df_cls = df_cls.copy()
    
    # 优先：昨收>0（有真实价格）
    with_price = df_cls[df_cls['昨收'] > 0]
    
    # 对A股ETF，优先宽基和主要行业
    selected = []
    
    # 1. 宽基指数ETF（沪深300/中证500/创业板/科创50等）
    broad_keywords = ['沪深300', '中证500', '中证1000', '中证A500', '创业板', '科创50', '科创100', 
                      '上证50', '上证180', '深证100', '中证红利', '红利低波', '自由现金流']
    for kw in broad_keywords:
        matches = with_price[with_price['名称'].str.contains(kw, na=False)]
        if len(matches) > 0:
            # 每个宽基取成交量最大的一只
            selected.append(matches.iloc[0])
    
    # 2. 行业ETF（选主要行业）
    industry_keywords = ['半导体', '芯片', '人工智能', 'AI', '新能源', '光伏', '电池', 
                         '军工', '医疗', '医药', '创新药', '消费', '食品饮料', '酒',
                         '银行', '券商', '证券', '保险', '地产', '房地产',
                         '汽车', '电力', '通信', '计算机', '软件', '传媒', '游戏',
                         '国防', '央企', '国企', '一带一路']
    for kw in industry_keywords:
        matches = with_price[with_price['名称'].str.contains(kw, na=False)]
        matches = matches[~matches.index.isin([s.name for s in selected])]
        if len(matches) > 0:
            selected.append(matches.iloc[0])
    
    # 3. 去重后截断
    seen_names = set()
    final = []
    for s in selected:
        name = s['名称']
        if name not in seen_names:
            seen_names.add(name)
            final.append(s)
    
    return final[:max_n]

universe_etfs = []

# 各资产类别精选
for cls, max_n, desc in [('us', 30, '美股'), ('hk', 50, '港股'), ('gold', 25, '黄金'), 
                           ('bond', 55, '债券'), ('commodity', 30, '商品')]:
    subset = df_filtered[df_filtered['asset_class'] == cls]
    selected = select_quality(subset, max_n) if cls == 'cn' else [row for _, row in subset.head(max_n).iterrows()]
    for row in selected:
        universe_etfs.append({
            'code': row['代码'],
            'numCode': row['代码'][2:],
            'name': row['名称'],
            'exchange': 'SH' if row['代码'].startswith('sh') else 'SZ',
            'assetClass': cls,
        })
    print(f"  {desc}: {len(selected)} 只")

# A股单独处理（最多）
cn_subset = df_filtered[df_filtered['asset_class'] == 'cn']
cn_selected = select_quality(cn_subset, 80)
for row in cn_selected:
    universe_etfs.append({
        'code': row['代码'],
        'numCode': row['代码'][2:],
        'name': row['名称'],
        'exchange': 'SH' if row['代码'].startswith('sh') else 'SZ',
        'assetClass': 'cn',
    })
print(f"  A股: {len(cn_selected)} 只")

# 去重
seen = set()
unique = []
for e in universe_etfs:
    if e['code'] not in seen:
        seen.add(e['code'])
        unique.append(e)
universe_etfs = unique

print(f"\n  精选 Universe: {len(universe_etfs)} 只 ETF")

# ─── Step 4: 批量下载历史数据 ───
print(f"\nStep 4: 批量下载历史数据 ({len(universe_etfs)} 只)")

start_date = "20200101"
end_date = "20250617"

success = 0
failed_list = []
all_data = {}

for i, etf in enumerate(universe_etfs):
    code = etf['numCode']
    try:
        h = ak.fund_etf_hist_em(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
        records = []
        for _, row in h.iterrows():
            records.append({
                'date': str(row['日期']),
                'open': float(row['开盘']),
                'high': float(row['最高']),
                'low': float(row['最低']),
                'close': float(row['收盘']),
                'volume': int(row['成交量']),
                'amount': float(row['成交额']),
            })
        all_data[etf['code']] = {
            'info': etf,
            'records': records,
            'days': len(records),
        }
        success += 1
        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(universe_etfs)}] {success} succeeded, {len(failed_list)} failed")
        
        time.sleep(0.3)  # Rate limit protection
    except Exception as e:
        failed_list.append(f"{code} {etf['name']}: {str(e)[:80]}")
        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(universe_etfs)}] {success} succeeded, {len(failed_list)} failed")

print(f"\n  下载完成: {success}/{len(universe_etfs)} 成功, {len(failed_list)} 失败")

if failed_list:
    print(f"  失败列表 (前10):")
    for f in failed_list[:10]:
        print(f"    {f}")

# ─── Step 5: 保存数据 ───
print(f"\nStep 5: 保存数据")

# 保存每只ETF的独立数据
etf_dir = os.path.join(SAVE_DIR, 'etfs')
os.makedirs(etf_dir, exist_ok=True)

for code, d in all_data.items():
    fname = os.path.join(etf_dir, f"etf_{d['info']['numCode']}.json")
    with open(fname, 'w') as f:
        json.dump(d['records'], f, ensure_ascii=False)

# 保存 Universe 元数据
universe_meta = {
    'generatedAt': datetime.now().isoformat(),
    'totalActive': success,
    'etfs': {}
}
for code, d in all_data.items():
    recs = d['records']
    universe_meta['etfs'][d['info']['numCode']] = {
        'code': d['info']['code'],
        'numCode': d['info']['numCode'],
        'name': d['info']['name'],
        'exchange': d['info']['exchange'],
        'assetClass': d['info']['assetClass'],
        'status': 'active' if len(recs) >= 120 else 'pending',
        'historyStart': recs[0]['date'] if recs else None,
        'historyEnd': recs[-1]['date'] if recs else None,
        'historyDays': len(recs),
    }

with open(os.path.join(SAVE_DIR, 'etf_universe.json'), 'w') as f:
    json.dump(universe_meta, f, ensure_ascii=False, indent=2)

print(f"  Universe 元数据: {len(universe_meta['etfs'])} 只")
print(f"  独立数据文件: {etf_dir}/")

# ─── 统计 ───
print(f"\n{'='*60}")
print(f"汇总")
print(f"{'='*60}")

total_records = sum(d['days'] for d in all_data.values())
print(f"总数据量: {total_records:,} 条日线记录")

# 按资产类别统计
class_stats = defaultdict(lambda: {'count': 0, 'total_days': 0, 'names': []})
for code, d in all_data.items():
    cls = d['info']['assetClass']
    class_stats[cls]['count'] += 1
    class_stats[cls]['total_days'] += d['days']
    if len(d['records']) >= 252:  # 至少1年数据
        class_stats[cls]['names'].append(d['info']['name'])

print(f"\n资产类别统计:")
for cls in ['cn', 'us', 'hk', 'gold', 'bond', 'commodity']:
    s = class_stats[cls]
    if s['count'] > 0:
        print(f"  {cls}: {s['count']}只, {s['total_days']:,}条记录, {len(s['names'])}只有1年+数据")
        # Show top 3 by record count
        cls_etfs = [(code, d) for code, d in all_data.items() if d['info']['assetClass'] == cls]
        cls_etfs.sort(key=lambda x: x[1]['days'], reverse=True)
        for code, d in cls_etfs[:3]:
            print(f"    {d['info']['code']} {d['info']['name']:<18s} {d['days']}d [{d['records'][0]['date']}~{d['records'][-1]['date']}]")

print(f"\n✅ 全量扫描完成")
print(f"   精选池: {len(universe_etfs)} 只 ETF")
print(f"   成功下载: {success} 只")
print(f"   失败: {len(failed_list)} 只")
