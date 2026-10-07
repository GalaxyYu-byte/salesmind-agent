"""显式启用的 MySQL 8.4 集成测试，只创建/删除本次生成的临时 schema。"""

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from salesmind.db.migration import MigrationSettings, migration_options
from scripts.baseline_database import stamp_baseline, validate_baseline

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("SALESMIND_RUN_MYSQL_MIGRATION_TESTS") != "1",
        reason="需要显式启用 MySQL 临时 schema 测试",
    ),
]


async def test_mysql_upgrade_drift_guard_downgrade_and_restamp():
    config = Config(str(ROOT / "alembic.ini"))
    # 此测试专门验证初始快照；避免后续未做 schema 映射的 revision 触碰真实库。
    assert ScriptDirectory.from_config(config).get_current_head() == "20260930_0001"
    settings = MigrationSettings()
    if settings.migration_database_url is None:
        pytest.fail("MySQL 迁移集成测试需要管理 DSN")
    url = make_url(settings.migration_database_url.get_secret_value())
    suffix = uuid.uuid4().hex[:12]
    schemas = {
        "sales_data": f"sm_mig_sales_{suffix}",
        "agent_app": f"sm_mig_app_{suffix}",
    }
    admin = create_async_engine(url, hide_parameters=True)
    created = []
    engine = None
    try:
        async with admin.connect() as connection:
            for schema in schemas.values():
                assert schema.startswith("sm_mig_") and schema.endswith(suffix)
                exists = await connection.scalar(
                    text(
                        "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=:name"
                    ),
                    {"name": schema},
                )
                assert exists is None
                await connection.execute(
                    text(
                        f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                    )
                )
                created.append(schema)
            await connection.commit()
        engine = create_async_engine(
            url.set(database=schemas["agent_app"]), hide_parameters=True
        )
        config.attributes["schema_map"] = schemas
        async with engine.connect() as connection:

            def execute(sync, operation, *args):
                config.attributes["connection"] = sync
                operation(config, *args)

            await connection.run_sync(execute, command.upgrade, "head")
            await connection.commit()

            def validate(sync):
                validate_baseline(sync, config)
                options = migration_options(config, sync)
                assert not compare_metadata(
                    MigrationContext.configure(sync, opts=options),
                    options["target_metadata"],
                )
                assert "fk_session_active_run" in {
                    fk["name"]
                    for fk in inspect(sync).get_foreign_keys(
                        "chat_session", schema=schemas["agent_app"]
                    )
                }

            await connection.run_sync(validate)
            await connection.commit()

            # 真实修改临时表的默认值，基线登记必须拒绝结构漂移。
            await connection.execute(
                text(
                    f"ALTER TABLE `{schemas['sales_data']}`.sales_region ALTER COLUMN active SET DEFAULT 0"
                )
            )
            with pytest.raises(CommandError, match="结构差异"):
                await connection.run_sync(validate_baseline, config)
            await connection.execute(
                text(
                    f"ALTER TABLE `{schemas['sales_data']}`.sales_region ALTER COLUMN active SET DEFAULT 1"
                )
            )
            await connection.commit()

            await connection.execute(
                text(
                    f"ALTER TABLE `{schemas['sales_data']}`.sales_order DROP CHECK ck_order_quantity"
                )
            )
            with pytest.raises(CommandError, match="CHECK"):
                await connection.run_sync(validate_baseline, config)
            await connection.execute(
                text(
                    f"ALTER TABLE `{schemas['sales_data']}`.sales_order ADD CONSTRAINT ck_order_quantity CHECK (quantity > 0)"
                )
            )
            await connection.commit()

            # 模拟已建表但尚未纳管的数据库，stamp 不改变业务/应用表。
            await connection.execute(
                text(f"DROP TABLE `{schemas['agent_app']}`.alembic_version")
            )
            await connection.commit()
            await connection.run_sync(stamp_baseline, config)
            await connection.commit()
            assert (
                await connection.scalar(
                    text(
                        f"SELECT version_num FROM `{schemas['agent_app']}`.alembic_version"
                    )
                )
                == "20260930_0001"
            )
            await connection.commit()
            await connection.run_sync(execute, command.check)
            await connection.commit()

            await connection.run_sync(execute, command.downgrade, "base")
            await connection.commit()

            def verify_empty(sync):
                inspector = inspect(sync)
                assert not inspector.get_table_names(schema=schemas["sales_data"])
                assert set(inspector.get_table_names(schema=schemas["agent_app"])) <= {
                    "alembic_version"
                }

            await connection.run_sync(verify_empty)
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin.connect() as connection:
            for schema in reversed(created):
                assert (
                    schema in schemas.values()
                    and schema.startswith("sm_mig_")
                    and schema.endswith(suffix)
                )
                await connection.execute(text(f"DROP DATABASE `{schema}`"))
            await connection.commit()
        await admin.dispose()
