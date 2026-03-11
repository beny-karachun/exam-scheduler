"""Test the OR-Tools solver against the mock data from Step 1.

Loads data from exam_scheduler_dev.db, runs the ScheduleOptimizer,
validates all hard constraints, and prints a detailed report.

Usage:
    python -m scripts.test_solver
"""

from __future__ import annotations

import asyncio
import math
import sys
import io
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# Force UTF-8 on Windows console
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session_factory
from app.models import Course, Enrollment, OwnershipDomain, Room, Student
from app.solver import (
    CourseData,
    EnrollmentData,
    RoomData,
    ScheduleOptimizer,
    StudentData,
    _dt_to_minutes,
)
from app.config import settings


# -- Exam period (must match generate_mock_data.py) ----------------
EXAM_PERIOD_START = datetime(2026, 3, 16, 8, 0, tzinfo=timezone.utc)
EXAM_PERIOD_END = datetime(2026, 3, 21, 20, 0, tzinfo=timezone.utc)  # Sat evening


# -- Data Loading --------------------------------------------------

async def load_data():
    """Load all entities from the database into plain data objects."""
    async with async_session_factory() as session:
        # Courses
        result = await session.execute(select(Course))
        courses_orm = result.scalars().all()

        internal_courses: list[CourseData] = []
        external_courses: list[CourseData] = []

        for c in courses_orm:
            cd = CourseData(
                id=c.id,
                code=c.code,
                is_internal=(c.ownership_domain == OwnershipDomain.INTERNAL),
                duration_minutes=c.duration_minutes,
                fixed_start=c.fixed_start_time,
                fixed_end=c.fixed_end_time,
            )
            if cd.is_internal:
                internal_courses.append(cd)
            else:
                external_courses.append(cd)

        # Students
        result = await session.execute(select(Student))
        students_orm = result.scalars().all()
        students = [
            StudentData(id=s.id, name=s.name, accommodations_multiplier=s.accommodations_multiplier)
            for s in students_orm
        ]

        # Rooms
        result = await session.execute(select(Room))
        rooms_orm = result.scalars().all()
        rooms = [RoomData(id=r.id, name=r.name, exam_capacity=r.exam_capacity) for r in rooms_orm]

        # Enrollments
        result = await session.execute(select(Enrollment))
        enrollments_orm = result.scalars().all()
        enrollments = [
            EnrollmentData(student_id=e.student_id, course_id=e.course_id)
            for e in enrollments_orm
        ]

    return internal_courses, external_courses, students, enrollments, rooms


# -- Constraint Validators -----------------------------------------

def validate_hc1(events, external_courses, enrollments, students_map):
    """
    HC1: No internal exam overlaps a student's external blackout
    (including 45-min transit buffer).
    """
    buffer = settings.TRANSIT_BUFFER_MINUTES
    violations = []

    # Build student -> external blackouts
    ext_map = {c.id: c for c in external_courses}
    student_ext: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for e in enrollments:
        if e.course_id in ext_map:
            ec = ext_map[e.course_id]
            buf_start = _dt_to_minutes(ec.fixed_start, EXAM_PERIOD_START) - buffer
            buf_end = _dt_to_minutes(ec.fixed_end, EXAM_PERIOD_START) + buffer
            student_ext[e.student_id].append((buf_start, buf_end))

    # Build student -> internal course IDs
    int_ids = {ev["course_id"] for ev in events}
    student_int_courses: dict[int, list[int]] = defaultdict(list)
    for e in enrollments:
        if e.course_id in int_ids:
            student_int_courses[e.student_id].append(e.course_id)

    # Event lookup
    event_map = {ev["course_id"]: ev for ev in events}

    for sid, ext_blackouts in student_ext.items():
        for cid in student_int_courses.get(sid, []):
            ev = event_map[cid]
            ev_start = ev["start_minutes"]
            ev_end = ev["end_minutes"]

            # Check accommodations
            student = students_map.get(sid)
            if student and student.accommodations_multiplier > 1.0:
                adj_dur = math.ceil(ev["duration_minutes"] * student.accommodations_multiplier)
                ev_end = ev_start + adj_dur

            for buf_s, buf_e in ext_blackouts:
                if ev_start < buf_e and ev_end > buf_s:
                    violations.append({
                        "student_id": sid,
                        "internal_course": ev["course_code"],
                        "external_blackout": (buf_s, buf_e),
                        "exam_window": (ev_start, ev_end),
                    })

    return violations


def validate_hc2(events, enrollments, students_map):
    """HC2: No two internal exams overlap for any student."""
    violations = []
    int_ids = {ev["course_id"] for ev in events}
    student_courses: dict[int, list[int]] = defaultdict(list)
    for e in enrollments:
        if e.course_id in int_ids:
            student_courses[e.student_id].append(e.course_id)

    event_map = {ev["course_id"]: ev for ev in events}

    for sid, cids in student_courses.items():
        if len(cids) < 2:
            continue
        intervals = []
        student = students_map.get(sid)
        mult = student.accommodations_multiplier if student else 1.0

        for cid in cids:
            ev = event_map[cid]
            s = ev["start_minutes"]
            dur = ev["duration_minutes"]
            if mult > 1.0:
                dur = math.ceil(dur * mult)
            intervals.append((s, s + dur, ev["course_code"]))

        intervals.sort()
        for i in range(len(intervals) - 1):
            if intervals[i][1] > intervals[i + 1][0]:
                violations.append({
                    "student_id": sid,
                    "exam_a": intervals[i][2],
                    "exam_b": intervals[i + 1][2],
                })

    return violations


def validate_hc3(events, rooms_map, course_students):
    """HC3: Room exclusivity + capacity."""
    violations = []

    # Capacity check
    for ev in events:
        rid = ev["room_id"]
        room = rooms_map.get(rid)
        enrolled = len(course_students.get(ev["course_id"], set()))
        if room and enrolled > room.exam_capacity:
            violations.append({
                "type": "capacity",
                "course": ev["course_code"],
                "enrolled": enrolled,
                "room_capacity": room.exam_capacity,
            })

    # Room exclusivity
    room_events: dict[int, list[dict]] = defaultdict(list)
    for ev in events:
        room_events[ev["room_id"]].append(ev)

    for rid, evs in room_events.items():
        evs_sorted = sorted(evs, key=lambda e: e["start_minutes"])
        for i in range(len(evs_sorted) - 1):
            if evs_sorted[i]["end_minutes"] > evs_sorted[i + 1]["start_minutes"]:
                violations.append({
                    "type": "overlap",
                    "room_id": rid,
                    "exam_a": evs_sorted[i]["course_code"],
                    "exam_b": evs_sorted[i + 1]["course_code"],
                })

    return violations


def compute_fatigue(events, enrollments):
    """Report students with >2 exams in 24 hours."""
    window = settings.FATIGUE_WINDOW_HOURS * 60
    int_ids = {ev["course_id"] for ev in events}
    student_courses: dict[int, list[int]] = defaultdict(list)
    for e in enrollments:
        if e.course_id in int_ids:
            student_courses[e.student_id].append(e.course_id)

    event_map = {ev["course_id"]: ev for ev in events}
    fatigued = []

    for sid, cids in student_courses.items():
        if len(cids) <= 2:
            continue
        starts = sorted(event_map[cid]["start_minutes"] for cid in cids)
        for i in range(len(starts)):
            count = 1
            for j in range(i + 1, len(starts)):
                if starts[j] - starts[i] < window:
                    count += 1
            if count > 2:
                fatigued.append(sid)
                break

    return fatigued


# -- Main ----------------------------------------------------------

async def main() -> None:
    print("=" * 70)
    print("  [SOLVER] OR-TOOLS CP-SAT SOLVER TEST")
    print("=" * 70)

    # Load data
    print("\n[LOAD] Loading mock data from database ...")
    internal_courses, external_courses, students, enrollments, rooms = await load_data()

    print(f"    Internal courses : {len(internal_courses)}")
    print(f"    External courses : {len(external_courses)}")
    print(f"    Students         : {len(students)}")
    print(f"    Rooms            : {len(rooms)}")
    print(f"    Enrollments      : {len(enrollments)}")
    print(f"    Period           : {EXAM_PERIOD_START.isoformat()} -> {EXAM_PERIOD_END.isoformat()}")
    print(f"    Transit buffer   : {settings.TRANSIT_BUFFER_MINUTES} min")

    # Solve
    print("\n[BUILD] Building & solving CP-SAT model ...")
    optimizer = ScheduleOptimizer(
        internal_courses=internal_courses,
        external_courses=external_courses,
        students=students,
        enrollments=enrollments,
        rooms=rooms,
        period_start=EXAM_PERIOD_START,
        period_end=EXAM_PERIOD_END,
    )

    result = optimizer.solve(time_limit_seconds=60)

    print(f"\n[RESULT] Solver Status: {result.status}")
    print(f"    Solve time: {result.solve_time_seconds}s")

    if result.status in ("OPTIMAL", "FEASIBLE"):
        # Print schedule
        print("\n" + "-" * 70)
        print("  GENERATED SCHEDULE")
        print("-" * 70)

        rooms_map = {r.id: r for r in rooms}
        for ev in sorted(result.events, key=lambda e: e["start_minutes"]):
            room = rooms_map.get(ev["room_id"])
            room_name = room.name if room else "???"
            enrolled = len(optimizer.course_students.get(ev["course_id"], set()))
            print(
                f"    {ev['course_code']:<10} | "
                f"{ev['start_time'][:16]} -> {ev['end_time'][:16]} | "
                f"{room_name:<25} | "
                f"{enrolled} students"
            )

        # Validate
        print("\n" + "-" * 70)
        print("  CONSTRAINT VALIDATION")
        print("-" * 70)

        students_map = {s.id: s for s in students}
        course_students = optimizer.course_students

        # HC1
        hc1_violations = validate_hc1(result.events, external_courses, enrollments, students_map)
        status_str = "PASS" if not hc1_violations else f"FAIL ({len(hc1_violations)} violations)"
        print(f"    HC1 (External Blackout + Buffer) : {status_str}")
        for v in hc1_violations[:3]:
            print(f"        Student {v['student_id']} -- {v['internal_course']} overlaps blackout")

        # HC2
        hc2_violations = validate_hc2(result.events, enrollments, students_map)
        status_str = "PASS" if not hc2_violations else f"FAIL ({len(hc2_violations)} violations)"
        print(f"    HC2 (Student Zero-Overlap)       : {status_str}")
        for v in hc2_violations[:3]:
            print(f"        Student {v['student_id']} -- {v['exam_a']} overlaps {v['exam_b']}")

        # HC3
        hc3_violations = validate_hc3(result.events, rooms_map, course_students)
        status_str = "PASS" if not hc3_violations else f"FAIL ({len(hc3_violations)} violations)"
        print(f"    HC3 (Room Exclusivity & Capacity): {status_str}")
        for v in hc3_violations[:3]:
            print(f"        {v}")

        # Fatigue
        fatigued = compute_fatigue(result.events, enrollments)
        print(f"\n    SC  (Fatigue): {len(fatigued)} students with >2 exams in {settings.FATIGUE_WINDOW_HOURS}h")

        all_pass = not hc1_violations and not hc2_violations and not hc3_violations
        print("\n" + "=" * 70)
        if all_pass:
            print("  ALL HARD CONSTRAINTS SATISFIED -- Schedule is valid!")
        else:
            print("  WARNING: Some constraints violated -- see details above.")
        print("=" * 70)

    else:
        print("\n[FAIL] Solver could not find a feasible schedule.")
        if result.infeasibility_details:
            print("\n    Infeasibility details:")
            for key, val in result.infeasibility_details.items():
                print(f"      {key}: {val}")


if __name__ == "__main__":
    asyncio.run(main())
