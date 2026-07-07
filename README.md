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
- [交易计划示例文件](./templates/trade-plan.example.yaml)

## 当前可执行工具

### 交易计划风控校验

```bash
python3 tools/risk_check.py \
  --profile config/investment-profile.example.yaml \
  --plan templates/trade-plan.example.yaml
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
