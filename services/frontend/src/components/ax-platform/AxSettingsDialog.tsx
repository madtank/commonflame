import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AxiosError } from "axios";
import {
  AlertTriangle,
  Bot,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  LayoutGrid,
  Loader2,
  LogOut,
  Monitor,
  Moon,
  Radio,
  Sun,
  RefreshCw,
  Shield,
  Siren,
  User2,
} from "lucide-react";
import { api, apiClient } from "@/lib/api-clean";
import { agentControlService } from "@/services/agentControlService";
import { formatSpaceLabel } from "@/lib/display-utils";
import {
  DEFAULT_HIDDEN_WIDGETS,
  MCP_SURFACE_POLICY_RULES,
} from "@/lib/space-agent-surface-policy";
import {
  createPersonalAccessKey,
  listPatScopeAgents,
  listPersonalAccessKeys,
  revokePersonalAccessKey,
  rotatePersonalAccessKey,
  type AgentKeyCreateResponse,
  type AgentKeyInfo,
  type AgentSummary,
  type PatScopeAgent,
  type PersonalAccessKey,
  type PersonalAccessKeyAgentScope,
  type PersonalAccessKeyCreateResponse,
} from "@/lib/api-clean";
import { copyToClipboard } from "@/lib/clipboard-utils";
import { PresenceDot } from "@/components/ax-platform/shell/PresenceDot";
import { WorkspaceInvitation } from "./WorkspaceInvitation";
import { cn } from "@/lib/utils";
import { getAgentRuntimeDisplay } from "@/lib/agent-utils";
import { usePresence } from "@/hooks/usePresence";
import { useUserSettings } from "@/hooks/useUserSettings";
import { useToast } from "@/components/ui/use-toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type UserProfile = {
  id?: string;
  username?: string;
  email?: string;
  full_name?: string;
  role?: string;
  admin?: boolean;
  is_admin?: boolean;
  platform_features?: {
    subscription_tier?: string;
  };
};

type AxSettingsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentSpaceId?: string | null;
  currentSpaceName: string;
  username?: string | null;
  onLogout?: () => void;
  hiddenWidgets?: Set<string>;
  onToggleWidget?: (policyId: string) => void;
};

type ViolationSeverity = "critical" | "high" | "medium" | "low";

type ViolationStatus = "open" | "in_review" | "resolved";

type ViolationsApiItem = {
  id?: string | number | null;
  violation_id?: string | number | null;
  title?: string | null;
  type?: string | null;
  category?: string | null;
  severity?: string | null;
  status?: string | null;
  owner?: string | null;
  owner_name?: string | null;
  assigned_to?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
  summary?: string | null;
  description?: string | null;
  details?: string | null;
};

type ViolationsApiResponse = {
  violations?: ViolationsApiItem[];
  items?: ViolationsApiItem[];
};

type ViolationItem = {
  id: string;
  title: string;
  severity: ViolationSeverity;
  status: ViolationStatus;
  owner: string;
  updatedAt: string;
  summary: string;
};

function normalizeViolationSeverity(value?: string | null): ViolationSeverity {
  switch ((value || "").toLowerCase()) {
    case "critical":
    case "high":
    case "medium":
    case "low":
      return value!.toLowerCase() as ViolationSeverity;
    default:
      return "low";
  }
}

function normalizeViolationStatus(value?: string | null): ViolationStatus {
  switch ((value || "").toLowerCase()) {
    case "open":
    case "in_review":
    case "resolved":
      return value!.toLowerCase() as ViolationStatus;
    default:
      return "open";
  }
}

function normalizeViolationItem(
  item: ViolationsApiItem,
  index: number,
): ViolationItem {
  const rawTitle = item.title || item.type || item.category;
  const rawOwner = item.owner || item.owner_name || item.assigned_to;
  const rawSummary = item.summary || item.description || item.details;
  const rawId = item.id ?? item.violation_id;

  return {
    id: String(rawId || `violation-${index + 1}`),
    title: rawTitle?.trim() || `Violation ${index + 1}`,
    severity: normalizeViolationSeverity(item.severity),
    status: normalizeViolationStatus(item.status),
    owner: rawOwner?.trim() || "Unassigned",
    updatedAt: item.updated_at || item.created_at || "",
    summary: rawSummary?.trim() || "No summary provided.",
  };
}

function getViolationTone(severity: ViolationSeverity) {
  switch (severity) {
    case "critical":
      return "border-rose-500/40 bg-rose-500/15 text-gray-900 dark:text-rose-100";
    case "high":
      return "border-amber-500/40 bg-amber-500/15 text-gray-900 dark:text-amber-100";
    case "medium":
      return "border-cyan-500/40 bg-cyan-500/15 text-gray-900 dark:text-cyan-100";
    case "low":
    default:
      return "border-border bg-muted text-gray-700 dark:text-muted-foreground";
  }
}

function getViolationStatusTone(status: ViolationStatus) {
  switch (status) {
    case "open":
      return "border-rose-500/30 bg-rose-500/15 text-gray-900 dark:text-rose-100";
    case "in_review":
      return "border-amber-500/30 bg-amber-500/15 text-gray-900 dark:text-amber-100";
    case "resolved":
    default:
      return "border-emerald-500/30 bg-emerald-500/15 text-gray-900 dark:text-emerald-100";
  }
}

function formatViolationLabel(value: string) {
  return value.replace(/_/g, " ");
}

function formatTimestamp(value?: string | null) {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatRelativeActivity(value?: string | null) {
  if (!value) return "No recent activity";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  const diffMs = Date.now() - date.getTime();
  const diffMinutes = Math.max(0, Math.floor(diffMs / 60000));
  if (diffMinutes < 1) return "Active just now";
  if (diffMinutes < 60) return `Active ${diffMinutes}m ago`;

  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `Active ${diffHours}h ago`;

  const diffDays = Math.floor(diffHours / 24);
  return `Active ${diffDays}d ago`;
}

function getManagedAgentStatus(agent: AgentSummary) {
  if (agent.control?.is_disabled) {
    return {
      label: "Disabled",
      className:
        "border-rose-500/30 bg-rose-500/15 text-gray-900 dark:text-rose-100",
    };
  }

  const normalizedStatus = (agent.status || "").trim().toLowerCase();
  if (normalizedStatus === "online" || normalizedStatus === "active") {
    return {
      label: normalizedStatus === "online" ? "Online" : "Active",
      className:
        "border-emerald-500/30 bg-emerald-500/15 text-gray-900 dark:text-emerald-100",
    };
  }

  if (normalizedStatus) {
    return {
      label: normalizedStatus.replace(/_/g, " "),
      className:
        "border-border bg-background text-gray-600 dark:text-muted-foreground",
    };
  }

  return {
    label: "Unknown",
    className: "border-border bg-background text-muted-foreground",
  };
}

function getManagedAgentDisabledCopy(agent: AgentSummary) {
  if (!agent.control?.is_disabled) return null;
  const reason = agent.control.disabled_reason?.trim();
  if (reason) return reason;
  return "This agent is disabled and will not accept new work until it is re-enabled.";
}

function getPatSecret(result: PersonalAccessKeyCreateResponse | null) {
  return result?.token || result?.key || result?.raw_token || "";
}

function isEndpointUnavailable(error: unknown) {
  return (error as AxiosError | undefined)?.response?.status === 404;
}

function isAgentM2mCredentialsEnabled() {
  return import.meta.env.VITE_ENABLE_AGENT_M2M_CREDENTIALS === "true";
}

function normalizeAgentScope(
  key?: Pick<PersonalAccessKey, "agent_scope" | "allowed_agent_ids"> | null,
): PersonalAccessKeyAgentScope {
  if (
    key?.agent_scope === "all" ||
    key?.agent_scope === "user" ||
    key?.agent_scope === "unbound"
  ) {
    return key.agent_scope;
  }
  if (key?.allowed_agent_ids?.length) return "agents";
  return "all";
}

function getAgentScopeLabel(scope: PersonalAccessKeyAgentScope) {
  switch (scope) {
    case "user":
      return "User only";
    case "agents":
      return "Selected agents";
    case "unbound":
      return "Unbound";
    case "all":
    default:
      return "All access";
  }
}

function renderAllowedAgents(
  key: PersonalAccessKey,
  agentMap: Map<string, string>,
) {
  const scope = normalizeAgentScope(key);
  if (scope === "user") return "User only. Agent targeting is blocked.";
  if (scope === "unbound")
    return "Unbound — registers and binds to one agent on first use.";

  const allowed = key.allowed_agent_ids || [];
  if (!allowed.length) return "All visible agents";
  return allowed.map((id) => agentMap.get(id) || id).join(", ");
}

function getPatAgentLabel(agent: PatScopeAgent) {
  return agent.name || agent.agent_name || agent.id;
}

function getPatAgentPresenceKey(agent: PatScopeAgent) {
  return (agent.agent_name || agent.name || "").replace(/^@/, "").trim();
}

export function AxSettingsDialog({
  open,
  onOpenChange,
  currentSpaceId,
  currentSpaceName,
  username,
  onLogout,
  hiddenWidgets,
  onToggleWidget,
}: AxSettingsDialogProps) {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { settings, saving, updateSettings } = useUserSettings();
  const { getStatus: getPresenceStatus, getFreshness: getPresenceFreshness } =
    usePresence(currentSpaceId);
  const [newKeyName, setNewKeyName] = useState("");
  const [scopeMode, setScopeMode] =
    useState<PersonalAccessKeyAgentScope>("agents");
  const [tokenType] = useState<"user" | "agent">("agent");
  const [enrollmentMode, setEnrollmentMode] = useState(false);
  const [tokenAudience, setTokenAudience] = useState<"cli" | "mcp" | "both">(
    "cli",
  );
  const [showCreatedToken, setShowCreatedToken] = useState(false);
  const [agentFilter, setAgentFilter] = useState("");
  const [selectedAgentIds, setSelectedAgentIds] = useState<string[]>([]);
  const [selectedBoundAgentId, setSelectedBoundAgentId] = useState<
    string | null
  >(null);
  const [unboundMode, setUnboundMode] = useState(false);
  const [revealedCreatedKey, setRevealedCreatedKey] =
    useState<PersonalAccessKeyCreateResponse | null>(null);
  const [revealedRotatedKeys, setRevealedRotatedKeys] = useState<
    Record<string, PersonalAccessKeyCreateResponse>
  >({});
  const [managedAgentFilter, setManagedAgentFilter] = useState("");
  const [selectedManagedAgentId, setSelectedManagedAgentId] = useState<
    string | null
  >(null);
  const [newAgentKeyLabel, setNewAgentKeyLabel] = useState("");
  const [revealedCreatedAgentKeys, setRevealedCreatedAgentKeys] = useState<
    Record<string, AgentKeyCreateResponse>
  >({});
  const [revealedRotatedAgentKeys, setRevealedRotatedAgentKeys] = useState<
    Record<string, AgentKeyCreateResponse>
  >({});
  const agentM2mCredentialsEnabled = isAgentM2mCredentialsEnabled();

  const profileQuery = useQuery({
    queryKey: ["ax-settings", "profile"],
    queryFn: async () => (await api.getUserProfile()) as UserProfile | null,
    enabled: open,
  });

  const agentsQuery = useQuery({
    queryKey: ["ax-settings", "pat-agents", currentSpaceId],
    queryFn: () => listPatScopeAgents({ spaceId: currentSpaceId }),
    enabled: open,
  });

  const keysQuery = useQuery({
    queryKey: ["ax-settings", "pat-keys"],
    queryFn: listPersonalAccessKeys,
    enabled: open,
  });

  const managedAgentsQuery = useQuery({
    queryKey: ["ax-settings", "managed-agents"],
    queryFn: async () => {
      const result = await api.getAgents({
        owner: "me",
        limit: 100,
        sort: "activity",
      });
      return (result.agents || []) as AgentSummary[];
    },
    enabled: open && agentM2mCredentialsEnabled,
  });

  const violationsQuery = useQuery({
    queryKey: ["ax-settings", "violations"],
    queryFn: async () => {
      const { data: response } = await apiClient.get(
        "/api/v1/credentials/violations",
      );
      const items = Array.isArray(response)
        ? response
        : response?.violations || response?.items || [];
      return items.map(normalizeViolationItem);
    },
    enabled: open,
  });

  const patAgents = useMemo(() => {
    return (agentsQuery.data || []).filter(
      (agent: PatScopeAgent) => agent.agent_type !== "user",
    );
  }, [agentsQuery.data]);

  const agentNameMap = useMemo(
    () => new Map(patAgents.map((agent) => [agent.id, agent.name])),
    [patAgents],
  );

  const filteredPatAgents = useMemo(() => {
    const query = agentFilter.trim().toLowerCase();
    if (!query) return patAgents;

    return patAgents.filter((agent) => {
      const haystack = `${agent.name} ${agent.id}`.toLowerCase();
      return haystack.includes(query);
    });
  }, [agentFilter, patAgents]);

  const managedAgents = useMemo(() => {
    const rows = (managedAgentsQuery.data || []).filter(
      (agent) => agent && agent.id && agent.name,
    );
    const query = managedAgentFilter.trim().toLowerCase();
    if (!query) return rows;

    return rows.filter((agent) => {
      const haystack = `${agent.name} ${agent.id}`.toLowerCase();
      return haystack.includes(query);
    });
  }, [managedAgentFilter, managedAgentsQuery.data]);

  const selectedManagedAgent = useMemo(() => {
    return (
      managedAgents.find((agent) => agent.id === selectedManagedAgentId) ||
      managedAgents[0] ||
      null
    );
  }, [managedAgents, selectedManagedAgentId]);

  const agentKeysQuery = useQuery({
    queryKey: ["ax-settings", "agent-keys", selectedManagedAgent?.id],
    queryFn: () => api.listAgentKeys(selectedManagedAgent!.id),
    enabled: open && agentM2mCredentialsEnabled && !!selectedManagedAgent?.id,
  });

  const invalidatePatQueries = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["ax-settings", "pat-keys"] }),
      queryClient.invalidateQueries({
        queryKey: ["ax-settings", "pat-agents", currentSpaceId],
      }),
    ]);
  };

  const invalidateAgentKeyQuery = async (agentId: string) => {
    await queryClient.invalidateQueries({
      queryKey: ["ax-settings", "agent-keys", agentId],
    });
  };

  const createKeyMutation = useMutation({
    mutationFn: () =>
      createPersonalAccessKey({
        name: newKeyName.trim(),
        // AUTH-SPEC-001: User tokens use "user" scope, Agent tokens bind or enroll
        agent_scope:
          tokenType === "agent"
            ? enrollmentMode
              ? "unbound"
              : "agents"
            : "user",
        allowed_agent_ids:
          tokenType === "agent" && !enrollmentMode && selectedBoundAgentId
            ? [selectedBoundAgentId]
            : undefined,
        bound_agent_id:
          tokenType === "agent" && !enrollmentMode
            ? (selectedBoundAgentId ?? undefined)
            : undefined,
        audience: tokenAudience,
      }),
    onSuccess: async (data) => {
      setRevealedCreatedKey(data);
      setShowCreatedToken(false);
      setNewKeyName("");
      // Keep token type so user can create multiple of the same kind
      setEnrollmentMode(false);
      setScopeMode("agents");
      setUnboundMode(false);
      setAgentFilter("");
      setSelectedAgentIds([]);
      setSelectedBoundAgentId(null);
      await invalidatePatQueries();
      toast({
        title: "PAT created",
        description: "Save the token now. It is only shown once.",
      });
    },
    onError: (error: unknown) => {
      toast({
        title: "Failed to create PAT",
        description: isEndpointUnavailable(error)
          ? "PAT endpoints are not available in this backend yet."
          : "The backend rejected the key creation request.",
        variant: "destructive",
      });
    },
  });

  const rotateKeyMutation = useMutation({
    mutationFn: async (key: PersonalAccessKey) => ({
      sourceKey: key,
      rotatedKey: await rotatePersonalAccessKey(key.credential_id),
    }),
    onSuccess: async ({ sourceKey, rotatedKey }) => {
      queryClient.setQueryData<PersonalAccessKey[]>(
        ["ax-settings", "pat-keys"],
        (current = []) =>
          current.map((key) =>
            key.credential_id === sourceKey.credential_id
              ? {
                  ...sourceKey,
                  credential_id: rotatedKey.credential_id,
                  created_at: rotatedKey.created_at,
                  last_used_at: null,
                  revoked_at: null,
                }
              : key,
          ),
      );
      setRevealedRotatedKeys((current) => ({
        ...current,
        [rotatedKey.credential_id]: rotatedKey,
      }));
      toast({
        title: "PAT rotated",
        description:
          "The replacement token is shown on the rotated credential.",
      });
    },
    onError: () => {
      toast({
        title: "Failed to rotate PAT",
        description: "The backend rejected the rotate request.",
        variant: "destructive",
      });
    },
  });

  const revokeKeyMutation = useMutation({
    mutationFn: revokePersonalAccessKey,
    onSuccess: async () => {
      await invalidatePatQueries();
      toast({
        title: "PAT revoked",
        description: "That credential can no longer be used.",
      });
    },
    onError: () => {
      toast({
        title: "Failed to revoke PAT",
        description: "The backend rejected the revoke request.",
        variant: "destructive",
      });
    },
  });

  const createAgentKeyMutation = useMutation({
    mutationFn: ({ agentId, label }: { agentId: string; label?: string }) =>
      api.createAgentKey(agentId, label?.trim() ? { label: label.trim() } : {}),
    onSuccess: async (data, variables) => {
      setNewAgentKeyLabel("");
      setRevealedCreatedAgentKeys((current) => ({
        ...current,
        [variables.agentId]: data,
      }));
      await invalidateAgentKeyQuery(variables.agentId);
      toast({
        title: "Agent credential created",
        description: "Save the client secret now. It is only shown once.",
      });
    },
    onError: (error: unknown) => {
      toast({
        title: "Failed to create agent credential",
        description: isEndpointUnavailable(error)
          ? "Agent credential endpoints are not available in this backend yet."
          : "The backend rejected the credential request.",
        variant: "destructive",
      });
    },
  });

  const rotateAgentKeyMutation = useMutation({
    mutationFn: async ({
      agentId,
      keyId,
    }: {
      agentId: string;
      keyId: string;
    }) => ({
      agentId,
      rotatedKey: await api.rotateAgentKey(agentId, keyId),
    }),
    onSuccess: async ({ agentId, rotatedKey }) => {
      setRevealedRotatedAgentKeys((current) => ({
        ...current,
        [rotatedKey.key_id]: rotatedKey,
      }));
      await invalidateAgentKeyQuery(agentId);
      toast({
        title: "Agent credential rotated",
        description: "The replacement client secret is shown once below.",
      });
    },
    onError: (error: unknown) => {
      toast({
        title: "Failed to rotate agent credential",
        description: isEndpointUnavailable(error)
          ? "Agent credential endpoints are not available in this backend yet."
          : "The backend rejected the rotation request.",
        variant: "destructive",
      });
    },
  });

  const revokeAgentKeyMutation = useMutation({
    mutationFn: ({ agentId, keyId }: { agentId: string; keyId: string }) =>
      api.revokeAgentKey(agentId, keyId),
    onSuccess: async (_data, variables) => {
      await invalidateAgentKeyQuery(variables.agentId);
      toast({
        title: "Agent credential revoked",
        description: "That credential can no longer mint new tokens.",
      });
    },
    onError: (error: unknown) => {
      toast({
        title: "Failed to revoke agent credential",
        description: isEndpointUnavailable(error)
          ? "Agent credential endpoints are not available in this backend yet."
          : "The backend rejected the revoke request.",
        variant: "destructive",
      });
    },
  });

  const enableManagedAgentMutation = useMutation({
    mutationFn: (agentId: string) => agentControlService.enableAgent(agentId),
    onSuccess: async (controlState, agentId) => {
      queryClient.setQueryData<AgentSummary[]>(
        ["ax-settings", "managed-agents"],
        (current = []) =>
          current.map((agent) =>
            agent.id === agentId ? { ...agent, control: controlState } : agent,
          ),
      );
      await queryClient.invalidateQueries({
        queryKey: ["ax-settings", "managed-agents"],
      });
      toast({
        title: "Agent re-enabled",
        description: "The agent can accept new work again.",
      });
    },
    onError: () => {
      toast({
        title: "Failed to re-enable agent",
        description: "The backend rejected the re-enable request.",
        variant: "destructive",
      });
    },
  });

  const handleAgentToggle = (agentId: string, checked: boolean) => {
    setSelectedAgentIds((current) =>
      checked ? [...current, agentId] : current.filter((id) => id !== agentId),
    );
  };

  const requiresAgentSelection = scopeMode === "agents" && !unboundMode;

  const handleCopyValue = async (value: string, noun: string) => {
    if (!value) return;
    const copied = await copyToClipboard(value);
    toast({
      title: copied ? `${noun} copied` : "Copy fallback opened",
      description: copied
        ? `The ${noun.toLowerCase()} is now on your clipboard.`
        : "Clipboard access was blocked, so a manual copy fallback was opened.",
    });
  };

  const saveSetting = async (
    patch: { compact_mode: boolean } | { ai_auto_summarize: boolean },
  ) => {
    try {
      await updateSettings(patch);
      toast({
        title: "Settings saved",
        description: "Your preferences were updated.",
      });
    } catch {
      toast({
        title: "Settings failed",
        description: "The preference could not be saved.",
        variant: "destructive",
      });
    }
  };

  const profile = profileQuery.data;
  const keyRows = (keysQuery.data || []).filter((key) => !key.revoked_at);
  const managedAgentKeyRows = [...(agentKeysQuery.data || [])].sort((a, b) =>
    b.created_at.localeCompare(a.created_at),
  );
  const patUnavailable = isEndpointUnavailable(keysQuery.error);
  const patAgentsUnavailable = isEndpointUnavailable(agentsQuery.error);
  const managedAgentsUnavailable = isEndpointUnavailable(
    managedAgentsQuery.error,
  );
  const managedAgentsLoadFailed =
    managedAgentsQuery.isError && !managedAgentsUnavailable;
  const agentCredentialsUnavailable = isEndpointUnavailable(
    agentKeysQuery.error,
  );
  const createdAgentKey = selectedManagedAgent
    ? revealedCreatedAgentKeys[selectedManagedAgent.id] || null
    : null;
  const isAdminProfile = Boolean(
    profile?.role === "admin" ||
    profile?.role === "super_admin" ||
    profile?.admin ||
    profile?.is_admin,
  );
  const subscriptionTier = profile?.platform_features?.subscription_tier;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[88vh] max-h-[88vh] max-w-5xl flex-col overflow-hidden border border-border bg-background p-0 text-foreground shadow-[0_32px_120px_-48px_rgba(8,145,178,0.28)]">
        <DialogHeader className="border-b border-border px-6 py-5 text-left">
          <DialogTitle className="flex items-center gap-2 text-xl text-foreground">
            <Shield className="h-5 w-5 text-cyan-300" />
            Settings
          </DialogTitle>
          <DialogDescription className="text-muted-foreground">
            Profile preferences, credentials, and agent management for{" "}
            {currentSpaceName}.
          </DialogDescription>
        </DialogHeader>

        <Tabs
          defaultValue="profile"
          className="flex flex-1 min-h-0 flex-col overflow-hidden"
        >
          <div className="border-b border-border px-6 py-3">
            <TabsList className="bg-muted text-gray-700 dark:text-slate-300">
              <TabsTrigger
                value="profile"
                className="gap-2 text-gray-700 dark:text-slate-300 data-[state=active]:bg-background data-[state=active]:text-foreground"
              >
                <User2 className="h-4 w-4" />
                Profile
              </TabsTrigger>
              <TabsTrigger
                value="access"
                className="gap-2 text-gray-700 dark:text-slate-300 data-[state=active]:bg-background data-[state=active]:text-foreground"
              >
                <KeyRound className="h-4 w-4" />
                Credentials
              </TabsTrigger>
              {agentM2mCredentialsEnabled ? (
                <TabsTrigger
                  value="agents"
                  className="gap-2 text-gray-700 dark:text-slate-300 data-[state=active]:bg-background data-[state=active]:text-foreground"
                >
                  <Bot className="h-4 w-4" />
                  Agents
                </TabsTrigger>
              ) : null}
              <TabsTrigger
                value="widgets"
                className="gap-2 text-gray-700 dark:text-slate-300 data-[state=active]:bg-background data-[state=active]:text-foreground"
              >
                <LayoutGrid className="h-4 w-4" />
                Widgets
              </TabsTrigger>
              <TabsTrigger
                value="monitor"
                className="gap-2 text-gray-700 dark:text-slate-300 data-[state=active]:bg-background data-[state=active]:text-foreground"
              >
                <Shield className="h-4 w-4" />
                Monitor
              </TabsTrigger>
              <TabsTrigger
                value="violations"
                className="gap-2 text-gray-700 dark:text-slate-300 data-[state=active]:bg-background data-[state=active]:text-foreground"
              >
                <Siren className="h-4 w-4" />
                Violations
              </TabsTrigger>
            </TabsList>
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5">
            <TabsContent value="profile" className="mt-0 space-y-5">
              <Card className="border-border bg-card shadow-none">
                <CardHeader className="pb-3">
                  <CardTitle className="text-lg text-foreground">
                    Identity
                  </CardTitle>
                </CardHeader>
                <CardContent className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="ax-profile-username">Username</Label>
                    <Input
                      id="ax-profile-username"
                      readOnly
                      autoComplete="off"
                      data-1p-ignore
                      data-lpignore="true"
                      value={profile?.username || username || ""}
                      className="border-border bg-background text-foreground"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="ax-profile-email">Email</Label>
                    <Input
                      id="ax-profile-email"
                      readOnly
                      autoComplete="off"
                      data-1p-ignore
                      data-lpignore="true"
                      value={profile?.email || ""}
                      className="border-border bg-background text-foreground"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="ax-profile-user-id">User ID</Label>
                    <Input
                      id="ax-profile-user-id"
                      readOnly
                      autoComplete="off"
                      data-1p-ignore
                      data-lpignore="true"
                      value={profile?.id || ""}
                      className="border-border bg-background text-foreground"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="ax-profile-space">Current space</Label>
                    <Input
                      id="ax-profile-space"
                      readOnly
                      autoComplete="off"
                      data-1p-ignore
                      data-lpignore="true"
                      value={formatSpaceLabel(currentSpaceName, currentSpaceId)}
                      className="border-border bg-background text-foreground"
                    />
                  </div>
                  <div className="md:col-span-2 flex flex-wrap items-center gap-2">
                    {profile?.role ? (
                      <Badge
                        variant="secondary"
                        className="border border-border bg-muted text-foreground"
                      >
                        Role: {profile.role.replace(/_/g, " ")}
                      </Badge>
                    ) : null}
                    {subscriptionTier ? (
                      <Badge
                        variant="outline"
                        className="border border-cyan-500/30 bg-cyan-500/15 text-gray-900 dark:text-cyan-100"
                      >
                        Tier: {subscriptionTier}
                      </Badge>
                    ) : null}
                  </div>
                  {isAdminProfile ? (
                    <div className="md:col-span-2">
                      <a
                        href="/admin"
                        className="inline-flex items-center text-sm font-medium text-cyan-300 transition hover:text-cyan-200"
                      >
                        Open admin panel
                      </a>
                    </div>
                  ) : null}
                  <p className="md:col-span-2 text-sm text-muted-foreground">
                    Identity is read-only here for now. Preference settings
                    below already persist through the existing user settings
                    endpoint.
                  </p>
                </CardContent>
              </Card>

              {open ? <WorkspaceInvitation key={currentSpaceId} /> : null}

              <Card className="border-border bg-card shadow-none">
                <CardHeader className="pb-3">
                  <CardTitle className="text-lg text-foreground">
                    Preferences
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-5">
                  <div className="flex items-center justify-between gap-4 rounded-2xl border border-border bg-muted/40 px-4 py-3">
                    <div>
                      <div className="text-sm font-medium text-foreground">
                        Compact mode
                      </div>
                      <p className="text-sm text-gray-600 dark:text-muted-foreground">
                        Tighten spacing across lists and panels.
                      </p>
                    </div>
                    <Switch
                      checked={settings.compact_mode}
                      disabled={saving}
                      onCheckedChange={(checked) =>
                        void saveSetting({ compact_mode: checked })
                      }
                      aria-label="Toggle compact mode"
                    />
                  </div>

                  <div className="space-y-3 rounded-2xl border border-border bg-muted/40 px-4 py-4">
                    <div>
                      <div className="text-sm font-medium text-foreground">
                        Theme
                      </div>
                      <p className="text-sm text-gray-600 dark:text-muted-foreground">
                        Choose the login and app theme together. The selection
                        is persisted in user settings and local storage.
                      </p>
                    </div>
                    <div className="grid gap-2 sm:grid-cols-3">
                      {[
                        {
                          value: "system" as const,
                          label: "System",
                          icon: Monitor,
                        },
                        {
                          value: "light" as const,
                          label: "Light",
                          icon: Sun,
                        },
                        {
                          value: "dark" as const,
                          label: "Dark",
                          icon: Moon,
                        },
                      ].map(({ value, label, icon: Icon }) => {
                        const active = settings.theme === value;
                        return (
                          <button
                            key={value}
                            type="button"
                            disabled={saving}
                            onClick={() =>
                              void updateSettings({ theme: value })
                            }
                            className={cn(
                              "flex items-center gap-3 rounded-xl border px-3 py-3 text-left transition",
                              active
                                ? "border-cyan-400/60 bg-cyan-500/10 text-foreground"
                                : "border-border bg-background text-foreground hover:bg-muted",
                              saving && "cursor-wait opacity-70",
                            )}
                            aria-pressed={active}
                          >
                            <Icon className="h-4 w-4 shrink-0" />
                            <span className="text-sm font-medium">{label}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  <div className="flex items-center justify-between gap-4 rounded-2xl border border-border bg-muted/40 px-4 py-3">
                    <div>
                      <div className="text-sm font-medium text-foreground">
                        Auto summarize
                      </div>
                      <p className="text-sm text-gray-600 dark:text-muted-foreground">
                        Let Waystation generate automatic summaries where the backend
                        supports it.
                      </p>
                    </div>
                    <Switch
                      checked={settings.ai_auto_summarize}
                      disabled={saving}
                      onCheckedChange={(checked) =>
                        void saveSetting({ ai_auto_summarize: checked })
                      }
                      aria-label="Toggle auto summarize"
                    />
                  </div>

                  {onLogout ? (
                    <>
                      <Separator className="bg-border" />
                      <Button
                        variant="outline"
                        onClick={onLogout}
                        className="border-border bg-background text-foreground hover:bg-muted"
                      >
                        <LogOut className="mr-2 h-4 w-4" />
                        Sign out
                      </Button>
                    </>
                  ) : null}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="monitor" className="mt-0 space-y-5">
              <Card className="border-border bg-card shadow-none">
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-lg text-foreground">
                    <KeyRound className="h-5 w-5 text-blue-300" />
                    Personal Access Tokens
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {keysQuery.isLoading ? (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Loading credentials...
                    </div>
                  ) : keysQuery.isError ? (
                    <p className="text-sm text-gray-600 dark:text-muted-foreground">
                      Could not load credentials.
                    </p>
                  ) : (
                    <>
                      <div className="grid gap-3 md:grid-cols-3">
                        <div className="rounded-2xl border border-blue-500/30 bg-blue-500/10 px-4 py-3">
                          <div className="text-xs font-semibold uppercase tracking-wide text-gray-700 dark:text-blue-100">
                            Active tokens
                          </div>
                          <div className="mt-1 text-2xl font-bold text-gray-900 dark:text-blue-100">
                            {keyRows.length}
                          </div>
                        </div>
                        <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/15 px-4 py-3">
                          <div className="text-xs font-semibold uppercase tracking-wide text-gray-700 dark:text-emerald-100">
                            Recently used
                          </div>
                          <div className="mt-1 text-2xl font-bold text-gray-900 dark:text-emerald-100">
                            {
                              keyRows.filter(
                                (k: PersonalAccessKey) => k.last_used_at,
                              ).length
                            }
                          </div>
                        </div>
                        <div className="rounded-2xl border border-amber-500/30 bg-amber-500/15 px-4 py-3">
                          <div className="text-xs font-semibold uppercase tracking-wide text-gray-700 dark:text-amber-100">
                            Never used
                          </div>
                          <div className="mt-1 text-2xl font-bold text-gray-900 dark:text-amber-100">
                            {
                              keyRows.filter(
                                (k: PersonalAccessKey) => !k.last_used_at,
                              ).length
                            }
                          </div>
                        </div>
                      </div>

                      <div className="space-y-2">
                        {keyRows.map((key: PersonalAccessKey) => (
                          <div
                            key={key.credential_id}
                            className="flex items-center justify-between rounded-2xl border border-border bg-muted/40 px-4 py-3"
                          >
                            <div className="space-y-0.5">
                              <div className="text-sm font-semibold text-gray-900 dark:text-foreground">
                                {key.name}
                              </div>
                              <div className="text-xs text-gray-600 dark:text-muted-foreground">
                                {key.allowed_agent_ids?.length
                                  ? `Scoped to ${key.allowed_agent_ids.length} agent(s)`
                                  : "Unscoped"}
                              </div>
                            </div>
                            <div className="text-right text-xs text-gray-600 dark:text-muted-foreground">
                              <div>
                                {key.last_used_at
                                  ? `Used ${formatTimestamp(key.last_used_at)}`
                                  : "Never used"}
                              </div>
                              <div>
                                Created {formatTimestamp(key.created_at)}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </CardContent>
              </Card>

              <Card className="border-border bg-card shadow-none">
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-lg text-foreground">
                    <Siren className="h-5 w-5 text-rose-300" />
                    Security violations
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {violationsQuery.isLoading ? (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Loading violations...
                    </div>
                  ) : violationsQuery.isError ? (
                    <p className="text-sm text-gray-600 dark:text-muted-foreground">
                      Could not load violations.
                    </p>
                  ) : (violationsQuery.data || []).length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-emerald-500/30 bg-emerald-500/5 px-4 py-3">
                      <p className="text-sm text-emerald-800 dark:text-emerald-200">
                        No violations detected.
                      </p>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {(violationsQuery.data || []).map(
                        (item: ViolationItem) => (
                          <div
                            key={item.id}
                            className="rounded-2xl border border-border bg-muted/40 px-4 py-4"
                          >
                            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                              <div className="space-y-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <div className="text-sm font-medium text-foreground">
                                    {item.title}
                                  </div>
                                  <Badge
                                    variant="outline"
                                    className={cn(
                                      "capitalize",
                                      getViolationTone(item.severity),
                                    )}
                                  >
                                    {item.severity}
                                  </Badge>
                                  <Badge
                                    variant="outline"
                                    className={cn(
                                      "capitalize",
                                      getViolationStatusTone(item.status),
                                    )}
                                  >
                                    {formatViolationLabel(item.status)}
                                  </Badge>
                                </div>
                                <p className="text-sm text-gray-600 dark:text-muted-foreground">
                                  {item.summary}
                                </p>
                              </div>
                              <div className="space-y-1 text-xs text-gray-600 dark:text-muted-foreground md:text-right">
                                <div>{item.id}</div>
                                <div>Owner: {item.owner}</div>
                                <div>Updated {item.updatedAt}</div>
                              </div>
                            </div>
                          </div>
                        ),
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="access" className="mt-0 space-y-5">
              <Card className="border-border bg-card shadow-none">
                <CardHeader className="pb-3">
                  <CardTitle className="text-lg text-foreground">
                    Personal access tokens
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-sm text-gray-600 dark:text-muted-foreground">
                    Create agent tokens for supported headless API access and
                    MCP runtime credentials.
                  </p>
                  <p className="text-xs text-gray-600 dark:text-muted-foreground">
                    Client ID/client secret credentials are future-gated while
                    the end-to-end M2M path is hardened. Use Agent Token for
                    current headless setup.
                  </p>

                  {patUnavailable ? (
                    <div className="rounded-2xl border border-amber-400/30 bg-amber-500/15 px-4 py-3 text-sm text-gray-900 dark:text-amber-100">
                      PAT endpoints are not available from this backend yet. The
                      credentials tab is ready for `/api/v1/keys` once the local
                      API exposes it.
                    </div>
                  ) : null}

                  <div className="grid gap-4 lg:grid-cols-[1.1fr_1.4fr]">
                    <div className="space-y-3 rounded-2xl border border-border bg-muted/40 p-4">
                      <div className="space-y-2">
                        <Label htmlFor="ax-pat-name">Token name</Label>
                        <Input
                          id="ax-pat-name"
                          value={newKeyName}
                          onChange={(event) =>
                            setNewKeyName(event.target.value)
                          }
                          placeholder="Team Space automation"
                          className="border-border bg-background text-foreground"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label>Token type</Label>
                        <div
                          className={cn(
                            "rounded-xl border px-3 py-3 text-left",
                            "border-cyan-400/60 bg-cyan-500/10 text-foreground",
                          )}
                        >
                          <div className="text-sm font-medium">Agent Token</div>
                          <div className="mt-1 text-xs text-muted-foreground">
                            Bound to one agent &mdash; headless API access
                          </div>
                        </div>
                      </div>
                      {tokenType === "agent" && (
                        <label
                          className={cn(
                            "flex items-start gap-3 rounded-xl border px-3 py-2 text-sm transition cursor-pointer",
                            enrollmentMode
                              ? "border-cyan-400/60 bg-cyan-500/10 text-foreground"
                              : "border-border bg-background text-foreground hover:bg-muted",
                          )}
                        >
                          <input
                            type="checkbox"
                            checked={enrollmentMode}
                            onChange={(event) => {
                              setEnrollmentMode(event.target.checked);
                              if (event.target.checked)
                                setSelectedBoundAgentId(null);
                            }}
                            className="mt-1 h-4 w-4 shrink-0 rounded border-border bg-background accent-cyan-500"
                          />
                          <div>
                            <div className="font-medium">
                              Register agent and bind on first use
                            </div>
                            <div className="text-xs text-muted-foreground">
                              Creates an enrollment token that binds to an agent
                              on its first exchange. Use when the agent
                              doesn&apos;t exist yet.
                            </div>
                          </div>
                        </label>
                      )}
                      <p className="text-xs text-gray-600 dark:text-muted-foreground">
                        {tokenType === "user"
                          ? "Exchanges for user_access or user_admin JWTs. Cannot act as an agent."
                          : enrollmentMode
                            ? "Creates an enrollment token (1-hour TTL). On first exchange with an agent_id, it binds permanently."
                            : "Exchanges for agent_access JWTs only. Permanently bound to one agent."}
                      </p>
                      <div className="space-y-2">
                        <Label>Target</Label>
                        <div className="grid gap-1.5 sm:grid-cols-3">
                          {(
                            [
                              {
                                value: "cli" as const,
                                label: "CLI",
                                desc: "API only",
                              },
                              {
                                value: "mcp" as const,
                                label: "MCP",
                                desc: "MCP only",
                              },
                              {
                                value: "both" as const,
                                label: "Both",
                                desc: "API + MCP",
                              },
                            ] as const
                          ).map((opt) => (
                            <button
                              key={opt.value}
                              type="button"
                              onClick={() => setTokenAudience(opt.value)}
                              className={cn(
                                "rounded-lg border px-2.5 py-2 text-left transition text-xs",
                                tokenAudience === opt.value
                                  ? "border-cyan-400/60 bg-cyan-500/10 text-foreground"
                                  : "border-border bg-background text-foreground hover:bg-muted",
                              )}
                            >
                              <div className="font-medium">{opt.label}</div>
                              <div className="text-muted-foreground">
                                {opt.desc}
                              </div>
                            </button>
                          ))}
                        </div>
                        {tokenAudience === "both" && (
                          <p className="text-xs text-amber-400">
                            Grants access to both CLI and MCP &mdash; not
                            least-privilege.
                          </p>
                        )}
                      </div>
                      <Button
                        onClick={() => createKeyMutation.mutate()}
                        disabled={
                          !newKeyName.trim() ||
                          (tokenType === "agent" &&
                            !enrollmentMode &&
                            !selectedBoundAgentId) ||
                          createKeyMutation.isPending ||
                          patUnavailable
                        }
                        className="w-full bg-cyan-500 text-slate-950 hover:bg-cyan-400"
                      >
                        {createKeyMutation.isPending ? (
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        ) : (
                          <KeyRound className="mr-2 h-4 w-4" />
                        )}
                        {tokenType === "user"
                          ? "Create User Token"
                          : "Create Agent Token"}
                      </Button>

                      {revealedCreatedKey &&
                      getPatSecret(revealedCreatedKey) ? (
                        <div className="rounded-2xl border border-emerald-400/30 bg-emerald-500/10 p-4">
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <div className="text-sm font-semibold text-gray-900 dark:text-emerald-100">
                                Save this token now
                              </div>
                              <p className="mt-1 text-sm text-gray-700 dark:text-emerald-50/80">
                                The backend only returns the plaintext PAT once.
                              </p>
                            </div>
                            <div className="flex gap-2">
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() =>
                                  setShowCreatedToken((prev) => !prev)
                                }
                                className="border-emerald-300/40 bg-transparent text-emerald-900 hover:bg-emerald-400/10 dark:border-emerald-200/30 dark:text-emerald-50"
                              >
                                {showCreatedToken ? (
                                  <EyeOff className="h-4 w-4" />
                                ) : (
                                  <Eye className="h-4 w-4" />
                                )}
                              </Button>
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() =>
                                  void handleCopyValue(
                                    getPatSecret(revealedCreatedKey),
                                    "PAT",
                                  )
                                }
                                className="border-emerald-300/40 bg-transparent text-emerald-900 hover:bg-emerald-400/10 dark:border-emerald-200/30 dark:text-emerald-50"
                              >
                                <Copy className="mr-2 h-4 w-4" />
                                Copy
                              </Button>
                            </div>
                          </div>
                          <div className="mt-3 rounded-xl bg-emerald-500/10 p-3 font-mono text-xs text-emerald-700 break-all dark:text-emerald-100">
                            {showCreatedToken
                              ? getPatSecret(revealedCreatedKey)
                              : getPatSecret(revealedCreatedKey)!.slice(0, 10) +
                                "••••••••••••••••••••••••"}
                          </div>
                        </div>
                      ) : null}
                    </div>

                    <div className="rounded-2xl border border-border bg-muted/40 p-4">
                      <div className="mb-3 flex items-center justify-between gap-3">
                        <div>
                          <div className="text-sm font-medium text-foreground">
                            {tokenType === "user"
                              ? "Token details"
                              : enrollmentMode
                                ? "Enrollment token"
                                : "Select agent"}
                          </div>
                          <p className="text-sm text-gray-600 dark:text-muted-foreground">
                            {tokenType === "user"
                              ? "User tokens exchange for short-lived JWTs via POST /auth/exchange."
                              : enrollmentMode
                                ? "This token will bind to an agent on its first exchange."
                                : "Choose the agent this token will be permanently bound to."}
                          </p>
                        </div>
                        {agentsQuery.isFetching ? (
                          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                        ) : null}
                      </div>
                      {tokenType === "user" ? (
                        <div className="space-y-3">
                          <div className="rounded-xl border border-border bg-background px-3 py-4 text-sm text-muted-foreground">
                            <div className="font-medium text-foreground mb-2">
                              How it works
                            </div>
                            <ol className="list-decimal list-inside space-y-1 text-xs">
                              <li>
                                Create token &rarr; save the{" "}
                                <code className="text-cyan-400">axp_u_</code>{" "}
                                value
                              </li>
                              <li>
                                Exchange:{" "}
                                <code className="text-cyan-400">
                                  POST /auth/exchange
                                </code>{" "}
                                with the token
                              </li>
                              <li>
                                Get a short-lived JWT (15 min user_access, 5 min
                                user_admin)
                              </li>
                              <li>Use the JWT on all API endpoints</li>
                            </ol>
                          </div>
                          <div className="rounded-xl border border-border bg-background px-3 py-3 text-xs text-muted-foreground">
                            <span className="font-medium text-foreground">
                              Available scopes:
                            </span>{" "}
                            messages, tasks, context, agents, spaces, search
                            (user_access) &mdash; agents.create,
                            credentials.issue.agent, credentials.revoke
                            (user_admin)
                          </div>
                        </div>
                      ) : enrollmentMode ? (
                        <div className="space-y-3">
                          <div className="rounded-xl border border-border bg-background px-3 py-4 text-sm text-muted-foreground">
                            <div className="font-medium text-foreground mb-2">
                              Enrollment flow
                            </div>
                            <ol className="list-decimal list-inside space-y-1 text-xs">
                              <li>
                                Create token &rarr; save the{" "}
                                <code className="text-cyan-400">axp_a_</code>{" "}
                                value (1-hour enrollment window)
                              </li>
                              <li>
                                Give the token to your agent or automation
                              </li>
                              <li>
                                On first{" "}
                                <code className="text-cyan-400">
                                  POST /auth/exchange
                                </code>{" "}
                                with an agent_id, the token binds permanently
                              </li>
                              <li>
                                Token TTL extends to 90 days after binding
                              </li>
                            </ol>
                          </div>
                          <div className="rounded-xl border border-amber-400/30 bg-amber-500/15 px-3 py-3 text-xs text-gray-900 dark:text-amber-100">
                            The enrollment window is 1 hour. If the token
                            isn&apos;t used within that time, it expires and
                            you&apos;ll need to create a new one.
                          </div>
                        </div>
                      ) : (
                        <div className="space-y-3">
                          {patAgentsUnavailable ? (
                            <div className="rounded-xl border border-amber-400/30 bg-amber-500/15 px-3 py-3 text-xs text-gray-900 dark:text-amber-100">
                              Agent selection is unavailable from this backend,
                              but user and enrollment token creation can still
                              use the PAT endpoint.
                            </div>
                          ) : null}
                          <Input
                            value={agentFilter}
                            onChange={(event) =>
                              setAgentFilter(event.target.value)
                            }
                            placeholder="Search agents by name or ID"
                            className="border-border bg-background text-foreground"
                          />
                          <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                            {filteredPatAgents.length ? (
                              filteredPatAgents.map((agent) => {
                                const selected =
                                  selectedBoundAgentId === agent.id;
                                const agentLabel = getPatAgentLabel(agent);
                                const presenceKey =
                                  getPatAgentPresenceKey(agent);
                                return (
                                  <button
                                    key={agent.id}
                                    type="button"
                                    onClick={() =>
                                      setSelectedBoundAgentId(
                                        selected ? null : agent.id,
                                      )
                                    }
                                    className={cn(
                                      "flex w-full items-center justify-between rounded-xl border px-3 py-2 text-sm text-left transition",
                                      selected
                                        ? "border-cyan-400/60 bg-cyan-500/10 text-foreground"
                                        : "border-border bg-background text-foreground hover:bg-muted",
                                    )}
                                  >
                                    <div>
                                      <div className="flex items-center gap-2 font-medium">
                                        {presenceKey ? (
                                          <PresenceDot
                                            status={getPresenceStatus(
                                              presenceKey,
                                            )}
                                            freshness={getPresenceFreshness(
                                              presenceKey,
                                            )}
                                          />
                                        ) : null}
                                        <span>{agentLabel}</span>
                                      </div>
                                      <div className="text-xs text-muted-foreground">
                                        {agent.id}
                                      </div>
                                    </div>
                                    {selected ? (
                                      <Badge
                                        variant="outline"
                                        className="border-cyan-400/60 text-cyan-400"
                                      >
                                        bound
                                      </Badge>
                                    ) : null}
                                  </button>
                                );
                              })
                            ) : (
                              <div className="rounded-xl border border-dashed border-border px-3 py-6 text-sm text-muted-foreground">
                                {patAgents.length
                                  ? "No agents matched that search."
                                  : "No agents available in this space."}
                              </div>
                            )}
                          </div>
                          <div className="rounded-xl border border-border bg-background px-3 py-3 text-xs text-muted-foreground">
                            <span className="font-medium text-foreground">
                              Agent tokens
                            </span>{" "}
                            use the{" "}
                            <code className="text-cyan-400">axp_a_</code> prefix
                            and exchange for{" "}
                            <code className="text-cyan-400">agent_access</code>{" "}
                            JWTs only.
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card className="border-border bg-card shadow-none">
                <CardHeader className="pb-3">
                  <CardTitle className="text-lg text-foreground">
                    Existing credentials
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {keysQuery.isLoading ? (
                    <div className="flex items-center gap-2 rounded-2xl border border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Loading existing PATs...
                    </div>
                  ) : keyRows.length ? (
                    keyRows.map((key) => {
                      const scope = normalizeAgentScope(key);
                      const rotatedKey = revealedRotatedKeys[key.credential_id];
                      const rotatedSecret = getPatSecret(rotatedKey || null);
                      return (
                        <div
                          key={key.credential_id}
                          className="rounded-2xl border border-border bg-muted/40 p-4"
                        >
                          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                            <div className="space-y-2">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-semibold text-foreground">
                                  {key.name}
                                </span>
                                <span className="rounded-full border border-border bg-background px-2 py-0.5 text-xs text-muted-foreground">
                                  {getAgentScopeLabel(scope)}
                                </span>
                              </div>
                              <div className="font-mono text-xs text-muted-foreground">
                                {key.credential_id}
                              </div>
                              <div className="text-sm text-foreground">
                                Scope: {renderAllowedAgents(key, agentNameMap)}
                              </div>
                              <div className="text-xs text-muted-foreground">
                                Created {formatTimestamp(key.created_at)}. Last
                                used {formatTimestamp(key.last_used_at)}.
                              </div>
                            </div>
                            <div className="flex gap-2">
                              <Button
                                variant="outline"
                                size="sm"
                                disabled={rotateKeyMutation.isPending}
                                onClick={() => rotateKeyMutation.mutate(key)}
                                className="border-border bg-background text-foreground hover:bg-muted"
                              >
                                <RefreshCw className="mr-2 h-4 w-4" />
                                Rotate
                              </Button>
                              <Button
                                variant="outline"
                                size="sm"
                                disabled={revokeKeyMutation.isPending}
                                onClick={() =>
                                  revokeKeyMutation.mutate(key.credential_id)
                                }
                                className="border-red-400/30 bg-transparent text-red-800 hover:bg-red-500/10 hover:text-red-900 dark:text-red-100 dark:hover:text-red-50"
                              >
                                Revoke
                              </Button>
                            </div>
                          </div>
                          {rotatedSecret ? (
                            <div className="mt-4 rounded-2xl border border-emerald-400/30 bg-emerald-500/10 p-4">
                              <div className="flex items-start justify-between gap-3">
                                <div>
                                  <div className="text-sm font-semibold text-gray-900 dark:text-emerald-100">
                                    Save the replacement token now
                                  </div>
                                  <p className="mt-1 text-sm text-gray-700 dark:text-emerald-50/80">
                                    This rotated PAT secret is shown once for
                                    this credential.
                                  </p>
                                </div>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() =>
                                    void handleCopyValue(rotatedSecret, "PAT")
                                  }
                                  className="border-emerald-300/40 bg-transparent text-emerald-900 hover:bg-emerald-400/10 dark:border-emerald-200/30 dark:text-emerald-50"
                                >
                                  <Copy className="mr-2 h-4 w-4" />
                                  Copy
                                </Button>
                              </div>
                              <div className="mt-3 rounded-xl bg-emerald-500/10 p-3 font-mono text-xs text-emerald-700 break-all dark:text-emerald-100">
                                {rotatedSecret}
                              </div>
                            </div>
                          ) : null}
                        </div>
                      );
                    })
                  ) : (
                    <div className="rounded-2xl border border-dashed border-border bg-muted/30 px-4 py-8 text-center text-sm text-muted-foreground">
                      No PATs exist for this user yet.
                    </div>
                  )}

                  {keysQuery.isError && !patUnavailable ? (
                    <div className="flex items-start gap-3 rounded-2xl border border-amber-400/30 bg-amber-500/15 px-4 py-3 text-sm text-gray-900 dark:text-amber-50">
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                      The PAT list failed to load. The credentials tab is wired,
                      but this backend rejected the request.
                    </div>
                  ) : null}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="agents" className="mt-0 space-y-5">
              <Card className="border-border bg-card shadow-none">
                <CardHeader className="pb-3">
                  <CardTitle className="text-lg text-foreground">
                    Agents
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-sm text-gray-600 dark:text-muted-foreground">
                    Manage agents you own and create headless MCP credentials
                    for each one without leaving the modern settings surface.
                  </p>

                  <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
                    <div className="space-y-3 rounded-2xl border border-border bg-muted/40 p-4">
                      <div>
                        <div className="text-sm font-medium text-foreground">
                          Your agents
                        </div>
                        <p className="mt-1 text-sm text-gray-600 dark:text-muted-foreground">
                          Pick an agent to manage its headless credentials.
                        </p>
                      </div>
                      <Input
                        value={managedAgentFilter}
                        onChange={(event) =>
                          setManagedAgentFilter(event.target.value)
                        }
                        placeholder={
                          managedAgentsUnavailable
                            ? "Owned-agent endpoint unavailable"
                            : "Search owned agents"
                        }
                        disabled={
                          managedAgentsQuery.isLoading ||
                          managedAgentsUnavailable
                        }
                        className="border-border bg-background text-foreground"
                      />

                      {managedAgentsUnavailable ? (
                        <div className="flex items-start gap-3 rounded-2xl border border-amber-400/30 bg-amber-500/15 px-4 py-3 text-sm text-gray-900 dark:text-amber-100">
                          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                          Owned-agent management is not available from this
                          backend yet. This surface is ready once `/auth/agents`
                          is exposed.
                        </div>
                      ) : managedAgentsQuery.isLoading ? (
                        <div className="flex items-center gap-2 rounded-2xl border border-border bg-background px-4 py-3 text-sm text-muted-foreground">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          Loading agents...
                        </div>
                      ) : managedAgents.length ? (
                        <div className="max-h-[520px] space-y-3 overflow-y-auto pr-1">
                          {managedAgents.map((agent) => {
                            const selected =
                              agent.id === selectedManagedAgent?.id;
                            return (
                              <button
                                key={agent.id}
                                type="button"
                                onClick={() => {
                                  setSelectedManagedAgentId(agent.id);
                                  setNewAgentKeyLabel("");
                                }}
                                className={cn(
                                  "w-full rounded-2xl border p-4 text-left transition",
                                  selected
                                    ? "border-cyan-400/60 bg-cyan-500/10"
                                    : "border-border bg-background hover:bg-muted",
                                )}
                              >
                                <div className="flex items-start gap-3">
                                  <div
                                    className={cn(
                                      "flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl",
                                      selected
                                        ? "bg-cyan-500/20 text-cyan-800 dark:text-cyan-200"
                                        : "bg-muted text-muted-foreground",
                                    )}
                                  >
                                    <Bot className="h-5 w-5" />
                                  </div>
                                  <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2">
                                      <span className="truncate text-sm font-semibold text-foreground">
                                        {agent.name}
                                      </span>
                                      <span
                                        className={cn(
                                          "rounded-full border px-2 py-0.5 text-[10px] capitalize",
                                          getManagedAgentStatus(agent)
                                            .className,
                                        )}
                                      >
                                        {getManagedAgentStatus(agent).label}
                                      </span>
                                      <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-muted-foreground">
                                        {getAgentRuntimeDisplay(agent).label}
                                      </span>
                                    </div>
                                    <div className="mt-1 text-xs text-muted-foreground">
                                      {agent.control?.is_disabled
                                        ? getManagedAgentDisabledCopy(agent)
                                        : formatRelativeActivity(
                                            agent.last_active_at,
                                          )}
                                    </div>
                                    <div className="mt-3 font-mono text-[11px] text-muted-foreground break-all">
                                      {agent.id}
                                    </div>
                                  </div>
                                </div>
                              </button>
                            );
                          })}
                        </div>
                      ) : managedAgentsLoadFailed ? (
                        <div className="flex items-start gap-3 rounded-2xl border border-amber-400/30 bg-amber-500/15 px-4 py-3 text-sm text-gray-900 dark:text-amber-100">
                          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                          The owned agent list failed to load. Try again after
                          the backend recovers.
                        </div>
                      ) : (
                        <div className="rounded-2xl border border-dashed border-border bg-background px-4 py-8 text-center text-sm text-muted-foreground">
                          {managedAgentFilter.trim()
                            ? "No owned agents matched that search."
                            : "No owned agents are available to manage here yet."}
                        </div>
                      )}
                    </div>

                    <div className="space-y-4">
                      {selectedManagedAgent ? (
                        <>
                          <div className="rounded-2xl border border-border bg-muted/40 p-4">
                            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                              <div className="flex items-start gap-3">
                                <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-cyan-500/10 text-cyan-300">
                                  <Bot className="h-6 w-6" />
                                </div>
                                <div>
                                  <div className="text-lg font-semibold text-foreground">
                                    {selectedManagedAgent.name}
                                  </div>
                                  <p className="mt-1 text-sm text-gray-600 dark:text-muted-foreground">
                                    Create, rotate, and revoke headless
                                    credentials for this agent.
                                  </p>
                                </div>
                              </div>
                              <div className="flex flex-wrap gap-2">
                                <span className="rounded-full border border-border bg-background px-2 py-1 text-xs text-muted-foreground">
                                  {selectedManagedAgent.visibility || "private"}
                                </span>
                                <span
                                  className={cn(
                                    "rounded-full border px-2 py-1 text-xs capitalize",
                                    getManagedAgentStatus(selectedManagedAgent)
                                      .className,
                                  )}
                                >
                                  {
                                    getManagedAgentStatus(selectedManagedAgent)
                                      .label
                                  }
                                </span>
                                <span className="rounded-full border border-border bg-background px-2 py-1 text-xs text-muted-foreground">
                                  {
                                    getAgentRuntimeDisplay(selectedManagedAgent)
                                      .label
                                  }
                                </span>
                              </div>
                            </div>
                            {selectedManagedAgent.control?.is_disabled ? (
                              <div className="mt-4 rounded-2xl border border-rose-500/30 bg-rose-500/15 px-4 py-3 text-sm text-gray-900 dark:text-rose-100">
                                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                                  <div>
                                    <div className="font-medium">
                                      Agent disabled
                                    </div>
                                    <div className="mt-1 text-gray-700 dark:text-rose-100/90">
                                      {getManagedAgentDisabledCopy(
                                        selectedManagedAgent,
                                      )}
                                    </div>
                                  </div>
                                  <Button
                                    type="button"
                                    onClick={() =>
                                      enableManagedAgentMutation.mutate(
                                        selectedManagedAgent.id,
                                      )
                                    }
                                    disabled={
                                      enableManagedAgentMutation.isPending
                                    }
                                    className="bg-rose-100 text-rose-950 hover:bg-white"
                                  >
                                    {enableManagedAgentMutation.isPending ? (
                                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    ) : null}
                                    Re-enable agent
                                  </Button>
                                </div>
                              </div>
                            ) : null}
                            <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                              <div className="rounded-2xl border border-border bg-background px-4 py-3">
                                <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
                                  Last active
                                </div>
                                <div className="mt-2 text-sm text-foreground">
                                  {formatRelativeActivity(
                                    selectedManagedAgent.last_active_at,
                                  )}
                                </div>
                              </div>
                              <div className="rounded-2xl border border-border bg-background px-4 py-3">
                                <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
                                  Agent ID
                                </div>
                                <div className="mt-2 font-mono text-xs text-foreground break-all">
                                  {selectedManagedAgent.id}
                                </div>
                              </div>
                              <div className="rounded-2xl border border-border bg-background px-4 py-3">
                                <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
                                  Credential model
                                </div>
                                <div className="mt-2 text-sm text-foreground">
                                  Per-agent client credentials
                                </div>
                              </div>
                            </div>
                          </div>

                          <Card className="border-border bg-card shadow-none">
                            <CardHeader className="pb-3">
                              <CardTitle className="text-lg text-foreground">
                                Create agent credential
                              </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-4">
                              <p className="text-sm text-gray-600 dark:text-muted-foreground">
                                Generate a client ID and client secret for this
                                agent. The secret is shown once and should be
                                stored outside source control.
                              </p>

                              {agentCredentialsUnavailable ? (
                                <div className="rounded-2xl border border-amber-400/30 bg-amber-500/15 px-4 py-3 text-sm text-gray-900 dark:text-amber-100">
                                  Headless credential endpoints are not
                                  available from this backend yet. This surface
                                  is ready once `/api/v1/agents/:agentId/keys`
                                  is exposed.
                                </div>
                              ) : null}

                              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
                                <div className="space-y-2">
                                  <Label htmlFor="ax-agent-key-label">
                                    Credential label
                                  </Label>
                                  <Input
                                    id="ax-agent-key-label"
                                    value={newAgentKeyLabel}
                                    onChange={(event) =>
                                      setNewAgentKeyLabel(event.target.value)
                                    }
                                    placeholder="Production MCP connector"
                                    className="border-border bg-background text-foreground"
                                  />
                                </div>
                                <div className="flex items-end">
                                  <Button
                                    onClick={() =>
                                      createAgentKeyMutation.mutate({
                                        agentId: selectedManagedAgent.id,
                                        label: newAgentKeyLabel,
                                      })
                                    }
                                    disabled={
                                      createAgentKeyMutation.isPending ||
                                      agentCredentialsUnavailable
                                    }
                                    className="w-full bg-cyan-500 text-slate-950 hover:bg-cyan-400 md:w-auto"
                                  >
                                    {createAgentKeyMutation.isPending ? (
                                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    ) : (
                                      <KeyRound className="mr-2 h-4 w-4" />
                                    )}
                                    Create credential
                                  </Button>
                                </div>
                              </div>

                              {createdAgentKey ? (
                                <div className="rounded-2xl border border-emerald-400/30 bg-emerald-500/10 p-4">
                                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                                    <div>
                                      <div className="text-sm font-semibold text-gray-900 dark:text-emerald-100">
                                        Save these credentials now
                                      </div>
                                      <p className="mt-1 text-sm text-gray-700 dark:text-emerald-50/80">
                                        The client secret is only returned once
                                        for this agent credential.
                                      </p>
                                    </div>
                                    <div className="flex flex-wrap gap-2">
                                      <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() =>
                                          void handleCopyValue(
                                            createdAgentKey.client_id,
                                            "Client ID",
                                          )
                                        }
                                        className="border-emerald-300/40 bg-transparent text-emerald-900 hover:bg-emerald-400/10 dark:border-emerald-200/30 dark:text-emerald-50"
                                      >
                                        <Copy className="mr-2 h-4 w-4" />
                                        Copy client ID
                                      </Button>
                                      <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() =>
                                          void handleCopyValue(
                                            createdAgentKey.client_secret,
                                            "Client secret",
                                          )
                                        }
                                        className="border-emerald-300/40 bg-transparent text-emerald-900 hover:bg-emerald-400/10 dark:border-emerald-200/30 dark:text-emerald-50"
                                      >
                                        <Copy className="mr-2 h-4 w-4" />
                                        Copy secret
                                      </Button>
                                    </div>
                                  </div>
                                  <div className="mt-4 grid gap-3 md:grid-cols-2">
                                    <div className="rounded-xl bg-emerald-500/10 p-3">
                                      <div className="text-[11px] uppercase tracking-[0.2em] text-gray-700 dark:text-emerald-50/80">
                                        Client ID
                                      </div>
                                      <div className="mt-2 font-mono text-xs text-emerald-900 break-all dark:text-emerald-100">
                                        {createdAgentKey.client_id}
                                      </div>
                                    </div>
                                    <div className="rounded-xl bg-emerald-500/10 p-3">
                                      <div className="text-[11px] uppercase tracking-[0.2em] text-gray-700 dark:text-emerald-50/80">
                                        Client secret
                                      </div>
                                      <div className="mt-2 font-mono text-xs text-emerald-900 break-all dark:text-emerald-100">
                                        {createdAgentKey.client_secret}
                                      </div>
                                    </div>
                                  </div>
                                </div>
                              ) : null}
                            </CardContent>
                          </Card>

                          <Card className="border-border bg-card shadow-none">
                            <CardHeader className="pb-3">
                              <CardTitle className="text-lg text-foreground">
                                Existing agent credentials
                              </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-3">
                              {agentKeysQuery.isLoading ? (
                                <div className="flex items-center gap-2 rounded-2xl border border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
                                  <Loader2 className="h-4 w-4 animate-spin" />
                                  Loading agent credentials...
                                </div>
                              ) : managedAgentKeyRows.length ? (
                                managedAgentKeyRows.map((key: AgentKeyInfo) => {
                                  const rotatedKey =
                                    revealedRotatedAgentKeys[key.key_id];
                                  const rotatedSecret =
                                    rotatedKey?.client_secret || "";
                                  return (
                                    <div
                                      key={key.key_id}
                                      className="rounded-2xl border border-border bg-muted/40 p-4"
                                    >
                                      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                                        <div className="space-y-2">
                                          <div className="flex items-center gap-2">
                                            <span className="text-sm font-semibold text-foreground">
                                              {key.label ||
                                                "Unlabeled credential"}
                                            </span>
                                            <span className="rounded-full border border-border bg-background px-2 py-0.5 text-xs text-muted-foreground">
                                              {key.is_active
                                                ? "Active"
                                                : "Inactive"}
                                            </span>
                                          </div>
                                          <div className="font-mono text-xs text-muted-foreground break-all">
                                            {key.client_id}
                                          </div>
                                          <div className="grid gap-1 text-sm text-foreground">
                                            <div>
                                              Created:{" "}
                                              {formatTimestamp(key.created_at)}
                                            </div>
                                            <div>
                                              Last used:{" "}
                                              {formatTimestamp(
                                                key.last_used_at,
                                              )}
                                            </div>
                                            <div>
                                              Expires:{" "}
                                              {formatTimestamp(key.expires_at)}
                                            </div>
                                          </div>
                                          {key.scopes ? (
                                            <div className="text-xs text-muted-foreground">
                                              Scopes: {key.scopes}
                                            </div>
                                          ) : null}
                                        </div>
                                        <div className="flex flex-wrap gap-2">
                                          <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={() =>
                                              void handleCopyValue(
                                                key.client_id,
                                                "Client ID",
                                              )
                                            }
                                          >
                                            <Copy className="mr-2 h-4 w-4" />
                                            Copy client ID
                                          </Button>
                                          <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={() =>
                                              rotateAgentKeyMutation.mutate({
                                                agentId:
                                                  selectedManagedAgent.id,
                                                keyId: key.key_id,
                                              })
                                            }
                                            disabled={
                                              rotateAgentKeyMutation.isPending ||
                                              !key.is_active
                                            }
                                          >
                                            <RefreshCw className="mr-2 h-4 w-4" />
                                            Rotate
                                          </Button>
                                          <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={() =>
                                              revokeAgentKeyMutation.mutate({
                                                agentId:
                                                  selectedManagedAgent.id,
                                                keyId: key.key_id,
                                              })
                                            }
                                            disabled={
                                              revokeAgentKeyMutation.isPending ||
                                              !key.is_active
                                            }
                                          >
                                            Revoke
                                          </Button>
                                        </div>
                                      </div>

                                      {rotatedSecret ? (
                                        <div className="mt-4 rounded-2xl border border-emerald-400/30 bg-emerald-500/10 p-4">
                                          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                                            <div>
                                              <div className="text-sm font-semibold text-gray-900 dark:text-emerald-100">
                                                Save the replacement secret now
                                              </div>
                                              <p className="mt-1 text-sm text-gray-700 dark:text-emerald-50/80">
                                                This rotated agent secret is
                                                shown once for this credential.
                                              </p>
                                            </div>
                                            <Button
                                              variant="outline"
                                              size="sm"
                                              onClick={() =>
                                                void handleCopyValue(
                                                  rotatedSecret,
                                                  "Client secret",
                                                )
                                              }
                                              className="border-emerald-300/40 bg-transparent text-emerald-900 hover:bg-emerald-400/10 dark:border-emerald-200/30 dark:text-emerald-50"
                                            >
                                              <Copy className="mr-2 h-4 w-4" />
                                              Copy secret
                                            </Button>
                                          </div>
                                          <div className="mt-3 rounded-xl bg-emerald-500/10 p-3 font-mono text-xs text-emerald-900 break-all dark:text-emerald-100">
                                            {rotatedSecret}
                                          </div>
                                        </div>
                                      ) : null}
                                    </div>
                                  );
                                })
                              ) : (
                                <div className="rounded-2xl border border-dashed border-border bg-muted/30 px-4 py-8 text-center text-sm text-muted-foreground">
                                  No headless credentials exist for this agent
                                  yet.
                                </div>
                              )}

                              {agentKeysQuery.isError &&
                              !agentCredentialsUnavailable ? (
                                <div className="flex items-start gap-3 rounded-2xl border border-amber-400/30 bg-amber-500/15 px-4 py-3 text-sm text-gray-900 dark:text-amber-50">
                                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                                  The agent credential list failed to load for
                                  this agent.
                                </div>
                              ) : null}
                            </CardContent>
                          </Card>
                        </>
                      ) : managedAgentsUnavailable ? (
                        <div className="rounded-2xl border border-amber-400/30 bg-amber-500/15 px-4 py-12 text-center text-sm text-gray-900 dark:text-amber-100">
                          This backend does not expose `/auth/agents`, so
                          headless credential management is unavailable here
                          yet.
                        </div>
                      ) : managedAgentsLoadFailed ? (
                        <div className="flex items-start gap-3 rounded-2xl border border-amber-400/30 bg-amber-500/15 px-4 py-12 text-sm text-gray-900 dark:text-amber-100">
                          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                          The owned agent list failed to load, so credentials
                          cannot be managed right now.
                        </div>
                      ) : (
                        <div className="rounded-2xl border border-dashed border-border bg-muted/30 px-4 py-12 text-center text-sm text-muted-foreground">
                          Select an owned agent to manage its credentials.
                        </div>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="widgets" className="mt-0 space-y-5">
              <Card className="border-border bg-card shadow-none">
                <CardHeader className="pb-3">
                  <CardTitle className="text-lg text-foreground">
                    Widget visibility
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <p className="text-sm text-gray-600 dark:text-muted-foreground">
                    Choose which MCP tool widgets appear in the conversation.
                    Hidden widgets are only suppressed in this browser — the
                    tools still work in other apps.
                  </p>
                  {MCP_SURFACE_POLICY_RULES.map((rule) => {
                    const isHidden =
                      hiddenWidgets?.has(rule.id) ??
                      DEFAULT_HIDDEN_WIDGETS.has(rule.id);
                    const isDefault = DEFAULT_HIDDEN_WIDGETS.has(rule.id);
                    return (
                      <div
                        key={rule.id}
                        className="flex items-center justify-between gap-4 rounded-2xl border border-border bg-muted/40 px-4 py-3"
                      >
                        <div>
                          <div className="flex items-center gap-2 text-sm font-medium capitalize text-foreground">
                            {rule.id}
                            {isDefault && (
                              <span className="rounded-full border border-border px-1.5 py-0.5 text-[10px] font-normal normal-case text-muted-foreground">
                                off by default
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-gray-600 dark:text-muted-foreground">
                            Tools: {rule.match.toolNames?.join(", ") || "—"}
                          </p>
                        </div>
                        <Switch
                          checked={!isHidden}
                          onCheckedChange={() => onToggleWidget?.(rule.id)}
                          aria-label={`Toggle ${rule.id} widget`}
                        />
                      </div>
                    );
                  })}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="violations" className="mt-0 space-y-5">
              <Card className="border-border bg-card shadow-none">
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-lg text-foreground">
                    <Siren className="h-5 w-5 text-rose-300" />
                    Violations
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {violationsQuery.isLoading ? (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Loading violations...
                    </div>
                  ) : violationsQuery.isError ? (
                    <p className="text-sm text-gray-600 dark:text-muted-foreground">
                      Could not load violations.
                    </p>
                  ) : (violationsQuery.data || []).length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-emerald-500/30 bg-emerald-500/5 px-4 py-3">
                      <p className="text-sm text-emerald-800 dark:text-emerald-200">
                        No violations detected.
                      </p>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {(violationsQuery.data || []).map(
                        (item: ViolationItem) => (
                          <div
                            key={item.id}
                            className="rounded-2xl border border-border bg-muted/40 px-4 py-4"
                          >
                            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                              <div className="space-y-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <div className="text-sm font-medium text-foreground">
                                    {item.title}
                                  </div>
                                  <Badge
                                    variant="outline"
                                    className={cn(
                                      "capitalize",
                                      getViolationTone(item.severity),
                                    )}
                                  >
                                    {item.severity}
                                  </Badge>
                                  <Badge
                                    variant="outline"
                                    className={cn(
                                      "capitalize",
                                      getViolationStatusTone(item.status),
                                    )}
                                  >
                                    {formatViolationLabel(item.status)}
                                  </Badge>
                                </div>
                                <p className="text-sm text-gray-600 dark:text-muted-foreground">
                                  {item.summary}
                                </p>
                              </div>
                              <div className="space-y-1 text-xs text-gray-600 dark:text-muted-foreground md:text-right">
                                <div>{item.id}</div>
                                <div>Owner: {item.owner}</div>
                                <div>Updated {item.updatedAt}</div>
                              </div>
                            </div>
                          </div>
                        ),
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          </div>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
