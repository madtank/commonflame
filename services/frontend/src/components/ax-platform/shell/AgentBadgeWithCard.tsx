/**
 * AgentBadgeWithCard — agent identity badge in message headers that
 * opens an agent card dialog on click (instead of inserting a mention).
 *
 * The dialog shows the full agent summary (trust, owner, kill switch)
 * via the shared AgentSummaryBody component.
 */

import { useState, useEffect } from "react";
import { ChevronRight } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { AgentSummaryBody } from "@/components/AvatarName";
import { fetchAgentSummary, type AgentSummary } from "@/lib/api-clean";
import { useAuth } from "@/contexts/AuthContext";
import { PresenceDot } from "@/components/ax-platform/shell/PresenceDot";
import type { PresenceStatus } from "@/hooks/usePresence";
import {
  agentControlService,
  type AgentControlState,
} from "@/services/agentControlService";

interface AgentBadgeWithCardProps {
  name: string;
  emoji?: string;
  subtle?: boolean;
  agentId?: string;
  spaceId?: string;
  onMention?: (name: string) => void;
  presenceStatus?: PresenceStatus;
  presenceFreshness?: number;
}

function isSpaceOwnedAgentSummary(
  summary: AgentSummary | null | undefined,
  fallbackName: string,
) {
  const normalizedName = fallbackName.replace(/^@/, "").toLowerCase();
  return (
    summary?.origin === "space_agent" ||
    summary?.agent_type === "space_agent" ||
    summary?.runtime_kind === "space_agent" ||
    (normalizedName === "ax" && summary?.owner?.handle === "__system__")
  );
}

function normalizeEmbeddedControlState(
  control: AgentSummary["control"],
): AgentControlState | null {
  if (!control) return null;
  return {
    is_disabled: Boolean(control.is_disabled),
    disabled_reason: control.disabled_reason ?? undefined,
    disabled_by: control.disabled_by ?? [],
    disabled_until: control.disabled_until ?? null,
    no_reply: control.no_reply,
    no_reply_reason: control.no_reply_reason ?? undefined,
    no_reply_by: control.no_reply_by ?? [],
    no_reply_until: control.no_reply_until ?? null,
    routing_only: control.routing_only,
    routing_only_reason: control.routing_only_reason ?? undefined,
    routing_only_by: control.routing_only_by ?? [],
    routing_only_until: control.routing_only_until ?? null,
  };
}

export function AgentBadgeWithCard({
  name,
  emoji,
  subtle = true,
  agentId,
  spaceId,
  onMention,
  presenceStatus,
  presenceFreshness,
}: AgentBadgeWithCardProps) {
  const [open, setOpen] = useState(false);
  const { user } = useAuth();
  const currentUserId = user?.attributes?.id;
  const currentUserHandle = (user?.attributes?.handle || user?.username || "")
    .replace(/^@/, "")
    .toLowerCase();

  // Agent summary from API
  const [summary, setSummary] = useState<AgentSummary | null>(null);

  // Control state for kill switch
  const [controlState, setControlState] = useState<AgentControlState | null>(
    null,
  );
  const [controlLoading, setControlLoading] = useState(false);
  // Authoritative: server allowed us to GET /agents/{id}/control. The same
  // permission check gates the PATCH, so a successful fetch proves the viewer
  // can also toggle the kill switch — don't double-gate client-side.
  const [canControl, setCanControl] = useState(false);

  // Best-effort ownership signal for the summary body (drives copy/affordance
  // decisions elsewhere). Accept id match OR handle match so we remain usable
  // even when the summary endpoint returns differently-shaped owner records.
  const ownerIdMatch = Boolean(
    currentUserId && summary?.owner?.id && currentUserId === summary.owner.id,
  );
  const ownerHandleMatch = Boolean(
    currentUserHandle &&
    summary?.owner?.handle &&
    summary.owner.handle.replace(/^@/, "").toLowerCase() === currentUserHandle,
  );
  const isSpaceOwnedAgent = isSpaceOwnedAgentSummary(summary, name);
  const isOwnAgent = ownerIdMatch || ownerHandleMatch || canControl;
  const canRenderControl = isOwnAgent || (isSpaceOwnedAgent && canControl);

  // Fetch agent summary when dialog opens
  useEffect(() => {
    if (!open || !agentId || !spaceId) return;

    let cancelled = false;
    fetchAgentSummary(agentId, spaceId)
      .then((data) => {
        if (cancelled) return;
        setSummary(data);
        const embeddedControl = normalizeEmbeddedControlState(data.control);
        if (embeddedControl && isSpaceOwnedAgentSummary(data, name)) {
          setControlState(embeddedControl);
          setCanControl(true);
        }
      })
      .catch((err) => {
        console.error("Failed to fetch agent summary:", err);
      });

    return () => {
      cancelled = true;
    };
  }, [open, agentId, spaceId]);

  // Fetch control state whenever the dialog is open and we have an agent id.
  // The backend (/agents/{id}/control) requires the viewer to be the owner or
  // an admin, so a successful fetch is itself the authorization signal — we
  // no longer gate on a client-side ownership guess that was racing/breaking
  // for sentinels with mismatched owner id shapes.
  useEffect(() => {
    if (!open || !agentId) return;
    if (spaceId && !summary) return;

    const embeddedControl = normalizeEmbeddedControlState(summary?.control);
    if (embeddedControl && isSpaceOwnedAgentSummary(summary, name)) {
      setControlState(embeddedControl);
      setCanControl(true);
      setControlLoading(false);
      return;
    }

    let cancelled = false;
    const fetchControlState = async () => {
      setControlLoading(true);
      try {
        const state = await agentControlService.getControlState(agentId);
        if (cancelled) return;
        setControlState(state);
        setCanControl(true);
      } catch (error) {
        if (cancelled) return;
        // 403 / 404 here just means the viewer can't control this agent; that
        // is the normal case for non-owners. Keep controlState null so the
        // kill switch is not rendered, and don't surface this as a UI error.
        console.debug("Agent control state not available for viewer:", error);
        setControlState(null);
        setCanControl(false);
      } finally {
        if (!cancelled) setControlLoading(false);
      }
    };

    fetchControlState();
    return () => {
      cancelled = true;
    };
  }, [open, agentId, spaceId, summary, name]);

  // Reset state when dialog closes
  useEffect(() => {
    if (!open) {
      setSummary(null);
      setControlState(null);
      setCanControl(false);
    }
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="group cursor-pointer rounded-full text-left transition hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/40 dark:focus-visible:ring-cyan-300/50"
        aria-label={`View ${name} details`}
        title={`View ${name} details`}
      >
        <div
          className={cn(
            "inline-flex max-w-full min-w-0 items-center gap-2 rounded-full border px-2.5 py-1 shadow-sm transition-all duration-150",
            subtle
              ? "border-slate-300 bg-white text-slate-800 hover:-translate-y-px hover:border-cyan-300 hover:bg-cyan-50 hover:text-cyan-900 hover:shadow-md dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:shadow-none dark:hover:border-cyan-300/25 dark:hover:bg-cyan-400/[0.08] dark:hover:text-white dark:hover:shadow-none"
              : "border-cyan-300 bg-cyan-50 text-cyan-900 hover:-translate-y-px hover:border-cyan-400 hover:bg-cyan-100 hover:shadow-md dark:border-cyan-300/15 dark:bg-cyan-400/[0.08] dark:text-cyan-50 dark:hover:border-cyan-300/30 dark:hover:bg-cyan-400/[0.14] dark:hover:shadow-none",
          )}
        >
          {presenceStatus ? (
            <PresenceDot
              status={presenceStatus}
              freshness={presenceFreshness}
              className="mr-0.5"
            />
          ) : null}
          <span className="text-sm leading-none">{emoji || "🤖"}</span>
          <span className="max-w-[min(42vw,12rem)] truncate text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-900 transition-colors group-hover:text-cyan-950 dark:text-slate-100 dark:group-hover:text-white sm:max-w-none">
            {name}
          </span>
          <ChevronRight
            className="h-3.5 w-3.5 shrink-0 text-slate-400 opacity-80 transition-all duration-150 group-hover:translate-x-0.5 group-hover:text-cyan-700 group-hover:opacity-100 dark:text-slate-500 dark:group-hover:text-cyan-200"
            aria-hidden="true"
          />
        </div>
      </button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-3">
              <span className="text-lg">{emoji || "🤖"}</span>
              <span>{name}</span>
            </DialogTitle>
          </DialogHeader>
          <AgentSummaryBody
            summary={summary ?? undefined}
            agentHandle={name}
            onMessageAgent={onMention}
            agentId={agentId}
            agentName={name}
            isOwnAgent={canRenderControl}
            controlState={controlState}
            controlLoading={controlLoading}
            onControlToggle={setControlState}
            onClose={() => setOpen(false)}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}
