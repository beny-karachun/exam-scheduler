"""Generate realistic mock data for the exam scheduling system.

Creates an SQLite database with:
- 5 INTERNAL courses (faculty-controlled)
- 3 EXTERNAL courses (fixed blackout windows)
- 4 faculty-owned rooms
- 100 students (~5 % accommodated)
- Overlapping enrollments: every student in 2-4 internal + ~60 % in ≥1 external

Usage:
    python -m scripts.generate_mock_data
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory, init_db, drop_db
from app.models import Course, Enrollment, OwnershipDomain, Room, Student


# ── Configuration ─────────────────────────────────────────────

SEED = 42

# Exam period: a full week starting next Monday at 08:00
EXAM_PERIOD_START = datetime(2026, 3, 16, 8, 0, tzinfo=timezone.utc)

INTERNAL_COURSES = [
    {"code": "BIO401", "name": "Genetics 401",                    "duration_minutes": 180},
    {"code": "BIO302", "name": "Molecular Biology 302",           "duration_minutes": 150},
    {"code": "BIO210", "name": "Biochemistry Fundamentals",       "duration_minutes": 120},
    {"code": "BIO455", "name": "Bioinformatics & Genomics",       "duration_minutes": 150},
    {"code": "BIO330", "name": "Cell Biology & Signal Transduction", "duration_minutes": 120},
]

EXTERNAL_COURSES = [
    {
        "code": "MATH201",
        "name": "Calculus II",
        "fixed_start_time": EXAM_PERIOD_START + timedelta(days=0, hours=1),   # Mon 09:00
        "fixed_end_time":   EXAM_PERIOD_START + timedelta(days=0, hours=4),   # Mon 12:00
    },
    {
        "code": "PHYS101",
        "name": "Physics I – Mechanics",
        "fixed_start_time": EXAM_PERIOD_START + timedelta(days=1, hours=2),   # Tue 10:00
        "fixed_end_time":   EXAM_PERIOD_START + timedelta(days=1, hours=5),   # Tue 13:00
    },
    {
        "code": "CHEM220",
        "name": "Organic Chemistry",
        "fixed_start_time": EXAM_PERIOD_START + timedelta(days=2, hours=0),   # Wed 08:00
        "fixed_end_time":   EXAM_PERIOD_START + timedelta(days=2, hours=3),   # Wed 11:00
    },
]

ROOMS = [
    {"name": "Biotech Auditorium A",  "exam_capacity": 120},
    {"name": "Lab Wing Lecture Hall",  "exam_capacity": 80},
    {"name": "Seminar Room B2",        "exam_capacity": 40},
    {"name": "Tutorial Room C3",       "exam_capacity": 30},
]

# Pre-built name pools (no faker dependency)
FIRST_NAMES = [
    "Noa", "Yael", "Tamar", "Shira", "Maya", "Liora", "Talia", "Ori",
    "Dana", "Michal", "Lior", "Adi", "Omer", "Amit", "Itai", "Eyal",
    "Rotem", "Hila", "Avigail", "Yoav", "Elad", "Gal", "Ron", "Dor",
    "Matan", "Nir", "Alon", "Keren", "Inbar", "Sapir", "Tal", "Shay",
    "Daniel", "Noam", "Ido", "Ben", "Tom", "Roni", "Ofir", "Yonatan",
    "Chen", "Zohar", "Naomi", "Ella", "Shai", "Ran", "Gil", "Yuval",
    "Ariel", "Oren",
]

LAST_NAMES = [
    "Cohen", "Levy", "Mizrahi", "Peretz", "Biton", "Dahan", "Avraham",
    "Friedman", "Malka", "Azoulay", "Shapira", "Yosef", "David",
    "Aharoni", "Bar", "Katz", "Goldstein", "Ben-David", "Stern",
    "Weiss", "Hadad", "Ovadia", "Klein", "Rosenberg", "Berkowitz",
    "Naor", "Tzur", "Regev", "Segal", "Hoffman", "Carmi", "Sasson",
    "Elbaz", "Golan", "Levi", "Gross", "Feldman", "Rosen", "Navon",
    "Harari", "Kimchi", "Ben-Ari", "Meir", "Shriki", "Salem",
    "Zilberman", "Toledano", "Gabay", "Halevi", "Shalom",
]


# ── Seeding Functions ─────────────────────────────────────────

async def seed_courses(session: AsyncSession) -> dict[str, Course]:
    """Insert internal + external courses. Returns {code: Course}."""
    courses: dict[str, Course] = {}

    for c in INTERNAL_COURSES:
        course = Course(
            code=c["code"],
            name=c["name"],
            ownership_domain=OwnershipDomain.INTERNAL,
            duration_minutes=c["duration_minutes"],
        )
        session.add(course)
        courses[c["code"]] = course

    for c in EXTERNAL_COURSES:
        course = Course(
            code=c["code"],
            name=c["name"],
            ownership_domain=OwnershipDomain.EXTERNAL,
            fixed_start_time=c["fixed_start_time"],
            fixed_end_time=c["fixed_end_time"],
        )
        session.add(course)
        courses[c["code"]] = course

    await session.flush()  # assigns IDs
    return courses


async def seed_rooms(session: AsyncSession) -> list[Room]:
    """Insert faculty-owned rooms."""
    rooms = [Room(**r) for r in ROOMS]
    session.add_all(rooms)
    await session.flush()
    return rooms


async def seed_students(session: AsyncSession, count: int = 100) -> list[Student]:
    """Insert students with realistic names; ~5% get accommodations."""
    rng = random.Random(SEED)
    students: list[Student] = []

    for i in range(1, count + 1):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        multiplier = 1.5 if rng.random() < 0.05 else 1.0
        student = Student(
            student_number=f"2024{i:04d}",
            name=f"{first} {last}",
            accommodations_multiplier=multiplier,
        )
        students.append(student)

    session.add_all(students)
    await session.flush()
    return students


async def seed_enrollments(
    session: AsyncSession,
    students: list[Student],
    courses: dict[str, Course],
) -> list[Enrollment]:
    """
    Generate overlapping enrollments:
    - Each student enrolled in 2-4 INTERNAL courses.
    - ~60% of students enrolled in ≥1 EXTERNAL course (some in 2+).
    """
    rng = random.Random(SEED + 1)
    internal_codes = [c for c, obj in courses.items() if obj.ownership_domain == OwnershipDomain.INTERNAL]
    external_codes = [c for c, obj in courses.items() if obj.ownership_domain == OwnershipDomain.EXTERNAL]
    enrollments: list[Enrollment] = []

    for student in students:
        # INTERNAL: 2-4 courses
        num_internal = rng.randint(2, 4)
        chosen_internal = rng.sample(internal_codes, min(num_internal, len(internal_codes)))
        for code in chosen_internal:
            enrollments.append(
                Enrollment(student_id=student.id, course_id=courses[code].id)
            )

        # EXTERNAL: 60% chance → 1-2 external courses
        if rng.random() < 0.60:
            num_external = rng.choices([1, 2], weights=[0.7, 0.3])[0]
            chosen_external = rng.sample(external_codes, min(num_external, len(external_codes)))
            for code in chosen_external:
                enrollments.append(
                    Enrollment(student_id=student.id, course_id=courses[code].id)
                )

    session.add_all(enrollments)
    await session.flush()
    return enrollments


# ── Summary Report ────────────────────────────────────────────

async def print_summary(session: AsyncSession) -> None:
    """Print stats about the seeded data."""
    # Total counts
    n_students = (await session.execute(select(func.count(Student.id)))).scalar_one()
    n_courses  = (await session.execute(select(func.count(Course.id)))).scalar_one()
    n_rooms    = (await session.execute(select(func.count(Room.id)))).scalar_one()
    n_enroll   = (await session.execute(select(func.count(Enrollment.id)))).scalar_one()

    # Internal vs external course counts
    n_internal = (await session.execute(
        select(func.count(Course.id)).where(Course.ownership_domain == OwnershipDomain.INTERNAL)
    )).scalar_one()
    n_external = (await session.execute(
        select(func.count(Course.id)).where(Course.ownership_domain == OwnershipDomain.EXTERNAL)
    )).scalar_one()

    # Students with ≥1 external enrollment
    ext_students_q = (
        select(func.count(func.distinct(Enrollment.student_id)))
        .join(Course, Enrollment.course_id == Course.id)
        .where(Course.ownership_domain == OwnershipDomain.EXTERNAL)
    )
    n_ext_students = (await session.execute(ext_students_q)).scalar_one()

    # Accommodated students
    n_accommodated = (await session.execute(
        select(func.count(Student.id)).where(Student.accommodations_multiplier > 1.0)
    )).scalar_one()

    # Conflict density: student pairs sharing ≥2 courses
    # (simple in-memory computation for 100 students)
    all_enrollments = (await session.execute(
        select(Enrollment.student_id, Enrollment.course_id)
    )).all()
    student_courses: dict[int, set[int]] = defaultdict(set)
    for sid, cid in all_enrollments:
        student_courses[sid].add(cid)

    conflict_pairs = 0
    student_ids = list(student_courses.keys())
    for i in range(len(student_ids)):
        for j in range(i + 1, len(student_ids)):
            shared = student_courses[student_ids[i]] & student_courses[student_ids[j]]
            if len(shared) >= 2:
                conflict_pairs += 1

    # Enrollments per course
    course_enrollment_q = (
        select(Course.code, Course.name, func.count(Enrollment.id))
        .join(Enrollment, Course.id == Enrollment.course_id)
        .group_by(Course.id, Course.code, Course.name)
        .order_by(Course.code)
    )
    course_enrollments = (await session.execute(course_enrollment_q)).all()

    # ── Print ──
    print("\n" + "=" * 65)
    print("  📊  MOCK DATA GENERATION SUMMARY")
    print("=" * 65)
    print(f"  Students       : {n_students:>5}   (accommodated: {n_accommodated})")
    print(f"  Courses        : {n_courses:>5}   (internal: {n_internal}, external: {n_external})")
    print(f"  Rooms          : {n_rooms:>5}")
    print(f"  Enrollments    : {n_enroll:>5}")
    print(f"  Ext-enrolled   : {n_ext_students:>5}   students with ≥1 external course")
    print(f"  Conflict pairs : {conflict_pairs:>5}   student pairs sharing ≥2 courses")
    print("-" * 65)
    print("  📋  ENROLLMENTS PER COURSE")
    print("-" * 65)
    for code, name, count in course_enrollments:
        bar = "█" * (count // 2)
        print(f"    {code:<10} {name:<38} {count:>3} {bar}")
    print("=" * 65)
    print()


# ── Main ──────────────────────────────────────────────────────

async def main() -> None:
    print("🗄️  Initializing database (SQLite) …")
    await drop_db()
    await init_db()

    async with async_session_factory() as session:
        async with session.begin():
            print("📚  Seeding courses …")
            courses = await seed_courses(session)

            print("🏫  Seeding rooms …")
            await seed_rooms(session)

            print("🎓  Seeding students …")
            students = await seed_students(session)

            print("🔗  Seeding enrollments …")
            await seed_enrollments(session, students, courses)

    # Read-only session for summary
    async with async_session_factory() as session:
        await print_summary(session)

    print("✅  Mock data generation complete. Database: exam_scheduler_dev.db\n")


if __name__ == "__main__":
    asyncio.run(main())
