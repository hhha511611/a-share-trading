#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""期指基差（中金所）日级抓取：IF/IH/IC/IM 主力合约。

只做场外观察，不进入 CAN SLIM V1.1 执行层，不构成买卖建议，不预测价格。
主数据源：中金所官网日统计 CSV（HTTP，HTTPS 不可用）：
  http://www.cffex.com.cn/sj/hqsj/rtj/{YYYYMM}/{DD}/{YYYYMMDD}_1.csv （GBK 编码）
  该文件含全部品种（含期权），只取合约代码匹配 ^(IF|IH|IC|IM)\\d{4}$ 的行；
  主力合约 = 同品种中持仓量最大者（须核实代码口径：新浪 IF0 类代码为主力连续，
  本脚本以中金所持仓量为准，不依赖连续合约代码）。
现货指数收盘价：腾讯 qt.gtimg.cn（sh000300 沪深300、sh000016 上证50、
sh000905 中证500、sh000852 中证1000），第一来源；东财 push2his 日线为第二来源
（secid 1.000300 等；注意中金所市场码为 220，113 是上期所——勿用错）。
  结算价的第二来源：中金所首页行情与日统计交叉核对（收盘价一致）；结算价本身
  只有中金所发布，脚本内不另找第二来源，README 中明说。
公式：基差 = F - S（F 为当日结算价）；基差率 = (F-S)/S；
  年化基差率 = 基差率 × 250 / 剩余交易日（2026-11-19 统一口径：原 ×365/剩余天数
  改为 ×250/剩余交易日，国内期货基差年化常用口径；原型未积累历史，改动不影响一致性）。
  剩余交易日 = 自次一交易日起至交割日（含交割日）的交易日数；交割日 = 交割月第 3 个周五。
  5 日变化方向：年化基差率绝对值缩小为收敛、扩大为扩大（需 5 日历史）。
口径标注：
  交割周（剩余交易日 <=5）：基差机械收敛，年化值不可靠，标注；
  主力换月：主力代码较上一日变化时标注跳变；
  5–7 月分红季：未做分红调整，置信度下降，标注；
  A 股持续贴水是常态：只看年化基差率的历史分位（窗口内百分位），不看绝对值。
分位：100 日分位需 >=80 个有效日、250 日分位需 >=200 个有效日，否则记 null 并标注
“分位仅供参考”。基差缺失时输出“无信号”，禁止用 ETF 折溢价顶替。
3σ 检查只用于基差率本身，只标记、不自动跳过。
失败不覆盖旧文件：当日 CSV 未发布（404）时不写文件、退出码 0 并打印原因；全部失败退出码 1。
用法：python3 fetch_basis.py [--date YYYY-MM-DD] [--csv PATH] [--selftest]
  --csv：用本地已下载的中金所日统计 CSV 跑（验证/补录用，须与 --date 同日）。
"""
import csv
import io
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

BJ = timezone(timedelta(hours=8))
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, "data", "basis.json")
KEEP_DAYS = 365
CLOSE_GUARD_HHMM = (15, 5)

# 品种 -> 现货指数（腾讯代码）与合约乘数（仅作记录，不参与基差计算）
VARIETIES = {
    "IF": {"index": "sh000300", "index_name": "沪深300"},
    "IH": {"index": "sh000016", "index_name": "上证50"},
    "IC": {"index": "sh000905", "index_name": "中证500"},
    "IM": {"index": "sh000852", "index_name": "中证1000"},
}
FUT_RE = re.compile(r"^(IF|IH|IC|IM)(\d{4})$")

CFFEX_RTJ_URL = "http://www.cffex.com.cn/sj/hqsj/rtj/{yyyymm}/{dd}/{yyyymmdd}_1.csv"
TENCENT_URL = "https://qt.gtimg.cn/q={codes}"
EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_FIELDS = ("fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
             "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
EM_SECID = {"sh000300": "1.000300", "sh000016": "1.000016",
            "sh000905": "1.000905", "sh000852": "1.000852"}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 2026 年中国公众假期（国务院放假安排；交易日历每年更新，见 README）。
# 仅影响剩余交易日计数；2026-11/12 月无假期，11-20 当日计数不受影响。
HOLIDAYS_2026 = {
    "2026-01-01", "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18",
    "2026-02-19", "2026-02-20", "2026-02-21", "2026-04-04", "2026-04-05",
    "2026-04-06", "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04",
    "2026-05-05", "2026-06-19", "2026-06-20", "2026-06-21", "2026-09-25",
    "2026-09-26", "2026-09-27", "2026-10-01", "2026-10-02", "2026-10-03",
    "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07",
}


def third_friday(year, month):
    """某月第 3 个周五（股指期货交割日）。"""
    d = date(year, month, 1)
    # 第一个周五
    offset = (4 - d.weekday()) % 7
    first_friday = d + timedelta(days=offset)
    return first_friday + timedelta(days=14)


def remaining_trading_days(from_date, expiry):
    """自次一交易日起至交割日（含）的交易日数；工作日减假期。"""
    if expiry <= from_date:
        return 0
    n = 0
    d = from_date + timedelta(days=1)
    covered = from_date.year == 2026 and expiry.year == 2026
    while d <= expiry:
        if d.weekday() < 5 and d.strftime("%Y-%m-%d") not in HOLIDAYS_2026:
            n += 1
        d += timedelta(days=1)
    return n, covered


def pct_rank(series, value):
    if not series or value is None:
        return None
    return round(100.0 * sum(1 for v in series if v <= value) / len(series), 1)


def fetch_cffex_rtj(day, attempts=3, backoff=(5, 15, 30)):
    """拉中金所日统计 CSV，返回 GBK 解码后的文本；404 表示当日未发布。"""
    url = CFFEX_RTJ_URL.format(yyyymm=day[:4] + day[5:7], dd=day[8:10],
                               yyyymmdd=day.replace("-", ""))
    last_err = None
    for i in range(attempts):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                raise RuntimeError("HTTP %d" % r.status_code)
            return r.content.decode("gbk", "replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if i < attempts - 1:
                print("WARN 中金所日统计第 %d 次失败: %s，%d 秒后重试"
                      % (i + 1, e, backoff[i]))
                time.sleep(backoff[i])
    raise RuntimeError("中金所日统计拉取失败: %s" % last_err)


def parse_rtj(text):
    """解析日统计 CSV，返回 {品种: 主力行 dict}（主力 = 持仓量最大）。"""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or rows[0][0] != "合约代码":
        raise RuntimeError("日统计 CSV 表头异常: %s" % (rows[0] if rows else None))
    by_var = {}
    for row in rows[1:]:
        if len(row) < 11 or not row[0]:
            continue
        m = FUT_RE.match(row[0].strip())
        if not m:
            continue
        var = m.group(1)
        try:
            rec = {"contract": row[0].strip(), "open": float(row[1]),
                   "high": float(row[2]), "low": float(row[3]),
                   "volume": int(float(row[4])), "amount": float(row[5]),
                   "oi": int(float(row[6])), "oi_chg": int(float(row[7])),
                   "close": float(row[8]), "settle": float(row[9]),
                   "prev_settle": float(row[10])}
        except (ValueError, IndexError):
            continue
        if var not in by_var or rec["oi"] > by_var[var]["oi"]:
            by_var[var] = rec
    missing = [v for v in VARIETIES if v not in by_var]
    if missing:
        raise RuntimeError("日统计缺品种主力: %s" % ",".join(missing))
    return by_var


def fetch_spot_tencent(codes, attempts=4, backoff=(5, 15, 30, 60)):
    """腾讯指数收盘价。返回 {code: (close, prev_close)}。
    qt.gtimg.cn 在本机间歇超时（2026-11-20 实测），故多试几次；https 不通时改 http。"""
    last_err = None
    for i in range(attempts):
        scheme = "https" if i < 2 else "http"
        url = "%s://qt.gtimg.cn/q=%s" % (scheme, ",".join(codes))
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                raise RuntimeError("HTTP %d" % r.status_code)
            out = {}
            for m in re.finditer(r'v_(\w+)="([^"]*)"', r.text):
                parts = m.group(2).split("~")
                if len(parts) < 5:
                    continue
                try:
                    out[m.group(1)] = (float(parts[3]), float(parts[4]))
                except ValueError:
                    continue
            if len(out) < len(codes):
                raise RuntimeError("腾讯返回不全：%s" % sorted(out))
            return out
        except Exception as e:  # noqa: BLE001
            last_err = e
            print("WARN 腾讯第 %d 次失败(%s): %s" % (i + 1, scheme, e))
            if i < attempts - 1:
                time.sleep(backoff[i])
    raise RuntimeError("腾讯指数收盘价拉取失败: %s" % last_err)


def fetch_spot_em(code, day, attempts=3, backoff=(5, 15, 30)):
    """东财 push2his 日线（第二来源）：取当日收盘价。返回 close 或 None。"""
    secid = EM_SECID[code]
    params = {"secid": secid, "klt": "101", "fqt": "1",
              "beg": day.replace("-", ""), "end": day.replace("-", "")}
    url = EM_KLINE_URL + "?" + EM_FIELDS
    last_err = None
    for i in range(attempts):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                raise RuntimeError("HTTP %d" % r.status_code)
            payload = r.json()
            klines = (payload.get("data") or {}).get("klines") or []
            if not klines:
                return None
            return float(klines[-1].split(",")[2])
        except Exception as e:  # noqa: BLE001
            last_err = e
            if i < attempts - 1:
                time.sleep(backoff[i])
    print("WARN 东财第二来源 %s 失败: %s" % (code, last_err))
    return None


def load_history():
    if not os.path.exists(OUT_PATH):
        return {"version": 1, "indicator": "futures_basis", "days": []}
    with open(OUT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def selftest():
    # 第三周五：2026-12-18；2026-11-20 起剩余交易日应为 20
    assert third_friday(2026, 12) == date(2026, 12, 18)
    assert third_friday(2026, 11) == date(2026, 11, 20)
    n, covered = remaining_trading_days(date(2026, 11, 20), date(2026, 12, 18))
    assert n == 20 and covered, (n, covered)
    # 年化：IF2612 2026-11-20，F=3861，S=3954.57，剩余 20 日 -> 约 -29.6%
    ann = (3861 - 3954.57) / 3954.57 * 250 / 20
    assert abs(ann * 100 - (-29.58)) < 0.05, ann
    assert pct_rank([1.0, 2.0, 3.0], 2.0) == 66.7
    # 解析：主力取持仓量最大者
    text = ("合约代码,今开盘,最高价,最低价,成交量,成交金额,持仓量,持仓变化,"
            "今收盘,今结算,前结算,涨跌1,涨跌2,Delta\n"
            "IF2611,3928.4,3934.4,3892.4,15654,1838177.412,0,-21100,3919,3919.42,3961.6,-42.6,-42.18,--\n"
            "IF2612,3889.8,3889.8,3836,103707,12007759.926,188038,2992,3869,3861,3915.8,-46.8,-54.8,--\n"
            "IH2612,2701,2727.4,2669,59501,4811614.878,79153,5287,2721.6,2706.6,2724.8,-3.2,-18.2,--\n"
            "IC2612,6069.8,6093.8,5891.6,155810,18630960.656,190090,8692,5902,5934.8,6181,-279,-246.2,--\n"
            "IM2612,5760,5769.8,5535.2,288059,32647062.344,337731,17424,5545.6,5600.6,5877,-331.4,-276.4,--\n")
    main = parse_rtj(text)
    assert main["IF"]["contract"] == "IF2612" and main["IF"]["settle"] == 3861.0
    assert main["IH"]["settle"] == 2706.6 and main["IC"]["settle"] == 5934.8
    assert main["IM"]["settle"] == 5600.6
    print("selftest PASS")


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        selftest()
        return 0
    backfill = None
    local_csv = None
    if "--date" in args:
        backfill = args[args.index("--date") + 1]
        datetime.strptime(backfill, "%Y-%m-%d")
    if "--csv" in args:
        local_csv = args[args.index("--csv") + 1]
    now = datetime.now(BJ)
    today = backfill or now.strftime("%Y-%m-%d")
    if backfill is None and (now.hour, now.minute) < CLOSE_GUARD_HHMM:
        print("未到收盘后 15:05（北京时间 %s），不计算。" % now.strftime("%H:%M"))
        return 0

    if local_csv:
        with open(local_csv, encoding="gbk", errors="replace") as f:
            text = f.read()
        print("使用本地日统计 CSV：%s" % local_csv)
    else:
        try:
            text = fetch_cffex_rtj(today)
        except Exception as e:  # noqa: BLE001
            print("ERROR: %s" % e)
            return 1
        if text is None:
            print("当日 %s 中金所日统计尚未发布（404），记无信号，不写文件。" % today)
            return 0
    try:
        main_contracts = parse_rtj(text)
    except Exception as e:  # noqa: BLE001
        print("ERROR: 日统计解析失败: %s" % e)
        return 1

    codes = [v["index"] for v in VARIETIES.values()]
    try:
        spot = fetch_spot_tencent(codes)
    except Exception as e:  # noqa: BLE001
        print("ERROR: 现货指数收盘价（腾讯）拉取失败: %s" % e)
        return 1

    hist = load_history()
    days = [d for d in hist.get("days", []) if d.get("date") != today]
    prev_day = days[-1] if days else None

    # 第二来源抽查：每个指数用东财日线核收盘价（失败只记警告，不阻塞）
    cross = {}
    for code in codes:
        em_close = fetch_spot_em(code, today)
        tx_close = spot[code][0]
        if em_close is None:
            cross[code] = "东财未取到"
        elif abs(em_close - tx_close) < 0.01:
            cross[code] = "一致"
        else:
            cross[code] = "分歧：腾讯 %.2f vs 东财 %.2f" % (tx_close, em_close)

    rows = []
    for var, meta in VARIETIES.items():
        rec = main_contracts[var]
        code = meta["index"]
        s_close, s_prev = spot[code]
        f_settle = rec["settle"]
        basis = round(f_settle - s_close, 2)
        basis_pct = (f_settle - s_close) / s_close
        yymm = rec["contract"][2:]
        expiry = third_friday(2000 + int(yymm[:2]), int(yymm[2:]))
        rem, covered = remaining_trading_days(
            datetime.strptime(today, "%Y-%m-%d").date(), expiry)
        flags = []
        if not covered:
            flags.append("交易日历未覆盖该区间，按工作日估算剩余交易日")
        if rem < 5:
            flags.append("剩余交易日 %d < 5：年化值不可靠" % rem)
        if rem <= 5:
            flags.append("交割周：基差机械收敛，慎用")
        month = int(today[5:7])
        if month in (5, 6, 7):
            flags.append("5–7月分红季：未做分红调整，置信度下降")
        # 主力换月检查
        if prev_day:
            prev_contracts = {r["variety"]: r["contract"]
                              for r in prev_day.get("rows", [])}
            if prev_contracts.get(var) and prev_contracts[var] != rec["contract"]:
                flags.append("主力换月（%s → %s），基差跳变"
                             % (prev_contracts[var], rec["contract"]))
        ann = None
        if rem > 0:
            ann = round(basis_pct * 250 / rem * 100, 2)  # 百分比
        # 历史分位与 5 日变化（取本品种历史）
        hist_ann = [r["ann_basis_pct"] for d in days for r in d.get("rows", [])
                    if r.get("variety") == var and r.get("ann_basis_pct") is not None]
        pct100 = pct_rank(hist_ann[-100:], ann) if len(hist_ann) >= 80 else None
        pct250 = pct_rank(hist_ann[-250:], ann) if len(hist_ann) >= 200 else None
        if pct100 is None:
            flags.append("分位仅供参考（%s 有效历史 %d 日 < 80 日）" % (var, len(hist_ann)))
        # 3σ 检查（只标记）
        if len(hist_ann) >= 20:
            mean = sum(hist_ann) / len(hist_ann)
            sd = math.sqrt(sum((v - mean) ** 2 for v in hist_ann) / len(hist_ann))
            if sd > 0 and ann is not None and abs(ann - mean) > 3 * sd:
                flags.append("3σ 极端值（均值 %.2f%%，标准差 %.2f%%）" % (mean, sd))
        # 5 日变化方向
        chg5 = None
        if len(hist_ann) >= 4 and ann is not None:
            prev_ann = hist_ann[-4]
            chg5 = round(abs(ann) - abs(prev_ann), 2)
            flags.append("5日变化：年化基差率绝对值%s（%.2f%% → %.2f%%）"
                         % ("缩小·收敛" if chg5 < 0 else "扩大", prev_ann, ann))
        rows.append({
            "timestamp": today, "indicator": "basis_%s" % var,
            "variety": var, "contract": rec["contract"],
            "settle": rec["settle"], "close": rec["close"],
            "prev_settle": rec["prev_settle"],
            "volume": rec["volume"], "oi": rec["oi"], "oi_chg": rec["oi_chg"],
            "index": meta["index_name"], "index_code": code,
            "spot_close": s_close, "spot_prev_close": s_prev,
            "basis": basis, "basis_pct": round(basis_pct * 100, 4),
            "value": ann, "ann_basis_pct": ann,
            "expiry": expiry.strftime("%Y-%m-%d"),
            "remaining_trading_days": rem,
            "percentile": pct100, "percentile_250": pct250,
            "change_5d_abs": chg5,
            "spot_cross_check": cross[code],
            "data_quality_flag": "；".join(flags) if flags else "ok",
        })

    day = {"date": today, "rows": rows, "source": "中金所日统计 CSV + 腾讯指数收盘",
           "source_url": CFFEX_RTJ_URL.format(yyyymm=today[:4] + today[5:7],
                                              dd=today[8:10],
                                              yyyymmdd=today.replace("-", ""))}
    days.append(day)
    days.sort(key=lambda d: d["date"])
    hist["days"] = days[-KEEP_DAYS:]
    hist["updated"] = today
    hist["generated_at_beijing"] = now.strftime("%Y-%m-%d %H:%M")
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT_PATH)
    for r in rows:
        print("%s %s：结算 %.1f / 现货 %.2f，基差 %.2f（%.2f%%），年化 %.2f%%"
              "（剩余 %d 交易日），100日分位 %s，标志：%s"
              % (today, r["contract"], r["settle"], r["spot_close"], r["basis"],
                 r["basis_pct"], r["value"], r["remaining_trading_days"],
                 r["percentile"], r["data_quality_flag"]))
    print("现货收盘第二来源抽查（东财）：%s" % cross)
    return 0


if __name__ == "__main__":
    sys.exit(main())
