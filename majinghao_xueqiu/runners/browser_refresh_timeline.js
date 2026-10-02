// ============================================================
// 增量更新 · 步骤A：刷新时间线并上传（粘贴到 browser_evaluate 的 script 参数）
// 作用: 拉取最新时间线第1页 → 上传为 refresh_{日期}.json → 返回本页帖子 id
// 效果: 上传后服务器 todo 接口自动算出"新增待抓"（断点协议天然支持增量）
// 注意: 1) 必须用 www.xueqiu.com（不带 www 会吃 WAF 挑战页返回 HTML）
//       2) 执行前先 browser_tabs({action:"activate", index:0})；
//          若报 WebView 错误，先 browser_navigate 打开
//          https://www.xueqiu.com/u/1301462220 过 WAF
//       3) 纯表达式体（块体箭头返回值会被引擎吞掉），勿手写 IIFE
// 产出: 逗号分隔的 id 串；与本地 posts 对比即知新增数量
// ============================================================
fetch('https://www.xueqiu.com/v4/statuses/user_timeline.json?user_id=1301462220&page=1')
  .then(r => r.text())
  .then(t => (t && t[0] === '{'
    ? fetch('http://127.0.0.1:18765/file?dir=timeline&name=refresh_YYYYMMDD.json',
            {method: 'POST', mode: 'no-cors', headers: {'Content-Type': 'text/plain'}, body: t})
        .then(() => (JSON.parse(t).statuses || []).map(s => s.id).join(','))
    : 'WAF挑战:' + t.slice(0, 60)))
// 使用说明: 把 refresh_YYYYMMDD.json 中的日期改为当天（如 refresh_20261002.json）
// 若时间跨度超过1页（20篇），把 page=1 改为 page=2 再跑一次、文件名加 _p2 后缀
