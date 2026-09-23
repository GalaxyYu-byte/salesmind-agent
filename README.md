# SalesMind Agent

基于 Python 的智能销售数据分析 Agent。目标是让销售人员通过自然语言完成数据查询、统计分析、图表展示和连续追问。

当前阶段：**架构设计完成，业务代码尚未实现**。本仓库参考 Java 项目的业务场景，按 Python 技术栈和简历中的工作流要求重新设计。

## 设计文档

- [完整架构设计](docs/完整架构设计.md)：系统分层、LangGraph 工作流、工具协议、数据模型、指标口径、权限、多轮会话、缓存、SSE、部署与故障处理。
- [数据库设计](docs/数据库设计.md)：5 张业务表与 8 张应用表的字段、关系、约束、索引和生命周期；附 [业务库 DDL](docs/sql/001_sales_data.sql) 与 [应用库 DDL](docs/sql/002_agent_app.sql)。
- [测试数据说明](docs/测试数据说明.md)：独立 demo 库、10,261 笔订单、10 个测试账号、8 个任务场景及固定统计断言；包含生成器和可导入 SQL。
- [实施与验收计划](docs/实施与验收计划.md)：目录结构、开发顺序、测试场景、简历职责与实现证据映射。
- [uv 常用命令](docs/uv常用命令.md)：环境同步、依赖管理、服务启动、测试与代码检查的日常命令。

## 技术栈

Python / FastAPI / Pydantic / LangChain / LangGraph StateGraph / Qwen 或 DeepSeek / SQLAlchemy / MySQL / Redis / Pandas / Plotly / Docker。

展示端拟采用 Vue 3 + TypeScript + Plotly.js。图表由 Python 生成 Figure JSON，前端负责交互展示。

核心链路：

```text
自然语言 → 意图解析 → 受约束计划 → 安全查询 → 确定性计算 → 图表 → 证据化回答
                       LangGraph StateGraph
```

Python 版以显式状态图组织工作流，统一数据权限与指标口径。所有模型输出都按不可信输入校验；数据查询只能通过注册的结构化工具进入。

数据库设计、建表 SQL 和测试数据 SQL 已整理，测试数据通过离线校验，尚未连接数据库执行；Alembic 迁移和数据库集成测试待实现。本阶段未运行 Java 初始化脚本或复制其运行配置。
