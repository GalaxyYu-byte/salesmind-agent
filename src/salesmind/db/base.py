"""Separate metadata registries for the two MySQL schemas."""

from sqlalchemy.orm import DeclarativeBase


class SalesDataBase(DeclarativeBase):
    """ORM metadata for the sales_data schema."""


class AgentAppBase(DeclarativeBase):
    """ORM metadata for the agent_app schema."""
