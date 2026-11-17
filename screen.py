#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A 股多策略智能选股器  |  A-share Multi-Strategy Screener
================================================================
零第三方依赖：仅用 Python 标准库，拉东方财富公开行情接口。

策略（可用 --strategy 切换）：
  value   低估值蓝筹   —— 低 PE / 低 PB / 高股息代理，剔除亏损与ST
  growth  高景气动量   —— 涨幅动量 + 量比 + 主力资金流入
  dividend 红利防御    —— 低估值 + 低波动代理（中小涨跌幅）
  all     综合打分（默认）—— 三策略 z-score 标准化后加权

用法：
  python screen.py                       # 综合打分，输出 Top 20
  python screen.py -s value -n 15        # 低估值策略 Top 15
  python screen.py -s growth --json      # 高景气策略，JSON 输出
  python screen.py --min-cap 100         # 只看总市值 > 100 亿
  python screen.py --exclude-st --min-price 2 --max-price 200

免责声明：本程序输出仅为基于公开数据的量化筛选结果，不构成任何投资建议。
市场有风险，投资需谨慎。
"""

import argparse
import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import datetime

API = "https://push2.eastmoney.com/api/qt/clist/get"
# 全 A 股：沪深主板 + 创业板 + 科创板 + 北交所
MARKETS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

FIELDS = {
    "f12": "code",        # 代码
    "f14": "name",        # 名称
    "f2": "price",        # 最新价
    "f3": "change",       # 涨跌幅 %
    "f5": "volume",       # 成交量(手)
    "f6": "amount",       # 成交额(元)
    "f8": "turnover",     # 换手率 %
    "f9": "pe",           # 市盈率TTM
    "f10": "volratio",    # 量比
    "f20": "mktcap",      # 总市值
    "f21": "floatcap",    # 流通市值
    "f23": "pb",          # 市净率
    "f62": "mainflow",    # 主力净流入(元)
    "f184": "mainratio",  # 主力净占比 %
    "f124": "ts",         # 时间戳
}


def num(v):
    """把接口值安全转成 float，' - ' 或 None 返回 None。"""
    if v is None or v == "-" or v == "":
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def fetch(page=1, size=100, order_field="f3", desc=True):
    """拉取一页行情。返回 (总数, 股票列表)。"""
    params = {
        "pn": page, "pz": size,
        "po": 1 if desc else 0, "np": 1,
        "fltt": 2, "invt": 2,
        "fid": order_field,
        "fs": MARKETS,
        "fields": ",".join(FIELDS.keys()),
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://quote.eastmoney.com/",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    block = data.get("data") or {}
    rows = block.get("diff") or []
    out = []
    for r in rows:
        item = {}
        for k, name in FIELDS.items():
            item[name] = num(r.get(k)) if k not in ("f12", "f14") else r.get(k)
        out.append(item)
    return block.get("total", 0), out


def fetch_all(max_items=6000, size=100):
    """分页拉全市场（默认按成交额排序，保证先拿到活跃个股）。"""
    total, _ = fetch(1, 1)
    total = min(total, max_items)
    all_rows, page = [], 1
    while len(all_rows) < total:
        _, rows = fetch(page, size, order_field="f6")  # f6 成交额降序
        if not rows:
            break
        all_rows.extend(rows)
        page += 1
    # 按代码去重
    seen, uniq = set(), []
    for r in all_rows:
        if r["code"] not in seen:
            seen.add(r["code"])
            uniq.append(r)
    return uniq


def filter_universe(rows, args):
    """基础风控过滤：剔除亏损、ST、低价/高价、市值过小/过大。"""
    out = []
    for r in rows:
        name = (r.get("name") or "").upper()
        if args.exclude_st and ("ST" in name or "退" in name):
            continue
        price = r.get("price")
        if price is None or price < args.min_price or price > args.max_price:
            continue
        pe, pb = r.get("pe"), r.get("pb")
        if pe is None or pe <= 0:            # 剔除亏损/无 PE
            continue
        if pb is None or pb <= 0:
            continue
        cap = (r.get("mktcap") or 0) / 1e8   # 亿元
        if cap < args.min_cap or cap > args.max_cap:
            continue
        r["cap_yi"] = cap
        out.append(r)
    return out


def zscore(values):
    """横截面标准化；样本不足或方差为 0 时返回中性 0。"""
    vals = [v for v in values if v is not None]
    n = len(vals)
    if n < 2:
        return [0.0] * len(values)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    std = math.sqrt(var)
    if std < 1e-12:
        return [0.0] * len(values)
    return [0.0 if v is None else (v - mean) / std for v in values]


def score(rows, strategy):
    """按策略做多因子打分（因子方向已处理：分数越高越优）。"""
    if not rows:
        return rows
    # 收集原始因子
    for r in rows:
        r["_momentum"] = r.get("change") or 0.0
        r["_volratio"] = r.get("volratio") or 1.0
        r["_mainratio"] = r.get("mainratio") or 0.0
        r["_pe_inv"] = 1.0 / (r.get("pe") or 1.0)   # PE 越低越好
        r["_pb_inv"] = 1.0 / (r.get("pb") or 1.0)   # PB 越低越好
        r["_lowvol"] = -abs(r.get("change") or 0.0)  # 涨跌绝对值越小越"稳"
        r["_liquidity"] = math.log10(max(r.get("amount") or 1, 1))

    def z(field):
        arr = zscore([r[field] for r in rows])
        for r, v in zip(rows, arr):
            r["z_" + field] = v

    for f in ("_momentum", "_volratio", "_mainratio", "_pe_inv", "_pb_inv", "_lowvol", "_liquidity"):
        z(f)

    weights = {
        "value":    {"z__pe_inv": 0.40, "z__pb_inv": 0.30, "z__liquidity": 0.15, "z__mainratio": 0.15},
        "growth":   {"z__momentum": 0.35, "z__volratio": 0.20, "z__mainratio": 0.30, "z__liquidity": 0.15},
        "dividend": {"z__pe_inv": 0.30, "z__pb_inv": 0.25, "z__lowvol": 0.30, "z__mainratio": 0.15},
        "all":      {"z__pe_inv": 0.20, "z__pb_inv": 0.15, "z__momentum": 0.20,
                     "z__mainratio": 0.20, "z__volratio": 0.10, "z__liquidity": 0.15},
    }
    w = weights.get(strategy, weights["all"])
    for r in rows:
        r["score"] = sum(r.get(k, 0.0) * v for k, v in w.items())
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows


STRATEGY_CN = {
    "value": "低估值蓝筹", "growth": "高景气动量",
    "dividend": "红利防御", "all": "综合打分",
}


def report(rows, strategy, topn, as_json, ts):
    if as_json:
        payload = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "market_data_timestamp": ts,
            "strategy": STRATEGY_CN.get(strategy, strategy),
            "universe_size": len(rows),
            "results": [{
                "rank": i + 1, "code": r["code"], "name": r["name"],
                "price": r.get("price"), "change_pct": r.get("change"),
                "pe": r.get("pe"), "pb": r.get("pb"),
                "mktcap_yi": round(r.get("cap_yi", 0), 1),
                "main_net_inflow_yi": round((r.get("mainflow") or 0) / 1e8, 3),
                "main_net_ratio_pct": r.get("mainratio"),
                "score": round(r["score"], 3),
            } for i, r in enumerate(rows[:topn])],
            "disclaimer": "仅为量化筛选结果，不构成投资建议。市场有风险，投资需谨慎。",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print("=" * 96)
    print(f"  A 股多策略智能选股器  |  {STRATEGY_CN.get(strategy, strategy)}  |  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"  候选池 {len(rows)} 只（已剔除亏损/ST/市值与价格越界）   行情时间戳 {ts}")
    print("=" * 96)
    hdr = f"{'#':>3}  {'代码':<7} {'名称':<10} {'现价':>8} {'涨跌%':>7} {'PE':>8} {'PB':>7} {'市值亿':>9} {'主力净占比%':>10} {'得分':>7}"
    print(hdr)
    print("-" * 96)
    for i, r in enumerate(rows[:topn]):
        chg = r.get("change")
        chg_s = f"{chg:+.2f}" if chg is not None else "-"
        print(f"{i+1:>3}  {r['code']:<7} {str(r['name']):<10} "
              f"{(r.get('price') or 0):>8.2f} {chg_s:>7} "
              f"{(r.get('pe') or 0):>8.1f} {(r.get('pb') or 0):>7.2f} "
              f"{r.get('cap_yi', 0):>9.0f} {(r.get('mainratio') or 0):>10.2f} "
              f"{r['score']:>+7.2f}")
    print("-" * 96)
    print("  说明：得分为横截面 z-score 加权，越高代表该策略维度上相对越优。")
    print("  免责声明：以上仅为基于公开数据的量化筛选，不构成投资建议。市场有风险，投资需谨慎。")


def main():
    ap = argparse.ArgumentParser(description="A 股多策略智能选股器（零依赖）")
    ap.add_argument("-s", "--strategy", choices=["all", "value", "growth", "dividend"],
                    default="all", help="选股策略（默认 all）")
    ap.add_argument("-n", "--top", type=int, default=20, help="输出前 N 只（默认 20）")
    ap.add_argument("--min-cap", type=float, default=20, help="最小总市值（亿元，默认 20）")
    ap.add_argument("--max-cap", type=float, default=100000, help="最大总市值（亿元）")
    ap.add_argument("--min-price", type=float, default=2, help="最低股价（元，默认 2）")
    ap.add_argument("--max-price", type=float, default=500, help="最高股价（元）")
    ap.add_argument("--exclude-st", action="store_true", default=True, help="剔除 ST/退市股（默认开启）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（便于程序调用）")
    ap.add_argument("--limit", type=int, default=1500, help="拉取股票数量上限（默认 1500）")
    args = ap.parse_args()

    print(f"[1/3] 正在拉取全市场行情（上限 {args.limit} 只）...", file=sys.stderr)
    rows = fetch_all(max_items=args.limit)
    print(f"      获取 {len(rows)} 只股票", file=sys.stderr)
    print(f"[2/3] 风控过滤 + 因子打分（策略：{STRATEGY_CN[args.strategy]}）...", file=sys.stderr)
    universe = filter_universe(rows, args)
    ranked = score(universe, args.strategy)
    print(f"      有效候选 {len(ranked)} 只", file=sys.stderr)
    ts = ""
    for r in rows:
        if r.get("ts"):
            ts = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M")
            break
    print("[3/3] 结果如下：\n", file=sys.stderr)
    report(ranked, args.strategy, args.top, args.json, ts)


if __name__ == "__main__":
    main()
