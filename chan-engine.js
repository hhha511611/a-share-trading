/* chan-engine.js — 缠论结构引擎（固定规则版）
 * 纯逻辑，无 DOM 依赖；浏览器与 node 通用。
 * 规则与回测口径一致；与 108 课原文的偏离见 APP 内"算法说明"与 BUILD_NOTES.md。
 * 输入 bars: [{t:毫秒时间戳, o,h,l,c, v}]，按时间从旧到新。
 * 固定规则链：K线包含处理 → 分型 → 笔（相邻异类分型、极值K间隔≥5根；三态：已确认/待确认/形成中）
 * → 线段（"破坏"简化规则：反向笔跌破/升破前一同向笔起点则终结，至少3笔）
 * → 中枢（连续3段方向交替；已验证闭合/候选延伸）
 * → 背驰（MACD柱面积比较，操作化定义） → 三类买卖点（只从已确认结构发出）。
 */
(function (root) {
"use strict";

var D = {
  MIN_STROKE_GAP: 5,      // 笔：相邻异类分型极值 K 间隔 ≥5 根（处理后 K 线）
  DIV_FORCE_RATIO: 0.8,   // 背驰：后段力度 < 前段力度 × 0.8
  MACD_FAST: 12, MACD_SLOW: 26, MACD_SIG: 9
};

/* ---------- 工具 ---------- */
function emaArr(vals, n) {
  var k = 2 / (n + 1), out = [], e = null;
  for (var i = 0; i < vals.length; i++) {
    e = (e === null) ? vals[i] : vals[i] * k + e * (1 - k);
    out.push(e);
  }
  return out;
}
function smaArr(vals, n) {
  var out = [];
  for (var i = 0; i < vals.length; i++) {
    if (i < n - 1) { out.push(null); continue; }
    var s = 0;
    for (var j = i - n + 1; j <= i; j++) s += vals[j];
    out.push(s / n);
  }
  return out;
}
function stdev(vals, i, n, mean) {
  var s = 0;
  for (var j = i - n + 1; j <= i; j++) s += (vals[j] - mean) * (vals[j] - mean);
  return Math.sqrt(s / n);
}

/* ---------- 指标 ---------- */
function macd(closes) {
  var f = emaArr(closes, D.MACD_FAST), s = emaArr(closes, D.MACD_SLOW);
  var dif = closes.map(function (_, i) { return f[i] - s[i]; });
  var dea = emaArr(dif, D.MACD_SIG);
  var hist = dif.map(function (v, i) { return 2 * (v - dea[i]); });
  return { dif: dif, dea: dea, hist: hist };
}
function kdj(bars) {
  var K = [], Dd = [], J = [], k = 50, d = 50;
  for (var i = 0; i < bars.length; i++) {
    var s = Math.max(0, i - 8), hh = -Infinity, ll = Infinity, j;
    for (j = s; j <= i; j++) { hh = Math.max(hh, bars[j].h); ll = Math.min(ll, bars[j].l); }
    var rsv = (hh === ll) ? 50 : (bars[i].c - ll) / (hh - ll) * 100;
    k = (2 / 3) * k + (1 / 3) * rsv;
    d = (2 / 3) * d + (1 / 3) * k;
    K.push(k); Dd.push(d); J.push(3 * k - 2 * d);
  }
  return { k: K, d: Dd, j: J };
}
function rsi(closes, n) {
  n = n || 14;
  var out = [], g = 0, l = 0;
  for (var i = 0; i < closes.length; i++) {
    if (i === 0) { out.push(null); continue; }
    var ch = closes[i] - closes[i - 1];
    var gain = Math.max(ch, 0), loss = Math.max(-ch, 0);
    if (i <= n) { g += gain; l += loss; out.push(i < n ? null : 100 - 100 / (1 + g / Math.max(l, 1e-9))); }
    else { g = (g * (n - 1) + gain) / n; l = (l * (n - 1) + loss) / n; out.push(l === 0 ? 100 : 100 - 100 / (1 + g / l)); }
  }
  return out;
}

/* ---------- 1. 剔除未收盘 K 线 ---------- */
/* 规则：bar 的 [t, t+dur) 区间若与 now 有交叠（即 t+dur > now），视为正在形成中，不参与结构运算。 */
function dropForming(bars, periodMs, nowMs) {
  if (!bars.length) return { bars: [], dropped: false };
  var last = bars[bars.length - 1];
  if (last.t + periodMs > nowMs) return { bars: bars.slice(0, -1), dropped: true };
  return { bars: bars, dropped: false };
}

/* ---------- 2. K 线包含处理 ---------- */
function mergeBars(bars) {
  if (!bars.length) return [];
  var out = [{ t: bars[0].t, o: bars[0].o, h: bars[0].h, l: bars[0].l, c: bars[0].c, v: bars[0].v || 0, to: 0 }];
  var trend = bars.length > 1 ? (bars[1].c >= bars[0].c ? 1 : -1) : 1;
  for (var i = 1; i < bars.length; i++) {
    var p = out[out.length - 1], c = bars[i];
    var incl = (c.h <= p.h && c.l >= p.l) || (c.h >= p.h && c.l <= p.l);
    if (incl) {
      if (trend >= 0) out[out.length - 1] = { t: c.t, o: p.o, h: Math.max(p.h, c.h), l: Math.max(p.l, c.l), c: c.c, v: p.v + (c.v || 0), to: i };
      else out[out.length - 1] = { t: c.t, o: p.o, h: Math.min(p.h, c.h), l: Math.min(p.l, c.l), c: c.c, v: p.v + (c.v || 0), to: i };
    } else {
      if (c.h > p.h && c.l > p.l) trend = 1;
      else if (c.h < p.h && c.l < p.l) trend = -1;
      out.push({ t: c.t, o: c.o, h: c.h, l: c.l, c: c.c, v: c.v || 0, to: i });
    }
  }
  return out;
}

/* ---------- 3. 分型 ---------- */
function findFractals(mp) {
  var fs = [];
  for (var i = 1; i < mp.length - 1; i++) {
    var isTop = mp[i].h > mp[i - 1].h && mp[i].h > mp[i + 1].h;
    var isBot = mp[i].l < mp[i - 1].l && mp[i].l < mp[i + 1].l;
    if (isTop && !isBot) fs.push({ pi: i, type: "top", price: mp[i].h, t: mp[i].t, barTo: mp[i].to });
    else if (isBot && !isTop) fs.push({ pi: i, type: "bot", price: mp[i].l, t: mp[i].t, barTo: mp[i].to });
  }
  return fs;
}

/* ---------- 4. 笔（三态） ---------- */
function buildStrokes(fs, mp) {
  var strokes = [];
  var kept = [];
  fs.forEach(function (f) {
    var last = kept[kept.length - 1];
    if (!last) { kept.push(f); return; }
    if (f.type === last.type) {
      // 同类分型只保留更极端的（简化规则，已声明）
      if ((f.type === "top" && f.price > last.price) || (f.type === "bot" && f.price < last.price)) kept[kept.length - 1] = f;
      return;
    }
    if (f.pi - last.pi >= D.MIN_STROKE_GAP) { kept.push(f); }
    // 间距不足：丢弃后者（简化规则，已声明）
  });
  /* 待确认笔延伸：若最新分型之后价格突破该分型极值（新高/新低），
   * 则把最后一个分型更新为新极值点——待确认笔终点随之延伸，状态仍为待确认（会重绘）。
   * 已确认分型列表（findFractals 输出）不受影响；形成中笔随后从新极值重新计算。 */
  if (kept.length >= 2) {
    var _lf = kept[kept.length - 1], _ne = null, _nePi = -1, _p;
    for (_p = _lf.pi + 1; _p < mp.length; _p++) {
      if (_lf.type === "top") { if (_ne === null || mp[_p].h > _ne) { _ne = mp[_p].h; _nePi = _p; } }
      else { if (_ne === null || mp[_p].l < _ne) { _ne = mp[_p].l; _nePi = _p; } }
    }
    var _broke = _ne !== null && (_lf.type === "top" ? _ne > _lf.price : _ne < _lf.price);
    if (_broke) {
      kept[kept.length - 1] = { pi: _nePi, type: _lf.type, price: _ne, t: mp[_nePi].t, barTo: mp[_nePi].to, extended: true };
    }
  }
  for (var i = 0; i + 1 < kept.length; i++) {
    var a = kept[i], b = kept[i + 1];
    var dir = b.type === "top" ? 1 : -1; // 底→顶为向上
    strokes.push({
      from: a, to: b, dir: dir,
      hi: Math.max(a.price, b.price), lo: Math.min(a.price, b.price),
      state: (i === kept.length - 2) ? "pending" : "confirmed",
      rawFrom: Math.min(a.barTo, b.barTo), rawTo: Math.max(a.barTo, b.barTo)
    });
  }
  // 形成中笔：最后一个分型 → 之后处理后 K 线的极值
  var forming = null;
  if (kept.length && mp.length) {
    var lf = kept[kept.length - 1];
    var ext = null, extPi = -1;
    for (var p = lf.pi + 1; p < mp.length; p++) {
      if (lf.type === "top") { if (ext === null || mp[p].l < ext) { ext = mp[p].l; extPi = p; } }
      else { if (ext === null || mp[p].h > ext) { ext = mp[p].h; extPi = p; } }
    }
    if (ext !== null) {
      forming = {
        from: lf, to: { pi: extPi, type: lf.type === "top" ? "bot" : "top", price: ext, t: mp[extPi].t },
        dir: lf.type === "top" ? -1 : 1, hi: Math.max(lf.price, ext), lo: Math.min(lf.price, ext),
        state: "forming", rawFrom: lf.barTo, rawTo: mp[extPi].to
      };
    }
  }
  return { strokes: strokes, forming: forming };
}


/* ---------- 5. 线段（简化"破坏"规则） ----------
 * 规则：线段从某笔开始，方向=该笔方向；逐笔延续。
 * 反向笔（回抽）若跌破/升破"前一同向笔的起点"（上涨线段中向下笔的最低点 < 前一向上笔的最低点，
 * 下跌线段中向上笔的最高点 > 前一向下笔的最高点），则线段被破坏、在该笔终结，新线段从该笔开始。
 * 线段至少 3 笔：不足 3 笔时不终结（声明）。
 * 偏离声明：原文用特征序列+缺口规则判定线段终结，社区有三派分歧；此处为"线段破坏派"思想的操作化简化。 */
function buildSegments(strokes) {
  var segs = [], n = strokes.length;
  if (!n) return segs;
  var start = 0, dir = strokes[0].dir;
  for (var j = 1; j < n; j++) {
    var s = strokes[j];
    if (s.dir === dir) continue; // 同向笔延续
    var prev = strokes[j - 1];   // 前一同向笔
    var destroyed = dir === 1 ? (s.lo < prev.lo) : (s.hi > prev.hi);
    if (destroyed && (j - start + 1) >= 3) {
      segs.push(makeSeg(strokes, start, j, dir));
      start = j; dir = s.dir;
    }
  }
  segs.push(makeSeg(strokes, start, n - 1, dir));
  return segs;
}
function makeSeg(strokes, a, b, dir) {
  var hi = -Infinity, lo = Infinity, rf = Infinity, rt = -Infinity, idx = [];
  for (var k = a; k <= b; k++) {
    idx.push(k);
    hi = Math.max(hi, strokes[k].hi); lo = Math.min(lo, strokes[k].lo);
    rf = Math.min(rf, strokes[k].rawFrom); rt = Math.max(rt, strokes[k].rawTo);
  }
  return { dir: dir, hi: hi, lo: lo, idx: idx, rawFrom: rf, rawTo: rt };
}

/* ---------- 6. 中枢 ----------
 * 规则：连续 3 段方向交替（线段天然交替）且有价格重叠 → 中枢；
 * 上沿=三段最高价之最小，下沿=三段最低价之最大。
 * 延伸：后续线段未离开 [下沿, 上沿] 则并入同一中枢（区间不变，只延长时间）；
 * 离开：后续线段最高价>上沿 或 最低价<下沿 → 已验证闭合。
 * 线段不被相邻中枢复用：闭合后从离开段开始找下一个中枢；未闭合则后续皆为延伸，不再成新中枢。
 * 偏离声明：原文要求"次级别走势类型"递归，此处以线段近似（社区通行做法）。 */
function buildPivots(segs) {
  var ps = [], i = 0;
  while (i + 2 < segs.length) {
    var a = segs[i], b = segs[i + 1], c = segs[i + 2];
    if (a.dir === b.dir || b.dir === c.dir) { i++; continue; } // 防御：线段应天然交替
    var top = Math.min(a.hi, b.hi, c.hi), bot = Math.max(a.lo, b.lo, c.lo);
    if (!(top > bot)) { i++; continue; }
    var p = {
      top: top, bot: bot, segIdx: [i, i + 1, i + 2],
      rawFrom: Math.min(a.rawFrom, b.rawFrom, c.rawFrom),
      rawTo: Math.max(a.rawTo, b.rawTo, c.rawTo),
      state: "candidate", leaveSegIdx: -1
    };
    var j = i + 3, closed = false;
    while (j < segs.length) {
      var s = segs[j];
      if (s.hi > p.top || s.lo < p.bot) { closed = true; p.leaveSegIdx = j; break; }
      p.segIdx.push(j);
      p.rawFrom = Math.min(p.rawFrom, s.rawFrom);
      p.rawTo = Math.max(p.rawTo, s.rawTo);
      j++;
    }
    p.state = closed ? "closed" : "candidate";
    ps.push(p);
    if (!closed) break;
    i = p.leaveSegIdx;
  }
  return ps;
}

/* ---------- 7. 背驰（MACD 柱面积，操作化定义） ---------- */
function detectDivergence(segs, hist) {
  var out = [];
  for (var d = 1; d >= -1; d -= 2) {
    var list = [];
    segs.forEach(function (s, i) { if (s.dir === d) list.push(i); });
    if (list.length < 2) continue;
    var i1 = list[list.length - 2], i2 = list[list.length - 1];
    function force(s) {
      var f = 0;
      for (var r = s.rawFrom; r <= s.rawTo && r < hist.length; r++) f += Math.abs(hist[r] || 0);
      return f;
    }
    var f1 = force(segs[i1]), f2 = force(segs[i2]);
    if (f1 > 0 && f2 < f1 * D.DIV_FORCE_RATIO) {
      out.push({
        type: d === 1 ? "top" : "bottom", segIdx: i2, prevSegIdx: i1,
        ratio: f2 / f1, price: d === 1 ? segs[i2].hi : segs[i2].lo,
        t: null
      });
    }
  }
  return out;
}

/* ---------- 8. 三类买卖点（只从已确认结构出） ---------- */
function buildSignals(strokes, segs, pivots, divergences, bars) {
  var sigs = [];
  function push(type, t, price, state, note) {
    sigs.push({ type: type, t: t, price: price, state: state, note: note });
  }
  var confStrokes = strokes.filter(function (s) { return s.state === "confirmed"; });

  divergences.forEach(function (dv) {
    var seg = segs[dv.segIdx];
    var isBuy = dv.type === "bottom";
    // 一类：背驰段结束后出现反向已确认笔
    var after = strokes.filter(function (s) {
      return s.rawFrom >= seg.rawTo && s.dir === (isBuy ? 1 : -1);
    });
    var b1state = after.length && after[0].state === "confirmed" ? "confirmed" : "candidate";
    var b1price = isBuy ? seg.lo : seg.hi;
    var b1t = bars[Math.min(seg.rawTo, bars.length - 1)].t;
    push(isBuy ? "B1" : "S1", b1t, b1price, b1state,
      (isBuy ? "底背驰" : "顶背驰") + "，力度比 " + dv.ratio.toFixed(2));

    // 二类：一买后回抽不破极值 + 恢复（简化：找背驰段之后的第一组反向-同向笔）
    if (b1state === "confirmed") {
      var rev = null, rec = null;
      for (var i = 0; i < confStrokes.length; i++) {
        var s = confStrokes[i];
        if (s.rawFrom < seg.rawTo) continue;
        if (!rev && s.dir === (isBuy ? -1 : 1)) rev = s;
        else if (rev && s.dir === (isBuy ? 1 : -1)) { rec = s; break; }
      }
      if (rev && rec) {
        var okPx = isBuy ? rev.lo > b1price : rev.hi < b1price;
        push(isBuy ? "B2" : "S2", bars[Math.min(rev.rawTo, bars.length - 1)].t,
          isBuy ? rev.lo : rev.hi, okPx ? "confirmed" : "candidate",
          "回抽" + (okPx ? "未破一买极值" : "已破一买极值（失效）"));
      }
    }
  });

  // 三类：中枢 + 突破 + 回抽不回中枢 + 同向已确认笔恢复（简化）
  // 约束：突破须发生在离开段附近（离开段收尾后 20 根内），缺"恢复"则标候选
  pivots.forEach(function (pv) {
    if (pv.state !== "closed" || pv.leaveSegIdx < 0 || pv.leaveSegIdx >= segs.length) return;
    var scanEnd = Math.min(segs[pv.leaveSegIdx].rawTo + 20, bars.length - 1);
    var dir = null, r = -1;
    // 在离开段附近找第一根收盘站上/跌破的 K 线
    for (var rr = pv.rawTo + 1; rr <= scanEnd; rr++) {
      if (bars[rr].c > pv.top) { dir = 1; r = rr; break; }
      if (bars[rr].c < pv.bot) { dir = -1; r = rr; break; }
    }
    if (!dir) return;
    var edge = dir === 1 ? pv.top : pv.bot;
    var pullBack = null;
    for (var q = r + 1; q <= Math.min(r + 20, scanEnd); q++) {
      var inside = dir === 1 ? bars[q].l <= pv.top : bars[q].h >= pv.bot;
      if (inside) break; // 回到中枢 → 三类不成立
      var back = dir === 1 ? bars[q].l : bars[q].h;
      var okEdge = dir === 1 ? back > pv.top : back < pv.bot;
      if (okEdge) { pullBack = { r: q, px: back }; break; }
    }
    if (!pullBack) return;
    // 再恢复：中枢结束后第一根同向已确认笔，且其终点不早于回抽
    var rec = null;
    for (var si = 0; si < confStrokes.length; si++) {
      var sc = confStrokes[si];
      if (sc.rawFrom < pv.rawTo || sc.dir !== dir || sc.rawTo < pullBack.r) continue;
      rec = sc; break;
    }
    push(dir === 1 ? "B3" : "S3", bars[pullBack.r].t, pullBack.px,
      rec ? "confirmed" : "candidate",
      "回抽不回中枢（" + (dir === 1 ? "上沿" : "下沿") + edge.toFixed(2) + "）" +
      (rec ? "" : "，待同向已确认笔恢复"));
  });

  sigs.sort(function (a, b) { return a.t - b.t; });
  return sigs;
}

/* ---------- 主入口 ---------- */
function analyze(rawBars, periodMs, nowMs) {
  nowMs = nowMs || Date.now();
  var dropped = dropForming(rawBars, periodMs, nowMs);
  var bars = dropped.bars;
  var mp = mergeBars(bars);
  var fractals = findFractals(mp);
  var st = buildStrokes(fractals, mp);
  var segments = buildSegments(st.strokes);
  var pivots = buildPivots(segments);
  var closes = bars.map(function (b) { return b.c; });
  var m = macd(closes);
  var divergences = detectDivergence(segments, m.hist);
  // 背驰时间取背驰段末端
  divergences.forEach(function (dv) {
    var s = segments[dv.segIdx];
    dv.t = bars[Math.min(s.rawTo, bars.length - 1)].t;
  });
  var signals = buildSignals(st.strokes, segments, pivots, divergences, bars);

  var lastClose = bars.length ? bars[bars.length - 1].c : null;
  var lastPivot = pivots.length ? pivots[pivots.length - 1] : null;
  var lastSignal = signals.length ? signals[signals.length - 1] : null;
  return {
    nBars: bars.length, droppedLast: dropped.dropped,
    fractals: fractals, strokes: st.strokes, formingStroke: st.forming,
    segments: segments, pivots: pivots, divergences: divergences, signals: signals,
    lastClose: lastClose, lastPivot: lastPivot, lastSignal: lastSignal,
    macdHist: m.hist
  };
}

/* ---------- 给 AI 对话用的结构摘要 ---------- */
function summarize(a) {
  function px(v) { return v == null ? "—" : v.toFixed(2); }
  var lines = [];
  lines.push("K线" + a.nBars + "根" + (a.droppedLast ? "（已剔除未收盘K线）" : "") +
    "，分型" + a.fractals.length + "个，笔" + a.strokes.length + "笔" +
    "（已确认" + a.strokes.filter(function (s) { return s.state === "confirmed"; }).length +
    "/待确认" + a.strokes.filter(function (s) { return s.state === "pending"; }).length + ")" +
    "，线段" + a.segments.length + "段，中枢" + a.pivots.length + "个" +
    "（闭合" + a.pivots.filter(function (p) { return p.state === "closed"; }).length +
    "/候选中" + a.pivots.filter(function (p) { return p.state === "candidate"; }).length + ")" +
    "，背驰" + a.divergences.length + "处，买卖点" + a.signals.length + "个。");
  if (a.lastPivot) {
    lines.push("最近中枢：" + (a.lastPivot.state === "closed" ? "已验证闭合" : "候选延伸") +
      "，上沿" + px(a.lastPivot.top) + "，下沿" + px(a.lastPivot.bot) +
      "，现价" + px(a.lastClose) + (a.lastClose > a.lastPivot.top ? "（中枢上方）" : a.lastClose < a.lastPivot.bot ? "（中枢下方）" : "（中枢内）") + "。");
  }
  var conf = a.signals.filter(function (s) { return s.state === "confirmed"; });
  if (conf.length) {
    var s = conf[conf.length - 1];
    var nm = { B1: "一买", B2: "二买", B3: "三买", S1: "一卖", S2: "二卖", S3: "三卖" }[s.type] || s.type;
    lines.push("最近已确认信号：" + nm + "@" + px(s.price) + "（" + s.note + "）。");
  } else lines.push("当前无已确认买卖点信号。");
  var cands = a.signals.filter(function (s) { return s.state === "candidate"; });
  if (cands.length) lines.push("另有" + cands.length + "个候选信号（待确认结构，点位可能变动）。");
  return lines.join("\n");
}

var ChanEngine = {
  D: D, analyze: analyze, summarize: summarize,
  macd: macd, kdj: kdj, rsi: rsi, emaArr: emaArr, smaArr: smaArr,
  mergeBars: mergeBars, findFractals: findFractals, buildStrokes: buildStrokes,
  buildSegments: buildSegments, buildPivots: buildPivots
};
if (typeof module !== "undefined" && module.exports) module.exports = ChanEngine;
else root.ChanEngine = ChanEngine;
})(typeof window !== "undefined" ? window : global);
