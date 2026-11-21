#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日级 TRIN（Arms Index）抓取：东财全市场快照 clist 接口。

只做场外观察，不进入 CAN SLIM V1.1 执行层，不构成买卖建议，不预测价格。
数据源：东方财富 push2.eastmoney.com/api/qt/clist/get（全 A 股，f3 涨跌幅、f5 成交量、f6 成交额）。
全市场约 5565 只，每页固定返回 100 条（pz 参数无效，须分页拉取），顺序拉取并加重试
（并发下偶发空页，故不用并发；空页/失败页重试 3 次，退避 5/10/20 秒）。
字段口径：
  上涨 f3>0，下跌 f3<0；平盘 f3==0 且有成交；停牌以 f5==0 且 f6==0 判定（无成交）。
  TRIN(量) = (上涨家数/下跌家数) / (上涨成交量/下跌成交量)
  TRIN(额) = (上涨家数/下跌家数) / (上涨成交额/下跌成交额)
  两口径分歧时以成交额口径为准（页面/小表只以成交额口径作主读数）。
  涨跌停家数按板块阈值计：创业板 300/301 与科创板 688 为 20%（ST 亦然）、
  主板 ST 为 5%、其余主板 10%（阈值减 0.1 个百分点容忍价格取整）；
  北交所（30%）不在本宇宙。旧口径 f3>=9.9 一刀切会把创业/科创 10%–20% 的
  涨跌计入涨跌停，2026-11-20 实测会多计约百只，不可用。
样本检查：上涨+下跌家数与（全 A 总数-停牌数）偏差 >5% 时跳过本轮并标记样本异常。
分位：100 日分位需 >=80 个有效日、250 日分位需 >=200 个有效日，否则记 null 并标注
“分位仅供参考”。原型上线首日无历史，分位为 null。
3σ 检查只用于 TRIN 本身，只标记、不自动跳过。
失败不覆盖旧文件：当日快照拉不全时不写文件，退出码 0 并打印原因；全部失败退出码 1。
用法：python3 fetch_trin.py [--date YYYY-MM-DD] [--selftest]
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

BJ = timezone(timedelta(hours=8))
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, "data", "trin.json")
KEEP_DAYS = 365  # 历史保留 1 年（分位窗口上限 250 日）
CLOSE_GUARD_HHMM = (15, 5)

CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
# 全 A 股：深主板 m:0+t:6、创业板 m:0+t:80、沪主板 m:1+t:2、科创板 m:1+t:23
FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
FIELDS = "f12,f14,f3,f5,f6"
PAGE_SIZE = 100
# 注意：必须按 fid=f12（代码）排序拉取。按 fid=f3（实时涨跌幅）拉取时价格在分页间
# 变动，行会跨页重复/丢失（2026-11-20 实测：5565 行仅 5123 个唯一代码）。ut 为东财固定参数。
UT = "bd1d9ddb04089700cf9c27f6f742628"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Referer": "https://quote.eastmoney.com/"}


def _num(v):
    """clist 数值归一化：'-'（停牌等无数据）-> None，其余转 float。"""
    if v is None or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pct_rank(series, value):
    """百分位 = <= 当日值的天数 / 总天数 * 100，保留 1 位小数。"""
    if not series or value is None:
        return None
    return round(100.0 * sum(1 for v in series if v <= value) / len(series), 1)


def fetch_page(session, pn, attempts=5, backoff=(5, 15, 30, 60)):
    """拉一页 clist；空页/失败重试。返回 (total, diff list)。"""
    params = {"fid": "f12", "po": "1", "pz": str(PAGE_SIZE), "pn": str(pn),
              "np": "1", "fltt": "2", "invt": "2", "fs": FS, "fields": FIELDS,
              "ut": UT}
    last_err = None
    for i in range(attempts):
        try:
            r = session.get(CLIST_URL, params=params, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                raise RuntimeError("HTTP %d" % r.status_code)
            payload = r.json()
            if payload.get("rc") != 0 or not isinstance(payload.get("data"), dict):
                raise RuntimeError("rc=%s data 为空" % payload.get("rc"))
            data = payload["data"]
            diff = data.get("diff") or []
            if not diff:
                raise RuntimeError("空页（pn=%d）" % pn)
            return int(data.get("total") or 0), diff
        except Exception as e:  # noqa: BLE001 - retry then surface
            last_err = e
            if i < attempts - 1:
                wait = backoff[i] if i < len(backoff) else backoff[-1]
                print("WARN clist 第 %d 页第 %d 次失败: %s，%d 秒后重试"
                      % (pn, i + 1, e, wait))
                time.sleep(wait)
    raise RuntimeError("clist 第 %d 页拉取失败: %s" % (pn, last_err))


def fetch_snapshot():
    """顺序分页拉全市场快照；失败页 60 秒后整轮重拉一次。返回 rows 与 total。"""
    session = requests.Session()
    total, first = fetch_page(session, 1)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    collected = {1: first}
    failed = []
    for pn in range(2, pages + 1):
        try:
            _t, diff = fetch_page(session, pn)
            collected[pn] = diff
        except Exception as e:  # noqa: BLE001 - 记下失败页，第二轮重拉
            print("WARN clist 第 %d 页第一轮失败: %s" % (pn, e))
            failed.append(pn)
        time.sleep(0.5)  # 错峰
    if failed:
        print("第一轮失败 %d 页，60 秒后第二轮重拉：%s" % (len(failed), failed))
        time.sleep(60)
        still = []
        for pn in failed:
            try:
                _t, diff = fetch_page(session, pn)
                collected[pn] = diff
            except Exception as e:  # noqa: BLE001
                print("WARN clist 第 %d 页第二轮仍失败: %s" % (pn, e))
                still.append(pn)
            time.sleep(0.5)
        if still:
            raise RuntimeError("clist 以下页始终拉不到: %s" % still)
    rows = []
    for pn in range(1, pages + 1):
        rows.extend(collected[pn])
    return total, rows


def limit_threshold(code, name):
    """涨跌停阈值（%）：创业板 300/301 与科创板 688 为 20%（ST 亦然）；
    主板 ST 为 5%；其余主板 10%。北交所（30%）不在本宇宙。"""
    code = (code or "")
    if code.startswith(("300", "301", "688")):
        return 20.0
    if "ST" in (name or ""):
        return 5.0
    return 10.0


def compute(rows):
    """由快照行算 TRIN 两口径与辅助计数。返回 dict。
    停牌判定：f5==0 且 f6==0（无成交）。另记明细以备核对：
    dash_all（f3/f5/f6 全为 '-'）、f3_num_f5_zero（有涨跌幅但无成交量）。"""
    up = down = flat = suspended = 0
    up_vol = down_vol = up_amt = down_amt = 0.0
    limit_up = limit_down = up_ge5 = 0
    n_no_pct = 0
    n_dash_all = n_f3_num_f5_zero = 0
    for r in rows:
        raw_f3, raw_f5, raw_f6 = r.get("f3"), r.get("f5"), r.get("f6")
        f3, f5, f6 = _num(raw_f3), _num(raw_f5), _num(raw_f6)
        if f5 is None:
            f5 = 0
        if f6 is None:
            f6 = 0
        if f5 == 0 and f6 == 0:
            suspended += 1
            if raw_f3 == "-" and raw_f5 == "-" and raw_f6 == "-":
                n_dash_all += 1
            else:
                n_f3_num_f5_zero += 1
            continue
        if f3 is None:
            n_no_pct += 1
            continue
        if f3 > 0:
            up += 1
            up_vol += f5
            up_amt += f6
            thr = limit_threshold(r.get("f12"), r.get("f14"))
            if f3 >= thr - 0.1:
                limit_up += 1
            if f3 >= 5.0:
                up_ge5 += 1
        elif f3 < 0:
            down += 1
            down_vol += f5
            down_amt += f6
            thr = limit_threshold(r.get("f12"), r.get("f14"))
            if f3 <= -(thr - 0.1):
                limit_down += 1
        else:
            flat += 1
    trin_vol = trin_amt = None
    if down > 0 and down_vol > 0 and up_vol > 0:
        trin_vol = round((up / down) / (up_vol / down_vol), 4)
    if down > 0 and down_amt > 0 and up_amt > 0:
        trin_amt = round((up / down) / (up_amt / down_amt), 4)
    return {
        "total": len(rows), "up": up, "down": down, "flat": flat,
        "suspended": suspended, "no_pct": n_no_pct,
        "suspended_dash_all": n_dash_all,
        "suspended_f3_num_f5_zero": n_f3_num_f5_zero,
        "up_vol": up_vol, "down_vol": down_vol,
        "up_amt": round(up_amt, 2), "down_amt": round(down_amt, 2),
        "limit_up": limit_up, "limit_down": limit_down, "up_ge5pct": up_ge5,
        "trin_vol": trin_vol, "trin_amt": trin_amt,
        # 主读数：成交额口径（两口径分歧时以此为准）
        "trin": trin_amt,
    }


def load_history():
    if not os.path.exists(OUT_PATH):
        return {"version": 1, "indicator": "TRIN", "days": []}
    with open(OUT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def selftest():
    # 构造 100 只：60 涨（量 100/只）、40 跌（量 200/只）
    # TRIN = (60/40)/(6000/8000) = 1.5/0.75 = 2.0
    rows = ([{"f3": 1.0, "f5": 100, "f6": 1000}] * 60
            + [{"f3": -1.0, "f5": 200, "f6": 2000}] * 40)
    c = compute(rows)
    assert c["up"] == 60 and c["down"] == 40, c
    assert c["trin_vol"] == 2.0 and c["trin_amt"] == 2.0, c
    assert c["trin"] == 2.0
    # 停牌行（无成交）不计入涨跌
    rows2 = rows + [{"f3": 0.0, "f5": 0, "f6": 0}]
    assert compute(rows2)["suspended"] == 1
    # 平盘行（有成交、f3==0）计 flat
    rows3 = rows + [{"f3": 0.0, "f5": 10, "f6": 100}]
    assert compute(rows3)["flat"] == 1
    # 无下跌家数 -> TRIN 为 null，不抛错
    assert compute([{"f3": 1.0, "f5": 10, "f6": 100}])["trin"] is None
    assert pct_rank([1.0, 2.0, 3.0], 2.0) == 66.7
    assert pct_rank([], 1.0) is None
    # 板块涨跌停阈值：创业 20%、主板 ST 5%、主板 10%
    assert limit_threshold("300001", "XX科技") == 20.0
    assert limit_threshold("688001", "XX科技") == 20.0
    assert limit_threshold("300001", "ST XX") == 20.0
    assert limit_threshold("600001", "ST XX") == 5.0
    assert limit_threshold("600001", "XX银行") == 10.0
    lim = compute([{"f12": "300001", "f14": "A", "f3": 12.0, "f5": 10, "f6": 100},
                   {"f12": "600001", "f14": "B", "f3": 10.0, "f5": 10, "f6": 100},
                   {"f12": "600002", "f14": "ST C", "f3": 5.0, "f5": 10, "f6": 100},
                   {"f12": "600003", "f14": "D", "f3": -1.0, "f5": 10, "f6": 100}])
    assert lim["limit_up"] == 2, lim  # 创业 12% 不算涨停；主板 10% 与 ST 5% 算
    assert lim["up"] == 3 and lim["down"] == 1
    print("selftest PASS")


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        selftest()
        return 0
    backfill = None
    if "--date" in args:
        backfill = args[args.index("--date") + 1]
        datetime.strptime(backfill, "%Y-%m-%d")
    now = datetime.now(BJ)
    today = backfill or now.strftime("%Y-%m-%d")
    if backfill is None and (now.hour, now.minute) < CLOSE_GUARD_HHMM:
        print("未到收盘后 15:05（北京时间 %s），不计算。" % now.strftime("%H:%M"))
        return 0

    try:
        total, rows = fetch_snapshot()
    except Exception as e:  # noqa: BLE001
        print("ERROR: 全市场快照拉取失败: %s" % e)
        return 1
    c = compute(rows)

    # 样本检查：上涨+下跌 vs（总数-停牌），偏差 >5% 跳过本轮
    denom = c["total"] - c["suspended"]
    covered = c["up"] + c["down"]
    flags = []
    if denom > 0:
        dev = abs(covered - denom) / denom
        if dev > 0.05:
            print("ERROR: 样本异常：上涨+下跌 %d vs 总数-停牌 %d，偏差 %.1f%% > 5%%，跳过本轮"
                  % (covered, denom, dev * 100))
            return 0
    if c["trin"] is None:
        print("ERROR: 下跌家数为 0 或成交额缺失，TRIN 无法计算，记无信号，不写文件。")
        return 0

    hist = load_history()
    days = [d for d in hist.get("days", []) if d.get("date") != today]
    series_amt = [d["value"] for d in days if d.get("value") is not None]
    series_vol = [d.get("value_vol") for d in days if d.get("value_vol") is not None]
    # 分位最小有效天数：100 日分位需 >=80 有效日，否则 null
    pct100 = pct_rank(series_amt[-100:], c["trin"]) if len(series_amt) >= 80 else None
    pct250 = pct_rank(series_amt[-250:], c["trin"]) if len(series_amt) >= 200 else None
    # 10 日均值（积累日起；窗口不足标注）
    window10 = series_amt[-9:] + [c["trin"]]
    ma10 = round(sum(window10) / len(window10), 4)
    if len(window10) < 10:
        flags.append("10日均值窗口不足（仅 %d 日）" % len(window10))
    if pct100 is None:
        flags.append("分位仅供参考（有效历史 %d 日 < 80 日）" % len(series_amt))
    # 3σ 检查（只标记）
    if len(series_amt) >= 20:
        mean = sum(series_amt) / len(series_amt)
        sd = math.sqrt(sum((v - mean) ** 2 for v in series_amt) / len(series_amt))
        if sd > 0 and abs(c["trin"] - mean) > 3 * sd:
            flags.append("3σ 极端值（均值 %.3f，标准差 %.3f）" % (mean, sd))
    # 极端提示仅在滚动分位 <5% 或 >95% 时输出
    extreme = None
    if pct100 is not None and (pct100 < 5 or pct100 > 95):
        extreme = "TRIN(额) 100日分位 %.1f%%，极端读数" % pct100
    if c["trin_vol"] is not None and c["trin_amt"] is not None:
        rel = abs(c["trin_vol"] - c["trin_amt"]) / max(c["trin_amt"], 1e-9)
        if rel > 0.2:
            flags.append("两口径分歧 %.1f%%（量 %.4f vs 额 %.4f），以成交额为准"
                         % (rel * 100, c["trin_vol"], c["trin_amt"]))

    day = {
        "timestamp": today,
        "date": today,
        "indicator": "TRIN",
        "value": c["trin"],               # 主读数：成交额口径
        "value_vol": c["trin_vol"],       # 成交量口径
        "percentile": pct100,             # 100 日分位（主分位）
        "percentile_250": pct250,
        "ma10": ma10,
        "ma10_n": len(window10),
        "up": c["up"], "down": c["down"], "flat": c["flat"],
        "suspended": c["suspended"], "total": c["total"],
        "suspended_dash_all": c["suspended_dash_all"],
        "suspended_f3_num_f5_zero": c["suspended_f3_num_f5_zero"],
        "up_vol": c["up_vol"], "down_vol": c["down_vol"],
        "up_amt": c["up_amt"], "down_amt": c["down_amt"],
        "limit_up": c["limit_up"], "limit_down": c["limit_down"],
        "up_ge5pct": c["up_ge5pct"],
        "extreme_note": extreme,
        "data_quality_flag": "；".join(flags) if flags else "ok",
    }
    days.append(day)
    days.sort(key=lambda d: d["date"])
    hist["days"] = days[-KEEP_DAYS:]
    hist["updated"] = today
    hist["generated_at_beijing"] = now.strftime("%Y-%m-%d %H:%M")
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT_PATH)
    print("wrote %s: TRIN(额)=%.4f TRIN(量)=%s 上涨 %d / 下跌 %d / 平盘 %d / 停牌 %d，"
          "涨停 %d 跌停 %d，10日均值 %.4f（n=%d），100日分位 %s，标志：%s"
          % (today, c["trin"], c["trin_vol"], c["up"], c["down"], c["flat"],
             c["suspended"], c["limit_up"], c["limit_down"], ma10, len(window10),
             pct100, day["data_quality_flag"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
