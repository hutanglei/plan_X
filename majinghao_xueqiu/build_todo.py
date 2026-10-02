#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 raw/timeline 提取所有帖子 id，减去已下载的，生成待抓清单 raw/meta/todo.json"""
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
TL = os.path.join(ROOT, "raw", "timeline")
POSTS = os.path.join(ROOT, "raw", "posts")
META = os.path.join(ROOT, "raw", "meta")
os.makedirs(META, exist_ok=True)

ids = []
seen = set()
for fn in sorted(os.listdir(TL)):
    if not fn.endswith(".json"):
        continue
    with open(os.path.join(TL, fn), encoding="utf-8") as f:
        j = json.load(f)
    for x in j.get("statuses") or []:
        if isinstance(x, dict) and x.get("id") and x["id"] not in seen:
            seen.add(x["id"])
            ids.append(x["id"])

todo = [i for i in ids if not os.path.exists(os.path.join(POSTS, f"{i}.json"))]
with open(os.path.join(META, "todo.json"), "w", encoding="utf-8") as f:
    json.dump(todo, f)
print(f"时间线页数: {len([f for f in os.listdir(TL) if f.endswith('.json')])}")
print(f"总帖子: {len(ids)}  已下载: {len(ids) - len(todo)}  待抓: {len(todo)}")
