/* AI缠论 chan.js — 应用逻辑（无外部 JS 依赖） */
(function () {
"use strict";
function $(id) { return document.getElementById(id); }
var DASH = "—";

/* ============ 0. 工具 ============ */
function mktOf(code) {
  code = String(code).trim();
  if (/^(sh|sz|bj)/i.test(code)) return code.slice(0, 2).toLowerCase();
  var c = code.charAt(0);
  if (c === "6" || c === "5" || c === "9") return "sh";
  if (c === "4" || c === "8") return "bj";
  return "sz";
}
function marketCode(code) { code = String(code).trim().toLowerCase(); return /^(sh|sz|bj)/.test(code) ? code : mktOf(code) + code; }
function sinaSym(code) { return marketCode(code); }
function px2(v) { return (v === null || v === undefined || isNaN(v)) ? DASH : Number(v).toFixed(2); }
function pct2(v) { return (v === null || v === undefined || isNaN(v)) ? DASH : (v >= 0 ? "+" : "") + v.toFixed(2) + "%"; }
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
function bjDate(ms) {
  var d = new Date(ms);
  var p = function (n) { return (n < 10 ? "0" : "") + n; };
  // 用 UTC+8 换算
  var t = new Date(ms + 8 * 3600e3);
  return t.getUTCFullYear() + "-" + p(t.getUTCMonth() + 1) + "-" + p(t.getUTCDate());
}
function bjTime(ms) {
  var t = new Date(ms + 8 * 3600e3);
  var p = function (n) { return (n < 10 ? "0" : "") + n; };
  return p(t.getUTCMonth() + 1) + "-" + p(t.getUTCDate()) + " " + p(t.getUTCHours()) + ":" + p(t.getUTCMinutes());
}
function parseDay(s) { return new Date(s + "T00:00:00+08:00").getTime(); }
function parseMin(s) { return new Date(s.replace(" ", "T") + "+08:00").getTime(); }

/* ============ 1. 常量 ============ */
var PERIODS = {
  week: { label: "周", dur: 7 * 864e5 },
  day: { label: "日", dur: 864e5 },
  m30: { label: "30分", dur: 1800e3 },
  m5: { label: "5分", dur: 300e3 },
  m1: { label: "1分", dur: 60e3 }
};
var WATCH = [
  ["上证指数", "sh000001"], ["深证成指", "sz399001"], ["创业板指", "sz399006"],
  ["绿的谐波", "688017"], ["恒立液压", "601100"], ["瑞芯微", "603893"],
  ["乐鑫科技", "688018"], ["晶晨股份", "688099"], ["拓普集团", "601689"],
  ["浙江荣泰", "603119"], ["芯原股份", "688521"]
];
var SIG_NAME = { B1: "一买", B2: "二买", B3: "三买", S1: "一卖", S2: "二卖", S3: "三卖" };
var DISCL = "缠论结构信号（本工具规则引擎输出）";

/* 战法库 */
var TACTICS = [
  { n: "底背驰一买", t: "下跌线段出现底背驰（后段MACD柱面积<前段×0.8），随后出现向上已确认笔。", f: "跌破背驰段最低点则失效；背驰后可再背驰，仓位分批。" },
  { n: "二买回抽", t: "一买后出现向下笔回抽，回抽低点不破一买低点，随后向上笔恢复。", f: "回抽跌破一买低点则二买不成立。" },
  { n: "三买", t: "收盘突破中枢上沿，回抽不重回中枢（回抽低点>上沿），再恢复向上。", f: "回抽重回中枢内则三买失效。" },
  { n: "中枢震荡高抛低吸", t: "价格在已验证闭合中枢内运行；只用做T仓，上沿附近减、下沿附近加。", f: "收盘有效突破/跌破中枢沿则策略终止，转突破策略。" },
  { n: "跌破中枢下沿离场", t: "收盘跌破中枢下沿，反抽不过下沿；底仓按纪律离场，不摊平。", f: "收回中枢内则重新评估，不追回。" },
  { n: "背了又背", t: "背驰只保证转折方向不保证幅度；一买分批建仓，硬止损设在背驰低点下方。", f: "连续背驰是常态，不因一次背驰重仓。" }
];

/* ============ 2. 存储 ============ */
var LS = { custom: "chan_custom_v1", ledger: "chan_ledger_v1", notes: "chan_notes_v1", mon: "chan_mon_v1", set: "chan_set_v1" };
function lsGet(k, d) { try { var v = JSON.parse(localStorage.getItem(k)); return v === null || v === undefined ? d : v; } catch (e) { return d; } }
function lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
var _savedSet = lsGet(LS.set, {});
var settings = Object.assign({ rec: false, ma: false, boll: false, sub: "vol", drawerW: 270, monOn: false, v: 2 }, _savedSet);
if (!_savedSet.v) { settings.ma = false; settings.boll = false; settings.sub = "vol"; settings.v = 2; saveSettings(); }
function saveSettings() { lsSet(LS.set, settings); }

/* 录屏脱敏 */
var maskMap = {};
function maskName(code, name) {
  if (!settings.rec) return name || code;
  if (!maskMap[code]) maskMap[code] = "股票" + String.fromCharCode(65 + Object.keys(maskMap).length % 26);
  return maskMap[code];
}
function maskCode(code) {
  if (!settings.rec) return code;
  maskName(code, "");
  return maskMap[code];
}

/* ============ 3. 状态 ============ */
var S = {
  code: "603893", period: "day",
  bars: [], ksrc: "", knote: "", analysis: null,
  quotes: {}, custom: lsGet(LS.custom, []),
  ledger: lsGet(LS.ledger, { positions: [], trades: [] }),
  notes: lsGet(LS.notes, []), monLevels: lsGet(LS.mon, []),
  chatCtx: { topic: "" }, pan: 0, annotateArm: false,
  periodOK: { week: true, day: true, m30: true, m5: true, m1: true },
  periodWhy: {}
};
function codeName(code) {
  for (var i = 0; i < WATCH.length; i++) if (WATCH[i][1] === code) return WATCH[i][0];
  for (var j = 0; j < S.custom.length; j++) if (S.custom[j][1] === code) return S.custom[j][0];
  var ps = S.ledger.positions;
  for (var k = 0; k < ps.length; k++) if (ps[k].code === code) return ps[k].name || code;
  return code;
}

/* ============ 4. 数据加载 ============ */
function jsonp(url, cbName, timeoutMs) {
  return new Promise(function (resolve, reject) {
    var name = (cbName || "cb") + Date.now() + Math.floor(Math.random() * 1e6);
    var done = false, timer = setTimeout(function () { cleanup(); reject(new Error("timeout")); }, timeoutMs || 20000);
    function cleanup() { done = true; clearTimeout(timer); var s = document.getElementById(name); if (s) s.remove(); try { delete window[name]; } catch (e) { window[name] = undefined; } }
    window[name] = function (d) { if (done) return; cleanup(); resolve(d); };
    var s = document.createElement("script");
    s.id = name; s.async = true;
    s.onerror = function () { if (done) return; cleanup(); reject(new Error("neterr")); };
    s.src = url + (url.indexOf("?") >= 0 ? "&" : "?") + "callback=" + name;
    document.head.appendChild(s);
  });
}
function toBars(rows) {
  // rows: {t,o,h,l,c,v}
  return rows.filter(function (r) { return r && isFinite(r.h) && isFinite(r.l) && r.h >= r.l; });
}
function loadLocalDay(code) {
  return fetch("data/klines.json", { cache: "no-store" }).then(function (r) {
    if (!r.ok) throw 0;
    return r.json();
  }).then(function (j) {
    var arr = (j.stocks && j.stocks[code]) || (j.indexes && j.indexes[marketCode(code)]);
    if (!arr || arr.length < 30) throw 0;
    return { bars: toBars(arr.map(function (a) { return { t: parseDay(a[0]), o: a[1], h: a[2], l: a[3], c: a[4], v: a[5] }; })), src: "本地每日K线(" + (j.date || "") + ")" };
  });
}
function loadTencentDay(code) {
  return fetch("https://ifzq.gtimg.cn/appstock/app/newfqkline/get?param=" + marketCode(code) + ",day,,,640,qfq").then(function (r) {
    if (!r.ok) throw 0; return r.json();
  }).then(function (j) {
    var d = j && j.data && j.data[marketCode(code)];
    var arr = d && (d.qfqday || d.day);
    if (!arr || arr.length < 30) throw 0;
    return { bars: toBars(arr.map(function (a) { return { t: parseDay(a[0]), o: +a[1], h: +a[3], l: +a[4], c: +a[2], v: +a[5] }; })), src: "腾讯日K" };
  });
}
function loadSinaDay(code) {
  return jsonp("https://quotes.sina.cn/cn/api/openapi.php/CN_MarketDataService.getKLineData?symbol=" + sinaSym(code) + "&scale=240&ma=no&datalen=350").then(function (d) {
    var arr = d && d.result && d.result.data;
    if (!arr || arr.length < 30) throw 0;
    return { bars: toBars(arr.map(function (r) { return { t: parseDay(r.day.split(" ")[0]), o: +r.open, h: +r.high, l: +r.low, c: +r.close, v: +r.volume }; })), src: "新浪日K" };
  });
}
function loadTencentWeek(code) {
  return fetch("https://ifzq.gtimg.cn/appstock/app/newfqkline/get?param=" + marketCode(code) + ",week,,,640,qfq").then(function (r) {
    if (!r.ok) throw 0; return r.json();
  }).then(function (j) {
    var d = j && j.data && j.data[marketCode(code)];
    var arr = d && (d.qfqweek || d.week);
    if (!arr || arr.length < 10) throw 0;
    return { bars: toBars(arr.map(function (a) { return { t: parseDay(a[0]), o: +a[1], h: +a[3], l: +a[4], c: +a[2], v: +a[5] }; })), src: "腾讯周K" };
  });
}
function aggWeek(dayBars) {
  var weeks = [], cur = null, key = "";
  dayBars.forEach(function (b) {
    var t = new Date(b.t + 8 * 3600e3), dow = t.getUTCDay(), mon = b.t - ((dow + 6) % 7) * 864e5;
    var k = String(mon);
    if (k !== key) { key = k; cur = { t: mon, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v || 0 }; weeks.push(cur); }
    else { cur.h = Math.max(cur.h, b.h); cur.l = Math.min(cur.l, b.l); cur.c = b.c; cur.v += b.v || 0; }
  });
  return weeks;
}
function loadSinaMin(code, period) {
  var scale = { m30: 30, m5: 5, m1: 1 }[period];
  return jsonp("https://quotes.sina.cn/cn/api/openapi.php/CN_MarketDataService.getKLineData?symbol=" + sinaSym(code) + "&scale=" + scale + "&ma=no&datalen=600").then(function (d) {
    var arr = d && d.result && d.result.data;
    if (!arr || arr.length < 30) throw 0;
    return { bars: toBars(arr.map(function (r) { return { t: parseMin(r.day), o: +r.open, h: +r.high, l: +r.low, c: +r.close, v: +r.volume }; })), src: "新浪" + PERIODS[period].label + "K" };
  });
}
function loadKlines(code, period) {
  var seq;
  if (period === "day") seq = [loadLocalDay, loadTencentDay, loadSinaDay];
  else if (period === "week") seq = [loadTencentWeek, function (c) { return loadLocalDay(c).then(function (r) { return { bars: aggWeek(r.bars), src: r.src + "→合成周K" }; }); }, function (c) { return loadSinaDay(c).then(function (r) { return { bars: aggWeek(r.bars), src: r.src + "→合成周K" }; }); }];
  else seq = [function (c) { return loadSinaMin(c, period); }];
  var errs = [];
  function next(i) {
    if (i >= seq.length) return Promise.reject(errs.join("；") || "无可用数据源");
    return seq[i](code).catch(function (e) { errs.push(String(e && e.message || e)); return next(i + 1); });
  }
  return next(0);
}

/* 日线实时补齐：本地文件是收盘后快照，盘中会滞后一天；
 * 后台拉实时日K（腾讯→新浪），只补当日 bar：比本地末 bar 新则追加，
 * 与末 bar 同日且当日未收盘则刷新该 bar（结构运算会剔除未收盘 bar，只影响显示）。 */
function mergeLiveDay(localBars, liveBars) {
  if (!liveBars || !liveBars.length || !localBars.length) return null;
  var merged = localBars.slice();
  var last = merged[merged.length - 1];
  var changed = false, today = bjDate(Date.now());
  liveBars.forEach(function (b) {
    if (b.t > last.t) { merged.push(b); last = b; changed = true; }
    else if (b.t === last.t && bjDate(b.t) === today) {
      if (b.c !== last.c || b.h !== last.h || b.l !== last.l || b.v !== last.v) {
        merged[merged.length - 1] = b; last = b; changed = true;
      }
    }
  });
  return changed ? merged : null;
}
function tryLiveDay(code) {
  function attempt(fn) { return fn(code).then(function (r) { return r.bars; }); }
  return attempt(loadTencentDay).catch(function () { return attempt(loadSinaDay); })
    .then(function (liveBars) { return mergeLiveDay(S.bars, liveBars); });
}

/* 行情 */
function fetchQuotes(codes) {
  return new Promise(function (resolve) {
    var mapped = codes.map(marketCode), done = false;
    var s = document.createElement("script");
    var timer = setTimeout(function () { finish({}); }, 12000);
    function finish(out) {
      if (done) return; done = true; clearTimeout(timer);
      if (s.parentNode) s.parentNode.removeChild(s);
      resolve(out);
    }
    s.onerror = function () { finish({}); };
    s.onload = function () {
      var out = {};
      mapped.forEach(function (mc) {
        try {
          var raw = window["v_" + mc];
          try { delete window["v_" + mc]; } catch (e) { window["v_" + mc] = undefined; }
          if (typeof raw !== "string") return;
          var f = raw.split("~");
          if (f.length < 33) return;
          var num = function (i) { var v = parseFloat(f[i]); return isNaN(v) ? null : v; };
          out[mc] = { name: f[1], price: num(3), pct: num(32), high: num(33), low: num(34), time: f[30] };
        } catch (e) {}
      });
      finish(out);
    };
    s.src = "https://qt.gtimg.cn/q=" + mapped.join(",");
    document.head.appendChild(s);
  });
}

/* ============ 5. 引擎缓存 ============ */
var anaCache = {};
function getAnalysis(code, period, bars) {
  var key = code + "|" + period + "|" + bars.length + "|" + (bars.length ? bars[bars.length - 1].t : 0);
  if (!anaCache[key]) anaCache[key] = ChanEngine.analyze(bars, PERIODS[period].dur, Date.now());
  return anaCache[key];
}

/* ============ 6. K线图渲染 ============ */
var chart = { N: 140 };
function rr(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
function setupCanvas(cv, hCss) {
  var dpr = window.devicePixelRatio || 1;
  var w = cv.clientWidth || cv.parentNode.clientWidth;
  cv.width = w * dpr; cv.height = hCss * dpr;
  cv.style.height = hCss + "px";
  var ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx: ctx, w: w, h: hCss };
}
function visBars() {
  var n = Math.min(chart.N, S.bars.length);
  var end = S.bars.length - 1 - S.pan;
  if (end >= S.bars.length) end = S.bars.length - 1;
  var start = Math.max(0, end - n + 1);
  return { start: start, end: end };
}
function drawChart() {
  var cv = $("chart");
  if (!S.bars.length) {
    var g0 = setupCanvas(cv, 300), c0 = g0.ctx;
    c0.fillStyle = "#8b98a8"; c0.font = "14px sans-serif"; c0.textAlign = "center";
    c0.fillText(S.knote || "K线加载中…", g0.w / 2, 150);
    return;
  }
  var mainH = 260, subH = 90, padL = 4, padR = 52, padT = 14, padB = 18;
  var g = setupCanvas(cv, mainH + subH + 8), ctx = g.ctx, W = g.w;
  var v = visBars(), bars = S.bars.slice(v.start, v.end + 1), n = bars.length;
  var plotW = W - padL - padR, cw = plotW / n;
  function X(i) { return padL + (i + 0.5) * cw; }
  var lo = Infinity, hi = -Infinity;
  bars.forEach(function (b) { lo = Math.min(lo, b.l); hi = Math.max(hi, b.h); });
  var mas = {};
  if (settings.ma) [5, 10, 20, 60].forEach(function (k) { mas[k] = ChanEngine.smaArr(S.bars.map(function (b) { return b.c; }), k); });
  var boll = null;
  if (settings.boll) {
    var cs = S.bars.map(function (b) { return b.c; }), mid = ChanEngine.smaArr(cs, 20), up = [], dn = [];
    for (var i = 0; i < cs.length; i++) {
      if (mid[i] === null) { up.push(null); dn.push(null); continue; }
      var s = 0; for (var j = i - 19; j <= i; j++) s += (cs[j] - mid[i]) * (cs[j] - mid[i]);
      var sd = Math.sqrt(s / 20); up.push(mid[i] + 2 * sd); dn.push(mid[i] - 2 * sd);
      lo = Math.min(lo, dn[i]); hi = Math.max(hi, up[i]);
    }
    boll = { mid: mid, up: up, dn: dn };
  }
  var pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  function Y(p) { return padT + (hi - p) / (hi - lo) * mainH; }

  ctx.clearRect(0, 0, W, mainH + subH + 8);
  // 网格
  ctx.strokeStyle = "#1c2635"; ctx.lineWidth = 1; ctx.fillStyle = "#5c6a7d"; ctx.font = "10px sans-serif"; ctx.textAlign = "left";
  for (var gi = 0; gi <= 4; gi++) {
    var gp = lo + (hi - lo) * gi / 4, gy = Y(gp);
    ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(W - padR, gy); ctx.stroke();
    ctx.fillText(gp.toFixed(2), W - padR + 4, gy + 3);
  }
  // 时间轴
  ctx.textAlign = "center";
  for (var ti = 0; ti < n; ti += Math.ceil(n / 4)) {
    var bti = bars[ti];
    ctx.fillText(S.period === "day" || S.period === "week" ? bjDate(bti.t).slice(5) : bjTime(bti.t).slice(0, 11), X(ti), mainH + subH + 4);
  }
  // K线
  var bw = Math.max(2, Math.min(14, cw * 0.62));
  bars.forEach(function (b, i) {
    var up = b.c >= b.o, col = up ? "#f04444" : "#22b07d";
    ctx.strokeStyle = col; ctx.fillStyle = col;
    ctx.beginPath(); ctx.moveTo(X(i), Y(b.h)); ctx.lineTo(X(i), Y(b.l)); ctx.stroke();
    var y1 = Y(b.o), y2 = Y(b.c);
    ctx.fillRect(X(i) - bw / 2, Math.min(y1, y2), bw, Math.max(2, Math.abs(y2 - y1)));
  });
  // 均线 / 布林
  function line(arr, color, gi0) {
    ctx.strokeStyle = color; ctx.lineWidth = 1.2; ctx.beginPath();
    var started = false;
    for (var i = 0; i < n; i++) {
      var val = arr[v.start + i];
      if (val === null || val === undefined) { started = false; continue; }
      if (!started) { ctx.moveTo(X(i), Y(val)); started = true; } else ctx.lineTo(X(i), Y(val));
    }
    ctx.stroke();
  }
  if (settings.ma) {
    var mc = { 5: "#f5c518", 10: "#4da3ff", 20: "#b083f0", 60: "#ff9f43" };
    Object.keys(mas).forEach(function (k) { line(mas[k], mc[k]); });
  }
  if (boll) { line(boll.up, "#8b98a8"); line(boll.mid, "#e6ecf3"); line(boll.dn, "#8b98a8"); }

  var a = S.analysis;
  if (a) {
    // 中枢（先画，垫底）；只给最新一个中枢标文字，旧中枢只留框
    var lastPi = a.pivots.length ? a.pivots[a.pivots.length - 1] : null;
    a.pivots.forEach(function (p) {
      var x1 = X(Math.max(0, p.rawFrom - v.start)) - cw / 2, x2 = X(Math.min(n - 1, p.rawTo - v.start)) + cw / 2;
      if (x2 < padL || x1 > W - padR) return;
      if (p.state === "closed") {
        ctx.fillStyle = "rgba(245,197,24,.10)"; ctx.strokeStyle = "#f5c518"; ctx.setLineDash([]);
      } else {
        ctx.fillStyle = "rgba(245,197,24,.05)"; ctx.strokeStyle = "rgba(245,197,24,.65)"; ctx.setLineDash([5, 4]);
      }
      ctx.lineWidth = 1.5;
      ctx.fillRect(x1, Y(p.top), x2 - x1, Y(p.bot) - Y(p.top));
      ctx.strokeRect(x1, Y(p.top), x2 - x1, Y(p.bot) - Y(p.top));
      ctx.setLineDash([]);
      if (p === lastPi) {
        ctx.fillStyle = p.state === "closed" ? "#f5c518" : "rgba(245,197,24,.8)";
        ctx.font = "10px sans-serif"; ctx.textAlign = "left";
        ctx.fillText(p.state === "closed" ? "中枢" : "候选中枢", x1 + 3, Y(p.top) - 4);
      }
    });
    // 笔：已确认实线，待确认虚线；形成中笔不再绘制（每 tick 重绘的预览线，不是笔）
    function strokeLine(s, color, dash, width) {
      var i1 = s.rawFrom - v.start, i2 = s.rawTo - v.start;
      if (i2 < 0 || i1 >= n) return;
      i1 = Math.max(0, i1); i2 = Math.min(n - 1, i2);
      var p1 = s.from.price, p2 = s.to.price;
      ctx.strokeStyle = color; ctx.lineWidth = width || 1.8; ctx.setLineDash(dash || []);
      ctx.beginPath(); ctx.moveTo(X(i1), Y(p1)); ctx.lineTo(X(i2), Y(p2)); ctx.stroke();
      ctx.setLineDash([]);
    }
    a.strokes.forEach(function (s) {
      if (s.state === "confirmed") strokeLine(s, "#4da3ff");
      else strokeLine(s, "#ff9f43", [6, 4]);
    });
    // 买卖点：进场（买1/2/3）与卖出（卖1/2/3）信号，直接标在对应K线上
    var badgeBuy = [], badgeSell = [];
    a.signals.forEach(function (sg) {
      var bi = barIdxOf(sg.t);
      if (bi < v.start || bi > v.end) return;
      var i = bi - v.start, isB = sg.type.charAt(0) === "B";
      var confirmed = sg.state === "confirmed";
      var col = isB ? "#f04444" : "#22b07d";
      var x = X(i);
      // 信号K线处画纵向虚线，便于在图中定位
      ctx.save();
      ctx.globalAlpha = 0.3;
      ctx.strokeStyle = confirmed ? col : "#8b98a8";
      ctx.setLineDash([3, 4]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, mainH); ctx.stroke();
      ctx.restore();
      // 徽标：买1/买2/买3/卖1/卖2/卖3；候选加"?"；已确认实心、候选空心虚线框
      var txt = (isB ? "买" : "卖") + sg.type.charAt(1) + (confirmed ? "" : "?");
      ctx.font = "bold 11px sans-serif";
      var tw = ctx.measureText(txt).width + 12, th = 17;
      var bx = Math.max(padL + 1, Math.min(W - padR - tw - 1, x - tw / 2));
      var arr = isB ? badgeBuy : badgeSell, lvl = 0, k;
      for (k = 0; k < arr.length; k++) if (Math.abs(arr[k] - x) < tw + 6) lvl++;
      arr.push(x);
      var by = isB ? Y(S.bars[bi].l) + 5 + lvl * (th + 4)
                   : Y(S.bars[bi].h) - 5 - th - lvl * (th + 4);
      if (confirmed) {
        ctx.fillStyle = col; rr(ctx, bx, by, tw, th, 5); ctx.fill();
        ctx.fillStyle = "#ffffff";
      } else {
        ctx.strokeStyle = "#8b98a8"; ctx.lineWidth = 1.2;
        ctx.setLineDash([4, 3]); rr(ctx, bx, by, tw, th, 5); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = "#8b98a8";
      }
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(txt, bx + tw / 2, by + th / 2 + 1);
      ctx.textBaseline = "alphabetic";
    });
  }
  // 标注旗
  var myNotes = S.notes.filter(function (x) { return x.code === S.code && x.period === S.period; });
  ctx.font = "10px sans-serif";
  myNotes.forEach(function (nt, k) {
    var bi = nt.bar;
    if (bi < v.start || bi > v.end) return;
    var x = X(bi - v.start), y = padT + 12 + (k % 3) * 14;
    ctx.fillStyle = "#b083f0";
    ctx.beginPath(); ctx.arc(x, y, 8, 0, 7); ctx.fill();
    ctx.fillStyle = "#0e1319"; ctx.textAlign = "center";
    ctx.fillText(String(k + 1), x, y + 3.5);
  });

  // 副图
  var sy0 = mainH + 8, sh = subH;
  ctx.strokeStyle = "#1c2635";
  ctx.beginPath(); ctx.moveTo(padL, sy0); ctx.lineTo(W - padR, sy0); ctx.stroke();
  ctx.fillStyle = "#5c6a7d"; ctx.textAlign = "left"; ctx.font = "10px sans-serif";
  ctx.fillText({ vol: "成交量", macd: "MACD(12,26,9)" }[settings.sub] || "成交量", padL + 2, sy0 + 11);
  var closes = S.bars.map(function (b) { return b.c; });
  if (settings.sub === "vol") {
    var vmx = 0; bars.forEach(function (b) { vmx = Math.max(vmx, b.v || 0); }); vmx = vmx || 1;
    bars.forEach(function (b, i) {
      var h = (b.v || 0) / vmx * (sh - 16);
      ctx.fillStyle = b.c >= b.o ? "rgba(240,68,68,.7)" : "rgba(34,176,125,.7)";
      ctx.fillRect(X(i) - bw / 2, sy0 + sh - 4 - h, bw, h);
    });
  } else if (settings.sub === "macd") {
    var m = ChanEngine.macd(closes), vals = [];
    for (var i = 0; i < n; i++) { vals.push(m.dif[v.start + i], m.dea[v.start + i]); }
    var mn = Math.min.apply(null, vals.concat([0])), mx = Math.max.apply(null, vals.concat([0]));
    function MY(p) { return sy0 + sh - 4 - (p - mn) / ((mx - mn) || 1) * (sh - 20); }
    bars.forEach(function (b, i) {
      var h = m.hist[v.start + i] || 0;
      ctx.fillStyle = h >= 0 ? "rgba(240,68,68,.6)" : "rgba(34,176,125,.6)";
      var y0 = MY(0);
      ctx.fillRect(X(i) - bw / 2, Math.min(y0, MY(h)), bw, Math.max(1, Math.abs(MY(h) - y0)));
    });
    [["dif", "#f5c518"], ["dea", "#4da3ff"]].forEach(function (kv) {
      ctx.strokeStyle = kv[1]; ctx.lineWidth = 1; ctx.beginPath();
      var st = false;
      for (var i = 0; i < n; i++) {
        var vv = m[kv[0]][v.start + i];
        if (!st) { ctx.moveTo(X(i), MY(vv)); st = true; } else ctx.lineTo(X(i), MY(vv));
      }
      ctx.stroke();
    });
  }
  // 分隔信息
  ctx.fillStyle = "#5c6a7d"; ctx.textAlign = "right"; ctx.font = "10px sans-serif";
  ctx.fillText(bars.length + "根", W - padR - 4, sy0 + sh - 4);
}
function barIdxOf(t) {
  // 二分找最接近 t 的 bar 下标
  var lo = 0, hi = S.bars.length - 1;
  while (lo < hi) { var mid = (lo + hi) >> 1; if (S.bars[mid].t < t) lo = mid + 1; else hi = mid; }
  return lo;
}

/* ============ 7. 股票池抽屉 ============ */
function allPoolCodes() {
  var codes = WATCH.map(function (w) { return w[1]; }).concat(S.custom.map(function (c) { return c[1]; }));
  S.ledger.positions.forEach(function (p) { if (codes.indexOf(p.code) < 0) codes.push(p.code); });
  return codes;
}
function renderPool() {
  var el = $("pool"), q = S.quotes, html = "";
  function row(code, name, canDel) {
    var mc = marketCode(code), qq = q[mc] || {};
    var cls = qq.pct > 0 ? "up" : qq.pct < 0 ? "down" : "flat";
    return '<div class="pitem' + (S.code === code ? " cur" : "") + '" data-code="' + esc(code) + '">' +
      '<div class="nm">' + esc(maskName(code, name)) + '<div class="cd">' + esc(maskCode(code)) + '</div></div>' +
      '<div class="px ' + cls + '">' + (qq.price != null ? px2(qq.price) : "—") + '<div class="cd">' + pct2(qq.pct) + '</div></div>' +
      (canDel ? '<button class="del" data-del="' + esc(code) + '">✕</button>' : "") + '</div>';
  }
  html += '<div class="pgroup"><span>自选（本机）</span><span>' + S.custom.length + '</span></div>';
  html += S.custom.length ? S.custom.map(function (c) { return row(c[1], c[0], true); }).join("") : '<div class="caption">暂无，点击下方添加</div>';
  html += '<div class="field" style="margin:6px 0"><div style="display:flex;gap:6px"><input id="cName" placeholder="名称" style="flex:1"><input id="cCode" inputmode="numeric" placeholder="6位代码" style="flex:1"></div>' +
    '<button class="btn btn-ghost" id="cAdd" style="width:100%;margin:6px 0 0">＋ 加入自选</button></div>';
  html += '<div class="pgroup"><span>观察（默认）</span><span>' + WATCH.length + '</span></div>';
  html += WATCH.map(function (w) { return row(w[1], w[0], false); }).join("");
  html += '<div class="pgroup"><span>持仓（同步账本）</span><span>' + S.ledger.positions.length + '</span></div>';
  html += S.ledger.positions.length ? S.ledger.positions.map(function (p) { return row(p.code, p.name || p.code, false); }).join("") : '<div class="caption">账本暂无持仓，去"持仓"页添加</div>';
  el.innerHTML = html;
  el.querySelectorAll(".pitem").forEach(function (d) {
    d.addEventListener("click", function (e) {
      if (e.target.getAttribute("data-del")) return;
      selectCode(d.getAttribute("data-code"));
      closeDrawer();
    });
  });
  el.querySelectorAll("[data-del]").forEach(function (b) {
    b.addEventListener("click", function (e) {
      e.stopPropagation();
      var code = b.getAttribute("data-del");
      S.custom = S.custom.filter(function (c) { return c[1] !== code; });
      lsSet(LS.custom, S.custom); renderPool();
    });
  });
  var add = $("cAdd");
  if (add) add.onclick = function () {
    var nm = $("cName").value.trim(), cd = $("cCode").value.trim();
    if (!/^\d{6}$/.test(cd)) { alert("代码须为6位数字"); return; }
    if (S.custom.some(function (c) { return c[1] === cd; })) { alert("已在自选中"); return; }
    S.custom.push([nm || cd, cd]); lsSet(LS.custom, S.custom);
    renderPool(); refreshQuotes();
  };
}
function openDrawer() { $("drawer").classList.add("open"); $("scrim").classList.add("open"); }
function closeDrawer() { $("drawer").classList.remove("open"); $("scrim").classList.remove("open"); }

/* ============ 8. 主流程 ============ */
function setPeriodNote() {
  var p = PERIODS[S.period], el = $("periodNote");
  var extra = S.knote ? " · " + S.knote : "";
  el.textContent = "周期：" + p.label + extra + (S.analysis && S.analysis.droppedLast ? " · 已剔除未收盘K线" : "");
  document.querySelectorAll("#periodSeg button").forEach(function (b) {
    var per = b.getAttribute("data-p");
    b.disabled = !S.periodOK[per];
    b.classList.toggle("on", per === S.period);
    b.title = S.periodOK[per] ? "" : (S.periodWhy[per] || "数据源不可用");
  });
}
function refreshAll() {
  var code = S.code, period = S.period;
  $("topSym").textContent = maskName(code, codeName(code)) + " " + maskCode(code) + " · " + PERIODS[period].label;
  $("periodNote").textContent = "K线加载中…";
  loadKlines(code, period).then(function (r) {
    S.bars = r.bars; S.ksrc = r.src; S.knote = r.src;
    S.pan = 0; anaCache = {};
    S.analysis = getAnalysis(code, period, S.bars);
    $("dataSrc").textContent = r.src;
    renderAll();
    // 日线且走本地快照时，后台补当日实时 bar（盘中否则滞后一天）
    if (period === "day" && r.src.indexOf("本地") === 0) {
      tryLiveDay(code).then(function (merged) {
        if (merged && S.code === code && S.period === "day") {
          S.bars = merged; S.ksrc = r.src + "＋今日实时"; S.knote = S.ksrc;
          anaCache = {}; S.analysis = getAnalysis(code, period, S.bars);
          $("dataSrc").textContent = S.ksrc;
          renderAll();
        }
      }).catch(function () {});
    }
  }).catch(function (e) {
    S.bars = []; S.analysis = null;
    S.knote = "加载失败：" + e;
    S.periodOK[period] = false; S.periodWhy[period] = String(e).slice(0, 40);
    renderAll();
  });
  refreshQuotes();
}
function refreshQuotes() {
  var codes = allPoolCodes();
  fetchQuotes(codes).then(function (out) {
    S.quotes = out;
    renderPool();
    var qq = out[marketCode(S.code)];
    if (qq && qq.price != null) {
      $("topSym").textContent = maskName(S.code, codeName(S.code)) + " " + maskCode(S.code) + " · " + PERIODS[S.period].label +
        "  " + px2(qq.price) + " (" + pct2(qq.pct) + ")";
    }
    renderPositions();
  });
}
/* 状态行（一目了然）：位置（相对最近中枢）＋中枢区间＋最近一段方向＋最近已确认信号＋背驰 */
function renderStructBar() {
  var a = S.analysis, el = $("structBar");
  if (!a) { el.innerHTML = ""; return; }
  var parts = [];
  if (a.lastPivot) {
    var pos = a.lastClose > a.lastPivot.top ? "中枢上方" : a.lastClose < a.lastPivot.bot ? "中枢下方" : "中枢内";
    parts.push(pos + " · 中枢 " + px2(a.lastPivot.bot) + "–" + px2(a.lastPivot.top) + (a.lastPivot.state === "candidate" ? "（候选）" : ""));
  } else parts.push("无中枢");
  var lastSeg = a.segments.length ? a.segments[a.segments.length - 1] : null;
  if (lastSeg) parts.push("最近一段" + (lastSeg.dir > 0 ? "向上" : "向下"));
  var conf = a.signals.filter(function (s) { return s.state === "confirmed"; });
  parts.push(conf.length ? "最近已确认：" + (SIG_NAME[conf[conf.length - 1].type] || conf[conf.length - 1].type) + " @" + px2(conf[conf.length - 1].price) : "无已确认信号");
  if (a.divergences.length) parts.push("背驰 " + a.divergences.length + " 处");
  el.innerHTML = parts.map(function (p) { return "<span>" + p + "</span>"; }).join("");
}
function renderSignals() {
  var a = S.analysis, el = $("sigList");
  if (!a || !a.signals.length) { el.innerHTML = '<span style="color:var(--muted)">当前无结构信号</span>'; return; }
  el.innerHTML = a.signals.slice().reverse().slice(0, 6).map(function (s) {
    var isB = s.type.charAt(0) === "B";
    var cls = s.state === "confirmed" ? (isB ? "tag b1" : "tag s1") : "tag cand";
    return '<div style="padding:6px 0;border-bottom:1px solid var(--line)"><span class="' + cls + '">' +
      (SIG_NAME[s.type] || s.type) + (s.state === "confirmed" ? "" : "·候选") + "</span> " +
      "<b>" + px2(s.price) + "</b> <span style='color:var(--muted)'>" + bjDate(s.t) + " · " + esc(s.note) + "</span></div>";
  }).join("") + '<div class="note" style="margin:6px 0 0">共 ' + a.signals.length + " 个信号，仅列最近 6 个；" + DISCL + "；候选信号点位可能变动，仅已确认信号用于定位。</div>";
}
function renderAll() {
  setPeriodNote(); drawChart(); renderStructBar(); renderSignals(); renderNotes();
}

/* ============ 9. 复盘标注 ============ */
function renderNotes() {
  var el = $("noteList");
  var list = S.notes.map(function (n, i) { return { n: n, i: i }; })
    .filter(function (x) { return x.n.code === S.code && x.n.period === S.period; });
  if (!list.length) { el.innerHTML = '<span style="color:var(--muted)">暂无标注。点"＋ 复盘标注"后点图表位置添加。</span>'; return; }
  el.innerHTML = list.map(function (x, k) {
    return '<div style="padding:6px 0;border-bottom:1px solid var(--line)"><b style="color:var(--purple)">' + (k + 1) + ".</b> " +
      esc(x.n.text) + ' <span style="color:var(--muted)">' + bjDate(x.n.t) + " " + bjTime(S.bars[x.n.bar] ? S.bars[x.n.bar].t : 0).slice(6) + "</span> " +
      '<button class="del" data-ni="' + x.i + '" style="float:right">删除</button></div>';
  }).join("");
  el.querySelectorAll("[data-ni]").forEach(function (b) {
    b.onclick = function () { S.notes.splice(+b.getAttribute("data-ni"), 1); lsSet(LS.notes, S.notes); renderNotes(); drawChart(); };
  });
}
function chartTap(ev) {
  var cv = $("chart"), rect = cv.getBoundingClientRect();
  var x = (ev.clientX - rect.left), v = visBars(), n = v.end - v.start + 1;
  var plotW = rect.width - 4 - 52, cw = plotW / n;
  var i = Math.round((x - 4) / cw - 0.5);
  i = Math.max(0, Math.min(n - 1, i));
  var bi = v.start + i;
  // 点中标注旗？
  var myNotes = S.notes.filter(function (x) { return x.code === S.code && x.period === S.period; });
  for (var k = 0; k < myNotes.length; k++) {
    if (Math.abs(myNotes[k].bar - bi) <= 1) { alert("标注 " + (k + 1) + "：" + myNotes[k].text); return; }
  }
  if (!S.annotateArm) return;
  S.annotateArm = false; $("noteAdd").textContent = "＋ 复盘标注";
  var txt = prompt("在 " + bjDate(S.bars[bi].t) + " 处添加复盘标注：");
  if (txt && txt.trim()) {
    S.notes.push({ code: S.code, period: S.period, bar: bi, t: S.bars[bi].t, text: txt.trim().slice(0, 120) });
    lsSet(LS.notes, S.notes); renderNotes(); drawChart();
  }
}
function selectCode(code) {
  S.code = code; anaCache = {};
  renderPool(); refreshAll();
  if (S.chatCtx) { S.chatCtx.topic = ""; }
}

/* ============ 10. AI小缠推演（规则意图引擎，非大模型） ============ */
function chatAdd(who, text) {
var box = $("chat"), d = document.createElement("div");
d.className = "msg " + who;
d.innerHTML = '<div class="who">' + (who === "ai"? "AI小缠 · 规则引擎": "我") + "</div>" + esc(text);
box.appendChild(d);
box.scrollTop = box.scrollHeight;
}
function chatChips(list) {
var el = $("chips");
el.innerHTML = "";
list.forEach(function (t) {
var b = document.createElement("button");
b.textContent = t;
b.onclick = function () { ask(t);};
el.appendChild(b);
});
}
function myPosition(code) {
var ps = S.ledger.positions;
for (var i = 0; i < ps.length; i++) if (ps[i].code === code) return ps[i];
return null;
}
function keyLevels(a) {
// 返回 {name, price} 列表：中枢沿、最近分型极值、信号触发价
var lv = [];
a.pivots.forEach(function (p, i) {
var tag = p.state === "closed"? "中枢" + (i + 1): "候选中枢" + (i + 1);
lv.push({ name: tag + "上沿", price: p.top});
lv.push({ name: tag + "下沿", price: p.bot});
});
var fs = a.fractals;
if (fs.length) {
var lf = fs[fs.length - 1];
lv.push({ name: "最近" + (lf.type === "top"? "顶分型": "底分型"), price: lf.price});
}
a.signals.forEach(function (s) {
if (s.state === "confirmed") lv.push({ name: (SIG_NAME[s.type] || s.type) + "触发价", price: s.price});
});
return lv;
}
function fmtLevels(lv) {
return lv.map(function (l) { return "· " + l.name + "：" + px2(l.price);}).join("\n");
}
function tacticText(name) {
for (var i = 0; i < TACTICS.length; i++) if (TACTICS[i].n === name) {
var t = TACTICS[i];
return "\n触发条件：" + t.t + "\n失效条件：" + t.f;
}
return "";
}
function ask(text) {
text = (text || "").trim();
if (!text) return;
chatAdd("me", text);
$("chatIn").value = "";
setTimeout(function () { answer(text);}, 350);
}
function answer(text) {
var code = S.code, name = codeName(code);
var a = S.analysis, q = S.quotes[marketCode(code)] || {};
var pos = myPosition(code);
var t = text.replace(/\s+/g, "");
var R = "", chips = [];
function needA() { return a;}

if (/^(help|帮助|你能|功能)$/.test(t) || /怎么用|介绍/.test(t)) {
R = "我可以回答（基于当前股 " + maskName(code, name) + " 的多周期结构＋行情＋你的持仓账本）：\n" +
"· 结构综述：多周期笔/中枢/背驰/买卖点\n· 关键价位：中枢上下沿、分型极值、信号触发价\n· 区间套：日线→30分钟→5分钟逐级定位\n· 做T方案：按账本可卖存量核算\n· 止损：只允许上移；破位摊平会明确拒绝\n· 选股：对观察＋自选池跑结构筛查\n· 持仓诊断：结合结构看你的持仓\n以上均为" + DISCL + "，不构成买卖建议。";
chips = ["结构综述", "关键价位", "区间套分析", "持仓诊断"];
} else if (/结构|综述|总结|现在什么|走势/.test(t)) {
if (!needA()) { R = "K线尚未加载成功，请稍后重试。";}
else {
var parts = ["", ChanEngine.summarize(a)];
parts.push("关键价位：\n" + fmtLevels(keyLevels(a).slice(0, 6)));
if (q.price!= null) parts.push("现价 " + px2(q.price) + "（" + pct2(q.pct) + "）。");
parts.push("——" + DISCL + "，不构成买卖建议。");
R = parts.join("\n");
}
chips = ["关键价位", "区间套分析", "最近买卖点", "背驰情况"];
} else if (/关键价|关键价格|支撑|压力/.test(t)) {
R =!needA()? "K线尚未加载。": "\n" + fmtLevels(keyLevels(a)) +
(q.price!= null? "\n现价 " + px2(q.price) + "。": "") + "\n——" + DISCL + "。";
chips = ["区间套分析", "结构综述", "持仓诊断"];
} else if (/区间套/.test(t)) {
R = "区间套分析需要多周期数据，正在为你逐级计算…";
chatAdd("ai", R); chatChips([]);
nestAnalyze(code).then(function (txt) { chatAdd("ai", txt); chatChips(["关键价位", "结构综述", "做T方案"]);});
return;
} else if (/做T|做t|T0|t0/.test(t)) {
if (!pos ||!(pos.tQty > 0)) {
R = "账本中“ + maskName(code, name) + ”没有做T仓（可卖存量为0）。\n做T铁律：卖出量 ≤ 可卖存量，否则当日无法回补。先去“持仓”页录入做T数量。";
} else {
var lv = a? keyLevels(a).slice(0, 4): [];
R = "\n账本做T仓：" + pos.tQty + "股（可卖存量上限）。\n" +
"· 单次卖出 ≤ " + pos.tQty + "股，超出将被拦截。\n" +
(lv.length? "· 参考价位：\n" + fmtLevels(lv) + "\n": "") +
"· 建议只在已验证闭合中枢内高抛低吸（战法：中枢震荡高抛低吸），单边趋势中不做T。\n" +
"——" + DISCL + "。";
}
chips = ["关键价位", "中枢震荡战法", "持仓诊断"];
} else if (/止损/.test(t)) {
var m = t.match(/止损.{0,4}([\d.]+)/);
if (m && pos) {
var nw = parseFloat(m[1]);
if (nw >= pos.stop) { pos.stop = nw; lsSet(LS.ledger, S.ledger); renderPositions(); R = "底仓止损已上移至 " + px2(nw) + "（原 " + px2(pos.stop) + "）。规则：止损只上移不下移。";}
else R = "拒绝：新止损 " + px2(nw) + " 低于现止损 " + px2(pos.stop) + "。规则是止损只允许上移，不允许下移。";
} else if (!pos) R = "账本中无该股持仓，无法调整止损。请先去“持仓”页录入。";
else R = "当前底仓止损：" + px2(pos.stop) + "。\n规则：止损只上移不下移。直接说“止损调到X”即可上移。\n" + tacticText("跌破中枢下沿离场");
chips = ["持仓诊断", "关键价位", "结构综述"];
} else if (/摊平|补仓|加仓/.test(t)) {
R = "明确拒绝：破位不摊平。\n理由：摊平把“看错一次”变成“越错越大”；底仓纪律是“止损只上移不下移”。如果到了止损位，按纪律离场，而不是补仓摊薄。";
chips = ["止损规则", "持仓诊断", "结构综述"];
} else if (/底仓/.test(t)) {
    R = pos ? "【底仓·" + maskName(code, name) + "】数量 " + pos.baseQty + "股，成本 " + px2(pos.cost) + "，止损 " + px2(pos.stop) +
      "。\n底仓只吃趋势：持有逻辑是“中枢上移/趋势延续”，离场逻辑是“跌破中枢下沿/止损”。不与做T仓混用。" :
"账本中无该股底仓。底仓是趋势仓位，做T是套利仓位，两者分开记账、互不挪用。";
chips = ["止损规则", "做T方案", "持仓诊断"];
} else if (/选股/.test(t)) {
R = "选股筛查在“选股”页运行（对观察＋自选池跑日线结构信号）。切过去点“开始筛查”即可，结果会列出已确认一/二/三买卖点。";
chips = ["结构综述", "help"];
} else if (/持仓|诊断|我的/.test(t)) {
if (!pos) R = "账本中暂无" + maskName(code, name) + "的持仓。去“持仓”页录入后，我可以结合结构做诊断。";
else {
var pnl = q.price!= null? (q.price - pos.cost) * (pos.baseQty + pos.tQty): null;
R = "\n底仓 " + pos.baseQty + "股＋做T " + pos.tQty + "股，成本 " + px2(pos.cost) +
"，止损 " + px2(pos.stop) + "。" + (pnl!== null? "\n浮动盈亏约 " + (pnl >= 0? "+": "") + Math.round(pnl) + "元（按现价" + px2(q.price) + "）。": "") +
(a? "\n" + ChanEngine.summarize(a).split("\n")[0]: "") +
"\n纪律检查：现价" + (q.price!= null && q.price < pos.stop? "已低于止损" + px2(pos.stop) + "，按纪律应离场、不摊平。": "在止损" + px2(pos.stop) + "之上，持有。") +
"\n——" + DISCL + "。";
}
chips = ["止损规则", "做T方案", "关键价位"];
} else if (/买点|卖点/.test(t)) {
if (!needA()) R = "K线尚未加载。";
else {
var ss = a.signals;
R = ss.length? "\n" + ss.map(function (s) {
return "· " + (SIG_NAME[s.type] || s.type) + (s.state === "confirmed"? "（已确认）": "（候选·会重绘）") + " @" + px2(s.price) + " " + bjDate(s.t) + " —— " + s.note;
}).join("\n") + "\n——" + DISCL + "。": "当前周期无结构信号。";
}
chips = ["关键价位", "背驰情况", "结构综述"];
} else if (/背驰/.test(t)) {
if (!needA()) R = "K线尚未加载。";
else R = a.divergences.length? "发现 " + a.divergences.length + " 处背驰：\n" + a.divergences.map(function (d) {
return "· " + (d.type === "bottom"? "底背驰": "顶背驰") + "，力度比 " + d.ratio.toFixed(2) + "，位置约 " + px2(d.price);
}).join("\n") + "\n" + tacticText("背了又背"): "当前周期未发现背驰（MACD柱面积口径）。";
chips = ["最近买卖点", "结构综述"];
} else if (/中枢/.test(t)) {
if (!needA()) R = "K线尚未加载。";
else {
R = a.pivots.length? "\n" + a.pivots.map(function (p, i) {
return "· " + (p.state === "closed"? "已验证闭合": "候选延伸") + "中枢" + (i + 1) + "：上沿 " + px2(p.top) + "，下沿 " + px2(p.bot);
}).join("\n") + "\n" + tacticText("中枢震荡高抛低吸"): "当前周期未形成中枢。";
}
chips = ["关键价位", "做T方案", "结构综述"];
} else if (/战法|策略库/.test(t)) {
R = "\n" + TACTICS.map(function (x) { return "· " + x.n;}).join("\n") + "\n说战法名（如“三买”）看触发/失效条件。";
chips = TACTICS.slice(0, 4).map(function (x) { return x.n;});
} else if (/一买|二买|三买/.test(t)) {
var tn = t.indexOf("一买") >= 0? "底背驰一买": t.indexOf("二买") >= 0? "二买回抽": "三买";
R = tacticText(tn);
chips = ["最近买卖点", "结构综述"];
} else {
R = "没理解。" + maskName(code, name) + "当前可问：结构综述 / 关键价位 / 区间套分析 / 做T方案 / 止损 / 持仓诊断 / 选股 / 战法库。试试“关键价位”。";
chips = ["结构综述", "关键价位", "help"];
}
S.chatCtx.topic = text.slice(0, 20);
chatAdd("ai", R); chatChips(chips);
}
/* 区间套：日线笔 → 30分钟线段 → 5分钟结构 */
function nestAnalyze(code) {
function one(period) {
return loadKlines(code, period).then(function (r) {
return { period: period, a: getAnalysis(code, period, r.bars), src: r.src};
}).catch(function (e) { return { period: period, err: String(e).slice(0, 30)};});
}
return Promise.all([one("day"), one("m30"), one("m5")]).then(function (rs) {
var out = ["（日线笔 → 30分钟线段 → 5分钟结构）"];
rs.forEach(function (r) {
if (r.err) { out.push(PERIODS[r.period].label + "：数据不可用（" + r.err + "）。"); return;}
var a = r.a;
var lastSeg = a.segments.length? a.segments[a.segments.length - 1]: null;
out.push(PERIODS[r.period].label + "（" + r.src + ")：" + a.strokes.length + "笔/" + a.segments.length + "段/中枢" + a.pivots.length + "个" +
(lastSeg? "，当下段方向" + (lastSeg.dir > 0? "向上": "向下"): "") +
(a.lastPivot? "，最近中枢" + px2(a.lastPivot.bot) + "-" + px2(a.lastPivot.top): ""));
});
out.push("用法：大级别定方向（日线段），小级别找精确位置（5分钟背驰/买卖点）；小级别服从大级别。——" + DISCL + "。");
return out.join("\n");
});
}

/* ============ 11. AI盯盘 ============ */
function autoLevels() {
var a = S.analysis;
if (!a) return [];
return keyLevels(a).map(function (l) { return { name: l.name, price: l.price, auto: true, hit: false, last: null};});
}
function allLevels() {
var custom = S.monLevels.filter(function (l) { return l.code === S.code;});
return autoLevels().concat(custom.map(function (l) { return { name: l.note || "自设", price: l.price, auto: false, hit:!!l.hit, last: l.last};}));
}
function renderMon() {
var el = $("monList");
var lv = allLevels();
el.innerHTML = lv.length? lv.map(function (l, i) {
return '<div class="lvl' + (l.hit? " hit": "") + '"><div class="t">' + esc(l.name) + (l.auto? ' <span class="tag">自动</span>': "") +
(l.hit? ' <span class="tag b1">已触发</span>': "") + '</div><div class="p">' + px2(l.price) + "</div></div>";
}).join(""): '<span style="color:var(--muted)">K线未加载，无自动价位</span>';
}
function beep() {
try {
var C = window.AudioContext || window.webkitAudioContext;
if (!C) return;
var ctx = new C(), o = ctx.createOscillator(), gn = ctx.createGain();
o.connect(gn); gn.connect(ctx.destination);
o.frequency.value = 880; o.type = "sine";
gn.gain.setValueAtTime(0.25, ctx.currentTime);
o.start(); o.stop(ctx.currentTime + 0.35);
} catch (e) {}
}
function monTick() {
if (!settings.monOn || document.visibilityState!== "visible") return;
fetchQuotes([S.code]).then(function (out) {
var q = out[marketCode(S.code)];
if (!q || q.price == null) return;
var px = q.price, lv = allLevels(), hitAny = false;
lv.forEach(function (l) {
var prev = l.last;
l.last = px;
if (prev === null || prev === undefined) return;
var crossed = (prev < l.price && px >= l.price) || (prev > l.price && px <= l.price);
if (crossed &&!l.hit) {
l.hit = true; hitAny = true;
monAlert(l, px);
if (!l.auto) {
S.monLevels.forEach(function (c) { if (c.code === S.code && c.price === l.price) c.hit = true;});
lsSet(LS.mon, S.monLevels);
}
}
});
if (hitAny) renderMon();
});
}
function monAlert(l, px) {
beep();
var bar = document.createElement("div");
bar.className = "banner careful";
bar.style.cssText = "position:fixed;top:56px;left:12px;right:12px;z-index:80;margin:0";
bar.textContent = "盯盘提醒：" + maskName(S.code, codeName(S.code)) + " 触及 " + l.name + " " + px2(l.price) + "（现价" + px2(px) + "），请复核。";
document.body.appendChild(bar);
setTimeout(function () { if (bar.parentNode) bar.parentNode.removeChild(bar);}, 12000);
var log = $("alertLog");
if (log.querySelector("div") && log.textContent.trim() === "暂无") log.innerHTML = "";
var d = document.createElement("div");
d.textContent = bjTime(Date.now()) + " · " + l.name + " " + px2(l.price) + " 被触及（现价" + px2(px) + "）";
log.insertBefore(d, log.firstChild);
}

/* ============ 12. 选股筛查 ============ */
function runScan() {
var codes = WATCH.map(function (w) { return w[1];}).concat(S.custom.map(function (c) { return c[1];}));
var btn = $("scanBtn"), st = $("scanStatus"), list = $("scanList");
btn.disabled = true;
list.innerHTML = "";
var rows = [], i = 0;
st.textContent = "筛查中 0/" + codes.length + "…";
function next() {
if (i >= codes.length) { renderScan(rows); btn.disabled = false; st.textContent = "完成 " + codes.length + " 只，有信号 " + rows.length + " 只"; return;}
var code = codes[i];
st.textContent = "筛查中 " + (i + 1) + "/" + codes.length + " " + code + "…";
loadKlines(code, "day").then(function (r) {
var a = getAnalysis(code, "day", r.bars);
var conf = a.signals.filter(function (s) { return s.state === "confirmed";});
if (conf.length) {
var last = conf[conf.length - 1];
rows.push({ code: code, sig: last, n: conf.length, close: a.lastClose});
}
}).catch(function (e) { rows.push({ code: code, err: String(e).slice(0, 24)});})
.then(function () { i++; setTimeout(next, 250);});
}
next();
}
function renderScan(rows) {
var el = $("scanList");
var ok = rows.filter(function (r) { return!r.err;});
if (!ok.length) { el.innerHTML = '<span style="color:var(--muted)">均无信号或均加载失败</span>'; return;}
el.innerHTML = '<table><thead><tr><th>股票</th><th>最新信号</th><th>触发价</th><th>日期</th><th>现价</th></tr></thead><tbody>' +
ok.map(function (r) {
var isB = r.sig.type.charAt(0) === "B";
return "<tr><td>" + esc(maskName(r.code, codeName(r.code))) + "<br><span style='color:var(--faint);font-size:11px'>" + esc(maskCode(r.code)) + "</span></td>" +
'<td><span class="tag ' + (isB? "b1": "s1") + '">' + (SIG_NAME[r.sig.type] || r.sig.type) + "</span></td>" +
"<td>" + px2(r.sig.price) + "</td><td>" + bjDate(r.sig.t) + "</td><td>" + px2(r.close) + "</td></tr>";
}).join("") + "</tbody></table>" +
'<div class="note" style="margin:6px 0 0">日线口径 · ' + DISCL + "，不构成买卖建议。</div>" +
(rows.some(function (r) { return r.err;})? '<div class="note">加载失败：' + rows.filter(function (r) { return r.err;}).map(function (r) { return r.code;}).join("、") + "</div>": "");
}

/* ============ 13. 持仓账本 ============ */
function renderPositions() {
  var el = $("posList");
  var ps = S.ledger.positions;
  if (!ps.length) { el.innerHTML = '<div class="caption">暂无持仓</div>'; return; }
  el.innerHTML = ps.map(function (p, i) {
    var q = S.quotes[marketCode(p.code)] || {};
    var qty = (p.baseQty || 0) + (p.tQty || 0);
    var pnl = q.price != null ? (q.price - p.cost) * qty : null;
    var belowStop = q.price != null && p.stop > 0 && q.price < p.stop;
    return '<div class="pos"><div class="ph"><b>' + esc(maskName(p.code, p.name || p.code)) + " " + esc(maskCode(p.code)) + "</b>" +
      '<button class="del" data-pdel="' + i + '">删除</button></div>' +
      '<div class="kv"><span class="k">现价 / 涨跌</span><span class="v">' + px2(q.price) + " / " + pct2(q.pct) + "</span></div>" +
      '<div class="kv"><span class="k">底仓 / 做T</span><span class="v">' + (p.baseQty || 0) + " / " + (p.tQty || 0) + " 股</span></div>" +
      '<div class="kv"><span class="k">成本 / 止损</span><span class="v">' + px2(p.cost) + " / " + px2(p.stop) + "</span></div>" +
      '<div class="kv"><span class="k">浮动盈亏</span><span class="v ' + (pnl >= 0 ? "up" : "down") + '">' + (pnl === null ? DASH : (pnl >= 0 ? "+" : "") + Math.round(pnl) + "元") + "</span></div>" +
      (belowStop ? '<div class="banner defend" style="margin:6px 0 0">现价已低于止损' + px2(p.stop) + "，按纪律应离场、不摊平。</div>" : "") +
      "</div>";
  }).join("");
  el.querySelectorAll("[data-pdel]").forEach(function (b) {
    b.onclick = function () {
      if (!confirm("删除该持仓？")) return;
      S.ledger.positions.splice(+b.getAttribute("data-pdel"), 1);
      lsSet(LS.ledger, S.ledger); renderPositions(); renderPool();
    };
  });
}
function renderTrades() {
  var el = $("tradeList"), ts = S.ledger.trades;
  if (!ts.length) { el.innerHTML = '<span style="color:var(--muted)">暂无</span>'; return; }
  el.innerHTML = ts.slice().reverse().slice(0, 60).map(function (t) {
    return '<div style="padding:6px 0;border-bottom:1px solid var(--line)">' +
      '<span class="tag ' + (t.dir === "买入" ? "b1" : "s1") + '">' + t.dir + "</span> " +
      esc(maskName(t.code, t.name || t.code)) + " " + t.qty + "股 @ " + px2(t.price) +
      (t.kind ? "（" + t.kind + "）" : "") + ' <span style="color:var(--muted)">' + esc(t.date) + "</span></div>";
  }).join("");
}
/* 成交记录解析：买入 乐鑫科技 500股 150.2 / 卖出 603893 200股 195.5 做T / 10手=1000股 */
function parseTrades(text) {
  var ok = [], fail = [];
  text.split("\n").forEach(function (ln) {
    ln = ln.trim();
    if (!ln) return;
    var m = ln.match(/^(买入|卖出)\s+([^\s]+)\s+(\d+)\s*(股|手)\s+([\d.]+)\s*(做T|底仓)?/);
    if (!m) { fail.push(ln); return; }
    var nameOrCode = m[2], qty = parseInt(m[3], 10) * (m[4] === "手" ? 100 : 1), price = parseFloat(m[5]);
    if (!(qty > 0 && price > 0)) { fail.push(ln); return; }
    var code = /^\d{6}$/.test(nameOrCode) ? nameOrCode : "";
    var name = code ? codeName(code) : nameOrCode;
    if (!code) {
      // 按名称反查
      var all = WATCH.concat(S.custom);
      for (var i = 0; i < all.length; i++) if (all[i][0] === nameOrCode) { code = all[i][1]; name = all[i][0]; break; }
      S.ledger.positions.forEach(function (p) { if ((p.name || "") === nameOrCode) { code = p.code; name = p.name; } });
    }
    if (!code) { fail.push(ln + "（未识别标的）"); return; }
    ok.push({ date: bjDate(Date.now()), code: code, name: name, dir: m[1], qty: qty, price: price, kind: m[6] || "" });
  });
  return { ok: ok, fail: fail };
}

/* ============ 14. 初始化与事件 ============ */
function switchTab(t) {
  document.querySelectorAll(".bottomnav button").forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-tab") === t); });
  document.querySelectorAll(".tabpane").forEach(function (p) { p.classList.toggle("on", p.id === "tab-" + (t === "k" ? "k" : t)); });
  if (t === "k") setTimeout(drawChart, 30);
  if (t === "mon") { renderMon(); }
  if (t === "chat" && !$("chat").children.length) {
    chatAdd("ai", "我是AI小缠（规则引擎）。当前：" + maskName(S.code, codeName(S.code)) + "。\n可问：结构综述 / 关键价位 / 区间套分析 / 做T方案 / 止损 / 持仓诊断 / 选股 / 战法库。");
    chatChips(["结构综述", "关键价位", "区间套分析", "持仓诊断"]);
  }
}
function init() {
  // 抽屉
  $("poolBtn").onclick = openDrawer;
  $("drawerHide").onclick = closeDrawer;
  $("scrim").onclick = closeDrawer;
  $("drawer").style.width = settings.drawerW + "px";
  $("drawerW").value = settings.drawerW;
  $("drawerW").oninput = function () { settings.drawerW = +$("drawerW").value; $("drawer").style.width = settings.drawerW + "px"; saveSettings(); };
  // 录屏
  var rb = $("recBtn");
  rb.classList.toggle("on", settings.rec);
  rb.onclick = function () {
    settings.rec = !settings.rec; saveSettings();
    rb.classList.toggle("on", settings.rec);
    maskMap = {};
    renderPool(); refreshAll();
    alert(settings.rec ? "录屏模式已开：全部股票名称与代码已脱敏。" : "录屏模式已关。");
  };
  $("algoBtn").onclick = function () { switchTab("k"); setTimeout(function () { $("algoBox").open = true; $("algoBox").scrollIntoView(); }, 50); };
  // 底部导航
  document.querySelectorAll(".bottomnav button").forEach(function (b) { b.onclick = function () { switchTab(b.getAttribute("data-tab")); }; });
  // 周期
  document.querySelectorAll("#periodSeg button").forEach(function (b) {
    b.onclick = function () {
      if (b.disabled) { alert("该周期数据源不可用：" + (S.periodWhy[b.getAttribute("data-p")] || "")); return; }
      S.period = b.getAttribute("data-p");
      refreshAll();
    };
  });
  // 均线/布林
  document.querySelectorAll("#maSeg button").forEach(function (b) {
    b.onclick = function () {
      var k = b.getAttribute("data-m");
      if (k === "ma") settings.ma = !settings.ma; else settings.boll = !settings.boll;
      b.classList.toggle("on", k === "ma" ? settings.ma : settings.boll);
      saveSettings(); drawChart();
    };
  });
  $("maSeg").querySelector('[data-m="ma"]').classList.toggle("on", settings.ma);
  $("maSeg").querySelector('[data-m="boll"]').classList.toggle("on", settings.boll);
  // 副图
  document.querySelectorAll("#subSeg button").forEach(function (b) {
    b.onclick = function () {
      settings.sub = b.getAttribute("data-s"); saveSettings();
      document.querySelectorAll("#subSeg button").forEach(function (x) { x.classList.toggle("on", x === b); });
      drawChart();
    };
  });
  document.querySelectorAll("#subSeg button").forEach(function (x) { x.classList.toggle("on", x.getAttribute("data-s") === settings.sub); });
  // 平移 / 标注 / 刷新
  $("panL").onclick = function () { S.pan = Math.min(S.bars.length - chart.N, S.pan + 20); drawChart(); };
  $("panR").onclick = function () { S.pan = Math.max(0, S.pan - 20); drawChart(); };
  $("refreshBtn").onclick = function () { refreshAll(); };
  $("noteAdd").onclick = function () {
    S.annotateArm = !S.annotateArm;
    $("noteAdd").textContent = S.annotateArm ? "点图表位置…" : "＋ 复盘标注";
  };
  $("chart").addEventListener("click", chartTap);
  window.addEventListener("resize", function () { drawChart(); });
  // 对话
  $("chatSend").onclick = function () { ask($("chatIn").value); };
  $("chatIn").addEventListener("keydown", function (e) { if (e.key === "Enter") ask($("chatIn").value); });
  // 盯盘
  var monOn = $("monOn");
  monOn.checked = settings.monOn;
  monOn.onchange = function () { settings.monOn = monOn.checked; saveSettings(); renderMon(); };
  $("monAdd").onclick = function () {
    var px = parseFloat($("monPx").value), note = $("monNote").value.trim();
    if (!(px > 0)) { alert("请输入有效价格"); return; }
    S.monLevels.push({ code: S.code, price: px, note: note, hit: false });
    lsSet(LS.mon, S.monLevels);
    $("monPx").value = ""; $("monNote").value = "";
    renderMon();
  };
  setInterval(monTick, 30000);
  // 选股
  $("scanBtn").onclick = runScan;
  // 持仓
  $("pSave").onclick = function () {
    var code = $("pCode").value.trim();
    if (!/^\d{6}$/.test(code)) { alert("代码须为6位数字"); return; }
    var old = myPosition(code);
    var p = {
      code: code, name: codeName(code) === code ? "" : codeName(code),
      baseQty: parseInt($("pBase").value || "0", 10) || 0,
      tQty: parseInt($("pT").value || "0", 10) || 0,
      cost: parseFloat($("pCost").value || "0") || 0,
      stop: parseFloat($("pStop").value || "0") || 0
    };
    if (old && p.stop < old.stop && old.stop > 0) { alert("拒绝：新止损低于原止损。止损只允许上移。"); return; }
    if (old) S.ledger.positions.splice(S.ledger.positions.indexOf(old), 1);
    if (!p.name) {
      var q = S.quotes[marketCode(code)];
      p.name = (q && q.name) || code;
    }
    S.ledger.positions.push(p);
    lsSet(LS.ledger, S.ledger);
    renderPositions(); renderPool(); refreshQuotes();
    ["pCode", "pBase", "pT", "pCost", "pStop"].forEach(function (id) { $(id).value = ""; });
  };
  $("tradeParse").onclick = function () {
    var r = parseTrades($("tradeText").value);
    r.ok.forEach(function (t) { S.ledger.trades.push(t); });
    lsSet(LS.ledger, S.ledger);
    renderTrades();
    var msg = "成功解析 " + r.ok.length + " 条并记账。";
    if (r.fail.length) msg += "失败 " + r.fail.length + " 条：<br>" + r.fail.map(esc).join("<br>");
    $("tradeMsg").innerHTML = msg;
    $("tradeText").value = "";
  };
  // 启动
  renderPool(); renderPositions(); renderTrades(); renderMon();
  refreshAll();
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
})();
