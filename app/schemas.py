"""Pydantic v2 schemas for request validation and response serialization.

Naming convention:
- *Create  → used for POST request bodies (write)
- *Read    → used for GET response bodies (read)
- *Update  → used for PATCH request bodies (partial update)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── Enums ─────────────────────────────────────────────────────

class OwnershipDomain(str, Enum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


# ── Course ────────────────────────────────────────────────────

class CourseBase(BaseModel):
    code: str = Field(..., max_length=20, examples=["BIO401"])
    name: str = Field(..., max_length=200, examples=["Genetics 401"])
    ownership_domain: OwnershipDomain

    # INTERNAL-only
    duration_minutes: int | None = Field(
        None, gt=0, le=480,
        description="Exam duration in minutes (required for INTERNAL courses).",
    )

    # EXTERNAL-only
    fixed_start_time: datetime | None = Field(
        None,
        description="Fixed exam start (required for EXTERNAL courses).",
    )
    fixed_end_time: datetime | None = Field(
        None,
        description="Fixed exam end (required for EXTERNAL courses).",
    )

    @model_validator(mode="after")
    def _validate_domain_fields(self) -> "CourseBase":
        if self.ownership_domain == OwnershipDomain.INTERNAL:
            if self.duration_minutes is None:
                raise ValueError("INTERNAL courses must specify duration_minutes.")
            if self.fixed_start_time or self.fixed_end_time:
                raise ValueError(
                    "INTERNAL courses must not have fixed_start_time / fixed_end_time."
                )
        else:  # EXTERNAL
            if self.fixed_start_time is None or self.fixed_end_time is None:
                raise ValueError(
                    "EXTERNAL courses must specify both fixed_start_time and fixed_end_time."
                )
            if self.fixed_end_time <= self.fixed_start_time:
                raise ValueError("fixed_end_time must be after fixed_start_time.")
            if self.duration_minutes is not None:
                raise ValueError(
                    "EXTERNAL courses must not have duration_minutes."
                )
        return self


class CourseCreate(CourseBase):
    pass


class CourseRead(CourseBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class CourseUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    duration_minutes: int | None = Field(None, gt=0, le=480)
    fixed_start_time: datetime | None = None
    fixed_end_time: datetime | None = None


# ── Student ───────────────────────────────────────────────────

class StudentBase(BaseModel):
    student_number: str = Field(..., max_length=20, examples=["2024001"])
    name: str = Field(..., max_length=200, examples=["Alice Johnson"])
    accommodations_multiplier: float = Field(
        1.0, ge=1.0, le=3.0,
        description="Time multiplier for exam accommodations (1.0 = standard).",
    )


class StudentCreate(StudentBase):
    pass


class StudentRead(StudentBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class StudentUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    accommodations_multiplier: float | None = Field(None, ge=1.0, le=3.0)


# ── Enrollment ────────────────────────────────────────────────

class EnrollmentCreate(BaseModel):
    student_id: int
    course_id: int


class EnrollmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    student_id: int
    course_id: int


# ── Room ──────────────────────────────────────────────────────

class RoomBase(BaseModel):
    name: str = Field(..., max_length=100, examples=["Biotech Auditorium A"])
    exam_capacity: int = Field(..., gt=0, le=1000)


class RoomCreate(RoomBase):
    pass


class RoomRead(RoomBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class RoomUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    exam_capacity: int | None = Field(None, gt=0, le=1000)


# ── ExamEvent ─────────────────────────────────────────────────

class ExamEventBase(BaseModel):
    course_id: int
    room_id: int
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def _validate_time_window(self) -> "ExamEventBase":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time.")
        return self


class ExamEventCreate(ExamEventBase):
    pass


class ExamEventRead(ExamEventBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ExamEventUpdate(BaseModel):
    room_id: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


# ── Solver I/O Schemas (used by OR-Tools in Step 2) ──────────

class ExternalBlackout(BaseModel):
    """Represents an external exam window + transit buffer for the solver."""
    course_code: str
    student_ids: list[int]
    buffered_start: datetime  # fixed_start - 45 min
    buffered_end: datetime    # fixed_end   + 45 min


class ScheduleRequest(BaseModel):
    """Input payload for the scheduling solver."""
    exam_period_start: datetime
    exam_period_end: datetime
    slot_duration_minutes: int = Field(30, description="Granularity of time slots.")


class ScheduleResult(BaseModel):
    """Output from the solver."""
    status: str = Field(..., examples=["OPTIMAL", "FEASIBLE", "INFEASIBLE"])
    events: list[ExamEventCreate] = []
    solve_time_seconds: float = 0.0
    infeasibility_report: str | None = None
