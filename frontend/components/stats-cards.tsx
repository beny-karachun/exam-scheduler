"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BookOpen, Users, DoorOpen, CalendarCheck } from "lucide-react";
import type { Course, Student, Room, ExamEvent } from "@/lib/api";

interface StatsCardsProps {
  courses: Course[];
  students: Student[];
  rooms: Room[];
  examEvents: ExamEvent[];
}

export function StatsCards({ courses, students, rooms, examEvents }: StatsCardsProps) {
  const internalCourses = courses.filter((c) => c.ownership_domain === "INTERNAL");
  const externalCourses = courses.filter((c) => c.ownership_domain === "EXTERNAL");

  const stats = [
    {
      title: "Internal Courses",
      value: internalCourses.length,
      description: "Courses to schedule",
      icon: BookOpen,
      color: "text-internal",
    },
    {
      title: "External Courses",
      value: externalCourses.length,
      description: "Fixed blackouts",
      icon: CalendarCheck,
      color: "text-external-foreground",
    },
    {
      title: "Students",
      value: students.length,
      description: "Total enrolled",
      icon: Users,
      color: "text-primary",
    },
    {
      title: "Rooms",
      value: rooms.length,
      description: `${rooms.reduce((acc, r) => acc + r.exam_capacity, 0)} total capacity`,
      icon: DoorOpen,
      color: "text-muted-foreground",
    },
  ];

  return (
    <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
      {stats.map((stat) => (
        <Card key={stat.title} className="bg-card/50">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              {stat.title}
            </CardTitle>
            <stat.icon className={`h-4 w-4 ${stat.color}`} />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stat.value}</div>
            <p className="text-xs text-muted-foreground">{stat.description}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
