"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DoorOpen, Users } from "lucide-react";
import type { Room } from "@/lib/api";

interface RoomsPanelProps {
  rooms: Room[];
}

export function RoomsPanel({ rooms }: RoomsPanelProps) {
  const totalCapacity = rooms.reduce((acc, r) => acc + r.exam_capacity, 0);

  return (
    <Card className="bg-card/50">
      <CardHeader>
        <CardTitle className="text-base">Exam Rooms</CardTitle>
        <CardDescription>
          {rooms.length} rooms, {totalCapacity} total seats
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 max-h-[300px] overflow-y-auto">
        {rooms.map((room) => (
          <div
            key={room.id}
            className="flex items-center gap-3 p-2 rounded-md bg-secondary/50 border border-border"
          >
            <div className="flex items-center justify-center w-8 h-8 rounded-md bg-muted">
              <DoorOpen className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="font-medium text-sm truncate">{room.name}</p>
            </div>
            <Badge variant="outline" className="gap-1">
              <Users className="h-3 w-3" />
              {room.exam_capacity}
            </Badge>
          </div>
        ))}

        {rooms.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-4">
            No rooms found
          </p>
        )}
      </CardContent>
    </Card>
  );
}
