import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError

from salesmind.core.config import Settings
from salesmind.db.migration import INITIAL_REVISION, MigrationSettings, target_metadata

ROOT = Path(__file__).resolve().parents[1]


def configuration():
    config = Config(str(ROOT / "alembic.ini"))
    config.output_buffer = io.StringIO()
    return config


def test_revision_graph_has_one_initial_head():
    script = ScriptDirectory.from_config(configuration())
    assert script.get_heads() == [INITIAL_REVISION]
    assert script.get_revision(INITIAL_REVISION).down_revision is None


def test_offline_upgrade_creates_thirteen_tables_and_defers_cycle():
    config = configuration()
    command.upgrade(config, "head", sql=True)
    sql = config.output_buffer.getvalue()
    assert sql.count("CREATE TABLE sales_data.") == 5
    assert sql.count("CREATE TABLE agent_app.") == 9  # 8 张应用表加版本表。
    assert sql.count("fk_session_active_run") == 1
    assert (
        "ALTER TABLE agent_app.chat_session ADD CONSTRAINT fk_session_active_run" in sql
    )
    assert sql.index("CREATE TABLE agent_app.analysis_run") < sql.index(
        "ADD CONSTRAINT fk_session_active_run"
    )
    assert "ON UPDATE CURRENT_TIMESTAMP(3)" in sql
    assert "CHARACTER SET ascii COLLATE ascii_bin" in sql
    # SQLAlchemy Numeric 编译为 MySQL NUMERIC（DECIMAL 的同义类型）。
    assert "NUMERIC(18,2)" in sql.replace(" ", "")
    assert "COMMENT" in sql
    assert "INSERT INTO" in sql and "alembic_version" in sql


def test_offline_downgrade_removes_cycle_before_tables():
    config = configuration()
    command.downgrade(config, f"{INITIAL_REVISION}:base", sql=True)
    sql = config.output_buffer.getvalue()
    assert sql.count("DROP TABLE sales_data.") == 5
    assert sql.count("DROP TABLE agent_app.") == 8  # 版本表保留，仅清除版本记录。
    assert sql.index("DROP FOREIGN KEY fk_session_active_run") < sql.index(
        "DROP TABLE agent_app.analysis_run"
    )
    assert "DROP DATABASE" not in sql


def test_comparison_metadata_normalizes_default_schema_foreign_keys():
    targets = target_metadata(configuration(), "agent_app")
    app_metadata = targets[1]
    assert all(table.schema is None for table in app_metadata.tables.values())
    # 访问 sorted_tables 会解析全部外键；默认 schema 转换不能留下旧 schema 引用。
    assert len(app_metadata.sorted_tables) == 8
    assert all(
        fk.column.table.schema is None
        for table in app_metadata.tables.values()
        for fk in table.foreign_keys
    )


def test_management_credentials_do_not_enter_runtime_settings(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "SALESMIND_MIGRATION_DATABASE_URL=mysql+asyncmy://root:management-secret@localhost/agent_app\n",
        encoding="utf-8",
    )
    runtime = Settings(_env_file=dotenv)
    migration = MigrationSettings(_env_file=dotenv)
    assert "management-secret" not in repr(runtime)
    assert "migration_database_url" not in runtime.model_dump()
    assert migration.migration_database_url is not None
    assert "management-secret" not in repr(migration)


@pytest.mark.parametrize(
    "url",
    [
        "mysql+pymysql://root:hidden-password@localhost/agent_app",
        "mysql+asyncmy://root:hidden-password@localhost/sales_data",
        "mysql+asyncmy://root:hidden-password@localhost:65536/agent_app",
        "invalid-url-with-hidden-password",
    ],
)
def test_invalid_management_configuration_hides_password(url):
    with pytest.raises(ValidationError) as error:
        MigrationSettings(_env_file=None, migration_database_url=url)
    assert "hidden-password" not in str(error.value)
