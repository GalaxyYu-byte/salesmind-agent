# SalesMind 数据库迁移

一个 Alembic revision 同时管理 `sales_data` 的 5 张业务表与 `agent_app` 的 8 张应用表。版本表位于 `agent_app.alembic_version`，初始版本为 `20260930_0001`。

迁移脚本冻结表定义，不在执行时导入当前 ORM 创建表。初始迁移保留金额精度、unsigned 类型、毫秒时间、ON UPDATE、字符集、列注释、索引、CHECK 与外键；会话和任务之间的循环外键在建表后单独添加，回滚时先移除。

## 配置

在项目根目录 `.env` 中配置管理连接：

```dotenv
SALESMIND_MIGRATION_DATABASE_URL=mysql+asyncmy://migration_admin:REPLACE_ME@127.0.0.1:3307/agent_app
```

当前开发环境使用已有 root 进行迁移。该连接由独立的 `MigrationSettings` 读取，服务运行的 `Settings` 忽略它；Alembic 不回退到 `sales_reader` 或 `agent_writer`。管理账号需要两个 schema 的 DDL 权限及版本表读写权限。密码不写入 `alembic.ini`。

本机操作依赖 SSH 隧道，保持 `uv run python scripts/dev_tunnel.py` 运行。迁移是独立命令，FastAPI 启动不会执行建表或迁移。

## 已有数据库首次接管

已有业务和应用表时，不直接执行初始 `upgrade`，也不直接使用未校验的 `stamp head`。在初始结构与模型一致的项目版本执行：

```powershell
uv run python scripts/baseline_database.py
uv run alembic current
uv run alembic check
```

接管脚本先核对两个 schema 的表集合、CHECK 约束名称、Alembic 已登记版本，以及 Alembic 可比较的列、类型、默认值、注释、索引与外键。存在差异即拒绝接管。成功后仅创建版本表并登记 `20260930_0001`，不重建业务表或修改业务数据。脚本只适用于当前 head 仍为初始版本的仓库。

Alembic 的自动差异检查不覆盖 CHECK 表达式、触发器和所有 MySQL 特有属性；CHECK 名称匹配不等同于表达式语义匹配。修改这些对象时需显式写迁移并对照 DDL 核验，不能仅以 `alembic check` 通过判定没有差异。

## 空数据库初始化

管理员先创建两个空 schema，连接 DSN 指向 `agent_app`：

```sql
CREATE DATABASE sales_data CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE agent_app CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
```

然后执行：

```powershell
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

迁移不创建账号、不导入样例数据，也不创建数据库。以上两个 schema 名称与 ORM 映射一致，不要使用业务运行账号执行建表。

## 后续开发

修改 ORM 后生成并检查迁移：

```powershell
uv run alembic revision --autogenerate -m "describe_schema_change"
uv run alembic upgrade head
uv run alembic check
uv run alembic history
```

自动生成是候选迁移，需检查数据回填、约束和升级/回滚顺序。自动生成仅反射目标 schema 中受管理的表，不生成其他数据库或未纳管表的删除指令。

预览初始化 SQL（不连接数据库；Windows 显式使用 UTF-8 输出）：

```powershell
uv run python -X utf8 -m alembic upgrade head --sql
```

`uv run alembic downgrade base` 会删除迁移管理的 13 张表及其数据，保留空版本表；仅在明确需要回滚的环境执行。MySQL DDL 不是事务性的，失败后需检查已完成的语句再处理。

## 验证

默认测试包含离线升级/回滚 SQL、版本链、管理配置隔离与默认 schema 外键解析：

```powershell
uv run pytest -q
```

真实 MySQL 集成测试需显式启用：

```powershell
$env:SALESMIND_RUN_MYSQL_MIGRATION_TESTS = "1"
uv run pytest -q tests/test_migrations_mysql.py
Remove-Item Env:SALESMIND_RUN_MYSQL_MIGRATION_TESTS
```

测试使用管理连接创建两个随机命名的 `sm_mig_*` 临时 schema，验证升级、真实默认值漂移拒绝、缺失 CHECK 拒绝、已有结构接管、`check` 与回滚，结束后仅删除本次成功创建的临时 schema。
