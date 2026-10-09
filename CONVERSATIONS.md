# 会话、任务与 SSE

当前版本使用 LangGraph StateGraph 组织理解、澄清、计划、查询、计算、排序、画图和保存节点。DeepSeek 提取白名单条件，后端确定性计算和生成图形数据，前端展示结果快照。跨轮追问只继承同一用户、同一会话最近成功分析的条件，新任务通过授权查询缓存获取数据，并重新计算图表与结论。MySQL 保存任务、条件、澄清回复、计划、事件、查询检查点和结果；查询检查点支持同一任务暂停或进程重启后继续，Redis 缓存可复用同权限、同数据版本的短期查询结果，尚未接入 LangGraph 原生 Checkpointer。配置与能力边界见 [意图与工作流](INTENT.md)。

## 运行

保持 SSH 隧道、MySQL、Redis 可用，按 README 启动前后端即可。沿用已有 `agent_app` 表，不需要新增迁移。应用账号需要这些表的正常读写权限。

**只支持一个 FastAPI 实例、一个 Uvicorn worker。** 不使用 `--workers 2` 或多个共享数据库的 API 实例。执行权在当前进程中管理；扩容前需要持久任务队列、租约与执行者隔离。

`main.py` 的 lifespan 创建和关闭 `TaskSupervisor`。第一次通过认证访问会话接口时，执行一次遗留任务恢复：将旧进程的 queued、running、cancelling 改为 interrupted，保留会话占用，由用户显式继续或取消。健康检查不触发该写入，也不要求模型可用。

默认同时执行 4 个任务，执行加排队最多 32 个，队列满返回 429。查询超时 10 秒，任务累计活动预算 90 秒；暂停等待不计入预算，恢复保留已用时间。服务关闭在 10 秒内尝试中断任务并写入事件；如果存储不可用，重启后通过遗留任务恢复处理。

配置见 `.env.example`：`RUN_CONCURRENCY`、`RUN_QUEUE_LIMIT`、`RUN_TIMEOUT`、`RUN_SHUTDOWN_SECONDS`、`ARTIFACT_RETENTION_DAYS`、`SSE_POLL_SECONDS`、`SSE_HEARTBEAT_SECONDS`，均使用 `SALESMIND_` 前缀。

## API

所有接口要求 Bearer Access Token，资源查询带当前用户归属条件。他人的会话、任务和结果统一返回 404。销售数据范围和指标由后端按当前账号授权过滤；缓存键包含完整授权范围，命中缓存仍需复查权限。

| 方法与路径 | 行为 |
|---|---|
| POST `/api/v1/sessions` | 创建会话；body `{}` 或 `{"title":"..."}` |
| GET `/api/v1/sessions?limit=50&offset=0` | 分页返回自己的会话 |
| GET `/api/v1/sessions/{id}/messages?limit=50&offset=0` | 最近一页任务及其可见消息；limit 按任务计，最多 100 |
| DELETE `/api/v1/sessions/{id}` | 删除已无活动任务的会话及消息、事件和结果；否则 409 |
| POST `/api/v1/sessions/{id}/runs` | 创建任务，返回 202；要求 `Idempotency-Key` |
| GET `/api/v1/runs/{id}` | 状态、阶段、原请求、事件序号和结果 ID |
| GET `/api/v1/runs/{id}/events` | SSE 订阅，可带 `Last-Event-ID` |
| POST `/api/v1/runs/{id}/cancel` | 停止执行并释放会话；已结束任务重复调用保持原终态 |
| POST `/api/v1/runs/{id}/pause` | 停止执行并保留会话占用，标记 interrupted |
| POST `/api/v1/runs/{id}/resume` | 继续 paused/interrupted 或补充 awaiting_input 任务 |
| GET `/api/v1/artifacts/{id}` | 读取持久化的销售查询快照 |

自然语言请求只需 `{ "question": "分析2026年8月华东销售额" }`，或显式提供 `mode: "auto"`；模型在后台 understand 阶段解析。以下为仍兼容的结构化创建请求：

```json
{
  "question": "分析2026年8月华东销售额",
  "mode": "analysis",
  "analysis": {
    "scope": {"months": ["2026-08"], "region": 1},
    "metric": "amount",
    "view": "trend",
    "chart": "line"
  }
}
```

region 为 0 表示全部区域。月份必须连续、升序、无重复且最多 24 个；实际查询还校验数据库数据水位。未知字段、SQL、非法指标等不能进入查询。客户端不能指定 owner、run ID、权限或数据版本。

分析模板在现有请求结构上扩展：

| 条件 | 行为 |
|---|---|
| `comparison: "mom"` | 趋势按各月上月计算；汇总、区域或商品对比按紧邻的等长上一期间计算 |
| `view: "products", product_ids: [1, 2]` | 仅比较可见目录内的指定商品，支持数值、同比或环比排序；自然语言使用商品名称，由后端匹配 ID |
| `goal: "anomalies", view: "trend"` | 比较每月与此前 `anomaly_window` 个完整月的历史均值，默认 3 个月；绝对偏离达到 `anomaly_threshold_percent`（默认 30）标记异常 |

增长与异常分析需要 `growth_rate` 授权。异常窗口允许 3–12 个月，阈值大于 0 且不超过 1000%；客单价历史基准按历史总销售额除以总订单数，其他指标取月均。历史未完整覆盖、历史均值为零或负数时标记不可判断，不推断业务原因。`product_ids` 也可用于商品异常趋势。结果保留旧同比字段，并增加通用比较、环比、异常及历史均值字段；前端表格、图表和 CSV 使用对应标签。

同一用户、同一幂等键和同一请求返回已有任务；相同键搭配不同会话或输入返回 409。任务记录、用户问题、排队事件和会话占用在一个事务中写入，提交后再投递执行器。一个会话只允许一个未结束任务，awaiting_input 和 interrupted 仍占用会话。

旧版结构化客户端需要澄清时可提交 `mode: "clarify"`、question 和 explanation，不带 analysis；任务直接进入 awaiting_input 并写入澄清消息。resume 时提供 analysis 和可选 reply，沿用原任务 ID。`mode: "unsupported"` 保存问题与解释后进入 failed，不启动查询。

auto 任务缺少条件时由服务端进入 awaiting_input，resume 提交 `{ "reply": "2026年8月" }`，同一任务重新解析。返回的 clarification_fields 标识需要补充的字段；auto 澄清不接受客户端 analysis。

暂停后的 resume body 为 `{}`，保留原始分析范围；更换范围应创建新任务。**查询已完成并提交有效检查点时，继续从 analyze 计算节点开始，跳过理解、计划和查询，使用原查询快照及其数据版本。** 计算、排序或画图中途暂停，也从 analyze 重做这些后续步骤；当前没有保存每个节点的检查点，尚未接入 LangGraph 原生 Checkpointer。completed、failed、cancelled 不可继续。

查询中途暂停、查询快照尚未提交或旧任务没有检查点时，继续会重新查询；已经解析的条件不重新调用模型，尚未解析的 auto 任务再次调用模型。恢复前检查用户归属、当前权限、工作流版本、指标版本、快照格式、输入条件及有效期；已有检查点失效时返回 409，过期返回 410，任务保持暂停，可取消后重新创建，不能静默改用新数据。补充澄清仍按原有流程重新解析。跨轮追问创建新任务，不能复用上一轮检查点。

## SSE

先创建任务，再订阅 SSE。关闭页面或断开事件连接只停止订阅，任务继续；显式 cancel/pause 才停止执行。服务端收到停止请求先写 cancelling，再取消协程、等待收尾，最后写 cancelled/interrupted；晚到结果不能覆盖状态或写入成功快照。

事件 envelope：`schema_version / run_id / session_id / seq / timestamp / type / payload`。事件先与状态变化一起提交到 MySQL，再通过轮询发送；每批最多 100 条，不创建无界的订阅内存队列。

当前事件有 `run.queued`、`run.started`、`stage.started`、`stage.completed`、`intent.resolved`、`plan.created`、`result.ready`、`clarification.required`、`run.cancelling`、`run.completed`、`run.failed`、`run.cancelled`、`run.interrupted`。实际执行阶段由 plan.created 指定；包含 understand、plan、query、analyze、visualize、result，分组分析增加 rank。不发送模型 Token 或内部推理。

```text
id: 8
event: result.ready
data: {"schema_version":1,"run_id":"...","session_id":"...","seq":8,"timestamp":"2026-10-07T12:00:00Z","type":"result.ready","payload":{"artifact_id":"...","row_count":12}}

```

默认每 0.5 秒读取新事件，每 15 秒发送注释心跳，心跳不入库。不可恢复终态、等待补充或 interrupted 时结束订阅。重连带最近收到的序号，只读取更大的 seq；前端按 run 和 seq 去重并验证连续性、UTF-8 分片和完整帧。

首次订阅先鉴权，再返回流；流中定期检查设备会话、账号和权限版本，Access Token 到期结束连接，让客户端正常刷新后重连。Token 不放 URL。响应设置 `Cache-Control: no-store`、`X-Accel-Buffering: no`；生产反向代理需要禁用缓冲并保持足够的空闲超时。

游标格式无效或超过最新序号返回 400；低于 replay_floor_seq 返回 410，客户端读取 run 和 artifact 重建。当前没有定时事件清理，事件保留至删除会话；回放下界已支持后续清理策略。

## 历史与结果

会话、任务、可见消息、业务事件和查询结果分别写入现有 `chat_session / analysis_run / chat_message / run_event / result_artifact`。读取和删除均检查用户归属。内部查询检查点复用 `result_artifact`，使用独立键 `checkpoint:query`，与最终结果的 `overview` 键区分，不需要新增表或迁移。查询快照、该阶段完成事件和数据版本在同一事务中提交；前端收到 query 完成事件时，后端已经能恢复这份快照。

结果快照默认有效 7 天，单条 schema 与 payload 合计不超过 1 MiB。过期读取返回 410；权限版本变化返回 409，要求重新分析。过期规则限制读取，不是后台自动清理任务。

前端登录后从服务端分页加载会话，打开会话时读取任务和有效结果；仍在运行的任务重新订阅事件。网络错误会重试有限次数，并提示重新打开会话同步状态。API 失败不会使用固定样例数据代替查询结果。

保存的报告仍按用户保存在浏览器。旧浏览器会话保留在原 localStorage 键中，暂不自动上传或混入服务端列表；保存报告不会删除这些旧记录。

## 验证

```powershell
.venv\Scripts\python.exe -m pytest -q --basetemp=.test-tmp/conversations-check
.venv\Scripts\python.exe -m ruff check .
cd frontend
npm test
npm run build
```

pytest 的 basetemp 应选项目内专用测试目录，运行时会重建该目录，不能指定项目根目录或其他重要数据目录。

新增后端测试使用临时 SQLite 的真实事务、唯一约束与外键，以及内存 Redis，覆盖持久化、SSE 回放、请求幂等、归属、同会话互斥、队列、超时、取消后晚到结果、暂停继续、澄清和重启恢复。检查点验证重点是：查询完成后暂停或重启再继续不重复查询，查询未提交时继续会重查，后续计算使用原快照的数据版本，以及权限或版本变化、快照过期时拒绝恢复。这不代替真实 MySQL 账号授权、代理流缓冲及浏览器交互验证；下文 2026-10-07 联调发生在检查点实现前，不证明检查点的真实 MySQL 联调已通过。

2026-10-07 已使用现有 MySQL/Redis 和临时设备会话完成真实 API 联调：查询 2026-03 至 2026-08 全部区域，销售额 4,782,185.61 元、订单 1,750 笔；9 条业务事件完整回放，Last-Event-ID 续播、历史和结果读取通过。临时会话、结果、事件及登录凭证在联调后清理。此联调没有接管其他遗留任务，也未验证独立数据库账号授权或生产代理行为。


## 计划与跨轮上下文

GET run 返回 `plan: [{id, label}]`、`context_run_id`、`inherited_fields`、`clarification_fields` 和 `clarification_replies`。`plan.created` SSE 携带 `steps`；阶段事件包含 `understand/plan/query/analyze/rank/visualize/result`，分组分析才执行 rank。前端按实际计划显示进度。

创建 auto 任务时冻结 `_context_run_id`；解析前重新校验来源的用户、会话、完成状态、权限版本、指标版本和范围版本。模型只接收上一轮已确认的语义条件，不接收数值、区域 ID、结果快照或完整业务目录。只有 `follow_up=true` 才继承缺失字段；完整新问题不自动继承。明确全部区域覆盖单一区域。没有可用历史时需要补全条件。

结果快照保留原 overview 字段并添加 `analysis_result`：排序后行、选中 ID、结论、口径、Plotly figure 和单位。`largest_decline` 采用同比百分比升序取第一个负值；不完整基期、零/负基准和无法计算的客单价不参与百分比排名。取消、超时、权限失效和晚到结果仍由任务事务保护。
