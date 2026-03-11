"use client";

import React, { useMemo, useState, useCallback } from "react";
import {
  DndContext,
  DragEndEvent,
  DragOverlay,
  DragStartEvent,
  useDraggable,
  useDroppable,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  format,
  addDays,
  startOfDay,
  differenceInMinutes,
  setHours,
  setMinutes,
  parseISO,
  isSameDay,
} from "date-fns";
import { Lock, GripVertical } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Course, Room, ExamEvent } from "@/lib/api";
import { validateMove } from "@/lib/api";
import { toast } from "@/components/ui/toaster";

interface ExamCalendarProps {
  courses: Course[];
  rooms: Room[];
  examEvents: ExamEvent[];
  periodStart: Date;
  periodEnd: Date;
  onEventUpdate?: (eventId: number, newStart: Date, newRoomId: number) => void;
}

interface CalendarEvent {
  id: number;
  courseId: number;
  courseCode: string;
  courseName: string;
  roomId: number;
  roomName: string;
  startTime: Date;
  endTime: Date;
  isExternal: boolean;
  durationMinutes: number;
}

const HOUR_HEIGHT = 48;
const START_HOUR = 8;
const END_HOUR = 20;
const HOURS = Array.from({ length: END_HOUR - START_HOUR }, (_, i) => START_HOUR + i);

function getTimePosition(date: Date): number {
  const hours = date.getHours();
  const minutes = date.getMinutes();
  return ((hours - START_HOUR) + minutes / 60) * HOUR_HEIGHT;
}

function getEventHeight(durationMinutes: number): number {
  return (durationMinutes / 60) * HOUR_HEIGHT;
}

function positionToTime(y: number, baseDate: Date): Date {
  const totalMinutes = (y / HOUR_HEIGHT) * 60;
  const hours = Math.floor(totalMinutes / 60) + START_HOUR;
  const minutes = Math.round((totalMinutes % 60) / 15) * 15;
  return setMinutes(setHours(startOfDay(baseDate), hours), minutes);
}

// Draggable Exam Block
function ExamBlock({
  event,
  isDragging = false,
}: {
  event: CalendarEvent;
  isDragging?: boolean;
}) {
  const { attributes, listeners, setNodeRef, transform } = useDraggable({
    id: `event-${event.id}`,
    disabled: event.isExternal,
    data: event,
  });

  const style = transform
    ? {
        transform: `translate3d(${transform.x}px, ${transform.y}px, 0)`,
      }
    : undefined;

  const top = getTimePosition(event.startTime);
  const height = getEventHeight(event.durationMinutes);

  return (
    <div
      ref={setNodeRef}
      style={{
        ...style,
        top: `${top}px`,
        height: `${height}px`,
        minHeight: "32px",
      }}
      className={cn(
        "exam-block",
        event.isExternal ? "external" : "internal",
        isDragging && "dragging"
      )}
      {...(event.isExternal ? {} : { ...listeners, ...attributes })}
    >
      <div className="flex items-center gap-1 h-full overflow-hidden">
        {event.isExternal ? (
          <Lock className="h-3 w-3 flex-shrink-0 opacity-60" />
        ) : (
          <GripVertical className="h-3 w-3 flex-shrink-0 opacity-60" />
        )}
        <div className="flex flex-col overflow-hidden min-w-0">
          <span className="font-semibold truncate">{event.courseCode}</span>
          {height > 40 && (
            <span className="text-[10px] opacity-80 truncate">{event.roomName}</span>
          )}
        </div>
      </div>
    </div>
  );
}

// Droppable Time Slot
function TimeSlotDroppable({
  day,
  hour,
  roomId,
  children,
}: {
  day: Date;
  hour: number;
  roomId: number;
  children?: React.ReactNode;
}) {
  const id = `slot-${format(day, "yyyy-MM-dd")}-${hour}-${roomId}`;
  const { isOver, setNodeRef } = useDroppable({
    id,
    data: { day, hour, roomId },
  });

  return (
    <div
      ref={setNodeRef}
      className={cn(
        "time-slot relative",
        isOver && "bg-primary/10 border-primary/30"
      )}
    >
      {children}
    </div>
  );
}

// Day Column Header
function DayHeader({ date }: { date: Date }) {
  const isToday = isSameDay(date, new Date());
  return (
    <div
      className={cn(
        "sticky top-0 z-10 bg-card border-b border-border px-3 py-3 text-center",
        isToday && "bg-primary/10"
      )}
    >
      <div className="text-xs text-muted-foreground uppercase">
        {format(date, "EEE")}
      </div>
      <div className={cn("text-lg font-semibold", isToday && "text-primary")}>
        {format(date, "d")}
      </div>
    </div>
  );
}

// Room Column
function RoomColumn({
  room,
  day,
  events,
}: {
  room: Room;
  day: Date;
  events: CalendarEvent[];
}) {
  const dayEvents = events.filter((e) => isSameDay(e.startTime, day));

  return (
    <div className="flex-1 min-w-[140px] border-r border-border last:border-r-0">
      <div className="sticky top-12 z-10 bg-secondary/50 border-b border-border px-2 py-1.5 text-center">
        <div className="text-xs font-medium truncate">{room.name}</div>
        <div className="text-[10px] text-muted-foreground">Cap: {room.exam_capacity}</div>
      </div>
      <div className="relative">
        {HOURS.map((hour) => (
          <TimeSlotDroppable key={hour} day={day} hour={hour} roomId={room.id} />
        ))}
        {dayEvents
          .filter((e) => e.roomId === room.id)
          .map((event) => (
            <ExamBlock key={event.id} event={event} />
          ))}
      </div>
    </div>
  );
}

// Main Calendar Component
export function ExamCalendar({
  courses,
  rooms,
  examEvents,
  periodStart,
  periodEnd,
  onEventUpdate,
}: ExamCalendarProps) {
  const [activeEvent, setActiveEvent] = useState<CalendarEvent | null>(null);
  const [validating, setValidating] = useState(false);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    })
  );

  // Convert exam events to calendar events
  const calendarEvents = useMemo<CalendarEvent[]>(() => {
    const courseMap = new Map(courses.map((c) => [c.id, c]));
    const roomMap = new Map(rooms.map((r) => [r.id, r]));

    // Internal exam events from solver
    const internalEvents: CalendarEvent[] = examEvents.map((ev) => {
      const course = courseMap.get(ev.course_id);
      const room = roomMap.get(ev.room_id);
      const startTime = parseISO(ev.start_time);
      const endTime = parseISO(ev.end_time);
      return {
        id: ev.id,
        courseId: ev.course_id,
        courseCode: course?.code || "Unknown",
        courseName: course?.name || "Unknown Course",
        roomId: ev.room_id,
        roomName: room?.name || "Unknown Room",
        startTime,
        endTime,
        isExternal: false,
        durationMinutes: differenceInMinutes(endTime, startTime),
      };
    });

    // External courses as fixed events
    const externalEvents: CalendarEvent[] = courses
      .filter((c) => c.ownership_domain === "EXTERNAL" && c.fixed_start_time && c.fixed_end_time)
      .map((c, idx) => {
        const startTime = parseISO(c.fixed_start_time!);
        const endTime = parseISO(c.fixed_end_time!);
        return {
          id: -c.id, // Negative ID to distinguish from internal
          courseId: c.id,
          courseCode: c.code,
          courseName: c.name,
          roomId: -1, // External rooms not in our system
          roomName: "External",
          startTime,
          endTime,
          isExternal: true,
          durationMinutes: differenceInMinutes(endTime, startTime),
        };
      });

    return [...internalEvents, ...externalEvents];
  }, [courses, rooms, examEvents]);

  // Generate days array
  const days = useMemo(() => {
    const result: Date[] = [];
    let current = startOfDay(periodStart);
    const end = startOfDay(periodEnd);
    while (current <= end) {
      result.push(current);
      current = addDays(current, 1);
    }
    return result.slice(0, 7); // Show max 7 days
  }, [periodStart, periodEnd]);

  const handleDragStart = useCallback((event: DragStartEvent) => {
    const eventData = event.active.data.current as CalendarEvent;
    if (eventData && !eventData.isExternal) {
      setActiveEvent(eventData);
    }
  }, []);

  const handleDragEnd = useCallback(
    async (event: DragEndEvent) => {
      setActiveEvent(null);

      const { active, over } = event;
      if (!over || !active.data.current) return;

      const draggedEvent = active.data.current as CalendarEvent;
      if (draggedEvent.isExternal) return;

      const dropData = over.data.current as { day: Date; hour: number; roomId: number };
      if (!dropData) return;

      const newStartTime = setMinutes(
        setHours(startOfDay(dropData.day), dropData.hour),
        0
      );

      // Skip if no change
      if (
        isSameDay(newStartTime, draggedEvent.startTime) &&
        newStartTime.getHours() === draggedEvent.startTime.getHours() &&
        dropData.roomId === draggedEvent.roomId
      ) {
        return;
      }

      // Validate the move
      setValidating(true);
      try {
        const result = await validateMove({
          course_id: draggedEvent.courseId,
          new_start_time: newStartTime.toISOString(),
          new_room_id: dropData.roomId,
        });

        if (result.is_valid) {
          toast({
            title: "Move valid",
            description: `${draggedEvent.courseCode} can be moved to this slot.`,
            variant: "success",
          });
          onEventUpdate?.(draggedEvent.id, newStartTime, dropData.roomId);
        } else {
          toast({
            title: "Conflict detected",
            description: result.conflict_reason,
            variant: "destructive",
          });
        }
      } catch (error) {
        toast({
          title: "Validation error",
          description: error instanceof Error ? error.message : "Failed to validate move",
          variant: "destructive",
        });
      } finally {
        setValidating(false);
      }
    },
    [onEventUpdate]
  );

  return (
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
    >
      <div className="flex flex-col h-full">
        {/* Legend */}
        <div className="flex items-center gap-4 px-4 py-2 border-b border-border bg-card/50">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded bg-internal" />
            <span className="text-xs text-muted-foreground">Internal (Draggable)</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded bg-external" />
            <span className="text-xs text-muted-foreground">External (Locked)</span>
          </div>
          {validating && (
            <span className="text-xs text-primary animate-pulse">Validating...</span>
          )}
        </div>

        {/* Calendar Grid */}
        <div className="flex-1 overflow-auto">
          <div className="flex min-w-max">
            {/* Time Column */}
            <div className="w-16 flex-shrink-0 border-r border-border bg-card">
              <div className="sticky top-0 z-10 h-12 bg-card border-b border-border" />
              <div className="sticky top-12 z-10 h-9 bg-card border-b border-border" />
              {HOURS.map((hour) => (
                <div
                  key={hour}
                  className="h-12 flex items-start justify-end pr-2 border-b border-border"
                >
                  <span className="text-xs text-muted-foreground -mt-2">
                    {format(setHours(new Date(), hour), "ha")}
                  </span>
                </div>
              ))}
            </div>

            {/* Day Columns */}
            {days.map((day) => (
              <div key={day.toISOString()} className="flex-1 min-w-[280px]">
                <DayHeader date={day} />
                <div className="flex">
                  {rooms.map((room) => (
                    <RoomColumn
                      key={room.id}
                      room={room}
                      day={day}
                      events={calendarEvents}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Drag Overlay */}
      <DragOverlay>
        {activeEvent && (
          <div
            className="exam-block internal dragging"
            style={{
              height: `${getEventHeight(activeEvent.durationMinutes)}px`,
              width: "120px",
              minHeight: "32px",
            }}
          >
            <div className="flex items-center gap-1 h-full">
              <GripVertical className="h-3 w-3 flex-shrink-0" />
              <span className="font-semibold truncate">{activeEvent.courseCode}</span>
            </div>
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
}
