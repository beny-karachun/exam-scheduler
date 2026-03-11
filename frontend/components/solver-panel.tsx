"use client";

import { useState, useEffect, useCallback } from "react";
import { format, addDays } from "date-fns";
import { Play, Loader2, CheckCircle2, XCircle, AlertCircle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { solveSchedule, getSolverResult, type SolverResultResponse } from "@/lib/api";
import { toast } from "@/components/ui/toaster";

interface SolverPanelProps {
  periodStart: Date;
  periodEnd: Date;
  onSolveComplete?: () => void;
}

type SolverStatus = "idle" | "running" | "success" | "error" | "infeasible";

export function SolverPanel({ periodStart, periodEnd, onSolveComplete }: SolverPanelProps) {
  const [status, setStatus] = useState<SolverStatus>("idle");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [result, setResult] = useState<SolverResultResponse | null>(null);
  const [solveTime, setSolveTime] = useState<number | null>(null);

  const pollResult = useCallback(async (id: string) => {
    try {
      const res = await getSolverResult(id);
      
      if (res.status === "PENDING") {
        // Continue polling
        setTimeout(() => pollResult(id), 1000);
      } else if (res.status === "OPTIMAL" || res.status === "FEASIBLE") {
        setStatus("success");
        setResult(res);
        setSolveTime(res.solve_time_seconds);
        toast({
          title: "Schedule generated",
          description: `Found ${res.status.toLowerCase()} solution with ${res.events.length} events in ${res.solve_time_seconds.toFixed(1)}s`,
          variant: "success",
        });
        onSolveComplete?.();
      } else if (res.status === "INFEASIBLE") {
        setStatus("infeasible");
        setResult(res);
        toast({
          title: "No feasible schedule",
          description: "The constraints cannot be satisfied. See details below.",
          variant: "destructive",
        });
      } else {
        setStatus("error");
        setResult(res);
        toast({
          title: "Solver error",
          description: "An error occurred during optimization.",
          variant: "destructive",
        });
      }
    } catch {
      setStatus("error");
      toast({
        title: "Polling error",
        description: "Failed to fetch solver result.",
        variant: "destructive",
      });
    }
  }, [onSolveComplete]);

  const handleSolve = async () => {
    setStatus("running");
    setResult(null);
    setSolveTime(null);

    try {
      const response = await solveSchedule({
        exam_period_start: periodStart.toISOString(),
        exam_period_end: periodEnd.toISOString(),
        slot_duration_minutes: 30,
      });
      
      setTaskId(response.task_id);
      toast({
        title: "Solver started",
        description: "Optimizing exam schedule...",
      });
      
      // Start polling
      pollResult(response.task_id);
    } catch {
      setStatus("error");
      toast({
        title: "Failed to start solver",
        description: "Could not connect to the backend.",
        variant: "destructive",
      });
    }
  };

  const statusConfig = {
    idle: { icon: Play, label: "Ready", color: "secondary" as const },
    running: { icon: Loader2, label: "Solving...", color: "default" as const },
    success: { icon: CheckCircle2, label: "Solved", color: "default" as const },
    error: { icon: XCircle, label: "Error", color: "destructive" as const },
    infeasible: { icon: AlertCircle, label: "Infeasible", color: "destructive" as const },
  };

  const currentStatus = statusConfig[status];

  return (
    <Card className="bg-card/50">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base">Schedule Solver</CardTitle>
            <CardDescription>
              OR-Tools constraint optimization
            </CardDescription>
          </div>
          <Badge variant={currentStatus.color} className="gap-1">
            <currentStatus.icon
              className={`h-3 w-3 ${status === "running" ? "animate-spin" : ""}`}
            />
            {currentStatus.label}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-muted-foreground">Period Start</p>
            <p className="font-medium">{format(periodStart, "MMM d, yyyy")}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Period End</p>
            <p className="font-medium">{format(periodEnd, "MMM d, yyyy")}</p>
          </div>
        </div>

        {solveTime !== null && (
          <div className="text-sm">
            <p className="text-muted-foreground">Solve Time</p>
            <p className="font-medium">{solveTime.toFixed(2)} seconds</p>
          </div>
        )}

        {result?.events && result.events.length > 0 && (
          <div className="text-sm">
            <p className="text-muted-foreground">Scheduled Events</p>
            <p className="font-medium text-primary">{result.events.length} exams</p>
          </div>
        )}

        {result?.infeasibility_explanation && (
          <div className="p-3 rounded-md bg-destructive/10 border border-destructive/20">
            <p className="text-sm font-medium text-destructive">Infeasibility Details</p>
            <p className="text-xs text-muted-foreground mt-1">
              {JSON.stringify(result.infeasibility_explanation, null, 2)}
            </p>
          </div>
        )}

        <Button
          onClick={handleSolve}
          disabled={status === "running"}
          className="w-full"
        >
          {status === "running" ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Optimizing...
            </>
          ) : (
            <>
              <Play className="h-4 w-4" />
              Generate Schedule
            </>
          )}
        </Button>
      </CardContent>
    </Card>
  );
}
