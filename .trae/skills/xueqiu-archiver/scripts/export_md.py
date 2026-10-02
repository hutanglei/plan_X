#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 raw/posts/*.json 导出为 Markdown + index.csv
用法: python3 export_md.py <raw_posts目录> <md输出目录> [user_id]
"""
import csv
import html
import json
import os
import re
import sys
import time

raw_posts = sys.argv[1]
md_dir = sys.argv[2]
uid = sys.argv[3] if len(sys.argv) > 3 else ""
BASE = "https://xueqiu.com"
os.makedirs(md_dir, exist_ok=True)


def clean_html(h):
    if not h:
        return ""
    t = h.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    t = re.sub(r"<script.*?</script>", "", t, flags=re.S | re.I)
    t = re.sub(r"<style.*?</style>", "", t, flags=re.S | re.I)
    t = re.sub(r"</p>\s*", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def ts_to_date(ts):
    if not ts:
        return "unknown"
    try:
        return time.strftime("%Y-%m-%d", time.localtime(ts / 1000.0))
    except Exception:  # noqa: BLE001
        return "unknown"


def safe_filename(s, maxlen=40):
    s = re.sub(r"[\\/:*?\"<>|\n\r\t]", "", s)
    return s[:maxlen].strip() or "untitled"


rows = []
for fn in sorted(os.listdir(raw_posts)):
    if not fn.endswith(".json"):
        continue
    with open(os.path.join(raw_posts, fn), encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict) or not d.get("id"):
        continue
    pid = d["id"]
    date = ts_to_date(d.get("created_at"))
    title = (d.get("title") or "").strip()
    body = clean_html(d.get("text") or "")
    if not title:
        title = safe_filename(body.split("\n")[0] if body else "")
    url = BASE + (d.get("target") or (f"/{uid}/{pid}" if uid else f"/status/{pid}"))
    md_name = f"{date}_{pid}_{safe_filename(title)}.md"
    with open(os.path.join(md_dir, md_name), "w", encoding="utf-8") as f:
        f.write(
            f"---\nid: {pid}\ndate: {date}\ntitle: {title}\nurl: {url}\n"
            f"views: {d.get('view_count', '')}\nlikes: {d.get('like_count', '')}\n"
            f"comments: {d.get('reply_count', '')}\nforwards: {d.get('retweet_count', '')}\n"
            f"type: {d.get('type', '')}\n---\n\n# {title}\n\n" + body + "\n")
    rows.append({"id": pid, "date": date, "title": title, "url": url,
                 "views": d.get("view_count", ""), "likes": d.get("like_count", ""),
                 "comments": d.get("reply_count", ""), "forwards": d.get("retweet_count", ""),
                 "md_file": md_name})

rows.sort(key=lambda r: (r["date"], str(r["id"])), reverse=True)
with open(os.path.join(os.path.dirname(os.path.abspath(md_dir.rstrip("/"))) or ".", "index.csv"),
          "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["id", "date", "title", "url", "views",
                                      "likes", "comments", "forwards", "md_file"])
    w.writeheader()
    w.writerows(rows)
print(f"[export] 导出 {len(rows)} 篇 Markdown，索引 index.csv")
