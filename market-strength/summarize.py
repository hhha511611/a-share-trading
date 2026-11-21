#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日级汇总：读三路历史，输出当日一张小表，并写对照日志。

只做场外观察，不进入 CAN SLIM V1.1 执行层；不定义场景标签、不做综合分数、
不做加权打分；不给个股买卖建议、不预测股价。
对照日志（data/comparison_log.csv）：每日一行——日期、三路指标分位/状态、
当日 M 门状态、涨跌停家数。M 门状态取自当日 16:16 扫描日志
（~/workspace/goals/way-of-the-turtle-5-trend-trading-validation/hidden_files/
scan-YYYY-MM-DD.md 的“大盘 M”一节）；扫描日志缺失时 M 门记“未取到”，
不编造、不沿用前一日状态。
某路当日缺失时该格记“无信号”，不拿旧数据顶替。
用法：python3 summarize.py [--date YYYY-MM-DD] [--selftest]
"""
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

BJ = timezone(timedelta(hours=8))
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
LOG_PATH = os.path.join(DATA, "comparison_log.csv")
SCAN_LOG_DIR = os.path.expanduser(
    "~/workspace/goals/way-of-the-turtle-5-trend-trading-validation/hidden_files")

LOG_COLUMNS = ["date", "if_ann_pct", "if_ann_pctile", "ih_ann_pct", "ic_ann_pct",
               "im_ann_pct", "if_net", "if_net_chg", "ih_net", "ih_net_chg",
               "ic_net", "ic_net_chg", "im_net", "im_net_chg",
               "trin_amt", "trin_vol", "trin_ma10", "trin_pctile",
               "limit_up", "limit_down", "up", "down",
               "m_gate", "sh_pct", "sz_pct", "cyb_pct", "notes"]


def load_json(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def day_of(hist, date):
    if not hist:
        return None
    for d in hist.get("days", []):
        if d.get("date") == date:
            return d
    return None


def parse_m_gate(date):
    """由当日扫描日志解析 M 门状态与三指数涨跌幅。返回 (m_gate, sh, sz, cyb, raw)。"""
    path = os.path.join(SCAN_LOG_DIR, "scan-%s.md" % date)
    if not os.path.exists(path):
        return "未取到（扫描日志缺失）", None, None, None, ""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    m_gate = None
    raw = ""
    m = re.search(r"^## 大盘 M[^\n]*$", text, re.M)
    if m:
        raw = m.group(0)
        inner = re.search(r"[（(]([^）)]+)[）)]", raw)
        if inner:
            m_gate = inner.group(1)
    if not m_gate:
        m2 = re.search(r"M 仍为([^，。\n]+)", text)
        m_gate = m2.group(1).strip() if m2 else "未解析出"
    def _idx(name):
        mm = re.search(name + r"\s*(-?\d+\.\d+)%", text)
        return float(mm.group(1)) if mm else None
    return m_gate, _idx("上证"), _idx("深证"), _idx("创业板"), raw


def fmt(v, nd=2):
    if v is None:
        return "无信号"
    if isinstance(v, float):
        return "%.*f" % (nd, v)
    return str(v)


def selftest():
    m, sh, sz, cyb, raw = parse_m_gate("2026-11-20")
    assert "走弱/修正" in m, m
    assert sh == -1.23 and sz == -3.09 and cyb == -3.54, (sh, sz, cyb)
    print("selftest PASS（M 门：%s；上证 %.2f%%，深证 %.2f%%，创业板 %.2f%%）"
          % (m, sh, sz, cyb))


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

    basis = day_of(load_json("basis.json"), today)
    members = day_of(load_json("members.json"), today)
    trin = day_of(load_json("trin.json"), today)
    m_gate, sh, sz, cyb, _raw = parse_m_gate(today)

    def basis_row(var):
        if not basis:
            return None
        for r in basis.get("rows", []):
            if r.get("variety") == var:
                return r
        return None

    def mem_row(var):
        if not members:
            return None
        for r in members.get("rows", []):
            if r.get("variety") == var:
                return r
        return None

    notes = []
    if not basis:
        notes.append("基差无信号")
    if not members:
        notes.append("会员持仓无信号")
    if not trin:
        notes.append("TRIN 无信号")
    if m_gate.startswith("未取到"):
        notes.append("M 门未取到")

    row = {"date": today}
    for var, key in (("IF", "if"), ("IH", "ih"), ("IC", "ic"), ("IM", "im")):
        b = basis_row(var)
        row["%s_ann_pct" % key] = b["value"] if b else None
        if key == "if":
            row["if_ann_pctile"] = b["percentile"] if b else None
        mrow = mem_row(var)
        row["%s_net" % key] = mrow["value"] if mrow else None
        row["%s_net_chg" % key] = mrow.get("net_day_chg") if mrow else None
    row["trin_amt"] = trin["value"] if trin else None
    row["trin_vol"] = trin.get("value_vol") if trin else None
    row["trin_ma10"] = trin.get("ma10") if trin else None
    row["trin_pctile"] = trin.get("percentile") if trin else None
    row["limit_up"] = trin.get("limit_up") if trin else None
    row["limit_down"] = trin.get("limit_down") if trin else None
    row["up"] = trin.get("up") if trin else None
    row["down"] = trin.get("down") if trin else None
    row["m_gate"] = m_gate
    row["sh_pct"] = sh
    row["sz_pct"] = sz
    row["cyb_pct"] = cyb
    row["notes"] = "；".join(notes) if notes else ""

    # 追加对照日志（同日去重）
    existing = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8", newline="") as f:
            existing = list(csv.DictReader(f))
    existing = [r for r in existing if r.get("date") != today]
    existing.append({c: ("" if row.get(c) is None else row[c]) for c in LOG_COLUMNS})
    existing.sort(key=lambda r: r["date"])
    with open(LOG_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        w.writeheader()
        w.writerows(existing)

    # 当日小表（文本 + Markdown 存档）
    b_if, b_ih, b_ic, b_im = (basis_row(v) for v in ("IF", "IH", "IC", "IM"))
    m_if, m_ih, m_ic, m_im = (mem_row(v) for v in ("IF", "IH", "IC", "IM"))
    lines = []
    lines.append("# 大盘强弱日级原型 · 当日小表（%s）" % today)
    lines.append("")
    lines.append("> 观察口径，不进入 V1.1 执行层；M 门仍是唯一执行裁决。"
                 "无场景标签、无综合分数、无买卖建议。")
    lines.append("")
    lines.append("## 期指基差（年化基差率，×250/剩余交易日）")
    lines.append("")
    lines.append("| 合约 | 结算 | 现货收盘 | 基差 | 年化基差率 | 100日分位 | 剩余交易日 |")
    lines.append("|---|---|---|---|---|---|---|")
    for b in (b_if, b_ih, b_ic, b_im):
        if b:
            lines.append("| %s | %.1f | %.2f | %.2f | %s%% | %s | %d |"
                         % (b["contract"], b["settle"], b["spot_close"], b["basis"],
                            fmt(b["value"]), fmt(b["percentile"], 1),
                            b["remaining_trading_days"]))
        else:
            lines.append("| — | 无信号 |")
    lines.append("")
    lines.append("## 前20结算会员净持仓（买−卖，代客口径）")
    lines.append("")
    lines.append("| 合约 | 净持仓 | 日变化 | 买合计 | 卖合计 | 100日分位 |")
    lines.append("|---|---|---|---|---|---|")
    for mrow in (m_if, m_ih, m_ic, m_im):
        if mrow and mrow.get("value") is not None:
            lines.append("| %s | %d | %s | %d | %d | %s |"
                         % (mrow["contract"], mrow["value"],
                            fmt(mrow.get("net_day_chg"), 0),
                            mrow["buy_total"], mrow["sell_total"],
                            fmt(mrow.get("percentile"), 1)))
        else:
            lines.append("| — | 无信号 |")
    lines.append("")
    lines.append("## TRIN（日级）")
    lines.append("")
    if trin:
        lines.append("- TRIN（成交额口径，主读数）：%s；TRIN（成交量口径）：%s；"
                     "10 日均值：%s（n=%d）"
                     % (fmt(trin["value"], 4), fmt(trin.get("value_vol"), 4),
                        fmt(trin.get("ma10"), 4), trin.get("ma10_n", 0)))
        lines.append("- 上涨 %d 家 / 下跌 %d 家 / 平盘 %d 家 / 停牌 %d 家（总数 %d）"
                     % (trin["up"], trin["down"], trin["flat"],
                        trin["suspended"], trin["total"]))
        lines.append("- 涨停 %d 家 / 跌停 %d 家；100 日分位：%s"
                     % (trin["limit_up"], trin["limit_down"],
                        fmt(trin.get("percentile"), 1)))
    else:
        lines.append("- 无信号")
    lines.append("")
    lines.append("## M 门对照（当日 16:16 扫描日志）")
    lines.append("")
    lines.append("- M 门状态：%s" % m_gate)
    lines.append("- 上证 %s%%，深证 %s%%，创业板 %s%%"
                 % (fmt(sh), fmt(sz), fmt(cyb)))
    if notes:
        lines.append("")
        lines.append("备注：%s" % "；".join(notes))
    md_path = os.path.join(DATA, "summary-%s.md" % today)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\n".join(lines))
    print("\n对照日志已追加：%s（共 %d 行）" % (LOG_PATH, len(existing)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
