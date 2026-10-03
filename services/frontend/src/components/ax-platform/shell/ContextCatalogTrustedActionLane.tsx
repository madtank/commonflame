import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, GitBranch, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import type {
  ContextCatalogActionDefinition,
  ContextCatalogActionInvocationInput,
  ContextCatalogActionResult,
  ContextCatalogEntry,
} from "@/lib/context-catalog";

type ContextCatalogTrustedActionLaneProps = {
  entry: ContextCatalogEntry;
  actorLabel?: string | null;
  onInvokeAction: (
    input: ContextCatalogActionInvocationInput,
  ) => Promise<ContextCatalogActionResult> | ContextCatalogActionResult;
};

function shortHash(value?: string | null) {
  if (!value) return null;
  return value.length > 12 ? value.slice(0, 12) : value;
}

function isTrustedAction(action: ContextCatalogActionDefinition) {
  return action.source_lane === "trusted_named_action";
}

function requiresConfirmation(action: ContextCatalogActionDefinition) {
  return action.confirmation === "required";
}

function buildIdempotencyKey(entryId: string, actionId: string) {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `ctxcat-${entryId}-${actionId}-${random}`;
}

export function ContextCatalogTrustedActionLane({
  entry,
  actorLabel = "You",
  onInvokeAction,
}: ContextCatalogTrustedActionLaneProps) {
  const [confirmingAction, setConfirmingAction] =
    useState<ContextCatalogActionDefinition | null>(null);
  const [pendingActionId, setPendingActionId] = useState<string | null>(null);
  const [resultText, setResultText] = useState<string | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [actionConflict, setActionConflict] =
    useState<ContextCatalogEntry["conflict"]>(null);
  const [committedVersions, setCommittedVersions] = useState<{
    artifactVersionId?: string | null;
    baseArtifactVersionId?: string | null;
    baseStateVersionId?: string | null;
    entryId: string;
    stateVersionId?: string | null;
  } | null>(null);

  const trustedActions = useMemo(
    () => (entry.available_actions || []).filter(isTrustedAction),
    [entry.available_actions],
  );
  const propsAdvancedPastLocalCommit = Boolean(
    committedVersions?.entryId === entry.id &&
    ((entry.current_artifact_version_id &&
      entry.current_artifact_version_id !==
        committedVersions.baseArtifactVersionId &&
      entry.current_artifact_version_id !==
        committedVersions.artifactVersionId) ||
      (entry.current_state_version_id &&
        entry.current_state_version_id !==
          committedVersions.baseStateVersionId &&
        entry.current_state_version_id !== committedVersions.stateVersionId)),
  );
  const localVersions =
    committedVersions?.entryId === entry.id && !propsAdvancedPastLocalCommit
      ? committedVersions
      : null;
  const artifactVersion =
    localVersions?.artifactVersionId ||
    entry.current_artifact_version_id ||
    "unknown";
  const stateVersion =
    localVersions?.stateVersionId ||
    entry.current_state_version_id ||
    "unknown";
  const versionHash = shortHash(entry.current_artifact_sha256);

  const invokeAction = async (action: ContextCatalogActionDefinition) => {
    if (!entry.current_context_object_id) {
      setErrorText("Context object is unavailable.");
      return;
    }
    if (artifactVersion === "unknown" || stateVersion === "unknown") {
      setErrorText("Current catalog versions are unavailable.");
      return;
    }

    setPendingActionId(action.id);
    setErrorText(null);
    setResultText(null);
    setActionConflict(null);
    try {
      const result = await onInvokeAction({
        context_object_id: entry.current_context_object_id,
        action_id: action.id,
        source_lane: "trusted_named_action",
        base_artifact_version_id: artifactVersion,
        base_state_version_id: stateVersion,
        idempotency_key: buildIdempotencyKey(entry.id, action.id),
        payload: {},
      });
      if (result.error || result.conflict) {
        setActionConflict(result.conflict || null);
        setErrorText(
          result.error || result.conflict?.error || "Action failed.",
        );
      } else {
        if (result.new_artifact_version_id || result.new_state_version_id) {
          setCommittedVersions({
            artifactVersionId:
              result.new_artifact_version_id || artifactVersion,
            baseArtifactVersionId: artifactVersion,
            baseStateVersionId: stateVersion,
            entryId: entry.id,
            stateVersionId: result.new_state_version_id || stateVersion,
          });
        }
        setResultText(
          result.status
            ? `${action.canonical_label}: ${result.status}`
            : `${action.canonical_label} recorded.`,
        );
      }
    } catch (error) {
      setErrorText(
        error instanceof Error ? error.message : "Action failed to submit.",
      );
    } finally {
      setPendingActionId(null);
      setConfirmingAction(null);
    }
  };

  const handleAction = (action: ContextCatalogActionDefinition) => {
    if (requiresConfirmation(action)) {
      setConfirmingAction(action);
      return;
    }
    void invokeAction(action);
  };
  const visibleConflict = actionConflict || entry.conflict;

  return (
    <section
      data-testid="context-catalog-trusted-lane"
      className="shrink-0 border-b border-slate-200 bg-white px-4 py-3 text-slate-950 shadow-sm dark:border-white/10 dark:bg-slate-950 dark:text-white sm:px-6"
      aria-label="Trusted context actions"
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-cyan-500/25 bg-cyan-500/10 px-2 py-1 text-xs font-semibold text-cyan-700 dark:text-cyan-200">
              <GitBranch className="h-3 w-3" />
              Context Catalog
            </span>
            {entry.status ? (
              <span className="rounded-full border border-slate-200 px-2 py-1 text-xs capitalize text-slate-600 dark:border-white/10 dark:text-slate-300">
                {entry.status}
              </span>
            ) : null}
          </div>
          <h2 className="mt-2 truncate text-sm font-semibold">
            {entry.title || entry.id}
          </h2>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
            <span>{entry.artifact_type || "artifact"}</span>
            <span>Artifact {artifactVersion}</span>
            <span>State {stateVersion}</span>
            {versionHash ? <span>Hash {versionHash}</span> : null}
            <span>Actor {actorLabel || "You"}</span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {trustedActions.length > 0 ? (
            trustedActions.map((action) => (
              <Button
                key={action.id}
                type="button"
                size="sm"
                variant={action.id === "approve" ? "default" : "outline"}
                onClick={() => handleAction(action)}
                disabled={Boolean(pendingActionId)}
                className={cn(
                  "min-w-24",
                  action.id === "approve"
                    ? "bg-cyan-600 text-white hover:bg-cyan-500"
                    : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50 dark:border-white/15 dark:bg-white/[0.04] dark:text-slate-100 dark:hover:bg-white/[0.08]",
                )}
              >
                {pendingActionId === action.id ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : null}
                {action.canonical_label}
              </Button>
            ))
          ) : (
            <span className="text-xs text-slate-500 dark:text-slate-400">
              No trusted actions available.
            </span>
          )}
        </div>
      </div>

      {visibleConflict ? (
        <div
          data-testid="context-catalog-conflict"
          className="mt-3 rounded-lg border border-amber-300/50 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-300/25 dark:bg-amber-300/10 dark:text-amber-100"
        >
          <div className="flex items-center gap-2 font-semibold">
            <AlertTriangle className="h-4 w-4" />
            Version conflict
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs">
            <span>
              Base {visibleConflict.base_artifact_version_id || "unknown"} /{" "}
              {visibleConflict.base_state_version_id || "unknown"}
            </span>
            <span>
              Current {visibleConflict.current_artifact_version_id || "unknown"}{" "}
              / {visibleConflict.current_state_version_id || "unknown"}
            </span>
          </div>
        </div>
      ) : null}

      {resultText ? (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-emerald-700 dark:text-emerald-200">
          <CheckCircle2 className="h-3.5 w-3.5" />
          {resultText}
        </p>
      ) : null}
      {errorText ? (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-rose-600 dark:text-rose-300">
          <AlertTriangle className="h-3.5 w-3.5" />
          {errorText}
        </p>
      ) : null}

      <Dialog
        open={Boolean(confirmingAction)}
        onOpenChange={(open) => {
          if (!open) setConfirmingAction(null);
        }}
      >
        <DialogContent className="max-w-md border border-slate-200 bg-white text-slate-950 dark:border-white/10 dark:bg-slate-950 dark:text-white">
          <DialogTitle>
            Confirm {confirmingAction?.canonical_label || "action"}
          </DialogTitle>
          <p className="text-sm text-slate-600 dark:text-slate-300">
            This trusted action will be committed against artifact{" "}
            {artifactVersion} and state {stateVersion}.
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirmingAction(null)}
            >
              Cancel
            </Button>
            {confirmingAction ? (
              <Button
                type="button"
                onClick={() => void invokeAction(confirmingAction)}
                disabled={pendingActionId === confirmingAction.id}
              >
                {pendingActionId === confirmingAction.id ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : null}
                Confirm {confirmingAction.canonical_label}
              </Button>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}
