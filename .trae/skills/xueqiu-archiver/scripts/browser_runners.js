// 浏览器侧抓取脚本（在 integrated_code_mode 的 Exec 沙箱里构造并执行）
// 关键约束见 SKILL.md「browser_evaluate 引擎的坑」：
//  - 不能用顶层 await；块体箭头函数的 return 值会被吞 → 全部用表达式体+逗号运算符
//  - 不能手写 IIFE；脚本多语句 OK，完成值 = 最后一个表达式（Promise 会被引擎 await）
//  - 先用 new Function() 做语法检查再 evaluate（双层转义极易少括号）
//  - acc.then 链收尾：sleep 行末要补一个 ")" 关 acc.then(，再 ", Promise.resolve())"
//  - 时间预算 38~42s（evaluate 的 IDE 超时约 60s）

// ========== 脚本1：时间线分页（改 UID / 起始页 / 每轮页数后使用） ==========
const timelineChunk = [
  "window.__xq = window.__xq || { page: 1, ids: [], seen: {}, fails: [] };",
  "const S = window.__xq;",
  "const start = S.page;",
  "const end = Math.min(start + 10, 83);",        // 83 = maxPage+1；每轮10页
  "const range = [];",
  "for (let p = start; p < end; p++) range.push(p);",
  "range.reduce((acc, p) => acc.then(() =>",
  "  fetch('https://xueqiu.com/v4/statuses/user_timeline.json?user_id=1301462220&page=' + p)",
  "    .then(r => r.json())",
  "    .then(j => (((j.statuses || []).forEach(x => { if (x && x.id && !S.seen[x.id]) { S.seen[x.id] = 1; S.ids.push({ id: x.id, target: x.target }); } }), j)))",
  "    .then(j => fetch('http://127.0.0.1:18765/upload?dir=timeline&name=page_' + String(p).padStart(4, '0') + '.json', { method: 'POST', mode: 'no-cors', headers: { 'Content-Type': 'text/plain' }, body: JSON.stringify(j) }))",
  "    .catch(e => { S.fails.push('page' + p + ':' + e.message); })",
  "    .then(() => new Promise(r => setTimeout(r, 350)))",   // 时间线接口敏感，350ms+
  ", Promise.resolve()).then(() => (S.page = end, JSON.stringify({ nextPage: S.page, ids: S.ids.length, fails: S.fails.length })))"
].join("\n").replace("setTimeout(r, 350)))", "setTimeout(r, 350))))");
// 注意上面 .replace：join 后 sleep 行是 "setTimeout(r, 350)))"，补一个 ")" 关 acc.then(

// ========== 脚本2：详情无状态轮次（服务器动态算 todo，页面重载免疫） ==========
const statelessRunner = [
  "window.__bad = window.__bad || {}; window.__t0 = Date.now(); window.__pc = window.__pc || 0;",
  "fetch('http://127.0.0.1:18765/file?todo=1&limit=60')",
  "  .then(r => r.json())",
  "  .then(list => list.reduce((acc, id) => acc.then(() => (window.__bad[id] || Date.now() - window.__t0 > 42000 ? 0 :",
  "    fetch('https://xueqiu.com/statuses/show.json?id=' + id)",
  "      .then(r => r.json())",
  "      .then(d => (d && d.id) ? fetch('http://127.0.0.1:18765/upload?dir=posts&name=' + d.id + '.json', { method: 'POST', mode: 'no-cors', headers: { 'Content-Type': 'text/plain' }, body: JSON.stringify(d) }).then(() => { window.__pc = window.__pc + 1; }) : Promise.reject(new Error('nodata')))",
  "      .catch(e => { window.__bad[id] = 1; })",
  "      .then(() => new Promise(r => setTimeout(r, 650)))",   // 详情接口 650ms ≈ 1.5/s，可持续
  ", Promise.resolve()))",
  "  .then(() => JSON.stringify({ ok: window.__pc, bad: Object.keys(window.__bad).length, ms: Date.now() - window.__t0 }))"
].join("\n").replace("setTimeout(r, 650)))", "setTimeout(r, 650)))))");
// 同样的补括号技巧

// ========== 在 V8 沙箱里的执行方式 ==========
// 每轮执行前：先激活标签页（WebView 空闲会卸载），再语法检查，再 evaluate：
//
//   await tools.browser_tabs({ action: "activate", index: 0 });
//   await tools.browser_wait_for({ time: 1 });
//   try { new Function(statelessRunner); } catch (e) { /* 语法错误，先修 */ }
//   const r = await tools.browser_evaluate({ viewId: "<目标标签viewId>", script: statelessRunner });
//   text(r.content[0].text);   // {"ok":46,"bad":0,"ms":42544} —— ok累计/失败/耗时
//
// 完成判据：curl "http://127.0.0.1:18765/file?todo=1&limit=3" 返回 []
