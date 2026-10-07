"""核对已有初始表后登记 Alembic 基线，不执行原有表的建表/修改/删表。"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from salesmind.db.migration import (
    INITIAL_REVISION,
    MigrationSettings,
    migration_options,
)

ROOT = Path(__file__).resolve().parents[1]


def validate_baseline(connection, config) -> None:
    """拒绝缺表、多表、列/索引/外键差异或 CHECK 名称不一致的已有库。"""
    if ScriptDirectory.from_config(config).get_current_head() != INITIAL_REVISION:
        raise CommandError("当前代码已有后续迁移，不能用此初始基线脚本接管")
    options = migration_options(config, connection)
    context = MigrationContext.configure(connection, opts=options)
    heads = context.get_current_heads()
    if heads and heads != (INITIAL_REVISION,):
        raise CommandError("数据库已登记其他迁移版本，拒绝覆盖")
    inspector = inspect(connection)
    for metadata in options["target_metadata"]:
        tables = list(metadata.tables.values())
        schema = tables[0].schema or connection.dialect.default_schema_name
        actual = set(inspector.get_table_names(schema=schema)) - {"alembic_version"}
        expected = {table.name for table in tables}
        if actual != expected:
            raise CommandError(
                f"{schema} 表集合不匹配，缺少 {sorted(expected - actual)}，多出 {sorted(actual - expected)}"
            )
        for table in tables:
            expected_checks = {
                constraint.name
                for constraint in table.constraints
                if isinstance(constraint, CheckConstraint)
            }
            actual_checks = {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    table.name, schema=schema
                )
            }
            if expected_checks != actual_checks:
                raise CommandError(f"{schema}.{table.name} CHECK 约束名称不匹配")
    differences = compare_metadata(context, options["target_metadata"])
    if differences:
        raise CommandError(
            f"已有数据库与初始模型有 {len(differences)} 项结构差异；拒绝 stamp，请先检查差异"
        )


def stamp_baseline(connection, config) -> None:
    validate_baseline(connection, config)
    connection.commit()
    config.attributes["connection"] = connection
    try:
        command.stamp(config, INITIAL_REVISION)
    finally:
        config.attributes.pop("connection", None)
    connection.commit()
    print(f"Baseline verified and stamped: {INITIAL_REVISION}.")


async def main() -> int:
    config = Config(str(ROOT / "alembic.ini"))
    try:
        settings = MigrationSettings()
        if settings.migration_database_url is None:
            raise CommandError("请配置 SALESMIND_MIGRATION_DATABASE_URL")
        engine = create_async_engine(
            settings.migration_database_url.get_secret_value(),
            poolclass=NullPool,
            hide_parameters=True,
            connect_args={"connect_timeout": 10},
        )
        try:
            async with engine.connect() as connection:
                await connection.run_sync(stamp_baseline, config)
        finally:
            await engine.dispose()
    except (CommandError, ValidationError) as error:
        print(str(error))
        return 1
    except (SQLAlchemyError, OSError) as error:
        print(f"迁移连接失败：{type(error).__name__}，未登记基线。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
