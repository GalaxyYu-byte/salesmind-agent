"""SQLAlchemy entities matching the reviewed MySQL DDL."""

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
