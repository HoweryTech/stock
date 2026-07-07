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
- [交易计划与风控校验](./docs/交易计划与风控校验.md)
- [数据目录与股票基础信息导入](./docs/数据目录与股票基础信息导入.md)
- [日线行情数据导入](./docs/日线行情数据导入.md)
- [财务核心指标导入](./docs/财务核心指标导入.md)
- [估值指标导入](./docs/估值指标导入.md)
- [价值质量策略筛选](./docs/价值质量策略筛选.md)
- [趋势强度基础因子](./docs/趋势强度基础因子.md)
- [趋势强度策略筛选](./docs/趋势强度策略筛选.md)
- [多策略候选池合并](./docs/多策略候选池合并.md)
- [观察池报告](./docs/观察池报告.md)
- [观察池流水线](./docs/观察池流水线.md)
- [候选池质量检查](./docs/候选池质量检查.md)
- [候选股生成交易计划草稿](./docs/候选股生成交易计划草稿.md)
- [交易计划草稿质量检查](./docs/交易计划草稿质量检查.md)
- [交易计划准入门禁](./docs/交易计划准入门禁.md)
- [交易计划示例文件](./templates/trade-plan.example.yaml)
- [持仓记录示例文件](./templates/position.example.yaml)

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

### 筛选趋势强度候选股

```bash
python3 tools/screen_trend_strength.py \
  --profile config/investment-profile.example.yaml \
  --factors data/processed/trend_factors.csv \
  --output data/processed/trend_candidates.csv \
  --metadata-output data/metadata/trend_candidates.json
```

### 合并多策略候选池

```bash
python3 tools/merge_candidate_pool.py \
  --trend-candidates data/processed/trend_candidates.csv \
  --value-quality-candidates data/processed/value_quality_candidates.csv \
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
  --universe data/processed/tradable_universe.csv \
  --report-output reports/watchlist.md
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
  --plan plans/TP-示例.yaml
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
