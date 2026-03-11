"""SQLAlchemy async ORM models for the Faculty-Scale Exam Scheduling System.

Tables
------
- Course        INTERNAL (we schedule) or EXTERNAL (fixed blackout)
- Student       with optional accommodations multiplier
- Enrollment    many-to-many nexus (student ↔ course)  — captures BOTH domains
- Room          faculty-owned exam rooms only
- ExamEvent     a solved / scheduled exam placement
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


# ── Enums ─────────────────────────────────────────────────────

class OwnershipDomain(str, enum.Enum):
    """Whether a course is owned by our faculty or an external one."""
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


# ── Base ──────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """Shared declarative base for all models."""
    pass


# ── Course ────────────────────────────────────────────────────

class Course(Base):
    """
    A university course whose exam must be scheduled.

    * INTERNAL courses: we control their time/room.
      → `duration_minutes` is required.
    * EXTERNAL courses: time is fixed externally.
      → `fixed_start_time` and `fixed_end_time` are required.
    """

    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    ownership_domain: Mapped[OwnershipDomain] = mapped_column(
        Enum(OwnershipDomain), nullable=False, index=True,
    )

    # ── INTERNAL-only fields ──
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── EXTERNAL-only fields ──
    fixed_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fixed_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationships ──
    enrollments: Mapped[list[Enrollment]] = relationship(back_populates="course", cascade="all, delete-orphan")
    exam_events: Mapped[list[ExamEvent]] = relationship(back_populates="course", cascade="all, delete-orphan")

    __table_args__ = (
        # INTERNAL courses MUST have a duration
        CheckConstraint(
            "(ownership_domain != 'INTERNAL') OR (duration_minutes IS NOT NULL)",
            name="ck_internal_has_duration",
        ),
        # EXTERNAL courses MUST have fixed start & end
        CheckConstraint(
            "(ownership_domain != 'EXTERNAL') OR "
            "(fixed_start_time IS NOT NULL AND fixed_end_time IS NOT NULL)",
            name="ck_external_has_fixed_times",
        ),
    )

    def __repr__(self) -> str:
        return f"<Course {self.code} ({self.ownership_domain.value})>"


# ── Student ───────────────────────────────────────────────────

class Student(Base):
    """A student who may be enrolled in both internal and external courses."""

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    accommodations_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # ── Relationships ──
    enrollments: Mapped[list[Enrollment]] = relationship(back_populates="student", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Student {self.student_number}: {self.name}>"


# ── Enrollment (The Nexus) ────────────────────────────────────

class Enrollment(Base):
    """
    Many-to-many link between students and courses.

    Captures BOTH internal and external enrollments so the conflict
    matrix can be computed correctly by the OR-Tools solver.
    """

    __tablename__ = "enrollments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), nullable=False, index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)

    # ── Relationships ──
    student: Mapped[Student] = relationship(back_populates="enrollments")
    course: Mapped[Course] = relationship(back_populates="enrollments")

    __table_args__ = (
        UniqueConstraint("student_id", "course_id", name="uq_student_course"),
    )

    def __repr__(self) -> str:
        return f"<Enrollment student={self.student_id} course={self.course_id}>"


# ── Room ──────────────────────────────────────────────────────

class Room(Base):
    """A faculty-owned room available for scheduling internal exams."""

    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    exam_capacity: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Relationships ──
    exam_events: Mapped[list[ExamEvent]] = relationship(back_populates="room", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Room {self.name} (cap={self.exam_capacity})>"


# ── ExamEvent (Solved Placement) ──────────────────────────────

class ExamEvent(Base):
    """
    A scheduled exam event — the output of the OR-Tools solver.

    Links a Course to a Room at a specific time window.
    """

    __tablename__ = "exam_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # ── Relationships ──
    course: Mapped[Course] = relationship(back_populates="exam_events")
    room: Mapped[Room] = relationship(back_populates="exam_events")

    __table_args__ = (
        CheckConstraint("end_time > start_time", name="ck_event_end_after_start"),
    )

    def __repr__(self) -> str:
        return f"<ExamEvent course={self.course_id} room={self.room_id} {self.start_time}>"
