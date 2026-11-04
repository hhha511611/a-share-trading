#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日拉取 8 只验证池 + 3 大指数的日 K 线（新浪前复权口径），写入 data/klines.json。
供 GitHub Actions 每日收盘后运行；页面优先读同目录的 klines.json（同源，无 CORS 问题），
新浪实时接口仅作备用。失败的标的不覆盖旧数据。"""
import json, os, re, sys, urllib.request
from datetime import datetime, timezone, timedelta

STOCKS = ["601689", "601100", "603119", "688017", "603893", "688018", "688099", "688521"]
INDEXES = {"sh000001": "sh000001", "sz399001": "sz399001", "sz399006": "sz399006"}

def sina_sym(code):
    c = code[0]
    if c in "659":
        return "sh" + code
    if c in "48":
        return "bj" + code
    return "sz" + code

def fetch_kline(symbol, datalen=350):
    url = ("https://quotes.sina.cn/cn/api/jsonp.php/var=kl/CN_MarketDataService.getKLineData"
           "?symbol=%s&scale=240&ma=no&datalen=%d" % (symbol, datalen))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode("utf-8", "ignore")
    m = re.search(r"var=kl\((.*)\);?\s*$", txt, re.S)
    if not m:
        return []
    rows = json.loads(m.group(1))
    out = []
    for d in rows:
        try:
            out.append([d["day"], float(d["open"]), float(d["high"]),
                        float(d["low"]), float(d["close"]), float(d["volume"])])
        except (KeyError, ValueError, TypeError):
            continue
    return out

def main():
    base = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(base, "data", "klines.json")
    old = {}
    if os.path.exists(data_path):
        try:
            old = json.load(open(data_path, encoding="utf-8"))
        except Exception:
            old = {}
    bj = timezone(timedelta(hours=8))
    today = datetime.now(bj).strftime("%Y-%m-%d")
    stocks = dict((old.get("stocks") or {}))
    indexes = dict((old.get("indexes") or {}))
    ok, fail = 0, []
    for code in STOCKS:
        try:
            rows = fetch_kline(sina_sym(code))
        except Exception:
            rows = []
        if len(rows) >= 60:
            stocks[code] = rows
            ok += 1
        else:
            fail.append(code)
    for key, sym in INDEXES.items():
        try:
            rows = fetch_kline(sym, 120)
        except Exception:
            rows = []
        if len(rows) >= 30:
            indexes[key] = rows
            ok += 1
        else:
            fail.append(key)
    os.makedirs(os.path.join(base, "data"), exist_ok=True)
    payload = {"date": today, "stocks": stocks, "indexes": indexes}
    json.dump(payload, open(data_path, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print("date=%s ok=%d fail=%s" % (today, ok, fail))
    # 至少 8 只股票中有 6 只有数据才算成功，避免把空文件提交上去
    if sum(1 for c in STOCKS if len(stocks.get(c, [])) >= 60) < 6:
        print("ERROR: 有效股票 K 线不足 6 只，保留旧文件", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
