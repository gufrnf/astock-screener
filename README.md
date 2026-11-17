# A 股多策略智能选股器 + 择时买卖点提醒

一个**零第三方依赖**的命令行工具集：只用 Python 标准库，调用东方财富公开行情接口。
- `screen.py`：对全 A 股做多因子横截面打分，输出候选清单（**选什么**）。
- `trade_signals.py`：对候选股拉历史 K 线，算均线/MACD/RSI/KDJ/布林/量能，给出「何时入 / 何时出」信号（**何时买卖**）。

有 Python 3 就能跑，不需要 `pip install` 任何东西。在线研报：https://gufrnf.github.io/astock-screener/

> ⚠️ **免责声明**：本工具输出仅为基于公开数据的量化筛选结果，仅供研究参考，**不构成任何投资建议**。市场有风险，投资需谨慎。

## 快速开始

```bash
python screen.py                       # 综合打分，Top 20
python screen.py -s value -n 15        # 低估值蓝筹策略，Top 15
python screen.py -s growth             # 高景气动量策略
python screen.py -s dividend           # 红利防御策略
```

## 四种策略

| 策略 | 参数 | 逻辑 | 适合 |
|---|---|---|---|
| 综合打分 | `-s all`（默认）| 估值/动量/资金/流动性加权 | 通用初筛 |
| 低估值蓝筹 | `-s value` | 低 PE + 低 PB + 流动性 + 主力资金 | 价值、左侧布局 |
| 高景气动量 | `-s growth` | 涨幅动量 + 量比 + 主力净流入 | 趋势、右侧跟随 |
| 红利防御 | `-s dividend` | 低估值 + 低波动 + 资金 | 稳健、防守 |

## 参数说明

```
-n, --top N          输出前 N 只（默认 20）
--min-cap 亿         最小总市值，默认 20
--max-cap 亿         最大总市值，默认 100000
--min-price 元       最低股价，默认 2
--max-price 元       最高股价，默认 500
--json               以 JSON 输出，便于程序集成
--limit N            拉取股票数量上限，默认 1500
```

示例：

```bash
python screen.py -s value --min-cap 100 --json > result.json   # 只看百亿以上，JSON 输出
python screen.py -s growth -n 30 --max-price 100               # 百元以下成长股 Top 30
```

## 打分方法

1. **风控过滤**：剔除亏损股（PE≤0）、ST/退市股、市值与价格越界个股。
2. **因子标准化**：对 7 个因子做横截面 z-score（PE/PB 取倒数转为"越低越优"）。
3. **加权合成**：不同策略用不同权重，得分越高表示在该策略维度上相对越优。
4. **排序输出**：按得分降序取 Top N。

因子包括：估值（PE、PB）、动量（涨跌幅）、量能（量比）、资金（主力净占比、主力净流入）、流动性（成交额对数）。

## 数据源与时效

- 数据源：东方财富公开行情接口 `push2.eastmoney.com`。
- 时效：接口返回的是**实时或最近交易日**行情，输出中带行情时间戳。
- A 股交易时段 9:30-11:30 / 13:00-15:00（北京时间）；非交易时段为最近一次收盘数据。

## 择时买卖点提醒（trade_signals.py）

在选股之上增加「择时」维度：先按策略选出候选，再逐个拉取近 120 日 K 线，用 6 类技术指标综合打分，输出关注买点 / 卖点 / 观望。

```bash
python trade_signals.py                       # 综合选股 Top10，逐个给买卖点
python trade_signals.py -s value -n 20        # 低估值策略 Top20
python trade_signals.py -c 002409 601211      # 指定代码，直接看买卖点
python trade_signals.py --json                # JSON 输出（便于推送）
python trade_signals.py --summary             # 只看信号分布统计
```

| 信号 | 分数阈值 | 含义 |
|---|---|---|
| ★ 关注买点（强）| ≥ +4.5 | 多指标强共振 |
| 关注买点 | +2 ~ +4.5 | 偏多 |
| 持有/观望 | -2 ~ +2 | 信号不明朗 |
| 关注卖点 | -4.5 ~ -2 | 偏空 |
| ▼ 关注卖点（强）| ≤ -4.5 | 多指标转空 |

六类信号：均线金叉/死叉与多空排列、MACD 金叉/死叉与柱状变化、RSI 超卖回升/超买回落、KDJ 超卖金叉/超买死叉、布林带上下轨、放量突破/跌破 20 日高低点。输出还附带现价、技术止损位、目标位的参考值（**非买卖建议**）。

**做每日自动提醒**：把命令加入定时任务（Windows 任务计划程序 / Linux cron）盘后运行，配合 `--json` 输出到文件；需要推送到手机时，可在脚本末尾把 JSON 结果 POST 到钉钉/企业微信机器人 webhook。示例：

```python
# 伪代码：钉钉推送（webhook 需自行创建群机器人获取）
import json, urllib.request
data = {'msgtype':'text','text':{'content':'今日买点信号：海立股份、长虹美菱'}}
req = urllib.request.Request(DINGTALK_WEBHOOK,
    data=json.dumps(data).encode(), headers={'Content-Type':'application/json'})
urllib.request.urlopen(req)
```

## 在线研报

`index.html` 是发布在 GitHub Pages 上的可视化研报：GitHub 量化工具检索清单 + 择时信号体系 + 当日真实买卖点 + 全市场选股结果。本地可直接双击打开。

## 风险提示

- 量化打分是**横截面相对排序**，不代表绝对收益，更不代表买入信号。
- 历史/当日因子对未来收益没有必然预测力，策略可能失效。
- 请结合基本面、政策、自身风险承受能力独立决策，必要时咨询持牌机构。
