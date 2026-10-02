#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指标路由：扫描 md/ 全部文章，按关键词命中数建立 指标→文章 映射表
输出: indicator_routes.json {indicator: {articles: [{file, hits, title}], total_files}}
"""
import json
import os
import re

MD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "md")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "indicator_routes.json")

# 指标词典：key → (中文类别名, 关键词列表)
INDICATORS = {
    # 盈利能力
    "roe": ("盈利能力", ["ROE", "净资产收益率"]),
    "roa": ("盈利能力", ["ROA", "总资产收益率"]),
    "roic": ("盈利能力", ["ROIC"]),
    "gross_margin": ("盈利能力", ["毛利率", "毛利"]),
    "net_margin": ("盈利能力", ["净利率", "销售净利率"]),
    "core_profit": ("盈利能力", ["核心利润"]),
    "equity_multiplier": ("盈利能力", ["权益乘数", "杜邦", "杠杆率", "财务杠杆"]),
    "eps": ("盈利能力", ["每股收益", "股本扩", "增发新股"]),
    # 现金流质量
    "fcf": ("现金流质量", ["自由现金流", "FCFF"]),
    "cash_to_profit": ("现金流质量", ["净现比", "净利润现金含量", "现金含量"]),
    "cash_to_revenue": ("现金流质量", ["收现比", "营业收入现金含量", "收现"]),
    "dividend_financing": ("现金流质量", ["分红融资比", "派息率", "分红率"]),
    "cash_cycle": ("现金流质量", ["现金循环周期", "营运资金", "周转天数"]),
    # 偿债能力
    "debt_ratio": ("偿债能力", ["资产负债率", "负债率", "负债比重"]),
    "interest_bearing_debt": ("偿债能力", ["有息负债", "带息债务"]),
    "liquidity_ratio": ("偿债能力", ["流动比率", "速动比率"]),
    "interest_cover": ("偿债能力", ["已获利息倍数", "利息保障", "利息覆盖", "利息倍数"]),
    "cash_vs_debt": ("偿债能力", ["货币资金", "存贷双高", "短期借款"]),
    # 资产质量
    "receivables": ("资产质量", ["应收账款", "白条收入"]),
    "inventory": ("资产质量", ["存货"]),
    "prepaid": ("资产质量", ["预付账款", "预付款项"]),
    "other_receivable": ("资产质量", ["其他应收款", "其他应付款"]),
    "cip": ("资产质量", ["在建工程"]),
    "fixed_assets": ("资产质量", ["固定资产"]),
    "goodwill": ("资产质量", ["商誉"]),
    "deferred_tax": ("资产质量", ["递延所得税"]),
    "intangibles": ("资产质量", ["无形资产"]),
    "contract_bs": ("资产质量", ["合同资产", "合同负债", "预收账款"]),
    "long_term_expense": ("资产质量", ["长期待摊费用"]),
    # 收入质量
    "gross_vs_net_method": ("收入质量", ["总额法", "净额法"]),
    "income_quality": ("收入质量", ["收入质量", "利润质量", "含金量", "虚增收入", "虚构收入"]),
    "growth_cross": ("收入质量", ["增速", "增长速度", "同比增长"]),
    # 造假识别
    "big_bath": ("造假识别", ["洗大澡"]),
    "ponzi": ("造假识别", ["庞氏"]),
    "timing_shift": ("造假识别", ["寅吃卯粮", "提前确认收入", "推迟确认费用", "费用资本化"]),
    "fraud_realms": ("造假识别", ["五重境界", "造假", "虚增利润"]),
    "audit_opinion": ("造假识别", ["审计意见", "无保留意见", "审计报告"]),
}

files = [f for f in os.listdir(MD_DIR) if f.endswith(".md")]
routes = {}
for key, (cat, kws) in INDICATORS.items():
    pat = re.compile("|".join(re.escape(k) for k in kws))
    arts = []
    for fn in files:
        try:
            with open(os.path.join(MD_DIR, fn), encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        hits = len(pat.findall(text))
        if hits >= 2:  # 至少命中2次才算相关，过滤顺带提及
            arts.append({"file": fn, "hits": hits,
                         "id": re.search(r"_(\d{9,})_", fn).group(1) if re.search(r"_(\d{9,})_", fn) else ""})
    arts.sort(key=lambda x: -x["hits"])
    routes[key] = {"category": cat, "keywords": kws,
                   "articles": arts, "n_articles": len(arts)}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(routes, f, ensure_ascii=False, indent=1)

print(f"文章总数: {len(files)}")
for key, v in routes.items():
    print(f"{v['category']:6s} {key:22s} {v['n_articles']:3d} 篇  关键词命中前3: "
          + ", ".join(a["file"][:40] for a in v["articles"][:3]))
