"""SalesMind 初始结构：5 张业务表、8 张应用表。

Revision ID: 20260930_0001
Revises: None
"""

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import mysql

revision = "20260930_0001"
down_revision = None
branch_labels = None
depends_on = None


def _schema(name):
    # 隔离集成测试可映射 schema，默认名称固定。
    return context.get_context().opts.get("schema_map", {}).get(name, name)


def _reference(value):
    schema, table, column = value.split(".", 2)
    return f"{_schema(schema)}.{table}.{column}"


def upgrade() -> None:
    # 冻结的初始结构；后续修改应创建新 revision。
    op.create_table(
        "dataset_version",
        sa.Column(
            "id",
            mysql.TINYINT(unsigned=True),
            autoincrement=False,
            nullable=False,
            comment="固定为 1，描述当前发布的数据集",
        ),
        sa.Column(
            "version",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            comment="只递增的数据版本",
        ),
        sa.Column(
            "refreshed_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            comment="业务数据与版本一起提交的 UTC 时间",
        ),
        sa.Column(
            "available_from", sa.Date(), nullable=False, comment="数据覆盖起始业务日期"
        ),
        sa.Column(
            "complete_through",
            sa.Date(),
            nullable=True,
            comment="截至该日数据完整，含当日；未知时为空",
        ),
        sa.CheckConstraint(
            "complete_through IS NULL OR complete_through >= available_from",
            name="ck_dataset_dates",
        ),
        sa.CheckConstraint("id = 1", name="ck_dataset_singleton"),
        sa.CheckConstraint("version > 0", name="ck_dataset_version"),
        sa.PrimaryKeyConstraint("id"),
        schema=_schema("sales_data"),
        comment="当前业务数据版本和完整性水位，不保存历史数据快照",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "product",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
            comment="商品 ID",
        ),
        sa.Column(
            "sku",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=50),
            nullable=False,
            comment="商品编码",
        ),
        sa.Column("name", sa.String(length=200), nullable=False, comment="商品名称"),
        sa.Column(
            "category",
            sa.String(length=50),
            nullable=False,
            comment="首版固定品类，已有订单后不直接修改",
        ),
        sa.Column(
            "active",
            mysql.TINYINT(unsigned=True),
            server_default=sa.text("1"),
            nullable=False,
            comment="商品是否启用：1 启用，0 停用",
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="记录创建时间，UTC，毫秒精度",
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text(
                "CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"
            ),
            nullable=False,
            comment="记录更新时间，UTC，毫秒精度，更新时自动刷新",
        ),
        sa.CheckConstraint("active IN (0, 1)", name="ck_product_active"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku", name="uk_product_sku"),
        schema=_schema("sales_data"),
        comment="商品维度；统计使用订单中的实际成交金额与成本",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_product_category",
        "product",
        ["category"],
        unique=False,
        schema=_schema("sales_data"),
    )
    op.create_table(
        "sales_region",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
            comment="区域 ID",
        ),
        sa.Column(
            "name",
            sa.String(length=50),
            nullable=False,
            comment="区域名称，首版全局唯一",
        ),
        sa.Column(
            "parent_id",
            mysql.BIGINT(unsigned=True),
            nullable=True,
            comment="上级区域，顶层为空",
        ),
        sa.Column(
            "active",
            mysql.TINYINT(unsigned=True),
            server_default=sa.text("1"),
            nullable=False,
            comment="1 启用，0 停用",
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="记录创建时间，UTC，毫秒精度",
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text(
                "CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"
            ),
            nullable=False,
            comment="记录更新时间，UTC，毫秒精度，更新时自动刷新",
        ),
        sa.CheckConstraint("active IN (0, 1)", name="ck_region_active"),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            [_reference("sales_data.sales_region.id")],
            name="fk_region_parent",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uk_region_name"),
        schema=_schema("sales_data"),
        comment="销售区域；父子循环由写入服务拒绝",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_region_parent",
        "sales_region",
        ["parent_id"],
        unique=False,
        schema=_schema("sales_data"),
    )
    op.create_table(
        "sales_rep",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
            comment="销售员 ID",
        ),
        sa.Column(
            "name", sa.String(length=50), nullable=False, comment="销售员姓名，允许重名"
        ),
        sa.Column(
            "region_id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            comment="当前所属区域",
        ),
        sa.Column(
            "active",
            mysql.TINYINT(unsigned=True),
            server_default=sa.text("1"),
            nullable=False,
            comment="销售员是否启用：1 启用，0 停用",
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="记录创建时间，UTC，毫秒精度",
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text(
                "CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"
            ),
            nullable=False,
            comment="记录更新时间，UTC，毫秒精度，更新时自动刷新",
        ),
        sa.CheckConstraint("active IN (0, 1)", name="ck_rep_active"),
        sa.ForeignKeyConstraint(
            ["region_id"],
            [_reference("sales_data.sales_region.id")],
            name="fk_rep_region",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=_schema("sales_data"),
        comment="销售员维度，不承担登录认证",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_rep_region",
        "sales_rep",
        ["region_id"],
        unique=False,
        schema=_schema("sales_data"),
    )
    op.create_table(
        "sales_order",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
            comment="单商品订单 ID",
        ),
        sa.Column(
            "order_no",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=64),
            nullable=False,
            comment="唯一业务订单号",
        ),
        sa.Column(
            "rep_id", mysql.BIGINT(unsigned=True), nullable=False, comment="成交销售员"
        ),
        sa.Column(
            "region_id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            comment="成交时区域快照，不跟随员工调区变化",
        ),
        sa.Column(
            "product_id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            comment="订单商品 ID，关联 product.id",
        ),
        sa.Column(
            "customer_name",
            sa.String(length=100),
            nullable=True,
            comment="可选客户名称，不作为首版分析维度",
        ),
        sa.Column(
            "quantity",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            comment="销售数量，必须为正",
        ),
        sa.Column(
            "unit_price",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
            comment="成交单价快照，币种人民币",
        ),
        sa.Column(
            "amount",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
            comment="成交总额，首版等于数量乘成交单价",
        ),
        sa.Column(
            "cost",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
            comment="整笔订单成本总额，允许高于成交额",
        ),
        sa.Column(
            "status",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=20),
            nullable=False,
            comment="订单状态：COMPLETED 已完成，REFUNDED 已全额退款，CANCELLED 已取消；仅已完成订单参与销售统计",
        ),
        sa.Column(
            "order_date", sa.Date(), nullable=False, comment="Asia/Shanghai 业务日期"
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="记录创建时间，UTC，毫秒精度",
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text(
                "CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"
            ),
            nullable=False,
            comment="记录更新时间，UTC，毫秒精度，更新时自动刷新",
        ),
        sa.CheckConstraint(
            "status IN ('COMPLETED', 'REFUNDED', 'CANCELLED')", name="ck_order_status"
        ),
        sa.CheckConstraint(
            "amount = ROUND(quantity * unit_price, 2)", name="ck_order_amount_formula"
        ),
        sa.CheckConstraint("quantity > 0", name="ck_order_quantity"),
        sa.CheckConstraint(
            "unit_price >= 0 AND amount >= 0 AND cost >= 0", name="ck_order_amounts"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            [_reference("sales_data.product.id")],
            name="fk_order_product",
        ),
        sa.ForeignKeyConstraint(
            ["region_id"],
            [_reference("sales_data.sales_region.id")],
            name="fk_order_region",
        ),
        sa.ForeignKeyConstraint(
            ["rep_id"], [_reference("sales_data.sales_rep.id")], name="fk_order_rep"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_no", name="uk_order_no"),
        schema=_schema("sales_data"),
        comment="销售事实表，一行一个单商品订单，不独立存储毛利",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_order_date",
        "sales_order",
        ["order_date"],
        unique=False,
        schema=_schema("sales_data"),
    )
    op.create_index(
        "idx_order_product_status_date",
        "sales_order",
        ["product_id", "status", "order_date"],
        unique=False,
        schema=_schema("sales_data"),
    )
    op.create_index(
        "idx_order_region_status_date",
        "sales_order",
        ["region_id", "status", "order_date"],
        unique=False,
        schema=_schema("sales_data"),
    )
    op.create_index(
        "idx_order_rep_status_date",
        "sales_order",
        ["rep_id", "status", "order_date"],
        unique=False,
        schema=_schema("sales_data"),
    )
    op.create_index(
        "idx_order_status_date_product",
        "sales_order",
        ["status", "order_date", "product_id"],
        unique=False,
        schema=_schema("sales_data"),
    )
    op.create_table(
        "app_user",
        sa.Column(
            "id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="登录账号 ID，服务端生成的小写 UUID",
        ),
        sa.Column(
            "username",
            mysql.VARCHAR(charset="ascii", collation="ascii_general_ci", length=64),
            nullable=False,
            comment="唯一登录用户名，仅限 ASCII，不区分大小写",
        ),
        sa.Column(
            "display_name",
            sa.String(length=100),
            nullable=False,
            comment="用户展示名称",
        ),
        sa.Column(
            "password_hash",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=255),
            nullable=False,
            comment="带盐密码哈希，不保存明文密码",
        ),
        sa.Column(
            "role",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=32),
            nullable=False,
            comment="用户角色：SALES_REP 销售员，SALES_MANAGER 销售经理，SALES_DIRECTOR 销售总监",
        ),
        sa.Column(
            "rep_id",
            mysql.BIGINT(unsigned=True),
            nullable=True,
            comment="逻辑关联 sales_data.sales_rep；销售员角色必填",
        ),
        sa.Column(
            "allowed_metrics_json",
            sa.JSON(),
            nullable=False,
            comment="服务端维护的指标白名单，空数组表示无指标权限",
        ),
        sa.Column(
            "permission_version",
            mysql.BIGINT(unsigned=True),
            server_default=sa.text("1"),
            nullable=False,
            comment="当前权限版本，角色、人员绑定、指标、启停或区域授权变更时在同一事务递增",
        ),
        sa.Column(
            "active",
            mysql.TINYINT(unsigned=True),
            server_default=sa.text("1"),
            nullable=False,
            comment="账号是否启用：1 允许登录和使用系统，0 停用",
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="记录创建时间，UTC，毫秒精度",
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text(
                "CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"
            ),
            nullable=False,
            comment="记录更新时间，UTC，毫秒精度，更新时自动刷新",
        ),
        sa.CheckConstraint(
            "JSON_TYPE(allowed_metrics_json) = 'ARRAY'", name="ck_user_metrics"
        ),
        sa.CheckConstraint(
            "role <> 'SALES_REP' OR rep_id IS NOT NULL", name="ck_user_rep_required"
        ),
        sa.CheckConstraint(
            "role IN ('SALES_REP', 'SALES_MANAGER', 'SALES_DIRECTOR')",
            name="ck_user_role",
        ),
        sa.CheckConstraint("active IN (0, 1)", name="ck_user_active"),
        sa.CheckConstraint("permission_version > 0", name="ck_user_permission_version"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rep_id", name="uk_user_rep"),
        sa.UniqueConstraint("username", name="uk_user_username"),
        schema=_schema("agent_app"),
        comment="登录账号；一个销售员最多绑定一个账号，账号可不绑定销售员",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "chat_session",
        sa.Column(
            "id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="会话 ID，服务端生成的小写 UUID",
        ),
        sa.Column(
            "owner_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="会话所属登录账号 ID，关联 app_user.id，由服务端从认证身份取得",
        ),
        sa.Column(
            "title",
            sa.String(length=200),
            server_default=sa.text("'新会话'"),
            nullable=False,
            comment="会话列表展示标题",
        ),
        sa.Column(
            "active_run_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=True,
            comment="当前占用会话的分析任务 ID，关联 analysis_run；无活动任务时为空",
        ),
        sa.Column(
            "graph_version",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=32),
            nullable=False,
            comment="会话工作流版本，用于判断检查点兼容性",
        ),
        sa.Column(
            "context_summary",
            sa.Text(),
            nullable=True,
            comment="有界语义摘要，不保存大结果和完整模型提示词",
        ),
        sa.Column(
            "context_scope_hash",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=64),
            nullable=True,
            comment="语义摘要生成时的权限范围 SHA-256 指纹，复用摘要前校验",
        ),
        sa.Column(
            "context_permission_version",
            mysql.BIGINT(unsigned=True),
            nullable=True,
            comment="语义摘要生成时的权限版本，复用摘要前校验",
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="记录创建时间，UTC，毫秒精度",
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text(
                "CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"
            ),
            nullable=False,
            comment="记录更新时间，UTC，毫秒精度，更新时自动刷新",
        ),
        sa.ForeignKeyConstraint(
            ["active_run_id", "id", "owner_id"],
            [
                _reference("agent_app.analysis_run.id"),
                _reference("agent_app.analysis_run.session_id"),
                _reference("agent_app.analysis_run.owner_id"),
            ],
            name="fk_session_active_run",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], [_reference("agent_app.app_user.id")], name="fk_session_owner"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "owner_id", name="uk_session_owner"),
        schema=_schema("agent_app"),
        comment="一个用户拥有多个会话；同会话串行执行任务",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_session_active",
        "chat_session",
        ["active_run_id", "id", "owner_id"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_index(
        "idx_session_owner_updated",
        "chat_session",
        ["owner_id", "updated_at", "id"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_table(
        "user_region_scope",
        sa.Column(
            "user_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="被授权的登录账号 ID，关联 app_user.id",
        ),
        sa.Column(
            "region_id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            comment="逻辑关联 sales_data.sales_region",
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="区域授权创建时间，UTC，毫秒精度",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], [_reference("agent_app.app_user.id")], name="fk_scope_user"
        ),
        sa.PrimaryKeyConstraint("user_id", "region_id"),
        schema=_schema("agent_app"),
        comment="经理与授权区域的多对多关联；变更时同步递增用户权限版本",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_scope_region",
        "user_region_scope",
        ["region_id"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_table(
        "analysis_run",
        sa.Column(
            "id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="分析任务 ID，服务端生成的小写 UUID",
        ),
        sa.Column(
            "session_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="所属会话 ID，与 owner_id 共同关联 chat_session",
        ),
        sa.Column(
            "owner_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="任务所属登录账号 ID，与会话归属一致，由服务端从认证身份取得",
        ),
        sa.Column(
            "request_id",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=64),
            nullable=False,
            comment="创建请求幂等键",
        ),
        sa.Column(
            "input_hash",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=64),
            nullable=False,
            comment="规范化请求的 SHA-256，包含 session_id",
        ),
        sa.Column(
            "request_json",
            sa.JSON(),
            nullable=False,
            comment="原始业务请求，禁止包含令牌或服务端密钥",
        ),
        sa.Column(
            "status",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=24),
            server_default=sa.text("'queued'"),
            nullable=False,
            comment="任务状态：queued 排队，running 执行中，awaiting_input 等待澄清，interrupted 中断可恢复，cancelling 取消中，completed 完成，partial 部分完成，failed 失败，cancelled 已取消",
        ),
        sa.Column(
            "stage",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=32),
            nullable=True,
            comment="当前工作流节点",
        ),
        sa.Column(
            "attempt",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
            comment="开始执行或恢复时递增，用于拒绝旧执行者写入",
        ),
        sa.Column(
            "reference_date", sa.Date(), nullable=False, comment="本轮固定的业务基准日"
        ),
        sa.Column(
            "timezone",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=64),
            server_default=sa.text("'Asia/Shanghai'"),
            nullable=False,
            comment="本轮固定的业务时区，用于解释业务日期和相对时间",
        ),
        sa.Column(
            "graph_version",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=32),
            nullable=False,
            comment="本轮固定的工作流版本，用于执行和恢复兼容性校验",
        ),
        sa.Column(
            "metric_version",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=32),
            nullable=False,
            comment="本轮固定的指标公式版本",
        ),
        sa.Column(
            "scope_hash",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=64),
            nullable=False,
            comment="任务创建时固定的有效用户、人员、区域及指标权限的 SHA-256 指纹",
        ),
        sa.Column(
            "permission_version",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            comment="创建任务时的权限快照版本",
        ),
        sa.Column(
            "data_version",
            mysql.BIGINT(unsigned=True),
            nullable=True,
            comment="第一次获取业务数据后固定；跨库逻辑关联",
        ),
        sa.Column(
            "last_event_seq",
            mysql.BIGINT(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
            comment="持有任务行锁时递增",
        ),
        sa.Column(
            "last_message_seq",
            mysql.BIGINT(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
            comment="任务内最后分配的消息序号，初始为 0，用于分配 chat_message.seq",
        ),
        sa.Column(
            "replay_floor_seq",
            mysql.BIGINT(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
            comment="已清理事件的最大 seq",
        ),
        sa.Column(
            "node_steps",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
            comment="本轮累计工作流节点访问次数，恢复时不重置，用于预算控制",
        ),
        sa.Column(
            "tool_calls",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
            comment="本轮累计工具调用次数，恢复时不重置，用于预算控制",
        ),
        sa.Column(
            "model_calls",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
            comment="本轮累计模型调用次数，恢复时不重置，用于预算控制",
        ),
        sa.Column(
            "active_elapsed_ms",
            mysql.BIGINT(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
            comment="累计执行耗时，不含等待澄清",
        ),
        sa.Column(
            "error_code",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=64),
            nullable=True,
            comment="任务业务错误码，无错误时为空",
        ),
        sa.Column(
            "error_message",
            sa.String(length=500),
            nullable=True,
            comment="可展示且已脱敏的错误说明",
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="记录创建时间，UTC，毫秒精度",
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text(
                "CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"
            ),
            nullable=False,
            comment="记录更新时间，UTC，毫秒精度，更新时自动刷新",
        ),
        sa.Column(
            "started_at",
            mysql.DATETIME(fsp=3),
            nullable=True,
            comment="任务首次开始执行时间，UTC，毫秒精度，尚未开始时为空",
        ),
        sa.Column(
            "finished_at",
            mysql.DATETIME(fsp=3),
            nullable=True,
            comment="仅不可恢复终态设置",
        ),
        sa.Column(
            "paused_until",
            mysql.DATETIME(fsp=3),
            nullable=True,
            comment="澄清或中断状态的过期截止时间",
        ),
        sa.CheckConstraint(
            "(status IN ('completed', 'partial', 'failed', 'cancelled') AND finished_at IS NOT NULL) OR (status NOT IN ('completed', 'partial', 'failed', 'cancelled') AND finished_at IS NULL)",
            name="ck_run_finished",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'awaiting_input', 'interrupted', 'cancelling', 'completed', 'partial', 'failed', 'cancelled')",
            name="ck_run_status",
        ),
        sa.CheckConstraint(
            "data_version IS NULL OR data_version > 0", name="ck_run_data_version"
        ),
        sa.CheckConstraint("permission_version > 0", name="ck_run_permission"),
        sa.CheckConstraint(
            "replay_floor_seq <= last_event_seq", name="ck_run_replay_floor"
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "owner_id"],
            [
                _reference("agent_app.chat_session.id"),
                _reference("agent_app.chat_session.owner_id"),
            ],
            name="fk_run_session_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "session_id", "owner_id", name="uk_run_session_owner"
        ),
        sa.UniqueConstraint("owner_id", "request_id", name="uk_run_owner_request"),
        schema=_schema("agent_app"),
        comment="一次独立分析请求；澄清和恢复继续使用同一任务",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_run_paused_until",
        "analysis_run",
        ["paused_until"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_index(
        "idx_run_session_created",
        "analysis_run",
        ["session_id", "created_at", "id"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_index(
        "idx_run_session_owner_fk",
        "analysis_run",
        ["session_id", "owner_id"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_index(
        "idx_run_status_updated",
        "analysis_run",
        ["status", "updated_at"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_table(
        "chat_message",
        sa.Column(
            "id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="消息 ID，服务端生成的小写 UUID",
        ),
        sa.Column(
            "run_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="所属分析任务 ID，与 session_id、owner_id 共同关联 analysis_run",
        ),
        sa.Column(
            "session_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="所属会话 ID，与关联分析任务的会话一致",
        ),
        sa.Column(
            "owner_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="消息所属登录账号 ID，与关联分析任务的归属一致",
        ),
        sa.Column(
            "seq",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            comment="任务内消息顺序，由 last_message_seq 分配",
        ),
        sa.Column(
            "message_key",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=128),
            nullable=False,
            comment="稳定业务键，重试不改变",
        ),
        sa.Column(
            "role",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=16),
            nullable=False,
            comment="消息角色：user 用户，assistant 助手",
        ),
        sa.Column(
            "kind",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=32),
            nullable=False,
            comment="消息用途：用户为 question 提问或 clarification_reply 澄清回复；助手为 clarification 澄清问题、answer 回答或 error 错误提示",
        ),
        sa.Column(
            "content",
            mysql.MEDIUMTEXT(),
            nullable=False,
            comment="用户可见文本；读取助手结果前重新校验 run 权限",
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="消息保存时间，UTC，毫秒精度",
        ),
        sa.CheckConstraint(
            "(role = 'user' AND kind IN ('question', 'clarification_reply')) OR (role = 'assistant' AND kind IN ('clarification', 'answer', 'error'))",
            name="ck_message_role_kind",
        ),
        sa.CheckConstraint("seq > 0", name="ck_message_seq"),
        sa.ForeignKeyConstraint(
            ["run_id", "session_id", "owner_id"],
            [
                _reference("agent_app.analysis_run.id"),
                _reference("agent_app.analysis_run.session_id"),
                _reference("agent_app.analysis_run.owner_id"),
            ],
            name="fk_message_run_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "message_key", name="uk_message_run_key"),
        sa.UniqueConstraint("run_id", "seq", name="uk_message_run_seq"),
        schema=_schema("agent_app"),
        comment="按条保存可见消息，允许同任务中多次澄清，不保存内部推理",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_message_run_owner_fk",
        "chat_message",
        ["run_id", "session_id", "owner_id"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_index(
        "idx_message_session_created",
        "chat_message",
        ["session_id", "created_at", "id"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_table(
        "result_artifact",
        sa.Column(
            "id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="分析结果 ID，服务端生成的小写 UUID",
        ),
        sa.Column(
            "run_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="所属分析任务 ID，与 session_id、owner_id 共同关联 analysis_run",
        ),
        sa.Column(
            "session_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="所属会话 ID，与关联分析任务的会话一致",
        ),
        sa.Column(
            "owner_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="结果所属登录账号 ID，与关联分析任务的归属一致",
        ),
        sa.Column(
            "artifact_key",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=128),
            nullable=False,
            comment="任务内稳定输出键，如 q_current:dataset",
        ),
        sa.Column(
            "kind",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=16),
            nullable=False,
            comment="结果类型：DATASET 数据集，FIGURE Plotly 图表，ANSWER 结构化答案",
        ),
        sa.Column(
            "schema_version",
            mysql.SMALLINT(unsigned=True),
            server_default=sa.text("1"),
            nullable=False,
            comment="分析结果结构协议版本，必须大于 0",
        ),
        sa.Column(
            "schema_json",
            sa.JSON(),
            nullable=False,
            comment="列类型或结构协议，以及来源 artifact 引用",
        ),
        sa.Column(
            "payload_json",
            sa.JSON(),
            nullable=False,
            comment="有界数据集、Plotly Figure 或结构化答案",
        ),
        sa.Column(
            "scope_hash",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=64),
            nullable=False,
            comment="结果生成时的有效用户、人员、区域及指标权限的 SHA-256 指纹",
        ),
        sa.Column(
            "permission_version",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            comment="结果生成时的权限快照版本，必须大于 0，读取和复用前校验",
        ),
        sa.Column(
            "data_version",
            mysql.BIGINT(unsigned=True),
            nullable=True,
            comment="无业务数据的解释性回答可为空",
        ),
        sa.Column(
            "metric_version",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=32),
            nullable=False,
            comment="生成结果时使用的指标公式版本",
        ),
        sa.Column(
            "row_count",
            mysql.INTEGER(unsigned=True),
            nullable=True,
            comment="仅数据集填结果行数",
        ),
        sa.Column(
            "byte_size",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            comment="服务端计算 schema 与 payload 的序列化总字节数",
        ),
        sa.Column(
            "expires_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            comment="结果过期时间，UTC，毫秒精度，由应用设置且必须晚于创建时间",
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="结果保存时间，UTC，毫秒精度",
        ),
        sa.CheckConstraint(
            "(data_version IS NOT NULL AND data_version > 0) OR (kind = 'ANSWER' AND data_version IS NULL)",
            name="ck_artifact_data_version",
        ),
        sa.CheckConstraint(
            "(kind = 'DATASET' AND row_count IS NOT NULL) OR (kind <> 'DATASET' AND row_count IS NULL)",
            name="ck_artifact_rows",
        ),
        sa.CheckConstraint(
            "kind IN ('DATASET', 'FIGURE', 'ANSWER')", name="ck_artifact_kind"
        ),
        sa.CheckConstraint(
            "byte_size > 0 AND byte_size <= 1048576", name="ck_artifact_bytes"
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_artifact_expiry"),
        sa.CheckConstraint("permission_version > 0", name="ck_artifact_permission"),
        sa.CheckConstraint("schema_version > 0", name="ck_artifact_schema"),
        sa.ForeignKeyConstraint(
            ["run_id", "session_id", "owner_id"],
            [
                _reference("agent_app.analysis_run.id"),
                _reference("agent_app.analysis_run.session_id"),
                _reference("agent_app.analysis_run.owner_id"),
            ],
            name="fk_artifact_run_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "artifact_key", name="uk_artifact_run_key"),
        schema=_schema("agent_app"),
        comment="带归属、权限与数据版本的分析结果，默认保留七天",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_artifact_expires",
        "result_artifact",
        ["expires_at"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_index(
        "idx_artifact_run_owner_fk",
        "result_artifact",
        ["run_id", "session_id", "owner_id"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_index(
        "idx_artifact_session_created",
        "result_artifact",
        ["session_id", "created_at", "id"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_table(
        "run_event",
        sa.Column(
            "run_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="所属分析任务 ID，关联 analysis_run.id，与 seq 构成联合主键",
        ),
        sa.Column(
            "seq",
            mysql.BIGINT(unsigned=True),
            nullable=False,
            comment="任务内单调递增事件序号",
        ),
        sa.Column(
            "schema_version",
            mysql.SMALLINT(unsigned=True),
            server_default=sa.text("1"),
            nullable=False,
            comment="SSE 业务事件协议版本，必须大于 0",
        ),
        sa.Column(
            "event_type",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=64),
            nullable=False,
            comment="业务事件类型，如 stage.started、tool.completed、run.completed",
        ),
        sa.Column(
            "payload_json",
            sa.JSON(),
            nullable=False,
            comment="有界业务事件；大结果只存 artifact_id",
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
            comment="事件保存时间，UTC，毫秒精度，用于保留期清理",
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_event_schema"),
        sa.CheckConstraint("seq > 0", name="ck_event_seq"),
        sa.ForeignKeyConstraint(
            ["run_id"], [_reference("agent_app.analysis_run.id")], name="fk_event_run"
        ),
        sa.PrimaryKeyConstraint("run_id", "seq"),
        schema=_schema("agent_app"),
        comment="SSE 业务事件回放日志；心跳不入库",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_event_created",
        "run_event",
        ["created_at"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_table(
        "tool_execution",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            autoincrement=True,
            nullable=False,
            comment="工具执行审计记录 ID，自增主键",
        ),
        sa.Column(
            "run_id",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=36),
            nullable=False,
            comment="所属分析任务 ID，关联 analysis_run.id",
        ),
        sa.Column(
            "task_id",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=64),
            nullable=False,
            comment="计划中的稳定任务 ID",
        ),
        sa.Column(
            "run_attempt",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            comment="所属 run 执行尝试，恢复时不同",
        ),
        sa.Column(
            "attempt",
            mysql.INTEGER(unsigned=True),
            nullable=False,
            comment="当前 run_attempt 内工具自身的重试次数，从 1 开始",
        ),
        sa.Column(
            "tool_name",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=64),
            nullable=False,
            comment="本次调用的注册工具名称",
        ),
        sa.Column(
            "fingerprint",
            mysql.CHAR(charset="ascii", collation="ascii_bin", length=64),
            nullable=False,
            comment="规范化参数与权限、版本的指纹，不存原始敏感参数",
        ),
        sa.Column(
            "cache_hit",
            mysql.TINYINT(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
            comment="本次工具调用是否命中缓存：0 未命中，1 命中",
        ),
        sa.Column(
            "duration_ms",
            mysql.BIGINT(unsigned=True),
            nullable=True,
            comment="本次工具调用耗时，单位毫秒；running 时为空，其他状态必填",
        ),
        sa.Column(
            "status",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=16),
            nullable=False,
            comment="工具执行状态：running 执行中，succeeded 成功，failed 失败，cancelled 已取消，interrupted 已中断",
        ),
        sa.Column(
            "error_code",
            mysql.VARCHAR(charset="ascii", collation="ascii_bin", length=64),
            nullable=True,
            comment="工具调用错误码，无错误时为空",
        ),
        sa.Column(
            "started_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            comment="本次工具调用开始时间，UTC，毫秒精度",
        ),
        sa.Column(
            "finished_at",
            mysql.DATETIME(fsp=3),
            nullable=True,
            comment="本次工具调用结束时间，UTC，毫秒精度；running 时为空，其他状态必填",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND finished_at IS NULL AND duration_ms IS NULL) OR (status <> 'running' AND finished_at IS NOT NULL AND duration_ms IS NOT NULL)",
            name="ck_tool_finished",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled', 'interrupted')",
            name="ck_tool_status",
        ),
        sa.CheckConstraint("cache_hit IN (0, 1)", name="ck_tool_cache"),
        sa.CheckConstraint("run_attempt > 0 AND attempt > 0", name="ck_tool_attempts"),
        sa.ForeignKeyConstraint(
            ["run_id"], [_reference("agent_app.analysis_run.id")], name="fk_tool_run"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "task_id", "run_attempt", "attempt", name="uk_tool_attempt"
        ),
        schema=_schema("agent_app"),
        comment="一条工具执行尝试一条审计记录，可分别追踪恢复与工具重试",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "idx_tool_started",
        "tool_execution",
        ["started_at"],
        unique=False,
        schema=_schema("agent_app"),
    )
    op.create_foreign_key(
        "fk_session_active_run",
        "chat_session",
        "analysis_run",
        ["active_run_id", "id", "owner_id"],
        ["id", "session_id", "owner_id"],
        source_schema=_schema("agent_app"),
        referent_schema=_schema("agent_app"),
        use_alter=True,
    )


def downgrade() -> None:
    # 冻结的初始结构；后续修改应创建新 revision。
    op.drop_constraint(
        "fk_session_active_run",
        "chat_session",
        schema=_schema("agent_app"),
        type_="foreignkey",
    )
    op.drop_table("tool_execution", schema=_schema("agent_app"))
    op.drop_table("run_event", schema=_schema("agent_app"))
    op.drop_table("result_artifact", schema=_schema("agent_app"))
    op.drop_table("chat_message", schema=_schema("agent_app"))
    op.drop_table("analysis_run", schema=_schema("agent_app"))
    op.drop_table("user_region_scope", schema=_schema("agent_app"))
    op.drop_table("chat_session", schema=_schema("agent_app"))
    op.drop_table("app_user", schema=_schema("agent_app"))
    op.drop_table("sales_order", schema=_schema("sales_data"))
    op.drop_table("sales_rep", schema=_schema("sales_data"))
    op.drop_table("sales_region", schema=_schema("sales_data"))
    op.drop_table("product", schema=_schema("sales_data"))
    op.drop_table("dataset_version", schema=_schema("sales_data"))
