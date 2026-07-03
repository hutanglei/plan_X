#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从处罚判决文件夹批量读取Excel，提取标的+时间窗口，生成模式B输入CSV。

修复版：处理多种期间字段名、日期格式、多股票合并、datetime对象等问题。
"""

import os
import re
import glob
import warnings
import pandas as pd
import numpy as np
import tushare as ts

warnings.filterwarnings('ignore')

SRC_DIR = r"D:\BaiduSyncdisk\处罚判决"
OUTPUT_CSV = r"d:\BaiduSyncdisk\code-h\plan_X\watchlist_input.csv"

# ============================================================
# 日期解析
# ============================================================

def parse_date_cn(s):
    """解析中文日期：2021年1月4日 -> 20210104"""
    if pd.isna(s) or not s:
        return ''
    s = str(s).strip()
    m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', s)
    if m:
        return f"{int(m.group(1)):04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    s_clean = s.replace('-', '').replace('/', '').replace('.', '')
    if s_clean.isdigit() and len(s_clean) == 8:
        return s_clean
    return s


def parse_date_any(val):
    """通用日期解析：处理 Timestamp、datetime、字符串、年月格式等"""
    if pd.isna(val) or val is None:
        return ''
    # pandas Timestamp
    if isinstance(val, (pd.Timestamp,)):
        return val.strftime('%Y%m%d')
    # Python datetime
    if hasattr(val, 'strftime'):
        try:
            return val.strftime('%Y%m%d')
        except:
            pass
    s = str(val).strip()
    # 已经是标准格式
    if re.match(r'^\d{8}$', s):
        return s
    # 2017-08-15
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s.replace('-', '')
    # 2019/4/8 (带斜杠，月日不补零)
    m = re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})$', s)
    if m:
        return f"{int(m.group(1)):04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    # 2015/4 (带斜杠，年月)
    m = re.match(r'^(\d{4})/(\d{1,2})$', s)
    if m:
        return f"{int(m.group(1)):04d}{int(m.group(2)):02d}01"
    # 2017-08 (年月，取月初)
    if re.match(r'^\d{4}-\d{2}$', s):
        return s.replace('-', '') + '01'
    # 2017年8月 (年月，取月初)
    m = re.match(r'(\d{4})年(\d{1,2})月$', s)
    if m:
        return f"{int(m.group(1)):04d}{int(m.group(2)):02d}01"
    # 中文日期
    return parse_date_cn(s)


def parse_period(s):
    """解析期间字符串，支持多种格式"""
    if pd.isna(s) or not s:
        return '', ''
    s = str(s).strip()
    # 中文格式：2017年7月24日 - 2018年6月26日
    m = re.match(r'(\d{4}年\d{1,2}月\d{1,2}日)\s*[-~至到]\s*(\d{4}年\d{1,2}月\d{1,2}日)', s)
    if m:
        return parse_date_cn(m.group(1)), parse_date_cn(m.group(2))
    # 标准格式：2017-07-24 - 2018-06-26
    m = re.match(r'(\d{4}-\d{2}-\d{2})\s*[-~至到]\s*(\d{4}-\d{2}-\d{2})', s)
    if m:
        return m.group(1).replace('-', ''), m.group(2).replace('-', '')
    # 年月格式：2017年7月 - 2018年6月
    m = re.match(r'(\d{4}年\d{1,2}月)\s*[-~至到]\s*(\d{4}年\d{1,2}月)', s)
    if m:
        return parse_date_any(m.group(1)), parse_date_any(m.group(2))
    # 年份格式：2003年-2018年11月
    m = re.match(r'(\d{4})年\s*[-~至到]\s*(\d{4}年\d{1,2}月)', s)
    if m:
        return f"{m.group(1)}0101", parse_date_any(m.group(2))
    return '', ''


# ============================================================
# 股票名称处理
# ============================================================

def extract_stock_name(raw_name):
    """提取股票简称"""
    if pd.isna(raw_name) or not raw_name:
        return ''
    name = str(raw_name).strip()
    name = re.sub(r'\([^)]*\)', '', name).strip()
    name = re.sub(r'[Ａ-ＺA-Z]+$', '', name).strip()
    return name


def split_multi_stocks(name_str):
    """拆分多股票名称，如 '利德曼、中源协和等20只股票' -> ['利德曼', '中源协和']"""
    if pd.isna(name_str) or not name_str:
        return []
    s = str(name_str).strip()
    # 去掉 "等N只股票" "等N支股票" 后缀
    s = re.sub(r'等\d+只?股票', '', s)
    s = re.sub(r'等\d+支?股票', '', s)
    # 按顿号、逗号拆分
    parts = re.split(r'[、,，]', s)
    result = []
    for p in parts:
        p = extract_stock_name(p)
        if p and len(p) >= 2:
            result.append(p)
    return result


# ============================================================
# 读取Excel
# ============================================================

def read_one_excel(filepath):
    """读取单个Excel，返回记录列表"""
    try:
        xl = pd.ExcelFile(filepath)
    except Exception as e:
        print(f"  [跳过] 无法读取: {os.path.basename(filepath)} - {e}")
        return []

    # ---- 基本信息 ----
    basic_info = {}
    if '基本信息' in xl.sheet_names:
        df_basic = pd.read_excel(filepath, sheet_name='基本信息', header=None)
        for _, row in df_basic.iterrows():
            key = str(row.iloc[0]).strip() if not pd.isna(row.iloc[0]) else ''
            val = row.iloc[1]  # 保留原始值（可能是datetime）
            val_str = str(val).strip() if not pd.isna(val) else ''

            if key == '涉案股票':
                basic_info['stock_name_raw'] = val_str
            elif key in ('操纵期间', '内幕交易期间', '违法期间'):
                basic_info['period'] = val_str
            elif key == '处罚日期':
                basic_info['penalty_date'] = parse_date_any(val)
            elif key == '当事人':
                basic_info['parties'] = val_str
            elif key == '文号':
                basic_info['doc_id'] = val_str
            elif key == '文件名称':
                basic_info['doc_title'] = val_str

    # ---- 交易详情 ----
    records = []
    if '交易详情' in xl.sheet_names:
        df_detail = pd.read_excel(filepath, sheet_name='交易详情')
        # 标准化列名
        col_map = {}
        for c in df_detail.columns:
            c_str = str(c).strip()
            if '买入区间开始' in c_str or c_str == '开始时间':
                col_map[c] = 'buy_start'
            elif '买入区间结束' in c_str or c_str == '结束时间':
                col_map[c] = 'buy_end'
            elif '标的名称' in c_str or '股票名称' in c_str:
                col_map[c] = 'stock_name_detail'
            elif '阶段' in c_str:
                col_map[c] = 'phase'
        df_detail = df_detail.rename(columns=col_map)

        for _, row in df_detail.iterrows():
            stock_name_raw = str(row.get('stock_name_detail', '')).strip()
            if stock_name_raw in ('', 'nan', 'None'):
                stock_name_raw = basic_info.get('stock_name_raw', '')

            # 拆分多股票
            stock_names = split_multi_stocks(stock_name_raw)
            if not stock_names:
                # 尝试从基本信息获取
                stock_names = split_multi_stocks(basic_info.get('stock_name_raw', ''))

            buy_start = parse_date_any(row.get('buy_start', ''))
            buy_end = parse_date_any(row.get('buy_end', ''))

            if not buy_start or not buy_end:
                continue

            phase = str(row.get('phase', '')).strip() if not pd.isna(row.get('phase', '')) else ''

            for sn in stock_names:
                records.append({
                    'stock_name': sn,
                    'start_date': buy_start,
                    'end_date': buy_end,
                    'phase': phase,
                    'parties': basic_info.get('parties', ''),
                    'doc_id': basic_info.get('doc_id', ''),
                    'penalty_date': basic_info.get('penalty_date', ''),
                    'period_raw': basic_info.get('period', ''),
                    'source_file': os.path.basename(filepath),
                })

    # 如果交易详情为空，尝试从基本信息中提取
    if not records and basic_info.get('period'):
        start_d, end_d = parse_period(basic_info['period'])
        if start_d and end_d:
            stock_names = split_multi_stocks(basic_info.get('stock_name_raw', ''))
            if not stock_names:
                stock_names = [extract_stock_name(basic_info.get('stock_name_raw', ''))]
            for sn in stock_names:
                if sn:
                    records.append({
                        'stock_name': sn,
                        'start_date': start_d,
                        'end_date': end_d,
                        'phase': '',
                        'parties': basic_info.get('parties', ''),
                        'doc_id': basic_info.get('doc_id', ''),
                        'penalty_date': basic_info.get('penalty_date', ''),
                        'period_raw': basic_info.get('period', ''),
                        'source_file': os.path.basename(filepath),
                    })

    return records


# ============================================================
# 股票名称 -> ts_code 映射（含历史名称）
# ============================================================

def build_name_to_code_map():
    """从Tushare获取股票名称到ts_code的映射，包含当前和历史名称"""
    token = os.environ.get('TUSHARE_TOKEN')
    if not token:
        print("[警告] 未设置 TUSHARE_TOKEN")
        return {}, {}

    ts.set_token(token)
    pro = ts.pro_api()

    name_map = {}  # 名称 -> ts_code
    code_info = {}  # ts_code -> {name, industry, list_date, delist_date}

    try:
        # 当前上市股票
        df = pro.stock_basic(exchange='', list_status='L',
                             fields='ts_code,name,industry,list_date')
        for _, row in df.iterrows():
            name = str(row['name']).strip()
            if name not in name_map:
                name_map[name] = row['ts_code']
            code_info[row['ts_code']] = {
                'name': name, 'industry': row.get('industry', ''),
                'list_date': str(row.get('list_date', '')),
            }

        # 退市股票
        df_d = pro.stock_basic(exchange='', list_status='D',
                               fields='ts_code,name,industry,list_date,delist_date')
        for _, row in df_d.iterrows():
            name = str(row['name']).strip()
            if name not in name_map:
                name_map[name] = row['ts_code']
            code_info[row['ts_code']] = {
                'name': name, 'industry': row.get('industry', ''),
                'list_date': str(row.get('list_date', '')),
                'delist_date': str(row.get('delist_date', '')),
            }

        # 暂停上市
        df_p = pro.stock_basic(exchange='', list_status='P',
                               fields='ts_code,name,industry,list_date')
        for _, row in df_p.iterrows():
            name = str(row['name']).strip()
            if name not in name_map:
                name_map[name] = row['ts_code']
            code_info[row['ts_code']] = {
                'name': name, 'industry': row.get('industry', ''),
                'list_date': str(row.get('list_date', '')),
            }

        print(f"[映射] 从Tushare获取 {len(name_map)} 条股票名称映射（含退市/暂停）")
        return name_map, code_info

    except Exception as e:
        print(f"[警告] 获取股票映射失败: {e}")
        return {}, {}


# ============================================================
# 手动映射（已知的历史名称/别名）
# ============================================================

MANUAL_NAME_MAP = {
    # 已更名股票
    '华平股份': '300074.SZ',
    '万通地产': '600246.SH',       # 现名：万通发展
    '东方能源': '000958.SZ',       # 现名：电投产融
    '中昌数据': '600242.SH',       # 已退市
    '当代东方': '000673.SZ',       # 已退市
    '鑫茂科技': '000836.SZ',       # 现名：富通信息
    '安信信托': '600816.SH',       # 现名：建元信托
    '宜华健康': '000150.SZ',       # 已退市
    '欧浦智网': '002711.SZ',       # 已退市
    '新光圆成': '002147.SZ',       # 已退市
    '柏堡龙': '002776.SZ',         # ST柏龙，已退市
    '美尚生态': '300495.SZ',       # 已退市
    '天域生态': '603717.SH',
    '花王股份': '603007.SH',       # ST花王
    '恒久科技': '002808.SZ',
    '新华锦': '600735.SH',
    '联创股份': '300343.SZ',
    '力盛赛车': '002858.SZ',       # 现名：力盛体育
    '聆达股份': '300125.SZ',
    '康惠制药': '603139.SH',
    'ST天业': '600807.SH',         # 现名：济南高新
    '*ST金洲': '000587.SZ',        # 已退市
    '九鼎集团': '430719.BJ',       # 新三板 -> 北交所
    '道尔智控': '',                # 新三板，无法匹配
    # 新增未匹配股票
    '众应互联': '002464.SZ',       # 已退市
    '华电重工': '601226.SH',
    '云内动力': '000903.SZ',
    '易联众': '300096.SZ',         # 现名：ST易联众
    '商业城': '600306.SH',         # 已退市
    '喜临门': '603008.SH',
    '嘉澳环保': '603822.SH',
    '天宝股份': '002220.SZ',       # 已退市
    '天山生物': '300313.SZ',       # ST天山
    '天药股份': '600488.SH',       # 现名：津药药业
    '天铁股份': '300587.SZ',
    '太化股份': '600281.SH',       # 现名：华阳新材
    '宁波热电': '600982.SH',       # 现名：宁波能源
    '广宇发展': '000537.SZ',       # 现名：中绿电
    '湘油泵': '603319.SH',
    '美芝股份': '002856.SZ',
    '贵人鸟': '603555.SH',         # 已退市
    '远兴能源': '000683.SZ',
    '隆华节能': '300263.SZ',       # 现名：隆华科技
    '青海华鼎': '600243.SH',
    '麦迪电气': '300341.SZ',       # 现名：麦克奥迪
    '鼎捷软件': '300378.SZ',       # 现名：鼎捷数智
    '深桑达': '000032.SZ',         # 现名：深桑达A
    '元成股份': '603388.SH',
    '柯利达': '603828.SH',
    '亚振家居': '603389.SH',
    '维格娜丝': '603518.SH',       # 现名：锦泓集团
    '东易日盛': '002713.SZ',
    '济民制药': '603222.SH',       # 现名：济民健康
    '绿城水务': '601368.SH',
    'ST狮头': '600539.SH',         # 现名：狮头股份
    '摩恩电气': '002451.SZ',
    '*ST中富': '000659.SZ',        # 现名：珠海中富
    '*ST星星': '300256.SZ',        # ST星星
    '圣龙股份': '603178.SH',
    '诺邦股份': '603238.SH',
    '康盛股份': '002418.SZ',
    '延华智能': '002178.SZ',
    '爱仕达': '002403.SZ',
    '大庆华科': '000985.SZ',
    '宜宾纸业': '600793.SH',
    '哈森股份': '603958.SH',
    '友邦吊顶': '002718.SZ',
    '金麒麟': '603586.SH',
    '康隆达': '603665.SH',
    '铁流股份': '603926.SH',
    '红蜻蜓': '603116.SH',
    '日月股份': '603218.SH',
    '太平鸟': '603877.SH',
    '秦安股份': '603758.SH',
    '上海亚虹': '603159.SH',
    '恒润股份': '603985.SH',
    '中马传动': '603767.SH',
    '新华医疗': '600587.SH',
    '第一医药': '600833.SH',
    '吉林高速': '601518.SH',
    '大连热电': '600719.SH',
    '诚迈科技': '300598.SZ',
    '和而泰': '002402.SZ',
    '和邦生物': '603077.SH',
}

# ============================================================
# 主流程
# ============================================================

def main():
    xlsx_files = glob.glob(os.path.join(SRC_DIR, '*.xlsx'))
    print(f"共找到 {len(xlsx_files)} 个Excel文件\n")

    # 读取所有文件
    all_records = []
    for i, fpath in enumerate(sorted(xlsx_files)):
        fname = os.path.basename(fpath)
        print(f"[{i+1}/{len(xlsx_files)}] {fname}")
        records = read_one_excel(fpath)
        if records:
            all_records.extend(records)
            print(f"  -> 提取 {len(records)} 条记录")
        else:
            print(f"  -> 无有效记录")

    print(f"\n共提取 {len(all_records)} 条原始记录")

    df = pd.DataFrame(all_records)
    if df.empty:
        print("无有效数据，退出")
        return

    # 去重
    df = df.drop_duplicates(subset=['stock_name', 'start_date', 'end_date'], keep='first')

    # 获取映射
    name_map, code_info = build_name_to_code_map()

    # 合并手动映射
    for name, code in MANUAL_NAME_MAP.items():
        if code and name not in name_map:
            name_map[name] = code

    # 匹配ts_code
    df['ts_code'] = df['stock_name'].map(name_map)

    # 检查未匹配的
    unmatched = df[df['ts_code'].isna()]['stock_name'].unique()
    if len(unmatched) > 0:
        print(f"\n[警告] 以下股票名称未能匹配到ts_code ({len(unmatched)}个):")
        for n in sorted(unmatched):
            print(f"  - {n}")

    # 构建输出
    output_rows = []
    for (ts_code, stock_name), group in df.groupby(['ts_code', 'stock_name'], dropna=False):
        if pd.isna(ts_code) or not ts_code:
            continue

        start_date = group['start_date'].min()
        end_date = group['end_date'].max()

        parties = '、'.join(sorted(set(
            p for p in group['parties'].dropna() if p and p != 'nan'
        )))
        doc_ids = '、'.join(sorted(set(
            d for d in group['doc_id'].dropna() if d and d != 'nan'
        )))

        # 从文件名推断违规类型
        source_files = ' '.join(group['source_file'].unique())
        if '内幕' in source_files or '内幕' in str(group['period_raw'].iloc[0]):
            violation_type = '内幕交易'
        elif '信披' in source_files or '信息披露' in source_files:
            violation_type = '信披违规'
        elif '限制期' in source_files or '短线交易' in source_files:
            violation_type = '短线交易'
        else:
            violation_type = '操纵市场'

        output_rows.append({
            'ts_code': ts_code,
            'start_date': start_date,
            'end_date': end_date,
            'name': stock_name,
            'case_id': doc_ids[:50] if doc_ids else '',
            'violation_type': violation_type,
        })

    out_df = pd.DataFrame(output_rows)
    out_df = out_df.sort_values(['ts_code', 'start_date'])

    out_df.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
    print(f"\n输出文件: {OUTPUT_CSV}")
    print(f"共 {len(out_df)} 条记录")
    print(f"\n前15条预览:")
    print(out_df.head(15).to_string(index=False))

    print(f"\n统计:")
    print(f"  涉及股票: {out_df['ts_code'].nunique()} 只")
    print(f"  日期范围: {out_df['start_date'].min()} ~ {out_df['end_date'].max()}")
    print(f"  违规类型分布:")
    for vt, cnt in out_df['violation_type'].value_counts().items():
        print(f"    {vt}: {cnt}")


if __name__ == '__main__':
    main()
