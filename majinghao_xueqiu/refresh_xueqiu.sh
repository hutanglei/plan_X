#!/bin/bash
# ============================================================
# 马靖昊雪球存档 · 增量更新编排脚本（shell 侧）
# 浏览器侧 JS 见 runners/ 目录，操作手册见 xueqiu-archiver SKILL.md 第八节
#
# 用法:
#   ./refresh_xueqiu.sh status   # 状态总览（服务/待抓/存档水位/最新文章）
#   ./refresh_xueqiu.sh serve    # 确保上传服务器运行（未运行则启动）
#   ./refresh_xueqiu.sh export   # 抓取完成后：导出 md + 展示本次新增清单
# ============================================================
set -euo pipefail

PORT=18765
XQ_UID=1301462220
BASE="$(cd "$(dirname "$0")" && pwd)"
SERVER="$BASE/upload_server.py"
EXPORT="/Users/lnz/Documents/code-h/plan_X/.trae/skills/xueqiu-archiver/scripts/export_md.py"
META="$BASE/raw/meta/last_refresh.json"

server_pid() { lsof -tiTCP:$PORT -sTCP:LISTEN 2>/dev/null || true; }

cmd_status() {
  echo "=== 雪球存档状态 ==="
  local pid; pid=$(server_pid)
  if [[ -n "$pid" ]]; then
    echo "上传服务器: 运行中 (PID $pid, 端口 $PORT)"
  else
    echo "上传服务器: 未运行（先执行 ./refresh_xueqiu.sh serve）"
  fi
  echo "posts 落盘: $(ls "$BASE"/raw/posts/*.json 2>/dev/null | wc -l | tr -d ' ') 篇"
  echo "md 导出:   $(ls "$BASE"/md/*.md 2>/dev/null | wc -l | tr -d ' ') 篇"
  if [[ -n "$pid" ]]; then
    local todo; todo=$(curl -s "http://127.0.0.1:$PORT/file?todo=1&limit=200" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)))' 2>/dev/null || echo "?")
    echo "待抓清单:  $todo 篇（>0 则需跑浏览器抓取轮次）"
  fi
  python3 - "$BASE/index.csv" <<'EOF'
import csv, sys
try:
    rows = list(csv.DictReader(open(sys.argv[1])))
    if rows:
        dates = sorted(r["date"] for r in rows)
        latest = max(rows, key=lambda r: r["date"])
        print(f"存档水位:  {len(rows)} 篇，{dates[0]} ~ {dates[-1]}")
        print(f"最新一篇:  {latest['date']} {latest['title'][:40]}")
except Exception as e:
    print(f"index.csv 读取失败: {e}")
EOF
}

cmd_serve() {
  if [[ -n "$(server_pid)" ]]; then
    echo "上传服务器已在运行 (PID $(server_pid))"
    return
  fi
  cd "$BASE"
  nohup python3 "$SERVER" $PORT > /tmp/xq_upload_server.log 2>&1 &
  sleep 1
  if [[ -n "$(server_pid)" ]]; then
    echo "上传服务器已启动 (PID $(server_pid)，日志 /tmp/xq_upload_server.log)"
  else
    echo "启动失败，查看 /tmp/xq_upload_server.log"; exit 1
  fi
}

cmd_export() {
  echo "=== 导出 Markdown ==="
  python3 "$EXPORT" "$BASE/raw/posts" "$BASE/md" $XQ_UID | tail -1
  python3 - "$BASE/index.csv" "$META" <<'EOF'
import csv, json, os, sys
idx, meta = sys.argv[1], sys.argv[2]
rows = list(csv.DictReader(open(idx)))
last = {}
if os.path.exists(meta):
    last = json.load(open(meta))
cur_ids = set(int(r["\ufeffid"] if "\ufeffid" in r else r["id"]) for r in rows)
prev_ids = set(last.get("ids", [])) if last else cur_ids  # 首次运行：以当前为基线
new_ids = cur_ids - prev_ids
new_rows = [r for r in rows if int(r["\ufeffid"] if "\ufeffid" in r else r["id"]) in new_ids]
new_rows.sort(key=lambda r: r["date"])
print(f"index 总量: {len(rows)} 篇 | 本次新增: {len(new_rows)} 篇")
for r in new_rows:
    print(f"  + {r['date']}  {r['title'][:44]}")
json.dump({"count": len(rows), "ids": sorted(cur_ids)}, open(meta, "w"))
print(f"水位已记录 → {meta}")
EOF
  echo "提示: 新增文章如需入知识库，参照 v4/v5 流程（子代理提取 → 批量脚本 → 三重校验）"
}

case "${1:-status}" in
  status) cmd_status ;;
  serve)  cmd_serve ;;
  export) cmd_export ;;
  *) echo "用法: $0 {status|serve|export}"; exit 1 ;;
esac
