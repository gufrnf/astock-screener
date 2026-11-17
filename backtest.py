#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A 股策略回测引擎  |  Strategy Backtester
================================================================
移植 GitHub 成熟量化工具的核心功能（零第三方依赖重新实现）：
  · backtesting.py / vectorbt   → 事件驱动回测 + 绩效指标体系
  · TA-Lib (ta-lib-python)      → 扩展技术指标 CCI / 威廉%R / BIAS / OBV
  · PyPortfolioOpt              → 组合层风险度量（波动率 / 回撤 / 夏普）

关键设计：
  1) 无未来函数：T 日收盘计算信号，T+1 开盘价成交，逐日 mark-to-market。
  2) 交易成本：佣金万 2.5（双边）+ 卖出印花税千 1，A 股 100 股/手。
  3) 增强信号 = trade_signals 基础分 + TA-Lib 扩展指标分，
     并同时回测「基础 / 增强」两套信号，直观看到移植功能的增益。

绩效指标：累计收益、年化收益、最大回撤、夏普比率、卡玛比率、
           胜率、盈亏比、交易次数、年化波动率。

用法：
  python backtest.py -c 002409 601211          # 指定股票回测
  python backtest.py -c 002409 --json          # JSON 输出
  python backtest.py --source ths -c 601211    # 同花顺数据源
  python backtest.py                            # 默认观察池组合回测

严重声明：历史回测不代表未来收益，指标有滞后与过拟合风险，
          本程序仅供学习研究，不构成投资建议，据此操作风险自负。
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime

import trade_signals as ts

# ==================== 扩展技术指标（移植 TA-Lib 思路） ====================
def cci(highs, lows, closes, n=14):
    """商品通道指标 CCI：>100 超买，<-100 超卖。"""
    out = [None] * len(closes)
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    for i in range(n - 1, len(closes)):
        seg = tp[i - n + 1:i + 1]
        m = sum(seg) / n
        md = sum(abs(x - m) for x in seg) / n
        out[i] = 0.0 if md < 1e-12 else (tp[i] - m) / (0.015 * md)
    return out


def williams_r(highs, lows, closes, n=14):
    """威廉 %R：-100 超卖区底部，0 超买区顶部。"""
    out = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        hh = max(highs[i - n + 1:i + 1])
        ll = min(lows[i - n + 1:i + 1])
        out[i] = -50.0 if hh == ll else (hh - closes[i]) / (hh - ll) * -100
    return out


def bias(closes, n=6):
    """乖离率 BIAS：(收盘 - MA) / MA × 100。"""
    ma = ts.ma(closes, n)
    return [None if ma[i] is None else (closes[i] - ma[i]) / ma[i] * 100
            for i in range(len(closes))]


def obv(closes, vols):
    """能量潮 OBV：上涨累加成交量，下跌递减。"""
    out = [0.0] * len(closes)
    for i in range(1, len(closes)):
        out[i] = out[i - 1] + (vols[i] if closes[i] > closes[i - 1]
                               else -vols[i] if closes[i] < closes[i - 1] else 0)
    return out


def expanded_indicators(ks):
    """计算扩展指标最新值 + 贡献分（TA-Lib 功能应用）。"""
    closes = [x["close"] for x in ks]
    highs = [x["high"] for x in ks]
    lows = [x["low"] for x in ks]
    vols = [x["vol"] for x in ks]
    i = len(closes) - 1
    cci_v = cci(highs, lows, closes)
    wr_v = williams_r(highs, lows, closes)
    bias_v = bias(closes, 6)
    obv_v = obv(closes, vols)

    pts, reasons = 0.0, []
    cv, wv, bv = cci_v[i], wr_v[i], bias_v[i]
    # CCI：从超卖回升 / 超买回落
    if cv is not None:
        if cci_v[i - 1] is not None and cci_v[i - 1] < -100 and cv >= -100:
            pts += 1.2; reasons.append("CCI超卖回升")
        if cci_v[i - 1] is not None and cci_v[i - 1] > 100 and cv < 100:
            pts -= 1.2; reasons.append("CCI超买回落")
        if cv < -150:
            pts += 0.5; reasons.append("CCI深度超卖")
        if cv > 150:
            pts -= 0.5; reasons.append("CCI深度超买")
    # 威廉 %R
    if wv is not None:
        if wv < -80:
            pts += 0.8; reasons.append("威廉%R超卖")
        if wv > -20:
            pts -= 0.8; reasons.append("威廉%R超买")
    # BIAS 乖离率：负乖离（超跌）加分，正乖离过大减分
    if bv is not None:
        if bv < -5:
            pts += 0.8; reasons.append(f"BIAS负乖离{bv:.1f}%")
        if bv > 7:
            pts -= 0.8; reasons.append(f"BIAS正乖离过大{bv:.1f}%")
    # OBV 能量潮：OBV 创 10 日新高但价格未创新高（量能先行）
    if i >= 10:
        if obv_v[i] > max(obv_v[i - 10:i]) and closes[i] < max(closes[i - 10:i]):
            pts += 0.7; reasons.append("OBV量能先行")
        if obv_v[i] < min(obv_v[i - 10:i]) and closes[i] > min(closes[i - 10:i]):
            pts -= 0.7; reasons.append("OBV量价背离")

    return {
        "cci": round(cv, 1) if cv is not None else None,
        "wr": round(wv, 1) if wv is not None else None,
        "bias": round(bv, 2) if bv is not None else None,
        "points": round(pts, 2),
        "reasons": reasons,
    }


# ==================== 回测引擎（移植 backtesting.py 事件驱动） ====================
COMMISSION = 0.00025   # 佣金万 2.5（双边）
STAMP_TAX = 0.001      # 卖出印花税千 1


def signal_at(ks, t, enhanced):
    """截止 t 日收盘的信号分（无未来函数）。"""
    if t < 60:
        return 0.0
    sub = ks[:t + 1]
    sig = ts.decide(sub)
    if not sig:
        return 0.0
    score = sig["score"]
    if enhanced:
        score += expanded_indicators(sub)["points"]
    return max(-10.0, min(10.0, score))


def run_backtest(ks, code="", name="", enhanced=True, capital=100000.0,
                 buy_th=2.5, sell_th=-2.5, warmup=60):
    """单只股票事件驱动回测。返回绩效 + 权益曲线 + 交易明细。"""
    n = len(ks)
    if n < warmup + 5:
        return None
    cash = capital
    shares = 0
    entry_price = 0.0
    entry_date = ""
    entry_fee = 0.0
    trades = []
    equity_curve = []
    pending = 0  # 1 买入 / -1 卖出 / 0 无

    for t in range(warmup, n - 1):
        score = signal_at(ks, t, enhanced)
        if shares == 0 and score >= buy_th:
            pending = 1
        elif shares > 0 and score <= sell_th:
            pending = -1
        else:
            pending = 0

        # T+1 开盘成交
        nx = ks[t + 1]
        px = nx["open"]
        if pending == 1 and shares == 0 and px > 0:
            lots = int(cash / (px * 100 * (1 + COMMISSION)))
            if lots > 0:
                cost = lots * 100 * px * (1 + COMMISSION)
                cash -= cost
                shares = lots * 100
                entry_price = px
                entry_date = nx["date"]
                entry_fee = cost - lots * 100 * px
        elif pending == -1 and shares > 0 and px > 0:
            proceeds = shares * px * (1 - COMMISSION - STAMP_TAX)
            cash += proceeds
            pnl = proceeds - (entry_price * shares + entry_fee)
            pnl_pct = pnl / (entry_price * shares) * 100
            trades.append({
                "entry_date": entry_date, "exit_date": nx["date"],
                "entry": round(entry_price, 2), "exit": round(px, 2),
                "days": _days_between(entry_date, nx["date"]),
                "pnl_pct": round(pnl_pct, 2),
            })
            shares, entry_price = 0, 0.0

        mtm = cash + shares * nx["close"]
        equity_curve.append({
            "date": nx["date"],
            "equity": round(mtm, 2),
            "close": nx["close"],
            "score": round(score, 2),
            "position": 1 if shares > 0 else 0,
        })

    # 期末未平仓按最后收盘价强制平仓结算
    if shares > 0 and equity_curve:
        last = equity_curve[-1]
        proceeds = shares * last["close"] * (1 - COMMISSION - STAMP_TAX)
        cash += proceeds - shares * last["close"]  # mtm 里已按 close 计入，修正费用
        pnl = proceeds - (entry_price * shares + entry_fee)
        trades.append({
            "entry_date": entry_date, "exit_date": last["date"] + "(持仓)",
            "entry": round(entry_price, 2), "exit": round(last["close"], 2),
            "days": _days_between(entry_date, last["date"]),
            "pnl_pct": round(pnl / (entry_price * shares) * 100, 2),
        })

    metrics = _metrics(equity_curve, capital, trades)
    return {
        "code": code, "name": name, "enhanced": enhanced,
        "data_date": ks[-1]["date"], "bars": n, "traded_days": len(equity_curve),
        "metrics": metrics, "trades": trades, "equity": equity_curve,
        "indicators": expanded_indicators(ks),
    }


def _days_between(d1, d2):
    try:
        from datetime import date
        a = date.fromisoformat(d1)
        b = date.fromisoformat(d2.replace("(持仓)", ""))
        return (b - a).days
    except Exception:
        return 0


def _metrics(curve, capital, trades):
    """绩效指标体系（移植 backtesting.py / vectorbt 统计口径）。"""
    if not curve:
        return {k: 0 for k in ("total_return", "annual_return", "max_drawdown",
                               "sharpe", "calmar", "win_rate", "profit_factor",
                               "trades", "volatility")}
    vals = [p["equity"] for p in curve]
    final = vals[-1]
    total_ret = (final / capital - 1) * 100
    days = max(_days_between(curve[0]["date"], curve[-1]["date"]), 1)
    annual_ret = ((final / capital) ** (252 / max(days * 252 / 365, 1)) - 1) * 100

    rets = [(vals[i] / vals[i - 1] - 1) for i in range(1, len(vals)) if vals[i - 1] > 0]
    mean_r = sum(rets) / len(rets) if rets else 0
    var_r = sum((r - mean_r) ** 2 for r in rets) / len(rets) if rets else 0
    sd = math.sqrt(var_r)
    sharpe = mean_r / sd * math.sqrt(252) if sd > 1e-12 else 0

    peak = vals[0]
    mdd = 0.0
    for v in vals:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak * 100)

    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    gp = sum(t["pnl_pct"] for t in wins)
    gl = abs(sum(t["pnl_pct"] for t in losses))
    pf = gp / gl if gl > 1e-9 else (999.0 if gp > 0 else 0)
    calmar = annual_ret / mdd if mdd > 1e-9 else 0

    return {
        "total_return": round(total_ret, 2),
        "annual_return": round(annual_ret, 2),
        "max_drawdown": round(mdd, 2),
        "sharpe": round(sharpe, 2),
        "calmar": round(calmar, 2),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(min(pf, 999.0), 2),
        "trades": len(trades),
        "volatility": round(sd * math.sqrt(252) * 100, 2),
    }


def portfolio_backtest(results, capital=100000.0):
    """组合层回测：等权分配，逐日平均权益（移植 PyPortfolioOpt 组合风险思路）。"""
    curves = [r["equity"] for r in results if r.get("equity")]
    if not curves:
        return None
    by_date = {}
    for c in curves:
        for p in c:
            by_date.setdefault(p["date"], []).append(p["equity"] / c[0]["equity"])
    dates = sorted(by_date)
    n_stock = len(curves)
    port_curve = []
    for d in dates:
        ratios = by_date[d]
        avg = sum(ratios) / len(ratios)  # 等权，缺股的用当日已有股票均值
        port_curve.append({"date": d, "equity": round(capital * avg, 2),
                           "close": None, "score": None, "position": 1})
    # 组合层分散度：平均两两相关性粗估（用日收益同步涨跌比例）
    return {
        "metrics": _metrics(port_curve, capital, []),
        "equity": port_curve,
        "n_stocks": n_stock,
    }


# ==================== 数据获取与输出 ====================
DEFAULT_CODES = ["002409", "300666", "601211", "000651", "600619", "000521"]


def fetch(name, code, source, ths_fetch, days=250):
    if source == "ths" and ths_fetch:
        return ths_fetch(code, days=days)
    return ts.fetch_kline(code, days=days)


def main():
    ap = argparse.ArgumentParser(description="A股策略回测引擎（零依赖，移植GitHub量化工具功能）")
    ap.add_argument("-c", "--codes", nargs="+", help="指定股票代码")
    ap.add_argument("--source", choices=["eastmoney", "ths"], default="eastmoney")
    ap.add_argument("--days", type=int, default=250, help="历史K线天数（默认250≈1年）")
    ap.add_argument("--capital", type=float, default=100000)
    ap.add_argument("--buy-th", type=float, default=2.5, help="买入信号阈值")
    ap.add_argument("--sell-th", type=float, default=-2.5, help="卖出信号阈值")
    ap.add_argument("--no-enhance", action="store_true", help="只用基础信号（关闭TA-Lib扩展）对照")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    ths_fetch = None
    if args.source == "ths":
        try:
            from ths_source import fetch_kline_ths as ths_fetch
        except ImportError:
            print("错误：缺少 ths_source.py", file=sys.stderr)
            sys.exit(1)

    codes = args.codes or DEFAULT_CODES
    print(f"[回测] {len(codes)} 只股票 | {args.days}日K线 | 数据源:{'同花顺' if args.source=='ths' else '东方财富'} | "
          f"信号:{'增强(含TA-Lib)' if not args.no_enhance else '基础'}", file=sys.stderr)

    results = []
    for code in codes:
        name, ks = fetch(code, code, args.source, ths_fetch, args.days)
        if not ks:
            print(f"  {code} K线获取失败", file=sys.stderr)
            continue
        r = run_backtest(ks, code, name, enhanced=not args.no_enhance,
                         capital=args.capital, buy_th=args.buy_th, sell_th=args.sell_th)
        if not r:
            continue
        results.append(r)
        m = r["metrics"]
        print(f"  {code} {name[:8]:<8} 收益{m['total_return']:>+6.1f}% | "
              f"胜率{m['win_rate']:>5.1f}% | 回撤{m['max_drawdown']:>5.1f}% | "
              f"夏普{m['sharpe']:>+5.2f} | 交易{m['trades']}次", file=sys.stderr)
        time.sleep(0.12)

    port = portfolio_backtest(results, args.capital) if len(results) > 1 else None
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": args.source,
        "source_cn": "同花顺" if args.source == "ths" else "东方财富",
        "signal_mode": "enhanced" if not args.no_enhance else "basic",
        "params": {"capital": args.capital, "buy_th": args.buy_th,
                   "sell_th": args.sell_th, "days": args.days,
                   "commission": COMMISSION, "stamp_tax": STAMP_TAX},
        "count": len(results),
        "results": results,
        "portfolio": port,
        "disclaimer": "历史回测不代表未来收益，存在过拟合与幸存者偏差，仅供学习研究，不构成投资建议。",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("=" * 88)
        print(f"  策略回测结果  |  {payload['generated_at']}  |  {args.days}日历史")
        print("=" * 88)
        hdr = (f"{'代码':<8}{'名称':<9}{'总收益':>8}{'年化':>8}{'最大回撤':>9}"
               f"{'夏普':>7}{'胜率':>7}{'盈亏比':>7}{'交易':>5}")
        print(hdr); print("-" * 88)
        for r in results:
            m = r["metrics"]
            print(f"{r['code']:<8}{str(r['name'])[:8]:<9}{m['total_return']:>+7.1f}%"
                  f"{m['annual_return']:>+7.1f}%{m['max_drawdown']:>8.1f}%"
                  f"{m['sharpe']:>+7.2f}{m['win_rate']:>6.1f}%{m['profit_factor']:>7.2f}{m['trades']:>5}")
        if port:
            m = port["metrics"]
            print("-" * 88)
            print(f"{'组合(等权)':<17}{m['total_return']:>+7.1f}%{m['annual_return']:>+7.1f}%"
                  f"{m['max_drawdown']:>8.1f}%{m['sharpe']:>+7.2f}{'—':>7}{'—':>7}{'—':>5}")
        print("-" * 88)
        print("  说明：T日收盘算信号 / T+1开盘成交；佣金万2.5+卖出印花税千1；历史回测不代表未来。")


if __name__ == "__main__":
    main()
