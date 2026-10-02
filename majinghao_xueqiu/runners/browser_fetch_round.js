// ============================================================
// 增量更新 · 步骤B：标准抓取轮次（粘贴到 browser_evaluate 的 script 参数）
// 作用: 从本地 todo 接口取 25 个待抓 id → 逐个拉详情 → 上传落盘
// 配额纪律（实测）: WAF 配额约 25 请求/分钟窗口
//   · 每轮 limit=25，轮内 650ms 间隔（一轮约 22s）
//   · 轮间冷却 ≥75s（不冷却则整轮 0 命中）
//   · 单发请求几乎总能成功——失败是瞬时的，下轮自动重试（断点协议）
// 返回: {"ok":N,"bad":N,"fetched_n":N}；bad 偶发属正常，todo 未消化的下轮重抓
// 完成判据: ./refresh_xueqiu.sh status 显示"待抓清单: 0 篇"
// ============================================================
var log = {ok: 0, bad: 0, n: 0};
fetch('http://127.0.0.1:18765/file?todo=1&limit=25')
  .then(r => r.json())
  .catch(e => [])
  .then(ids => (log.n = ids.length, ids.reduce((acc, id) => acc.then(() => fetch('https://www.xueqiu.com/statuses/show.json?id=' + id)
    .then(r => r.text())
    .then(t => (t && t[0] === '{'
      ? fetch('http://127.0.0.1:18765/file?dir=posts&name=' + id + '.json',
              {method: 'POST', mode: 'no-cors', headers: {'Content-Type': 'text/plain'}, body: t})
          .then(() => { log.ok++; }, () => { log.bad++; })
      : (log.bad++, null)))
    .catch(() => { log.bad++; })
    .then(() => new Promise(res => setTimeout(res, 650)))), Promise.resolve())))
  .then(() => JSON.stringify({ok: log.ok, bad: log.bad, fetched_n: log.n}))
// 注意: 此脚本是配平过的括号（尾部长度 4+3）；改动三元/then 层级后务必重新数括号，
// 并可先在 Exec 沙箱里 new Function(script) 做语法检查再 evaluate
