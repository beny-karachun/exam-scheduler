"use client";

import { useState, useCallback } from "react";
import useSWR from "swr";
import { addDays } from "date-fns";
import { DashboardHeader } from "@/components/dashboard-header";
import { StatsCards } from "@/components/stats-cards";
import { ExamCalendar } from "@/components/exam-calendar";
import { SolverPanel } from "@/components/solver-panel";
import { CoursesPanel } from "@/components/courses-panel";
import { RoomsPanel } from "@/components/rooms-panel";
import { fetcher, type Course, type Student, type Room, type Enrollment, type ExamEvent } from "@/lib/api";

export default function DashboardPage() {
  const [refreshKey, setRefreshKey] = useState(0);

  // Fetch all data with SWR
  const { data: courses, isLoading: loadingCourses, mutate: mutateCourses } = useSWR<Course[]>(
    `/courses?refresh=${refreshKey}`,
    fetcher
  );
  const { data: students, isLoading: loadingStudents, mutate: mutateStudents } = useSWR<Student[]>(
    `/students?refresh=${refreshKey}`,
    fetcher
  );
  const { data: rooms, isLoading: loadingRooms, mutate: mutateRooms } = useSWR<Room[]>(
    `/rooms?refresh=${refreshKey}`,
    fetcher
  );
  const { data: enrollments, mutate: mutateEnrollments } = useSWR<Enrollment[]>(
    `/enrollments?refresh=${refreshKey}`,
    fetcher
  );
  const { data: examEvents, isLoading: loadingEvents, mutate: mutateEvents } = useSWR<ExamEvent[]>(
    `/exam-events?refresh=${refreshKey}`,
    fetcher
  );

  const isLoading = loadingCourses || loadingStudents || loadingRooms || loadingEvents;

  // Exam period (configurable in a real app)
  const periodStart = new Date();
  const periodEnd = addDays(periodStart, 14);

  const handleRefresh = useCallback(() => {
    setRefreshKey((k) => k + 1);
    mutateCourses();
    mutateStudents();
    mutateRooms();
    mutateEnrollments();
    mutateEvents();
  }, [mutateCourses, mutateStudents, mutateRooms, mutateEnrollments, mutateEvents]);

  const handleSolveComplete = useCallback(() => {
    // Refetch exam events after solver completes
    mutateEvents();
  }, [mutateEvents]);

  const handleEventUpdate = useCallback(
    (eventId: number, newStart: Date, newRoomId: number) => {
      // In a real app, this would call an API to persist the change
      // For now, we just refetch
      mutateEvents();
    },
    [mutateEvents]
  );

  return (
    <div className="min-h-screen bg-background">
      <DashboardHeader onRefresh={handleRefresh} isLoading={isLoading} />

      <main className="container mx-auto px-4 py-6 space-y-6">
        {/* Stats Overview */}
        <StatsCards
          courses={courses || []}
          students={students || []}
          rooms={rooms || []}
          examEvents={examEvents || []}
        />

        {/* Main Content Grid */}
        <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
          {/* Calendar */}
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <div className="h-[600px]">
              {isLoading ? (
                <div className="flex items-center justify-center h-full">
                  <div className="flex flex-col items-center gap-2">
                    <div className="h-8 w-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                    <p className="text-sm text-muted-foreground">Loading schedule...</p>
                  </div>
                </div>
              ) : (
                <ExamCalendar
                  courses={courses || []}
                  rooms={rooms || []}
                  examEvents={examEvents || []}
                  periodStart={periodStart}
                  periodEnd={periodEnd}
                  onEventUpdate={handleEventUpdate}
                />
              )}
            </div>
          </div>

          {/* Sidebar */}
          <div className="space-y-4">
            <SolverPanel
              periodStart={periodStart}
              periodEnd={periodEnd}
              onSolveComplete={handleSolveComplete}
            />
            <CoursesPanel courses={courses || []} />
            <RoomsPanel rooms={rooms || []} />
          </div>
        </div>
      </main>
    </div>
  );
}
