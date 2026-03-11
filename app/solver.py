"""OR-Tools CP-SAT Scheduling Engine.

The `ScheduleOptimizer` class encodes the exam scheduling problem as a
Constraint Programming model and solves it deterministically.

Constraints
-----------
HC1  External Blackout + 45-min Transit Buffer
HC2  Student Zero-Overlap (all exams)
HC3  Room Exclusivity & Capacity
SC   Fatigue Penalty (>2 exams in 24 h rolling window)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ortools.sat.python import cp_model

from app.config import settings


# ── Plain Data Objects (solver never touches SQLAlchemy) ──────

@dataclass(frozen=True)
class CourseData:
    id: int
    code: str
    is_internal: bool
    duration_minutes: int | None = None        # INTERNAL only
    fixed_start: datetime | None = None         # EXTERNAL only
    fixed_end: datetime | None = None           # EXTERNAL only


@dataclass(frozen=True)
class StudentData:
    id: int
    name: str
    accommodations_multiplier: float = 1.0


@dataclass(frozen=True)
class RoomData:
    id: int
    name: str
    exam_capacity: int


@dataclass(frozen=True)
class EnrollmentData:
    student_id: int
    course_id: int


@dataclass
class SolverResult:
    """Output of the solver, ready for the API layer to consume."""
    status: str                                   # OPTIMAL | FEASIBLE | INFEASIBLE | ERROR
    events: list[dict[str, Any]] = field(default_factory=list)
    solve_time_seconds: float = 0.0
    infeasibility_details: dict[str, Any] | None = None


# ── Helper: Time Discretization ──────────────────────────────

def _normalize_tz(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (assume UTC if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _dt_to_minutes(dt: datetime, origin: datetime) -> int:
    """Convert a datetime to integer minutes elapsed since `origin`."""
    delta = _normalize_tz(dt) - _normalize_tz(origin)
    return int(delta.total_seconds()) // 60


def _minutes_to_dt(minutes: int, origin: datetime) -> datetime:
    """Convert integer minutes back to a datetime."""
    return origin + timedelta(minutes=minutes)


# ── The Solver ────────────────────────────────────────────────

class ScheduleOptimizer:
    """
    Google OR-Tools CP-SAT model for faculty exam scheduling.

    All time values are discretized to integer minutes from `period_start`.
    """

    def __init__(
        self,
        internal_courses: list[CourseData],
        external_courses: list[CourseData],
        students: list[StudentData],
        enrollments: list[EnrollmentData],
        rooms: list[RoomData],
        period_start: datetime,
        period_end: datetime,
    ) -> None:
        self.internal_courses = internal_courses
        self.external_courses = external_courses
        self.students = {s.id: s for s in students}
        self.rooms = rooms
        self.period_start = period_start
        self.period_end = period_end
        self.horizon = _dt_to_minutes(period_end, period_start)

        # ── Pre-compute index maps ──
        # student_id → list of internal course IDs
        self.student_internal: dict[int, list[int]] = {}
        # student_id → list of external course IDs
        self.student_external: dict[int, list[int]] = {}
        # course_id → set of enrolled student IDs
        self.course_students: dict[int, set[int]] = {}

        for e in enrollments:
            self.course_students.setdefault(e.course_id, set()).add(e.student_id)

        internal_ids = {c.id for c in internal_courses}
        external_ids = {c.id for c in external_courses}

        for e in enrollments:
            if e.course_id in internal_ids:
                self.student_internal.setdefault(e.student_id, []).append(e.course_id)
            elif e.course_id in external_ids:
                self.student_external.setdefault(e.student_id, []).append(e.course_id)

        # Course lookup by ID
        self._course_map: dict[int, CourseData] = {
            c.id: c for c in (*internal_courses, *external_courses)
        }

        # ── CP model containers (populated during build) ──
        self.model = cp_model.CpModel()

        # Per internal course: start var, interval var, effective duration
        self.start_vars: dict[int, Any] = {}      # course_id → IntVar
        self.interval_vars: dict[int, Any] = {}    # course_id → IntervalVar
        self.durations: dict[int, int] = {}        # course_id → base duration (minutes)

        # Room assignment: (course_id, room_id) → BoolVar
        self.room_assign: dict[tuple[int, int], Any] = {}

        # Optional per-room intervals: (course_id, room_id) → IntervalVar
        self.room_intervals: dict[tuple[int, int], Any] = {}

        # Fatigue penalty vars
        self._fatigue_penalties: list[Any] = []

    # ── Build the entire model ────────────────────────────────

    def build(self) -> None:
        """Construct all variables and constraints."""
        self._create_variables()
        self._add_room_constraints()
        self._add_student_constraints()
        self._add_fatigue_penalties()
        self._set_objective()

    # ── Variable Creation ─────────────────────────────────────

    def _create_variables(self) -> None:
        """Create an IntervalVar for each internal course + room assignment BoolVars."""
        for course in self.internal_courses:
            cid = course.id
            dur = course.duration_minutes
            assert dur is not None, f"INTERNAL course {course.code} has no duration"

            self.durations[cid] = dur

            # The start variable: can be placed anywhere in [0, horizon - dur]
            start = self.model.new_int_var(0, self.horizon - dur, f"start_c{cid}")
            self.start_vars[cid] = start

            # Main interval (base duration — accommodations are per-student, handled
            # by creating expanded intervals in the student constraint section)
            interval = self.model.new_interval_var(
                start, dur, start + dur, f"interval_c{cid}"
            )
            self.interval_vars[cid] = interval

            # ── Room assignment BoolVars + optional room intervals ──
            feasible_rooms = []
            enrolled_count = len(self.course_students.get(cid, set()))

            for room in self.rooms:
                rid = room.id
                key = (cid, rid)

                if enrolled_count > room.exam_capacity:
                    # Prune: this room is too small
                    continue

                assign_var = self.model.new_bool_var(f"assign_c{cid}_r{rid}")
                self.room_assign[key] = assign_var
                feasible_rooms.append(assign_var)

                # Optional interval: only "present" (counted) when assigned
                opt_interval = self.model.new_optional_interval_var(
                    start, dur, start + dur, assign_var, f"room_interval_c{cid}_r{rid}"
                )
                self.room_intervals[key] = opt_interval

            # Exactly one feasible room must be chosen
            if not feasible_rooms:
                # No room can fit this course — model will be infeasible
                # Add a contradiction so solver reports INFEASIBLE cleanly
                self.model.add_bool_or([])  # empty clause → always false
            else:
                self.model.add_exactly_one(feasible_rooms)

    # ── HC3: Room Exclusivity & Capacity ──────────────────────

    def _add_room_constraints(self) -> None:
        """No two exams in the same room at the same time."""
        for room in self.rooms:
            rid = room.id
            room_intervals = [
                self.room_intervals[key]
                for key in self.room_intervals
                if key[1] == rid
            ]
            if len(room_intervals) > 1:
                self.model.add_no_overlap(room_intervals)

    # ── HC1 + HC2: Student Constraints ────────────────────────

    def _add_student_constraints(self) -> None:
        """
        Per student: NoOverlap across all their intervals.

        - Internal exams → use the course's IntervalVar (scaled by
          accommodations multiplier if > 1.0, via a per-student alias).
        - External exams → create fixed intervals with ±45-min transit buffer.
        """
        buffer = settings.TRANSIT_BUFFER_MINUTES

        for sid, student in self.students.items():
            student_intervals: list[Any] = []
            multiplier = student.accommodations_multiplier

            # ── Internal exam intervals for this student ──
            for cid in self.student_internal.get(sid, []):
                base_dur = self.durations[cid]

                if multiplier > 1.0:
                    # Create a separate, longer interval for this student
                    adj_dur = math.ceil(base_dur * multiplier)
                    start = self.start_vars[cid]
                    stu_interval = self.model.new_interval_var(
                        start, adj_dur, start + adj_dur,
                        f"stu_interval_s{sid}_c{cid}"
                    )
                    student_intervals.append(stu_interval)
                else:
                    # Standard — reuse the course-level interval
                    student_intervals.append(self.interval_vars[cid])

            # ── External blackout intervals (HC1) ──
            for cid in self.student_external.get(sid, []):
                ext_course = self._course_map[cid]
                assert ext_course.fixed_start is not None
                assert ext_course.fixed_end is not None

                raw_start = _dt_to_minutes(ext_course.fixed_start, self.period_start)
                raw_end = _dt_to_minutes(ext_course.fixed_end, self.period_start)

                buf_start = max(0, raw_start - buffer)
                buf_end = min(self.horizon, raw_end + buffer)
                buf_dur = buf_end - buf_start

                if buf_dur <= 0:
                    continue

                # Fixed interval — not a decision variable
                ext_interval = self.model.new_fixed_size_interval_var(
                    buf_start, buf_dur,
                    f"ext_blackout_s{sid}_c{cid}"
                )
                student_intervals.append(ext_interval)

            # ── HC2: No two intervals for this student can overlap ──
            if len(student_intervals) > 1:
                self.model.add_no_overlap(student_intervals)

    # ── SC: Fatigue Penalty ───────────────────────────────────

    def _add_fatigue_penalties(self) -> None:
        """
        Penalize students who have >2 exams within a 24-hour window.

        For each student with ≥3 internal exams, for every triplet of exams,
        add a penalty if all three start within FATIGUE_WINDOW of each other.
        """
        fatigue_window = settings.FATIGUE_WINDOW_HOURS * 60  # convert to minutes
        weight = settings.FATIGUE_PENALTY_WEIGHT

        for sid in self.students:
            internal_cids = self.student_internal.get(sid, [])
            if len(internal_cids) <= settings.FATIGUE_MAX_EXAMS:
                continue  # Can't exceed threshold with ≤2 exams

            # For every pair of exams, create a penalty if they're within the
            # fatigue window. Then, for students with 3+ exams, the cumulative
            # pair penalties will add up when clustering occurs.
            for i in range(len(internal_cids)):
                for j in range(i + 1, len(internal_cids)):
                    cid_i = internal_cids[i]
                    cid_j = internal_cids[j]
                    start_i = self.start_vars[cid_i]
                    start_j = self.start_vars[cid_j]

                    # |start_i - start_j| < fatigue_window
                    # Linearize: create bool var b such that b=1 iff close
                    # diff = start_i - start_j
                    diff = self.model.new_int_var(
                        -self.horizon, self.horizon,
                        f"diff_s{sid}_c{cid_i}_c{cid_j}"
                    )
                    self.model.add(diff == start_i - start_j)

                    abs_diff = self.model.new_int_var(
                        0, self.horizon,
                        f"abs_diff_s{sid}_c{cid_i}_c{cid_j}"
                    )
                    self.model.add_abs_equality(abs_diff, diff)

                    # is_close = 1 iff abs_diff < fatigue_window
                    is_close = self.model.new_bool_var(
                        f"fatigue_s{sid}_c{cid_i}_c{cid_j}"
                    )
                    # is_close = 1 → abs_diff < fatigue_window
                    self.model.add(abs_diff < fatigue_window).only_enforce_if(is_close)
                    # is_close = 0 → abs_diff >= fatigue_window
                    self.model.add(abs_diff >= fatigue_window).only_enforce_if(~is_close)

                    self._fatigue_penalties.append(is_close)

    # ── Objective ─────────────────────────────────────────────

    def _set_objective(self) -> None:
        """Minimize total fatigue penalty."""
        if self._fatigue_penalties:
            self.model.minimize(
                settings.FATIGUE_PENALTY_WEIGHT
                * sum(self._fatigue_penalties)
            )
        else:
            # No fatigue terms — any feasible solution is fine.
            # Optionally minimize makespan (latest exam end).
            if self.start_vars:
                latest_end = self.model.new_int_var(0, self.horizon, "makespan")
                for cid, start in self.start_vars.items():
                    dur = self.durations[cid]
                    self.model.add(latest_end >= start + dur)
                self.model.minimize(latest_end)

    # ── Solve ─────────────────────────────────────────────────

    def solve(self, time_limit_seconds: int = 120) -> SolverResult:
        """Run the CP-SAT solver and return structured results."""
        self.build()

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_seconds
        solver.parameters.num_workers = 8
        solver.parameters.log_search_progress = True

        t0 = time.perf_counter()
        status_code = solver.solve(self.model)
        elapsed = time.perf_counter() - t0

        status_name = solver.status_name(status_code)

        if status_code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            events = self._extract_events(solver)
            return SolverResult(
                status=status_name,
                events=events,
                solve_time_seconds=round(elapsed, 3),
            )

        # INFEASIBLE or MODEL_INVALID
        details = self._extract_infeasibility_details(solver, status_code)
        return SolverResult(
            status=status_name,
            solve_time_seconds=round(elapsed, 3),
            infeasibility_details=details,
        )

    # ── Result Extraction ─────────────────────────────────────

    def _extract_events(self, solver: cp_model.CpSolver) -> list[dict[str, Any]]:
        """Convert solved variables into ExamEvent-compatible dicts."""
        events: list[dict[str, Any]] = []

        for course in self.internal_courses:
            cid = course.id
            start_min = solver.value(self.start_vars[cid])
            dur = self.durations[cid]
            end_min = start_min + dur

            # Determine assigned room
            assigned_room_id: int | None = None
            for room in self.rooms:
                key = (cid, room.id)
                if key in self.room_assign and solver.value(self.room_assign[key]):
                    assigned_room_id = room.id
                    break

            events.append({
                "course_id": cid,
                "course_code": course.code,
                "room_id": assigned_room_id,
                "start_time": _minutes_to_dt(start_min, self.period_start).isoformat(),
                "end_time": _minutes_to_dt(end_min, self.period_start).isoformat(),
                "start_minutes": start_min,
                "end_minutes": end_min,
                "duration_minutes": dur,
            })

        return events

    def _extract_infeasibility_details(
        self, solver: cp_model.CpSolver, status_code: int
    ) -> dict[str, Any]:
        """Collect useful debugging info when solver fails."""
        details: dict[str, Any] = {
            "solver_status_code": status_code,
            "num_internal_courses": len(self.internal_courses),
            "num_students": len(self.students),
            "num_rooms": len(self.rooms),
            "horizon_minutes": self.horizon,
        }

        # Identify courses with no feasible room
        no_room_courses = []
        for course in self.internal_courses:
            cid = course.id
            has_room = any(
                (cid, r.id) in self.room_assign for r in self.rooms
            )
            if not has_room:
                enrolled = len(self.course_students.get(cid, set()))
                no_room_courses.append({
                    "course_code": course.code,
                    "enrolled_students": enrolled,
                    "max_room_capacity": max(r.exam_capacity for r in self.rooms) if self.rooms else 0,
                })
        if no_room_courses:
            details["courses_without_feasible_room"] = no_room_courses

        # Identify students with heaviest constraint load
        heavy_students = []
        for sid, student in self.students.items():
            n_internal = len(self.student_internal.get(sid, []))
            n_external = len(self.student_external.get(sid, []))
            if n_internal + n_external >= 4:
                heavy_students.append({
                    "student_id": sid,
                    "student_name": student.name,
                    "internal_exams": n_internal,
                    "external_exams": n_external,
                })
        if heavy_students:
            heavy_students.sort(key=lambda x: x["internal_exams"] + x["external_exams"], reverse=True)
            details["heavily_constrained_students"] = heavy_students[:10]

        return details
