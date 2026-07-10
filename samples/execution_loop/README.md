# 执行闭环样例

本目录提供一组最小可通过的执行闭环样例，用于查看 `tools/check_execution_loop.py` 的实际输出结构。

运行：

```bash
python3 tools/check_execution_loop.py \
  --trade-executions samples/execution_loop/executions/*.yaml \
  --positions samples/execution_loop/positions/*.yaml \
  --exit-executions samples/execution_loop/exit-executions/*.yaml \
  --reviews samples/execution_loop/reviews/*.yaml \
  --output /tmp/execution-loop-sample.md \
  --json-output /tmp/execution-loop-sample.json
```

该样例不是买卖建议，也不是实际交易记录。
