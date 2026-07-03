#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股日频量价+资金异常交易扫描器
基于Tushare Pro公开数据，支持两种模式：
  模式A - 全市场单日扫描：scan_all(trade_date)
  模式B - 标的列表时间窗口定向扫描：scan_watchlist(csv_path)

检测维度：
  D1  - 成交量突增：vol / MA(vol,20) > 3倍
  D2  - 换手率异常：turnover_rate > 板块均值 * 3倍
  D3  - 日内振幅异常：(high-low)/pre_close > 15%
  D4  - 连续涨跌停：连续3日触及涨跌停
  D5  - 量价背离：价涨量缩 / 价跌量增
  D6  - 融资异动：融资余额突增 / 融资买入占比异常
  D7  - 小盘股突放量：市值<50亿 + 换手率从低位突增至>15%
  --- 建仓期检测 ---
  D8  - 建仓前地量：vol < MA60的50%，换手率<2%，振幅<5%
  D9  - 温和放量启动：vol/MA20在1.2-2.5倍，涨跌幅<3%
  D10 - 价稳量增：连续3天涨跌幅<3%但vol>=MA20
  D11 - 换手率爬升：换手率从<3%逐步攀升，正斜率>0.15
  D12 - 量能堆积：10天内>=5天vol>MA20
  D13 - 窄幅吸筹：连续5天振幅<5%但vol>=MA20
  D14 - 建仓前后量比：建仓期均量/建仓前均量 > 2倍

用法：
  # 模式A - 全市场扫描
  python abnormal_trading_scanner.py                              # 最近交易日
  python abnormal_trading_scanner.py 20240603                     # 指定日期
  python abnormal_trading_scanner.py 20240601 20240607            # 日期范围
  python abnormal_trading_scanner.py --checks D1,D2,D6            # 指定检测项

  # 模式B - 标的列表定向扫描
  python abnormal_trading_scanner.py --watchlist watchlist.csv    # 从CSV读取标的
  python abnormal_trading_scanner.py --watchlist watchlist.csv --checks D1,D3,D6

标的列表CSV格式（模式B）：
  ts_code,start_date,end_date
  000001.SZ,20240101,20240331
  600519.SH,20240201,20240430
  ...

可选列：name, case_id, violation_type（用于输出标注）
"""

import os
import sys
import time
import argparse
import warnings
from datetime import datetime, timedelta
from collections import defaultdict

import pandas as pd
import numpy as np
import tushare as ts

warnings.filterwarnings('ignore')

# ============================================================
# 配置
# ============================================================

THRESHOLDS = {
    # ---- 原有异常检测 ----
    'D1_vol_ratio': 3.0,           # 成交量/20日均量 超过3倍
    'D2_turnover_ratio': 3.0,      # 换手率/板块均值 超过3倍
    'D2_min_turnover': 5.0,        # 换手率绝对值不低于5%
    'D3_amplitude': 15.0,          # 日内振幅超过15%
    'D4_consecutive_days': 3,      # 连续涨跌停天数
    'D5_vol_decline_ratio': 0.7,   # 量价背离：成交量低于20日均量70%
    'D5_price_change': 5.0,        # 量价背离：涨跌幅绝对值超过5%
    'D6_margin_balance_chg': 20.0, # 融资余额日环比超过20%
    'D6_margin_buy_ratio': 30.0,   # 融资买入/成交额超过30%
    'D7_market_cap': 50.0,         # 小盘股市值上限(亿)
    'D7_turnover_jump': 5.0,       # 换手率从前5日均值跳升倍数
    'D7_min_turnover': 15.0,       # 当日换手率不低于15%
    # ---- 建仓期检测 ----
    # D8 建仓前地量：vol < MA60的50%，换手率<2%，振幅<5%，且5日内至少3日满足
    'D8_vol_ratio_low': 0.5,       # vol/MA60 < 0.5
    'D8_max_turnover': 2.0,        # 换手率<2%
    'D8_max_amplitude': 5.0,       # 振幅<5%
    'D8_drought_window': 5,        # 持续性校验：回溯5日
    'D8_min_drought_days': 3,      # 至少3日满足地量条件
    # D9 温和放量启动：vol/MA20在1.2-2.5倍，涨跌幅<3%，且前一日缩量(vol/MA20<1.0)
    'D9_vol_ratio_min': 1.2,       # vol/MA20 >= 1.2倍
    'D9_vol_ratio_max': 2.5,       # vol/MA20 <= 2.5倍（排除突增）
    'D9_max_pct_chg': 3.0,         # 涨跌幅绝对值<3%
    'D9_quiet_vol_ratio': 1.0,     # 前一日vol/MA20 < 1.0（确保从缩量转折）
    # D10 价稳量增：连续3天涨跌幅<3%但vol>=MA20，且3日均量比>=1.2
    'D10_consecutive_days': 3,     # 连续N天
    'D10_max_pct_chg': 3.0,        # 涨跌幅绝对值<3%
    'D10_vol_ratio_min': 1.0,      # vol >= MA20
    'D10_avg_vol_ratio_min': 1.2,  # 3日均量比 >= 1.2（强度约束）
    # D11 换手率爬升：换手率从<3%逐步攀升，正斜率>0.15，R²>0.5
    'D11_turnover_start': 3.0,     # 起始换手率<3%
    'D11_turnover_slope': 0.15,    # 日均增长>0.15个百分点
    'D11_lookback_days': 10,       # 回溯天数
    'D11_r_squared_min': 0.5,      # R² > 0.5（趋势拟合度约束）
    # D12 量能堆积：10天内>=5天vol>MA20，且10日均量比>=1.2
    'D12_window_days': 10,         # 观察窗口10天
    'D12_min_days_above': 5,       # 至少5天vol>MA20
    'D12_vol_ratio_min': 1.0,      # vol >= MA20
    'D12_avg_vol_ratio_min': 1.2,  # 10日均量比 >= 1.2（强度约束）
    # D13 窄幅吸筹：连续5天振幅<5%但vol>=MA20，且5日区间涨跌<3%
    'D13_consecutive_days': 5,     # 连续N天
    'D13_max_amplitude': 5.0,      # 振幅<5%
    'D13_vol_ratio_min': 1.0,      # vol >= MA20
    'D13_price_range_max': 3.0,    # 5日区间涨跌幅 < 3%（价格约束）
    # D14 建仓前后量比：建仓期均量/建仓前均量 > 2倍，且近20天>=8天放量
    'D14_pre_window': 20,          # 建仓前窗口20天
    'D14_post_window': 20,         # 建仓期窗口20天
    'D14_vol_ratio': 2.0,          # 建仓期均量/建仓前均量 > 2倍
    'D14_min_post_days_above': 8,  # 近20天至少8天vol>=MA20（持续性约束）
}

INDUSTRY_MAP = {
    '银行': '金融', '保险': '金融', '证券': '金融', '多元金融': '金融',
    '房地产': '地产', '建筑': '基建', '建材': '基建',
    '医药': '医药', '医疗保健': '医药', '生物制药': '医药',
    '半导体': '科技', '元器件': '科技', '通信设备': '科技',
    '软件服务': '科技', 'IT设备': '科技', '互联网': '科技',
    '电气设备': '新能源', '汽车类': '汽车', '化工': '周期',
    '钢铁': '周期', '有色': '周期', '煤炭': '周期', '石油': '周期',
    '农林牧渔': '农业', '食品饮料': '消费', '酿酒': '消费',
    '家用电器': '消费', '纺织服饰': '消费', '商业连锁': '消费',
    '传媒娱乐': '传媒', '文教休闲': '传媒', '广告包装': '传媒',
    '运输服务': '交运', '交通设施': '交运', '仓储物流': '交运',
    '航空': '军工', '船舶': '军工', '军工航空': '军工',
    '电力': '公用', '供气供热': '公用', '水务': '公用', '环境保护': '公用',
}

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
STOCK_BASIC_CACHE = os.path.join(CACHE_DIR, 'stock_basic_cache.csv')
LOOKBACK_DAYS = 70  # MA60计算所需的回溯天数（原30，建仓期检测需要60日均线）


# ============================================================
# 工具函数
# ============================================================

def get_token():
    token = os.environ.get('TUSHARE_TOKEN')
    if not token:
        raise ValueError("请设置环境变量 TUSHARE_TOKEN")
    return token


def ensure_cache_dir():
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)


def fmt_date(d):
    if isinstance(d, pd.Timestamp):
        return d.strftime('%Y%m%d')
    return str(d).replace('-', '').strip()


def safe_float(val, default=np.nan):
    if val is None or pd.isna(val):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def add_days(date_str, n):
    """日期字符串加减天数"""
    return (datetime.strptime(str(date_str), '%Y%m%d') + timedelta(days=n)).strftime('%Y%m%d')


# ============================================================
# 核心扫描器
# ============================================================

class AbnormalTradingScanner:
    """A股异常交易扫描器"""

    def __init__(self, thresholds=None):
        self.thresholds = thresholds or THRESHOLDS
        self.pro = None
        self.stock_basic = None
        self._init_api()

    def _init_api(self):
        token = get_token()
        ts.set_token(token)
        self.pro = ts.pro_api()

    # ================================================================
    # 基础信息
    # ================================================================

    def get_stock_basic(self, force_refresh=False):
        """获取全A股基础信息（含缓存）"""
        ensure_cache_dir()
        if not force_refresh and os.path.exists(STOCK_BASIC_CACHE):
            df = pd.read_csv(STOCK_BASIC_CACHE, dtype={'ts_code': str})
            if not df.empty:
                self.stock_basic = df
                return df

        print("[数据] 获取全A股基础信息...")
        df = self.pro.stock_basic(
            exchange='', list_status='L',
            fields='ts_code,name,industry,market,list_date'
        )
        if df is not None and len(df) > 0:
            df.to_csv(STOCK_BASIC_CACHE, index=False, encoding='utf-8-sig')
            self.stock_basic = df
            print(f"  -> 共 {len(df)} 只股票")
        return df

    def get_stock_name(self, ts_code):
        """获取单只股票名称"""
        if self.stock_basic is not None:
            match = self.stock_basic[self.stock_basic['ts_code'] == ts_code]
            if not match.empty:
                return match.iloc[0]['name']
        return ''

    def get_stock_industry(self, ts_code):
        """获取单只股票行业"""
        if self.stock_basic is not None:
            match = self.stock_basic[self.stock_basic['ts_code'] == ts_code]
            if not match.empty:
                return match.iloc[0].get('industry', '')
        return ''

    # ================================================================
    # 模式B：按标的定向拉取数据
    # ================================================================

    def _fetch_daily_for_stock(self, ts_code, start_date, end_date):
        """获取单只股票的日线数据（含回溯窗口）"""
        fetch_start = add_days(start_date, -LOOKBACK_DAYS)
        try:
            df = self.pro.daily(
                ts_code=ts_code,
                start_date=fetch_start,
                end_date=end_date,
                fields='ts_code,trade_date,open,high,low,close,pre_close,'
                       'pct_chg,vol,amount'
            )
            if df is not None and len(df) > 0:
                df['trade_date'] = df['trade_date'].astype(str)
                df = df.sort_values('trade_date')
                return df
        except Exception as e:
            print(f"  [警告] 获取 {ts_code} 日线失败: {e}")
        return pd.DataFrame()

    def _fetch_daily_basic_for_stock(self, ts_code, start_date, end_date):
        """获取单只股票的日线指标（换手率、市值等）"""
        try:
            df = self.pro.daily_basic(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields='ts_code,trade_date,turnover_rate,turnover_rate_f,'
                       'volume_ratio,pe,pb,total_mv,circ_mv'
            )
            if df is not None and len(df) > 0:
                df['trade_date'] = df['trade_date'].astype(str)
                df = df.sort_values('trade_date')
                return df
        except Exception as e:
            print(f"  [警告] 获取 {ts_code} daily_basic 失败: {e}")
        return pd.DataFrame()

    def _fetch_margin_for_stock(self, ts_code, start_date, end_date):
        """获取单只股票的融资融券数据"""
        try:
            df = self.pro.margin(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date
            )
            if df is not None and len(df) > 0:
                df = df[df['exchange_id'] != 'BSE']
                df['trade_date'] = df['trade_date'].astype(str)
                df = df.sort_values('trade_date')
                return df
        except Exception as e:
            pass  # 部分股票无融资数据，静默跳过
        return pd.DataFrame()

    def _fetch_limit_list_range(self, start_date, end_date):
        """获取时间范围内的涨跌停数据"""
        try:
            df = self.pro.limit_list(
                start_date=start_date,
                end_date=end_date,
                limit=10000
            )
            if df is not None and len(df) > 0:
                df['trade_date'] = df['trade_date'].astype(str)
                return df
        except Exception as e:
            print(f"  [警告] 获取涨跌停数据失败: {e}")
        return pd.DataFrame()

    # ================================================================
    # 模式A：全市场批量拉取（保留原有逻辑）
    # ================================================================

    def get_daily_batch(self, trade_date, lookback_days=30):
        """批量获取全市场日线数据"""
        start_date = add_days(trade_date, -(lookback_days + 5))
        print(f"[数据] 获取日线数据 {start_date} ~ {trade_date}...")

        all_data = []
        offset = 0
        while True:
            try:
                df = self.pro.daily(
                    trade_date='',
                    start_date=start_date,
                    end_date=trade_date,
                    limit=4000,
                    offset=offset
                )
                if df is None or len(df) == 0:
                    break
                all_data.append(df)
                offset += 4000
                if len(df) < 4000:
                    break
                time.sleep(0.3)
            except Exception as e:
                print(f"  获取日线分页出错: {e}")
                break

        if not all_data:
            return pd.DataFrame()

        result = pd.concat(all_data, ignore_index=True)
        result['trade_date'] = result['trade_date'].astype(str)
        result = result.drop_duplicates(subset=['ts_code', 'trade_date'], keep='first')
        print(f"  -> 共 {result['ts_code'].nunique()} 只股票, {len(result)} 条记录")
        return result

    def get_daily_basic_batch(self, trade_date):
        """批量获取当日指标数据"""
        print(f"[数据] 获取日线指标 {trade_date}...")
        all_data = []
        offset = 0
        while True:
            try:
                df = self.pro.daily_basic(
                    trade_date=trade_date,
                    fields='ts_code,trade_date,turnover_rate,turnover_rate_f,'
                           'volume_ratio,pe,pb,total_mv,circ_mv',
                    limit=4000,
                    offset=offset
                )
                if df is None or len(df) == 0:
                    break
                all_data.append(df)
                offset += 4000
                if len(df) < 4000:
                    break
                time.sleep(0.3)
            except Exception as e:
                print(f"  获取日线指标分页出错: {e}")
                break

        if not all_data:
            return pd.DataFrame()

        result = pd.concat(all_data, ignore_index=True)
        result['trade_date'] = result['trade_date'].astype(str)
        print(f"  -> 共 {len(result)} 条记录")
        return result

    def get_margin_data(self, trade_date):
        """获取融资融券数据"""
        print(f"[数据] 获取融资融券数据 {trade_date}...")
        try:
            df = self.pro.margin(trade_date=trade_date)
            if df is None or len(df) == 0:
                return pd.DataFrame()
            df = df[df['exchange_id'] != 'BSE']
            df['trade_date'] = df['trade_date'].astype(str)
            print(f"  -> 共 {len(df)} 条记录")
            return df
        except Exception as e:
            print(f"  获取融资融券数据出错: {e}")
            return pd.DataFrame()

    def get_limit_list(self, trade_date, lookback_days=10):
        """获取涨跌停数据"""
        start_date = add_days(trade_date, -(lookback_days + 5))
        print(f"[数据] 获取涨跌停数据 {start_date} ~ {trade_date}...")
        try:
            df = self.pro.limit_list(
                start_date=start_date,
                end_date=trade_date,
                limit=10000
            )
            if df is None or len(df) == 0:
                return pd.DataFrame()
            df['trade_date'] = df['trade_date'].astype(str)
            print(f"  -> 共 {len(df)} 条记录")
            return df
        except Exception as e:
            print(f"  获取涨跌停数据出错: {e}")
            return pd.DataFrame()

    # ================================================================
    # 辅助计算
    # ================================================================

    def _calc_ma(self, df, ts_code, trade_date, window=20):
        """计算某只股票在指定日期前的N日均量"""
        stock_data = df[(df['ts_code'] == ts_code) & (df['trade_date'] <= trade_date)]
        stock_data = stock_data.sort_values('trade_date', ascending=False)
        if len(stock_data) < window:
            return np.nan
        return stock_data.head(window)['vol'].mean()

    def _calc_ma60(self, df, ts_code, trade_date):
        """计算某只股票在指定日期前的60日均量"""
        return self._calc_ma(df, ts_code, trade_date, window=60)

    def _calc_turnover_ma(self, df, ts_code, trade_date, window=5):
        """计算某只股票在指定日期前的N日均换手率"""
        stock_data = df[(df['ts_code'] == ts_code) & (df['trade_date'] <= trade_date)]
        stock_data = stock_data.sort_values('trade_date', ascending=False)
        if len(stock_data) < window:
            return np.nan
        return stock_data.head(window)['turnover_rate'].mean()

    def _get_sector_avg_turnover(self, daily_basic_df, trade_date):
        """计算当日各板块平均换手率"""
        if daily_basic_df.empty or self.stock_basic is None:
            return {}
        day_data = daily_basic_df[daily_basic_df['trade_date'] == trade_date].copy()
        if day_data.empty:
            return {}
        day_data = day_data.merge(
            self.stock_basic[['ts_code', 'industry']],
            on='ts_code', how='left'
        )
        day_data['sector'] = day_data['industry'].map(INDUSTRY_MAP).fillna('其他')
        day_data['turnover_rate'] = day_data['turnover_rate'].apply(safe_float)
        return day_data.groupby('sector')['turnover_rate'].mean().to_dict()

    # ================================================================
    # 检测逻辑（单日、单标的）
    # ================================================================

    def _check_D1_single(self, daily_df, ts_code, trade_date):
        """D1: 成交量突增 - 单日单标的"""
        row = daily_df[(daily_df['ts_code'] == ts_code) & (daily_df['trade_date'] == trade_date)]
        if row.empty:
            return None
        row = row.iloc[0]
        vol = safe_float(row['vol'])
        if pd.isna(vol) or vol == 0:
            return None
        ma20 = self._calc_ma(daily_df, ts_code, trade_date, 20)
        if pd.isna(ma20) or ma20 == 0:
            return None
        ratio = vol / ma20
        if ratio >= self.thresholds['D1_vol_ratio']:
            return {'vol_ratio': round(ratio, 2), 'vol': vol, 'ma20_vol': ma20}
        return None

    def _check_D2_single(self, daily_basic_df, ts_code, trade_date):
        """D2: 换手率异常 - 单日单标的"""
        row = daily_basic_df[(daily_basic_df['ts_code'] == ts_code) & (daily_basic_df['trade_date'] == trade_date)]
        if row.empty:
            return None
        row = row.iloc[0]
        turnover = safe_float(row.get('turnover_rate'))
        if pd.isna(turnover):
            return None

        sector_avg_map = self._get_sector_avg_turnover(daily_basic_df, trade_date)
        industry = self.get_stock_industry(ts_code)
        sector = INDUSTRY_MAP.get(industry, '其他')
        avg = sector_avg_map.get(sector, turnover)
        if avg is None or avg == 0:
            return None
        ratio = turnover / avg
        if ratio >= self.thresholds['D2_turnover_ratio'] and turnover >= self.thresholds['D2_min_turnover']:
            return {'turnover_rate': round(turnover, 2), 'sector_avg': round(avg, 2), 'turnover_ratio': round(ratio, 2)}
        return None

    def _check_D3_single(self, daily_df, ts_code, trade_date):
        """D3: 日内振幅异常 - 单日单标的"""
        row = daily_df[(daily_df['ts_code'] == ts_code) & (daily_df['trade_date'] == trade_date)]
        if row.empty:
            return None
        row = row.iloc[0]
        high = safe_float(row.get('high'))
        low = safe_float(row.get('low'))
        pre_close = safe_float(row.get('pre_close'))
        if pd.isna(high) or pd.isna(low) or pd.isna(pre_close) or pre_close == 0:
            return None
        amplitude = (high - low) / pre_close * 100
        if amplitude >= self.thresholds['D3_amplitude']:
            return {'amplitude': round(amplitude, 2), 'high': high, 'low': low}
        return None

    def _check_D4_single(self, limit_df, ts_code, trade_date):
        """D4: 连续涨跌停 - 单日单标的"""
        if limit_df.empty:
            return None
        n_days = self.thresholds['D4_consecutive_days']
        stock_limits = limit_df[limit_df['ts_code'] == ts_code].copy()
        if stock_limits.empty:
            return None
        dates = sorted(stock_limits['trade_date'].unique())
        if len(dates) < n_days or trade_date not in dates:
            return None

        idx = dates.index(trade_date)
        consecutive = 1
        for i in range(idx - 1, -1, -1):
            expected = add_days(dates[i + 1], -1)
            if dates[i] == expected:
                consecutive += 1
            else:
                break

        if consecutive >= n_days:
            day_data = stock_limits[stock_limits['trade_date'] == trade_date]
            limit_type = 'up' if (day_data['limit'] == 'U').any() else 'down'
            return {'consecutive_days': consecutive, 'limit_type': limit_type}
        return None

    def _check_D5_single(self, daily_df, ts_code, trade_date):
        """D5: 量价背离 - 单日单标的"""
        row = daily_df[(daily_df['ts_code'] == ts_code) & (daily_df['trade_date'] == trade_date)]
        if row.empty:
            return None
        row = row.iloc[0]
        pct_chg = safe_float(row.get('pct_chg'), 0)
        vol = safe_float(row.get('vol'))
        if pd.isna(vol) or vol == 0:
            return None
        ma20 = self._calc_ma(daily_df, ts_code, trade_date, 20)
        if pd.isna(ma20) or ma20 == 0:
            return None
        vol_ratio = vol / ma20

        if pct_chg >= self.thresholds['D5_price_change'] and vol_ratio < self.thresholds['D5_vol_decline_ratio']:
            return {'divergence_type': '价涨量缩', 'pct_chg': round(pct_chg, 2), 'vol_ratio': round(vol_ratio, 2)}
        elif pct_chg <= -self.thresholds['D5_price_change'] and vol_ratio > 1.5:
            return {'divergence_type': '价跌量增', 'pct_chg': round(pct_chg, 2), 'vol_ratio': round(vol_ratio, 2)}
        return None

    def _check_D6_single(self, margin_df, daily_df, ts_code, trade_date):
        """D6: 融资异动 - 单日单标的"""
        if margin_df.empty:
            return None
        stock_margin = margin_df[margin_df['ts_code'] == ts_code].sort_values('trade_date')
        if stock_margin.empty:
            return None

        details = {}
        margin_type = None

        # 融资余额突增
        if 'rzye' in stock_margin.columns:
            latest = stock_margin[stock_margin['trade_date'] == trade_date]
            if not latest.empty:
                prev = stock_margin[stock_margin['trade_date'] < trade_date]
                if not prev.empty:
                    prev_balance = safe_float(prev.iloc[-1].get('rzye'))
                    curr_balance = safe_float(latest.iloc[0].get('rzye'))
                    if not pd.isna(prev_balance) and not pd.isna(curr_balance) and prev_balance > 0:
                        chg_pct = (curr_balance - prev_balance) / prev_balance * 100
                        if chg_pct >= self.thresholds['D6_margin_balance_chg']:
                            margin_type = '融资余额突增'
                            details['margin_balance_chg'] = round(chg_pct, 2)

        # 融资买入占比异常
        if 'rzmre' in stock_margin.columns and not daily_df.empty:
            latest = stock_margin[stock_margin['trade_date'] == trade_date]
            if not latest.empty:
                rzmre = safe_float(latest.iloc[0].get('rzmre'))
                if not pd.isna(rzmre) and rzmre > 0:
                    day_row = daily_df[(daily_df['ts_code'] == ts_code) & (daily_df['trade_date'] == trade_date)]
                    if not day_row.empty:
                        amount = safe_float(day_row.iloc[0].get('amount'))
                        if not pd.isna(amount) and amount > 0:
                            ratio = rzmre / amount * 100
                            if ratio >= self.thresholds['D6_margin_buy_ratio']:
                                if margin_type:
                                    margin_type = '融资余额+买入占比双异常'
                                else:
                                    margin_type = '融资买入占比异常'
                                details['margin_buy_ratio'] = round(ratio, 2)

        if margin_type:
            return {'margin_type': margin_type, **details}
        return None

    def _check_D7_single(self, daily_basic_df, ts_code, trade_date):
        """D7: 小盘股突放量 - 单日单标的"""
        if daily_basic_df.empty:
            return None
        row = daily_basic_df[(daily_basic_df['ts_code'] == ts_code) & (daily_basic_df['trade_date'] == trade_date)]
        if row.empty:
            return None
        row = row.iloc[0]
        total_mv = safe_float(row.get('total_mv'))
        turnover = safe_float(row.get('turnover_rate'))
        if pd.isna(total_mv) or pd.isna(turnover):
            return None

        if total_mv >= self.thresholds['D7_market_cap']:
            return None
        if turnover < self.thresholds['D7_min_turnover']:
            return None

        ma5_turnover = self._calc_turnover_ma(daily_basic_df, ts_code, trade_date, 5)
        if pd.isna(ma5_turnover) or ma5_turnover == 0:
            return None

        jump_ratio = turnover / ma5_turnover
        if jump_ratio >= self.thresholds['D7_turnover_jump']:
            return {
                'turnover_rate': round(turnover, 2),
                'ma5_turnover': round(ma5_turnover, 2),
                'turnover_jump': round(jump_ratio, 2),
                'total_mv': round(total_mv, 2),
            }
        return None

    # ================================================================
    # 建仓期检测（D8-D14）
    # ================================================================

    def _check_D8_single(self, daily_df, daily_basic_df, ts_code, trade_date):
        """D8: 建仓前地量 - 成交量极度萎缩、换手率极低、振幅窄，且5日内至少3日满足"""
        stock_data = daily_df[(daily_df['ts_code'] == ts_code) & (daily_df['trade_date'] <= trade_date)]
        stock_data = stock_data.sort_values('trade_date', ascending=False)
        window = self.thresholds['D8_drought_window']
        min_days = self.thresholds['D8_min_drought_days']
        if len(stock_data) < window:
            return None

        recent = stock_data.head(window)
        if recent.iloc[0]['trade_date'] != trade_date:
            return None

        # 逐日检查地量条件
        drought_count = 0
        best_vol_ratio = 999
        best_vol = None
        best_ma60 = None
        best_amplitude = None
        best_turnover = None

        for _, r in recent.iterrows():
            td = r['trade_date']
            vol = safe_float(r['vol'])
            if pd.isna(vol) or vol == 0:
                continue
            ma60 = self._calc_ma60(daily_df, ts_code, td)
            if pd.isna(ma60) or ma60 == 0:
                continue
            vol_ratio = vol / ma60
            if vol_ratio > self.thresholds['D8_vol_ratio_low']:
                continue

            high = safe_float(r.get('high'))
            low = safe_float(r.get('low'))
            pre_close = safe_float(r.get('pre_close'))
            if pd.isna(high) or pd.isna(low) or pd.isna(pre_close) or pre_close == 0:
                continue
            amplitude = (high - low) / pre_close * 100
            if amplitude > self.thresholds['D8_max_amplitude']:
                continue

            # 换手率检查（可选，有数据才校验）
            turnover = None
            if not daily_basic_df.empty:
                br = daily_basic_df[(daily_basic_df['ts_code'] == ts_code) & (daily_basic_df['trade_date'] == td)]
                if not br.empty:
                    turnover = safe_float(br.iloc[0].get('turnover_rate'))
                    if not pd.isna(turnover) and turnover > self.thresholds['D8_max_turnover']:
                        continue

            drought_count += 1
            if vol_ratio < best_vol_ratio:
                best_vol_ratio = vol_ratio
                best_vol = vol
                best_ma60 = ma60
                best_amplitude = amplitude
                best_turnover = turnover

        if drought_count < min_days:
            return None

        return {
            'vol_ratio_ma60': round(best_vol_ratio, 2),
            'vol': best_vol,
            'ma60_vol': round(best_ma60, 0),
            'amplitude': round(best_amplitude, 2),
            'turnover_rate': round(best_turnover, 2) if best_turnover is not None and not pd.isna(best_turnover) else None,
            'drought_days': drought_count,
        }

    def _check_D9_single(self, daily_df, ts_code, trade_date):
        """D9: 温和放量启动 - 成交量温和放大（非突增），价格稳定，且前一日缩量"""
        row = daily_df[(daily_df['ts_code'] == ts_code) & (daily_df['trade_date'] == trade_date)]
        if row.empty:
            return None
        row = row.iloc[0]
        vol = safe_float(row['vol'])
        pct_chg = safe_float(row.get('pct_chg'), 0)
        if pd.isna(vol) or vol == 0:
            return None

        ma20 = self._calc_ma(daily_df, ts_code, trade_date, 20)
        if pd.isna(ma20) or ma20 == 0:
            return None

        vol_ratio = vol / ma20
        if vol_ratio < self.thresholds['D9_vol_ratio_min'] or vol_ratio > self.thresholds['D9_vol_ratio_max']:
            return None

        if abs(pct_chg) > self.thresholds['D9_max_pct_chg']:
            return None

        # 前一日缩量校验：确保是从缩量到放量的转折点
        stock_data = daily_df[(daily_df['ts_code'] == ts_code) & (daily_df['trade_date'] < trade_date)]
        stock_data = stock_data.sort_values('trade_date', ascending=False)
        if len(stock_data) > 0:
            prev_row = stock_data.iloc[0]
            prev_vol = safe_float(prev_row['vol'])
            prev_td = prev_row['trade_date']
            if not pd.isna(prev_vol) and prev_vol > 0:
                prev_ma20 = self._calc_ma(daily_df, ts_code, prev_td, 20)
                if not pd.isna(prev_ma20) and prev_ma20 > 0:
                    prev_vol_ratio = prev_vol / prev_ma20
                    if prev_vol_ratio >= self.thresholds['D9_quiet_vol_ratio']:
                        return None  # 前一日未缩量，不是启动点

        return {
            'vol_ratio': round(vol_ratio, 2),
            'pct_chg': round(pct_chg, 2),
            'vol': vol,
            'ma20_vol': round(ma20, 0),
        }

    def _check_D10_single(self, daily_df, ts_code, trade_date):
        """D10: 价稳量增 - 连续N天价格小幅波动但成交量持续高于均量，且均量比>=1.2"""
        n_days = self.thresholds['D10_consecutive_days']
        stock_data = daily_df[(daily_df['ts_code'] == ts_code) & (daily_df['trade_date'] <= trade_date)]
        stock_data = stock_data.sort_values('trade_date', ascending=False)
        if len(stock_data) < n_days:
            return None

        recent = stock_data.head(n_days)
        if recent.iloc[0]['trade_date'] != trade_date:
            return None

        consecutive_count = 0
        vol_ratios = []
        for _, r in recent.iterrows():
            pct = safe_float(r.get('pct_chg'), 0)
            v = safe_float(r['vol'])
            td = r['trade_date']
            if pd.isna(v) or v == 0:
                break
            ma20 = self._calc_ma(daily_df, ts_code, td, 20)
            if pd.isna(ma20) or ma20 == 0:
                break
            if abs(pct) <= self.thresholds['D10_max_pct_chg'] and v / ma20 >= self.thresholds['D10_vol_ratio_min']:
                consecutive_count += 1
                vol_ratios.append(v / ma20)
            else:
                break

        if consecutive_count < n_days:
            return None

        avg_vol_ratio = np.mean(vol_ratios)
        if avg_vol_ratio < self.thresholds['D10_avg_vol_ratio_min']:
            return None

        return {
            'consecutive_days': consecutive_count,
            'avg_vol_ratio': round(avg_vol_ratio, 2),
            'max_pct_chg_abs': round(max(abs(safe_float(r.get('pct_chg'), 0)) for _, r in recent.iterrows()), 2),
        }

    def _check_D11_single(self, daily_basic_df, ts_code, trade_date):
        """D11: 换手率爬升 - 换手率从低位逐步攀升，呈正斜率趋势，且R²>0.5"""
        if daily_basic_df.empty:
            return None
        lookback = self.thresholds['D11_lookback_days']
        stock_data = daily_basic_df[(daily_basic_df['ts_code'] == ts_code) & (daily_basic_df['trade_date'] <= trade_date)]
        stock_data = stock_data.sort_values('trade_date', ascending=False)
        if len(stock_data) < lookback:
            return None

        recent = stock_data.head(lookback).sort_values('trade_date')
        turnovers = []
        for _, r in recent.iterrows():
            t = safe_float(r.get('turnover_rate'))
            if pd.isna(t):
                continue
            turnovers.append(t)

        if len(turnovers) < lookback // 2:
            return None

        if turnovers[0] > self.thresholds['D11_turnover_start']:
            return None

        x = np.arange(len(turnovers))
        y = np.array(turnovers)
        coeffs = np.polyfit(x, y, 1)
        slope = coeffs[0]

        if slope <= self.thresholds['D11_turnover_slope']:
            return None

        # R² 趋势拟合度校验
        y_pred = np.polyval(coeffs, x)
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        if r_squared < self.thresholds['D11_r_squared_min']:
            return None

        return {
            'turnover_start': round(turnovers[0], 2),
            'turnover_end': round(turnovers[-1], 2),
            'slope': round(slope, 3),
            'r_squared': round(r_squared, 3),
            'lookback_days': len(turnovers),
        }

    def _check_D12_single(self, daily_df, ts_code, trade_date):
        """D12: 量能堆积 - 近N天内超过半数交易日成交量高于20日均量，且均量比>=1.2"""
        window = self.thresholds['D12_window_days']
        min_days = self.thresholds['D12_min_days_above']
        stock_data = daily_df[(daily_df['ts_code'] == ts_code) & (daily_df['trade_date'] <= trade_date)]
        stock_data = stock_data.sort_values('trade_date', ascending=False)
        if len(stock_data) < window:
            return None

        recent = stock_data.head(window)
        if recent.iloc[0]['trade_date'] != trade_date:
            return None

        above_count = 0
        vol_ratios = []
        for _, r in recent.iterrows():
            v = safe_float(r['vol'])
            td = r['trade_date']
            if pd.isna(v) or v == 0:
                continue
            ma20 = self._calc_ma(daily_df, ts_code, td, 20)
            if pd.isna(ma20) or ma20 == 0:
                continue
            ratio = v / ma20
            vol_ratios.append(ratio)
            if ratio >= self.thresholds['D12_vol_ratio_min']:
                above_count += 1

        if above_count < min_days:
            return None

        avg_ratio = np.mean(vol_ratios) if vol_ratios else 0
        if avg_ratio < self.thresholds['D12_avg_vol_ratio_min']:
            return None

        return {
            'window_days': window,
            'days_above_ma20': above_count,
            'avg_vol_ratio': round(avg_ratio, 2),
            'max_vol_ratio': round(max(vol_ratios), 2) if vol_ratios else 0,
        }

    def _check_D13_single(self, daily_df, ts_code, trade_date):
        """D13: 窄幅吸筹 - 连续N天振幅极窄但成交量高于均量，且5日区间涨跌<3%"""
        n_days = self.thresholds['D13_consecutive_days']
        stock_data = daily_df[(daily_df['ts_code'] == ts_code) & (daily_df['trade_date'] <= trade_date)]
        stock_data = stock_data.sort_values('trade_date', ascending=False)
        if len(stock_data) < n_days:
            return None

        recent = stock_data.head(n_days)
        if recent.iloc[0]['trade_date'] != trade_date:
            return None

        consecutive_count = 0
        amplitudes = []
        closes = []
        for _, r in recent.iterrows():
            high = safe_float(r.get('high'))
            low = safe_float(r.get('low'))
            pre_close = safe_float(r.get('pre_close'))
            close = safe_float(r.get('close'))
            v = safe_float(r['vol'])
            td = r['trade_date']
            if pd.isna(high) or pd.isna(low) or pd.isna(pre_close) or pre_close == 0 or pd.isna(v) or v == 0:
                break
            amp = (high - low) / pre_close * 100
            ma20 = self._calc_ma(daily_df, ts_code, td, 20)
            if pd.isna(ma20) or ma20 == 0:
                break
            if amp <= self.thresholds['D13_max_amplitude'] and v / ma20 >= self.thresholds['D13_vol_ratio_min']:
                consecutive_count += 1
                amplitudes.append(amp)
                closes.append(close)
            else:
                break

        if consecutive_count < n_days:
            return None

        # 5日区间价格约束：确保价格真正被压制在窄幅区间内
        if len(closes) >= 2 and closes[-1] > 0:
            price_range = abs(closes[0] - closes[-1]) / closes[-1] * 100
            if price_range > self.thresholds['D13_price_range_max']:
                return None

        return {
            'consecutive_days': consecutive_count,
            'avg_amplitude': round(np.mean(amplitudes), 2),
            'max_amplitude': round(max(amplitudes), 2),
        }

    def _check_D14_single(self, daily_df, ts_code, trade_date):
        """D14: 建仓前后量比 - 近期均量/远期均量 > 阈值，且近20天>=8天放量"""
        pre_window = self.thresholds['D14_pre_window']
        post_window = self.thresholds['D14_post_window']
        stock_data = daily_df[(daily_df['ts_code'] == ts_code) & (daily_df['trade_date'] <= trade_date)]
        stock_data = stock_data.sort_values('trade_date', ascending=False)
        total_needed = pre_window + post_window
        if len(stock_data) < total_needed:
            return None

        post_data = stock_data.head(post_window)
        pre_data = stock_data.iloc[post_window:post_window + pre_window]

        if len(post_data) < post_window // 2 or len(pre_data) < pre_window // 2:
            return None

        post_avg_vol = post_data['vol'].apply(safe_float).mean()
        pre_avg_vol = pre_data['vol'].apply(safe_float).mean()

        if pd.isna(post_avg_vol) or pd.isna(pre_avg_vol) or pre_avg_vol == 0:
            return None

        ratio = post_avg_vol / pre_avg_vol
        if ratio < self.thresholds['D14_vol_ratio']:
            return None

        # 放量持续性约束：近20天至少8天vol>=MA20
        post_days_above = 0
        for _, r in post_data.iterrows():
            v = safe_float(r['vol'])
            td = r['trade_date']
            if pd.isna(v) or v == 0:
                continue
            ma20 = self._calc_ma(daily_df, ts_code, td, 20)
            if pd.isna(ma20) or ma20 == 0:
                continue
            if v / ma20 >= 1.0:
                post_days_above += 1

        if post_days_above < self.thresholds['D14_min_post_days_above']:
            return None

        return {
            'pre_avg_vol': round(pre_avg_vol, 0),
            'post_avg_vol': round(post_avg_vol, 0),
            'vol_ratio': round(ratio, 2),
            'pre_window': pre_window,
            'post_window': post_window,
            'post_days_above': post_days_above,
        }

    # ================================================================
    # 模式B：标的列表时间窗口定向扫描
    # ================================================================

    def scan_watchlist(self, csv_path, checks=None):
        """
        从CSV读取标的列表，在各自时间窗口内逐日扫描。

        CSV格式（必选列）：
          ts_code, start_date, end_date

        CSV格式（可选列）：
          name, case_id, violation_type

        Parameters:
            csv_path: 标的列表CSV文件路径
            checks: 检测项列表，None则全部执行

        Returns:
            (summary_df, detail_df): 标的汇总表 + 逐日明细表
        """
        # ---- 读取标的列表 ----
        print(f"\n{'='*60}")
        print(f"  标的列表定向扫描")
        print(f"{'='*60}\n")

        print(f"[输入] 读取标的列表: {csv_path}")
        watchlist = pd.read_csv(csv_path, dtype={'ts_code': str})
        required_cols = ['ts_code', 'start_date', 'end_date']
        missing = [c for c in required_cols if c not in watchlist.columns]
        if missing:
            raise ValueError(f"CSV缺少必选列: {missing}")

        watchlist['start_date'] = watchlist['start_date'].astype(str).str.replace('-', '')
        watchlist['end_date'] = watchlist['end_date'].astype(str).str.replace('-', '')
        print(f"  -> 共 {len(watchlist)} 个标的\n")

        if checks is None:
            checks = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'D10', 'D11', 'D12', 'D13', 'D14']

        # 获取基础信息
        self.get_stock_basic()

        # 确定全局日期范围（用于批量拉取涨跌停数据）
        all_start = min(watchlist['start_date'])
        all_end = max(watchlist['end_date'])
        limit_fetch_start = add_days(all_start, -10)

        # 预拉取涨跌停数据（全范围一次拉取）
        limit_df = pd.DataFrame()
        if 'D4' in checks:
            print(f"[数据] 预拉取涨跌停数据 {limit_fetch_start} ~ {all_end}...")
            limit_df = self._fetch_limit_list_range(limit_fetch_start, all_end)
            print(f"  -> 共 {len(limit_df)} 条记录\n")

        # ---- 逐标的扫描 ----
        all_details = []  # 逐日明细

        for idx, row in watchlist.iterrows():
            ts_code = row['ts_code']
            start_date = row['start_date']
            end_date = row['end_date']
            name = row.get('name', '') or self.get_stock_name(ts_code)
            case_id = row.get('case_id', '')
            violation_type = row.get('violation_type', '')

            print(f"[{idx+1}/{len(watchlist)}] 扫描 {ts_code} {name}  ({start_date} ~ {end_date})")

            # 拉取该标的的日线数据（含回溯窗口）
            daily_df = pd.DataFrame()
            daily_basic_df = pd.DataFrame()
            margin_df = pd.DataFrame()

            need_daily = any(c in checks for c in ['D1', 'D3', 'D5', 'D6', 'D8', 'D9', 'D10', 'D12', 'D13', 'D14'])
            need_basic = any(c in checks for c in ['D2', 'D7', 'D8', 'D11'])
            need_margin = 'D6' in checks

            if need_daily:
                daily_df = self._fetch_daily_for_stock(ts_code, start_date, end_date)
                time.sleep(0.2)
            if need_basic:
                daily_basic_df = self._fetch_daily_basic_for_stock(ts_code, start_date, end_date)
                time.sleep(0.2)
            if need_margin:
                margin_df = self._fetch_margin_for_stock(ts_code, start_date, end_date)
                time.sleep(0.2)

            if daily_df.empty and daily_basic_df.empty:
                print(f"  -> 无数据，跳过")
                continue

            # 确定实际交易日列表
            if not daily_df.empty:
                trading_days = sorted(daily_df[
                    (daily_df['trade_date'] >= start_date) &
                    (daily_df['trade_date'] <= end_date)
                ]['trade_date'].unique())
            elif not daily_basic_df.empty:
                trading_days = sorted(daily_basic_df[
                    (daily_basic_df['trade_date'] >= start_date) &
                    (daily_basic_df['trade_date'] <= end_date)
                ]['trade_date'].unique())
            else:
                trading_days = []

            # 逐日检测
            stock_alerts_today = 0
            for trade_date in trading_days:
                day_alerts = []

                check_funcs = {
                    'D1': lambda: self._check_D1_single(daily_df, ts_code, trade_date),
                    'D2': lambda: self._check_D2_single(daily_basic_df, ts_code, trade_date),
                    'D3': lambda: self._check_D3_single(daily_df, ts_code, trade_date),
                    'D4': lambda: self._check_D4_single(limit_df, ts_code, trade_date),
                    'D5': lambda: self._check_D5_single(daily_df, ts_code, trade_date),
                    'D6': lambda: self._check_D6_single(margin_df, daily_df, ts_code, trade_date),
                    'D7': lambda: self._check_D7_single(daily_basic_df, ts_code, trade_date),
                    'D8': lambda: self._check_D8_single(daily_df, daily_basic_df, ts_code, trade_date),
                    'D9': lambda: self._check_D9_single(daily_df, ts_code, trade_date),
                    'D10': lambda: self._check_D10_single(daily_df, ts_code, trade_date),
                    'D11': lambda: self._check_D11_single(daily_basic_df, ts_code, trade_date),
                    'D12': lambda: self._check_D12_single(daily_df, ts_code, trade_date),
                    'D13': lambda: self._check_D13_single(daily_df, ts_code, trade_date),
                    'D14': lambda: self._check_D14_single(daily_df, ts_code, trade_date),
                }

                for check in checks:
                    if check in check_funcs:
                        try:
                            result = check_funcs[check]()
                            if result:
                                detail = {
                                    'ts_code': ts_code,
                                    'name': name,
                                    'case_id': case_id,
                                    'violation_type': violation_type,
                                    'window_start': start_date,
                                    'window_end': end_date,
                                    'trade_date': trade_date,
                                    'alert': check,
                                }
                                detail.update(result)
                                all_details.append(detail)
                                day_alerts.append(check)
                        except Exception as e:
                            pass  # 单日单项失败不影响整体

                if day_alerts:
                    stock_alerts_today += 1

            alert_days = len(set(d['trade_date'] for d in all_details if d['ts_code'] == ts_code))
            print(f"  -> {len(trading_days)} 个交易日, {alert_days} 天触发告警")

        # ---- 构建输出 ----
        if not all_details:
            print("\n[结果] 未检出异常")
            return pd.DataFrame(), pd.DataFrame()

        detail_df = pd.DataFrame(all_details)

        # 标的级汇总
        summary_rows = []
        for ts_code, group in detail_df.groupby('ts_code'):
            first = group.iloc[0]
            alert_types = group['alert'].value_counts().to_dict()
            alert_days = group['trade_date'].nunique()
            total_days = len(trading_days) if 'trading_days' in dir() else alert_days

            row = {
                'ts_code': ts_code,
                'name': first['name'],
                'case_id': first['case_id'],
                'violation_type': first['violation_type'],
                'window_start': first['window_start'],
                'window_end': first['window_end'],
                'total_alert_days': alert_days,
                'total_alerts': len(group),
                'alert_types': ','.join(sorted(alert_types.keys())),
            }
            for d in ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'D10', 'D11', 'D12', 'D13', 'D14']:
                row[f'{d}_days'] = alert_types.get(d, 0)

            # 峰值指标
            if 'vol_ratio' in group.columns:
                row['max_vol_ratio'] = round(group['vol_ratio'].max(), 2)
            if 'turnover_rate' in group.columns:
                row['max_turnover_rate'] = round(group['turnover_rate'].max(), 2)
            if 'amplitude' in group.columns:
                row['max_amplitude'] = round(group['amplitude'].max(), 2)
            if 'margin_balance_chg' in group.columns:
                row['max_margin_balance_chg'] = round(group['margin_balance_chg'].max(), 2)
            # 建仓期峰值指标
            if 'vol_ratio_ma60' in group.columns:
                row['min_vol_ratio_ma60'] = round(group['vol_ratio_ma60'].min(), 2)
            if 'slope' in group.columns:
                row['max_turnover_slope'] = round(group['slope'].max(), 3)
            if 'days_above_ma20' in group.columns:
                val = group['days_above_ma20'].max()
                row['max_days_above_ma20'] = int(val) if not pd.isna(val) else 0
            if 'avg_vol_ratio' in group.columns:
                row['max_avg_vol_ratio'] = round(group['avg_vol_ratio'].max(), 2)

            # 风险等级
            accumulation_types = sum(1 for d in ['D8', 'D9', 'D10', 'D11', 'D12', 'D13', 'D14'] if alert_types.get(d, 0) > 0)
            if alert_days >= 7 or len(alert_types) >= 6:
                row['risk_level'] = '高'
            elif alert_days >= 4 or len(alert_types) >= 3:
                row['risk_level'] = '中'
            else:
                row['risk_level'] = '低'

            if accumulation_types >= 3:
                row['accumulation_signal'] = '建仓特征显著'
            elif accumulation_types >= 1:
                row['accumulation_signal'] = '疑似建仓'
            else:
                row['accumulation_signal'] = ''

            summary_rows.append(row)

        summary_df = pd.DataFrame(summary_rows)
        risk_order = {'高': 0, '中': 1, '低': 2}
        summary_df['_risk_sort'] = summary_df['risk_level'].map(risk_order)
        summary_df = summary_df.sort_values(['_risk_sort', 'total_alert_days'], ascending=[True, False])
        summary_df = summary_df.drop(columns=['_risk_sort'])

        print(f"\n[结果] 共 {len(summary_df)} 只标的检出异常")
        print(f"  高风险: {(summary_df['risk_level']=='高').sum()} 只")
        print(f"  中风险: {(summary_df['risk_level']=='中').sum()} 只")
        print(f"  低风险: {(summary_df['risk_level']=='低').sum()} 只")

        return summary_df, detail_df

    # ================================================================
    # 模式A：全市场单日扫描（保留原有逻辑）
    # ================================================================

    def scan_all(self, trade_date=None, checks=None):
        """全市场单日扫描"""
        if trade_date is None:
            try:
                recent = self.pro.trade_cal(
                    exchange='SSE',
                    start_date=(datetime.now() - timedelta(days=10)).strftime('%Y%m%d'),
                    end_date=datetime.now().strftime('%Y%m%d'),
                    is_open='1'
                )
                if recent is not None and len(recent) > 0:
                    trade_date = recent['cal_date'].iloc[-1]
                else:
                    trade_date = datetime.now().strftime('%Y%m%d')
            except Exception:
                trade_date = datetime.now().strftime('%Y%m%d')

        trade_date = str(trade_date)
        print(f"\n{'='*60}")
        print(f"  全市场异常交易扫描 - {trade_date}")
        print(f"{'='*60}\n")

        if checks is None:
            checks = ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'D10', 'D11', 'D12', 'D13', 'D14']

        self.get_stock_basic()

        daily_df = pd.DataFrame()
        daily_basic_df = pd.DataFrame()
        margin_df = pd.DataFrame()
        limit_df = pd.DataFrame()

        need_daily = any(c in checks for c in ['D1', 'D3', 'D5', 'D6', 'D8', 'D9', 'D10', 'D12', 'D13', 'D14'])
        need_basic = any(c in checks for c in ['D2', 'D7', 'D8', 'D11'])
        need_margin = 'D6' in checks
        need_limit = 'D4' in checks

        if need_daily:
            daily_df = self.get_daily_batch(trade_date, lookback_days=70)
        if need_basic:
            daily_basic_df = self.get_daily_basic_batch(trade_date)
        if need_margin:
            margin_df = self.get_margin_data(trade_date)
        if need_limit:
            limit_df = self.get_limit_list(trade_date, lookback_days=10)

        all_alerts = []

        check_funcs = {
            'D1': lambda: self._check_D1_batch(daily_df, trade_date),
            'D2': lambda: self._check_D2_batch(daily_basic_df, trade_date),
            'D3': lambda: self._check_D3_batch(daily_df, trade_date),
            'D4': lambda: self._check_D4_batch(limit_df, trade_date),
            'D5': lambda: self._check_D5_batch(daily_df, trade_date),
            'D6': lambda: self._check_D6_batch(margin_df, daily_df, trade_date),
            'D7': lambda: self._check_D7_batch(daily_df, daily_basic_df, trade_date),
            'D8': lambda: self._check_D8_batch(daily_df, daily_basic_df, trade_date),
            'D9': lambda: self._check_D9_batch(daily_df, trade_date),
            'D10': lambda: self._check_D10_batch(daily_df, trade_date),
            'D11': lambda: self._check_D11_batch(daily_basic_df, trade_date),
            'D12': lambda: self._check_D12_batch(daily_df, trade_date),
            'D13': lambda: self._check_D13_batch(daily_df, trade_date),
            'D14': lambda: self._check_D14_batch(daily_df, trade_date),
        }

        for check in checks:
            if check in check_funcs:
                try:
                    alerts = check_funcs[check]()
                    all_alerts.extend(alerts)
                except Exception as e:
                    print(f"  [错误] {check} 检测失败: {e}")

        if not all_alerts:
            print("\n[结果] 未检出异常交易标的")
            return pd.DataFrame()

        return self._build_summary(all_alerts)

    # ---- 模式A的批量检测方法（遍历全市场） ----

    def _check_D1_batch(self, daily_df, trade_date):
        print("[检测] D1 成交量突增...")
        results = []
        target = daily_df[daily_df['trade_date'] == trade_date]
        for _, row in target.iterrows():
            ts_code = row['ts_code']
            r = self._check_D1_single(daily_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D1', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D2_batch(self, daily_basic_df, trade_date):
        print("[检测] D2 换手率异常...")
        results = []
        if daily_basic_df.empty:
            return results
        target = daily_basic_df[daily_basic_df['trade_date'] == trade_date]
        for _, row in target.iterrows():
            ts_code = row['ts_code']
            r = self._check_D2_single(daily_basic_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D2', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D3_batch(self, daily_df, trade_date):
        print("[检测] D3 日内振幅异常...")
        results = []
        target = daily_df[daily_df['trade_date'] == trade_date]
        for _, row in target.iterrows():
            ts_code = row['ts_code']
            r = self._check_D3_single(daily_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D3', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D4_batch(self, limit_df, trade_date):
        print("[检测] D4 连续涨跌停...")
        results = []
        if limit_df.empty:
            return results
        for ts_code in limit_df['ts_code'].unique():
            r = self._check_D4_single(limit_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D4', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D5_batch(self, daily_df, trade_date):
        print("[检测] D5 量价背离...")
        results = []
        target = daily_df[daily_df['trade_date'] == trade_date]
        for _, row in target.iterrows():
            ts_code = row['ts_code']
            r = self._check_D5_single(daily_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D5', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D6_batch(self, margin_df, daily_df, trade_date):
        print("[检测] D6 融资异动...")
        results = []
        if margin_df.empty:
            return results
        for ts_code in margin_df['ts_code'].unique():
            r = self._check_D6_single(margin_df, daily_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D6', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D7_batch(self, daily_df, daily_basic_df, trade_date):
        print("[检测] D7 小盘股突放量...")
        results = []
        if daily_basic_df.empty:
            return results
        target = daily_basic_df[daily_basic_df['trade_date'] == trade_date]
        for _, row in target.iterrows():
            ts_code = row['ts_code']
            r = self._check_D7_single(daily_basic_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D7', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D8_batch(self, daily_df, daily_basic_df, trade_date):
        print("[检测] D8 建仓前地量...")
        results = []
        if daily_df.empty:
            return results
        target = daily_df[daily_df['trade_date'] == trade_date]
        for _, row in target.iterrows():
            ts_code = row['ts_code']
            r = self._check_D8_single(daily_df, daily_basic_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D8', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D9_batch(self, daily_df, trade_date):
        print("[检测] D9 温和放量启动...")
        results = []
        if daily_df.empty:
            return results
        target = daily_df[daily_df['trade_date'] == trade_date]
        for _, row in target.iterrows():
            ts_code = row['ts_code']
            r = self._check_D9_single(daily_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D9', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D10_batch(self, daily_df, trade_date):
        print("[检测] D10 价稳量增...")
        results = []
        if daily_df.empty:
            return results
        target = daily_df[daily_df['trade_date'] == trade_date]
        for _, row in target.iterrows():
            ts_code = row['ts_code']
            r = self._check_D10_single(daily_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D10', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D11_batch(self, daily_basic_df, trade_date):
        print("[检测] D11 换手率爬升...")
        results = []
        if daily_basic_df.empty:
            return results
        target = daily_basic_df[daily_basic_df['trade_date'] == trade_date]
        for _, row in target.iterrows():
            ts_code = row['ts_code']
            r = self._check_D11_single(daily_basic_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D11', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D12_batch(self, daily_df, trade_date):
        print("[检测] D12 量能堆积...")
        results = []
        if daily_df.empty:
            return results
        target = daily_df[daily_df['trade_date'] == trade_date]
        for _, row in target.iterrows():
            ts_code = row['ts_code']
            r = self._check_D12_single(daily_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D12', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D13_batch(self, daily_df, trade_date):
        print("[检测] D13 窄幅吸筹...")
        results = []
        if daily_df.empty:
            return results
        target = daily_df[daily_df['trade_date'] == trade_date]
        for _, row in target.iterrows():
            ts_code = row['ts_code']
            r = self._check_D13_single(daily_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D13', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _check_D14_batch(self, daily_df, trade_date):
        print("[检测] D14 建仓前后量比...")
        results = []
        if daily_df.empty:
            return results
        target = daily_df[daily_df['trade_date'] == trade_date]
        for _, row in target.iterrows():
            ts_code = row['ts_code']
            r = self._check_D14_single(daily_df, ts_code, trade_date)
            if r:
                results.append({'ts_code': ts_code, 'trade_date': trade_date, 'alert': 'D14', **r})
        print(f"  -> 检出 {len(results)} 只")
        return results

    def _build_summary(self, all_alerts):
        """将告警列表汇总为DataFrame"""
        summary = defaultdict(lambda: {'ts_code': '', 'alerts': [], 'details': {}})
        for alert in all_alerts:
            ts_code = alert['ts_code']
            summary[ts_code]['ts_code'] = ts_code
            summary[ts_code]['trade_date'] = alert['trade_date']
            if alert['alert'] not in summary[ts_code]['alerts']:
                summary[ts_code]['alerts'].append(alert['alert'])
            for k, v in alert.items():
                if k not in ('ts_code', 'trade_date', 'alert'):
                    summary[ts_code]['details'][k] = v

        rows = []
        for ts_code, info in summary.items():
            alerts = sorted(info['alerts'])
            alert_count = len(alerts)
            accumulation_count = sum(1 for a in alerts if a in ['D8', 'D9', 'D10', 'D11', 'D12', 'D13', 'D14'])
            if alert_count >= 5:
                risk_level = '高'
            elif alert_count >= 3:
                risk_level = '中'
            else:
                risk_level = '低'
            row = {
                'ts_code': ts_code,
                'trade_date': info['trade_date'],
                'alerts': ','.join(alerts),
                'alert_count': alert_count,
                'risk_level': risk_level,
            }
            row.update(info['details'])
            rows.append(row)

        result = pd.DataFrame(rows)
        if self.stock_basic is not None:
            name_map = dict(zip(self.stock_basic['ts_code'], self.stock_basic['name']))
            result['name'] = result['ts_code'].map(name_map).fillna('未知')

        risk_order = {'高': 0, '中': 1, '低': 2}
        result['_risk_sort'] = result['risk_level'].map(risk_order)
        result = result.sort_values(['_risk_sort', 'alert_count'], ascending=[True, False])
        result = result.drop(columns=['_risk_sort'])

        print(f"\n[结果] 共检出 {len(result)} 只异常标的")
        print(f"  高风险(3+): {(result['risk_level']=='高').sum()} 只")
        print(f"  中风险(2):  {(result['risk_level']=='中').sum()} 只")
        print(f"  低风险(1):  {(result['risk_level']=='低').sum()} 只")
        return result

    # ================================================================
    # 输出
    # ================================================================

    def print_report(self, result):
        """打印扫描报告（模式A用）"""
        if result.empty:
            print("\n无异常交易标的。")
            return

        print(f"\n{'='*80}")
        print(f"  异常交易扫描报告")
        print(f"{'='*80}")

        for _, row in result.iterrows():
            risk_icon = {'高': '!!', '中': '! ', '低': '  '}.get(row['risk_level'], '  ')
            print(f"\n{risk_icon} [{row['risk_level']}风险] {row['ts_code']} {row.get('name', '')}")
            print(f"   触发检测: {row['alerts']} (共{row['alert_count']}项)")

            detail_items = []
            if 'vol_ratio' in row and not pd.isna(row['vol_ratio']):
                detail_items.append(f"量比={row['vol_ratio']}x")
            if 'turnover_rate' in row and not pd.isna(row['turnover_rate']):
                detail_items.append(f"换手率={row['turnover_rate']}%")
            if 'amplitude' in row and not pd.isna(row['amplitude']):
                detail_items.append(f"振幅={row['amplitude']}%")
            if 'consecutive_days' in row and not pd.isna(row['consecutive_days']):
                detail_items.append(f"连续{row['consecutive_days']}日{row.get('limit_type','')}停")
            if 'divergence_type' in row and not pd.isna(row['divergence_type']):
                detail_items.append(f"{row['divergence_type']}")
            if 'margin_type' in row and not pd.isna(row['margin_type']):
                detail_items.append(f"{row['margin_type']}")
            if 'turnover_jump' in row and not pd.isna(row['turnover_jump']):
                detail_items.append(f"换手跳升={row['turnover_jump']}x")
            # 建仓期指标
            if 'vol_ratio_ma60' in row and not pd.isna(row['vol_ratio_ma60']):
                detail_items.append(f"地量比(MA60)={row['vol_ratio_ma60']}x")
            if 'slope' in row and not pd.isna(row['slope']):
                detail_items.append(f"换手斜率={row['slope']}")
            if 'days_above_ma20' in row and not pd.isna(row['days_above_ma20']):
                detail_items.append(f"量能堆积={row['days_above_ma20']}天")
            if 'avg_vol_ratio' in row and not pd.isna(row['avg_vol_ratio']):
                detail_items.append(f"均量比={row['avg_vol_ratio']}x")

            if detail_items:
                print(f"   指标: {' | '.join(detail_items)}")

        print(f"\n{'='*80}")
        print(f"  合计: {len(result)} 只异常标的")
        print(f"{'='*80}\n")

    def print_watchlist_report(self, summary_df, detail_df):
        """打印标的列表扫描报告（模式B用）"""
        if summary_df.empty:
            print("\n无异常交易标的。")
            return

        print(f"\n{'='*80}")
        print(f"  标的列表异常扫描报告")
        print(f"{'='*80}")

        for _, row in summary_df.iterrows():
            risk_mark = {'高': '!!', '中': '! ', '低': '  '}.get(row['risk_level'], '  ')
            acc_signal = row.get('accumulation_signal', '')
            signal_str = f" [{acc_signal}]" if acc_signal else ''
            print(f"\n{risk_mark} [{row['risk_level']}风险]{signal_str} {row['ts_code']} {row.get('name', '')}")
            print(f"   时间窗口: {row['window_start']} ~ {row['window_end']}")
            print(f"   告警天数: {row['total_alert_days']}天, 共{row['total_alerts']}次告警")
            print(f"   触发类型: {row['alert_types']}")

            detail_items = []
            for d in ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9', 'D10', 'D11', 'D12', 'D13', 'D14']:
                if f'{d}_days' in row and row[f'{d}_days'] > 0:
                    detail_items.append(f"{d}={row[f'{d}_days']}天")
            print(f"   分布: {' | '.join(detail_items)}")

            peak_items = []
            if 'max_vol_ratio' in row and not pd.isna(row['max_vol_ratio']):
                peak_items.append(f"峰值量比={row['max_vol_ratio']}x")
            if 'max_turnover_rate' in row and not pd.isna(row['max_turnover_rate']):
                peak_items.append(f"峰值换手率={row['max_turnover_rate']}%")
            if 'max_amplitude' in row and not pd.isna(row['max_amplitude']):
                peak_items.append(f"峰值振幅={row['max_amplitude']}%")
            if 'min_vol_ratio_ma60' in row and not pd.isna(row['min_vol_ratio_ma60']):
                peak_items.append(f"最低地量比={row['min_vol_ratio_ma60']}x")
            if 'max_turnover_slope' in row and not pd.isna(row['max_turnover_slope']):
                peak_items.append(f"最大换手斜率={row['max_turnover_slope']}")
            if 'max_days_above_ma20' in row and not pd.isna(row['max_days_above_ma20']):
                peak_items.append(f"最多量能堆积={row['max_days_above_ma20']}天")
            if peak_items:
                print(f"   峰值: {' | '.join(peak_items)}")

        print(f"\n{'='*80}")
        print(f"  合计: {len(summary_df)} 只标的检出异常")
        print(f"  明细共 {len(detail_df)} 条记录")
        print(f"{'='*80}\n")

    def save_to_csv(self, result, filepath=None, trade_date=None):
        """保存结果到CSV（模式A用）"""
        if result.empty:
            print("无数据可保存")
            return
        if filepath is None:
            td = trade_date or result.iloc[0]['trade_date']
            filepath = f"abnormal_trading_{td}.csv"
        result.to_csv(filepath, index=False, encoding='utf-8-sig')
        print(f"结果已保存到: {filepath}")

    def save_watchlist_result(self, summary_df, detail_df, output_prefix=None):
        """保存标的列表扫描结果（模式B用）"""
        if summary_df.empty:
            print("无数据可保存")
            return
        if output_prefix is None:
            output_prefix = 'watchlist_scan'

        summary_path = f"{output_prefix}_summary.csv"
        detail_path = f"{output_prefix}_detail.csv"

        summary_df.to_csv(summary_path, index=False, encoding='utf-8-sig')
        detail_df.to_csv(detail_path, index=False, encoding='utf-8-sig')
        print(f"汇总已保存到: {summary_path}")
        print(f"明细已保存到: {detail_path}")


# ============================================================
# 命令行入口
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description='A股日频量价+资金异常交易扫描器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 模式A - 全市场扫描
  python abnormal_trading_scanner.py                              # 扫描最近交易日
  python abnormal_trading_scanner.py 20240603                     # 扫描指定日期
  python abnormal_trading_scanner.py 20240601 20240607            # 扫描日期范围
  python abnormal_trading_scanner.py --checks D1,D2,D6            # 指定检测项

  # 模式B - 标的列表定向扫描
  python abnormal_trading_scanner.py --watchlist watchlist.csv    # 从CSV读取标的
  python abnormal_trading_scanner.py --watchlist watchlist.csv --checks D1,D3,D6
  python abnormal_trading_scanner.py --watchlist watchlist.csv --output my_result
        """
    )
    parser.add_argument('dates', nargs='*', help='交易日期 (YYYYMMDD)，可指定1-2个（模式A）')
    parser.add_argument('--checks', type=str, default=None,
                        help='指定检测项，逗号分隔，如 D1,D2,D6')
    parser.add_argument('--output', type=str, default=None,
                        help='输出文件路径前缀')
    parser.add_argument('--watchlist', type=str, default=None,
                        help='标的列表CSV文件路径（模式B），包含 ts_code,start_date,end_date 列')
    return parser.parse_args()


def main():
    args = parse_args()

    checks = None
    if args.checks:
        checks = [c.strip() for c in args.checks.split(',')]

    scanner = AbnormalTradingScanner()

    # ---- 模式B：标的列表定向扫描 ----
    if args.watchlist:
        summary_df, detail_df = scanner.scan_watchlist(args.watchlist, checks=checks)
        scanner.print_watchlist_report(summary_df, detail_df)
        scanner.save_watchlist_result(summary_df, detail_df, output_prefix=args.output)
        return

    # ---- 模式A：全市场扫描 ----
    trade_dates = []
    if len(args.dates) >= 1:
        trade_dates.append(args.dates[0])
    if len(args.dates) >= 2:
        trade_dates.append(args.dates[1])

    if len(trade_dates) == 0:
        result = scanner.scan_all(trade_date=None, checks=checks)
        scanner.print_report(result)
        scanner.save_to_csv(result, filepath=args.output)
    elif len(trade_dates) == 1:
        result = scanner.scan_all(trade_date=trade_dates[0], checks=checks)
        scanner.print_report(result)
        scanner.save_to_csv(result, filepath=args.output, trade_date=trade_dates[0])
    else:
        start = datetime.strptime(trade_dates[0], '%Y%m%d')
        end = datetime.strptime(trade_dates[1], '%Y%m%d')
        current = start
        while current <= end:
            date_str = current.strftime('%Y%m%d')
            try:
                result = scanner.scan_all(trade_date=date_str, checks=checks)
                scanner.print_report(result)
                scanner.save_to_csv(result, filepath=args.output, trade_date=date_str)
            except Exception as e:
                print(f"[错误] {date_str} 扫描失败: {e}")
            current += timedelta(days=1)
            if current <= end:
                time.sleep(1)


if __name__ == '__main__':
    main()
