"""FastAPI application — REST API for the Exam Scheduling System.

Endpoints
---------
Schedule:
    POST /api/schedule/solve           → trigger OR-Tools solver
    GET  /api/schedule/result/{id}     → poll solver result
    POST /api/schedule/validate-move   → pre-flight drag validation

AI:
    POST /api/ai/extract-external-schedule → Gemini PDF parser
    POST /api/ai/nlp-constraint            → Gemini NLP parser

Data CRUD:
    GET/POST courses, students, rooms, enrollments
"""

from __future__ import annotations

import logging
import math
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory, get_db, init_db
from app.models import Course, Enrollment, ExamEvent, OwnershipDomain, Room, Student
from app.schemas import (
    CourseCreate,
    CourseRead,
    EnrollmentCreate,
    EnrollmentRead,
    ExamEventRead,
    RoomCreate,
    RoomRead,
    ScheduleRequest,
    ScheduleResult,
    StudentCreate,
    StudentRead,
)
from app.solver import (
    CourseData,
    EnrollmentData,
    RoomData,
    ScheduleOptimizer,
    StudentData,
    _dt_to_minutes,
    _normalize_tz,
)

logger = logging.getLogger(__name__)


# ── In-memory solver result store (upgrade to Redis in prod) ──

_solver_results: dict[str, dict[str, Any]] = {}


# ── Lifespan ──────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB tables on startup."""
    await init_db()
    logger.info("Database tables initialized.")
    yield


# ── App Factory ───────────────────────────────────────────────

app = FastAPI(
    title="Faculty Exam Scheduler",
    description="AI-powered exam scheduling with OR-Tools constraint optimization",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════
#  REQUEST / RESPONSE SCHEMAS (API-specific)
# ══════════════════════════════════════════════════════════════

class ValidateMoveRequest(BaseModel):
    course_id: int
    new_start_time: datetime
    new_room_id: int


class ValidateMoveResponse(BaseModel):
    is_valid: bool
    conflict_reason: str = ""


class SolveResponse(BaseModel):
    task_id: str
    message: str = "Solver started. Poll /api/schedule/result/{task_id} for results."


class SolverResultResponse(BaseModel):
    status: str  # PENDING | OPTIMAL | FEASIBLE | INFEASIBLE | ERROR
    events: list[dict[str, Any]] = []
    solve_time_seconds: float = 0.0
    infeasibility_details: dict[str, Any] | None = None
    infeasibility_explanation: dict[str, Any] | None = None


class NLPConstraintRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=1000)


# ══════════════════════════════════════════════════════════════
#  SCHEDULE ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.post("/api/schedule/solve", response_model=SolveResponse)
async def solve_schedule(
    request: ScheduleRequest,
    background_tasks: BackgroundTasks,
):
    """Trigger the OR-Tools solver asynchronously."""
    task_id = str(uuid.uuid4())
    _solver_results[task_id] = {"status": "PENDING"}

    background_tasks.add_task(
        _run_solver, task_id, request.exam_period_start, request.exam_period_end
    )

    return SolveResponse(task_id=task_id)


@app.get("/api/schedule/result/{task_id}", response_model=SolverResultResponse)
async def get_solver_result(task_id: str):
    """Poll for the solver result."""
    if task_id not in _solver_results:
        raise HTTPException(status_code=404, detail="Task not found.")
    return SolverResultResponse(**_solver_results[task_id])


@app.post("/api/schedule/validate-move", response_model=ValidateMoveResponse)
async def validate_move(
    req: ValidateMoveRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Pre-flight validation for drag-and-drop.

    Checks if moving an internal exam to a new time/room would violate:
    1. External blackout + 45-min transit buffer for any enrolled student
    2. Overlap with another scheduled internal exam for any enrolled student
    3. Room capacity
    """
    # Load the course
    course = await db.get(Course, req.course_id)
    if not course:
        raise HTTPException(404, "Course not found.")
    if course.ownership_domain != OwnershipDomain.INTERNAL:
        raise HTTPException(400, "Only INTERNAL courses can be moved.")
    if course.duration_minutes is None:
        raise HTTPException(400, "Course has no duration.")

    # Load the target room
    room = await db.get(Room, req.new_room_id)
    if not room:
        raise HTTPException(404, "Room not found.")

    # Get students enrolled in this course
    enrollments_q = await db.execute(
        select(Enrollment.student_id).where(Enrollment.course_id == req.course_id)
    )
    student_ids = [row[0] for row in enrollments_q.all()]

    # ── HC3: Room capacity check ──
    if len(student_ids) > room.exam_capacity:
        return ValidateMoveResponse(
            is_valid=False,
            conflict_reason=(
                f"Room '{room.name}' has capacity {room.exam_capacity}, "
                f"but {len(student_ids)} students are enrolled."
            ),
        )

    buffer = settings.TRANSIT_BUFFER_MINUTES
    new_start = _normalize_tz(req.new_start_time)
    new_end = new_start + timedelta(minutes=course.duration_minutes)

    # ── HC1: Check external blackout overlap for each student ──
    for sid in student_ids:
        # Get student's accommodations
        student = await db.get(Student, sid)
        mult = student.accommodations_multiplier if student else 1.0
        adj_dur = math.ceil(course.duration_minutes * mult)
        adj_end = new_start + timedelta(minutes=adj_dur)

        # Get external courses this student is enrolled in
        ext_q = await db.execute(
            select(Course)
            .join(Enrollment, Enrollment.course_id == Course.id)
            .where(
                Enrollment.student_id == sid,
                Course.ownership_domain == OwnershipDomain.EXTERNAL,
            )
        )
        ext_courses = ext_q.scalars().all()

        for ec in ext_courses:
            if ec.fixed_start_time is None or ec.fixed_end_time is None:
                continue
            buf_start = _normalize_tz(ec.fixed_start_time) - timedelta(minutes=buffer)
            buf_end = _normalize_tz(ec.fixed_end_time) + timedelta(minutes=buffer)

            if new_start < buf_end and adj_end > buf_start:
                student_name = student.name if student else f"ID {sid}"
                return ValidateMoveResponse(
                    is_valid=False,
                    conflict_reason=(
                        f"Student '{student_name}' has external exam {ec.code} "
                        f"({ec.fixed_start_time} - {ec.fixed_end_time}) with "
                        f"{buffer}-min transit buffer. The proposed time overlaps "
                        f"this blackout window."
                    ),
                )

        # ── HC2: Check overlap with other scheduled internal exams ──
        other_exams_q = await db.execute(
            select(ExamEvent)
            .join(Course, ExamEvent.course_id == Course.id)
            .join(Enrollment, Enrollment.course_id == Course.id)
            .where(
                Enrollment.student_id == sid,
                Course.ownership_domain == OwnershipDomain.INTERNAL,
                Course.id != req.course_id,
            )
        )
        other_exams = other_exams_q.scalars().all()

        for oe in other_exams:
            oe_start = _normalize_tz(oe.start_time)
            oe_end = _normalize_tz(oe.end_time)

            # Adjust for student accommodations on the other exam too
            other_course = await db.get(Course, oe.course_id)
            if other_course and other_course.duration_minutes and mult > 1.0:
                oe_adj_dur = math.ceil(other_course.duration_minutes * mult)
                oe_end = oe_start + timedelta(minutes=oe_adj_dur)

            if new_start < oe_end and adj_end > oe_start:
                student_name = student.name if student else f"ID {sid}"
                return ValidateMoveResponse(
                    is_valid=False,
                    conflict_reason=(
                        f"Student '{student_name}' already has {other_course.code if other_course else 'an exam'} "
                        f"scheduled at {oe.start_time} - {oe.end_time}. "
                        f"The proposed time would create an overlap."
                    ),
                )

    return ValidateMoveResponse(is_valid=True)


# ══════════════════════════════════════════════════════════════
#  AI ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.post("/api/ai/extract-external-schedule")
async def extract_external_schedule(
    file: UploadFile = File(...),
    course_codes: str = Form(..., description="Comma-separated external course codes"),
):
    """Upload a PDF/image of the university schedule → extract blackout windows."""
    from app.ai_services import parse_external_schedule

    if not settings.GEMINI_API_KEY:
        raise HTTPException(503, "Gemini API key not configured.")

    file_bytes = await file.read()
    mime = file.content_type or "application/pdf"
    codes = [c.strip() for c in course_codes.split(",") if c.strip()]

    if not codes:
        raise HTTPException(400, "No course codes provided.")

    try:
        entries = await parse_external_schedule(file_bytes, mime, codes)
        return {"entries": entries}
    except Exception as e:
        logger.exception("AI extraction failed")
        raise HTTPException(500, f"AI extraction failed: {str(e)}")


@app.post("/api/ai/nlp-constraint")
async def nlp_constraint(req: NLPConstraintRequest):
    """Parse a natural language scheduling request into structured constraint."""
    from app.ai_services import parse_natural_language_constraint

    if not settings.GEMINI_API_KEY:
        raise HTTPException(503, "Gemini API key not configured.")

    try:
        result = await parse_natural_language_constraint(req.text)
        return result
    except Exception as e:
        logger.exception("NLP constraint parsing failed")
        raise HTTPException(500, f"NLP parsing failed: {str(e)}")


# ══════════════════════════════════════════════════════════════
#  DATA CRUD ENDPOINTS
# ══════════════════════════════════════════════════════════════

# ── Courses ──

@app.get("/api/courses", response_model=list[CourseRead])
async def list_courses(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Course).order_by(Course.code))
    return result.scalars().all()


@app.post("/api/courses", response_model=CourseRead, status_code=201)
async def create_course(data: CourseCreate, db: AsyncSession = Depends(get_db)):
    course = Course(**data.model_dump())
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return course


# ── Students ──

@app.get("/api/students", response_model=list[StudentRead])
async def list_students(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Student).order_by(Student.student_number))
    return result.scalars().all()


@app.post("/api/students", response_model=StudentRead, status_code=201)
async def create_student(data: StudentCreate, db: AsyncSession = Depends(get_db)):
    student = Student(**data.model_dump())
    db.add(student)
    await db.commit()
    await db.refresh(student)
    return student


# ── Rooms ──

@app.get("/api/rooms", response_model=list[RoomRead])
async def list_rooms(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Room).order_by(Room.name))
    return result.scalars().all()


@app.post("/api/rooms", response_model=RoomRead, status_code=201)
async def create_room(data: RoomCreate, db: AsyncSession = Depends(get_db)):
    room = Room(**data.model_dump())
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return room


# ── Enrollments ──

@app.get("/api/enrollments", response_model=list[EnrollmentRead])
async def list_enrollments(
    student_id: int | None = Query(None),
    course_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(Enrollment)
    if student_id is not None:
        q = q.where(Enrollment.student_id == student_id)
    if course_id is not None:
        q = q.where(Enrollment.course_id == course_id)
    result = await db.execute(q)
    return result.scalars().all()


@app.post("/api/enrollments", response_model=EnrollmentRead, status_code=201)
async def create_enrollment(data: EnrollmentCreate, db: AsyncSession = Depends(get_db)):
    enrollment = Enrollment(**data.model_dump())
    db.add(enrollment)
    await db.commit()
    await db.refresh(enrollment)
    return enrollment


# ── Exam Events (read-only — created by solver) ──

@app.get("/api/exam-events", response_model=list[ExamEventRead])
async def list_exam_events(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ExamEvent).order_by(ExamEvent.start_time)
    )
    return result.scalars().all()


# ══════════════════════════════════════════════════════════════
#  BACKGROUND SOLVER TASK
# ══════════════════════════════════════════════════════════════

async def _run_solver(
    task_id: str,
    period_start: datetime,
    period_end: datetime,
) -> None:
    """Execute the OR-Tools solver in a background task."""
    try:
        async with async_session_factory() as session:
            # Load all data
            courses_orm = (await session.execute(select(Course))).scalars().all()
            students_orm = (await session.execute(select(Student))).scalars().all()
            rooms_orm = (await session.execute(select(Room))).scalars().all()
            enrollments_orm = (await session.execute(select(Enrollment))).scalars().all()

        # Convert to solver data objects
        internal_courses = [
            CourseData(
                id=c.id, code=c.code, is_internal=True,
                duration_minutes=c.duration_minutes,
            )
            for c in courses_orm
            if c.ownership_domain == OwnershipDomain.INTERNAL
        ]
        external_courses = [
            CourseData(
                id=c.id, code=c.code, is_internal=False,
                fixed_start=c.fixed_start_time, fixed_end=c.fixed_end_time,
            )
            for c in courses_orm
            if c.ownership_domain == OwnershipDomain.EXTERNAL
        ]
        students = [
            StudentData(id=s.id, name=s.name, accommodations_multiplier=s.accommodations_multiplier)
            for s in students_orm
        ]
        rooms = [RoomData(id=r.id, name=r.name, exam_capacity=r.exam_capacity) for r in rooms_orm]
        enrollments = [
            EnrollmentData(student_id=e.student_id, course_id=e.course_id)
            for e in enrollments_orm
        ]

        # Run solver
        optimizer = ScheduleOptimizer(
            internal_courses=internal_courses,
            external_courses=external_courses,
            students=students,
            enrollments=enrollments,
            rooms=rooms,
            period_start=_normalize_tz(period_start),
            period_end=_normalize_tz(period_end),
        )
        result = optimizer.solve(time_limit_seconds=120)

        # Store result
        result_dict: dict[str, Any] = {
            "status": result.status,
            "events": result.events,
            "solve_time_seconds": result.solve_time_seconds,
            "infeasibility_details": result.infeasibility_details,
            "infeasibility_explanation": None,
        }

        # If infeasible, try to get AI explanation (best-effort)
        if result.status == "INFEASIBLE" and result.infeasibility_details and settings.GEMINI_API_KEY:
            try:
                from app.ai_services import explain_infeasibility
                explanation = await explain_infeasibility(result.infeasibility_details)
                result_dict["infeasibility_explanation"] = explanation
            except Exception:
                logger.exception("AI infeasibility explanation failed (non-critical)")

        # Persist exam events to DB
        if result.status in ("OPTIMAL", "FEASIBLE"):
            async with async_session_factory() as session:
                async with session.begin():
                    # Clear previous events
                    from sqlalchemy import delete
                    await session.execute(delete(ExamEvent))
                    # Insert new events
                    for ev in result.events:
                        exam_event = ExamEvent(
                            course_id=ev["course_id"],
                            room_id=ev["room_id"],
                            start_time=ev["start_time"],
                            end_time=ev["end_time"],
                        )
                        session.add(exam_event)

        _solver_results[task_id] = result_dict

    except Exception as e:
        logger.exception("Solver background task failed")
        _solver_results[task_id] = {
            "status": "ERROR",
            "events": [],
            "solve_time_seconds": 0.0,
            "infeasibility_details": {"error": str(e)},
            "infeasibility_explanation": None,
        }
