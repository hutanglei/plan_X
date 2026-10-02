#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雪球"马靖昊说会计"专栏全量抓取脚本（纯标准库，无第三方依赖）
用法:
  python3 scrape.py timeline [--max-pages N]   # 阶段1: 抓时间线分页
  python3 scrape.py posts [--limit N]           # 阶段2: 抓每篇文章详情
  python3 scrape.py export                      # 阶段3: 导出 Markdown + index.csv
  python3 scrape.py all [--max-pages N]         # 依次执行三个阶段
所有阶段均可断点续传（已存在的文件自动跳过）。
"""
import csv
import html
import json
import os
import random
import re
import sys
import time
import urllib.request
import http.cookiejar

USER_ID = "1301462220"
BASE = "https://xueqiu.com"
ROOT = os.path.dirname(os.path.abspath(__file__))
RAW_TIMELINE = os.path.join(ROOT, "raw", "timeline")
RAW_POSTS = os.path.join(ROOT, "raw", "posts")
MD_DIR = os.path.join(ROOT, "md")
COOKIE_FILE = os.path.join(ROOT, "cookies.txt")  # 浏览器提取的 cookie 字符串（一行）

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# 时间线接口候选（按优先级尝试，第一个能用的生效）
TIMELINE_ENDPOINTS = [
    "/statuses/original/timeline.json?user_id={uid}&page={page}",
    "/v4/statuses/user_timeline.json?user_id={uid}&page={page}",
    "/statuses/user_timeline.json?user_id={uid}&page={page}",
]

MAX_PAGE_LIMIT = 300  # 分页安全上限


class Scraper:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj))
        self.opener.addheaders = [
            ("User-Agent", UA),
            ("Accept", "application/json, text/plain, */*"),
            ("Accept-Language", "zh-CN,zh;q=0.9"),
        ]
        self.timeline_endpoint = None
        self.load_browser_cookies()
        self.bootstrap()

    def load_browser_cookies(self):
        """把 cookies.txt 里的浏览器 cookie 注入 cookiejar（不含 HttpOnly 的那些）"""
        if not os.path.exists(COOKIE_FILE):
            return
        with open(COOKIE_FILE, encoding="utf-8") as f:
            raw = f.read().strip()
        for pair in raw.split(";"):
            if "=" not in pair:
                continue
            name, _, value = pair.strip().partition("=")
            c = http.cookiejar.Cookie(
                version=0, name=name, value=value,
                port=None, port_specified=False,
                domain=".xueqiu.com", domain_specified=True, domain_initial_dot=True,
                path="/", path_specified=True,
                secure=False, expires=None, discard=True,
                comment=None, comment_url=None, rest={}, rfc2109=False)
            self.cj.set_cookie(c)
        print(f"[cookie] 注入浏览器 cookie {len(self.cj)} 个", flush=True)

    def bootstrap(self):
        """访问首页：换取/刷新 acw_tc，并触发服务端下发 xq_a_token 等"""
        req = urllib.request.Request(BASE + "/", headers={"Referer": BASE})
        resp = self.opener.open(req, timeout=30)
        body = resp.read().decode("utf-8", "ignore")
        names = [c.name for c in self.cj]
        has_token = any(n in ("xq_a_token", "xqat") for n in names)
        print(f"[cookie] 首页访问完成: {len(body)} 字节, cookie: {', '.join(names)}", flush=True)
        return names

    def get_json(self, url, referer=BASE, retries=4):
        """GET 并解析 JSON；失败时自动重新 bootstrap cookie 再重试"""
        last_err = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers={"Referer": referer})
                with self.opener.open(req, timeout=30) as resp:
                    data = resp.read().decode("utf-8")
                return json.loads(data)
            except Exception as e:  # noqa: BLE001
                last_err = e
                wait = 3 * (attempt + 1)
                print(f"  [重试{attempt + 1}] {e}，{wait}s 后重试并刷新cookie", flush=True)
                time.sleep(wait)
                try:
                    self.bootstrap()
                except Exception as e2:  # noqa: BLE001
                    print(f"  [bootstrap失败] {e2}", flush=True)
        raise RuntimeError(f"请求最终失败: {url} -> {last_err}")

    def pick_endpoint(self):
        """探测可用的时间线接口"""
        for ep in TIMELINE_ENDPOINTS:
            url = BASE + ep.format(uid=USER_ID, page=1)
            try:
                resp = self.get_json(url)
                items = extract_items(resp)
                if items:
                    self.timeline_endpoint = ep
                    print(f"[接口] 使用 {ep}，第1页 {len(items)} 条", flush=True)
                    return resp
            except Exception as e:  # noqa: BLE001
                print(f"[接口] {ep} 不可用: {e}", flush=True)
        raise RuntimeError("所有时间线接口均不可用")

    def fetch_page(self, page):
        """抓取一页时间线，返回 item 列表"""
        if self.timeline_endpoint is None:
            resp = self.pick_endpoint()
            if page == 1:
                return extract_items(resp)
        url = BASE + self.timeline_endpoint.format(uid=USER_ID, page=page)
        resp = self.get_json(url, referer=BASE + "/u/" + USER_ID)
        return extract_items(resp)


def extract_items(resp):
    """兼容不同接口的响应结构，抽出帖子列表"""
    if resp is None:
        return []
    if isinstance(resp, list):
        return resp
    for key in ("items", "statuses", "list"):
        v = resp.get(key)
        if isinstance(v, list):
            return v
    return []


def phase_timeline(scraper, max_pages=None):
    """阶段1: 分页抓时间线，存 raw/timeline/page_XXXX.json"""
    os.makedirs(RAW_TIMELINE, exist_ok=True)
    seen_ids = set()
    # 先加载已有页面，支持续传
    for fn in sorted(os.listdir(RAW_TIMELINE)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(RAW_TIMELINE, fn), encoding="utf-8") as f:
            for item in extract_items(json.load(f)):
                if isinstance(item, dict) and item.get("id"):
                    seen_ids.add(item["id"])
    print(f"[timeline] 续传起点: 已有 {len(seen_ids)} 条", flush=True)

    page = 1
    empty_streak = 0
    while page <= MAX_PAGE_LIMIT:
        if max_pages and page > max_pages:
            break
        path = os.path.join(RAW_TIMELINE, f"page_{page:04d}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                items = extract_items(json.load(f))
        else:
            try:
                items = scraper.fetch_page(page)
            except RuntimeError as e:
                print(f"[timeline] 第 {page} 页失败: {e}", flush=True)
                break
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"page": page, "items": items}, f, ensure_ascii=False)
            time.sleep(random.uniform(0.6, 1.2))

        new_ids = {i.get("id") for i in items if isinstance(i, dict) and i.get("id")}
        fresh = new_ids - seen_ids
        seen_ids |= new_ids
        print(f"[timeline] 第 {page} 页: {len(items)} 条, 新增 {len(fresh)}, 累计 {len(seen_ids)}", flush=True)

        if not items:
            empty_streak += 1
            if empty_streak >= 2:
                print("[timeline] 连续空页，结束", flush=True)
                break
        else:
            empty_streak = 0
            if not fresh:
                # 整页都是重复，说明已到底
                print("[timeline] 整页无新增，判定到底，结束", flush=True)
                break
        page += 1
    print(f"[timeline] 完成，共 {len(seen_ids)} 条独立帖子", flush=True)
    return seen_ids


def collect_post_ids():
    """从 raw/timeline 汇总所有帖子 id，并带上元数据"""
    meta = {}
    for fn in sorted(os.listdir(RAW_TIMELINE)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(RAW_TIMELINE, fn), encoding="utf-8") as f:
            for item in extract_items(json.load(f)):
                if not (isinstance(item, dict) and item.get("id")):
                    continue
                pid = item["id"]
                if pid not in meta:
                    meta[pid] = {
                        "id": pid,
                        "title": (item.get("title") or "").strip(),
                        "created_at": item.get("created_at"),
                        "target": item.get("target") or f"/{USER_ID}/{pid}",
                    }
    return meta


def phase_posts(scraper, limit=None):
    """阶段2: 抓每篇帖子详情，存 raw/posts/{id}.json"""
    os.makedirs(RAW_POSTS, exist_ok=True)
    meta = collect_post_ids()
    ids = sorted(meta.keys(), reverse=True)  # 从新到旧抓
    if limit:
        ids = ids[:limit]
    done = 0
    t0 = time.time()
    for idx, pid in enumerate(ids, 1):
        path = os.path.join(RAW_POSTS, f"{pid}.json")
        if os.path.exists(path):
            done += 1
            continue
        try:
            detail = scraper.get_json(
                BASE + "/statuses/show.json?id=" + str(pid),
                referer=BASE + meta[pid]["target"])
        except RuntimeError as e:
            print(f"[posts] {pid} 失败: {e}", flush=True)
            time.sleep(2)
            continue
        if isinstance(detail, dict) and "status" in detail and isinstance(detail["status"], dict):
            detail = detail["status"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False)
        done += 1
        if idx % 20 == 0:
            rate = idx / (time.time() - t0 + 1e-9)
            remain = (len(ids) - idx) / max(rate, 0.01)
            print(f"[posts] {idx}/{len(ids)}（速率 {rate:.1f} 篇/s，剩余约 {remain / 60:.1f} 分钟）", flush=True)
        time.sleep(random.uniform(0.5, 1.0))
    print(f"[posts] 完成，本地已有详情 {done} 篇", flush=True)


def clean_html(h):
    """把雪球的 HTML 文本转成纯文本"""
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


def phase_export():
    """阶段3: 导出 Markdown 和 index.csv"""
    os.makedirs(MD_DIR, exist_ok=True)
    rows = []
    for fn in sorted(os.listdir(RAW_POSTS)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(RAW_POSTS, fn), encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not d.get("id"):
            continue
        pid = d["id"]
        date = ts_to_date(d.get("created_at"))
        title = (d.get("title") or "").strip()
        body = clean_html(d.get("text") or "")
        if not title:
            title = safe_filename(body.split("\n")[0] if body else "")
        url = BASE + (d.get("target") or f"/{USER_ID}/{pid}")
        md_name = f"{date}_{pid}_{safe_filename(title)}.md"
        md_path = os.path.join(MD_DIR, md_name)
        stats = (
            f"---\nid: {pid}\ndate: {date}\ntitle: {title}\nurl: {url}\n"
            f"views: {d.get('view_count', '')}\nlikes: {d.get('like_count', '')}\n"
            f"comments: {d.get('reply_count', '')}\nforwards: {d.get('retweet_count', '')}\n"
            f"type: {d.get('type', '')}\n---\n\n# {title}\n\n"
        )
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(stats + body + "\n")
        rows.append({
            "id": pid, "date": date, "title": title, "url": url,
            "views": d.get("view_count", ""), "likes": d.get("like_count", ""),
            "comments": d.get("reply_count", ""), "forwards": d.get("retweet_count", ""),
            "md_file": md_name,
        })
    rows.sort(key=lambda r: (r["date"], str(r["id"])), reverse=True)
    with open(os.path.join(ROOT, "index.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "date", "title", "url", "views",
                                          "likes", "comments", "forwards", "md_file"])
        w.writeheader()
        w.writerows(rows)
    print(f"[export] 导出 {len(rows)} 篇 Markdown 到 md/，索引 index.csv", flush=True)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    cmd = args[0]
    max_pages = None
    limit = None
    if "--max-pages" in args:
        max_pages = int(args[args.index("--max-pages") + 1])
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])

    scraper = Scraper()
    if cmd == "timeline":
        phase_timeline(scraper, max_pages)
    elif cmd == "posts":
        phase_posts(scraper, limit)
    elif cmd == "export":
        phase_export()
    elif cmd == "all":
        phase_timeline(scraper, max_pages)
        phase_posts(scraper)
        phase_export()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
