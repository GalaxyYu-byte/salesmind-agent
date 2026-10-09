# 意图解析评测结果

模式：live；模型：deepseek-flash；固定参考日：2026-10-08。
通过：10/10 组，20/20 轮。

真实模式调用模型；离线模式只验证给定提取结果的校验与查询阻断，不能衡量自然语言准确率。
两种模式都运行真实会话 API、SQLite 持久化与 LangGraph；销售聚合查询用 spy 统计次数。

| 用例 | 轮次/输入 | 预期→实际状态 | 预期/实际查询次数 | 结果 |
|---|---|---|---|---|
| 01-complete | 1：2026年8月华东销售额走势如何？ | completed → completed | 1/1 | 通过 |
| 02-missing-time | 1：华东卖了多少钱？ | awaiting_input → awaiting_input | 0/0 | 通过 |
| 02-missing-time | 2：2026年8月 | completed → completed | 1/1 | 通过 |
| 03-multiple-corrections | 1：看看华东 | awaiting_input → awaiting_input | 0/0 | 通过 |
| 03-multiple-corrections | 2：按销售额统计 | awaiting_input → awaiting_input | 0/0 | 通过 |
| 03-multiple-corrections | 3：不看华东了，改成2026年7月华南订单数 | completed → completed | 1/1 | 通过 |
| 04-correct-metric | 1：最近华东销售额走势如何？ | awaiting_input → awaiting_input | 0/0 | 通过 |
| 04-correct-metric | 2：不是销售额，改看毛利 | awaiting_input → awaiting_input | 0/0 | 通过 |
| 04-correct-metric | 3：2026年7月至8月 | completed → completed | 1/1 | 通过 |
| 05-region-followup | 1：2026年8月华东平均客单价走势 | completed → completed | 1/1 | 通过 |
| 05-region-followup | 2：华南呢？ | completed → completed | 1/1 | 通过 |
| 06-no-history | 1：华南呢？ | awaiting_input → awaiting_input | 0/0 | 通过 |
| 06-no-history | 2：看2026年8月华南订单数汇总 | completed → completed | 1/1 | 通过 |
| 07-unknown-region | 1：2026年8月火星区销售额多少？ | awaiting_input → awaiting_input | 0/0 | 通过 |
| 07-unknown-region | 2：区域改成华南 | completed → completed | 1/1 | 通过 |
| 08-unknown-metric | 1：2026年8月华东净利润多少？ | failed → failed | 0/0 | 通过 |
| 09-unknown-followup | 1：2026年8月华东销售额多少？ | completed → completed | 1/1 | 通过 |
| 09-unknown-followup | 2：改看转化率呢？ | failed → failed | 0/0 | 通过 |
| 10-relative-watermark | 1：上个月华东销售额多少？ | awaiting_input → awaiting_input | 0/0 | 通过 |
| 10-relative-watermark | 2：那改成2026年8月 | completed → completed | 1/1 | 通过 |
