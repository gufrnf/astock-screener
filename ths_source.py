#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同花顺行情数据源  |  THS Data Source
================================================================
提供同花顺公开行情接口，作为东方财富之外的数据源，供 trade_signals
计算买卖点使用。两个端点：
  · 历史日K：d.10jqka.com.cn/v6/line/hs_{code}/01/last.js  （近140交易日，不复权）
  · 当日实时：d.10jqka.com.cn/v6/line/hs_{code}/01/today.js （盘中快照）

K线字段顺序（last.js data）： 日期,开,高,低,收,成交量(手),成交额(元),...
实时字段（today.js，数字键）：1日期 7开 8高 9低 11现价 13量(股) 19额 1968584换手

接口返回 JSONP：quotebridge_v6_line_hs_XXXX_01_last({...})，需剥离回调壳。

用法：
  from ths_source import fetch_kline_ths
  name, ks = fetch_kline_ths("002409", days=120)
  # ks 格式与 trade_signals.decide() 兼容：
  #   [{date, open, close, high, low, vol, amount, chg}, ...]

  python ths_source.py 002409        # 命令行自测

说明：同花顺 last.js 为不复权数据，博主力除权当日会有跳变；短期技术分析影响有限，
      若需精确复权请以券商/交易所数据为准。
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://d.10jqka.com.cn/v6/line/hs_{code}/01/{kind}.js"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      "Referer": "https://stockpage.10jqka.com.cn/"}


def _get(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if i == retries - 1:
                return ""
            time.sleep(0.5 * (i + 1))
    return ""


def _jsonp(text):
    """剥离 quotebridge_xxx(...) 外壳，返回内部 dict。"""
    m = re.search(r"\((\{.*\})\)", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _sec(code):
    return str(code).strip()


def fetch_kline_ths(code, days=120, with_today=True):
    """
    拉取同花顺日K线，返回 (name, klines)。
    klines 元素：{date, open, close, high, low, vol, amount, chg}
    历史来自 last.js（近140日），当日实时来自 today.js 并拼成一根K线。
    """
    code = _sec(code)
    text = _get(BASE.format(code=code, kind="last"))
    d = _jsonp(text)
    if not d or "data" not in d:
        return "", []
    name = d.get("name", "")

    rows = []
    for seg in str(d["data"]).split(";"):
        p = seg.split(",")
        if len(p) < 7:
            continue
        date = p[0]
        date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else date
        prev_close = rows[-1]["close"] if rows else None
        close = _num(p[4])
        chg = ((close / prev_close - 1) * 100) if (prev_close and close) else None
        rows.append({
            "date": date_fmt,
            "open": _num(p[1]), "high": _num(p[2]), "low": _num(p[3]),
            "close": close,
            "vol": _num(p[5]), "amount": _num(p[6]), "chg": chg,
        })

    # 拼当日实时（today.js）
    if with_today:
        t = _jsonp(_get(BASE.format(code=code, kind="today")))
        q = t.get("hs_" + code, {})
        date = str(q.get("1", ""))
        price = _num(q.get("11"))
        if len(date) == 8 and price:
            date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
            op, hi, lo = _num(q.get("7")), _num(q.get("8")), _num(q.get("9"))
            vol_shares = _num(q.get("13"))        # 股
            amount = _num(q.get("19"))
            prev = rows[-1]["close"] if rows else None
            chg = ((price / prev - 1) * 100) if prev else None
            today_row = {
                "date": date_fmt,
                "open": op or price, "high": hi or price, "low": lo or price,
                "close": price,
                "vol": (vol_shares / 100) if vol_shares else None,  # 统一成"手"
                "amount": amount, "chg": chg,
            }
            # 避免重复：若最后一条已是当日则替换
            if rows and rows[-1]["date"] == date_fmt:
                rows[-1] = today_row
            else:
                rows.append(today_row)

    return name, rows[-days:]


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "002409"
    name, ks = fetch_kline_ths(code)
    print(f"同花顺 {code} {name}  共 {len(ks)} 条")
    for r in ks[-5:]:
        print(f"  {r['date']}  开{r['open']:.2f} 高{r['high']:.2f} 低{r['low']:.2f} "
              f"收{r['close']:.2f}  涨跌 {(r['chg'] or 0):+.2f}%")
