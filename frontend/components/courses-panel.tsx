"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Clock, Lock, GripVertical } from "lucide-react";
import type { Course } from "@/lib/api";
import { format, parseISO } from "date-fns";

interface CoursesPanelProps {
  courses: Course[];
}

export function CoursesPanel({ courses }: CoursesPanelProps) {
  const internalCourses = courses.filter((c) => c.ownership_domain === "INTERNAL");
  const externalCourses = courses.filter((c) => c.ownership_domain === "EXTERNAL");

  return (
    <Card className="bg-card/50">
      <CardHeader>
        <CardTitle className="text-base">Courses</CardTitle>
        <CardDescription>
          {internalCourses.length} internal, {externalCourses.length} external
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 max-h-[400px] overflow-y-auto">
        {/* Internal Courses */}
        {internalCourses.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Internal (Schedulable)
            </p>
            {internalCourses.map((course) => (
              <div
                key={course.id}
                className="flex items-center gap-3 p-2 rounded-md bg-internal/10 border border-internal/20"
              >
                <GripVertical className="h-4 w-4 text-internal/60" />
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-sm truncate">{course.code}</p>
                  <p className="text-xs text-muted-foreground truncate">{course.name}</p>
                </div>
                <Badge variant="internal" className="text-[10px] gap-1">
                  <Clock className="h-2.5 w-2.5" />
                  {course.duration_minutes}m
                </Badge>
              </div>
            ))}
          </div>
        )}

        {/* External Courses */}
        {externalCourses.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              External (Fixed)
            </p>
            {externalCourses.map((course) => (
              <div
                key={course.id}
                className="flex items-center gap-3 p-2 rounded-md bg-external/10 border border-external/20"
              >
                <Lock className="h-4 w-4 text-external-foreground/60" />
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-sm truncate">{course.code}</p>
                  <p className="text-xs text-muted-foreground truncate">{course.name}</p>
                </div>
                {course.fixed_start_time && (
                  <Badge variant="external" className="text-[10px]">
                    {format(parseISO(course.fixed_start_time), "MMM d, HH:mm")}
                  </Badge>
                )}
              </div>
            ))}
          </div>
        )}

        {courses.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-4">
            No courses found
          </p>
        )}
      </CardContent>
    </Card>
  );
}
