#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地接收服务器：接收浏览器 evaluate 上传的数据并写入 raw/ 目录
用法: python3 upload_server.py [端口]
"""
import http.server
import json
import os
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
TL_DIR = os.path.join(ROOT, "raw", "timeline")
POSTS_DIR = os.path.join(ROOT, "raw", "posts")
os.makedirs(TL_DIR, exist_ok=True)
os.makedirs(POSTS_DIR, exist_ok=True)
os.makedirs(os.path.join(ROOT, "raw", "meta"), exist_ok=True)

ALLOWED_DIRS = {"timeline", "posts", "meta"}

# 时间线 id 列表缓存（含目录签名，文件变化时自动失效）
_tl_cache = {"sig": None, "ids": []}


def timeline_ids():
    sig = tuple((f, os.path.getmtime(os.path.join(TL_DIR, f)))
                for f in sorted(os.listdir(TL_DIR)) if f.endswith(".json"))
    if _tl_cache["sig"] == sig:
        return _tl_cache["ids"]
    ids = []
    seen = set()
    for fn in sorted(os.listdir(TL_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(TL_DIR, fn), encoding="utf-8") as f:
                j = json.load(f)
        except Exception:  # noqa: BLE001
            continue
        for x in j.get("statuses") or []:
            if isinstance(x, dict) and x.get("id") and x["id"] not in seen:
                seen.add(x["id"])
                ids.append(x["id"])
    _tl_cache["sig"] = sig
    _tl_cache["ids"] = ids
    return ids


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        q = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(q.query)
        sub = params.get("dir", ["timeline"])[0]
        name = os.path.basename(params.get("name", ["unnamed"])[0])
        if sub not in ALLOWED_DIRS:
            sub = "timeline"
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        path = os.path.join(ROOT, "raw", sub, name)
        with open(path, "wb") as f:
            f.write(body)
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b"ok")
        sys.stdout.write(f"[{sub}] {name} {len(body)}B\n")
        sys.stdout.flush()

    def do_GET(self):
        """GET /file?todo=1&limit=N → 动态返回下一批待抓帖子 id；否则按 dir+name 读文件"""
        q = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(q.query)
        if "todo" in params:
            limit = min(int(params.get("limit", ["25"])[0]), 200)
            ids = [i for i in timeline_ids()
                   if not os.path.exists(os.path.join(POSTS_DIR, f"{i}.json"))]
            data = json.dumps(ids[:limit]).encode()
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
            sys.stdout.write(f"[todo] 剩余{len(ids)} 发{len(ids[:limit])}个\n")
            sys.stdout.flush()
            return
        sub = params.get("dir", ["meta"])[0]
        name = os.path.basename(params.get("name", [""])[0])
        if sub not in ALLOWED_DIRS or not name:
            self.send_response(404)
            self.end_headers()
            return
        path = os.path.join(ROOT, "raw", sub, name)
        if not os.path.isfile(path):
            self.send_response(404)
            self.end_headers()
            return
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # 静默默认日志
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18765
    print(f"upload server on http://127.0.0.1:{port}", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
