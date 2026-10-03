import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  BellOff,
  CheckCircle2,
  Loader2,
  RefreshCw,
} from "lucide-react";

import {
  api,
  type FleetControlState,
  type FleetControlUpdate,
} from "@/lib/api-clean";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/use-toast";
import { cn } from "@/lib/utils";

const QUERY_KEY = ["fleet-control-state"] as const;

function statusCopy(state?: FleetControlState) {
  if (!state) return { label: "Unknown", tone: "muted" as const };
  if (state.emergency_stop)
    return { label: "Emergency stop active", tone: "danger" as const };
  if (state.reminder_silence)
    return { label: "Reminder silence active", tone: "warn" as const };
  return { label: "Fleet enabled", tone: "ok" as const };
}

function formatValue(value?: string | null) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  if (!Number.isNaN(date.getTime())) return date.toLocaleString();
  return value;
}

function StatusPill({ state }: { state?: FleetControlState }) {
  const copy = statusCopy(state);
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5 rounded-full px-3 py-1 text-xs font-semibold",
        copy.tone === "danger" &&
          "border-rose-500/40 bg-rose-500/10 text-rose-200",
        copy.tone === "warn" &&
          "border-amber-500/40 bg-amber-500/10 text-amber-100",
        copy.tone === "ok" &&
          "border-emerald-500/40 bg-emerald-500/10 text-emerald-100",
        copy.tone === "muted" &&
          "border-slate-600/60 bg-slate-800/70 text-slate-300",
      )}
    >
      {copy.tone === "ok" ? (
        <CheckCircle2 className="h-3.5 w-3.5" />
      ) : (
        <AlertTriangle className="h-3.5 w-3.5" />
      )}
      {copy.label}
    </Badge>
  );
}

export function FleetControlPanel() {
  const [reason, setReason] = useState("");
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const stateQuery = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => api.getFleetControlState(),
    staleTime: 0,
    refetchOnMount: "always",
  });

  const updateMutation = useMutation({
    mutationFn: (update: FleetControlUpdate) =>
      api.updateFleetControlState(update),
    onSuccess: (state) => {
      queryClient.setQueryData(QUERY_KEY, state);
      setReason("");
      toast({
        title: "Fleet control updated",
        description: state.transition_id
          ? `Transition ${state.transition_id} recorded.`
          : "The backend returned the latest fleet-control readback.",
      });
    },
    onError: (error) => {
      const message =
        error instanceof Error ? error.message : "Fleet-control update failed.";
      toast({
        title: "Fleet control update failed",
        description: message,
        variant: "destructive",
      });
    },
  });

  const state = stateQuery.data;
  const hasSuccessfulReadback = stateQuery.isSuccess && Boolean(state);
  const hasReason = reason.trim().length >= 3;
  const canSubmit = hasReason && !updateMutation.isPending;
  const canLiftControls = canSubmit && hasSuccessfulReadback;
  const submit = (
    patch: Omit<FleetControlUpdate, "reason" | "transition_id">,
  ) => {
    updateMutation.mutate({
      ...patch,
      reason: reason.trim(),
      transition_id: `frontend-${Date.now().toString(36)}`,
    });
  };

  return (
    <Card
      className="border-amber-500/30 bg-slate-950/70 shadow-none"
      data-testid="fleet-control-panel"
    >
      <CardHeader className="space-y-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-xl text-white">
              <Ban className="h-5 w-5 text-amber-300" />
              Fleet stop/backoff controls
            </CardTitle>
            <CardDescription className="mt-2 max-w-3xl text-slate-300">
              Owner/admin surface for emergency stop and reminder silence
              readback. Use only with an approved process gate; every mutation
              requires a reason and backend audit readback.
            </CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill state={state} />
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => stateQuery.refetch()}
              disabled={stateQuery.isFetching}
              className="border-white/15 bg-white/[0.04] text-white hover:bg-white/10 hover:text-white"
            >
              {stateQuery.isFetching ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" />
              )}
              Refresh
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {stateQuery.isError ? (
          <div
            role="alert"
            className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-100"
          >
            Fleet-control status is unavailable. Do not assume the fleet is
            enabled until backend readback succeeds.
          </div>
        ) : null}

        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
              Dispatch
            </div>
            <div className="mt-2 text-lg font-semibold text-white">
              {state
                ? state.emergency_stop
                  ? "Blocked"
                  : "Allowed"
                : "Unknown"}
            </div>
            <p className="mt-1 text-sm text-slate-300">
              Agent communication is blocked only by emergency stop.
            </p>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
              Reminders
            </div>
            <div className="mt-2 text-lg font-semibold text-white">
              {state
                ? state.emergency_stop || state.reminder_silence
                  ? "Silenced"
                  : "Allowed"
                : "Unknown"}
            </div>
            <p className="mt-1 text-sm text-slate-300">
              Reminders stop when emergency stop or reminder silence is active.
            </p>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
              Last transition
            </div>
            <div className="mt-2 text-sm font-semibold text-white">
              {formatValue(state?.updated_at)}
            </div>
            <p className="mt-1 text-sm text-slate-300">
              Actor: {state?.actor_type || "unknown"}{" "}
              {state?.actor_id ? `/${state.actor_id}` : ""}
            </p>
          </div>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
            Current reason
          </div>
          <p className="mt-2 text-sm text-slate-200">
            {state?.reason || "No active reason recorded."}
          </p>
          {state?.transition_id ? (
            <p className="mt-1 text-xs text-slate-400">
              Transition: {state.transition_id}
            </p>
          ) : null}
        </div>

        <div className="space-y-2">
          <Label htmlFor="fleet-control-reason" className="text-slate-100">
            Required reason for any change
          </Label>
          <Textarea
            id="fleet-control-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Example: Process-approved canary dry run for task_000482; re-enable after verification."
            className="min-h-24 border-white/15 bg-slate-950/80 text-white placeholder:text-slate-500"
          />
          <p className="text-xs text-slate-400">
            Safety gate: no destructive fleet stop, production mutation, merge,
            deploy, or live rollout without explicit approval.
          </p>
        </div>

        <div className="flex flex-wrap gap-3">
          <Button
            type="button"
            variant="destructive"
            disabled={!canSubmit}
            onClick={() => submit({ emergency_stop: true })}
          >
            <Ban className="mr-2 h-4 w-4" />
            Enable emergency stop
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={!canLiftControls}
            onClick={() => submit({ emergency_stop: false })}
          >
            <CheckCircle2 className="mr-2 h-4 w-4" />
            Re-enable dispatch
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={!canSubmit}
            onClick={() => submit({ reminder_silence: true })}
          >
            <BellOff className="mr-2 h-4 w-4" />
            Silence reminders
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={!canLiftControls}
            onClick={() => submit({ reminder_silence: false })}
          >
            <RefreshCw className="mr-2 h-4 w-4" />
            Resume reminders
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
