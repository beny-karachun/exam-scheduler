// API client for the FastAPI backend

const API_BASE = "/api";

// Types matching the backend schemas
export interface Course {
  id: number;
  code: string;
  name: string;
  ownership_domain: "INTERNAL" | "EXTERNAL";
  duration_minutes: number | null;
  fixed_start_time: string | null;
  fixed_end_time: string | null;
}

export interface Student {
  id: number;
  student_number: string;
  name: string;
  accommodations_multiplier: number;
}

export interface Room {
  id: number;
  name: string;
  exam_capacity: number;
}

export interface Enrollment {
  id: number;
  student_id: number;
  course_id: number;
}

export interface ExamEvent {
  id: number;
  course_id: number;
  room_id: number;
  start_time: string;
  end_time: string;
}

export interface ValidateMoveRequest {
  course_id: number;
  new_start_time: string;
  new_room_id: number;
}

export interface ValidateMoveResponse {
  is_valid: boolean;
  conflict_reason: string;
}

export interface ScheduleRequest {
  exam_period_start: string;
  exam_period_end: string;
  slot_duration_minutes?: number;
}

export interface SolverResultResponse {
  status: "PENDING" | "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "ERROR";
  events: Array<{
    course_id: number;
    room_id: number;
    start_time: string;
    end_time: string;
  }>;
  solve_time_seconds: number;
  infeasibility_details: Record<string, unknown> | null;
  infeasibility_explanation: Record<string, unknown> | null;
}

// Fetcher for SWR
export const fetcher = <T>(url: string): Promise<T> =>
  fetch(`${API_BASE}${url}`).then((res) => {
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
  });

// API functions
export async function getCourses(): Promise<Course[]> {
  const res = await fetch(`${API_BASE}/courses`);
  if (!res.ok) throw new Error("Failed to fetch courses");
  return res.json();
}

export async function getStudents(): Promise<Student[]> {
  const res = await fetch(`${API_BASE}/students`);
  if (!res.ok) throw new Error("Failed to fetch students");
  return res.json();
}

export async function getRooms(): Promise<Room[]> {
  const res = await fetch(`${API_BASE}/rooms`);
  if (!res.ok) throw new Error("Failed to fetch rooms");
  return res.json();
}

export async function getEnrollments(): Promise<Enrollment[]> {
  const res = await fetch(`${API_BASE}/enrollments`);
  if (!res.ok) throw new Error("Failed to fetch enrollments");
  return res.json();
}

export async function getExamEvents(): Promise<ExamEvent[]> {
  const res = await fetch(`${API_BASE}/exam-events`);
  if (!res.ok) throw new Error("Failed to fetch exam events");
  return res.json();
}

export async function validateMove(
  data: ValidateMoveRequest
): Promise<ValidateMoveResponse> {
  const res = await fetch(`${API_BASE}/schedule/validate-move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.detail || "Validation failed");
  }
  return res.json();
}

export async function solveSchedule(
  data: ScheduleRequest
): Promise<{ task_id: string; message: string }> {
  const res = await fetch(`${API_BASE}/schedule/solve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to start solver");
  return res.json();
}

export async function getSolverResult(
  taskId: string
): Promise<SolverResultResponse> {
  const res = await fetch(`${API_BASE}/schedule/result/${taskId}`);
  if (!res.ok) throw new Error("Failed to fetch solver result");
  return res.json();
}
