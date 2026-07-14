# 做T机会检查

本文档定义基于持仓和日线行情的做T机会筛查。

目标不是直接给出买卖指令，而是把“今天是否值得人工看盘验证正T/反T”变成可复核的纪律检查。

## 1. 适用边界

`tools/check_t_trade_opportunity.py` 使用日线数据，只适合盘前或盘后筛查：

- 可以判断趋势、回踩、短线过热、振幅、止损距离和仓位风险。
- 不能替代分时确认。
- 不能确认实际成交价、盘口流动性和日内买卖点。
- 不能绕过交易计划、退出计划和人工确认。

A 股做T必须基于已有底仓。没有底仓时，本工具会阻断。

## 2. 结论

| 结论 | 含义 |
| --- | --- |
| `blocked` | 不做T，优先处理止损、仓位、停牌跌停或数据缺失问题。 |
| `watch_only` | 没有清晰正T或反T结构，只观察。 |
| `positive_t_candidate` | 中期趋势未破，且出现回踩或靠近短期均线，可列入正T观察。 |
| `reverse_t_candidate` | 短线涨幅或短期均线偏离较高，可列入反T观察。 |
| `needs_manual_review` | 正T和反T证据同时出现，必须人工拆解，不自动定方向。 |

## 3. 默认阻断条件

- 最新交易日停牌。
- 最新交易日跌停。
- 最新收盘价触发止损价，或距离止损价低于 `t_trading.near_stop_block_pct`。
- 缺少最新价格或止损价。
- 没有可识别底仓。
- 单票仓位超过投资体系上限。
- 日线数量不足以覆盖中期窗口。

持仓风险提醒和做T执行阻断使用不同阈值：

- `risk.near_stop_warning_pct`：持仓日检提醒阈值，默认 3%，用于提示进入止损观察区。
- `t_trading.near_stop_block_pct`：做T执行阻断阈值。个人配置可设为 1%，表示 1%-3% 区间只允许观察，不允许因为“想做T”而抬高止损或忽略退出风险。

## 4. 命令

```bash
python3 tools/check_t_trade_opportunity.py \
  --position positions/POS-示例.yaml \
  --daily-bars data/processed/daily_bars.csv
```

输出 JSON：

```bash
python3 tools/check_t_trade_opportunity.py \
  --position positions/POS-示例.yaml \
  --daily-bars data/processed/daily_bars.csv \
  --json
```

批量检查多个持仓并自动刷新行情：

```bash
python3 tools/check_portfolio_t_opportunities.py \
  --positions 'positions/POS-EASTMONEY-*.yaml' \
  --auto-fetch
```

批量结果同时保留 `market_setup`（行情形态）和 `conclusion`（账户风控后的可执行结论）。行情出现候选形态但缺少止损、仓位超限时，仍然不得执行。

## 5. 执行护栏

- 正T必须先定义买入价、卖出价、失败后是否转为加仓，以及最大新增仓位。
- 反T必须先定义卖出价、买回价、失败后是否接受减仓，以及保留底仓比例。
- 低于做T阻断阈值、触发止损、仓位超限、停牌或跌停时不做T。
- 处于持仓提醒阈值内但未低于做T阻断阈值时，只能进入人工观察，不应扩大仓位。
- 若日线筛查通过，仍需要分时走势确认后才能记录真实执行。
