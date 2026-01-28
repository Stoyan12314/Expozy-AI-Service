# api/orchestrator/db/models/enums.py
from enum import Enum

from sqlalchemy.dialects.postgresql import ENUM


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AttemptOutcome(str, Enum):
    SUCCESS = "success"
    FAIL = "fail"


# Keep create_type=False if Alembic migrations already create these enum types.
job_status_enum = ENUM(
    "queued", "running", "completed", "failed",
    name="job_status",
    create_type=False,
)

attempt_outcome_enum = ENUM(
    "success", "fail",
    name="attempt_outcome",
    create_type=False,
)
