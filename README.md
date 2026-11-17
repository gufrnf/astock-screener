# A 股投资助手 · 选股 / 择时 / 资金分配

一个**零第三方依赖**的工具集 + 交互式网页助手：命令行只用 Python 标准库，调用东方财富公开行情接口。
- `screen.py`：对全 A 股做多因子横截面打分，输出候选清单（**选什么**）。
- `trade_signals.py`：对候选股拉历史 K 线，算均线/MACD/RSI/KDJ/布林/量能，给出「何时入 / 何时出」信号（**何时买卖**）。
- `allocator.py`：用风险预算法把买卖信号转成可执行的资金分配方案——买多少股/多少金额/占多少仓位（**买多少**）。
- `index.html`：交互式网页助手，调总资金和风险参数即可实时生成分配方案、图表与 CSV（**助手软件**）。

- `ths_source.py`：同花顺行情数据源（历史日K + 当日实时），可作东方财富之外的第二数据源。

有 Python 3 就能跑，不需要 `pip install` 任何东西。在线助手：https://gufrnf.github.io/astock-screener/ ｜ 研报：https://gufrnf.github.io/astock-screener/report.html

## 双数据源：东方财富 / 同花顺

`trade_signals.py` 支持两套 K 线数据源，用 `--source` 切换：

```bash
python trade_signals.py -c 002409 601211                    # 东方财富（默认）
python trade_signals.py --source ths -c 002409 601211       # 同花顺
python trade_signals.py --source ths -s all -n 15 --json > signals_ths.json
```

- **东方财富**：`push2his.eastmoney.com`，前复权，接口稳定。
- **同花顺**：`d.10jqka.com.cn` 的 `last.js`（近140日历史）+ `today.js`（当日实时盘中价），不复权。模块 `ths_source.py` 可单独使用：`python ths_source.py 002409`。

两套数据源的信号方向通常一致，可交叉验证；价格略有差异（同花顺含当日实时、东财为最近交易日）。网页助手顶部可一键切换数据源查看两套信号。

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

## 资金分配（allocator.py）+ 助手软件

采用**风险预算法（固定分数风险）**：先定每笔最多亏总资金的比例（如 1%），再用 `风险预算 ÷ (入场价−止损价)` 反推可买股数，按 A股 100 股/手向下取整，并受单股仓位上限、组合总仓位上限约束。信号越强（评分越高）风险预算系数越大（最多 1.3x）。只给买入信号分配资金。

```bash
python allocator.py                              # 读 watch 信号，10万资金，默认参数
python allocator.py --capital 500000             # 50万资金
python allocator.py --risk 0.02 --max-total 0.9  # 单笔风险2%，总仓位上限90%
python allocator.py --source all --json          # 用全市场Top15信号，JSON输出
python allocator.py --capital 5000 --source ths  # 5千小额资金 + 同花顺信号
```

> **小额资金**：总资金可低至 100 元、按 100 元递增。由于 A股按 100 股/手整手买入，资金过低时高价股（如股价 500 元需 5 万/手）会自动跳过；若按风险预算算出的股数不足 1 手也不分配。网页助手在资金低于 2 万时会给出相应提示。

| 参数 | 默认 | 含义 |
|---|---|---|
| `--capital` | 100000 | 总资金（元），**可低至 100，按 100 元为单位取整** |
| `--risk` | 0.01 | 单笔风险比例，每笔最多亏总资金 1% |
| `--max-single` | 0.20 | 单股仓位上限 20% |
| `--max-total` | 0.80 | 组合总仓位上限 80%，其余留现金 |

输出每只股票的入场/止损/目标、建议手数与股数、金额、仓位%、最大亏损、盈亏比；并汇总组合总风险敞口（若全部触发止损的合计亏损）。**只给买点信号分配，卖点/观望不分配。**

### 交互式助手软件（index.html）

发布在 GitHub Pages 上的单页应用，本地双击也能开：
- 左侧调「总资金 / 单笔风险 / 单股上限 / 总仓位上限」，右侧**实时**生成分配方案
- 汇总卡片 + 分配明细表 + 资金分布饼图 + 仓位/盈亏比柱状图
- 一键导出 CSV、参数自动保存在本机浏览器
- 「当日信号」区展示所有观察标的的择时信号
- 前端分配算法与 `allocator.py` 完全一致（已逐项对照验证）

> 完整工作流：`trade_signals.py --json > signals_watch.json`（刷新信号）→ `allocator.py`（命令行方案）/ 打开网页（交互方案）。

## 在线页面

- 助手软件（首页）：https://gufrnf.github.io/astock-screener/
- 量化工具研报：https://gufrnf.github.io/astock-screener/report.html

## 风险提示

- 量化打分是**横截面相对排序**，不代表绝对收益，更不代表买入信号。
- 历史/当日因子对未来收益没有必然预测力，策略可能失效。
- 请结合基本面、政策、自身风险承受能力独立决策，必要时咨询持牌机构。
