# api/orchestrator/db/models/base.py
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    # map dict[str, Any] annotations to JSONB automatically
    type_annotation_map = {
        dict[str, Any]: JSONB,
    }
