"""已审定初始 DDL 的列注释；保持 ORM 与迁移/已有数据库一致。"""

COLUMN_COMMENTS = {
    ("agent_app", "analysis_run", "active_elapsed_ms"): "累计执行耗时，不含等待澄清",
    (
        "agent_app",
        "analysis_run",
        "attempt",
    ): "开始执行或恢复时递增，用于拒绝旧执行者写入",
    ("agent_app", "analysis_run", "created_at"): "记录创建时间，UTC，毫秒精度",
    (
        "agent_app",
        "analysis_run",
        "data_version",
    ): "第一次获取业务数据后固定；跨库逻辑关联",
    ("agent_app", "analysis_run", "error_code"): "任务业务错误码，无错误时为空",
    ("agent_app", "analysis_run", "error_message"): "可展示且已脱敏的错误说明",
    ("agent_app", "analysis_run", "finished_at"): "仅不可恢复终态设置",
    (
        "agent_app",
        "analysis_run",
        "graph_version",
    ): "本轮固定的工作流版本，用于执行和恢复兼容性校验",
    ("agent_app", "analysis_run", "id"): "分析任务 ID，服务端生成的小写 UUID",
    (
        "agent_app",
        "analysis_run",
        "input_hash",
    ): "规范化请求的 SHA-256，包含 session_id",
    ("agent_app", "analysis_run", "last_event_seq"): "持有任务行锁时递增",
    (
        "agent_app",
        "analysis_run",
        "last_message_seq",
    ): "任务内最后分配的消息序号，初始为 0，用于分配 chat_message.seq",
    ("agent_app", "analysis_run", "metric_version"): "本轮固定的指标公式版本",
    (
        "agent_app",
        "analysis_run",
        "model_calls",
    ): "本轮累计模型调用次数，恢复时不重置，用于预算控制",
    (
        "agent_app",
        "analysis_run",
        "node_steps",
    ): "本轮累计工作流节点访问次数，恢复时不重置，用于预算控制",
    (
        "agent_app",
        "analysis_run",
        "owner_id",
    ): "任务所属登录账号 ID，与会话归属一致，由服务端从认证身份取得",
    ("agent_app", "analysis_run", "paused_until"): "澄清或中断状态的过期截止时间",
    ("agent_app", "analysis_run", "permission_version"): "创建任务时的权限快照版本",
    ("agent_app", "analysis_run", "reference_date"): "本轮固定的业务基准日",
    ("agent_app", "analysis_run", "replay_floor_seq"): "已清理事件的最大 seq",
    ("agent_app", "analysis_run", "request_id"): "创建请求幂等键",
    (
        "agent_app",
        "analysis_run",
        "request_json",
    ): "原始业务请求，禁止包含令牌或服务端密钥",
    (
        "agent_app",
        "analysis_run",
        "scope_hash",
    ): "任务创建时固定的有效用户、人员、区域及指标权限的 SHA-256 指纹",
    (
        "agent_app",
        "analysis_run",
        "session_id",
    ): "所属会话 ID，与 owner_id 共同关联 chat_session",
    ("agent_app", "analysis_run", "stage"): "当前工作流节点",
    (
        "agent_app",
        "analysis_run",
        "started_at",
    ): "任务首次开始执行时间，UTC，毫秒精度，尚未开始时为空",
    (
        "agent_app",
        "analysis_run",
        "status",
    ): "任务状态：queued 排队，running 执行中，awaiting_input "
    "等待澄清，interrupted 中断可恢复，cancelling 取消中，completed "
    "完成，partial 部分完成，failed 失败，cancelled 已取消",
    (
        "agent_app",
        "analysis_run",
        "timezone",
    ): "本轮固定的业务时区，用于解释业务日期和相对时间",
    (
        "agent_app",
        "analysis_run",
        "tool_calls",
    ): "本轮累计工具调用次数，恢复时不重置，用于预算控制",
    (
        "agent_app",
        "analysis_run",
        "updated_at",
    ): "记录更新时间，UTC，毫秒精度，更新时自动刷新",
    ("agent_app", "app_user", "active"): "账号是否启用：1 允许登录和使用系统，0 停用",
    (
        "agent_app",
        "app_user",
        "allowed_metrics_json",
    ): "服务端维护的指标白名单，空数组表示无指标权限",
    ("agent_app", "app_user", "created_at"): "记录创建时间，UTC，毫秒精度",
    ("agent_app", "app_user", "display_name"): "用户展示名称",
    ("agent_app", "app_user", "id"): "登录账号 ID，服务端生成的小写 UUID",
    ("agent_app", "app_user", "password_hash"): "带盐密码哈希，不保存明文密码",
    (
        "agent_app",
        "app_user",
        "permission_version",
    ): "当前权限版本，角色、人员绑定、指标、启停或区域授权变更时在同一事务递增",
    (
        "agent_app",
        "app_user",
        "rep_id",
    ): "逻辑关联 sales_data.sales_rep；销售员角色必填",
    (
        "agent_app",
        "app_user",
        "role",
    ): "用户角色：SALES_REP 销售员，SALES_MANAGER 销售经理，SALES_DIRECTOR 销售总监",
    (
        "agent_app",
        "app_user",
        "updated_at",
    ): "记录更新时间，UTC，毫秒精度，更新时自动刷新",
    ("agent_app", "app_user", "username"): "唯一登录用户名，仅限 ASCII，不区分大小写",
    (
        "agent_app",
        "chat_message",
        "content",
    ): "用户可见文本；读取助手结果前重新校验 run 权限",
    ("agent_app", "chat_message", "created_at"): "消息保存时间，UTC，毫秒精度",
    ("agent_app", "chat_message", "id"): "消息 ID，服务端生成的小写 UUID",
    (
        "agent_app",
        "chat_message",
        "kind",
    ): "消息用途：用户为 question 提问或 clarification_reply 澄清回复；助手为 "
    "clarification 澄清问题、answer 回答或 error 错误提示",
    ("agent_app", "chat_message", "message_key"): "稳定业务键，重试不改变",
    (
        "agent_app",
        "chat_message",
        "owner_id",
    ): "消息所属登录账号 ID，与关联分析任务的归属一致",
    ("agent_app", "chat_message", "role"): "消息角色：user 用户，assistant 助手",
    (
        "agent_app",
        "chat_message",
        "run_id",
    ): "所属分析任务 ID，与 session_id、owner_id 共同关联 analysis_run",
    ("agent_app", "chat_message", "seq"): "任务内消息顺序，由 last_message_seq 分配",
    (
        "agent_app",
        "chat_message",
        "session_id",
    ): "所属会话 ID，与关联分析任务的会话一致",
    (
        "agent_app",
        "chat_session",
        "active_run_id",
    ): "当前占用会话的分析任务 ID，关联 analysis_run；无活动任务时为空",
    (
        "agent_app",
        "chat_session",
        "context_permission_version",
    ): "语义摘要生成时的权限版本，复用摘要前校验",
    (
        "agent_app",
        "chat_session",
        "context_scope_hash",
    ): "语义摘要生成时的权限范围 SHA-256 指纹，复用摘要前校验",
    (
        "agent_app",
        "chat_session",
        "context_summary",
    ): "有界语义摘要，不保存大结果和完整模型提示词",
    ("agent_app", "chat_session", "created_at"): "记录创建时间，UTC，毫秒精度",
    (
        "agent_app",
        "chat_session",
        "graph_version",
    ): "会话工作流版本，用于判断检查点兼容性",
    ("agent_app", "chat_session", "id"): "会话 ID，服务端生成的小写 UUID",
    (
        "agent_app",
        "chat_session",
        "owner_id",
    ): "会话所属登录账号 ID，关联 app_user.id，由服务端从认证身份取得",
    ("agent_app", "chat_session", "title"): "会话列表展示标题",
    (
        "agent_app",
        "chat_session",
        "updated_at",
    ): "记录更新时间，UTC，毫秒精度，更新时自动刷新",
    (
        "agent_app",
        "result_artifact",
        "artifact_key",
    ): "任务内稳定输出键，如 q_current:dataset",
    (
        "agent_app",
        "result_artifact",
        "byte_size",
    ): "服务端计算 schema 与 payload 的序列化总字节数",
    ("agent_app", "result_artifact", "created_at"): "结果保存时间，UTC，毫秒精度",
    ("agent_app", "result_artifact", "data_version"): "无业务数据的解释性回答可为空",
    (
        "agent_app",
        "result_artifact",
        "expires_at",
    ): "结果过期时间，UTC，毫秒精度，由应用设置且必须晚于创建时间",
    ("agent_app", "result_artifact", "id"): "分析结果 ID，服务端生成的小写 UUID",
    (
        "agent_app",
        "result_artifact",
        "kind",
    ): "结果类型：DATASET 数据集，FIGURE Plotly 图表，ANSWER 结构化答案",
    ("agent_app", "result_artifact", "metric_version"): "生成结果时使用的指标公式版本",
    (
        "agent_app",
        "result_artifact",
        "owner_id",
    ): "结果所属登录账号 ID，与关联分析任务的归属一致",
    (
        "agent_app",
        "result_artifact",
        "payload_json",
    ): "有界数据集、Plotly Figure 或结构化答案",
    (
        "agent_app",
        "result_artifact",
        "permission_version",
    ): "结果生成时的权限快照版本，必须大于 0，读取和复用前校验",
    ("agent_app", "result_artifact", "row_count"): "仅数据集填结果行数",
    (
        "agent_app",
        "result_artifact",
        "run_id",
    ): "所属分析任务 ID，与 session_id、owner_id 共同关联 analysis_run",
    (
        "agent_app",
        "result_artifact",
        "schema_json",
    ): "列类型或结构协议，以及来源 artifact 引用",
    (
        "agent_app",
        "result_artifact",
        "schema_version",
    ): "分析结果结构协议版本，必须大于 0",
    (
        "agent_app",
        "result_artifact",
        "scope_hash",
    ): "结果生成时的有效用户、人员、区域及指标权限的 SHA-256 指纹",
    (
        "agent_app",
        "result_artifact",
        "session_id",
    ): "所属会话 ID，与关联分析任务的会话一致",
    (
        "agent_app",
        "run_event",
        "created_at",
    ): "事件保存时间，UTC，毫秒精度，用于保留期清理",
    (
        "agent_app",
        "run_event",
        "event_type",
    ): "业务事件类型，如 stage.started、tool.completed、run.completed",
    ("agent_app", "run_event", "payload_json"): "有界业务事件；大结果只存 artifact_id",
    (
        "agent_app",
        "run_event",
        "run_id",
    ): "所属分析任务 ID，关联 analysis_run.id，与 seq 构成联合主键",
    ("agent_app", "run_event", "schema_version"): "SSE 业务事件协议版本，必须大于 0",
    ("agent_app", "run_event", "seq"): "任务内单调递增事件序号",
    (
        "agent_app",
        "tool_execution",
        "attempt",
    ): "当前 run_attempt 内工具自身的重试次数，从 1 开始",
    (
        "agent_app",
        "tool_execution",
        "cache_hit",
    ): "本次工具调用是否命中缓存：0 未命中，1 命中",
    (
        "agent_app",
        "tool_execution",
        "duration_ms",
    ): "本次工具调用耗时，单位毫秒；running 时为空，其他状态必填",
    ("agent_app", "tool_execution", "error_code"): "工具调用错误码，无错误时为空",
    (
        "agent_app",
        "tool_execution",
        "fingerprint",
    ): "规范化参数与权限、版本的指纹，不存原始敏感参数",
    (
        "agent_app",
        "tool_execution",
        "finished_at",
    ): "本次工具调用结束时间，UTC，毫秒精度；running 时为空，其他状态必填",
    ("agent_app", "tool_execution", "id"): "工具执行审计记录 ID，自增主键",
    ("agent_app", "tool_execution", "run_attempt"): "所属 run 执行尝试，恢复时不同",
    ("agent_app", "tool_execution", "run_id"): "所属分析任务 ID，关联 analysis_run.id",
    (
        "agent_app",
        "tool_execution",
        "started_at",
    ): "本次工具调用开始时间，UTC，毫秒精度",
    (
        "agent_app",
        "tool_execution",
        "status",
    ): "工具执行状态：running 执行中，succeeded 成功，failed 失败，cancelled "
    "已取消，interrupted 已中断",
    ("agent_app", "tool_execution", "task_id"): "计划中的稳定任务 ID",
    ("agent_app", "tool_execution", "tool_name"): "本次调用的注册工具名称",
    ("agent_app", "user_region_scope", "created_at"): "区域授权创建时间，UTC，毫秒精度",
    ("agent_app", "user_region_scope", "region_id"): "逻辑关联 sales_data.sales_region",
    (
        "agent_app",
        "user_region_scope",
        "user_id",
    ): "被授权的登录账号 ID，关联 app_user.id",
    ("sales_data", "dataset_version", "available_from"): "数据覆盖起始业务日期",
    (
        "sales_data",
        "dataset_version",
        "complete_through",
    ): "截至该日数据完整，含当日；未知时为空",
    ("sales_data", "dataset_version", "id"): "固定为 1，描述当前发布的数据集",
    (
        "sales_data",
        "dataset_version",
        "refreshed_at",
    ): "业务数据与版本一起提交的 UTC 时间",
    ("sales_data", "dataset_version", "version"): "只递增的数据版本",
    ("sales_data", "product", "active"): "商品是否启用：1 启用，0 停用",
    ("sales_data", "product", "category"): "首版固定品类，已有订单后不直接修改",
    ("sales_data", "product", "created_at"): "记录创建时间，UTC，毫秒精度",
    ("sales_data", "product", "id"): "商品 ID",
    ("sales_data", "product", "name"): "商品名称",
    ("sales_data", "product", "sku"): "商品编码",
    (
        "sales_data",
        "product",
        "updated_at",
    ): "记录更新时间，UTC，毫秒精度，更新时自动刷新",
    ("sales_data", "sales_order", "amount"): "成交总额，首版等于数量乘成交单价",
    ("sales_data", "sales_order", "cost"): "整笔订单成本总额，允许高于成交额",
    ("sales_data", "sales_order", "created_at"): "记录创建时间，UTC，毫秒精度",
    ("sales_data", "sales_order", "customer_name"): "可选客户名称，不作为首版分析维度",
    ("sales_data", "sales_order", "id"): "单商品订单 ID",
    ("sales_data", "sales_order", "order_date"): "Asia/Shanghai 业务日期",
    ("sales_data", "sales_order", "order_no"): "唯一业务订单号",
    ("sales_data", "sales_order", "product_id"): "订单商品 ID，关联 product.id",
    ("sales_data", "sales_order", "quantity"): "销售数量，必须为正",
    ("sales_data", "sales_order", "region_id"): "成交时区域快照，不跟随员工调区变化",
    ("sales_data", "sales_order", "rep_id"): "成交销售员",
    (
        "sales_data",
        "sales_order",
        "status",
    ): "订单状态：COMPLETED 已完成，REFUNDED 已全额退款，CANCELLED "
    "已取消；仅已完成订单参与销售统计",
    ("sales_data", "sales_order", "unit_price"): "成交单价快照，币种人民币",
    (
        "sales_data",
        "sales_order",
        "updated_at",
    ): "记录更新时间，UTC，毫秒精度，更新时自动刷新",
    ("sales_data", "sales_region", "active"): "1 启用，0 停用",
    ("sales_data", "sales_region", "created_at"): "记录创建时间，UTC，毫秒精度",
    ("sales_data", "sales_region", "id"): "区域 ID",
    ("sales_data", "sales_region", "name"): "区域名称，首版全局唯一",
    ("sales_data", "sales_region", "parent_id"): "上级区域，顶层为空",
    (
        "sales_data",
        "sales_region",
        "updated_at",
    ): "记录更新时间，UTC，毫秒精度，更新时自动刷新",
    ("sales_data", "sales_rep", "active"): "销售员是否启用：1 启用，0 停用",
    ("sales_data", "sales_rep", "created_at"): "记录创建时间，UTC，毫秒精度",
    ("sales_data", "sales_rep", "id"): "销售员 ID",
    ("sales_data", "sales_rep", "name"): "销售员姓名，允许重名",
    ("sales_data", "sales_rep", "region_id"): "当前所属区域",
    (
        "sales_data",
        "sales_rep",
        "updated_at",
    ): "记录更新时间，UTC，毫秒精度，更新时自动刷新",
}


def apply_column_comments(*metadata_registries) -> None:
    for metadata in metadata_registries:
        for table in metadata.tables.values():
            for column in table.columns:
                comment = COLUMN_COMMENTS.get((table.schema, table.name, column.name))
                if column.comment is None and comment is not None:
                    column.comment = comment
