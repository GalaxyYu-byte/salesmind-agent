# 意图解析、多轮追问与 LangGraph 工作流

链路：原始问题 → DeepSeek 提取白名单条件 → 后端归一化与校验 → 信息不足时追问；完整时进入 LangGraph 计划、查询、计算、排序、生成图形数据和保存结果。支持同一会话内跨轮条件继承、同比/环比、指定商品对比和异常月份检测。查询阶段在授权范围、数据版本和查询条件一致时复用缓存；分析与图形仍按当前模板计算。

## 配置与运行

在本地 `.env` 中增加以下配置，API Key 不提交到 Git：

```dotenv
SALESMIND_LLM_API_KEY=替换为自己的DeepSeek_API_Key
SALESMIND_LLM_BASE_URL=https://api.deepseek.com
SALESMIND_LLM_MODEL=deepseek-flash
SALESMIND_LLM_TIMEOUT=30
```

模型名称可替换为账号实际可用的名称。BASE_URL 填接口根地址，不带 `/chat/completions`；兼容代理可在根地址中包含 `/v1`。配置变更后重启后端。仍需按 README 启动 MySQL/Redis、前后端并登录。

已有 `DEEPSEEK_API_KEY` 时可直接沿用，无需重复设置 `SALESMIND_LLM_API_KEY`。支持系统环境变量和本地 `.env`；系统环境变量优先于文件，同一来源两项都有值时 `SALESMIND_LLM_API_KEY` 优先，空值会忽略。

运行 `uv sync --locked` 同步依赖。运行时依赖包含 httpx 和 LangGraph。没有数据库迁移。

未配置 Key 时服务仍能启动，工作台和结构化销售查询可用；自然语言任务返回明确的配置错误，不自动回退前端规则。模型认证失败、超时、限流、空输出、截断输出和无效 JSON 都会结束任务，且不执行销售聚合查询。模型调用只有一次，没有隐藏重试。

DeepSeek JSON Output 请求使用 `response_format={"type":"json_object"}`，提示词包含 JSON 格式说明和示例，关闭思考并限制输出长度。协议依据：[JSON Output](https://api-docs.deepseek.com/zh-cn/guides/json_mode/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。

## 支持范围

- 指标：销售额 amount、订单数 count、毛利 profit、平均客单价 average；每次一个指标。
- 时间：一个完整月或连续的 1–24 个完整月；明确范围、上个月、最近 N 个完整月、今年至今、最近 N 个有完整数据的月份。
- 区域：数据库中的一个区域或全部区域；未指定区域默认为全部，未知区域进入澄清。
- 分析方式：汇总 summary、月趋势 trend、商品对比/排名 products、区域对比 regions。未指定时默认汇总。趋势默认折线，其他默认柱状。
- 商品：可选择可见目录中的一个或多个商品进行商品对比或异常月趋势；未知、重名或已不可见的商品进入澄清，不能扩大成全部商品。明确“全部商品”时清除已继承的商品筛选。
- 只统计已完成订单；同比、环比和异常统计由后端使用 Decimal 确定性计算，前端展示结果。
- 同比 yoy：去年同月或同期；环比 mom：汇总和商品/区域分组比较紧邻的前一个等长期间，月趋势逐月比较上个月。
- 异常 anomalies：每月相对前 N 个完整月滚动均值的绝对百分比偏差达到阈值。默认 N=3、阈值=30%，可指定 3–12 个月和大于 0、不超过 1000% 的阈值。固定为月趋势、不叠加同比/环比或前 N 名排名；没有可比历史或基准不适用时说明无法判断，不解释业务原因。

日/周粒度、预测、业务原因、明细、销售员/客户/库存/目标、多个指标暂未开放。商品筛选仅用于商品对比或异常月趋势。分组分析支持按数值、同比/环比百分比或同比/环比增减排序和前 1–20 名；区域下降最多模板选取同比或环比下降百分比最小的一名。模型通过 unsupported 标记未支持的请求，后端不会执行对应查询。模型输出只允许注册的枚举和字段，不能带 SQL、工具、区域/商品 ID 或权限字段。同比、环比及异常偏差额外要求 growth_rate 指标权限。

每次解析都将输出 Schema 和区域/商品候选传给模型。候选仅包含用户原问题或本任务补充回答或上一轮已确认条件中已经出现、且在授权目录中的名称；完整目录和数据水位仅在后端校验，不发送给模型。指标限定为 amount/count/profit/average，分析方式限定为 summary/trend/products/regions，图形限定为 line/bar。区域只能输出候选中的标准名称或 null，商品只能输出候选名称列表；遇到名单之外的区域/商品，模型应标记 region_unknown/product_unknown=true，让后端请求澄清。商品 ID 只由服务端解析名称取得，上一轮模型上下文也只携带已确认商品名称。显式请求越权的区域或商品在模型调用前拒绝。模型实际返回候选之外的名称、非法枚举或额外字段时，后端拒绝解析结果，不执行销售查询。JSON 格式输出不保证语义正确，因此必须保留服务端校验。

时间规则由代码实现。任务创建时固定上海时区的 reference_date；“上个月”按该日期解析。2026-10-08 的上个月是 2026-09，如果数据库只完整到 2026-08，就提示调整范围，不静默改成八月。只有明确要求“有完整数据的月份”时才以数据水位为锚点。查询时还会再次检查实时水位。

指标提取另带 `metric_text`（用户原始指标词）与 `metric_status`（supported / unknown / ambiguous）。只有原词出现在原问题或补充回答中、属于服务端已注册别名、且与 metric 枚举一致时才接受映射。明确的“净利润”“转化率”等未知指标停止查询并说明支持范围；“利润”“收益”等口径不明确的词进入指标澄清。模型漏掉原词、编造原词或映射冲突时也先澄清，不能靠继承上一轮指标绕过校验。后面的明确指标更正优先；更换图形不会消除同一句中多个指标的冲突。校验是有限别名与原文证据规则，未注册的说法需要用户选择支持的指标。

查询白名单仍然保留。任务详情的 `metric_resolution` 保存本轮原词、校验状态和最终指标（沿用上一轮时状态为 inherited）；`intent.resolved`、`clarification.required` 和未知指标的 `run.failed` 事件携带同样记录。保存于已有 request JSON，无需数据库迁移。

## 请求、澄清与结果

创建任务仍使用原接口和 Idempotency-Key：

```http
POST /api/v1/sessions/{session_id}/runs
Idempotency-Key: <唯一请求键>
Content-Type: application/json

{"question":"2026年8月华东卖了多少钱？"}
```

仅提供 question 时自动采用 mode=auto；前端显式发送 auto。服务端异步解析，创建响应为 202。解析成功后，任务 request.analysis 保存校验好的 scope/metric/view/chart，mode 变成 analysis。原始 question、input_hash 和请求幂等键保留；再次提交原问题不会重复调用模型。

例如提取结果：

```json
{
  "scope": {"months": ["2026-08"], "region": 1},
  "metric": "amount",
  "view": "summary",
  "chart": "bar"
}
```

区域 ID 1 仅为示例；服务端从数据库目录匹配名称取得实际 ID。

缺少指标、时间或区域不明确时，任务进入 awaiting_input。GET run 返回 clarification_fields，SSE clarification.required 也携带 fields 和具体问题。用户通过原 resume 接口补充：

```http
POST /api/v1/runs/{run_id}/resume
Content-Type: application/json

{"reply":"2026年8月，按销售额统计"}
```

沿用同一 run。模型输入分别包含原问题与补充回答列表；后面明确的更正优先，reference_date 保持不变。最多五次补充；auto 任务不能通过客户端 analysis 跳过模型解析。等待期间不占执行槽，暂停恢复、取消和晚到结果保护继续有效。

历史结构化 mode=analysis、clarify、unsupported 请求仍兼容。同一 run 暂停后继续时，已提交有效查询检查点的任务使用原查询快照，从 analyze 开始，跳过理解、计划和查询；查询尚未完成时仍按保存的绝对范围重查，尚未解析的任务恢复会重新调用模型。当前只保存查询检查点，后续计算、排序或画图中断后从 analyze 重算，尚未接入 LangGraph 原生 Checkpointer。跨轮新 run 在查询阶段可以复用同授权范围、同数据版本和同绝对查询参数的缓存。独立新问题不自动继承；依赖上一轮的追问由模型提取改动字段，后端合并已确认条件，包括商品筛选及异常参数。

没有已完成历史时，当前任务的补充回答合并原问题和累计回复，不作为跨任务追问。即使模型误报 follow_up=true，服务端仍按当前任务缺失的条件继续澄清。有已确认上一轮条件时仍保留真实追问的继承行为。

## 代码与验证

15 组、27 轮可复跑的问句评测、逐轮预期及历史真实模型结果见 [意图解析评测](evals/README.md)。新增环比、商品对比/继承、未知商品澄清、异常模板及环比下降排名用例。默认离线验证查询阻断；显式 `--intent-live` 验证真实自然语言提取，同时记录实际查询次数。新增模板已完成离线评测，尚未调用真实模型复测。

- `services/intent.py`：模型协议、白名单 Schema、相对日期计算、区域匹配、数据覆盖校验。
- `services/task_supervisor.py`：管理执行槽、超时和取消，调用 LangGraph，提供隔离的模型和查询依赖。
- `services/conversations.py`：解析结果、澄清回复和 SSE 持久化；model_calls 记录调用次数；取消后忽略晚到结果。
- `frontend/src/lib/useConversations.ts`：原问题与原始补充回答提交；不使用本地 parseQuestion 生成任务。

测试使用模拟 HTTP 模型、临时 SQLite 和内存 Redis，覆盖四项提取后的查询链路、澄清恢复、幂等重试、错误输出、未知区域、相对日期、水位、模型错误及取消后晚到响应。测试没有调用真实 DeepSeek，也不代表自然语言准确率已经验收。

本地配置 Key 后建议验证“2026年8月华东销售额走势”“华东销售额（随后补充月份）”“上个月销售额（检查水位提示）”“最近六个有完整数据的月份各区域销售额对比”。


## 实际测试步骤

更新依赖并重启服务（沿用 `.env` 中现有的 `DEEPSEEK_API_KEY`）：

```powershell
uv sync --locked
.\start-dev.ps1
```

登录后，在同一个“智能分析”会话中按顺序发送：

1. `看看华东销售额`：应追问“你想看哪个时间段？”，没有分析结果和销售聚合查询。
2. `2026年8月`：补齐原任务，查询后显示已确认的指标、月份、区域和汇总结果。日期必须处于本地数据完整水位内。
3. `华南呢`：创建新任务；保留八月、销售额和汇总方式，只修改区域，重新查询。页面显示继承项。
4. `比较各区域同比，找出下降最多的区域`：保留时间和指标、切换全部区域。依次执行查询、计算、排序、画图；按同比下降百分比升序选一个负值，表格显示不可比原因。
5. `换成柱状图`：保留已确认条件，只切换图形。
6. 新建会话后发送 `华南呢`：应要求补齐条件，不继承其他会话。

也可以直接发送完整问题：`比较2026年8月各区域销售额同比，找出下降最多的区域`。没有时间或指标且没有可用历史时先澄清。全部区域增长时回答没有下降区域；没有可比基期时明确无法判断。金额以整数分汇总，平均客单价按总销售额÷总订单数计算；同比百分比按未舍入的 Decimal 值排序。

## LangGraph 实现边界

`services/workflow.py` 使用编译后的 StateGraph 固定 DAG。`understand` 根据 ready/clarify/unsupported 路由；clarify 进入 awaiting_input 后释放执行槽，resume 携带持久化补充回答重新调用图。分组分析增加 rank 节点。模型只能选择注册的分析方式和目标，执行计划由后端模板生成，不能生成任意节点、SQL 或工具调用。当前复杂模板覆盖区域/商品同比和环比计算排序、区域下降最多、指定商品对比及异常月份检测；未实现任意业务原因调查或自主通用规划。

`services/analysis.py` 执行计算、排序和生成 Plotly figure。模型不接收数据库数值、不生成结论。服务重启或暂停恢复时，同一 run 的有效 MySQL 查询检查点支持从 analyze 继续，沿用查询快照及其数据版本；没有已提交检查点时重新运行查询，失效检查点拒绝恢复。当前没有每个节点的检查点，也未接入 LangGraph 原生 Checkpointer；跨轮新 run 使用查询缓存减少相同条件下的重复聚合。图的单次执行最多 20 步，最多 24 个月，最多 5 次澄清，仍受总超时和并发上限约束。

实现方式参照 [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)。自动化测试覆盖跨轮条件继承、会话与权限隔离、连续澄清、实际节点顺序、取消与超时，以及基期不足、零/负基准、全增长、并列下降和加权客单价。真实 DeepSeek 样例验证了缺失时间、区域追问及区域同比目标提取；不代表所有自然语言问法已经验收。
