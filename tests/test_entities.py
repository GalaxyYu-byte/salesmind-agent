import re
from pathlib import Path

from sqlalchemy.orm import configure_mappers

from salesmind.db.base import AgentAppBase, SalesDataBase
from salesmind.db.entities import (
    AnalysisRun,
    AppUser,
    ChatMessage,
    ChatSession,
    DatasetVersion,
    Product,
    ResultArtifact,
    RunEvent,
    SalesOrder,
    SalesRegion,
    SalesRep,
    ToolExecution,
    UserRegionScope,
)


def test_all_ddl_tables_are_mapped_once_in_their_schema():
    configure_mappers()

    assert set(SalesDataBase.metadata.tables) == {
        "sales_data.sales_region",
        "sales_data.sales_rep",
        "sales_data.product",
        "sales_data.sales_order",
        "sales_data.dataset_version",
    }
    assert set(AgentAppBase.metadata.tables) == {
        "agent_app.app_user",
        "agent_app.user_region_scope",
        "agent_app.chat_session",
        "agent_app.analysis_run",
        "agent_app.chat_message",
        "agent_app.run_event",
        "agent_app.result_artifact",
        "agent_app.tool_execution",
    }


def test_entity_column_counts_match_the_reviewed_ddl():
    expected = {
        SalesRegion: 6,
        SalesRep: 6,
        Product: 7,
        SalesOrder: 14,
        DatasetVersion: 5,
        AppUser: 11,
        UserRegionScope: 3,
        ChatSession: 10,
        AnalysisRun: 30,
        ChatMessage: 10,
        RunEvent: 6,
        ResultArtifact: 17,
        ToolExecution: 13,
    }

    assert {entity: len(entity.__table__.columns) for entity in expected} == expected


def test_entity_column_names_match_the_reviewed_sql_files():
    root = Path(__file__).resolve().parents[1]
    mapped = {
        **{
            table.name: set(table.columns.keys())
            for table in SalesDataBase.metadata.tables.values()
        },
        **{
            table.name: set(table.columns.keys())
            for table in AgentAppBase.metadata.tables.values()
        },
    }

    for filename in ("001_sales_data.sql", "002_agent_app.sql"):
        sql = (root / "docs" / "sql" / filename).read_text(encoding="utf-8")
        for match in re.finditer(
            r"CREATE TABLE (\w+) \((.*?)\n\) ENGINE=", sql, re.DOTALL
        ):
            table_name, definition = match.groups()
            ddl_columns = set()
            for line in definition.splitlines():
                column_match = re.match(r"\s{4}([a-z][a-z0-9_]*)\s+", line)
                if column_match:
                    ddl_columns.add(column_match.group(1))
            assert mapped[table_name] == ddl_columns, table_name


def test_cross_schema_links_remain_logical_and_money_stays_decimal():
    assert not list(AppUser.__table__.c.rep_id.foreign_keys)
    assert not list(UserRegionScope.__table__.c.region_id.foreign_keys)
    assert not list(AnalysisRun.__table__.c.data_version.foreign_keys)
    assert SalesOrder.__table__.c.amount.type.precision == 18
    assert SalesOrder.__table__.c.amount.type.scale == 2
    assert SalesOrder.__table__.c.quantity.type.unsigned is True
    assert {constraint.name for constraint in ChatSession.__table__.constraints} >= {
        "fk_session_owner",
        "fk_session_active_run",
    }
