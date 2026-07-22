# 个人 A 股投资决策与复盘系统

本项目的目标不是做一个通用行情软件，也不是替代东方财富、同花顺等成熟交易终端。

项目成立的初衷是：围绕个人投资体系，建立一套可验证、可复盘、可控制风险的选股与交易决策系统。

## 北极星

更懂我的投资体系和策略，并且能强迫我用可验证、可复盘、可控制风险的方式做决策。

## 产品定位

本系统是一个个人投资决策操作系统，核心职责包括：

- 理解并固化个人投资体系、交易偏好和风险边界。
- 将选股、买入、持仓、卖出、复盘流程结构化。
- 所有决策必须能追溯到数据、规则、策略或事实证据。
- 在交易前执行纪律校验，避免冲动、重仓、追高和无计划交易。
- 持续沉淀交易记录，帮助识别策略问题和执行问题。

## 不做什么

第一阶段明确不做：

- 不做通用金融资讯门户。
- 不做社区、股吧或舆情娱乐化功能。
- 不承诺预测短期涨跌。
- 不直接给出无证据的买卖结论。
- 不默认接入自动下单。
- 不在没有人工确认和风控约束的情况下执行交易。

## 核心闭环

```text
投资体系配置
  -> 股票池筛选
  -> 策略信号生成
  -> 交易计划校验
  -> 持仓风险跟踪
  -> 卖出纪律执行
  -> 交易复盘归因
  -> 策略迭代
```

## 文档

- [需求文档](./docs/需求文档.md)
- [投资体系配置](./docs/投资体系配置.md)
- [投资体系配置文件](./config/investment-profile.example.yaml)
- [每日决策流程](./docs/每日决策流程.md)
- [运行产物与 Git 边界](./docs/运行产物与Git边界.md)
- [日常检查流水线](./docs/日常检查流水线.md)
- [每日操作摘要](./docs/每日操作摘要.md)
- [交易计划与风控校验](./docs/交易计划与风控校验.md)
- [数据目录与股票基础信息导入](./docs/数据目录与股票基础信息导入.md)
- [日线行情数据导入](./docs/日线行情数据导入.md)
- [财务核心指标导入](./docs/财务核心指标导入.md)
- [估值指标导入](./docs/估值指标导入.md)
- [价值质量策略筛选](./docs/价值质量策略筛选.md)
- [趋势强度基础因子](./docs/趋势强度基础因子.md)
- [多周期技术指标](./docs/多周期技术指标.md)
- [趋势强度策略筛选](./docs/趋势强度策略筛选.md)
- [事件催化策略筛选](./docs/事件催化策略筛选.md)
- [多策略候选池合并](./docs/多策略候选池合并.md)
- [观察池报告](./docs/观察池报告.md)
- [观察池流水线](./docs/观察池流水线.md)
- [候选池质量检查](./docs/候选池质量检查.md)
- [候选股生成交易计划草稿](./docs/候选股生成交易计划草稿.md)
- [交易计划补全辅助](./docs/交易计划补全辅助.md)
- [交易计划草稿质量检查](./docs/交易计划草稿质量检查.md)
- [交易计划准入门禁](./docs/交易计划准入门禁.md)
- [候选股到交易计划门禁流程](./docs/候选股到交易计划门禁流程.md)
- [交易执行记录](./docs/交易执行记录.md)
- [交易执行偏差检查](./docs/交易执行偏差检查.md)
- [执行记录生成持仓](./docs/执行记录生成持仓.md)
- [东方财富持仓导入](./docs/东方财富持仓导入.md)
- [导入持仓风险计划补全](./docs/导入持仓风险计划补全.md)
- [持仓日检](./docs/持仓日检.md)
- [组合持仓日检](./docs/组合持仓日检.md)
- [做T机会检查](./docs/做T机会检查.md)
- [持仓准实时监控](./docs/持仓准实时监控.md)
- [持仓动作矩阵回测](./docs/持仓动作矩阵回测.md)
- [退出计划](./docs/退出计划.md)
- [退出计划检查](./docs/退出计划检查.md)
- [卖出执行记录](./docs/卖出执行记录.md)
- [卖出执行检查](./docs/卖出执行检查.md)
- [卖出执行生成复盘](./docs/卖出执行生成复盘.md)
- [交易复盘质量检查](./docs/交易复盘质量检查.md)
- [执行闭环总检查](./docs/执行闭环总检查.md)
- [执行闭环报告示例](./docs/执行闭环报告示例.md)
- [执行闭环修复任务](./docs/执行闭环修复任务.md)
- [交易复盘分析](./docs/交易复盘分析.md)
- [复盘冷静期检查](./docs/复盘冷静期检查.md)
- [策略健康检查](./docs/策略健康检查.md)
- [策略复核任务](./docs/策略复核任务.md)
- [策略配置变更记录](./docs/策略配置变更记录.md)
- [策略配置版本快照](./docs/策略配置版本快照.md)
- [复盘维护流水线](./docs/复盘维护流水线.md)
- [交易计划示例文件](./templates/trade-plan.example.yaml)
- [持仓记录示例文件](./templates/position.example.yaml)
- [执行闭环样例数据](./samples/execution_loop/README.md)

## 当前可执行工具

### 导入股票基础信息

```bash
python3 tools/import_stock_universe.py \
  --input samples/stock_universe.sample.csv \
  --output data/processed/stock_universe.csv \
  --metadata-output data/metadata/stock_universe.import.json
```

### 过滤可交易股票池

```bash
python3 tools/filter_universe.py \
  --profile config/investment-profile.example.yaml \
  --input data/processed/stock_universe.csv \
  --output data/processed/tradable_universe.csv \
  --report-output data/metadata/tradable_universe.filter.json
```

### 导入日线行情

自动拉取：

```bash
python3 tools/fetch_daily_bars_sina.py \
  --codes 600000 300750 \
  --output data/processed/daily_bars.csv
```

从持仓文件自动拉取：

```bash
python3 tools/fetch_daily_bars_sina.py \
  --positions positions/*.yaml \
  --output data/processed/daily_bars.csv
```

手工 CSV 导入：

```bash
python3 tools/import_daily_bars.py \
  --input samples/daily_bars.sample.csv \
  --output data/processed/daily_bars.csv \
  --metadata-output data/metadata/daily_bars.import.json
```

### 导入财务核心指标

```bash
python3 tools/import_financial_metrics.py \
  --input samples/financial_metrics.sample.csv \
  --output data/processed/financial_metrics.csv \
  --metadata-output data/metadata/financial_metrics.import.json
```

### 导入估值指标

```bash
python3 tools/import_valuation_metrics.py \
  --input samples/valuation_metrics.sample.csv \
  --output data/processed/valuation_metrics.csv \
  --metadata-output data/metadata/valuation_metrics.import.json
```

### 筛选价值质量候选股

```bash
python3 tools/screen_value_quality.py \
  --profile config/investment-profile.example.yaml \
  --financial-metrics data/processed/financial_metrics.csv \
  --valuation-metrics data/processed/valuation_metrics.csv \
  --output data/processed/value_quality_candidates.csv \
  --metadata-output data/metadata/value_quality_candidates.json
```

### 计算趋势强度基础因子

```bash
python3 tools/calc_trend_factors.py \
  --daily-bars data/processed/daily_bars.csv \
  --universe data/processed/tradable_universe.csv \
  --output data/processed/trend_factors.csv \
  --metadata-output data/metadata/trend_factors.json \
  --windows 5,20
```

### 计算多周期技术指标

```bash
python3 tools/calc_technical_indicators.py \
  --daily-bars data/processed/daily_bars.csv \
  --positions 'positions/POS-EASTMONEY-*.yaml' \
  --output data/metadata/technical-indicators.json \
  --markdown-output reports/technical-indicators.md
```

该工具基于本地 OHLCV 日线聚合日线、周线和月线，计算 `MACD(12,26,9)`、`BOLL(20,2)`、`RSI(6,14)`、`KDJ(9,3,3)`、`ATR(14)` 和 20 根量比。它计算的是技术证据，不直接生成买卖动作。

### 筛选趋势强度候选股

```bash
python3 tools/screen_trend_strength.py \
  --profile config/investment-profile.example.yaml \
  --factors data/processed/trend_factors.csv \
  --output data/processed/trend_candidates.csv \
  --metadata-output data/metadata/trend_candidates.json
```

### 计算行业强度因子

```bash
python3 tools/calc_industry_strength.py \
  --daily-bars data/processed/daily_bars.csv \
  --universe data/processed/tradable_universe.csv \
  --output data/processed/industry_strength_factors.csv \
  --metadata-output data/metadata/industry_strength_factors.json \
  --windows 5,20
```

### 筛选事件催化候选股

```bash
python3 tools/screen_event_catalyst.py \
  --profile config/investment-profile.example.yaml \
  --events data/processed/event_catalyst_events.csv \
  --output data/processed/event_catalyst_candidates.csv \
  --metadata-output data/metadata/event_catalyst_candidates.json
```

### 合并多策略候选池

```bash
python3 tools/merge_candidate_pool.py \
  --trend-candidates data/processed/trend_candidates.csv \
  --value-quality-candidates data/processed/value_quality_candidates.csv \
  --event-candidates data/processed/event_catalyst_candidates.csv \
  --universe data/processed/tradable_universe.csv \
  --industry-strength data/processed/industry_strength_factors.csv \
  --output data/processed/candidate_pool.csv \
  --metadata-output data/metadata/candidate_pool.json
```

### 生成观察池报告

```bash
python3 tools/generate_watchlist_report.py \
  --candidates data/processed/candidate_pool.csv \
  --output reports/watchlist.md
```

### 检查候选池质量

```bash
python3 tools/check_candidate_pool.py \
  --candidates data/processed/candidate_pool.csv
```

### 一键生成观察池

```bash
python3 tools/run_watchlist_pipeline.py \
  --daily-bars data/processed/daily_bars.csv \
  --financial-metrics data/processed/financial_metrics.csv \
  --valuation-metrics data/processed/valuation_metrics.csv \
  --event-catalyst-events data/processed/event_catalyst_events.csv \
  --universe data/processed/tradable_universe.csv \
  --report-output reports/watchlist.md
```

### 运行日常检查流水线（推荐）

```bash
python3 tools/run_daily_check_pipeline.py
```

该命令会先刷新执行闭环总检查，再生成每日操作摘要，避免日报读取旧的闭环元数据。如果当天已经生成 `data/metadata/holding-action-draft.json`、`data/metadata/portfolio-action-matrix-backtests.json` 或 `data/metadata/realtime-decision-cards.json`，日报会同时汇总持仓趋势状态、价格动作矩阵、规则回测和实时决策卡；该流水线不自动生成这些处置草案或日内产物。

### 生成每日操作摘要（单独补跑）

```bash
python3 tools/generate_daily_summary.py \
  --output reports/daily-summary.md \
  --json-output data/metadata/daily-summary.json
```

### 执行闭环总检查（单独补跑）

```bash
python3 tools/check_execution_loop.py \
  --output reports/execution-loop-check.md \
  --json-output data/metadata/execution-loop-check.json
```

### 生成执行闭环修复任务

```bash
python3 tools/generate_execution_fix_tasks.py
```

### 交易计划风控校验

```bash
python3 tools/check_trade_plan_quality.py \
  --plan plans/TP-示例.yaml
```

```bash
python3 tools/risk_check.py \
  --profile config/investment-profile.example.yaml \
  --plan templates/trade-plan.example.yaml
```

统一门禁：

```bash
python3 tools/check_trade_plan_gate.py \
  --profile config/investment-profile.example.yaml \
  --plan plans/TP-示例.yaml \
  --strategy-health data/metadata/strategy-health.json
```

输出 JSON：

```bash
python3 tools/risk_check.py --json
```

### 新建交易计划草稿

```bash
python3 tools/new_trade_plan.py \
  --code 600000 \
  --name 示例股票 \
  --exchange SSE \
  --industry 示例行业 \
  --strategy trend_strength \
  --planned-buy-price 10 \
  --stop-loss-price 9.2 \
  --position-pct 5 \
  --buy-reason "填写你的买入假设" \
  --key-evidence "填写关键证据" \
  --risk "填写反证和风险" \
  --stop-loss-condition "填写止损条件" \
  --take-profit-condition "填写止盈条件" \
  --invalidation-condition "填写失效条件"
```

默认输出到 `plans/` 目录。真实交易计划默认不提交到 Git。

### 从候选池生成交易计划草稿

```bash
python3 tools/new_trade_plan_from_candidate.py \
  --candidates data/processed/candidate_pool.csv \
  --code 300750 \
  --name 宁德时代 \
  --exchange SZSE \
  --industry 电力设备 \
  --planned-buy-price 200 \
  --stop-loss-price 185 \
  --position-pct 5
```

生成后仍必须补充止盈、失效条件，并执行风控校验。

### 补全交易计划草稿

```bash
python3 tools/complete_trade_plan.py \
  --plan plans/TP-示例.yaml \
  --stop-loss-condition "收盘价跌破止损价。" \
  --take-profit-condition "达到计划目标区后根据量价分批止盈。" \
  --invalidation-condition "趋势或基本面证据失效。" \
  --review-focus "候选池证据是否被市场验证。" \
  --mark-ready
```

### 从候选股直接准备交易计划并执行门禁

```bash
python3 tools/prepare_trade_plan_from_candidate.py \
  --candidates data/processed/candidate_pool.csv \
  --code 300750 \
  --name 宁德时代 \
  --exchange SZSE \
  --industry 电力设备 \
  --planned-buy-price 200 \
  --stop-loss-price 185 \
  --position-pct 5 \
  --stop-loss-condition "收盘价跌破 185。" \
  --take-profit-condition "达到计划目标区后分批止盈。" \
  --invalidation-condition "趋势或估值证据失效。" \
  --review-focus "候选池证据是否被市场验证。" \
  --mark-ready
```

### 新建交易执行记录

```bash
python3 tools/new_trade_execution.py \
  --plan plans/TP-示例.yaml \
  --mode paper \
  --execution-date 2026-07-07 \
  --execution-price 200 \
  --shares 100 \
  --position-pct 5 \
  --user-confirmed \
  --note "按计划模拟成交。"
```

如果 `data/metadata/review-cooldown.json` 显示冷静期已触发，或 `data/metadata/strategy-health.json` 显示当前策略暂停新开仓，买入执行默认阻断；确需例外时必须传入 `--allow-cooldown-exception --cooldown-exception-reason "..." --user-confirmed`。

### 检查交易执行偏差

```bash
python3 tools/check_trade_execution.py \
  --execution executions/EXEC-示例.yaml
```

### 从执行记录生成持仓

```bash
python3 tools/new_position_from_execution.py \
  --execution executions/EXEC-示例.yaml \
  --current-price 201 \
  --days-held 1 \
  --note "从执行记录生成持仓。"
```

### 新建持仓记录

```bash
python3 tools/new_position.py \
  --plan plans/TP-示例.yaml \
  --entry-date 2026-07-07 \
  --entry-price 10 \
  --current-price 10.5 \
  --position-pct 5 \
  --shares 1000 \
  --note "按计划建仓"
```

默认输出到 `positions/` 目录。真实持仓记录默认不提交到 Git。

### 持仓日检

### 东方财富持仓导入

```bash
python3 tools/import_eastmoney_positions.py \
  --input data/raw/eastmoney_positions.csv \
  --total-assets 100000 \
  --default-stop-loss-pct 8
```

该工具读取东方财富导出的持仓 CSV，生成项目标准 `positions/*.yaml`。不接收券商账号密码，不自动登录，不自动下单。

### 持仓日检

```bash
python3 tools/update_position_daily.py \
  --position positions/POS-示例.yaml \
  --current-price 201 \
  --days-held 1 \
  --note "成交额继续放大，趋势仍在。" \
  --check-output data/metadata/POS-示例.daily-check.json
```

### 组合持仓日检

```bash
python3 tools/check_portfolio_positions.py \
  --positions positions/*.yaml \
  --output data/metadata/portfolio_positions.check.json
```

### 持仓风险检查

```bash
python3 tools/position_check.py \
  --profile config/investment-profile.example.yaml \
  --position positions/POS-示例.yaml
```

输出 JSON：

```bash
python3 tools/position_check.py --position positions/POS-示例.yaml --json
```

### 做T机会检查

```bash
python3 tools/check_t_trade_opportunity.py \
  --position positions/POS-示例.yaml \
  --daily-bars data/processed/daily_bars.csv \
  --auto-fetch
```

该工具基于持仓、止损、仓位和最近日线行情筛查正T/反T观察机会。日线结论只用于盘前/盘后筛查，真实执行仍需要分时确认和人工记录。

批量刷新行情并检查多个持仓：

```bash
python3 tools/check_portfolio_t_opportunities.py \
  --positions 'positions/POS-EASTMONEY-*.yaml' \
  --auto-fetch
```

批量结果区分行情形态 `market_setup` 与账户风控结论 `conclusion`，避免把技术观察信号直接当成可执行指令。

为导入持仓生成风险计划补全草案：

```bash
python3 tools/complete_imported_position_plan.py \
  --positions 'positions/POS-EASTMONEY-*.yaml' \
  --daily-bars data/processed/daily_bars.csv \
  --stop-loss-pct-from-entry 12
```

草案会列出缺失字段、止损参考位、失效条件、止盈/降仓条件和下一步，不直接修改持仓文件。

根据批量检查生成保守的持仓处置草案：

```bash
python3 tools/build_holding_action_draft.py \
  --t-report data/metadata/portfolio-t-opportunities.check.json
```

草案只进行风险排序、趋势状态归类和价格动作矩阵整理，不自动设置止损价或生成订单。每只持仓会输出：

- 趋势状态：如止损风险、趋势转弱、回踩观察、高位过热、趋势正常。
- 关键价格触发：如止损价、止损上方 3%、5/20 日均线、近期高低点。
- 对应动作：如生成退出计划、禁止补仓、正T/反T观察、复核止盈或买入假设。
- 执行边界：所有动作仍必须经过交易计划、退出计划、执行记录和人工确认。

自动补齐持仓行业、估值、最新财务指标和近期公告：

```bash
python3 tools/fetch_holding_research.py \
  --positions 'positions/POS-EASTMONEY-*.yaml' \
  --update-position-industries
```

将基本面与公告快照接入持仓处置草案：

```bash
python3 tools/build_holding_action_draft.py \
  --t-report data/metadata/portfolio-t-opportunities.check.json \
  --research-report data/metadata/holding-research.json
```

财务阈值和公告标题关键词只用于筛查，必须核对正式财报或公告原文后才能形成交易结论。

回测单只持仓的趋势状态和动作矩阵：

```bash
python3 tools/backtest_holding_action_matrix.py \
  --position positions/POS-EASTMONEY-示例.yaml \
  --daily-bars data/processed/daily_bars.csv
```

该回测逐日生成当时可见的趋势状态和价格动作矩阵，并统计触发后 1/3/5/10/20 日收益与回撤；它不模拟真实成交，也不构成买卖建议。可用 `--stop-loss-price` 做不修改持仓文件的假设止损价回测。

批量回测组合持仓：

```bash
python3 tools/run_portfolio_action_matrix_backtests.py \
  --positions 'positions/POS-EASTMONEY-*.yaml' \
  --daily-bars data/processed/daily_bars.csv \
  --stop-loss-pct-from-entry 12
```

启动持仓准实时监控：

```bash
python3 tools/monitor_intraday_positions.py \
  --positions 'positions/POS-EASTMONEY-*.yaml' \
  --total-assets 25480 \
  --interval 30 \
  --archive-interval 300
```

停止监控：

```bash
python3 tools/stop_intraday_monitor.py
```

启动本地持仓监控界面：

```bash
python3 tools/serve_monitor_dashboard.py --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765` 查看持仓状态、实时盈亏、风险筛选、个股分析建议、基本面和事件记录。

页面顶部会自动读取 `market_wait` 刷新检查：盘前、午间休市、盘后或非交易日会提示继续等待；进入需要实时行情的交易时段后，如果仍有等待状态，会显示完整刷新命令。

个股详情同时显示动态反T观察计划、费用估算、多周期趋势和仓位超限降仓步骤。反T根据最低佣金和税费反推交易股数与回补价，扣费后预计净收益不足时直接阻断；未达到回补价时不追价买回。所有计划均需人工确认，系统不提交订单。

一键刷新完整日内决策链：

```bash
python3 tools/run_intraday_decision_pipeline.py \
  --positions 'positions/POS-EASTMONEY-*.yaml' \
  --daily-bars data/processed/daily_bars.csv \
  --total-assets 25480
```

该命令会顺序刷新准实时快照、组合日检、做T检查、数据质量快照和实时持仓决策卡，避免页面同时读取不同时间点的产物。

检查等待交易时段的持仓是否已经需要刷新：

```bash
python3 tools/check_market_wait_refresh.py \
  --decision-cards data/metadata/realtime-decision-cards.json \
  --positions 'positions/POS-EASTMONEY-*.yaml' \
  --daily-bars data/processed/daily_bars.csv \
  --total-assets 25480
```

该命令只读取决策卡并判断当前交易时段：盘前、午间休市、盘后或非交易日只提示继续等待；进入集合竞价或连续竞价后，如果仍有 `market_wait` 持仓，会输出可执行的完整日内决策链刷新命令。

生成持仓数据质量快照：

```bash
python3 tools/build_data_quality_snapshot.py \
  --positions 'positions/POS-EASTMONEY-*.yaml' \
  --intraday-snapshot data/metadata/intraday-monitor.latest.json \
  --daily-bars data/processed/daily_bars.csv \
  --minute-cache-dir data/processed/minute-bars
```

该快照会检查交易时段、行情延迟、日线样本和分钟线缓存，先判断当前数据是否足够支撑盘中决策，并生成 `高可信`、`中可信`、`低可信` 三档。只有高可信数据才允许进入盘中人工确认；状态为 `stale`、`insufficient` 或 `missing` 时，实时建议只能用于观察或补数据排查。盘前、午间休市、盘后或非交易日的行情延迟会在决策卡中显示为等待交易时段，不再和盘中行情异常混在一起。

历史数据会纳入本地缓存：5分钟线按股票保存到 `data/processed/minute-bars/{code}.json`，日线统一合并到 `data/processed/daily_bars.csv`。数据质量快照还会检查东方财富现价与分钟线、同日期日线最新价的一致性，默认允许1%以内偏差，可通过 `--max-consistency-diff-pct` 调整。

生成并执行数据质量修复计划：

```bash
python3 tools/repair_data_quality.py \
  --positions 'positions/POS-EASTMONEY-*.yaml' \
  --quality-snapshot data/metadata/data-quality-snapshot.json

python3 tools/repair_data_quality.py \
  --positions 'positions/POS-EASTMONEY-*.yaml' \
  --quality-snapshot data/metadata/data-quality-snapshot.json \
  --total-assets 25480 \
  --execute
```

默认只生成修复计划；`--execute` 会按需刷新准实时行情、日线和5分钟线，并重建数据质量快照。开盘前或收盘后行情时间戳可能仍停在上一交易日，此时状态继续显示 `stale` 是正常保护。

生成统一实时持仓决策卡：

```bash
python3 tools/build_realtime_decision_cards.py \
  --intraday-snapshot data/metadata/intraday-monitor.latest.json \
  --portfolio-check data/metadata/eastmoney-portfolio-check.after-threshold.json \
  --t-opportunities data/metadata/eastmoney-portfolio-t-opportunities.near-config.json \
  --data-quality data/metadata/data-quality-snapshot.json \
  --technical-indicators data/metadata/technical-indicators.json \
  --output data/metadata/realtime-decision-cards.json \
  --markdown-output reports/realtime-decision-cards.md
```

决策卡会把准实时行情、数据质量、组合日检、做T检查、多周期技术指标、动作矩阵回测、反T回测和反T预测合并成每只持仓的状态、关键价格、技术评分、阻断原因、证据链和下一步动作。它只生成辅助建议，不自动下单。

触发已确认硬止损后，决策卡不会默认全仓退出，而是生成退出风控计划：止损减仓、反弹减仓或硬退出。详情见 [持仓准实时监控](./docs/持仓准实时监控.md) 的“动态止损复核与退出风控计划”。

本地监控界面会自动读取 `/api/decision-cards`，列表状态和个股详情优先展示决策卡结论，并显示数据质量、可信等级、数据源一致性与阻断原因。

准实时快照还包含主力、超大单、大单、中单和小单净流入。资金流按成交单大小统计，用于识别价格与资金分歧，不代表识别了具体机构身份。

### 新建退出计划

```bash
python3 tools/new_exit_plan.py \
  --position positions/POS-示例.yaml \
  --daily-check data/metadata/POS-示例.daily-check.json \
  --output exit-plans/EXIT-示例.yaml
```

检查退出计划：

```bash
python3 tools/check_exit_plan.py \
  --exit-plan exit-plans/EXIT-示例.yaml
```

新建卖出执行记录：

```bash
python3 tools/new_exit_execution.py \
  --exit-plan exit-plans/EXIT-示例.yaml \
  --execution-date 2026-07-08 \
  --execution-price 9.1 \
  --output exit-executions/EXITEXEC-示例.yaml
```

检查卖出执行记录：

```bash
python3 tools/check_exit_execution.py \
  --exit-execution exit-executions/EXITEXEC-示例.yaml
```

从卖出执行生成复盘草稿：

```bash
python3 tools/new_trade_review_from_exit_execution.py \
  --exit-execution exit-executions/EXITEXEC-示例.yaml \
  --output reviews/TR-示例.yaml
```

检查交易复盘质量：

```bash
python3 tools/check_trade_review_quality.py \
  --review reviews/TR-示例.yaml
```

生成交易复盘分析：

```bash
python3 tools/analyze_trade_reviews.py \
  --reviews reviews/*.yaml \
  --output reports/review-analysis.md \
  --json-output data/metadata/review-analysis.json
```

检查复盘冷静期：

```bash
python3 tools/check_review_cooldown.py \
  --reviews reviews/*.yaml \
  --output data/metadata/review-cooldown.json
```

一键执行复盘维护流水线：

```bash
python3 tools/run_review_pipeline.py \
  --reviews reviews/*.yaml
```

### 新建交易复盘草稿

```bash
python3 tools/new_trade_review.py \
  --plan plans/TP-示例.yaml \
  --entry-date 2026-07-01 \
  --exit-date 2026-07-07 \
  --entry-price 10 \
  --exit-price 10.8 \
  --position-pct 5 \
  --exit-reason "达到计划止盈区后退出" \
  --followed-plan \
  --lesson "按计划执行" \
  --next-action "归档并继续观察"
```

默认输出到 `reviews/` 目录。真实复盘记录默认不提交到 Git。

运行测试：

```bash
python3 -m unittest discover -s tests
```
