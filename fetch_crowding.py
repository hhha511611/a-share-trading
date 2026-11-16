#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日收盘后计算交易拥挤度附带表，写入 data/crowding.json。

供 GitHub Actions 每日 15:45（北京时间）运行；页面 crowding.html 同源读取该 JSON。
口径见 ~/workspace/notes/交易拥挤度监测口径.md（用户 2026-11-16 定为 16:16 扫描的常态化附带表，
观察口径，不进入 V1.1 执行层）。

指标（各取过去 250 个交易日滚动百分位，含当日）：
  1. 换手率分位：东财 K 线 turnover 字段直接给出。
  2. 成交额占比分位：个股成交额 ÷（上证指数成交额 + 深证成指成交额），再取分位。
  3. 融资余额占比分位：v1 未接通两融数据通道，记 null（表里显示"—"，判定按其余两项）。
判定：≥2 项 ≥90% 记"拥挤"；仅 1 项 ≥90% 记"观察"；两项均 <90% 记"正常"；
可用指标不足 2 项记"数据不全"。百分位定义：≤ 当日值的天数 ÷ 窗口天数 × 100。
失败不覆盖旧文件：任一环节拿不到当日收盘数据时不写文件、退出码 0 并打印原因；
全量拉取失败时退出码 1（工作流不提交）。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BJ = timezone(timedelta(hours=8))

# 12 只：8 只 V1.1 扫描池 + 4 只光模块（本表的起因案例），固定名单，改名单须用户定。
STOCKS = [
    ("601689", "拓普集团"), ("601100", "恒立液压"), ("603119", "浙江荣泰"),
    ("688017", "绿的谐波"), ("603893", "瑞芯微"), ("688018", "乐鑫科技"),
    ("688099", "晶晨股份"), ("688521", "芯原股份"),
    ("300308", "中际旭创"), ("300502", "新易盛"), ("300394", "天孚通信"),
    ("002463", "沪电股份"),
]
INDICES = [("000001", "上证指数"), ("399001", "深证成指")]

WINDOW = 250
MIN_DAYS = 200
KEEP_DAYS = 60
CLOSE_GUARD_HHMM = (15, 5)  # 北京时间 15:05 前不计算（防盘中误触发）


def secid(code):
    return ("1" if code[0] in "69" else "0") + "." + code


def fetch_kline(code, beg):
    """东财 push2his 日 K（前复权）。返回 [(date, close, pct, volume, amount, turnover)]，date 为 YYYY-MM-DD。"""
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        "?secid=%s&klt=101&fqt=1&beg=%s&end=20500101"
        "&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        % (secid(code), beg)
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = json.loads(r.read().decode("utf-8", "ignore"))
            break
        except Exception as e:  # noqa: BLE001 - retry then surface
            last_err = e
            time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError("拉取 %s 失败: %s" % (code, last_err))
    if payload.get("rc") != 0 or not payload.get("data") or not payload["data"].get("klines"):
        raise RuntimeError("拉取 %s 返回异常 rc=%s" % (code, payload.get("rc")))
    out = []
    for line in payload["data"]["klines"]:
        parts = line.split(",")
        if len(parts) < 7:
            continue
        try:
            date = parts[0]
            close = float(parts[2])
            pct = float(parts[8])
            volume = float(parts[5])
            amount = float(parts[6])
            turnover = float(parts[10]) if len(parts) > 10 and parts[10] != "" else None
        except ValueError:
            continue
        out.append((date, close, pct, volume, amount, turnover))
    out.sort(key=lambda x: x[0])
    return out


def pct_rank(series, today_value):
    """百分位 = ≤ 当日值的天数 ÷ 总天数 × 100，保留 1 位小数。"""
    n = len(series)
    if n == 0 or today_value is None:
        return None
    return round(100.0 * sum(1 for v in series if v <= today_value) / n, 1)


def compute_day(stock_series, index_amounts, today):
    """stock_series: {code: [(date, close, pct, volume, amount, turnover)]}（已取窗口内）。
    index_amounts: {date: 上证+深证当日成交额}。返回当日 rows。"""
    rows = []
    for code, name in STOCKS:
        series = stock_series.get(code, [])
        row = {"code": code, "name": name, "close": None, "pct": None,
               "turnover": None, "turnover_pct": None,
               "amount": None, "amount_share": None, "amount_share_pct": None,
               "margin_pct": None, "margin_date": None,
               "status": "数据不全", "n": 0, "note": ""}
        if not series or series[-1][0] != today:
            row["note"] = "当日 K 线未取到"
            rows.append(row)
            continue
        window = series[-WINDOW:]
        n = len(window)
        row["n"] = n
        last = window[-1]
        row["close"] = round(last[1], 2)
        row["pct"] = round(last[2], 2)
        row["amount"] = round(last[4] / 1e8, 2)  # 亿元
        notes = []
        # 指标 1：换手率分位
        turns = [d[5] for d in window if d[5] is not None]
        if len(turns) >= MIN_DAYS and last[5] is not None:
            row["turnover"] = round(last[5], 2)
            row["turnover_pct"] = pct_rank(turns, last[5])
        else:
            notes.append("换手历史不足")
        # 指标 2：成交额占比分位
        ratios = []
        for d in window:
            total = index_amounts.get(d[0])
            if total and total > 0:
                ratios.append(d[4] / total)
        total_today = index_amounts.get(today)
        if len(ratios) >= MIN_DAYS and total_today and total_today > 0:
            ratio_today = last[4] / total_today
            row["amount_share"] = round(100.0 * ratio_today, 2)
            row["amount_share_pct"] = pct_rank(ratios, ratio_today)
        else:
            notes.append("市场总量历史不足")
        # 指标 3：融资分位（v1 未接通）
        notes.append("融资分位待接通")
        # 判定
        avail = [v for v in (row["turnover_pct"], row["amount_share_pct"]) if v is not None]
        if len(avail) < 2:
            row["status"] = "数据不全"
        else:
            ge90 = sum(1 for v in avail if v >= 90)
            if ge90 >= 2:
                row["status"] = "拥挤"
            elif ge90 == 1:
                row["status"] = "观察"
            else:
                row["status"] = "正常"
        if row["turnover_pct"] is not None and row["turnover_pct"] >= 95:
            notes.append("换手≥95%极端")
        row["note"] = "；".join(notes)
        rows.append(row)
    return rows


def selftest():
    """不联网的逻辑自检：百分位与判定。"""
    # 250 天换手线性上升，当日为最大值 -> 100.0；当日为中位数 -> 约 50。
    series = [float(i) for i in range(1, 251)]
    assert pct_rank(series, 250.0) == 100.0, pct_rank(series, 250.0)
    assert abs(pct_rank(series, 125.0) - 50.0) < 0.5, pct_rank(series, 125.0)
    # 构造一日数据：换手与占比双双 ≥90 -> 拥挤
    today = "2026-11-16"
    dates = ["2026-%02d-%02d" % (m, d) for m in range(1, 12) for d in range(1, 24)][:250]
    dates[-1] = today
    fake = {}
    for code, _ in STOCKS:
        rows = []
        for i, dt in enumerate(dates):
            rows.append((dt, 10.0, 0.0, 100.0, 1e8 * (i + 1), 0.1 * (i + 1)))
        fake[code] = rows
    idx = {dt: 1e12 for dt in dates}
    day_rows = compute_day(fake, idx, today)
    assert all(r["status"] == "拥挤" for r in day_rows), [r["status"] for r in day_rows]
    assert all(r["turnover_pct"] == 100.0 for r in day_rows)
    # 当日 K 线缺失 -> 数据不全
    fake2 = {code: rows[:-1] for code, rows in fake.items()}
    day_rows2 = compute_day(fake2, idx, today)
    assert all(r["status"] == "数据不全" for r in day_rows2)
    print("selftest PASS")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
        return 0
    now = datetime.now(BJ)
    if (now.hour, now.minute) < CLOSE_GUARD_HHMM:
        print("未到收盘后 15:05（北京时间 %s），不计算。" % now.strftime("%H:%M"))
        return 0
    today = now.strftime("%Y-%m-%d")
    beg = (now - timedelta(days=420)).strftime("%Y%m%d")
    base = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(base, "data", "crowding.json")

    stock_series = {}
    for code, _ in STOCKS:
        try:
            stock_series[code] = fetch_kline(code, beg)
        except Exception as e:  # noqa: BLE001 - 单只失败不阻塞整表
            print("WARN %s: %s" % (code, e))
            stock_series[code] = []
    if not any(stock_series.values()):
        print("ERROR: 12 只全部拉取失败，不写文件。")
        return 1
    latest = max((s[-1][0] for s in stock_series.values() if s), default=None)
    if latest != today:
        print("最新 K 线日期为 %s，今日（%s）收盘数据未更新，不写文件。" % (latest, today))
        return 0

    index_amounts = {}
    for code, _ in INDICES:
        try:
            for date, _c, _p, _v, amount, _t in fetch_kline(code, beg):
                index_amounts[date] = index_amounts.get(date, 0.0) + amount
        except Exception as e:  # noqa: BLE001 - 指数失败则指标 2 记 null
            print("WARN 指数 %s: %s" % (code, e))

    rows = compute_day(stock_series, index_amounts, today)

    data = {"version": 1, "window_days": WINDOW,
            "stocks": [{"code": c, "name": n} for c, n in STOCKS],
            "days": []}
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                prev = json.load(f)
            if isinstance(prev.get("days"), list):
                data["days"] = [d for d in prev["days"] if d.get("date") != today]
        except (json.JSONDecodeError, OSError) as e:
            print("WARN 旧文件读取失败，按空历史处理: %s" % e)
    data["days"].append({"date": today, "rows": rows})
    data["days"] = data["days"][-KEEP_DAYS:]
    data["updated"] = today
    data["generated_at_beijing"] = now.strftime("%Y-%m-%d %H:%M")
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp_path, out_path)
    crowded = [r["name"] for r in rows if r["status"] == "拥挤"]
    print("wrote %s: %d 只，拥挤 %d 只（%s）" % (today, len(rows), len(crowded), "、".join(crowded) or "无"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
