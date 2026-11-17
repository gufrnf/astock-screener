#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A 股择时买卖点提醒系统  |  Entry / Exit Signal Alert
================================================================
在自动选股结果之上，对候选股拉取历史日 K 线，计算均线 / MACD /
RSI / KDJ / 布林带 / 量能等多维技术指标，综合打分给出：
    「何时入（买入）· 何时出（卖出）· 继续持有 · 观望」提示。

零第三方依赖：仅用 Python 标准库，拉东方财富公开行情接口。

核心信号（ BUY 为正分，SELL 为负分，阈值见 decide() ）：
  · 均线：MA5 上穿 MA10（金叉）/ 多头排列 / 站上 MA20   → 入
           MA5 下穿 MA10（死叉）/ 空头排列 / 跌破 MA20   → 出
  · MACD：DIF 上穿 DEA（金叉）/ 红柱放大                 → 入
           DIF 下穿 DEA（死叉）/ 绿柱放大                 → 出
  · RSI ：从超卖(<35)回升                                → 入
           超买(>70)且回落                                → 出
  · KDJ ：J 从超卖区金叉上行                             → 入
           J 超买区(>100)死叉下行                         → 出
  · 布林：回踩下轨企稳反弹                               → 入
           冲上轨后放量滞涨                              → 出
  · 量能：放量突破近 20 日高点（量比>1.5）               → 入
           放量跌破近 20 日低点                          → 出

用法：
  python trade_signals.py                       # 综合选股 Top10 逐个给出买卖点
  python trade_signals.py -n 20                 # 看前 20 只
  python trade_signals.py -c 002409 300666      # 指定代码，直接看买卖点
  python trade_signals.py -s value              # 用低估值策略先选股再看买卖点
  python trade_signals.py --json                # JSON 输出（便于程序调用/推送）
  python trade_signals.py --summary             # 只输出汇总统计（各信号档位数量）

严重声明：
  本程序为技术分析量化实验，指标信号具有滞后性与假信号，
  不构成任何投资建议。 exemplifies 仅供学习研究，据此操作风险自负。
"""

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

QUOTE_API = "https://push2.eastmoney.com/api/qt/clist/get"
KLINE_API = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
MARKETS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://quote.eastmoney.com/"}

# ---------------- 基础工具 ----------------
def num(v):
    if v is None or v in ("-", ""):
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def http_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def secid(code):
    """东方财富 secid：沪市 1.xxxxxx，深/创/北 0.xxxxxx。"""
    c = str(code)
    return ("1." + c) if c.startswith(("5", "6", "9")) else ("0." + c)


# ---------------- 行情快照（复用选股逻辑） ----------------
FIELDS = {
    "f12": "code", "f14": "name", "f2": "price", "f3": "change",
    "f5": "volume", "f6": "amount", "f8": "turnover", "f9": "pe",
    "f10": "volratio", "f20": "mktcap", "f23": "pb", "f62": "mainflow",
    "f184": "mainratio", "f124": "ts",
}


def fetch_page(page=1, size=100, order_field="f6", desc=True):
    params = {"pn": page, "pz": size, "po": 1 if desc else 0, "np": 1,
              "fltt": 2, "invt": 2, "fid": order_field, "fs": MARKETS,
              "fields": ",".join(FIELDS.keys())}
    data = http_json(QUOTE_API + "?" + urllib.parse.urlencode(params))
    block = data.get("data") or {}
    out = []
    for r in (block.get("diff") or []):
        item = {}
        for k, name in FIELDS.items():
            item[name] = num(r.get(k)) if k not in ("f12", "f14") else r.get(k)
        out.append(item)
    return block.get("total", 0), out


def fetch_universe(limit=1200, size=100):
    total, _ = fetch_page(1, 1)
    total = min(total, limit)
    rows, page = [], 1
    while len(rows) < total:
        _, batch = fetch_page(page, size)
        if not batch:
            break
        rows.extend(batch)
        page += 1
    seen, uniq = set(), []
    for r in rows:
        if r["code"] not in seen:
            seen.add(r["code"])
            uniq.append(r)
    return uniq[:total]


def clean_universe(rows):
    out = []
    for r in rows:
        name = (r.get("name") or "").upper()
        if "ST" in name or "退" in name or name.startswith("N"):
            continue
        p = r.get("price")
        if p is None or not (2 <= p <= 500):
            continue
        if not (r.get("pe") and r.get("pe") > 0):
            continue
        if not (r.get("pb") and r.get("pb") > 0):
            continue
        cap = (r.get("mktcap") or 0) / 1e8
        if not (20 <= cap <= 100000):
            continue
        r["cap_yi"] = cap
        out.append(r)
    return out


def zscore(vals):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return [0.0] * len(vals)
    m = sum(v) / len(v)
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / len(v))
    if sd < 1e-12:
        return [0.0] * len(vals)
    return [0.0 if x is None else (x - m) / sd for x in vals]


def preselect(rows, strategy, topn):
    """先用估值/动量/资金给全市场打分，选出值得看买卖点的候选。"""
    for r in rows:
        r["_pe_inv"] = 1.0 / (r.get("pe") or 1)
        r["_pb_inv"] = 1.0 / (r.get("pb") or 1)
        r["_mom"] = r.get("change") or 0.0
        r["_flow"] = r.get("mainratio") or 0.0
        r["_liq"] = math.log10(max(r.get("amount") or 1, 1))
    for f in ("_pe_inv", "_pb_inv", "_mom", "_flow", "_liq"):
        z = zscore([r[f] for r in rows])
        for r, v in zip(rows, z):
            r["z" + f] = v
    W = {
        "value": {"z_pe_inv": .4, "z_pb_inv": .3, "z_flow": .2, "z_liq": .1},
        "growth": {"z_mom": .35, "z_flow": .35, "z_liq": .2, "z_pb_inv": .1},
        "dividend": {"z_pe_inv": .35, "z_pb_inv": .3, "z_flow": .2, "z_liq": .15},
        "all": {"z_pe_inv": .2, "z_pb_inv": .15, "z_mom": .2, "z_flow": .25, "z_liq": .2},
    }[strategy]
    for r in rows:
        r["prescore"] = sum(r.get(k, 0) * w for k, w in W.items())
    rows.sort(key=lambda x: x["prescore"], reverse=True)
    return rows[:topn]


# ---------------- 历史 K 线 ----------------
def fetch_kline(code, days=120):
    params = {"secid": secid(code),
              "fields1": "f1,f2,f3,f4,f5,f6",
              "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
              "klt": 101, "fqt": 1, "end": "20500101", "lmt": days}
    for attempt in range(3):
        try:
            data = http_json(KLINE_API + "?" + urllib.parse.urlencode(params))
            d = data.get("data") or {}
            ks = []
            for line in (d.get("klines") or []):
                p = line.split(",")
                # 日期,开,收,高,低,成交量,成交额,振幅,涨跌幅,涨跌额,换手
                ks.append({"date": p[0], "open": num(p[1]), "close": num(p[2]),
                           "high": num(p[3]), "low": num(p[4]), "vol": num(p[5]),
                           "amount": num(p[6]), "chg": num(p[8])})
            return d.get("name") or "", ks
        except Exception:
            if attempt == 2:
                return "", []
            time.sleep(0.6 * (attempt + 1))
    return "", []


# ---------------- 技术指标 ----------------
def ma(vals, n):
    out = []
    for i in range(len(vals)):
        out.append(sum(vals[i - n + 1:i + 1]) / n if i >= n - 1 else None)
    return out


def ema(vals, n):
    out, k = [], 2.0 / (n + 1)
    prev = None
    for v in vals:
        prev = v if prev is None else k * v + (1 - k) * prev
        out.append(prev)
    return out


def macd(closes, fast=12, slow=26, sig=9):
    ef, es = ema(closes, fast), ema(closes, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema(dif, sig)
    hist = [(a - b) * 2 for a, b in zip(dif, dea)]
    return dif, dea, hist


def rsi(closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gain = loss = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gain += max(d, 0); loss += max(-d, 0)
    gain /= n; loss /= n
    out[n] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = (gain * (n - 1) + max(d, 0)) / n
        loss = (loss * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def kdj(closes, highs, lows, n=9):
    k_list, d_list, j_list = [], [], []
    k = d = 50.0
    for i in range(len(closes)):
        if i < n - 1:
            k_list.append(None); d_list.append(None); j_list.append(None)
            continue
        hh = max(highs[i - n + 1:i + 1]); ll = min(lows[i - n + 1:i + 1])
        rsv = 50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100
        k = (2 * k + rsv) / 3
        d = (2 * d + k) / 3
        k_list.append(k); d_list.append(d); j_list.append(3 * k - 2 * d)
    return k_list, d_list, j_list


def boll(closes, n=20, k=2):
    mid = ma(closes, n)
    up, dn = [], []
    for i in range(len(closes)):
        if mid[i] is None:
            up.append(None); dn.append(None); continue
        seg = closes[i - n + 1:i + 1]
        m = mid[i]
        sd = math.sqrt(sum((x - m) ** 2 for x in seg) / n)
        up.append(m + k * sd); dn.append(m - k * sd)
    return up, mid, dn


# ---------------- 信号决策 ----------------
def decide(ks):
    """输入日 K 线列表，输出买卖点信号 dict。"""
    if len(ks) < 60:
        return None
    closes = [x["close"] for x in ks]
    highs = [x["high"] for x in ks]
    lows = [x["low"] for x in ks]
    vols = [x["vol"] for x in ks]
    i = len(closes) - 1          # 今日
    price = closes[i]

    ma5, ma10, ma20, ma60 = ma(closes, 5), ma(closes, 10), ma(closes, 20), ma(closes, 60)
    dif, dea, hist = macd(closes)
    rsi_v = rsi(closes)
    K, D, J = kdj(closes, highs, lows)
    up, mid, dn = boll(closes)

    score = 0.0
    reasons = []

    # 1) 均线
    if ma5[i - 1] and ma10[i - 1]:
        if ma5[i - 1] <= ma10[i - 1] and ma5[i] > ma10[i]:
            score += 2.0; reasons.append("MA5上穿MA10(金叉)")
        if ma5[i - 1] >= ma10[i - 1] and ma5[i] < ma10[i]:
            score -= 2.0; reasons.append("MA5下穿MA10(死叉)")
    if ma5[i] is not None and ma10[i] is not None and ma20[i] is not None and ma60[i] is not None:
        if ma5[i] > ma10[i] > ma20[i] > ma60[i]:
            score += 1.5; reasons.append("均线多头排列")
        if ma5[i] < ma10[i] < ma20[i] < ma60[i]:
            score -= 1.5; reasons.append("均线空头排列")
    if ma20[i]:
        if closes[i - 1] < ma20[i - 1] and price > ma20[i]:
            score += 1.0; reasons.append("站上MA20")
        if closes[i - 1] > ma20[i - 1] and price < ma20[i]:
            score -= 1.0; reasons.append("跌破MA20")

    # 2) MACD
    if dif[i - 1] is not None:
        if dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]:
            score += 2.0; reasons.append("MACD金叉")
        if dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]:
            score -= 2.0; reasons.append("MACD死叉")
    if hist[i] is not None and hist[i - 1] is not None:
        if hist[i] > 0 and hist[i] > hist[i - 1]:
            score += 0.8; reasons.append("MACD红柱放大")
        if hist[i] < 0 and hist[i] < hist[i - 1]:
            score -= 0.8; reasons.append("MACD绿柱放大")

    # 3) RSI
    rv = rsi_v[i]
    if rv is not None:
        if rsi_v[i - 1] is not None and rsi_v[i - 1] < 35 and rv >= 35:
            score += 1.2; reasons.append("RSI超卖回升")
        if rsi_v[i - 1] is not None and rsi_v[i - 1] > 70 and rv < 70:
            score -= 1.2; reasons.append("RSI超买回落")
        if rv > 75:
            score -= 0.5; reasons.append("RSI高位")

    # 4) KDJ
    if J[i] is not None and J[i - 1] is not None:
        if J[i - 1] < 20 and J[i] >= J[i - 1]:
            score += 1.0; reasons.append("KDJ超卖金叉")
        if J[i - 1] > 100 and J[i] < J[i - 1]:
            score -= 1.0; reasons.append("KDJ超买死叉")

    # 5) 布林带
    if dn[i] and up[i]:
        if lows[i] <= dn[i] * 1.01 and price > dn[i]:
            score += 1.0; reasons.append("回踩布林下轨企稳")
        if price >= up[i] * 0.99 and closes[i] < closes[i - 1]:
            score -= 1.0; reasons.append("触布林上轨滞涨")

    # 6) 量能突破
    vma5 = ma(vols, 5)
    hi20 = max(highs[i - 20:i]); lo20 = min(lows[i - 20:i])
    if vma5[i] and vma5[i] > 0:
        vr = vols[i] / vma5[i]
        if vr > 1.5 and price >= hi20 * 0.995 and closes[i] > closes[i - 1]:
            score += 1.5; reasons.append(f"放量突破20日高(量比{vr:.1f})")
        if vr > 1.5 and price <= lo20 * 1.005 and closes[i] < closes[i - 1]:
            score -= 1.5; reasons.append(f"放量跌破20日低(量比{vr:.1f})")

    # 归一化到 -10 ~ +10
    score = max(-10.0, min(10.0, score))
    action = decide_action(score)

    # 参考价位（仅技术参考，非买卖建议）
    entry = round(price, 2)
    stop = round(max(lo20, ma20[i] or 0, price * 0.92), 2)
    if stop >= entry:
        stop = round(entry * 0.93, 2)
    target = round(max(hi20, entry * 1.08), 2)

    return {
        "score": round(score, 2),
        "action": action["label"],
        "action_key": action["key"],
        "reasons": reasons,
        "price": round(price, 2),
        "ma5": round(ma5[i], 2) if ma5[i] else None,
        "ma20": round(ma20[i], 2) if ma20[i] else None,
        "rsi": round(rv, 1) if rv else None,
        "entry_ref": entry,
        "stop_ref": stop,
        "target_ref": target,
        "hi20": round(hi20, 2),
        "lo20": round(lo20, 2),
        "last_date": ks[-1]["date"],
        "chg": ks[-1].get("chg"),
    }


def decide_action(score):
    if score >= 4.5:
        return {"key": "strong_buy", "label": "★ 关注买点（强）"}
    if score >= 2.0:
        return {"key": "buy", "label": "关注买点"}
    if score <= -4.5:
        return {"key": "strong_sell", "label": "▼ 关注卖点（强）"}
    if score <= -2.0:
        return {"key": "sell", "label": "关注卖点"}
    return {"key": "hold", "label": "持有/观望"}


# ---------------- 报告 ----------------
def print_report(results, elapsed):
    print("=" * 112)
    print(f"  A 股择时买卖点提醒  |  {datetime.now():%Y-%m-%d %H:%M:%S}  |  数据日期 {results[0]['signal']['last_date'] if results else '-'}")
    print("=" * 112)
    hdr = (f"{'代码':<8}{'名称':<10}{'现价':>8}{'信号':<18}{'分':>6}  {'入参考':>8}{'止损':>8}{'目标':>8}  依据")
    print(hdr); print("-" * 112)
    for r in results:
        s = r["signal"]
        why = "、".join(s["reasons"][:4]) if s["reasons"] else "无显著信号"
        print(f"{r['code']:<8}{str(r['name'])[:9]:<10}{s['price']:>8.2f}  {s['action']:<16}"
              f"{s['score']:>+5.1f}  {s['entry_ref']:>8.2f}{s['stop_ref']:>8.2f}{s['target_ref']:>8.2f}  {why}")
    print("-" * 112)
    cnt = {}
    for r in results:
        cnt[r["signal"]["action"]] = cnt.get(r["signal"]["action"], 0) + 1
    print("  信号分布：" + "  ".join(f"{k}×{v}" for k, v in sorted(cnt.items())))
    print(f"  耗时 {elapsed:.1f}s | 入参考=现价，止损/目标为技术参考位，非买卖建议")
    print("  免责声明：技术分析有滞后性与假信号，本结果不构成投资建议，据此操作风险自负。")


def build_json(results, source="eastmoney"):
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "ths" if source == "ths" else "eastmoney",
        "source_cn": "同花顺" if source == "ths" else "东方财富",
        "data_date": results[0]["signal"]["last_date"] if results else "",
        "count": len(results),
        "legend": {
            "strong_buy": "关注买点(强)", "buy": "关注买点",
            "hold": "持有/观望", "sell": "关注卖点", "strong_sell": "关注卖点(强)",
        },
        "results": [{
            "code": r["code"], "name": r["name"],
            "prescore": round(r.get("prescore", 0), 3),
            **r["signal"],
        } for r in results],
        "disclaimer": "技术分析有滞后性与假信号，仅为量化实验，不构成投资建议。市场有风险，投资需谨慎。",
    }


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser(description="A 股择时买卖点提醒（零依赖）")
    ap.add_argument("-n", "--top", type=int, default=10, help="先选股再看买卖点的数量（默认10）")
    ap.add_argument("-c", "--codes", nargs="+", help="指定股票代码，跳过选股直接看买卖点")
    ap.add_argument("-s", "--strategy", choices=["all", "value", "growth", "dividend"], default="all")
    ap.add_argument("--limit", type=int, default=1200, help="选股时拉取股票数量上限")
    ap.add_argument("--source", choices=["eastmoney", "ths"], default="eastmoney",
                    help="K线数据源：eastmoney 东方财富（默认）/ ths 同花顺")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--summary", action="store_true", help="仅输出信号分布统计")
    args = ap.parse_args()

    # 同花顺数据源按需加载（零依赖，本地模块）
    ths_fetch = None
    if args.source == "ths":
        try:
            from ths_source import fetch_kline_ths as ths_fetch
        except ImportError:
            print("错误：未找到 ths_source.py，请把该文件放在同目录", file=sys.stderr)
            sys.exit(1)

    t0 = time.time()

    if args.codes:
        targets = [{"code": c, "name": "", "prescore": 0} for c in args.codes]
        print(f"[1/2] 指定 {len(targets)} 只代码，直接拉取历史K线...", file=sys.stderr)
    else:
        print(f"[1/2] 拉取全市场行情（上限{args.limit}）并预选股...", file=sys.stderr)
        raw = fetch_universe(args.limit)
        print(f"      获取 {len(raw)} 只，风控过滤中...", file=sys.stderr)
        clean = clean_universe(raw)
        print(f"      有效 {len(clean)} 只，按[{args.strategy}]打分选 Top{args.top}...", file=sys.stderr)
        targets = preselect(clean, args.strategy, args.top)

    src_cn = "同花顺" if args.source == "ths" else "东方财富"
    print(f"[2/2] 逐只拉取日K线并计算买卖点信号（共{len(targets)}只，数据源：{src_cn}）...", file=sys.stderr)
    results = []
    for idx, t in enumerate(targets, 1):
        if ths_fetch:
            name, ks = ths_fetch(t["code"], days=120)
        else:
            name, ks = fetch_kline(t["code"])
        if not ks:
            print(f"      [{idx}/{len(targets)}] {t['code']} K线获取失败，跳过", file=sys.stderr)
            continue
        sig = decide(ks)
        if not sig:
            print(f"      [{idx}/{len(targets)}] {t['code']} 数据不足，跳过", file=sys.stderr)
            continue
        results.append({"code": t["code"], "name": name or t.get("name", ""),
                        "prescore": t.get("prescore", 0), "signal": sig})
        print(f"      [{idx}/{len(targets)}] {t['code']} {name or ''} -> {sig['action']} ({sig['score']:+.1f})",
              file=sys.stderr)
        time.sleep(0.15)

    results.sort(key=lambda x: x["signal"]["score"], reverse=True)
    elapsed = time.time() - t0

    if args.json:
        print(json.dumps(build_json(results, args.source), ensure_ascii=False, indent=2))
    elif args.summary:
        cnt = {}
        for r in results:
            k = r["signal"]["action"]
            cnt[k] = cnt.get(k, 0) + 1
        print(f"数据日期: {results[0]['signal']['last_date'] if results else '-'}  分析 {len(results)} 只")
        for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
            print(f"  {k:<20} {v:>3} 只  {'█' * v}")
    else:
        print_report(results, elapsed)


if __name__ == "__main__":
    main()
