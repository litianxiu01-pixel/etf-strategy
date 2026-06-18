"""
构建 Combined Daily JSON（对齐69只ETF到统一日期表）
然后运行 regime detector + 选股逻辑
"""
import json, os
from datetime import datetime
from collections import defaultdict

SAVE_DIR = os.path.expanduser("~/.qclaw/workspace-main/data/market_regime")
etf_dir = os.path.join(SAVE_DIR, 'etfs')

# ─── 加载所有 ETF 数据 ───
print("加载 ETF 数据...")
all_etfs = {}  # code -> {date -> row}
for fname in sorted(os.listdir(etf_dir)):
    if not fname.endswith('.json'):
        continue
    code = fname.replace('etf_', '').replace('.json', '')
    with open(os.path.join(etf_dir, fname)) as f:
        records = json.load(f)
    all_etfs[code] = {r['date']: r for r in records}

print(f"加载 {len(all_etfs)} 只 ETF")

# ─── 构建全局日期表 ───
all_dates = set()
for code, data in all_etfs.items():
    all_dates.update(data.keys())
all_dates = sorted(all_dates)
print(f"全局日期数: {len(all_dates)}")

# ─── 构建 combined_daily.json ───
combined = []
for date in all_dates:
    etfs_data = {}
    for code, data in all_etfs.items():
        if date in data:
            etfs_data[code] = data[date]
    
    if len(etfs_data) >= 5:  # 至少5只ETF有数据
        combined.append({
            'date': date,
            'etfs': etfs_data,
        })

print(f"Combined daily: {len(combined)} 天")

# 保存
combined_path = os.path.join(SAVE_DIR, 'combined_daily.json')
with open(combined_path, 'w') as f:
    json.dump(combined, f, ensure_ascii=False)
print(f"Saved: {combined_path} ({os.path.getsize(combined_path)/1024/1024:.1f} MB)")

# ─── 加载 Universe ───
with open(os.path.join(SAVE_DIR, 'etf_universe.json')) as f:
    universe = json.load(f)

print(f"\n{'='*60}")
print(f"Universe 摘要")
print(f"{'='*60}")
for cls in ['cn', 'us', 'hk', 'gold', 'bond', 'commodity']:
    etfs = [info for code, info in universe['etfs'].items() if info['assetClass'] == cls]
    if etfs:
        print(f"\n  {cls} ({len(etfs)}只):")
        for e in sorted(etfs, key=lambda x: x['historyDays'], reverse=True)[:5]:
            print(f"    {e['numCode']:<8s} {universe['etfs'][e['numCode']].get('name',''):<16s} {e['historyDays']}d [{e['historyStart']}~{e['historyEnd']}]")
