"""
全量 Universe 扫描器 v2 — 使用腾讯财经K线API（更稳定）
"""
import json, os, time, requests
from datetime import datetime
from collections import defaultdict

SAVE_DIR = os.path.expanduser("~/.qclaw/workspace-main/data/market_regime")
os.makedirs(SAVE_DIR, exist_ok=True)

# ─── 从已下载数据加载 ETF 列表 ───
# 先复用已有15只 + 扩展精选池
existing_codes = [
    '159611', '159985', '515880', '518880', '512680',
    '159740', '513060', '513310', '159501', '513820',
    '159755', '512170', '159992', '511010', '159201',
]

# ─── 扩展 Universe：全市场精选 ───
# 从 Sina ETF 列表筛选后的精选池
expanded_codes = [
    # 美股 (已覆盖的 + 新增)
    '159501', '159941', '159696', '159660', '159659', '159655', '159632', '159612', '159513', '513500', '513100', '513300', '513050',
    # 港股
    '159740', '513060', '513820', '513090', '513180', '159605', '159607', '513130', '159726', '513770',
    # 黄金
    '518880', '518660', '518680', '159812', '159830', '159831', '159322', '159937',
    # 国债
    '511010', '511020', '511030', '511260', '511090', '511220', '511180', '159649', '159650', '159651',
    # A股宽基
    '510300', '510050', '510500', '159915', '588000', '588080', '512100', '159845', '159949', '510880',
    # A股行业
    '512660', '512760', '512480', '515050', '516510', '515790', '512880', '512800', '512200', '159611', '159985',
    '515880', '512680', '512170', '159992', '159755', '563230', '159201',
    # 商品
    '159985',  # 豆粕
]

# 去重
seen = set()
unique_codes = []
for c in expanded_codes:
    if c not in seen:
        seen.add(c)
        unique_codes.append(c)

print(f"扫描 Universe: {len(unique_codes)} 只 ETF")
print("=" * 60)

# ─── 腾讯K线API ───
def fetch_tencent_kline(code, exchange, period='day', limit=2000):
    """腾讯财经K线接口"""
    prefix = 'sh' if exchange == 'SH' else 'sz'
    full_code = f"{prefix}{code}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={full_code},{period},,,{limit},qfq"
    
    try:
        r = requests.get(url, timeout=10, headers={
            'Referer': 'https://finance.qq.com/',
            'User-Agent': 'Mozilla/5.0'
        })
        data = r.json()
        
        if data.get('code') != 0:
            return None
        
        klines = data.get('data', {}).get(full_code, {})
        if period == 'day':
            klines = klines.get('qfqday', klines.get('day', []))
        else:
            klines = klines.get(f'qfq{period}', klines.get(period, []))
        
        if not klines:
            return None
        
        records = []
        for k in klines:
            records.append({
                'date': k[0],
                'open': float(k[1]),
                'close': float(k[2]),
                'high': float(k[3]),
                'low': float(k[4]),
                'volume': int(float(k[5])),
            })
        return records
    except Exception as e:
        return None

# ─── 下载 ───
success = 0
failed = []
all_data = {}
total_records = 0

for i, code in enumerate(unique_codes):
    # 确定交易所
    if code.startswith('51') or code.startswith('56') or code.startswith('58'):
        exchange = 'SH'
    else:
        exchange = 'SZ'
    
    records = fetch_tencent_kline(code, exchange)
    
    if records and len(records) > 0:
        all_data[code] = records
        success += 1
        total_records += len(records)
    else:
        # 可能交易所搞反了，试试另一边
        alt_exchange = 'SZ' if exchange == 'SH' else 'SH'
        records = fetch_tencent_kline(code, alt_exchange)
        if records and len(records) > 0:
            all_data[code] = records
            success += 1
            total_records += len(records)
        else:
            failed.append(code)
    
    if (i + 1) % 10 == 0:
        print(f"  [{i+1}/{len(unique_codes)}] {success} ok, {len(failed)} fail, {total_records} records")
    
    time.sleep(0.15)

print(f"\n结果: {success}/{len(unique_codes)} 成功, {len(failed)} 失败")
if failed:
    print(f"失败代码: {failed}")

# ─── 保存 ───
etf_dir = os.path.join(SAVE_DIR, 'etfs')
os.makedirs(etf_dir, exist_ok=True)

# 资产分类
def classify_by_code(code):
    if code in ['159501', '159941', '159696', '159660', '159659', '159655', '159632', '159612', '159513', '513500', '513100', '513300', '513050']:
        return 'us'
    if code in ['159740', '513060', '513820', '513090', '513180', '159605', '159607', '513130', '159726', '513770']:
        return 'hk'
    if code in ['518880', '518660', '518680', '159812', '159830', '159831', '159322', '159937']:
        return 'gold'
    if code in ['511010', '511020', '511030', '511260', '511090', '511220', '511180', '159649', '159650', '159651']:
        return 'bond'
    if code in ['159985']:
        return 'commodity'
    return 'cn'

universe_meta = {
    'generatedAt': datetime.now().isoformat(),
    'totalActive': success,
    'totalRecords': total_records,
    'source': 'Tencent Finance K-line API',
    'etfs': {}
}

for code, records in all_data.items():
    # Save individual file
    fname = os.path.join(etf_dir, f"etf_{code}.json")
    with open(fname, 'w') as f:
        json.dump(records, f, ensure_ascii=False)
    
    # Universe metadata
    universe_meta['etfs'][code] = {
        'numCode': code,
        'exchange': 'SH' if (code.startswith('51') or code.startswith('56') or code.startswith('58')) else 'SZ',
        'assetClass': classify_by_code(code),
        'status': 'active' if len(records) >= 120 else 'pending',
        'historyStart': records[0]['date'],
        'historyEnd': records[-1]['date'],
        'historyDays': len(records),
    }

with open(os.path.join(SAVE_DIR, 'etf_universe.json'), 'w') as f:
    json.dump(universe_meta, f, ensure_ascii=False, indent=2)

# ─── 统计 ───
print(f"\n{'='*60}")
print(f"汇总")
print(f"{'='*60}")
print(f"Universe: {success} 只 ETF, {total_records:,} 条日线记录")

stats = defaultdict(lambda: {'count': 0, 'days': 0, 'start': '9999', 'end': '0000'})
for code, records in all_data.items():
    cls = classify_by_code(code)
    stats[cls]['count'] += 1
    stats[cls]['days'] += len(records)
    if records[0]['date'] < stats[cls]['start']:
        stats[cls]['start'] = records[0]['date']
    if records[-1]['date'] > stats[cls]['end']:
        stats[cls]['end'] = records[-1]['date']

for cls in ['cn', 'us', 'hk', 'gold', 'bond', 'commodity']:
    s = stats[cls]
    if s['count'] > 0:
        print(f"  {cls}: {s['count']}只 {s['days']:,}条 [{s['start']}~{s['end']}]")

print(f"\n✅ 全量扫描完成 → {SAVE_DIR}/")
print(f"   etf_universe.json  ({success}只)")
print(f"   etfs/etf_*.json    ({success}个独立文件)")
