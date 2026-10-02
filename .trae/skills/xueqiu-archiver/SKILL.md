---
name: xueqiu-archiver
description: "Archives a Xueqiu (雪球) user's full post history via browser-side fetch (bypasses Aliyun WAF), local upload server and stateless resumable rounds. Invoke when scraping xueqiu.com user timelines/articles or resuming an interrupted scrape."
---

# 雪球用户文章全量存档（Xueqiu Archiver）

把一个雪球用户的全部帖子（时间线+全文）存档为本地 Markdown。核心是**浏览器当抓取引擎 + 本地服务器落盘 + 无状态断点续传**三件套。

## 一、为什么必须这样抓（原理）

1. **雪球在阿里云 WAF 后面**：纯 HTTP 客户端（requests/urllib/curl）第一个请求就收到 JS 挑战页（`<textarea id="renderData">{"_waf_xxx":...}`），没有 JS 引擎就过不去
2. **浏览器 cookie 移植不可行**：`document.cookie` 只能拿到 `ssxmod_itna` 等，而它与 **HttpOnly** 的 `acw_tc` 绑定校验，拼上服务端新发的 `acw_tc` 也无效（实测 405/挂起）
3. **正解**：让真实浏览器页面自己跑 `fetch()`——同源请求自动带全量 cookie（含 HttpOnly），页面自带的 JS 还会自动给 xueqiu.com 请求追加 `md5__1038` 签名参数（无需自己算）
4. **数据通道**：浏览器 `fetch('http://127.0.0.1:PORT/upload', {mode:'no-cors'})` 把 JSON POST 给本地服务器直接写盘——**数据不过 LLM 上下文**。HTTPS 页面可以请求 `http://127.0.0.1`（localhost 属 "potentially trustworthy origin"，不算混合内容）；`mode:'no-cors'` + `Content-Type: text/plain` 是简单请求免预检，响应不可读但无需读

## 二、API 清单

| 用途 | URL | 说明 |
|---|---|---|
| 时间线 | `https://xueqiu.com/v4/statuses/user_timeline.json?user_id={uid}&page={n}` | 返回 `{total, maxPage, statuses[]}`，每页 15~20 条；**maxPage 即总页数** |
| 帖子详情 | `https://xueqiu.com/statuses/show.json?id={id}` | 完整帖子，`text` 字段为全文 HTML |
| ~~原发时间线~~ | `/statuses/original/timeline.json` | **已退化**：只返回"专栏"文章（多数用户 0~1 篇），别用 |
| 用户 uid | 从个人主页 URL `xueqiu.com/u/{uid}` 取 | |

## 三、部署流程

```
工作目录/
├── scripts/upload_server.py    # 本地接收服务器（含动态 todo 接口）
├── scripts/build_todo.py       # 从 raw/timeline 提取全部 id、生成待抓清单（核对用）
├── scripts/export_md.py        # raw/posts/*.json → md/*.md + index.csv
└── raw/{timeline,posts,meta}/  # 服务器自动创建
```

1. `python3 scripts/upload_server.py 18765`（后台常驻）
2. 浏览器打开 `https://xueqiu.com/u/{uid}`（真实浏览器自动过 WAF）
3. 用 `browser_tabs list` 拿到 viewId；先 `fetch()` 探一下 v4 接口拿 `maxPage`
4. 跑时间线分页（`scripts/browser_runners.js` 脚本1），每轮 10 页、350ms 间隔，直到 nextPage > maxPage
5. 跑详情轮次（`scripts/browser_runners.js` 脚本2），每轮 ~45 篇、650ms 间隔；完成判据：`curl "http://127.0.0.1:18765/file?todo=1&limit=3"` 返回 `[]`
6. `python3 scripts/build_todo.py` 核对（应显示待抓 0 或仅剩永久失败项）
7. `python3 scripts/export_md.py raw/posts md {uid}` 导出 Markdown + 索引

## 四、browser_evaluate 引擎的坑（血泪清单）

1. **顶层 `await` 不可用**（返回 undefined）→ 只用 Promise 链
2. **块体箭头函数 `() => { return x }` 的返回值会被引擎吞掉**（副作用正常执行）→ 需要返回值的一律用**表达式体**：`(副作用, 返回值)` 逗号运算符；无需返回值的块体（如 `.catch(e => { list.push(x); })`）可以用
3. 多语句脚本 OK，**完成值 = 最后一个表达式**；返回 Promise 会被引擎正确 await
4. **绝不手写 IIFE**（引擎自动包裹，双层包裹返回 undefined）
5. 手工双层 JSON 转义极易错 → **在 V8 沙箱里 `array.join("\n")` 构造脚本 + `new Function()` 语法检查后再 evaluate**（曾靠它抓出少一个括号的 bug）
6. `acc.then(...)` 长链收尾括号：sleep 行 `.then(() => new Promise(r => setTimeout(r, N)))` 自身平衡后，**还要补一个 `)` 关 `acc.then(`**，然后 `, Promise.resolve())`（用 `.replace()` 在 join 后补，见 runners 注释）
7. 每个 evaluate 有 **~60s IDE 超时** → 轮次内置时间预算（38~42s）自动截断，下轮续跑
8. **fire-and-forget 后台循环会被引擎回收**（只跑了 20 多条就停）→ 别指望脚本自治跑完，靠外部一轮轮驱动
9. **WebView 空闲会卸载**（报 "WebView must be attached to the DOM"）→ **每次 evaluate 前先 `browser_tabs({action:"activate", index:0})`**
10. 页面可能随卸载/激活被**重载，window 状态清零** → 断点信息一律放服务器侧（todo 接口），浏览器保持无状态
11. 超时被杀的 evaluate，其页内链条**有时仍在继续并默默上传**（多次实测多出几十上百个文件）→ **判进度以磁盘文件数为准**，且警惕失控链：一旦发现 `bad` 计数狂涨，立即 `window.__todo=null; window.__bg.running=false` 杀链，冷却后再来

## 五、限速与风控（实测数据）

| 接口 | 触发条件 | 表现 | 对策 |
|---|---|---|---|
| show.json（详情） | >2/s 持续 ~200-300 次 | 405 HTML 错误页，或 fetch 直接挂起 | **650ms 间隔（≈1.5/s）可跑完千级总量**；触发后冷却 ~10 分钟 |
| v4 timeline | 150ms 间隔跑了 ~45 页后被限 | 单页挂起 60s+ | 350ms+ 间隔、每轮 ≤10 页 |
| 冷却方法 | 停止一切请求，终端 `sleep 150` 以上再单发探测，返回 200 即恢复 | | |

## 六、断点续传协议（架构核心）

1. 服务器 GET `/file?todo=1&limit=N`：实时扫描 `raw/timeline/*.json` 提取全部帖子 id（有序去重）− `raw/posts/` 已有文件 → 返回前 N 个待抓 id
2. 浏览器每轮 fetch 该接口拿下一批 → 逐个抓详情落盘 → **文件落地即自动从 todo 消失**
3. 因此页面重载、断线、限速、会话丢失**全部无感恢复**：重新打开页面继续跑轮次即可
4. 已知口径：`total`/`maxPage` 含隐藏帖，实际可下载 < total（本案例 1629 → 1145 可下载）；空页（`statuses: []`）属正常

## 七、导出与数据口径

- `export_md.py`：`text` 字段做 HTML→纯文本清洗；文件名 `{date}_{id}_{title}.md`；frontmatter 含 views/likes/comments/forwards/type；`index.csv` 全量索引
- 雪球帖子含转引互动（读者提问+马老师回答的问答体），是相比公众号源的独特价值
