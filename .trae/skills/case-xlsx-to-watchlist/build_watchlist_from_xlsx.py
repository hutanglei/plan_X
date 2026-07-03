"""
案例 xlsx → watchlist.csv 通用提取脚本

将一批结构异构的Excel案例文件（如证监会处罚决定书的"行情分析"报告）批量提取为
abnormal-trading-detection skill 模式B所需的结构化 watchlist：
  ts_code, start_date, end_date, name, case_id, violation_type

使用前请按需修改顶部的 BASE_DIR 和 OUTPUT_CSV 常量。
依赖：pandas, openpyxl, tushare (环境变量 TUSHARE_TOKEN)

详细方法论见同目录 SKILL.md
"""

import os
import re
import pandas as pd
import tushare as ts

# ====== 用户配置 ======
BASE_DIR = r'D:\BaiduSyncdisk\建仓前后行情新闻'
OUTPUT_CSV = r'd:\BaiduSyncdisk\code-h\plan_X\watchlist_jiancang.csv'
DEFAULT_VIOLATION_TYPE = '操纵市场'
# =====================


def get_pro_api():
    token = os.environ.get('TUSHARE_TOKEN')
    if not token:
        raise RuntimeError('请设置环境变量 TUSHARE_TOKEN')
    ts.set_token(token)
    return ts.pro_api()


# 已知股票名(含历史曾用名) -> ts_code 兜底映射
# 这些股票多为已退市/更名，Tushare当前名称无法直接匹配
# 复用时根据自己的数据集扩展
MANUAL_NAME_TO_CODE = {
    '嘉澳环保': '603822.SH', '天铁股份': '300587.SZ', '鼎捷软件': '300378.SZ',
    '亚振家居': '603389.SH', '宜华健康': '000150.SZ', 'ST天业': '600807.SH',
    '绿城水务': '601368.SH', '商业城': '600306.SH', '青海华鼎': '600243.SH',
    '济民制药': '603222.SH', '柏堡龙': '002776.SZ', '华电重工': '601226.SH',
    '天药股份': '600488.SH', '深桑达A': '000032.SZ', '麦迪电气': '300341.SZ',
    '当代东方': '000673.SZ', '维格娜丝': '603518.SH', '美尚生态': '300495.SZ',
    '东易日盛': '002713.SZ', '美芝股份': '002856.SZ', '湘油泵': '603319.SH',
    '康惠制药': '603139.SH', '柯利达': '603828.SH',
}


def load_stock_basic(pro):
    """
    三层兜底映射构建：
      1) Tushare stock_basic 当前名（list_status=L/D/P）
      2) Tushare namechange 历史曾用名（覆盖未存在的）
      3) MANUAL_NAME_TO_CODE 手工映射（最高优先级）
    """
    cache = r'd:\BaiduSyncdisk\code-h\plan_X\.trae\skills\abnormal-trading-detection\cache\stock_basic_cache.csv'
    namechange_cache = r'd:\BaiduSyncdisk\code-h\plan_X\.trae\skills\abnormal-trading-detection\cache\namechange_cache.csv'

    if os.path.exists(cache):
        df = pd.read_csv(cache, dtype={'ts_code': str})
    else:
        dfs = []
        for status in ['L', 'D', 'P']:
            try:
                d = pro.stock_basic(exchange='', list_status=status, fields='ts_code,name')
                if d is not None and len(d) > 0:
                    dfs.append(d)
            except Exception:
                pass
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        df.to_csv(cache, index=False, encoding='utf-8-sig')

    name_map = {}
    for _, r in df.iterrows():
        nm = str(r['name']).replace(' ', '').strip()
        name_map[nm] = r['ts_code']

    # 合并历史曾用名映射
    if os.path.exists(namechange_cache):
        nc = pd.read_csv(namechange_cache, dtype={'ts_code': str})
        for _, r in nc.iterrows():
            nm = str(r['name']).replace(' ', '').strip()
            if nm and nm not in name_map:
                name_map[nm] = r['ts_code']
    else:
        # 首次运行：拉取一次namechange（注意接口有10000条上限）
        try:
            nc = pro.namechange(fields='ts_code,name,start_date,end_date,change_reason')
            os.makedirs(os.path.dirname(namechange_cache), exist_ok=True)
            nc.to_csv(namechange_cache, index=False, encoding='utf-8-sig')
            for _, r in nc.iterrows():
                nm = str(r['name']).replace(' ', '').strip()
                if nm and nm not in name_map:
                    name_map[nm] = r['ts_code']
        except Exception:
            pass

    # 合并手工兜底映射（优先级最高）
    name_map.update(MANUAL_NAME_TO_CODE)
    return name_map


def parse_filename(fname):
    """
    从案例 xlsx 文件名解析：punish_date, persons, stock_name
    示例文件名：
      20210518_中国证监会行政处罚决定书（陈建铭、谢晶、胡侃）_中国证券监督管理委员会_中源家居_行情分析.xlsx
    """
    base = fname.replace('.xlsx', '')
    parts = base.split('_')
    # 处罚日期：第一段是8位数字日期（可能带后缀如"20210106_1"，所以取前8位）
    punish_date = ''
    if parts:
        m = re.match(r'^(\d{8})', parts[0])
        if m:
            punish_date = m.group(1)
    # 当事人：第二段中括号内的内容
    persons = ''
    if len(parts) >= 2:
        m = re.search(r'[（(](.+?)[）)]', parts[1])
        if m:
            persons = m.group(1)
        elif len(parts) >= 3:
            m = re.search(r'[（(](.+?)[）)]', parts[2])
            if m:
                persons = m.group(1)
    # 股票名称：在 "_中国证券监督管理委员会_" 之后、"_行情分析" 之前
    stock_name = ''
    # 用全部子段进行扫描，找出非通用关键词的段
    skip_tokens = {'行情分析', '扩充版', '最终版', '精简版',
                   '行业信息时间线', '利好利空', 'v2',
                   '中国证券监督管理委员会', '行政处罚决定书'}
    # 优先匹配 "中国证券监督管理委员会_<股票名>_行情分析" 模式
    if '中国证券监督管理委员会' in base:
        # 找最后一个"中国证券监督管理委员会"之后的内容（处理前缀干扰）
        idx = base.rfind('中国证券监督管理委员会')
        after = base[idx + len('中国证券监督管理委员会'):].strip('_')
        sub_parts = after.split('_')
        for p in sub_parts:
            p = p.strip()
            if p and p not in skip_tokens and not re.match(r'^v\d+$', p):
                stock_name = p
                break
    return punish_date, persons, stock_name


def get_all_text(fpath):
    """读取文件所有sheet内容拼接为一个字符串"""
    texts = []
    try:
        xls = pd.ExcelFile(fpath)
        for sname in xls.sheet_names:
            try:
                df = pd.read_excel(fpath, sheet_name=sname, header=None)
                texts.append(df.to_string())
            except Exception:
                pass
    except Exception:
        pass
    return '\n'.join(texts), xls if 'xls' in dir() else None


def extract_year(fpath, fallback_year):
    """
    建仓年份识别：取"行业信息时间线"sheet中时间列出现频次最高的年份（众数）。
    避开行业新闻里引用的历史年份污染。
    """
    try:
        xls = pd.ExcelFile(fpath)

        # 策略A：从"行业信息时间线"sheet的日期列统计众数年份
        for sname in xls.sheet_names:
            if '行业信息' in sname or '时间线' in sname:
                try:
                    df = pd.read_excel(fpath, sheet_name=sname)
                    time_col = None
                    for cand in ['时间', '日期', 'date']:
                        if cand in df.columns:
                            time_col = cand
                            break
                    # 处理合并标题行：如果第一列是合并标题，尝试 header=1 重新读
                    if time_col is None:
                        try:
                            df2 = pd.read_excel(fpath, sheet_name=sname, header=1)
                            for cand in ['时间', '日期', 'date']:
                                if cand in df2.columns:
                                    time_col = cand
                                    df = df2
                                    break
                        except Exception:
                            pass
                    if time_col:
                        years = []
                        for v in df[time_col].dropna():
                            ms = re.findall(r'(20\d{2})', str(v))
                            for y in ms:
                                yi = int(y)
                                if 2000 < yi <= 2024:
                                    years.append(yi)
                        if years:
                            from collections import Counter
                            return str(Counter(years).most_common(1)[0][0])
                except Exception:
                    pass

        # 策略B：从"频率变化趋势分析"中找"时间范围"/"建仓开始时间"
        for sname in xls.sheet_names:
            if '频率' in sname or '趋势' in sname:
                try:
                    df = pd.read_excel(fpath, sheet_name=sname)
                    for col in df.columns:
                        for v in df[col].dropna():
                            s = str(v)
                            if '时间范围' in s or '建仓开始时间' in s or '建仓期' in s:
                                m = re.search(r'(20\d{2})', s)
                                if m:
                                    yi = int(m.group(1))
                                    if 2000 < yi <= 2024:
                                        return str(yi)
                except Exception:
                    pass
    except Exception:
        pass

    return fallback_year


def parse_one_date_range(text, year):
    """
    支持多种日期格式。返回 (start_yyyymmdd, end_yyyymmdd) 或 (None, None)
    跨年判断：结束月份 < 开始月份 → 年份+1
    """
    if not isinstance(text, str):
        return None, None
    text = text.strip()

    # 1. 完整 YYYY-MM-DD ~ YYYY-MM-DD (或 - / . 分隔符)
    m = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*[~至到\-—]\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', text)
    if m:
        y1, m1, d1, y2, m2, d2 = m.groups()
        return f'{y1}{int(m1):02d}{int(d1):02d}', f'{y2}{int(m2):02d}{int(d2):02d}'

    # 2. 单一完整日期 YYYY-MM-DD
    m = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', text)
    if m:
        y1, m1, d1 = m.groups()
        s = f'{y1}{int(m1):02d}{int(d1):02d}'
        return s, s

    # 3. M月D日 至 M月D日 或 M月D日-M月D日
    pairs = re.findall(r'(\d{1,2})月(\d{1,2})日', text)
    if len(pairs) >= 2:
        start = f'{year}{int(pairs[0][0]):02d}{int(pairs[0][1]):02d}'
        end_m = int(pairs[1][0])
        end_d = int(pairs[1][1])
        # 跨年处理：如果结束月份<开始月份，年份+1
        end_year = int(year)
        if end_m < int(pairs[0][0]):
            end_year += 1
        end = f'{end_year}{end_m:02d}{end_d:02d}'
        return start, end
    if len(pairs) == 1:
        s = f'{year}{int(pairs[0][0]):02d}{int(pairs[0][1]):02d}'
        return s, s

    # 4. M.D-M.D 或 M/D-M/D
    m = re.search(r'(\d{1,2})[./](\d{1,2})\s*[-~]\s*(\d{1,2})[./](\d{1,2})', text)
    if m:
        m1, d1, m2, d2 = m.groups()
        start = f'{year}{int(m1):02d}{int(d1):02d}'
        end_year = int(year)
        if int(m2) < int(m1):
            end_year += 1
        end = f'{end_year}{int(m2):02d}{int(d2):02d}'
        return start, end

    # 5. YYYY年M月D日 单一中文日期
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    if m:
        s = f'{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}'
        return s, s

    # 6. M.D 单一月日
    m = re.search(r'(\d{1,2})[./](\d{1,2})', text)
    if m:
        m1, d1 = m.groups()
        if 1 <= int(m1) <= 12 and 1 <= int(d1) <= 31:
            s = f'{year}{int(m1):02d}{int(d1):02d}'
            return s, s

    return None, None


def extract_window(fpath, year):
    """
    四层兜底提取时间窗口：
      策略1：从"频率变化趋势分析"sheet提取"时间范围"/"建仓开始时间"
      策略2：从"利好与利空数量变化"sheet的时间列首尾行
      策略3：从"行业信息时间线"sheet的合并标题行或时间列首尾
      策略4：全文兜底扫描关键词（"分析范围"/"建仓期"/"建仓前后两周"）
    """
    try:
        xls = pd.ExcelFile(fpath)
    except Exception:
        return None, None

    start_date = None
    end_date = None

    # ---- 策略1：从"频率变化趋势分析" sheet提取"时间范围"和"建仓开始时间" ----
    for sname in xls.sheet_names:
        if '频率' in sname or '趋势分析' in sname:
            try:
                df = pd.read_excel(fpath, sheet_name=sname)
                for col in df.columns:
                    col_data = df[col].dropna().astype(str)
                    time_range_line = [v for v in col_data if '时间范围' in v or '分析时间' in v]
                    start_line = [v for v in col_data if '建仓开始时间' in v]
                    for line in time_range_line:
                        # "时间范围：2019年12月19日—2020年1月17日" 中文格式
                        m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日\s*[-~至到—]\s*(\d{4})年(\d{1,2})月(\d{1,2})日', line)
                        if m:
                            return (f'{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}',
                                    f'{m.group(4)}{int(m.group(5)):02d}{int(m.group(6)):02d}')
                        # "时间范围：2019-01-14 ~ 2019-02-15" 标准格式
                        m = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*[~至到\-—]\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', line)
                        if m:
                            return (f'{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}',
                                    f'{m.group(4)}{int(m.group(5)):02d}{int(m.group(6)):02d}')
                        # "前后各半个月"形式
                        m = re.search(r'时间范围[：:]\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})', line)
                        if m:
                            from datetime import datetime, timedelta
                            center = datetime.strptime(m.group(1).replace('/', '-').replace('.', '-'), '%Y-%m-%d')
                            start_date = (center - timedelta(days=15)).strftime('%Y%m%d')
                            end_date = (center + timedelta(days=15)).strftime('%Y%m%d')
                            return start_date, end_date
                    if not start_date:
                        for line in start_line:
                            m = re.search(r'建仓开始时间[：:]\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})', line)
                            if m:
                                s = m.group(1).replace('-', '').replace('/', '').replace('.', '')
                                from datetime import datetime, timedelta
                                center = datetime.strptime(s, '%Y%m%d')
                                start_date = (center - timedelta(days=7)).strftime('%Y%m%d')
                                end_date = (center + timedelta(days=7)).strftime('%Y%m%d')
                                return start_date, end_date
            except Exception:
                pass

    # ---- 策略2：从"利好与利空数量变化" sheet ----
    sheet_name = None
    for s in xls.sheet_names:
        if '利好' in s and '利空' in s and ('数量' in s or '变化' in s):
            sheet_name = s
            break

    if sheet_name:
        try:
            df = pd.read_excel(fpath, sheet_name=sheet_name)
            if not df.empty:
                # 确定时间列
                time_col = None
                for cand in ['时间范围', '时间段', '时间周期', '周次', '时间', '周期', '日期']:
                    if cand in df.columns:
                        time_col = cand
                        break
                if time_col is None:
                    time_col = df.columns[0]

                time_texts = [str(v) for v in df[time_col].dropna()]
                if '时间范围' in df.columns and '时间范围' != time_col:
                    rt = [str(v) for v in df['时间范围'].dropna()]
                    if rt and str(rt[0]).strip():
                        time_texts = rt

                for t in time_texts:
                    s, e = parse_one_date_range(t, year)
                    if s and not start_date:
                        start_date = s
                    if e and not end_date:
                        end_date = e
                for t in reversed(time_texts):
                    s, e = parse_one_date_range(t, year)
                    if e and (end_date is None or e != start_date):
                        end_date = e
                        break

                if start_date and end_date:
                    return start_date, end_date
        except Exception:
            pass

    # ---- 策略3：从行业信息时间线sheet的首尾日期 ----
    for sname in xls.sheet_names:
        if '行业信息' in sname:
            try:
                df = pd.read_excel(fpath, sheet_name=sname)
                # 尝试从标题行提取建仓期信息
                first_col_name = df.columns[0]
                m = re.search(r'建仓期[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日[-~至到](\d{4})年(\d{1,2})月(\d{1,2})日', str(first_col_name))
                if m:
                    return (f'{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}',
                            f'{m.group(4)}{int(m.group(5)):02d}{int(m.group(6)):02d}')
                m = re.search(r'(\d{4})[./](\d{1,2})[./](\d{1,2})[-~至到](\d{4})[./](\d{1,2})[./](\d{1,2})', str(first_col_name))
                if m:
                    return (f'{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}',
                            f'{m.group(4)}{int(m.group(5)):02d}{int(m.group(6)):02d}')
                # 从"时间"列提取首尾日期
                time_col = None
                for cand in ['时间', '日期', 'date']:
                    if cand in df.columns:
                        time_col = cand
                        break
                if time_col:
                    dates = []
                    for v in df[time_col].dropna():
                        s, e = parse_one_date_range(str(v), year)
                        if s:
                            dates.append(s)
                    if len(dates) >= 2:
                        return dates[0], dates[-1]
            except Exception:
                pass

    # ---- 策略4：扫描全文中的"分析范围""建仓期""建仓前后" 等关键词 ----
    text, _ = get_all_text(fpath)
    patterns = [
        r'分析范围[：:]\s*(\d{4})[./](\d{1,2})[./](\d{1,2})\s*[-~]\s*(\d{1,2})[./](\d{1,2})',
        r'分析范围[：:]\s*(\d{4})[./](\d{1,2})[./](\d{1,2})\s*[-~]\s*(\d{4})[./](\d{1,2})[./](\d{1,2})',
        r'建仓前后[一二两三四五六七八九十\d]*周\s*[（(](\d{4})\.(\d{1,2})\.(\d{1,2})\s*[-~]\s*(\d{1,2})\.(\d{1,2})',
        r'建仓期[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*[-~至到]\s*(\d{1,2})月(\d{1,2})日',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            groups = m.groups()
            if len(groups) == 5:
                y1, m1, d1, m2, d2 = groups
                end_year = int(y1)
                if int(m2) < int(m1):
                    end_year += 1
                return (f'{y1}{int(m1):02d}{int(d1):02d}',
                        f'{end_year}{int(m2):02d}{int(d2):02d}')
            elif len(groups) == 6:
                y1, m1, d1, *rest = groups
                if len(rest) == 3:
                    y2, m2, d2 = rest
                    return (f'{y1}{int(m1):02d}{int(d1):02d}',
                            f'{y2}{int(m2):02d}{int(d2):02d}')

    # 通用模式: "YYYY.M.D-M.D" 或 "YYYY-M-D~M-D"
    m = re.search(r'(\d{4})[./-](\d{1,2})[./-](\d{1,2})\s*[-~]\s*(\d{1,2})[./-](\d{1,2})(?!\d)', text)
    if m:
        y1, m1, d1, m2, d2 = m.groups()
        end_year = int(y1)
        if int(m2) < int(m1):
            end_year += 1
        return (f'{y1}{int(m1):02d}{int(d1):02d}',
                f'{end_year}{int(m2):02d}{int(d2):02d}')

    return start_date, end_date


def _add_suffix(code_6):
    """根据6位代码前缀添加交易所后缀"""
    if not code_6 or len(code_6) != 6:
        return None
    if code_6.startswith(('60', '68', '90', '50', '51', '52', '56', '58')):
        return f'{code_6}.SH'
    if code_6.startswith(('00', '30', '15', '16', '20')):
        return f'{code_6}.SZ'
    if code_6.startswith(('43', '83', '87', '88', '92')):
        return f'{code_6}.BJ'
    return None


def extract_code(fpath, stock_name, name_map):
    """从文件中提取ts_code，多重策略"""
    text, _ = get_all_text(fpath)

    # 1. 带后缀的代码
    m = re.search(r'(\d{6})\.(SH|SZ|BJ)', text, re.IGNORECASE)
    if m:
        return f'{m.group(1)}.{m.group(2).upper()}'

    # 2. 紧邻股票名：stock_name(代码) 或 stock_name:代码
    if stock_name:
        pat = re.compile(re.escape(stock_name) + r'[（(\[:：\s]*?(\d{6})')
        m = pat.search(text)
        if m:
            code = _add_suffix(m.group(1))
            if code:
                return code

    # 3. 任意6位数字
    codes = re.findall(r'\b(\d{6})\b', text)
    valid_codes = [c for c in codes
                   if c[0] in '600368' or c.startswith(('00', '30', '43', '83', '87', '88', '92'))]
    if valid_codes:
        code = _add_suffix(valid_codes[0])
        if code:
            return code

    # 4. 用名称反查
    if stock_name:
        key = stock_name.replace(' ', '').strip()
        if key in name_map:
            return name_map[key]
        for prefix in ['*ST', 'ST', 'XD', 'XR']:
            if key.startswith(prefix):
                t = key[len(prefix):]
                if t in name_map:
                    return name_map[t]
        for prefix in ['ST', '*ST']:
            t = prefix + key
            if t in name_map:
                return name_map[t]
        for n, c in name_map.items():
            if n and key and (n == key or n.endswith(key) or key.endswith(n)):
                return c

    return None


def validate_records(records):
    """数据质量校验"""
    if not records:
        return []
    df = pd.DataFrame(records)
    issues = []
    # 1. start_date <= end_date
    bad1 = df[df['start_date'] > df['end_date']]
    if len(bad1) > 0:
        issues.append(f'❌ {len(bad1)}条 start_date > end_date（跨年未处理）')
    # 2. start_date < punish_date
    df['punish_date'] = df['case_id'].str.extract(r'(\d{8})')[0]
    bad2 = df[df['start_date'] >= df['punish_date']]
    if len(bad2) > 0:
        issues.append(f'❌ {len(bad2)}条 start_date >= punish_date（建仓晚于处罚）')
    # 3. 列无空值
    nulls = df.isnull().sum().sum()
    if nulls > 0:
        issues.append(f'❌ {nulls} 个空值')
    return issues


def main():
    pro = get_pro_api()
    print('[1/3] 加载Tushare股票名称->代码映射...')
    name_map = load_stock_basic(pro)
    print(f'  -> 共 {len(name_map)} 个映射')

    files = sorted([f for f in os.listdir(BASE_DIR)
                    if f.endswith('.xlsx') and not f.startswith('~')])
    print(f'\n[2/3] 扫描 {len(files)} 个xlsx文件...')

    records = []
    skipped = []

    for idx, fname in enumerate(files, 1):
        fpath = os.path.join(BASE_DIR, fname)
        punish_date, persons, stock_name = parse_filename(fname)

        if not stock_name:
            skipped.append((fname, 'no_stock_name'))
            continue

        fallback_year = str(int(punish_date[:4]) - 1) if punish_date[:4].isdigit() else '2020'
        year = extract_year(fpath, fallback_year)

        start_date, end_date = extract_window(fpath, year)
        if not start_date or not end_date:
            skipped.append((fname, f'no_date_range (year={year})'))
            continue

        ts_code = extract_code(fpath, stock_name, name_map)
        if not ts_code:
            skipped.append((fname, f'no_code_for_{stock_name}'))
            continue

        case_id = f'{punish_date}_{persons}' if persons else punish_date
        records.append({
            'ts_code': ts_code,
            'start_date': start_date,
            'end_date': end_date,
            'name': stock_name,
            'case_id': case_id,
            'violation_type': DEFAULT_VIOLATION_TYPE,
        })
        if idx % 20 == 0 or idx == len(files):
            print(f'  进度 {idx}/{len(files)}, 已采集 {len(records)} 条')

    print(f'\n[3/3] 写出CSV...')
    df_out = pd.DataFrame(records)
    df_out.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
    print(f'  -> 已保存 {len(df_out)} 条记录到: {OUTPUT_CSV}')

    if skipped:
        print(f'\n跳过 {len(skipped)} 个文件:')
        for f, reason in skipped:
            print(f'  [{reason}] {f}')

    # 数据质量校验
    print(f'\n=== 数据质量校验 ===')
    issues = validate_records(records)
    if not issues:
        print('  ✅ 全部校验通过')
    else:
        for it in issues:
            print(f'  {it}')

    print(f'\n=== 汇总 ===')
    print(f'输入文件: {len(files)}')
    print(f'成功记录: {len(records)}')
    print(f'跳过文件: {len(skipped)}')


if __name__ == '__main__':
    main()
