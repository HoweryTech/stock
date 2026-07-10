# 运行产物与 Git 边界

本文档定义哪些文件属于本地运行产物，默认不提交到 Git。

本项目的代码、模板、文档和样例数据可以提交；真实投资记录、真实行情数据、真实日报和流水线元数据默认不提交。

## 1. 默认不提交

`.gitignore` 默认忽略：

```text
plans/*.yaml
reviews/*.yaml
positions/*.yaml
executions/*.yaml
exit-executions/*.yaml
exit-plans/*.yaml
data/raw/*
data/processed/*
data/metadata/*
reports/*.md
```

这些目录里的文件通常包含真实候选池、交易计划、执行记录、持仓、退出、复盘、日报和检查元数据。

## 2. 可以提交

以下内容可以提交：

- `tools/` 中的工具代码。
- `tests/` 中的测试代码。
- `docs/` 中的说明文档。
- `templates/` 中的结构模板。
- `samples/` 中脱敏或虚构的样例数据。
- 各运行目录中的 `.gitkeep` 占位文件。

## 3. 提交前自查

```bash
git status --short
```

如果看到以下路径下的真实文件，先确认是否应保留在本地：

```text
plans/
reviews/
positions/
executions/
exit-executions/
exit-plans/
data/raw/
data/processed/
data/metadata/
reports/
```

日常运行推荐使用：

```bash
python3 tools/run_daily_check_pipeline.py
```

该命令会生成 `reports/` 和 `data/metadata/` 下的运行产物，这些产物用于当天决策和复盘，不作为代码提交内容。

## 4. 例外

如果需要提交样例数据，请放在 `samples/` 下，并确保：

- 不含真实账户、交易、持仓和个人敏感信息。
- 股票和价格可以是虚构或脱敏示例。
- 文档明确说明样例不是买卖建议，也不是实际交易记录。
