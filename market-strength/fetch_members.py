#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前 20 结算会员持仓（中金所）日级抓取：IF/IH/IC/IM 主力合约。

只做场外观察，不进入 CAN SLIM V1.1 执行层，不构成买卖建议，不预测价格。
数据源：中金所官网成交持仓排名 CSV（HTTP，HTTPS 不可用）：
  http://www.cffex.com.cn/sj/ccpm/{YYYYMM}/{DD}/{品种}_1.csv （GBK 编码）
  文件含该品种全部合约的排名，取主力合约（与当日 fetch_basis.py 同口径：
  同品种持仓量最大者；本脚本复用中金所日统计 CSV 判定主力）。
页面结构（以 2026-11-20 实测为准）：
  成交量排名每家只有一列“成交量”（无买/卖拆分），另列“比上一交易日增减”；
  持仓排名分“持买单量”“持卖单量”两表，各有“比上一交易日增减”。
  因此多空净持仓 = 持买单量前 20 合计 − 持卖单量前 20 合计（仅持仓表有买/卖拆分）；
  成交量不拆买/卖，不编造拆分数字。
口径说明（每行标注）：持仓为代客口径——结算会员汇总了客户与自营，
单家会员的翻转不等于市场转向；前 20 名合计≠全市场持仓。
已知口径差异（2026-11-20 实测）：排名表成交量前 20 合计约为日统计合约成交量的
1.5 倍（IF 161913/103707、IH 94335/59501、IC 241321/155810、IM 428345/288059），
页面未说明换算关系；可能为同一笔成交在买卖双方会员处双边计数，此处仅并列记录、
不作调和，脚本输出两者比值供核对。
分位：净持仓的 100 日分位需 >=80 个有效日、250 日分位需 >=200 个有效日，
否则记 null 并标注“分位仅供参考”。3σ 检查只用于净持仓本身，只标记、不自动跳过。
失败不覆盖旧文件：当日 CSV 未发布（404）时不写文件、退出码 0 并打印原因。
用法：python3 fetch_members.py [--date YYYY-MM-DD] [--csv-dir DIR] [--selftest]
  --csv-dir：用本地已下载的排名 CSV 目录跑（验证/补录用，文件名为 IF_1.csv 等）。
"""
import csv
import io
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

BJ = timezone(timedelta(hours=8))
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, "data", "members.json")
KEEP_DAYS = 365
CLOSE_GUARD_HHMM = (15, 5)

VARIETIES = ["IF", "IH", "IC", "IM"]
CCPM_URL = "http://www.cffex.com.cn/sj/ccpm/{yyyymm}/{dd}/{var}_1.csv"
RTJ_URL = "http://www.cffex.com.cn/sj/hqsj/rtj/{yyyymm}/{dd}/{yyyymmdd}_1.csv"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def pct_rank(series, value):
    if not series or value is None:
        return None
    return round(100.0 * sum(1 for v in series if v <= value) / len(series), 1)


def fetch_text(url, attempts=3, backoff=(5, 15, 30)):
    """拉中金所 CSV 文本；404 返回 None。"""
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
                print("WARN %s 第 %d 次失败: %s，%d 秒后重试"
                      % (url.split("/")[-1], i + 1, e, backoff[i]))
                time.sleep(backoff[i])
    raise RuntimeError("拉取失败 %s: %s" % (url, last_err))


def main_contracts_from_rtj(text):
    """由日统计 CSV 判定各品种主力（持仓量最大者）。返回 {品种: (合约, 成交量, 持仓量)}。"""
    import re
    rows = list(csv.reader(io.StringIO(text)))
    out = {}
    for row in rows[1:]:
        if len(row) < 9 or not row[0]:
            continue
        m = re.match(r"^(IF|IH|IC|IM)(\d{4})$", row[0].strip())
        if not m:
            continue
        try:
            vol, oi = int(float(row[4])), int(float(row[6]))
        except ValueError:
            continue
        var = m.group(1)
        if var not in out or oi > out[var][2]:
            out[var] = (row[0].strip(), vol, oi)
    missing = [v for v in VARIETIES if v not in out]
    if missing:
        raise RuntimeError("日统计缺品种主力: %s" % ",".join(missing))
    return out


def parse_ccpm(text, contract):
    """解析一品种排名 CSV，取指定合约前 20 名。返回 totals 与明细列表。"""
    rows = list(csv.reader(io.StringIO(text)))
    # 表头占两行；数据行：交易日,合约,排名,成交会员,成交量,成交增减,
    #                   持买会员,持买量,持买增减,持卖会员,持卖量,持卖增减
    tgt = [r for r in rows if len(r) >= 12 and r[1].strip() == contract]
    if len(tgt) != 20:
        raise RuntimeError("%s 排名行数 %d ≠ 20" % (contract, len(tgt)))
    members = []
    for r in tgt:
        try:
            members.append({
                "rank": int(r[2]),
                "vol_member": r[3].strip(), "volume": int(r[4]), "vol_chg": int(r[5]),
                "buy_member": r[6].strip(), "buy_oi": int(r[7]), "buy_chg": int(r[8]),
                "sell_member": r[9].strip(), "sell_oi": int(r[10]), "sell_chg": int(r[11]),
            })
        except (ValueError, IndexError) as e:
            raise RuntimeError("%s 第 %s 名解析失败: %s" % (contract, r[2], e))
    vol_total = sum(m["volume"] for m in members)
    buy_total = sum(m["buy_oi"] for m in members)
    sell_total = sum(m["sell_oi"] for m in members)
    return {"members": members, "vol_total": vol_total, "buy_total": buy_total,
            "sell_total": sell_total, "net": buy_total - sell_total,
            "buy_chg_total": sum(m["buy_chg"] for m in members),
            "sell_chg_total": sum(m["sell_chg"] for m in members)}


def load_history():
    if not os.path.exists(OUT_PATH):
        return {"version": 1, "indicator": "members_net_position", "days": []}
    with open(OUT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def selftest():
    # 以 2026-11-20 IF2612 前 3 名实测行验算合计逻辑（全量 20 名在验证跑中核对）
    text = ("交易日,合约,排名,成交量排名,,,持买单量排名,,,持卖单量排名,,\n"
            ",,,会员简称,成交量,比上一交易日增减,会员简称,持买单量,比上一交易日增减,"
            "会员简称,持卖单量,比上一交易日增减\n"
            "20261120,IF2612,1,中信期货(代客),43320,7323,国泰君安(代客),29088,1314,"
            "中信期货(代客),35396,358\n"
            "20261120,IF2612,2,国泰君安(代客),31896,4729,中信期货(代客),24726,484,"
            "国泰君安(代客),20186,-438\n")
    try:
        parse_ccpm(text, "IF2612")
    except RuntimeError as e:
        assert "行数 2 ≠ 20" in str(e), e
    else:
        raise AssertionError("行数不足应报错")
    assert pct_rank([1.0, 2.0, 3.0], 2.0) == 66.7
    print("selftest PASS")


def main():
    # 行缓冲：日志实时可见；进程被取消时缓冲内的输出不丢失
    #（2026-11-23 事故：块缓冲导致被取消的运行日志里零输出）。
    sys.stdout.reconfigure(line_buffering=True)
    args = sys.argv[1:]
    if "--selftest" in args:
        selftest()
        return 0
    backfill = None
    csv_dir = None
    if "--date" in args:
        backfill = args[args.index("--date") + 1]
        datetime.strptime(backfill, "%Y-%m-%d")
    if "--csv-dir" in args:
        csv_dir = args[args.index("--csv-dir") + 1]
    now = datetime.now(BJ)
    today = backfill or now.strftime("%Y-%m-%d")
    if backfill is None and (now.hour, now.minute) < CLOSE_GUARD_HHMM:
        print("未到收盘后 15:05（北京时间 %s），不计算。" % now.strftime("%H:%M"))
        return 0
    yyyymm, dd, yyyymmdd = today[:4] + today[5:7], today[8:10], today.replace("-", "")

    if csv_dir:
        rtj_path = os.path.join(csv_dir, "rtj.csv")
        with open(rtj_path, encoding="gbk", errors="replace") as f:
            rtj_text = f.read()
        print("使用本地日统计 CSV：%s" % rtj_path)
    else:
        rtj_text = fetch_text(RTJ_URL.format(yyyymm=yyyymm, dd=dd, yyyymmdd=yyyymmdd))
        if rtj_text is None:
            print("当日 %s 中金所日统计尚未发布（404），记无信号，不写文件。" % today)
            return 0
    try:
        mains = main_contracts_from_rtj(rtj_text)
    except Exception as e:  # noqa: BLE001
        print("ERROR: 主力判定失败: %s" % e)
        return 1

    hist = load_history()
    days = [d for d in hist.get("days", []) if d.get("date") != today]
    prev_day = days[-1] if days else None

    rows = []
    for var in VARIETIES:
        contract, rtj_vol, rtj_oi = mains[var]
        if csv_dir:
            path = os.path.join(csv_dir, "%s_1.csv" % var)
            with open(path, encoding="gbk", errors="replace") as f:
                text = f.read()
            print("使用本地排名 CSV：%s" % path)
        else:
            url = CCPM_URL.format(yyyymm=yyyymm, dd=dd, var=var)
            text = fetch_text(url)
            if text is None:
                print("当日 %s %s 排名 CSV 尚未发布（404），该品种记无信号。"
                      % (today, var))
                rows.append({"timestamp": today, "indicator": "members_%s" % var,
                             "variety": var, "contract": contract, "value": None,
                             "percentile": None,
                             "data_quality_flag": "排名 CSV 未发布，无信号"})
                continue
        try:
            parsed = parse_ccpm(text, contract)
        except Exception as e:  # noqa: BLE001
            print("ERROR: %s 解析失败: %s" % (contract, e))
            return 1
        flags = ["代客口径：结算会员汇总客户与自营，单家翻转≠市场转向",
                 "前20名合计≠全市场持仓"]
        vol_ratio = round(parsed["vol_total"] / rtj_vol, 2) if rtj_vol else None
        if vol_ratio is not None and not (0.9 <= vol_ratio <= 1.1):
            flags.append("排名表成交量合计为日统计成交量的 %.2f 倍（疑双边计数），"
                         "仅并列记录、不调和" % vol_ratio)
        net = parsed["net"]
        net_chg = parsed["buy_chg_total"] - parsed["sell_chg_total"]
        hist_net = [r["value"] for d in days for r in d.get("rows", [])
                    if r.get("variety") == var and r.get("value") is not None]
        pct100 = pct_rank(hist_net[-100:], net) if len(hist_net) >= 80 else None
        pct250 = pct_rank(hist_net[-250:], net) if len(hist_net) >= 200 else None
        if pct100 is None:
            flags.append("分位仅供参考（%s 有效历史 %d 日 < 80 日）" % (var, len(hist_net)))
        if len(hist_net) >= 20:
            mean = sum(hist_net) / len(hist_net)
            sd = math.sqrt(sum((v - mean) ** 2 for v in hist_net) / len(hist_net))
            if sd > 0 and abs(net - mean) > 3 * sd:
                flags.append("3σ 极端值（均值 %.0f，标准差 %.0f）" % (mean, sd))
        prev_net = None
        if prev_day:
            for r in prev_day.get("rows", []):
                if r.get("variety") == var and r.get("value") is not None:
                    prev_net = r["value"]
        net_day_chg = (net - prev_net) if prev_net is not None else None
        rows.append({
            "timestamp": today, "indicator": "members_%s" % var,
            "variety": var, "contract": contract,
            "value": net, "net_position": net,
            "net_day_chg": net_day_chg,
            "net_chg_from_table": net_chg,
            "buy_total": parsed["buy_total"], "sell_total": parsed["sell_total"],
            "buy_chg_total": parsed["buy_chg_total"],
            "sell_chg_total": parsed["sell_chg_total"],
            "vol_total_top20": parsed["vol_total"],
            "rtj_volume": rtj_vol, "rtj_oi": rtj_oi,
            "vol_total_ratio_vs_rtj": vol_ratio,
            "percentile": pct100, "percentile_250": pct250,
            "members": parsed["members"],
            "data_quality_flag": "；".join(flags),
        })

    day = {"date": today, "rows": rows,
           "source": "中金所成交持仓排名 CSV",
           "source_url": CCPM_URL.format(yyyymm=yyyymm, dd=dd, var="{品种}")}
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
        if r["value"] is None:
            print("%s %s：无信号（%s）" % (today, r["contract"], r["data_quality_flag"]))
            continue
        print("%s %s：前20净持仓 %d（买 %d − 卖 %d），日变化 %s，"
              "成交量合计 %d（日统计 %d，比值 %.2f），100日分位 %s，标志：%s"
              % (today, r["contract"], r["value"], r["buy_total"], r["sell_total"],
                 r["net_day_chg"], r["vol_total_top20"], r["rtj_volume"],
                 r["vol_total_ratio_vs_rtj"], r["percentile"],
                 r["data_quality_flag"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
