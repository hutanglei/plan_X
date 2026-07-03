import pandas as pd
import re

df = pd.read_csv(r'd:\BaiduSyncdisk\code-h\plan_X\watchlist_input.csv',
                 dtype={'ts_code': str, 'start_date': str, 'end_date': str})

print("=== 自检 ===")

# 1. 必选列
required = ['ts_code', 'start_date', 'end_date']
for c in required:
    nulls = df[c].isna().sum()
    print(f"  {c}: {nulls} 空值")

# 2. 日期格式
bad_dates = []
for i, row in df.iterrows():
    for dcol in ['start_date', 'end_date']:
        if not re.match(r'^\d{8}$', str(row[dcol])):
            bad_dates.append((i, row['ts_code'], dcol, row[dcol]))
if bad_dates:
    print(f"  日期格式异常: {len(bad_dates)} 条")
    for b in bad_dates[:5]: print(f"    {b}")
else:
    print("  日期格式: 全部OK (YYYYMMDD)")

# 3. ts_code格式
bad_codes = [c for c in df['ts_code'] if not re.match(r'^\d{6}\.[A-Z]{2,3}$', str(c))]
print(f"  ts_code格式异常: {len(bad_codes)} 条")

# 4. 统计
print(f"\n总记录: {len(df)}")
print(f"唯一股票: {df['ts_code'].nunique()}")
print(f"日期范围: {df['start_date'].min()} ~ {df['end_date'].max()}")
print(f"违规类型: {df['violation_type'].value_counts().to_dict()}")

# 5. 同一股票多窗口
dup = df.groupby('ts_code').size()
multi = dup[dup > 1]
if len(multi) > 0:
    print(f"\n同一股票多窗口 ({len(multi)} 只):")
    for tc, cnt in multi.head(15).items():
        name = df[df['ts_code']==tc]['name'].iloc[0]
        windows = df[df['ts_code']==tc][['start_date','end_date']].values
        print(f"  {tc} {name}: {cnt}个窗口 - {windows.tolist()}")

print("\n=== 自检通过 ===")
