---
name: "case-xlsx-to-watchlist"
description: "将证监会处罚案例的异构xlsx文件（含股票名/时间窗口/事件时间线）批量提取为模式B扫描所需的 watchlist.csv。Invoke when user asks to build a watchlist from case xlsx files, extract stock_code/time_window from regulatory penalty Excel reports, or convert 建仓案例xlsx 批量转结构化csv."
---

# 案例 xlsx → watchlist.csv 批量提取技能

## 适用场景

将一批结构异构的Excel案例文件（如证监会处罚决定书的"行情分析"报告、操纵市场案例汇编）批量提取为 `abnormal-trading-detection` skill 模式B所需的结构化 watchlist：

```csv
ts_code,start_date,end_date,name,case_id,violation_type
603709.SH,20180129,20180223,中源家居,20210518_陈建铭、谢晶、胡侃,操纵市场
```

**典型输入文件特征**：
- 文件名包含处罚日期、当事人、股票名（如 `20210518_中国证监会行政处罚决定书（陈建铭等）_中国证券监督管理委员会_中源家居_行情分析.xlsx`）
- 内含 3 个常见 sheet：`行业信息时间线`、`利好与利空数量变化（按周）`、`频率变化趋势分析`
- 各 sheet 的列名/格式在不同文件之间存在高度差异

## 文件结构

```
.trae/skills/case-xlsx-to-watchlist/
├── SKILL.md                              # 本文件
└── build_watchlist_from_xlsx.py          # 通用提取脚本
```

## 使用方法

### 一键执行

```bash
# 修改脚本顶部的 BASE_DIR 和 OUTPUT_CSV 后运行
python .trae/skills/case-xlsx-to-watchlist/build_watchlist_from_xlsx.py
```

### 在Python中调用

```python
import sys
sys.path.insert(0, '.trae/skills/case-xlsx-to-watchlist')
from build_watchlist_from_xlsx import main

# 修改全局常量后调用
main()
```

## 核心方法论

本 skill 沉淀了一套从「**异构案例 xlsx**」批量提取「**结构化 watchlist**」的通用工程方法。

### 提取字段映射

| watchlist 列 | 提取来源 | 关键挑战 |
|--------------|---------|----------|
| `ts_code` | sheet正文或股票名反查 | 历史更名/退市导致直接反查失败 |
| `start_date` | 多sheet多列扫描 | 列名千变万化、日期格式异构、跨年 |
| `end_date` | 同上 | 同上 |
| `name` | 文件名解析 | 文件名分隔符不统一 |
| `case_id` | 文件名解析 | 处罚日期+当事人 |
| `violation_type` | 业务常量 | 通常固定 |

### 五大经验教训

#### 1. 文件名解析：用 rfind + skip_tokens 兜底

❌ **错误做法**：硬编码 `split('中国证券监督管理委员会')[1]` 取第二段

✅ **正确做法**：
- 用 `rfind` 取**最后一次出现**的位置（处理文件名前缀也含同名词）
- 维护 `skip_tokens` 集合过滤通用关键词（`行情分析`、`扩充版`、`精简版`、`v2` 等）
- 处罚日期用 `re.match(r'^(\d{8})')` 提取前缀，处理 `20221212_1xxx` 这种带序号格式

```python
skip_tokens = {'行情分析', '扩充版', '最终版', '精简版',
               '行业信息时间线', '利好利空', 'v2',
               '中国证券监督管理委员会', '行政处罚决定书'}
idx = base.rfind('中国证券监督管理委员会')
after = base[idx + len('中国证券监督管理委员会'):].strip('_')
for p in after.split('_'):
    if p and p not in skip_tokens and not re.match(r'^v\d+$', p):
        stock_name = p
        break
```

#### 2. 股票代码：三层兜底映射

Tushare 的 `stock_basic` 接口 `name` 字段返回的是 **当前最新名称**。但案例发生时（如 2017 年），股票名可能是「嘉澳环保」，而现在已改名为「ST嘉澳」。这是导致 23% 的标的反查失败的根本原因。

**三层兜底设计**：

| 优先级 | 来源 | 覆盖范围 | 数据接口 |
|--------|------|---------|---------|
| 1（最高）| 手工映射 `MANUAL_NAME_TO_CODE` | 已退市/更名的"硬骨头"股 | 人工维护 |
| 2 | Tushare `namechange` 历史曾用名 | 名称变更过的股票 | `pro.namechange()` |
| 3 | Tushare `stock_basic` 当前名 | 在市股票 + 部分已退市 | `pro.stock_basic(list_status='L,D,P')` |

```python
# 加载顺序：先低优先级，再用高优先级覆盖
name_map = {}  # 1) basic当前名
# 2) namechange曾用名（不覆盖已存在的）
for nm, code in namechange_records:
    if nm not in name_map:
        name_map[nm] = code
# 3) 手工映射（最高优先级，覆盖前两层）
name_map.update(MANUAL_NAME_TO_CODE)
```

**注意**：`pro.namechange()` 一次性调用有 10000 条上限，**大市场范围请分批拉取**，否则会出现"数据被截断、名称查不到"的奇怪现象。

#### 3. 日期窗口：四层提取策略

不同案例文件的"时间窗口"埋藏位置完全不同，需要**按优先级顺序**多策略提取：

| 策略 | 优先级 | 适用文件类型 | 提取位置 |
|------|--------|------------|---------|
| 1 | 最高 | "频率变化趋势分析"含结构化文本 | `时间范围：2019-01-14 ~ 2019-02-15` 或 `分析时间范围：2019年12月19日—2020年1月17日` |
| 2 | 中 | "利好与利空数量变化"sheet | 按周表的首尾行时间区间 |
| 3 | 中 | "行业信息时间线"sheet | 合并标题行的 `(分析范围：2020.01.19-02.18)` 或时间列首尾 |
| 4 | 最低 | 全文兜底扫描 | 关键词：`分析范围`、`建仓期`、`建仓前后两周（xx-xx）` |

**关键正则模式**（按支持的日期格式分类）：

```python
# 1. YYYY-MM-DD ~ YYYY-MM-DD
r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*[~至到\-—]\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})'
# 2. YYYY年M月D日—YYYY年M月D日（中文）
r'(\d{4})年(\d{1,2})月(\d{1,2})日\s*[-~至到—]\s*(\d{4})年(\d{1,2})月(\d{1,2})日'
# 3. M月D日 至 M月D日（年份从上下文推断）
r'(\d{1,2})月(\d{1,2})日'  # 多次匹配取前两个
# 4. M.D-M.D 或 M/D-M/D（年份从上下文推断）
r'(\d{1,2})[./](\d{1,2})\s*[-~]\s*(\d{1,2})[./](\d{1,2})'
```

#### 4. 建仓年份识别：取**事件时间线列的众数**，不取最小年

❌ **错误做法**：从全文提取所有 `20XX` 年份，取最小值（会把"历史回顾"误判为建仓年）

✅ **正确做法**：
- 从 `行业信息时间线` sheet 的 `时间`/`日期` 列统计**众数年份**
- 退化策略：从 `频率变化趋势分析` 的 `时间范围:` 提示语提取
- 处理合并标题行：当首行被识别为列名时（如 `奇精机械建仓期：...`），用 `header=1` 重新读

```python
from collections import Counter
years = [int(y) for v in df['时间'].dropna()
              for y in re.findall(r'(20\d{2})', str(v))
              if 2000 < int(y) <= 2024]
return str(Counter(years).most_common(1)[0][0])
```

#### 5. 跨年区间：必须用月份比较推断年份递增

格式如 `12.19-1.17`（年初年末）或 `12月29日至1月8日`，结束月份小于开始月份意味着跨年。

```python
start = f'{year}{m1:02d}{d1:02d}'
end_year = int(year)
if int(m2) < int(m1):  # 关键：跨年判断
    end_year += 1
end = f'{end_year}{m2:02d}{d2:02d}'
```

## 数据质量校验

提取完成后，**必须做以下逻辑校验**：

| 校验项 | 期望 | 异常原因 |
|--------|------|---------|
| `start_date <= end_date` | True | 跨年未处理 |
| `start_date < punish_date` | True（建仓发生在处罚前） | 年份识别错误 |
| `(punish_date - start_date).days < 365*10` | 一般成立 | 历史年份污染 |
| 列无空值 | True | 提取失败的应被skipped |
| 年份分布合理 | 应集中在近5-8年 | 否则需排查年份提取 |

校验脚本片段：

```python
df['punish_date'] = df['case_id'].str.extract(r'(\d{8})')[0]
df['days_diff'] = (pd.to_datetime(df['punish_date']) - pd.to_datetime(df['start_date'])).dt.days
abnormal = df[df['days_diff'] < 0]
assert len(abnormal) == 0, f'有{len(abnormal)}条建仓晚于处罚！'
```

## 常见跳过原因 + 解决方案

| 跳过原因 | 含义 | 解决方案 |
|---------|------|---------|
| `no_stock_name` | 文件名无具体股票（行业新闻类） | 业务上确认是否需要，通常跳过 |
| `no_date_range` | 所有4层策略都未提取到日期 | 人工查看该文件、增加新模式 |
| `no_code_for_<name>` | 股票名反查不到 ts_code | 加入 `MANUAL_NAME_TO_CODE` 兜底 |

## 调试技巧

1. **逐文件inspector**：写临时脚本读单个文件，打印所有sheet的columns和前5行，快速理解格式
2. **summary前先看cache**：检查 `stock_basic_cache.csv` 是否包含目标股票名
3. **正则模式**：先用 `re.search().group()` 在交互式环境验证再嵌入主脚本
4. **打印跳过原因**：每次跳过都记录 `(fname, reason)`，运行后批量分析

## 复用到新场景的步骤

1. **修改 `BASE_DIR` / `OUTPUT_CSV`** 指向新的输入文件夹/输出路径
2. **更新 `parse_filename`** 的 `skip_tokens` 和定位锚词（如不是"中国证券监督管理委员会"）
3. **扩展 `MANUAL_NAME_TO_CODE`** 加入新场景的退市/更名股
4. **新增日期格式正则**：在 `parse_one_date_range` 或策略4的 `patterns` 列表中追加
5. **复用 `load_stock_basic`** 即可（依赖 `TUSHARE_TOKEN` 环境变量）

## 实测性能

- **100 个 xlsx 文件 / 98 条成功提取（98%）**
- 主要失败原因：2 个文件无具体股票名（行业级新闻）
- 总耗时约 30-60 秒（含 Tushare cache 加载）
- 数据完整性：0 空值、跨年正确、年份分布集中在 2017-2020（符合A股近年案件分布）

## 依赖

- Python 3.7+
- `pandas`, `openpyxl`, `tushare`
- 环境变量 `TUSHARE_TOKEN`（120积分以上即可用 `namechange` 接口）
- 缓存目录：`.trae/skills/abnormal-trading-detection/cache/`（与异常交易检测skill共享）
