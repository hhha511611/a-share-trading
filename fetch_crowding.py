#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日收盘后计算交易拥挤度附带表，写入 data/crowding.json。

供 GitHub Actions 每日 15:45（北京时间）运行；页面 crowding.html 同源读取该 JSON。
口径见 ~/workspace/notes/交易拥挤度监测口径.md（用户 2026-11-16 定为 16:16 扫描的常态化附带表，
观察口径，不进入 V1.1 执行层）。

宇宙（2026-11-17 起动态规则，用户定）：8 只 V1.1 扫描池固定 + 当日换手率前 4 名动态
（候选 = 产业跟踪筛选池 46 家 + 光模块原型 4 家，剔除固定 8 只后按当日换手率排序取前 4，
并列按代码升序）。候选名单是筛选池文件的快照，池内名单变化时须同步更新本脚本
（周更任务注明、主对话推送）。

指标（各取过去至多 250 个交易日滚动百分位，含当日；实际天数记在行 n 字段）：
  1. 换手率分位：同花顺 K 线 turnover 字段直接给出。
  2. 成交额占比分位：个股成交额 ÷（上证指数成交额 + 深证成指成交额），再取分位。
  3. 融资余额占比分位：v1 未接通两融数据通道，记 null（表里显示"—"，判定按其余两项）。
短历史（2026-11-25 修）：上市不足 250 日的股票，历史满 60 日即按实际窗口
计算分位并在注记写明窗口天数（如"窗口仅 64 日"），不足 60 日仍记"数据不全"；
旧门槛 200 日会使宇树科技（64 日）、超纯应材（70 日）等次新股长期空白。
判定：≥2 项 ≥90% 记"拥挤"；仅 1 项 ≥90% 记"观察"；两项均 <90% 记"正常"；
可用指标不足 2 项记"数据不全"。百分位定义：≤ 当日值的天数 ÷ 窗口天数 × 100。
失败不覆盖旧文件：任一环节拿不到当日收盘数据时不写文件、退出码 0 并打印原因；
全量拉取失败时退出码 1（工作流不提交）。
数据源：同花顺 d.10jqka.com.cn v2 K 线（last.js + 目标年与上一年文件合并，
去重后取最近 250 日；年文件缺失时窗口缩短，n 如实记录）。单只 5 次重试
（45 秒超时，10/20/30/60 秒退避），第一遍失败的单只 30 秒后第二遍重拉；
仍失败则改走东财备用源（push2his 前复权日 K，lmt=300 覆盖 250 日窗口，
含换手率字段；2026-11-24 前该备用源已在 market-strength 的 fetch_basis.py
里于 Actions 上跑通）。两源都失败才记该行 null。
补算过去某日：python fetch_crowding.py --date YYYY-MM-DD（同日行会被替换）。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BJ = timezone(timedelta(hours=8))

# 行缓冲：管道/重定向时日志实时刷出，进程被取消也不丢已打印的行
# （2026-11-23 大资金原型教训：块缓冲导致取消时日志全丢）
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:  # noqa: BLE001 - 旧 Python 无此方法时忽略
    pass

# 宇宙（2026-11-17 起动态规则，用户定）：8 只 V1.1 扫描池固定 + 当日换手率前 4 名动态。
# 候选 = 产业跟踪筛选池 46 家（2026-11-17 快照，48 个条目去重；铖昌科技、中科创达各跨两板块）
#       + 光模块原型 4 家；与固定 8 只重叠的（绿的谐波、瑞芯微）只计固定位。
# 池内名单变化时须同步更新 CANDIDATES（周更任务注明、主对话推送）。
FIXED = [
    ("601689", "拓普集团"), ("601100", "恒立液压"), ("603119", "浙江荣泰"),
    ("688017", "绿的谐波"), ("603893", "瑞芯微"), ("688018", "乐鑫科技"),
    ("688099", "晶晨股份"), ("688521", "芯原股份"),
]
CANDIDATES = [
    # 固态电池 6
    ("300750", "宁德时代"), ("300450", "先导智能"), ("002709", "天赐材料"),
    ("300285", "国瓷材料"), ("002074", "国轩高科"), ("300014", "亿纬锂能"),
    # 人形机器人 5
    ("688836", "宇树科技"), ("688017", "绿的谐波"), ("002472", "双环传动"),
    ("688160", "步科股份"), ("603662", "柯力传感"),
    # 商业航天 4
    ("002025", "航天电器"), ("001270", "铖昌科技"), ("600118", "中国卫星"),
    ("688270", "ST 臻镭科技"),
    # 6G 4（铖昌科技已计入商业航天）
    ("000063", "中兴通讯"), ("688182", "灿勤科技"), ("002281", "光迅科技"),
    ("300136", "信维通信"),
    # 物理 AI 6
    ("603893", "瑞芯微"), ("002415", "海康威视"), ("300124", "汇川技术"),
    ("688322", "奥比中光"), ("688787", "海天瑞声"), ("300496", "中科创达"),
    # AI 制药 4
    ("603259", "药明康德"), ("688222", "成都先导"), ("301080", "百普赛斯"),
    ("688202", "美迪西"),
    # AI 应用 5（中科创达已计入物理 AI）
    ("688111", "金山办公"), ("300033", "同花顺"), ("300418", "昆仑万维"),
    ("300058", "蓝色光标"), ("600588", "用友网络"),
    # 半导体设备零部件 12
    ("300395", "菲利华"), ("688409", "富创精密"), ("688652", "京仪装备"),
    ("688195", "腾景科技"), ("300820", "英杰电气"), ("688502", "茂莱光学"),
    ("003031", "中瓷电子"), ("300666", "江丰电子"), ("688596", "正帆科技"),
    ("002222", "福晶科技"), ("688785", "恒运昌"), ("301717", "超纯应材"),
    # 光模块原型 4（本表起因案例，现为候选、不再固定占位）
    ("300308", "中际旭创"), ("300502", "新易盛"), ("300394", "天孚通信"),
    ("002463", "沪电股份"),
]
DYNAMIC_N = 4
INDICES = [("000001", "上证指数"), ("399001", "深证成指")]

WINDOW = 250
MIN_DAYS = 60  # 短历史下限（2026-11-25 由 200 改为 60）：满 60 日即按实际窗口算分位
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


EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_FIELDS = ("fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
             "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")


def _em_secid(code):
    """东财 secid：沪市 1. / 深市 0.；指数：上证 1.000001、深证 0.399001。"""
    if code.startswith("6") or code == "000001":
        return "1." + code
    return "0." + code


def _parse_em_klines(klines):
    """东财 kline 行：日期,开,收,高,低,成交量(手),成交额(元),振幅,涨跌幅,涨跌额,换手率。
    返回 {date: (close, volume, amount, turnover)}。"""
    out = {}
    for line in klines:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        try:
            date = parts[0].strip()
            close = float(parts[2])
            volume = float(parts[5])
            amount = float(parts[6])
            turnover = float(parts[10]) if parts[10].strip() != "" else None
        except (ValueError, IndexError):
            continue
        out[date] = (close, volume, amount, turnover)
    return out


def fetch_kline_em(code, today):
    """东财备用源：push2his 前复权日 K，lmt=300 覆盖 250 日窗口。
    返回与 fetch_kline 相同的 [(date, close, pct, volume, amount, turnover)]。
    注意：东财为前复权序列，收盘价与同花顺在除权日前后可能有微小口径差；
    换手率与成交额为原始值，不受复权影响，分位计算不受影响。"""
    url = ("%s?secid=%s&klt=101&fqt=1&lmt=300&end=20500101&iscca=1&%s"
           % (EM_KLINE_URL, _em_secid(code), EM_FIELDS))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_err = None
    merged = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                payload = json.load(r)
            if payload.get("rc") != 0 or not payload.get("data") or not payload["data"].get("klines"):
                raise RuntimeError("东财返回 rc=%s 或无 klines" % payload.get("rc"))
            merged = _parse_em_klines(payload["data"]["klines"])
            break
        except Exception as e:  # noqa: BLE001 - 3 次尝试后才抛错
            last_err = e
            if attempt < 2:
                time.sleep(10)
    if merged is None:
        raise RuntimeError("东财备用源拉取 %s 失败: %s" % (code, last_err))
    dates = sorted(merged)
    out = []
    prev_close = None
    for date in dates:
        close, volume, amount, turnover = merged[date]
        pct = 0.0 if prev_close is None or prev_close == 0 else (close / prev_close - 1.0) * 100.0
        out.append((date, close, pct, volume, amount, turnover))
        prev_close = close
    if not out:
        raise RuntimeError("东财备用源 %s 无有效 K 线" % code)
    return out


def pct_rank(series, today_value):
    """百分位 = ≤ 当日值的天数 ÷ 总天数 × 100，保留 1 位小数。"""
    n = len(series)
    if n == 0 or today_value is None:
        return None
    return round(100.0 * sum(1 for v in series if v <= today_value) / n, 1)


def select_dynamic(stock_series, today, candidates=None):
    """从候选里按当日换手率取前 DYNAMIC_N 名。须有当日 K 线且换手率非空；
    固定 8 只不参与排名；并列按代码升序。返回 [(code, name)]。"""
    if candidates is None:
        candidates = CANDIDATES
    fixed_codes = {c for c, _ in FIXED}
    ranked = []
    for code, name in candidates:
        if code in fixed_codes:
            continue
        series = stock_series.get(code, [])
        if not series or series[-1][0] != today:
            continue
        turnover = series[-1][5]
        if turnover is None:
            continue
        ranked.append((turnover, code, name))
    ranked.sort(key=lambda t: (-t[0], t[1]))
    return [(code, name) for _, code, name in ranked[:DYNAMIC_N]]


def compute_day(stock_series, index_amounts, today, selected, fallback_codes=None):
    """stock_series: {code: [(date, close, pct, volume, amount, turnover)]}（已取窗口内）。
    index_amounts: {date: 上证+深证当日成交额}。selected: [(code, name, slot)]。
    fallback_codes: 走东财备用源的股票代码集合，注记标注来源。返回当日 rows。"""
    rows = []
    for code, name, slot in selected:
        series = stock_series.get(code, [])
        row = {"code": code, "name": name, "slot": slot, "close": None, "pct": None,
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
        if n < WINDOW:
            notes.append("窗口仅 %d 日（上市不足 250 日）" % n)
        if fallback_codes and code in fallback_codes:
            notes.append("东财备用源（同花顺未取到）")
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
    """不联网的逻辑自检：百分位、判定与动态选股。"""
    # 250 天换手线性上升，当日为最大值 -> 100.0；当日为中位数 -> 约 50。
    series = [float(i) for i in range(1, 251)]
    assert pct_rank(series, 250.0) == 100.0, pct_rank(series, 250.0)
    assert abs(pct_rank(series, 125.0) - 50.0) < 0.5, pct_rank(series, 125.0)
    today = "2026-11-16"
    dates = ["2026-%02d-%02d" % (m, d) for m in range(1, 12) for d in range(1, 24)][:250]
    dates[-1] = today
    # 固定 8 只：换手与占比双双 ≥90 -> 拥挤
    fake = {}
    for code, _ in FIXED:
        rows = []
        for i, dt in enumerate(dates):
            rows.append((dt, 10.0, 0.0, 100.0, 1e8 * (i + 1), 0.1 * (i + 1)))
        fake[code] = rows
    # 候选 6 只换手率各异，验动态取前 4（000002/000006/000004/000001）
    cand_codes = ["000001", "000002", "000003", "000004", "000005", "000006"]
    cand_turns = [5.0, 9.0, 1.0, 7.0, 3.0, 8.0]
    for code, turn in zip(cand_codes, cand_turns):
        rows = []
        for i, dt in enumerate(dates):
            t = turn if dt == today else 0.5
            rows.append((dt, 10.0, 0.0, 100.0, 1e8, t))
        fake[code] = rows
    idx = {dt: 1e12 for dt in dates}
    picked = select_dynamic(fake, today, candidates=[(c, "候选" + c) for c in cand_codes])
    assert [c for c, _ in picked] == ["000002", "000006", "000004", "000001"], picked
    # 并列按代码升序：两只同为 9.0 时代码小的在前
    fake["000007"] = [(dt, 10.0, 0.0, 100.0, 1e8, 9.0 if dt == today else 0.5) for dt in dates]
    picked2 = select_dynamic(fake, today,
                             candidates=[(c, "候选" + c) for c in cand_codes + ["000007"]])
    assert [c for c, _ in picked2][:2] == ["000002", "000007"], picked2
    # compute_day：固定 8 只全拥挤且 slot 正确
    selected = [(c, n, "fixed") for c, n in FIXED]
    day_rows = compute_day(fake, idx, today, selected)
    assert all(r["status"] == "拥挤" for r in day_rows), [r["status"] for r in day_rows]
    assert all(r["turnover_pct"] == 100.0 for r in day_rows)
    assert all(r["slot"] == "fixed" for r in day_rows)
    # 当日 K 线缺失 -> 数据不全
    fake2 = {code: rows[:-1] for code, rows in fake.items()}
    day_rows2 = compute_day(fake2, idx, today, selected)
    assert all(r["status"] == "数据不全" for r in day_rows2)
    # 短历史：64 日窗口 -> 按实际窗口算分位（不为空），注记写明窗口
    short_dates = dates[-64:]
    short = {}
    for code, _ in FIXED[:1]:
        rows = []
        for i, dt in enumerate(short_dates):
            rows.append((dt, 10.0, 0.0, 100.0, 1e8 * (i + 1), 0.1 * (i + 1)))
        short[code] = rows
    short_rows = compute_day(short, idx, today, [(FIXED[0][0], FIXED[0][1], "fixed")])
    assert short_rows[0]["turnover_pct"] == 100.0, short_rows[0]
    assert short_rows[0]["n"] == 64 and "窗口仅 64 日" in short_rows[0]["note"], short_rows[0]
    assert short_rows[0]["status"] == "拥挤", short_rows[0]
    # 不足 60 日 -> 仍记数据不全
    tiny_dates = dates[-30:]
    tiny = {}
    for code, _ in FIXED[:1]:
        rows = []
        for i, dt in enumerate(tiny_dates):
            rows.append((dt, 10.0, 0.0, 100.0, 1e8 * (i + 1), 0.1 * (i + 1)))
        tiny[code] = rows
    tiny_rows = compute_day(tiny, idx, today, [(FIXED[0][0], FIXED[0][1], "fixed")])
    assert tiny_rows[0]["status"] == "数据不全" and tiny_rows[0]["turnover_pct"] is None, tiny_rows[0]
    # 东财解析：kline 行格式（日期,开,收,高,低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率）
    em_rows = _parse_em_klines([
        "2026-11-23,90.00,91.00,92.00,89.00,100000,910000000.00,3.00,1.00,0.90,1.50",
        "2026-11-24,91.00,93.25,94.00,90.50,120000,1119000000.00,3.50,2.47,2.25,1.80",
    ])
    assert em_rows["2026-11-24"] == (93.25, 120000.0, 1119000000.0, 1.8), em_rows
    assert _em_secid("601100") == "1.601100" and _em_secid("300308") == "0.300308"
    assert _em_secid("000001") == "1.000001" and _em_secid("399001") == "0.399001"
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

    fixed_codes = [c for c, _ in FIXED]
    all_codes = []
    for c, _ in FIXED + CANDIDATES:
        if c not in all_codes:
            all_codes.append(c)
    stock_series = {}
    em_fallback = set()
    for code in all_codes:
        try:
            stock_series[code] = fetch_kline(code, today)
        except Exception as e:  # noqa: BLE001 - 同花顺失败改走东财备用源
            print("WARN %s 同花顺失败，改走东财备用源: %s" % (code, e))
            try:
                stock_series[code] = fetch_kline_em(code, today)
                em_fallback.add(code)
            except Exception as e2:  # noqa: BLE001 - 两源都失败才记空
                print("WARN %s 东财备用源也失败: %s" % (code, e2))
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
            except Exception as e:  # noqa: BLE001 - 第二遍同花顺仍失败再试东财
                print("WARN %s 第二遍同花顺失败，改走东财备用源: %s" % (code, e))
                try:
                    stock_series[code] = fetch_kline_em(code, today)
                    em_fallback.add(code)
                except Exception as e2:  # noqa: BLE001
                    print("WARN %s 第二遍仍失败: %s" % (code, e2))
    if not any(stock_series.get(c) for c in fixed_codes):
        print("ERROR: 固定 8 只全部拉取失败，不写文件。")
        return 1
    # 截断到目标日期（--date 补算过去某日时，窗口以该日收盘为准）
    stock_series = {c: [d for d in s if d[0] <= today] for c, s in stock_series.items()}
    if not any(stock_series.get(c) and stock_series[c][-1][0] == today for c in fixed_codes):
        print("目标日期 %s 的固定池收盘数据未取到，不写文件。" % today)
        return 0

    index_amounts = {}
    for code, _ in INDICES:
        try:
            for date, _c, _p, _v, amount, _t in fetch_kline(code, today):
                if date <= today:
                    index_amounts[date] = index_amounts.get(date, 0.0) + amount
        except Exception as e:  # noqa: BLE001 - 指数同花顺失败改走东财备用源
            print("WARN 指数 %s 同花顺失败，改走东财备用源: %s" % (code, e))
            try:
                for date, _c, _p, _v, amount, _t in fetch_kline_em(code, today):
                    if date <= today:
                        index_amounts[date] = index_amounts.get(date, 0.0) + amount
            except Exception as e2:  # noqa: BLE001 - 指数失败则指标 2 记 null
                print("WARN 指数 %s 东财备用源也失败: %s" % (code, e2))
        time.sleep(2)

    dynamic = select_dynamic(stock_series, today)
    selected = [(c, n, "fixed") for c, n in FIXED] + [(c, n, "dynamic") for c, n in dynamic]
    rows = compute_day(stock_series, index_amounts, today, selected, fallback_codes=em_fallback)
    ranked_n = sum(1 for c in all_codes if c not in fixed_codes
                   and stock_series.get(c) and stock_series[c][-1][0] == today
                   and stock_series[c][-1][5] is not None)

    data = {"version": 2, "window_days": WINDOW,
            "stocks": [{"code": c, "name": n, "slot": s} for c, n, s in selected],
            "universe": {"fixed": fixed_codes,
                         "dynamic": [c for c, _ in dynamic],
                         "dynamic_ranked_by": "当日换手率",
                         "candidates_total": len(all_codes) - len(fixed_codes),
                         "candidates_ranked": ranked_n},
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
    print("wrote %s: %d 只（固定 8 + 动态 %s），拥挤 %d 只（%s）" % (
        today, len(rows), "、".join(n for _, n in dynamic) or "无",
        len(crowded), "、".join(crowded) or "无"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
