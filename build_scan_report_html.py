"""
将 watchlist_scan_summary.csv 转为美观的HTML报告。
功能：
  - 顶部统计卡片（总数、风险等级分布、建仓信号分布）
  - 可搜索/可排序/可过滤的交互式表格
  - 14个检测维度按建仓 vs 异常分组显示
  - 风险等级与建仓信号用色彩标识
  - 鼠标悬停显示完整告警类型
"""
import os
import json
import pandas as pd

INPUT_CSV = r'd:\BaiduSyncdisk\code-h\plan_X\watchlist_scan_summary.csv'
OUTPUT_HTML = r'd:\BaiduSyncdisk\code-h\plan_X\watchlist_scan_report.html'

# 维度元数据
DIMENSIONS = {
    # 异常检测
    'D1': ('成交量突增', 'abnormal'),
    'D2': ('换手率异常', 'abnormal'),
    'D3': ('日内振幅异常', 'abnormal'),
    'D4': ('连续涨跌停', 'abnormal'),
    'D5': ('量价背离', 'abnormal'),
    'D6': ('融资异动', 'abnormal'),
    'D7': ('小盘股突放量', 'abnormal'),
    # 建仓期检测
    'D8': ('建仓前地量', 'accumulation'),
    'D9': ('温和放量启动', 'accumulation'),
    'D10': ('价稳量增', 'accumulation'),
    'D11': ('换手率爬升', 'accumulation'),
    'D12': ('量能堆积', 'accumulation'),
    'D13': ('窄幅吸筹', 'accumulation'),
    'D14': ('建仓前后量比', 'accumulation'),
}


def format_date(s):
    s = str(s)
    if len(s) == 8 and s.isdigit():
        return f'{s[:4]}-{s[4:6]}-{s[6:]}'
    return s


def fmt_num(v, digits=2):
    if pd.isna(v):
        return '-'
    if isinstance(v, (int, float)):
        return f'{v:.{digits}f}' if digits > 0 else f'{int(v)}'
    return str(v)


def build_html(df):
    # 统计
    n_total = len(df)
    risk_counts = df['risk_level'].value_counts().to_dict()
    acc_counts = df['accumulation_signal'].fillna('').value_counts().to_dict()
    n_high = risk_counts.get('高', 0)
    n_mid = risk_counts.get('中', 0)
    n_low = risk_counts.get('低', 0)
    n_strong = acc_counts.get('建仓特征显著', 0)
    n_suspect = acc_counts.get('疑似建仓', 0)

    # 维度命中统计
    dim_stats = {}
    for code, (name, kind) in DIMENSIONS.items():
        col = f'{code}_days'
        if col in df.columns:
            hit = int((df[col] > 0).sum())
            dim_stats[code] = {'name': name, 'kind': kind, 'hit': hit}

    # 准备行数据
    rows_html = []
    for _, r in df.iterrows():
        risk = r['risk_level']
        acc = r.get('accumulation_signal', '') if pd.notna(r.get('accumulation_signal')) else ''
        risk_class = {'高': 'risk-high', '中': 'risk-mid', '低': 'risk-low'}.get(risk, '')
        acc_class = {'建仓特征显著': 'acc-strong', '疑似建仓': 'acc-suspect'}.get(acc, '')

        # 维度chip：异常类(红色) + 建仓类(蓝色)
        chips = []
        for code, (dname, kind) in DIMENSIONS.items():
            days = r.get(f'{code}_days', 0)
            if days and days > 0:
                cls = 'chip-abnormal' if kind == 'abnormal' else 'chip-accum'
                chips.append(f'<span class="chip {cls}" title="{code} {dname}: {int(days)}天">{code}·{int(days)}d</span>')
        chips_html = ''.join(chips) if chips else '<span class="chip chip-none">无</span>'

        # 峰值指标tooltip
        peaks = []
        for col, label, digits in [
            ('max_vol_ratio', '峰值量比', 2),
            ('max_turnover_rate', '峰值换手率%', 2),
            ('max_amplitude', '峰值振幅%', 2),
            ('min_vol_ratio_ma60', '最低地量比(MA60)', 2),
            ('max_turnover_slope', '最大换手斜率', 3),
            ('max_days_above_ma20', '最多量能堆积天数', 0),
            ('max_avg_vol_ratio', '最大均量比', 2),
        ]:
            if col in r and pd.notna(r[col]):
                peaks.append(f'{label}={fmt_num(r[col], digits)}')
        peaks_html = '<br/>'.join(peaks) if peaks else '<span class="muted">无</span>'

        rows_html.append(f"""
        <tr data-risk="{risk}" data-acc="{acc}">
          <td class="code-cell"><a href="https://xueqiu.com/S/{r['ts_code'].replace('.','').upper()}" target="_blank">{r['ts_code']}</a></td>
          <td class="name-cell"><strong>{r['name']}</strong></td>
          <td class="window-cell">{format_date(r['window_start'])}<br/><span class="muted">~ {format_date(r['window_end'])}</span></td>
          <td class="num">{int(r['total_alert_days'])}</td>
          <td class="num">{int(r['total_alerts'])}</td>
          <td class="chips-cell">{chips_html}</td>
          <td class="peaks-cell">{peaks_html}</td>
          <td class="case-cell" title="{r['case_id']}">{r['case_id'][:18]}{'...' if len(str(r['case_id']))>18 else ''}</td>
          <td><span class="badge {risk_class}">{risk}</span></td>
          <td><span class="badge {acc_class}">{acc or '-'}</span></td>
        </tr>
        """)

    rows_joined = '\n'.join(rows_html)

    # 维度统计卡片
    dim_cards_html = []
    for code, info in dim_stats.items():
        kind_label = '建仓期' if info['kind'] == 'accumulation' else '异常'
        kind_cls = 'dim-card-accum' if info['kind'] == 'accumulation' else 'dim-card-abnormal'
        dim_cards_html.append(f"""
        <div class="dim-card {kind_cls}">
            <div class="dim-code">{code}</div>
            <div class="dim-name">{info['name']}</div>
            <div class="dim-hit">{info['hit']} 只命中</div>
            <div class="dim-kind">{kind_label}</div>
        </div>
        """)
    dim_cards_joined = '\n'.join(dim_cards_html)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>建仓期扫描报告 - watchlist_scan_summary</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    margin: 0; padding: 0; background: #f5f7fa; color: #2c3e50;
  }}
  .header {{
    background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
    color: #fff; padding: 28px 40px;
  }}
  .header h1 {{ margin: 0; font-size: 26px; font-weight: 600; }}
  .header .subtitle {{ margin-top: 6px; opacity: 0.85; font-size: 14px; }}
  .container {{ max-width: 1600px; margin: 0 auto; padding: 24px 40px 60px; }}

  /* 统计卡片 */
  .stats-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 16px; margin-bottom: 28px;
  }}
  .stat-card {{
    background: #fff; border-radius: 10px; padding: 18px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    border-left: 4px solid #2a5298;
  }}
  .stat-card.risk-high {{ border-left-color: #e74c3c; }}
  .stat-card.risk-mid {{ border-left-color: #f39c12; }}
  .stat-card.risk-low {{ border-left-color: #95a5a6; }}
  .stat-card.acc-strong {{ border-left-color: #16a085; }}
  .stat-card.acc-suspect {{ border-left-color: #3498db; }}
  .stat-value {{ font-size: 28px; font-weight: 700; color: #2c3e50; line-height: 1; }}
  .stat-label {{ font-size: 13px; color: #7f8c8d; margin-top: 6px; }}

  /* 维度统计 */
  .section-title {{
    font-size: 18px; font-weight: 600; margin: 24px 0 14px;
    padding-left: 12px; border-left: 4px solid #2a5298;
  }}
  .dim-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 10px; margin-bottom: 28px;
  }}
  .dim-card {{
    background: #fff; border-radius: 8px; padding: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    text-align: center; transition: transform 0.15s;
  }}
  .dim-card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
  .dim-card-accum {{ border-top: 3px solid #16a085; }}
  .dim-card-abnormal {{ border-top: 3px solid #e74c3c; }}
  .dim-code {{ font-size: 16px; font-weight: 700; color: #2c3e50; }}
  .dim-name {{ font-size: 13px; color: #555; margin: 4px 0; }}
  .dim-hit {{ font-size: 18px; font-weight: 600; color: #2a5298; }}
  .dim-kind {{ font-size: 11px; color: #95a5a6; margin-top: 4px; }}

  /* 过滤器 */
  .filters {{
    background: #fff; border-radius: 8px; padding: 14px 18px;
    margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
  }}
  .filters label {{ font-size: 13px; color: #555; }}
  .filters select, .filters input {{
    padding: 6px 10px; border: 1px solid #dfe4ea; border-radius: 4px;
    font-size: 13px; outline: none;
  }}
  .filters input[type="text"] {{ width: 220px; }}
  .filters input:focus, .filters select:focus {{ border-color: #2a5298; }}
  .filter-count {{ margin-left: auto; font-size: 13px; color: #7f8c8d; }}

  /* 表格 */
  table {{
    width: 100%; border-collapse: collapse;
    background: #fff; border-radius: 8px; overflow: hidden;
    box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    font-size: 13px;
  }}
  thead {{ background: #34495e; color: #fff; }}
  th {{ padding: 12px 10px; text-align: left; font-weight: 500; cursor: pointer; user-select: none; white-space: nowrap; }}
  th:hover {{ background: #2c3e50; }}
  th.sort-asc::after {{ content: ' ▲'; opacity: 0.7; }}
  th.sort-desc::after {{ content: ' ▼'; opacity: 0.7; }}
  td {{ padding: 10px; border-bottom: 1px solid #ecf0f1; vertical-align: top; }}
  tr:hover {{ background: #f8f9fa; }}
  .num {{ text-align: right; font-family: "Consolas", "Monaco", monospace; }}
  .muted {{ color: #95a5a6; font-size: 12px; }}
  .code-cell a {{ color: #2980b9; text-decoration: none; font-family: monospace; }}
  .code-cell a:hover {{ text-decoration: underline; }}
  .name-cell {{ min-width: 80px; }}
  .window-cell {{ font-family: monospace; font-size: 12px; min-width: 100px; }}
  .chips-cell {{ min-width: 240px; }}
  .peaks-cell {{ font-size: 12px; line-height: 1.6; min-width: 150px; color: #555; }}
  .case-cell {{ font-size: 12px; color: #7f8c8d; max-width: 160px; }}

  /* Chip */
  .chip {{
    display: inline-block; padding: 2px 8px; margin: 2px;
    border-radius: 11px; font-size: 11px; font-weight: 600;
  }}
  .chip-abnormal {{ background: #fee; color: #c0392b; border: 1px solid #fcc; }}
  .chip-accum {{ background: #e8f8f5; color: #117a65; border: 1px solid #a3e4d7; }}
  .chip-none {{ background: #ecf0f1; color: #95a5a6; }}

  /* Badge */
  .badge {{
    display: inline-block; padding: 3px 10px;
    border-radius: 4px; font-size: 12px; font-weight: 600;
  }}
  .risk-high {{ background: #e74c3c; color: #fff; }}
  .risk-mid {{ background: #f39c12; color: #fff; }}
  .risk-low {{ background: #bdc3c7; color: #fff; }}
  .acc-strong {{ background: #16a085; color: #fff; }}
  .acc-suspect {{ background: #3498db; color: #fff; }}

  .footer {{ text-align: center; margin-top: 30px; color: #95a5a6; font-size: 12px; }}
</style>
</head>
<body>
  <div class="header">
    <h1>📊 建仓期扫描报告 · Watchlist Scan Summary</h1>
    <div class="subtitle">基于 abnormal-trading-detection skill 14维度检测 · 100个证监会处罚案例</div>
  </div>

  <div class="container">
    <!-- 统计卡片 -->
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-value">{n_total}</div><div class="stat-label">扫描标的总数</div></div>
      <div class="stat-card risk-high"><div class="stat-value">{n_high}</div><div class="stat-label">高风险标的</div></div>
      <div class="stat-card risk-mid"><div class="stat-value">{n_mid}</div><div class="stat-label">中风险标的</div></div>
      <div class="stat-card risk-low"><div class="stat-value">{n_low}</div><div class="stat-label">低风险标的</div></div>
      <div class="stat-card acc-strong"><div class="stat-value">{n_strong}</div><div class="stat-label">建仓特征显著</div></div>
      <div class="stat-card acc-suspect"><div class="stat-value">{n_suspect}</div><div class="stat-label">疑似建仓</div></div>
    </div>

    <!-- 维度命中统计 -->
    <div class="section-title">14维度命中分布</div>
    <div class="dim-grid">
      {dim_cards_joined}
    </div>

    <!-- 过滤器 -->
    <div class="section-title">扫描结果明细</div>
    <div class="filters">
      <label>🔍 搜索:</label>
      <input type="text" id="searchInput" placeholder="股票名称/代码/案例ID">
      <label>风险等级:</label>
      <select id="riskFilter">
        <option value="">全部</option>
        <option value="高">高</option>
        <option value="中">中</option>
        <option value="低">低</option>
      </select>
      <label>建仓信号:</label>
      <select id="accFilter">
        <option value="">全部</option>
        <option value="建仓特征显著">建仓特征显著</option>
        <option value="疑似建仓">疑似建仓</option>
      </select>
      <span class="filter-count" id="filterCount">显示 {n_total} / {n_total} 条</span>
    </div>

    <table id="dataTable">
      <thead>
        <tr>
          <th data-sort="text">股票代码</th>
          <th data-sort="text">股票名称</th>
          <th data-sort="text">时间窗口</th>
          <th data-sort="num">告警天数</th>
          <th data-sort="num">告警次数</th>
          <th>触发维度</th>
          <th>峰值指标</th>
          <th>案例ID</th>
          <th data-sort="text">风险</th>
          <th data-sort="text">建仓信号</th>
        </tr>
      </thead>
      <tbody>
        {rows_joined}
      </tbody>
    </table>

    <div class="footer">
      <p>说明：D1-D7 为异常检测（🟥红色chip），D8-D14 为建仓期检测（🟩绿色chip）。本报告仅供研究参考，不构成投资建议。</p>
      <p>Generated by abnormal-trading-detection skill · 数据源：Tushare Pro</p>
    </div>
  </div>

<script>
const searchInput = document.getElementById('searchInput');
const riskFilter = document.getElementById('riskFilter');
const accFilter = document.getElementById('accFilter');
const filterCount = document.getElementById('filterCount');
const rows = Array.from(document.querySelectorAll('#dataTable tbody tr'));
const total = rows.length;

function applyFilter() {{
  const q = searchInput.value.trim().toLowerCase();
  const risk = riskFilter.value;
  const acc = accFilter.value;
  let visible = 0;
  rows.forEach(r => {{
    const text = r.innerText.toLowerCase();
    const rRisk = r.getAttribute('data-risk');
    const rAcc = r.getAttribute('data-acc');
    const matchQ = !q || text.includes(q);
    const matchR = !risk || rRisk === risk;
    const matchA = !acc || rAcc === acc;
    const show = matchQ && matchR && matchA;
    r.style.display = show ? '' : 'none';
    if (show) visible++;
  }});
  filterCount.textContent = `显示 ${{visible}} / ${{total}} 条`;
}}

searchInput.addEventListener('input', applyFilter);
riskFilter.addEventListener('change', applyFilter);
accFilter.addEventListener('change', applyFilter);

// 排序
document.querySelectorAll('th[data-sort]').forEach((th, idx) => {{
  th.addEventListener('click', () => {{
    const sortType = th.getAttribute('data-sort');
    const tbody = document.querySelector('#dataTable tbody');
    const all = Array.from(tbody.querySelectorAll('tr'));
    const asc = !th.classList.contains('sort-asc');
    document.querySelectorAll('th').forEach(t => t.classList.remove('sort-asc', 'sort-desc'));
    th.classList.add(asc ? 'sort-asc' : 'sort-desc');
    all.sort((a, b) => {{
      const av = a.children[idx].innerText.trim();
      const bv = b.children[idx].innerText.trim();
      if (sortType === 'num') {{
        return (asc ? 1 : -1) * (parseFloat(av) - parseFloat(bv));
      }}
      return (asc ? 1 : -1) * av.localeCompare(bv, 'zh');
    }});
    all.forEach(r => tbody.appendChild(r));
  }});
}});
</script>
</body>
</html>
"""
    return html


def main():
    df = pd.read_csv(INPUT_CSV, dtype={'ts_code': str})
    print(f'读取 {len(df)} 条记录')
    # 默认排序：按告警天数降序
    df = df.sort_values('total_alert_days', ascending=False).reset_index(drop=True)
    html = build_html(df)
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    size_kb = os.path.getsize(OUTPUT_HTML) / 1024
    print(f'✅ 已生成HTML报告: {OUTPUT_HTML} ({size_kb:.1f} KB)')


if __name__ == '__main__':
    main()
