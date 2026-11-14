"""Declarative base. Table models are added from M1 (docs/DATA_MODEL.md)."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Parent of every mapped table; Alembic reads its metadata."""
