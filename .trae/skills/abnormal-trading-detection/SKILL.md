---
name: "abnormal-trading-detection"
description: "A股日频量价+资金异常交易扫描。基于Tushare Pro日线/融资融券/换手率/资金流向数据，检测成交量突增、换手率异常、振幅异常、连续涨跌停、量价背离、融资异动、小盘股突放量等7类异常信号，以及建仓前地量、温和放量启动、价稳量增、换手率爬升、量能堆积、窄幅吸筹、建仓前后量比等7类建仓期信号。Invoke when user asks to scan abnormal A-share trading, detect market manipulation signals, run daily trading anomaly checks, or identify accumulation/建仓 patterns."
---

# 异常交易检测 - 日频量价+资金异常扫描（含建仓期挖掘）

基于 Tushare Pro 公开数据（2120积分可全覆盖），支持两种扫描模式：

- **模式A - 全市场单日扫描**：对全A股在指定日期进行14维度检测
- **模式B - 标的列表定向扫描**：从CSV读取标的列表+时间窗口，在各自窗口内逐日扫描

## 覆盖的14大检测维度

### 原有异常检测（D1-D7）

| 编号 | 检测项 | 核心指标 | Tushare接口 | 积分要求 |
|------|--------|---------|------------|---------|
| D1 | 成交量突增 | vol / MA(vol,20) > 阈值 | `daily` | 免费 |
| D2 | 换手率异常 | turnover_rate > 板块均值N倍 | `daily_basic` | 120分 |
| D3 | 日内振幅异常 | (high-low)/pre_close > 阈值 | `daily` | 免费 |
| D4 | 连续涨跌停 | 连续N日触及涨跌停 | `limit_list` | 免费 |
| D5 | 量价背离 | 价涨量缩 / 价跌量增 | `daily` | 免费 |
| D6 | 融资异动 | 融资余额突增 / 融资买入占比异常 | `margin` | 免费 |
| D7 | 小盘股突放量 | 市值<50亿 + 换手率从低位突增至>15% | `daily` + `daily_basic` | 120分 |

### 建仓期检测（D8-D14）⭐

| 编号 | 检测项 | 公式 | 阶段 | Tushare接口 |
|------|--------|------|------|------------|
| D8 | 建仓前地量 | 5日内≥3日满足：`vol/MA60 < 0.5` AND `换手率 < 2%` AND `振幅 < 5%` | 建仓前 | `daily` + `daily_basic` |
| D9 | 温和放量启动 | `1.2 ≤ vol/MA20 ≤ 2.5` AND `|pct_chg| < 3%` AND `前一日vol/MA20 < 1.0` | 建仓时 | `daily` |
| D10 | 价稳量增 | 连续3日：`|pct_chg| < 3%` AND `vol ≥ MA20` AND `3日均量比 ≥ 1.2` | 建仓时 | `daily` |
| D11 | 换手率爬升 | 回溯10日：`起始换手率 < 3%` AND `线性回归斜率 > 0.15` AND `R² > 0.5` | 建仓时 | `daily_basic` |
| D12 | 量能堆积 | 10日内≥5日 `vol ≥ MA20` AND `10日均量比 ≥ 1.2` | 建仓时 | `daily` |
| D13 | 窄幅吸筹 | 连续5日：`振幅 < 5%` AND `vol ≥ MA20` AND `5日区间涨跌 < 3%` | 建仓时 | `daily` |
| D14 | 建仓前后量比 | `近20日均量 / 前20日均量 > 2.0` AND `近20日≥8日vol ≥ MA20` | 跨阶段 | `daily` |

> **建仓信号等级**：触发3个及以上建仓期检测 → `建仓特征显著`；触发1-2个 → `疑似建仓`

## 文件结构

```
.trae/skills/abnormal-trading-detection/
├── SKILL.md                          # 本文件
└── abnormal_trading_scanner.py       # 核心扫描脚本
```

## 使用方法

### 模式A - 全市场单日扫描

```bash
# 扫描最近一个交易日（默认14维度）
python .trae/skills/abnormal-trading-detection/abnormal_trading_scanner.py

# 扫描指定日期
python .trae/skills/abnormal-trading-detection/abnormal_trading_scanner.py 20240603

# 扫描日期范围
python .trae/skills/abnormal-trading-detection/abnormal_trading_scanner.py 20240601 20240607

# 仅检测建仓期信号
python .trae/skills/abnormal-trading-detection/abnormal_trading_scanner.py --checks D8,D9,D10,D11,D12,D13,D14

# 混合检测
python .trae/skills/abnormal-trading-detection/abnormal_trading_scanner.py --checks D1,D8,D9,D10,D12,D14
```

### 模式B - 标的列表定向扫描

```bash
# 从CSV读取标的列表，在各自时间窗口内扫描
python .trae/skills/abnormal-trading-detection/abnormal_trading_scanner.py --watchlist watchlist.csv

# 仅检测建仓期信号
python .trae/skills/abnormal-trading-detection/abnormal_trading_scanner.py --watchlist watchlist.csv --checks D8,D9,D10,D11,D12,D13,D14

# 指定输出文件前缀
python .trae/skills/abnormal-trading-detection/abnormal_trading_scanner.py --watchlist watchlist.csv --output my_result
```

**标的列表CSV格式（模式B）：**

```csv
ts_code,start_date,end_date,name,case_id,violation_type
000001.SZ,20240101,20240331,平安银行,CASE001,内幕交易
600519.SH,20240201,20240430,贵州茅台,CASE002,操纵市场
```

必选列：`ts_code`, `start_date`, `end_date`
可选列：`name`, `case_id`, `violation_type`（用于输出标注）

### 在Python中调用

```python
from abnormal_trading_scanner import AbnormalTradingScanner

scanner = AbnormalTradingScanner()

# 模式A：全市场单日扫描
results = scanner.scan_all(trade_date='20240603')
scanner.print_report(results)
scanner.save_to_csv(results, 'abnormal_report.csv')

# 模式B：标的列表定向扫描
summary_df, detail_df = scanner.scan_watchlist('watchlist.csv')
scanner.print_watchlist_report(summary_df, detail_df)
scanner.save_watchlist_result(summary_df, detail_df, output_prefix='my_result')

# 仅建仓期检测
summary_df, detail_df = scanner.scan_watchlist('watchlist.csv', checks=['D8','D9','D10','D11','D12','D13','D14'])
```

## 输出说明

### 模式A 输出

| 文件 | 内容 |
|------|------|
| `abnormal_trading_{date}.csv` | 当日所有异常标的汇总 |

| 字段 | 说明 |
|------|------|
| `ts_code` | 股票代码 |
| `name` | 股票名称 |
| `trade_date` | 交易日期 |
| `alerts` | 触发的检测项标签列表（如 D1,D3,D8,D12） |
| `alert_count` | 触发检测项数量 |
| `risk_level` | 风险等级：高(5+项) / 中(3-4项) / 低(1-2项) |

### 模式B 输出（双表）

| 文件 | 内容 |
|------|------|
| `{prefix}_summary.csv` | 标的级汇总：每个标的的告警天数、类型分布、峰值指标、风险等级、建仓信号 |
| `{prefix}_detail.csv` | 逐日明细：每个标的每天的每条告警记录，含具体指标值 |

**汇总表关键字段：**

| 字段 | 说明 |
|------|------|
| `ts_code` / `name` | 股票代码/名称 |
| `case_id` / `violation_type` | 案例编号/违规类型（来自输入CSV） |
| `window_start` / `window_end` | 时间窗口 |
| `total_alert_days` | 告警天数 |
| `total_alerts` | 总告警次数 |
| `alert_types` | 触发的检测类型（如 D1,D3,D8,D12） |
| `D1_days` ~ `D14_days` | 各检测项的告警天数 |
| `max_vol_ratio` / `max_turnover_rate` / `max_amplitude` | 窗口内峰值指标 |
| `min_vol_ratio_ma60` | 最低地量比（D8） |
| `max_turnover_slope` | 最大换手爬升斜率（D11） |
| `max_days_above_ma20` | 最多量能堆积天数（D12） |
| `risk_level` | 风险等级：高(>=7天或>=6类) / 中(>=4天或>=3类) / 低 |
| `accumulation_signal` | 建仓信号：建仓特征显著 / 疑似建仓 / (空) |

## 检测阈值配置

所有阈值定义在 `AbnormalTradingScanner.THRESHOLDS` 字典中，可按需调整：

```python
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
```

## 建仓期检测逻辑详解

建仓期挖掘的核心思路是捕捉**建仓前 → 建仓时**的量价渐变过程：

```
建仓前（D8 地量）→ 建仓启动（D9 温和放量、D14 量比跃迁）
                 → 建仓进行（D10 价稳量增、D12 量能堆积）
                 → 建仓特征（D11 换手爬升、D13 窄幅吸筹）
```

| 阶段 | 检测项 | 信号含义 | 关键约束 |
|------|--------|---------|---------|
| 建仓前 | D8 地量 | 无人关注，成交清淡，为主力低吸创造条件 | 5日内≥3日满足（排除偶然单日地量） |
| 启动 | D9 温和放量 | 量能开始温和放大但价格未明显波动，主力悄悄进场 | 前一日必须缩量（定位转折点） |
| 启动 | D14 量比跃迁 | 近期均量相比远期显著放大，确认量能级别跃迁 | 近20天≥8天放量（排除单日脉冲） |
| 进行 | D10 价稳量增 | 连续多日量能维持高位但价格稳定，主力控盘吸筹 | 3日均量比≥1.2（排除擦边均量） |
| 进行 | D12 量能堆积 | 观察窗口内多数交易日放量，持续吸筹行为 | 10日均量比≥1.2（实质性放量） |
| 特征 | D11 换手爬升 | 换手率从极低位逐步攀升，筹码从散户向主力转移 | R²>0.5（排除随机波动） |
| 特征 | D13 窄幅吸筹 | 连续多日振幅极窄但放量，主力压制价格吸筹 | 5日区间涨跌<3%（排除趋势性上涨） |

### 各维度公式详解

**D8 建仓前地量** — 两阶段校验：
```
Step 1 - 单日条件:
  is_drought_t = (vol_t / MA60_t < 0.5) AND (turnover_rate_t < 2%) AND ((high_t - low_t) / pre_close_t < 5%)
Step 2 - 持续性:
  alert = count(is_drought in 最近5日) ≥ 3
```

**D9 温和放量启动** — 转折点定位：
```
Step 1 - 当日放量:
  is_gentle_t = (1.2 ≤ vol_t / MA20_t ≤ 2.5) AND (|pct_chg_t| < 3%)
Step 2 - 前序静默:
  alert = is_gentle_t AND (vol_t-1 / MA20_t-1 < 1.0)
```

**D10 价稳量增** — 强度约束：
```
Step 1 - 连续条件:
  For i in [t-2, t]: |pct_chg_i| < 3% AND vol_i / MA20_i ≥ 1.0
Step 2 - 均量强度:
  alert = all(condition_i) AND mean(vol_i / MA20_i) ≥ 1.2
```

**D11 换手率爬升** — 趋势拟合：
```
Step 1 - 线性回归: y = [turnover_t-9, ..., turnover_t], slope = Cov(x,y)/Var(x)
Step 2 - 趋势质量:
  alert = (y[0] < 3%) AND (slope > 0.15) AND (R² > 0.5)
```

**D12 量能堆积** — 实质性放量：
```
Step 1 - 高于均量天数: count(vol_i / MA20_i ≥ 1.0 for i in [t-9, t]) ≥ 5
Step 2 - 堆积强度:
  alert = (days_above ≥ 5) AND mean(vol_i / MA20_i) ≥ 1.2
```

**D13 窄幅吸筹** — 价格压制：
```
Step 1 - 每日条件: amplitude_i < 5% AND vol_i / MA20_i ≥ 1.0 for i in [t-4, t]
Step 2 - 区间价格约束:
  alert = all(condition_i) AND |close_t - close_t-4| / close_t-4 < 3%
```

**D14 建仓前后量比** — 持续性跃迁：
```
Step 1 - 量比: post_avg_vol / pre_avg_vol > 2.0 (post=近20天, pre=前20天)
Step 2 - 放量持续性:
  alert = (vol_ratio > 2.0) AND count(vol_i / MA20_i ≥ 1.0 in post_window) ≥ 8
```

## 注意事项

1. **环境变量**：需要设置 `TUSHARE_TOKEN` 环境变量
2. **首次运行**：会缓存全A股基础信息到 `stock_basic_cache.csv`
3. **数据依赖**：D2/D7/D8/D11 需要 `daily_basic` 接口（120积分），D6 需要 `margin` 接口
4. **回溯窗口**：建仓期检测需要60日均线，回溯天数从30天增加到70天
5. **模式B性能**：按标的逐个拉取数据，每个标的约0.5-1秒（含API限流），适合几十到上百个标的
6. **模式A性能**：全市场扫描约需3-8分钟（取决于网络和股票数量，14维度比7维度更耗时）
7. **误报说明**：检测结果仅为"疑似异常/疑似建仓"，不构成违规认定或投资建议，需人工复核
8. **建仓信号**：建议关注 `accumulation_signal='建仓特征显著'` 的标的，结合D8+D9+D14的时序关系进一步分析
