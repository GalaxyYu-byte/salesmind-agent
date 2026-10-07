"""迁移连接独立于运行账号；双 schema 共用 agent_app 中的版本表。"""

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings
from sqlalchemy import MetaData, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.schema import BLANK_SCHEMA

from salesmind.core.config import Settings
from salesmind.db import entities  # noqa: F401
from salesmind.db.base import AgentAppBase, SalesDataBase
from salesmind.db.session import SQL_MODE

INITIAL_REVISION = "20260930_0001"
DEFAULT_SCHEMAS = {"sales_data": "sales_data", "agent_app": "agent_app"}


class MigrationSettings(BaseSettings):
    """管理 DSN 仅迁移命令读取，FastAPI Settings 不加载它。"""

    model_config = Settings.model_config
    migration_database_url: SecretStr | None = None

    @model_validator(mode="after")
    def validate_url(self) -> "MigrationSettings":
        if self.migration_database_url is None:
            return self
        try:
            url = make_url(self.migration_database_url.get_secret_value())
        except (ArgumentError, ValueError):
            raise ValueError("迁移 DSN 格式无效") from None
        if (
            url.drivername != "mysql+asyncmy"
            or url.database != "agent_app"
            or not url.host
            or not url.username
            or not url.password
            or url.port is not None
            and not 1 <= url.port <= 65535
        ):
            raise ValueError(
                "迁移 DSN 必须包含账号密码，使用 mysql+asyncmy 并连接 agent_app"
            )
        return self


def target_metadata(config, default_schema: str | None = None) -> list[MetaData]:
    """测试可注入隔离 schema；默认仅管理 sales_data 和 agent_app。"""
    schemas = config.attributes.get("schema_map", DEFAULT_SCHEMAS)
    targets = []
    for source, name in (
        (SalesDataBase.metadata, "sales_data"),
        (AgentAppBase.metadata, "agent_app"),
    ):
        destination = None if schemas[name] == default_schema else schemas[name]
        if destination == name:
            targets.append(source)
        else:
            target = MetaData()
            for table in source.tables.values():
                table.to_metadata(
                    target,
                    schema=destination,
                    referred_schema_fn=lambda table, schema, constraint, referred: (
                        BLANK_SCHEMA if schema is None else schema
                    ),
                )
            targets.append(target)
    return targets


def migration_options(config, connection=None) -> dict:
    schemas = config.attributes.get("schema_map", DEFAULT_SCHEMAS)
    default_schema = (
        connection.dialect.default_schema_name
        if connection is not None
        else schemas["agent_app"]
    )
    targets = target_metadata(config, default_schema)
    names = {
        (table.schema, table.name)
        for metadata in targets
        for table in metadata.tables.values()
    }

    def include_name(name, type_, parent_names):
        if type_ == "schema":
            return name is None or name in schemas.values()
        if type_ == "table":
            schema = parent_names.get("schema_name")
            return (schema, name) in names
        return True

    return {
        "target_metadata": targets,
        "version_table_schema": schemas["agent_app"],
        "include_schemas": True,
        "include_name": include_name,
        "compare_type": True,
        "compare_server_default": True,
        "schema_map": schemas,
    }


def configure_migrations(config, **kwargs) -> None:
    from alembic import context

    context.configure(**migration_options(config, kwargs.get("connection")), **kwargs)


async def run_online_migrations(config) -> None:
    from alembic import context
    from alembic.util import CommandError

    settings = MigrationSettings()
    if settings.migration_database_url is None:
        raise CommandError(
            "请配置 SALESMIND_MIGRATION_DATABASE_URL；迁移不使用运行账号"
        )
    url = make_url(
        settings.migration_database_url.get_secret_value()
    ).update_query_dict({"charset": "utf8mb4"})
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"connect_timeout": 10},
    )

    def migrate(connection):
        configure_migrations(config, connection=connection)
        with context.begin_transaction():
            context.run_migrations()

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SET SESSION time_zone = '+00:00'"))
            await connection.execute(text(f"SET SESSION sql_mode = '{SQL_MODE}'"))
            await connection.commit()
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()
