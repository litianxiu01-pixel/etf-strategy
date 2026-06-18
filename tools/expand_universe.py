#!/usr/bin/env python3
"""Download new ETF data via Tencent K-line and merge into existing universe"""
import json, requests, time, os, re, hashlib

DATA_DIR = os.path.expanduser("~/.qclaw/workspace-main/data/market_regime")

NEW_CODES = [
    ('515070', '人工智能ETF华夏', 'cn'),
    ('515400', '大数据ETF富国', 'cn'),
    ('516010', '游戏ETF国泰', 'cn'),
    ('512980', '传媒ETF广发', 'cn'),
    ('562510', '旅游ETF华夏', 'cn'),
    ('512690', '酒ETF鹏华', 'cn'),
    ('515170', '食品饮料ETF华夏', 'cn'),
    ('515030', '新能源车ETF华夏', 'cn'),
    ('516150', '稀土ETF嘉实', 'cn'),
    ('512400', '有色金属ETF南方', 'cn'),
    ('515220', '煤炭ETF国泰', 'cn'),
    ('515210', '钢铁ETF国泰', 'cn'),
    ('516020', '化工ETF华宝', 'cn'),
    ('512620', '农业ETF天弘', 'cn'),
    ('560150', '红利低波ETF泰康', 'cn'),
    ('515230', '软件ETF国泰', 'cn'),
    ('512720', '计算机ETF国泰', 'cn'),
    ('516110', '汽车ETF国泰', 'cn'),
    ('562390', '中药ETF银华', 'cn'),
    ('516950', '基建ETF银华', 'cn'),
    ('516530', '物流ETF银华', 'cn'),
    ('515640', '家电ETF华夏', 'cn'),
    ('516750', '建材ETF富国', 'cn'),
]

def download_kline(code):
    market = 'sh' if code.startswith(('51','56','58','52')) else 'sz'
    """Download from Tencent K-line API"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {'param': f'{market}{code},day,,,2000,qfq'}
    try:
        r = requests.get(url, params=params, timeout=15,
                         headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code != 200:
            return None
        data = r.json()
        klines = data.get('data', {}).get(f'{market}{code}', {}).get('day', []) or \
                 data.get('data', {}).get(f'{market}{code}', {}).get('qfqday', [])
        if not klines:
            return None
        
        result = {}
        for k in klines:
            date = k[0]
            close = float(k[2])
            vol = float(k[5]) if len(k) > 5 else 0
            open_p = float(k[1])
            high = float(k[3])
            low = float(k[4])
            result[date] = {'close': close, 'volume': vol, 'open': open_p, 'high': high, 'low': low}
        return result
    except Exception as e:
        return None

# Load existing data
with open(f"{DATA_DIR}/combined_daily.json") as f:
    combined = json.load(f)
with open(f"{DATA_DIR}/etf_universe.json") as f:
    universe = json.load(f)

existing_codes = set(universe['etfs'].keys())
existing_dates = {row['date']: i for i, row in enumerate(combined)}

print(f"📥 下载 {len(NEW_CODES)} 只新 ETF...")
success = 0
fail = 0

for code, name, asset_class in NEW_CODES:
    if code in existing_codes:
        print(f"  ⏭️  {code} {name} (已存在)")
        continue
    
    data = download_kline(code)
    if not data:
        print(f"  ❌ {code} {name} 下载失败")
        fail += 1
        continue
    
    # Add to universe
    universe['etfs'][code] = {
        'name': name,
        'assetClass': asset_class,
        'status': 'active',
        'source': 'tencent_kline',
        'added': '2026-06-17',
    }
    
    # Merge into combined_daily
    matched = 0
    new_dates = 0
    for date, row in data.items():
        if date in existing_dates:
            idx = existing_dates[date]
            combined[idx]['etfs'][code] = row
            matched += 1
        else:
            if date not in [r['date'] for r in combined]:
                combined.append({'date': date, 'etfs': {code: row}})
                existing_dates[date] = len(combined) - 1
                new_dates += 1
    
    # Sort combined by date
    combined.sort(key=lambda r: r['date'])
    existing_dates = {row['date']: i for i, row in enumerate(combined)}
    
    print(f"  ✅ {code} {name}  {len(data)}条记录 (匹配{matched}天)")
    success += 1
    time.sleep(0.3)  # Rate limit

# Save
with open(f"{DATA_DIR}/combined_daily.json", 'w') as f:
    json.dump(combined, f, ensure_ascii=False)
with open(f"{DATA_DIR}/etf_universe.json", 'w') as f:
    json.dump(universe, f, ensure_ascii=False, indent=2)

print(f"\n📊 完成: {success}成功, {fail}失败")
print(f"   Universe: {len(existing_codes)} → {len(universe['etfs'])} 只")
print(f"   Combined: {len(combined)} 天")
print(f"   最新日: {combined[-1]['date']}")
print(f"   最新日 ETF 数: {len(combined[-1]['etfs'])}")
