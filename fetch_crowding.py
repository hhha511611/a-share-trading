#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日收盘后计算交易拥挤度附带表，写入 data/crowding.json。

供 GitHub Actions 每日 15:45（北京时间）运行；页面 crowding.html 同源读取该 JSON。
口径见 ~/workspace/notes/交易拥挤度监测口径.md（用户 2026-11-16 定为 16:16 扫描的常态化附带表，
观察口径，不进入 V1.1 执行层）。

指标（各取过去至多 250 个交易日滚动百分位，含当日；实际天数记在行 n 字段）：
  1. 换手率分位：同花顺 K 线 turnover 字段直接给出。
  2. 成交额占比分位：个股成交额 ÷（上证指数成交额 + 深证成指成交额），再取分位。
  3. 融资余额占比分位：v1 未接通两融数据通道，记 null（表里显示"—"，判定按其余两项）。
判定：≥2 项 ≥90% 记"拥挤"；仅 1 项 ≥90% 记"观察"；两项均 <90% 记"正常"；
可用指标不足 2 项记"数据不全"。百分位定义：≤ 当日值的天数 ÷ 窗口天数 × 100。
失败不覆盖旧文件：任一环节拿不到当日收盘数据时不写文件、退出码 0 并打印原因；
全量拉取失败时退出码 1（工作流不提交）。
数据源：同花顺 d.10jqka.com.cn v2 K 线（last.js + 目标年与上一年文件合并，
去重后取最近 250 日；年文件缺失时窗口缩短，n 如实记录）。单只 5 次重试
（45 秒超时，10/20/30/60 秒退避），第一遍失败的单只 30 秒后第二遍重拉；
仍失败则该行记 null。
补算过去某日：python fetch_crowding.py --date YYYY-MM-DD（同日行会被替换）。
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


def ths_code(code):
    """同花顺代码：上证指数 000001 -> 1A0001，其余沿用 6 位代码。"""
    return "1A0001" if code == "000001" else code


def _fetch_ths_chunk(code, chunk, attempts, backoff):
    """拉取同花顺一个 K 线分片（last 或年份），返回原始 data 字符串；失败抛错。"""
    url = "https://d.10jqka.com.cn/v2/line/hs_%s/01/%s.js" % (ths_code(code), chunk)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_err = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                text = r.read().decode("utf-8", "ignore")
            if not text or text.lstrip().startswith("<"):
                raise RuntimeError("返回 HTML 错误页（%s）" % chunk)
            start = text.find("(")
            end = text.rfind(")")
            if start < 0 or end <= start:
                raise RuntimeError("返回格式异常（%s）" % chunk)
            payload = json.loads(text[start + 1:end])
            data = payload.get("data")
            if not data:
                raise RuntimeError("分片无数据（%s）" % chunk)
            return data
        except Exception as e:  # noqa: BLE001 - retry then surface
            last_err = e
            if attempt < attempts - 1:
                wait = backoff[attempt] if attempt < len(backoff) else backoff[-1]
                print("WARN %s/%s 第 %d 次拉取失败: %s，%d 秒后重试" % (code, chunk, attempt + 1, e, wait))
                time.sleep(wait)
    raise RuntimeError("拉取 %s/%s 失败: %s" % (code, chunk, last_err))


def _parse_ths_rows(data):
    """解析分片 data 字符串，返回 {date: (close, volume, amount, turnover)}，date 为 YYYY-MM-DD。
    行格式：YYYYMMDD,开,高,低,收,成交量,成交额,换手率,..."""
    out = {}
    for line in data.split(";"):
        parts = line.split(",")
        if len(parts) < 8:
            continue
        try:
            raw_date = parts[0].strip()
            date = "%s-%s-%s" % (raw_date[0:4], raw_date[4:6], raw_date[6:8])
            close = float(parts[4])
            volume = float(parts[5])
            amount = float(parts[6])
            turnover = float(parts[7]) if parts[7].strip() != "" else None
        except (ValueError, IndexError):
            continue
        out[date] = (close, volume, amount, turnover)
    return out


def fetch_kline(code, today):
    """同花顺日 K：last.js + 目标年与上一年文件合并，去重排序。
    返回 [(date, close, pct, volume, amount, turnover)]，pct 由相邻收盘价算出。
    last.js 必取（5 次重试）；年文件尽力取，缺失只缩短窗口，不整只失败。"""
    year = int(today[:4])
    merged = {}
    merged.update(_parse_ths_rows(_fetch_ths_chunk(code, "last", 5, (10, 20, 30, 60))))
    for y in (year, year - 1):
        try:
            merged.update(_parse_ths_rows(_fetch_ths_chunk(code, str(y), 3, (5, 10, 20))))
        except Exception as e:  # noqa: BLE001 - 年文件失败只记警告
            print("WARN %s 年文件 %d 未取到（窗口缩短）: %s" % (code, y, e))
    dates = sorted(merged)
    out = []
    prev_close = None
    for date in dates:
        close, volume, amount, turnover = merged[date]
        pct = 0.0 if prev_close is None or prev_close == 0 else (close / prev_close - 1.0) * 100.0
        out.append((date, close, pct, volume, amount, turnover))
        prev_close = close
    if not out:
        raise RuntimeError("拉取 %s 无有效 K 线" % code)
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
    args = sys.argv[1:]
    if "--selftest" in args:
        selftest()
        return 0
    backfill = None
    if "--date" in args:
        backfill = args[args.index("--date") + 1]
        datetime.strptime(backfill, "%Y-%m-%d")  # 格式校验，非法则报错退出
    now = datetime.now(BJ)
    today = backfill or now.strftime("%Y-%m-%d")
    if backfill is None and (now.hour, now.minute) < CLOSE_GUARD_HHMM:
        print("未到收盘后 15:05（北京时间 %s），不计算。" % now.strftime("%H:%M"))
        return 0
    base = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(base, "data", "crowding.json")

    stock_series = {}
    for code, _ in STOCKS:
        try:
            stock_series[code] = fetch_kline(code, today)
        except Exception as e:  # noqa: BLE001 - 单只失败不阻塞整表
            print("WARN %s: %s" % (code, e))
            stock_series[code] = []
        time.sleep(2)  # 错峰，避免连续请求被限流
    # 第二遍：第一遍失败的单只在暂停后重拉一次（数据源间歇性不可达）
    failed = [c for c, s in stock_series.items() if not s]
    if failed:
        print("第一遍失败 %d 只，30 秒后第二遍重拉：%s" % (len(failed), ",".join(failed)))
        time.sleep(30)
        for code in failed:
            try:
                stock_series[code] = fetch_kline(code, today)
            except Exception as e:  # noqa: BLE001
                print("WARN %s 第二遍仍失败: %s" % (code, e))
    if not any(stock_series.values()):
        print("ERROR: 12 只全部拉取失败，不写文件。")
        return 1
    # 截断到目标日期（--date 补算过去某日时，窗口以该日收盘为准）
    stock_series = {c: [d for d in s if d[0] <= today] for c, s in stock_series.items()}
    if not any(s and s[-1][0] == today for s in stock_series.values()):
        print("目标日期 %s 的收盘数据未取到，不写文件。" % today)
        return 0

    index_amounts = {}
    for code, _ in INDICES:
        try:
            for date, _c, _p, _v, amount, _t in fetch_kline(code, today):
                if date <= today:
                    index_amounts[date] = index_amounts.get(date, 0.0) + amount
        except Exception as e:  # noqa: BLE001 - 指数失败则指标 2 记 null
            print("WARN 指数 %s: %s" % (code, e))
        time.sleep(2)

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
    data["days"].sort(key=lambda d: d["date"])
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
