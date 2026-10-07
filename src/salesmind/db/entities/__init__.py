"""SQLAlchemy entities matching the reviewed MySQL DDL."""

from salesmind.db.base import AgentAppBase, SalesDataBase
from salesmind.db.entities.agent_app import (
    AnalysisRun,
    AppUser,
    ChatMessage,
    ChatSession,
    ResultArtifact,
    RunEvent,
    ToolExecution,
    UserRegionScope,
)
from salesmind.db.entities.sales_data import (
    DatasetVersion,
    Product,
    SalesOrder,
    SalesRegion,
    SalesRep,
)
from salesmind.db.entities.schema_comments import apply_column_comments

apply_column_comments(SalesDataBase.metadata, AgentAppBase.metadata)

__all__ = [
    "AnalysisRun",
    "AppUser",
    "ChatMessage",
    "ChatSession",
    "DatasetVersion",
    "Product",
    "ResultArtifact",
    "RunEvent",
    "SalesOrder",
    "SalesRegion",
    "SalesRep",
    "ToolExecution",
    "UserRegionScope",
]
