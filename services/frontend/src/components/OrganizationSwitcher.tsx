import React, { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  ChevronDown,
  ArrowRightLeft,
  Globe,
  Home,
  Lock,
  Settings,
  Sparkles,
  Unlock,
  Users,
} from "lucide-react";
import { api } from "@/lib/api-clean";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { storage, type StoredOrganization } from "@/lib/storage";
import { useToast } from "@/components/ui/use-toast";
import {
  getSpaceSwitcherDisplayName,
  getSpaceSwitcherLabel,
  getSpaceSwitcherTypeTag,
  resolveProvisionedHomeSpaceId,
} from "@/lib/current-space";

const AUTH_UNAVAILABLE_ERROR = "auth_unavailable";

interface Organization {
  id: string;
  name: string;
  slug: string;
  visibility: string;
  description?: string;
  member_count: number;
  is_member: boolean;
  is_current: boolean;
  created_at: string;
  /** §9.4 — viewer's role in this space; "guest" = external invited agent */
  viewer_role?: "owner" | "admin" | "member" | "viewer" | "guest";
  /** Active guest count — only populated for owner/admin callers */
  guest_count?: number;
  is_personal?: boolean | null;
}

interface SpaceSwitcherProps {
  onNavigateToSpaces?: () => void;
}

const mapStoredOrgToOrganization = (
  stored?: StoredOrganization | null,
): Organization | null => {
  if (!stored) return null;
  if (!stored.id && !stored.slug) return null;

  const id = stored.id ?? stored.slug ?? "";
  const slug = stored.slug ?? stored.id ?? "";

  return {
    id,
    name: stored.name ?? slug ?? "Current Space",
    slug,
    visibility: stored.visibility ?? "private",
    description: stored.description,
    member_count: stored.member_count ?? 1,
    is_member: stored.is_member ?? true,
    is_current: stored.is_current ?? true,
    created_at: stored.created_at ?? new Date().toISOString(),
    is_personal: stored.is_personal,
  };
};

function getWorkspaceType(
  org: Organization,
  homeSpaceId?: string | null,
): {
  type: "HOME" | "PRIVATE" | "TEAM" | "COMMUNITY";
  icon: React.ReactNode;
  color: string;
} {
  const type = getSpaceSwitcherTypeTag(org, homeSpaceId);

  switch (type) {
    case "HOME":
      return {
        type,
        icon: <Home className="w-3 h-3" />,
        color: "text-blue-600",
      };
    case "COMMUNITY":
      return {
        type,
        icon: <Globe className="w-3 h-3" />,
        color: "text-green-600",
      };
    case "TEAM":
      return {
        type,
        icon: <Users className="w-3 h-3" />,
        color: "text-purple-600",
      };
    case "PRIVATE":
    default:
      return {
        type: "PRIVATE",
        icon: <Lock className="w-3 h-3" />,
        color: "text-slate-500",
      };
  }
}

export function SpaceSwitcher({ onNavigateToSpaces }: SpaceSwitcherProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [lastKnownOrg, setLastKnownOrg] = useState<Organization | null>(() =>
    mapStoredOrgToOrganization(storage.getCurrentOrganization?.()),
  );

  // Fetch user's organizations
  const {
    data: organizations = [],
    isFetching,
    isLoading,
    refetch,
    error,
  } = useQuery<Organization[]>({
    queryKey: ["organizations"],
    queryFn: async () => {
      const token = await storage.getUserTokenAsync();
      if (!token) {
        const cached = queryClient.getQueryData<Organization[]>([
          "organizations",
        ]);
        if (cached && cached.length > 0) {
          return cached;
        }
        throw new Error(AUTH_UNAVAILABLE_ERROR);
      }

      const result = await api.getOrganizations();
      if (Array.isArray(result)) return result as Organization[];
      if (result && Array.isArray((result as any).organizations)) {
        return (result as any).organizations as Organization[];
      }
      return [];
    },
    placeholderData: () =>
      (queryClient.getQueryData(["organizations"]) as
        | Organization[]
        | undefined) ?? [],
    retry: (failureCount, err) => {
      if (err instanceof Error && err.message === AUTH_UNAVAILABLE_ERROR) {
        return false;
      }
      return failureCount < 2;
    },
    refetchInterval: 30000,
  });

  // When tokens refresh (in any tab), refetch orgs to avoid stale banner
  useEffect(() => {
    const onRefreshed = () => {
      try {
        refetch();
      } catch {} // eslint-disable-line no-empty
    };
    window.addEventListener("auth:token-refreshed", onRefreshed);
    return () =>
      window.removeEventListener("auth:token-refreshed", onRefreshed);
  }, [refetch]);

  // Ensure organizations is always an array before using array methods
  const orgsArray = Array.isArray(organizations) ? organizations : [];
  const currentOrg = orgsArray.find((org: Organization) => org.is_current);
  const otherOrgs = orgsArray.filter(
    (org: Organization) => org.is_member && !org.is_current,
  );
  const homeSpaceId = resolveProvisionedHomeSpaceId(orgsArray);

  useEffect(() => {
    if (currentOrg) {
      setLastKnownOrg(currentOrg);
      storage.setCurrentOrganization({
        id: currentOrg.id,
        name: currentOrg.name,
        slug: currentOrg.slug,
        visibility: currentOrg.visibility,
        description: currentOrg.description,
        member_count: currentOrg.member_count,
        is_member: currentOrg.is_member,
        is_current: currentOrg.is_current,
        created_at: currentOrg.created_at,
        is_personal: currentOrg.is_personal ?? undefined,
      });
    }
  }, [currentOrg]);

  useEffect(() => {
    if (!currentOrg && orgsArray.length === 0) {
      const authUnavailable =
        error instanceof Error && error.message === AUTH_UNAVAILABLE_ERROR;
      const hasBlockingError =
        error instanceof Error && error.message !== AUTH_UNAVAILABLE_ERROR;
      if (!isLoading && !isFetching && !authUnavailable && !hasBlockingError) {
        setLastKnownOrg(null);
        storage.setCurrentOrganization(null);
      }
    }
  }, [currentOrg, orgsArray.length, isLoading, isFetching, error]);

  const effectiveCurrentOrg = currentOrg ?? lastKnownOrg;
  const authUnavailable =
    error instanceof Error && error.message === AUTH_UNAVAILABLE_ERROR;

  const handleSwitchOrganization = async (org: Organization) => {
    try {
      const response = await api.switchOrganization(org.id);

      // Update stored token with new org context
      if (response.new_token) {
        storage.setUserToken(response.new_token);
      }

      toast({
        title: "Space Switched",
        description: `Switched to ${org.name}`,
      });

      // Refetch to update current space status
      queryClient.invalidateQueries({ queryKey: ["organizations"] });
      queryClient.invalidateQueries({ queryKey: ["agents"] });

      storage.setCurrentOrganization({
        id: org.id,
        name: org.name,
        slug: org.slug,
        visibility: org.visibility,
        description: org.description,
        member_count: org.member_count,
        is_member: org.is_member,
        is_current: true,
        created_at: org.created_at,
        is_personal: org.is_personal ?? undefined,
      });

      // Full reload is intentional: switching spaces changes all scoped content
      // (messages, tasks, cached data) and ensures a clean state.
      // Reload at current page (messages/tasks/agents) to maintain navigation context
      window.location.reload();
    } catch (error: any) {
      toast({
        title: "Error",
        description: error.response?.data?.detail || "Failed to switch space",
        variant: "destructive",
      });
    }
  };

  // Avoid showing "Join Space" prematurely while auth/orgs are initializing
  if (!effectiveCurrentOrg) {
    if (isLoading || isFetching || authUnavailable || Boolean(error)) {
      return (
        <Button variant="outline" size="sm" disabled className="h-8 opacity-70">
          <Sparkles className="w-4 h-4 mr-2 animate-pulse" />
          Loading…
        </Button>
      );
    }
    return (
      <Button
        variant="outline"
        size="sm"
        onClick={onNavigateToSpaces}
        className="h-8"
      >
        <Sparkles className="w-4 h-4 mr-2" />
        Join Space
      </Button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-8 max-w-full min-w-0 justify-start sm:w-fit sm:max-w-[20rem]"
          aria-label={`Current workspace: ${getSpaceSwitcherLabel(effectiveCurrentOrg, homeSpaceId)}`}
        >
          {(() => {
            const ws = getWorkspaceType(effectiveCurrentOrg, homeSpaceId);
            return (
              <span className={`mr-2 flex h-4 w-4 shrink-0 ${ws.color}`}>
                {ws.icon}
              </span>
            );
          })()}
          <span className="min-w-0 flex-1 truncate text-left sm:max-w-[14rem] sm:flex-none">
            {getSpaceSwitcherLabel(effectiveCurrentOrg, homeSpaceId)}
          </span>
          <ChevronDown className="ml-2 h-3 w-3 shrink-0" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="max-h-[400px] w-[min(22rem,calc(100vw-1rem))] overflow-y-auto"
      >
        <DropdownMenuLabel className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-blue-600" />
          Current Space
        </DropdownMenuLabel>
        <DropdownMenuItem className="flex cursor-default items-center justify-between gap-3 p-3">
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 truncate font-medium">
                {getSpaceSwitcherDisplayName(effectiveCurrentOrg)}
              </span>
              {(() => {
                const workspace = getWorkspaceType(
                  effectiveCurrentOrg,
                  homeSpaceId,
                );
                return (
                  <div
                    className={`flex shrink-0 items-center gap-1 ${workspace.color}`}
                  >
                    {workspace.icon}
                    <span className="text-xs font-medium">
                      {workspace.type}
                    </span>
                  </div>
                );
              })()}
            </div>
            <span className="truncate text-xs text-muted-foreground">
              @{effectiveCurrentOrg.slug} • {effectiveCurrentOrg.member_count}{" "}
              {effectiveCurrentOrg.member_count === 1 ? "member" : "members"}
            </span>
          </div>
          <Badge
            variant="outline"
            className="shrink-0 border-blue-200 bg-blue-50 text-xs text-blue-700"
          >
            Current
          </Badge>
        </DropdownMenuItem>

        {otherOrgs.length > 0 && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Switch Space</DropdownMenuLabel>
            {otherOrgs.map((org: Organization) => (
              <DropdownMenuItem
                key={org.id}
                onClick={() => handleSwitchOrganization(org)}
                className="flex cursor-pointer items-center justify-between gap-3 p-3"
              >
                <div className="flex min-w-0 flex-1 flex-col">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <span className="min-w-0 flex-1 truncate font-medium">
                      {getSpaceSwitcherDisplayName(org)}
                    </span>
                    {(() => {
                      const workspace = getWorkspaceType(org, homeSpaceId);
                      return (
                        <div
                          className={`flex shrink-0 items-center gap-1 ${workspace.color}`}
                        >
                          {workspace.icon}
                          <span className="text-xs font-medium">
                            {workspace.type}
                          </span>
                        </div>
                      );
                    })()}
                    {/* §9.4 — Guest badge: shown when current agent is an external guest */}
                    {org.viewer_role === "guest" && (
                      <Badge
                        variant="outline"
                        className="text-xs border-amber-400 text-amber-600 dark:text-amber-400 gap-1"
                        title="You are a guest in this space"
                      >
                        <Unlock className="w-3 h-3" />
                        Guest
                      </Badge>
                    )}
                  </div>
                  <span className="truncate text-xs text-muted-foreground">
                    @{org.slug} • {org.member_count}{" "}
                    {org.member_count === 1 ? "member" : "members"}
                  </span>
                </div>
                <ArrowRightLeft className="h-4 w-4 shrink-0 text-muted-foreground" />
              </DropdownMenuItem>
            ))}
          </>
        )}

        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={onNavigateToSpaces}
          className="flex items-center p-3 cursor-pointer"
        >
          <Settings className="w-4 h-4 mr-2" />
          Manage Spaces
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// Compatibility alias for existing imports
export const OrganizationSwitcher = SpaceSwitcher;
