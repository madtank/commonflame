import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  LayoutGrid,
  Loader2,
  LogOut,
  Monitor,
  Moon,
  Sun,
  Shield,
  Siren,
  User2,
} from "lucide-react";
import { api, apiClient } from "@/lib/api-clean";
import { formatSpaceLabel } from "@/lib/display-utils";
import {
  DEFAULT_HIDDEN_WIDGETS,
  MCP_SURFACE_POLICY_RULES,
} from "@/lib/space-agent-surface-policy";
import { WorkspaceInvitation } from "./WorkspaceInvitation";
import { cn } from "@/lib/utils";
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
  const { toast } = useToast();
  const { settings, saving, updateSettings } = useUserSettings();
  const profileQuery = useQuery({
    queryKey: ["ax-settings", "profile"],
    queryFn: async () => (await api.getUserProfile()) as UserProfile | null,
    enabled: open,
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
            Profile and workspace preferences for{" "}
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
