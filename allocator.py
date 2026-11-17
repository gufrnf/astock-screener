#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资金分配引擎  |  Capital Allocator
================================================================
把「择时买卖点信号」转成「可执行的资金分配方案」。

核心方法：风险预算法（Risk-based Position Sizing / 固定分数风险）
  1. 每笔交易只愿意承担总资金的固定比例风险（默认 1%），即"风险预算"。
  2. 个股每股风险 = 入场价 − 止损价。
  3. 可买股数 = 风险预算 ÷ 每股风险  →  按 A股 100 股/手向下取整。
  4. 这样每笔亏损被限制在风险预算内，而不会因为股价高就买得少/多而失衡。
  5. 信号越强（评分越高），风险预算系数越大（最多 1.3x）。
  6. 受两道约束：单股仓位上限、组合总仓位上限（留给现金）。

只给买入类信号分配资金；持有/卖点信号不分配。

用法：
  python allocator.py                          # 读 watch 信号，10万资金，默认参数
  python allocator.py --capital 500000         # 50万资金
  python allocator.py --risk 0.02 --max-total 0.9   # 调高单笔风险与总仓位
  python allocator.py --source all             # 用全市场Top15信号
  python allocator.py --json                   # JSON 输出

数据：读取同目录 signals_watch.json / signals_data.json（由 trade_signals.py --json 生成）。

严重声明：本程序为量化实验与仓位管理教学演示，不构成任何投资建议。
         仓位与止损仅为技术参考，据此操作风险自负。
"""

import argparse
import json
import math
import os
from datetime import datetime

LOT = 100  # A股 1 手 = 100 股（科创板规则不同，此处保守统一按100股）


def load_signals(source):
    """读取信号 JSON，统一返回 results 列表。"""
    base = os.path.dirname(os.path.abspath(__file__))
    fn = "signals_watch.json" if source == "watch" else "signals_data.json"
    path = os.path.join(base, fn)
    if not os.path.exists(path):
        return [], ""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return d.get("results", []), d.get("data_date", "")


def boost_for(score):
    """信号强度 → 风险预算放大系数（强信号给更多子弹，但有上限）。"""
    if score >= 6:
        return 1.3
    if score >= 4:
        return 1.15
    if score >= 2.5:
        return 1.0
    return 0.8


def allocate(signals, capital, risk=0.01, max_single=0.20, max_total=0.80):
    """
    风险预算法资金分配。
      risk       单笔风险比例（每笔最多亏总资金的多少）
      max_single 单股仓位上限（占总资金）
      max_total  组合总仓位上限（其余留现金）
    返回 (plans, summary)。
    """
    buys = [s for s in signals if s.get("action_key") in ("buy", "strong_buy")]
    buys.sort(key=lambda x: x.get("score", 0), reverse=True)

    plans = []
    invested = 0.0
    total_risk_amount = 0.0   # 组合总风险敞口（若全部触发止损的亏损合计）

    for s in buys:
        entry = s.get("entry_ref") or s.get("price")
        stop = s.get("stop_ref")
        target = s.get("target_ref")
        if not entry or not stop or entry <= 0 or stop >= entry:
            continue
        per_share_risk = entry - stop
        score = s.get("score", 0)

        # 1) 风险预算 → 理论股数
        budget = capital * risk * boost_for(score)
        raw_shares = budget / per_share_risk

        # 2) 单股仓位上限约束（金额）
        max_amount_single = capital * max_single
        max_shares_single = max_amount_single / entry

        # 3) 组合总仓位剩余约束
        remain = capital * max_total - invested
        if remain <= 0:
            break
        max_shares_total = remain / entry

        shares = min(raw_shares, max_shares_single, max_shares_total)
        shares = math.floor(shares / LOT) * LOT   # 向下取整到整手
        if shares < LOT:
            continue

        amount = shares * entry
        risk_amount = shares * per_share_risk      # 该笔最大亏损
        reward = shares * (target - entry) if target and target > entry else 0.0
        rr = (target - entry) / per_share_risk if target and per_share_risk > 0 else 0.0

        invested += amount
        total_risk_amount += risk_amount

        plans.append({
            "code": s.get("code"), "name": s.get("name"),
            "signal": s.get("action"), "score": score,
            "boost": round(boost_for(score), 2),
            "entry": round(entry, 2), "stop": round(stop, 2),
            "target": round(target, 2) if target else None,
            "shares": int(shares), "lots": int(shares // LOT),
            "amount": round(amount, 2),
            "weight": round(amount / capital * 100, 2),          # 仓位%
            "risk_amount": round(risk_amount, 2),
            "risk_pct": round(risk_amount / capital * 100, 2),   # 该笔最大亏损占总资金%
            "reward": round(reward, 2),
            "rr": round(rr, 2),
            "reasons": (s.get("reasons") or [])[:3],
        })

    plans.sort(key=lambda x: x["weight"], reverse=True)
    cash = capital - invested
    summary = {
        "capital": capital,
        "params": {"risk": risk, "max_single": max_single, "max_total": max_total, "lot": LOT},
        "positions": len(plans),
        "invested": round(invested, 2),
        "invested_pct": round(invested / capital * 100, 2),
        "cash": round(cash, 2),
        "cash_pct": round(cash / capital * 100, 2),
        "total_risk_amount": round(total_risk_amount, 2),
        "total_risk_pct": round(total_risk_amount / capital * 100, 2),
        "method": "风险预算法（固定分数风险）",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return plans, summary


def fmt_money(v):
    if v >= 1e8:
        return f"{v/1e8:.2f}亿"
    if v >= 1e4:
        return f"{v/1e4:.2f}万"
    return f"{v:.0f}"


def print_report(plans, summary, data_date):
    cap = summary["capital"]
    print("=" * 108)
    print(f"  A股资金分配方案  |  总资金 {fmt_money(cap)}  |  信号日期 {data_date}  |  {summary['generated_at']}")
    p = summary["params"]
    print(f"  参数：单笔风险 {p['risk']*100:.1f}%  单股上限 {p['max_single']*100:.0f}%  "
          f"总仓位上限 {p['max_total']*100:.0f}%  1手={p['lot']}股")
    print("=" * 108)
    if not plans:
        print("  当前无买入信号，建议空仓观望（现金 100%）。")
        print("=" * 108)
        return
    hdr = (f"{'代码':<8}{'名称':<9}{'信号':<9}{'评分':>5}{'入场':>8}{'止损':>8}{'目标':>8}"
           f"{'手数':>5}{'股数':>7}{'金额':>10}{'仓位%':>7}{'最大亏':>9}{'盈亏比':>7}")
    print(hdr); print("-" * 108)
    for x in plans:
        tgt = f"{x['target']:.2f}" if x["target"] else "-"
        print(f"{x['code']:<8}{str(x['name'])[:8]:<9}{x['signal']:<9}{x['score']:>+5.1f}"
              f"{x['entry']:>8.2f}{x['stop']:>8.2f}{tgt:>8} {x['lots']:>5}{x['shares']:>7}"
              f"{fmt_money(x['amount']):>10}{x['weight']:>7.1f}"
              f"{fmt_money(x['risk_amount']):>9}{x['rr']:>7.1f}")
    print("-" * 108)
    print(f"  合计投入 {fmt_money(summary['invested'])}（{summary['invested_pct']:.1f}%）  |  "
          f"保留现金 {fmt_money(summary['cash'])}（{summary['cash_pct']:.1f}%）")
    print(f"  组合总风险敞口 {fmt_money(summary['total_risk_amount'])}（{summary['total_risk_pct']:.2f}%）"
          f" —— 若全部止损，合计亏损约为总资金的 {summary['total_risk_pct']:.2f}%")
    print("  说明：股数按风险预算法算出并向下取整到整手；仓位受单股/总仓位上限约束。")
    print("  免责声明：仓位与止损仅为技术参考，本方案为量化实验，不构成投资建议，据此操作风险自负。")


def main():
    ap = argparse.ArgumentParser(description="资金分配引擎（风险预算法）")
    ap.add_argument("--capital", type=float, default=100000, help="总资金，默认 10万")
    ap.add_argument("--risk", type=float, default=0.01, help="单笔风险比例，默认 0.01（1%%）")
    ap.add_argument("--max-single", type=float, default=0.20, help="单股仓位上限，默认 0.20（20%%）")
    ap.add_argument("--max-total", type=float, default=0.80, help="组合总仓位上限，默认 0.80（80%%）")
    ap.add_argument("--source", choices=["watch", "all"], default="watch", help="信号来源")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args()

    signals, data_date = load_signals(a.source)
    plans, summary = allocate(signals, a.capital, a.risk, a.max_single, a.max_total)

    if a.json:
        print(json.dumps({
            "data_date": data_date, "summary": summary, "plans": plans,
            "disclaimer": "量化实验与仓位管理教学演示，不构成投资建议，据此操作风险自负。",
        }, ensure_ascii=False, indent=2))
    else:
        print_report(plans, summary, data_date)


if __name__ == "__main__":
    main()
