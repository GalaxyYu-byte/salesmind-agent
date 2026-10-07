# SalesMind Agent

基于 Python 的智能销售数据分析 Agent。目标是让销售人员通过自然语言完成数据查询、统计分析、图表展示和连续追问。

当前阶段：**架构设计、ORM 实体、MySQL/Redis 连接和 Alembic 迁移已接入，已接通首个确定性销售概览查询与前端；Agent 工作流尚未实现**。本仓库参考 Java 项目的业务场景，按 Python 技术栈和简历中的工作流要求重新设计。

## 设计文档

- [完整架构设计](docs/完整架构设计.md)：系统分层、LangGraph 工作流、工具协议、数据模型、指标口径、权限、多轮会话、缓存、SSE、部署与故障处理。
- [数据库设计](docs/数据库设计.md)：5 张业务表与 8 张应用表的字段、关系、约束、索引和生命周期；附 [业务库 DDL](docs/sql/001_sales_data.sql) 与 [应用库 DDL](docs/sql/002_agent_app.sql)。
- [测试数据说明](docs/测试数据说明.md)：独立 demo 库、10,261 笔订单、10 个测试账号、8 个任务场景及固定统计断言；包含生成器和可导入 SQL。
- [实施与验收计划](docs/实施与验收计划.md)：目录结构、开发顺序、测试场景、简历职责与实现证据映射。
- [uv 常用命令](docs/uv常用命令.md)：环境同步、依赖管理、服务启动、测试与代码检查的日常命令。

## 技术栈

Python / FastAPI / Pydantic / LangChain / LangGraph StateGraph / Qwen 或 DeepSeek / SQLAlchemy / MySQL / Redis / Pandas / Plotly / Docker。

展示端已采用 Vue 3 + TypeScript + Plotly.js。图表由 Python 生成 Figure JSON，前端负责交互展示。

核心链路：

```text
自然语言 → 意图解析 → 受约束计划 → 安全查询 → 确定性计算 → 图表 → 证据化回答
                       LangGraph StateGraph
```

Python 版以显式状态图组织工作流，统一数据权限与指标口径。所有模型输出都按不可信输入校验；数据查询只能通过注册的结构化工具进入。

数据库设计、建表 SQL 和测试数据 SQL 已整理，测试数据通过离线校验；已通过 SSH 隧道验证 MySQL 两个 schema 和 Redis 连通性，确认业务库有 5 张表、应用库有 8 张表。Alembic 初始迁移覆盖这 13 张表，支持已有库校验后接管；数据内容和独立账号权限的完整数据库集成验证待实现。本阶段未运行 Java 初始化脚本或复制其运行配置。

## 本地启动与数据库配置

Windows 用户可双击项目根目录的 `start-dev.bat` 一键启动前后端。脚本会先结束占用后端 8000 或前端 5173 端口的进程，再启动服务；首次运行会在缺少前端依赖时执行 `npm ci`。服务分别运行在两个终端窗口中。

在项目根目录执行：

```powershell
uv sync --locked
Copy-Item .env.example .env  # 仅首次执行，已有 .env 时保留原配置
uv run uvicorn salesmind.main:app --reload
```

存活检查：`GET http://127.0.0.1:8000/health/live`。两个数据库 URL 留空时仍可启动；需要数据库的接口会返回 503。存活检查不验证数据库连通性。

在 `.env` 中填写两项独立账号配置，环境变量优先于 `.env`；修改后重启服务。密码中的特殊字符需要 URL 百分号编码。

```dotenv
SALESMIND_SALES_DATABASE_URL=mysql+asyncmy://sales_reader:REPLACE_ME@127.0.0.1:3306/sales_data
SALESMIND_APP_DATABASE_URL=mysql+asyncmy://agent_writer:REPLACE_ME@127.0.0.1:3306/agent_app
```

业务账号仅授予 `sales_data` 所需表的 SELECT，应用账号仅授予 `agent_app` 所需表的 SELECT / INSERT / UPDATE / DELETE。账号创建与授权由数据库管理员完成；服务不会自动建库、建表或授权。两个 URL 必须同时配置，驱动固定为 `mysql+asyncmy`，schema 与现有实体保持一致，默认要求用户名不同。当前开发环境仅有 root 时，可显式设置 `SALESMIND_ALLOW_SHARED_DATABASE_ACCOUNT=true`；建立独立账号后改回 false。配置表示和配置校验错误隐藏 DSN；SQLAlchemy 错误隐藏 SQL 绑定参数。

两个异步连接池由 FastAPI lifespan 创建，首次查询时才连接 MySQL，退出时释放。新连接统一设置 `utf8mb4`、UTC 和严格 SQL 模式；业务连接另设只读事务。这不替代数据库账号的 SELECT 授权；root 配置仅表示开发连接可用，尚未实现独立运行账号的权限隔离。

路由可注入 `salesmind.api.dependencies.SalesSession` 或 `AppSession`。后台任务从 `app.state.database` 取得资源，每个并发任务分别调用 `sales_session()` 或 `app_session()`，不共享 Session。Session 离开上下文时关闭，未提交事务回滚；应用写入必须明确提交：

```python
async with database.app_session() as session:
    async with session.begin():
        session.add(record)
```

事务块成功退出时提交，发生异常时回滚。连接池大小、溢出上限、等待时间、连接回收和连接超时见 `.env.example`。每个池默认最多 10 条连接，两个池合计 20 条。

```powershell
uv run pytest -q
uv run ruff check .
```

数据库生命周期测试使用内存 SQLite 验证提交、回滚和 Session 隔离，不代表真实 MySQL 权限验证通过。

## Redis 与 SSH 隧道

配置 `SALESMIND_REDIS_URL=redis://:REPLACE_ME@127.0.0.1:6380/0`；Redis 客户端按需连接，由 FastAPI lifespan 关闭。路由可注入 `RedisClient`，后台服务可从 `app.state.redis.client` 取得客户端。当前接入部署说明中的一台 Redis，Checkpoint 和缓存业务尚未实现。

本机开发使用部署说明中的 SSH 隧道。也可使用开发脚本，在被 Git 忽略的 `.env.ssh` 中填写 `SALESMIND_SSH_HOST`、`SALESMIND_SSH_USER` 和 `SALESMIND_SSH_PASSWORD`。脚本读取系统 known_hosts 或被忽略的 `.env.ssh-known-hosts`，拒绝未知主机密钥；首次连接需先用交互式 ssh 确认服务器密钥。SSH 配置、数据库密码和 Redis 密码都不写入源码。

```powershell
# 独立终端运行并保持开启；已有隧道时不要重复启动。
uv run python scripts/dev_tunnel.py

# 在另一个终端验证两个 MySQL 库 SELECT 1 与 Redis PING。
uv run python scripts/check_connections.py
```

`GET /health/ready` 对两个 MySQL schema 和 Redis 并行执行只读探测，全部成功返回 200，否则返回 503，并报告 `ok / error / timeout / not_configured`。响应不包含底层异常或凭据。`/health/live` 只检查进程存活。

## Alembic 迁移

管理连接使用 `.env` 中独立的 `SALESMIND_MIGRATION_DATABASE_URL`，当前开发期可使用 root；运行账号权限保持业务库只读、应用库读写。版本表为 `agent_app.alembic_version`，迁移命令不会随 FastAPI 启动自动执行。

已有 13 张表的环境先运行 `uv run python scripts/baseline_database.py` 校验并登记基线；两个空 schema 的新环境运行 `uv run alembic upgrade head`。之后使用 `uv run alembic current` 查看版本，`uv run alembic check` 检查可自动比较的结构差异。

后续修改 ORM，通过 `uv run alembic revision --autogenerate -m "describe_schema_change"` 生成候选迁移，检查后执行 `upgrade head`。配置、初始化、回滚、差异检查边界与隔离的 MySQL 集成测试见 [迁移说明](migrations/README.md)。

## 前端工作台

已提供独立的 Vue 3 + TypeScript + Vite + Plotly 前端，包含经营概览、智能分析对话、会话历史、报告保存与导出、区域月度数据页及移动端布局。

```powershell
cd frontend
npm ci
npm run dev
```

访问 `http://127.0.0.1:5173`。`npm test` 验证数据计算与演示问题解析，`npm run build` 检查 TypeScript 并生成生产资源。

已接通首个真实查询 `GET /api/v1/sales/overview`。工作台、数据页和规则对话通过该接口读取当前 MySQL `sales_data` 中的已完成订单；返回当前期与同比基期的月度、区域、商品聚合，时间与区域筛选会重新请求接口。数据库中目前为项目样例订单，真实查询不代表生产业务数据。

首次联调先启动已有 SSH 隧道，再在独立终端启动本机 API：

```powershell
.venv\Scripts\python.exe scripts/dev_tunnel.py
# 隧道已运行时不重复启动；在另一个终端运行下两行。
.venv\Scripts\python.exe -m uvicorn salesmind.main:app --host 127.0.0.1 --port 8000
```

销售接口现已要求登录。首版支持用户名、密码、图片验证码，Access Token 有效 30 分钟，Refresh Token 设备会话存 Redis、有效 7 天，退出仅撤销当前设备；所有启用账号暂时可访问全部销售数据。配置与接口见 [登录模块说明](AUTH.md)。自然语言仍采用规则解析，大模型和 SSE 尚未接入；取消仅中止客户端读取，恢复会重新查询。前端使用与验证见 [前端说明](frontend/README.md)。
