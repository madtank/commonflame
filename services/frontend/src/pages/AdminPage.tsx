import {
  Fragment,
  useDeferredValue,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronsUpDown,
  Github,
  ListFilter,
  Loader2,
  LogIn,
  Mail,
  Moon,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  UserCheck,
  Users,
  X,
} from "lucide-react";
import { api, type ActivityReportUser } from "@/lib/api-clean";
import { cn } from "@/lib/utils";
import { FleetControlPanel } from "@/components/ax-platform/FleetControlPanel";
import { useToast } from "@/components/ui/use-toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { AdminRole, AdminUser, AdminUsersResponse } from "@/types";

const PAGE_SIZE = 50;
const MANAGEABLE_ROLES: AdminRole[] = [
  "user",
  "plus",
  "agent_manager",
  "admin",
];
const FILTER_ROLES: Array<AdminRole | "all"> = [
  "all",
  "user",
  "plus",
  "agent_manager",
  "admin",
  "super_admin",
];

type StatusFilter = "all" | "active" | "inactive";

const STATUS_FILTERS: Array<{ value: StatusFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" },
];

type JoinedFilter = "any" | "1" | "7" | "30";

const JOINED_FILTERS: Array<{ value: JoinedFilter; label: string }> = [
  { value: "any", label: "Any time" },
  { value: "1", label: "Last 24 hours" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
];

// Data-grid column header sorting. Sorting is server-side via the backend
// `sort_by` / `sort_dir` query params; the default view is created/desc
// (newest first).
type SortBy = "created" | "username" | "email" | "role" | "status";
type SortDir = "asc" | "desc";

type CurrentUser = {
  id?: string;
  role?: string;
};

type AccessRequestStatus = "pending" | "approved" | "denied";

type AccessRequest = {
  id: string;
  email: string | null;
  full_name: string | null;
  github_username: string | null;
  status: AccessRequestStatus;
  created_at: string | null;
  decided_at: string | null;
  decided_by: string | null;
};

type AccessRequestsResponse = {
  requests: AccessRequest[];
  counts: { pending: number; approved: number; denied: number };
  total: number;
};

// Activity report range options. The backend accepts 1-365 days; these are
// the curated presets surfaced in the UI (default 7).
const ACTIVITY_RANGES = [7, 30, 90] as const;
type ActivityRange = (typeof ACTIVITY_RANGES)[number];

const ACCESS_REQUEST_FILTERS: Array<AccessRequestStatus | "all"> = [
  "pending",
  "approved",
  "denied",
  "all",
];

const ACCESS_REQUEST_FILTER_LABELS: Record<string, string> = {
  pending: "Pending",
  approved: "Approved",
  denied: "Denied",
  all: "All",
};

function accessStatusBadgeClass(status: string) {
  switch (status) {
    case "approved":
      return "border-emerald-500/30 bg-emerald-500/10 text-emerald-100";
    case "denied":
      return "border-rose-500/30 bg-rose-500/10 text-rose-200";
    case "pending":
    default:
      return "border-amber-500/30 bg-amber-500/10 text-amber-100";
  }
}

/**
 * Data-grid style sortable column header. Clicking the label toggles the sort
 * direction when the column is already active, otherwise it activates the
 * column with a sensible default direction. The active column shows a ▲/▼
 * indicator; inactive sortable columns show a neutral up/down glyph.
 */
function SortButton({
  label,
  column,
  sortBy,
  sortDir,
  onSort,
  defaultDir = "asc",
}: {
  label: string;
  column: SortBy;
  sortBy: SortBy;
  sortDir: SortDir;
  onSort: (column: SortBy, dir: SortDir) => void;
  defaultDir?: SortDir;
}) {
  const isActive = sortBy === column;
  return (
    <button
      type="button"
      onClick={() =>
        onSort(
          column,
          isActive ? (sortDir === "asc" ? "desc" : "asc") : defaultDir,
        )
      }
      aria-label={`Sort by ${label}${
        isActive ? ` (${sortDir === "asc" ? "ascending" : "descending"})` : ""
      }`}
      className={cn(
        "group inline-flex items-center gap-1 text-left transition-colors hover:text-foreground",
        isActive ? "text-foreground" : "text-muted-foreground",
      )}
    >
      <span>{label}</span>
      {isActive ? (
        sortDir === "asc" ? (
          <ArrowUp className="h-3.5 w-3.5" />
        ) : (
          <ArrowDown className="h-3.5 w-3.5" />
        )
      ) : (
        <ChevronsUpDown className="h-3.5 w-3.5 opacity-40 group-hover:opacity-70" />
      )}
    </button>
  );
}

/**
 * Small funnel affordance that opens a dropdown of filter options. Highlights
 * (accent color + dot) when a non-default option is selected so it's obvious
 * the column is filtered.
 */
function FilterMenu<T extends string>({
  label,
  value,
  options,
  onChange,
  isActive,
}: {
  label: string;
  value: T;
  options: Array<{ value: T; label: string }>;
  onChange: (value: T) => void;
  isActive: boolean;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`Filter by ${label}`}
          className={cn(
            "relative inline-flex h-6 w-6 items-center justify-center rounded-md transition-colors hover:bg-muted",
            isActive ? "text-cyan-300" : "text-muted-foreground",
          )}
        >
          <ListFilter className="h-3.5 w-3.5" />
          {isActive ? (
            <span className="absolute -right-0 -top-0 h-1.5 w-1.5 rounded-full bg-cyan-400" />
          ) : null}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-[10rem]">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(next) => onChange(next as T)}
        >
          {options.map((option) => (
            <DropdownMenuRadioItem key={option.value} value={option.value}>
              {option.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const ROLE_LABELS: Record<string, string> = {
  user: "User",
  plus: "Plus",
  agent_manager: "Agent Manager",
  admin: "Admin",
  super_admin: "Super Admin",
};

function roleLabel(role: string) {
  return ROLE_LABELS[role] || role.replace(/_/g, " ");
}

function roleBadgeClass(role: string) {
  switch (role) {
    case "admin":
    case "super_admin":
      return "border-rose-500/30 bg-rose-500/10 text-rose-200";
    case "agent_manager":
      return "border-amber-500/30 bg-amber-500/10 text-amber-100";
    case "plus":
      return "border-cyan-500/30 bg-cyan-500/10 text-cyan-100";
    case "user":
    default:
      return "border-slate-600/60 bg-slate-800/70 text-slate-200";
  }
}

function statusBadgeClass(status: string) {
  return status === "active"
    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-100"
    : "border-slate-600/60 bg-slate-800/70 text-slate-300";
}

function formatDate(value?: string | null) {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatActivity(user: AdminUser) {
  return (
    user.activity_status || formatDate(user.last_activity || user.created_at)
  );
}

function getInitials(username?: string | null) {
  const source = (username || "?").trim();
  return source.slice(0, 2).toUpperCase();
}

function getUserLabel(user: AdminUser, index?: number) {
  const username = user.username?.trim();
  if (username) return username;

  const email = user.email?.trim();
  if (email) return email;

  return typeof index === "number" ? `User ${index + 1}` : "Unknown user";
}

function normalizeAdminUsers(users: AdminUsersResponse["users"] | unknown) {
  if (!Array.isArray(users)) return [] as AdminUser[];

  return users.map((rawUser, index) => {
    const user = (rawUser ?? {}) as AdminUser;
    const username = user.username?.trim() || null;
    const email = user.email?.trim() || null;

    return {
      ...user,
      username: username || email || `user-${index + 1}`,
      email,
      role:
        typeof user.role === "string" && user.role.trim() ? user.role : "user",
      status:
        typeof user.status === "string" && user.status.trim()
          ? user.status
          : "active",
    } satisfies AdminUser;
  });
}

function totalPages(total: number) {
  return Math.max(1, Math.ceil(total / PAGE_SIZE));
}

function pageWindow(page: number, pageCount: number) {
  const start = Math.max(1, page - 2);
  const end = Math.min(pageCount, start + 4);
  return Array.from({ length: end - start + 1 }, (_, index) => start + index);
}

function queryErrorMessage(error: unknown) {
  if (
    typeof error === "object" &&
    error !== null &&
    "response" in error &&
    typeof (error as { response?: { data?: { detail?: string } } }).response
      ?.data?.detail === "string"
  ) {
    return (error as { response: { data: { detail: string } } }).response.data
      .detail;
  }
  if (error instanceof Error && error.message) return error.message;
  return "The request failed.";
}

function StatCard({
  title,
  value,
  description,
  icon,
}: {
  title: string;
  value: string | number;
  description: string;
  icon: ReactNode;
}) {
  return (
    <Card className="border-border/70 bg-card/80 shadow-none">
      <CardContent className="flex items-start justify-between gap-4 p-5">
        <div className="space-y-1">
          <div className="text-xs uppercase tracking-[0.22em] text-muted-foreground">
            {title}
          </div>
          <div className="text-2xl font-semibold text-foreground">{value}</div>
          <div className="text-sm text-muted-foreground">{description}</div>
        </div>
        <div className="rounded-2xl border border-cyan-500/20 bg-cyan-500/10 p-3 text-cyan-200">
          {icon}
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * Username / email / last-seen table shared by the activity report's active
 * and dormant user lists. The backend caps each list at 200 rows; when the
 * accompanying `*_total` is larger we surface a truncation notice.
 */
function ActivityUsersTable({
  title,
  emptyLabel,
  users,
  total,
}: {
  title: string;
  emptyLabel: string;
  users: ActivityReportUser[];
  total: number;
}) {
  return (
    <div data-activity-table className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-[0.22em] text-muted-foreground">
        {title}
      </h3>
      <div className="overflow-hidden rounded-3xl border border-border/70">
        <Table>
          <TableHeader className="bg-muted/60">
            <TableRow className="border-border/70">
              <TableHead>Username</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Last seen</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.length ? (
              users.map((user, index) => (
                <TableRow
                  key={user.username || user.email || index}
                  className="border-border/70 bg-card/20"
                >
                  <TableCell className="text-sm text-foreground">
                    {user.username || "—"}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {user.email || "—"}
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {formatDate(user.last_login_at)}
                  </TableCell>
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell
                  colSpan={3}
                  className="h-20 text-center text-sm text-muted-foreground"
                >
                  {emptyLabel}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      {users.length < total ? (
        <p className="text-xs text-muted-foreground">
          Showing first {users.length} of {total}.
        </p>
      ) : null}
    </div>
  );
}

export function AdminPage() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState("");
  const [roleFilter, setRoleFilter] = useState<AdminRole | "all">("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [createdWithin, setCreatedWithin] = useState<JoinedFilter>("any");
  const [sortBy, setSortBy] = useState<SortBy>("created");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [expandedUserId, setExpandedUserId] = useState<string | null>(null);
  const deferredSearch = useDeferredValue(searchInput.trim());

  const currentUserQuery = useQuery({
    queryKey: ["admin-page", "current-user"],
    queryFn: async () => (await api.getUserProfile()) as CurrentUser | null,
    staleTime: 60_000,
  });

  // Applying any sort/filter change resets pagination back to the first page.
  const handleSort = (column: SortBy, dir: SortDir) => {
    setSortBy(column);
    setSortDir(dir);
    setPage(1);
  };

  const usersQuery = useQuery({
    queryKey: [
      "admin-page",
      "users",
      page,
      deferredSearch,
      roleFilter,
      statusFilter,
      createdWithin,
      sortBy,
      sortDir,
    ],
    queryFn: async () => {
      const response = (await api.adminUsers(
        PAGE_SIZE,
        (page - 1) * PAGE_SIZE,
        deferredSearch || undefined,
        roleFilter === "all" ? undefined : roleFilter,
        {
          status: statusFilter === "all" ? undefined : statusFilter,
          created_within_days:
            createdWithin === "any" ? undefined : Number(createdWithin),
          sort_by: sortBy,
          sort_dir: sortDir,
        },
      )) as AdminUsersResponse;

      // Sorting is server-side now — preserve the backend ordering.
      const users = normalizeAdminUsers(response.users);

      return {
        ...response,
        users,
      };
    },
  });

  const pageCount = totalPages(usersQuery.data?.total || 0);
  const activeUsers =
    usersQuery.data?.users.filter((user) => user.status === "active").length ||
    0;
  const plusUsers =
    usersQuery.data?.users.filter((user) => user.role === "plus").length || 0;

  useEffect(() => {
    setExpandedUserId(null);
  }, [
    page,
    deferredSearch,
    roleFilter,
    statusFilter,
    createdWithin,
    sortBy,
    sortDir,
  ]);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const roleMutation = useMutation({
    mutationFn: async ({
      userId,
      role,
      username,
    }: {
      userId: string;
      role: AdminRole;
      username: string;
    }) => {
      await api.adminUpdateUserRole(userId, { role });
      return { role, username };
    },
    onSuccess: ({ role, username }) => {
      void queryClient.invalidateQueries({ queryKey: ["admin-page", "users"] });
      toast({
        title: "Role updated",
        description: `${username} is now ${roleLabel(role)}.`,
      });
    },
    onError: (error) => {
      toast({
        title: "Role update failed",
        description: queryErrorMessage(error),
        variant: "destructive",
      });
    },
  });

  const statusMutation = useMutation({
    mutationFn: async ({
      userId,
      isActive,
      username,
    }: {
      userId: string;
      isActive: boolean;
      username: string;
    }) => {
      await api.adminUpdateUserStatus(userId, { is_active: isActive });
      return { isActive, username };
    },
    onSuccess: ({ isActive, username }) => {
      void queryClient.invalidateQueries({ queryKey: ["admin-page", "users"] });
      toast({
        title: isActive ? "User reactivated" : "User deactivated",
        description: `${username} is now ${isActive ? "active" : "inactive"}.`,
      });
    },
    onError: (error) => {
      toast({
        title: "Status update failed",
        description: queryErrorMessage(error),
        variant: "destructive",
      });
    },
  });

  const [activityDays, setActivityDays] = useState<ActivityRange>(7);

  const activityReportQuery = useQuery({
    queryKey: ["admin-page", "activity-report", activityDays],
    queryFn: async () => await api.getActivityReport(activityDays),
    // Keep the previous report visible while a new range loads instead of
    // collapsing the section back into the spinner.
    placeholderData: keepPreviousData,
  });

  const [accessStatusFilter, setAccessStatusFilter] = useState<
    AccessRequestStatus | "all"
  >("pending");

  const accessRequestsQuery = useQuery({
    queryKey: ["admin-page", "access-requests", accessStatusFilter],
    queryFn: async () =>
      (await api.adminAccessRequests(
        accessStatusFilter,
      )) as AccessRequestsResponse,
  });

  const accessRequestMutation = useMutation({
    mutationFn: async ({
      id,
      action,
    }: {
      id: string;
      action: "approve" | "deny" | "reset";
      email: string;
    }) => {
      if (action === "approve") await api.adminApproveAccessRequest(id);
      else if (action === "deny") await api.adminDenyAccessRequest(id);
      else await api.adminResetAccessRequest(id);
      return { action };
    },
    onSuccess: ({ action }, { email }) => {
      void queryClient.invalidateQueries({
        queryKey: ["admin-page", "access-requests"],
      });
      // Approve may re-enable a matching disabled user, so refresh users too.
      void queryClient.invalidateQueries({ queryKey: ["admin-page", "users"] });
      // Decisions change pending_access_requests, so refresh the activity
      // report to keep its pending-count chip in sync.
      void queryClient.invalidateQueries({
        queryKey: ["admin-page", "activity-report"],
      });
      const verb =
        action === "approve"
          ? "approved"
          : action === "deny"
            ? "denied"
            : "reset to pending";
      toast({
        title: "Access request updated",
        description: `${email || "Request"} ${verb}.`,
      });
    },
    onError: (error) => {
      toast({
        title: "Access request update failed",
        description: queryErrorMessage(error),
        variant: "destructive",
      });
    },
  });

  const pendingAccessCount = accessRequestsQuery.data?.counts?.pending ?? 0;

  const currentUserId = currentUserQuery.data?.id;
  const currentUserRole = currentUserQuery.data?.role;
  const pagination = pageWindow(page, pageCount);

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6">
      <Card className="overflow-hidden border-border/70 bg-[radial-gradient(circle_at_top_left,_rgba(34,211,238,0.12),_transparent_32%),linear-gradient(180deg,rgba(15,23,42,0.96),rgba(15,23,42,0.9))] shadow-none">
        <CardHeader className="gap-3">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-2">
              <Badge className="border-cyan-500/25 bg-cyan-500/10 text-cyan-100">
                Admin access
              </Badge>
              <CardTitle className="text-3xl font-semibold tracking-tight text-white">
                Promote users without the legacy dashboard
              </CardTitle>
              <CardDescription className="max-w-3xl text-slate-300">
                This page is the clean v1 admin surface for user access
                management. Search, verify, then promote users to{" "}
                <span className="font-medium text-cyan-100">Plus</span> or
                adjust their platform role without falling back to the old
                broken dashboard.
              </CardDescription>
            </div>
            <Button
              variant="outline"
              className="border-border bg-background/60 text-foreground hover:bg-muted"
              onClick={() =>
                void queryClient.invalidateQueries({
                  queryKey: ["admin-page", "users"],
                })
              }
            >
              {usersQuery.isFetching ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" />
              )}
              Refresh
            </Button>
          </div>
        </CardHeader>
      </Card>

      <div className="grid gap-4 md:grid-cols-3">
        <StatCard
          title="Visible users"
          value={usersQuery.data?.total || 0}
          description="Results after the current search/filter."
          icon={<Users className="h-5 w-5" />}
        />
        <StatCard
          title="Plus members"
          value={plusUsers}
          description="Users already promoted to the Plus role on this page."
          icon={<Sparkles className="h-5 w-5" />}
        />
        <StatCard
          title="Active"
          value={activeUsers}
          description="Accounts currently able to sign in and use the product."
          icon={<ShieldCheck className="h-5 w-5" />}
        />
      </div>

      <FleetControlPanel />

      <Card className="border-border/70 bg-card/80 shadow-none">
        <CardHeader className="gap-4">
          <div className="space-y-2">
            <Label htmlFor="admin-user-search">Search users</Label>
            <div className="relative max-w-md">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="admin-user-search"
                value={searchInput}
                onChange={(event) => {
                  setSearchInput(event.target.value);
                  setPage(1);
                }}
                placeholder="Search by username or email"
                className="border-border bg-background pl-10 text-foreground"
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Click a column header to sort; use the funnel to filter.
            </p>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="overflow-hidden rounded-3xl border border-border/70">
            <Table>
              <TableHeader className="bg-muted/60">
                <TableRow className="border-border/70">
                  <TableHead>
                    <SortButton
                      label="User"
                      column="username"
                      sortBy={sortBy}
                      sortDir={sortDir}
                      onSort={handleSort}
                    />
                  </TableHead>
                  <TableHead>
                    <SortButton
                      label="Email"
                      column="email"
                      sortBy={sortBy}
                      sortDir={sortDir}
                      onSort={handleSort}
                    />
                  </TableHead>
                  <TableHead>
                    <div className="flex items-center gap-1">
                      <SortButton
                        label="Role"
                        column="role"
                        sortBy={sortBy}
                        sortDir={sortDir}
                        onSort={handleSort}
                      />
                      <FilterMenu
                        label="Role"
                        value={roleFilter}
                        isActive={roleFilter !== "all"}
                        onChange={(value) => {
                          setRoleFilter(value);
                          setPage(1);
                        }}
                        options={FILTER_ROLES.map((role) => ({
                          value: role,
                          label: role === "all" ? "All roles" : roleLabel(role),
                        }))}
                      />
                    </div>
                  </TableHead>
                  <TableHead>
                    <div className="flex items-center gap-1">
                      <SortButton
                        label="Status"
                        column="status"
                        sortBy={sortBy}
                        sortDir={sortDir}
                        onSort={handleSort}
                      />
                      <FilterMenu
                        label="Status"
                        value={statusFilter}
                        isActive={statusFilter !== "all"}
                        onChange={(value) => {
                          setStatusFilter(value);
                          setPage(1);
                        }}
                        options={STATUS_FILTERS}
                      />
                    </div>
                  </TableHead>
                  <TableHead>
                    <div className="flex items-center gap-1">
                      <SortButton
                        label="Created"
                        column="created"
                        sortBy={sortBy}
                        sortDir={sortDir}
                        onSort={handleSort}
                        defaultDir="desc"
                      />
                      <FilterMenu
                        label="Created within"
                        value={createdWithin}
                        isActive={createdWithin !== "any"}
                        onChange={(value) => {
                          setCreatedWithin(value);
                          setPage(1);
                        }}
                        options={JOINED_FILTERS}
                      />
                    </div>
                  </TableHead>
                  <TableHead>Activity</TableHead>
                  <TableHead>Usage</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usersQuery.isLoading ? (
                  <TableRow>
                    <TableCell colSpan={8} className="h-48 text-center">
                      <div className="flex flex-col items-center justify-center gap-3 text-muted-foreground">
                        <Loader2 className="h-6 w-6 animate-spin" />
                        Loading admin users…
                      </div>
                    </TableCell>
                  </TableRow>
                ) : usersQuery.isError ? (
                  <TableRow>
                    <TableCell colSpan={8} className="h-48 text-center">
                      <div className="mx-auto flex max-w-xl flex-col items-center justify-center gap-3 text-center">
                        <ShieldCheck className="h-6 w-6 text-amber-300" />
                        <div className="space-y-1">
                          <div className="font-medium text-foreground">
                            Admin users could not be loaded
                          </div>
                          <div className="text-sm text-muted-foreground">
                            {queryErrorMessage(usersQuery.error)}
                          </div>
                        </div>
                      </div>
                    </TableCell>
                  </TableRow>
                ) : usersQuery.data?.users.length ? (
                  usersQuery.data.users.map((user, index) => {
                    const isExpanded = expandedUserId === user.id;
                    const isSelf = currentUserId === user.id;
                    const isRolePending =
                      roleMutation.isPending &&
                      roleMutation.variables?.userId === user.id;
                    const isStatusPending =
                      statusMutation.isPending &&
                      statusMutation.variables?.userId === user.id;
                    const userLabel = getUserLabel(user, index);

                    return (
                      <Fragment key={user.id}>
                        <TableRow className="border-border/70 bg-card/20">
                          <TableCell>
                            <div className="flex items-center gap-3">
                              <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-cyan-500/20 bg-cyan-500/10 text-sm font-semibold text-cyan-100">
                                {getInitials(userLabel)}
                              </div>
                              <div className="space-y-1">
                                <div className="font-medium text-foreground">
                                  {userLabel}
                                </div>
                              </div>
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-2 text-sm text-muted-foreground">
                              <Mail className="h-4 w-4" />
                              <span>{user.email || "—"}</span>
                            </div>
                          </TableCell>
                          <TableCell>
                            <Badge
                              className={cn(
                                "border font-medium",
                                roleBadgeClass(user.role || "user"),
                              )}
                            >
                              {roleLabel(user.role || "user")}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <Badge
                              className={cn(
                                "border font-medium",
                                statusBadgeClass(user.status || "active"),
                              )}
                            >
                              {user.status === "active" ? "Active" : "Inactive"}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-sm text-muted-foreground">
                            {formatDate(user.created_at)}
                          </TableCell>
                          <TableCell className="text-sm text-muted-foreground">
                            <div>{formatActivity(user)}</div>
                            <div className="text-xs">
                              Last message {formatDate(user.last_message_at)}
                            </div>
                          </TableCell>
                          <TableCell className="text-sm text-muted-foreground">
                            <div>{user.agent_count || 0} agents</div>
                            <div>{user.message_count || 0} messages</div>
                          </TableCell>
                          <TableCell className="text-right">
                            <Button
                              variant={isExpanded ? "default" : "outline"}
                              size="sm"
                              onClick={() =>
                                setExpandedUserId((current) =>
                                  current === user.id ? null : user.id,
                                )
                              }
                              aria-label={`Manage ${userLabel}`}
                            >
                              {isExpanded ? "Close" : "Manage"}
                            </Button>
                          </TableCell>
                        </TableRow>
                        {isExpanded ? (
                          <TableRow className="border-border/70 bg-muted/20">
                            <TableCell colSpan={8} className="p-0">
                              <div className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_320px]">
                                <div className="space-y-4">
                                  <div className="space-y-1">
                                    <div className="text-sm font-medium text-foreground">
                                      Role controls
                                    </div>
                                    <div className="text-sm text-muted-foreground">
                                      Promote this user to Plus or adjust access
                                      without leaving the page.
                                    </div>
                                  </div>
                                  <div className="flex flex-wrap gap-2">
                                    {MANAGEABLE_ROLES.map((role) => {
                                      const isCurrentRole = user.role === role;
                                      const blocksSelfAdminRemoval =
                                        isSelf &&
                                        currentUserRole === "admin" &&
                                        role !== "admin";
                                      const disabled =
                                        isCurrentRole ||
                                        isRolePending ||
                                        blocksSelfAdminRemoval ||
                                        user.role === "super_admin";

                                      return (
                                        <Button
                                          key={role}
                                          size="sm"
                                          variant={
                                            isCurrentRole
                                              ? "default"
                                              : "outline"
                                          }
                                          disabled={disabled}
                                          aria-label={`Set ${userLabel} to ${roleLabel(role)}`}
                                          onClick={() =>
                                            roleMutation.mutate({
                                              userId: user.id,
                                              role,
                                              username: userLabel,
                                            })
                                          }
                                        >
                                          {isRolePending &&
                                          roleMutation.variables?.role ===
                                            role ? (
                                            <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                                          ) : null}
                                          {roleLabel(role)}
                                        </Button>
                                      );
                                    })}
                                  </div>
                                  {user.role === "super_admin" ? (
                                    <p className="text-sm text-muted-foreground">
                                      Super-admin users are displayed here but
                                      not adjusted from this v1 screen.
                                    </p>
                                  ) : null}
                                  {isSelf ? (
                                    <p className="text-sm text-muted-foreground">
                                      Self-service safety is still enforced. You
                                      cannot disable yourself or remove your own
                                      admin access here.
                                    </p>
                                  ) : null}
                                </div>

                                <div className="space-y-4 rounded-3xl border border-border/70 bg-background/80 p-4">
                                  <div className="space-y-1">
                                    <div className="text-sm font-medium text-foreground">
                                      Account state
                                    </div>
                                    <div className="text-sm text-muted-foreground">
                                      Current role, status, and observed
                                      activity for {userLabel}.
                                    </div>
                                  </div>
                                  <div className="flex flex-wrap gap-2">
                                    <Badge
                                      className={cn(
                                        "border",
                                        roleBadgeClass(user.role || "user"),
                                      )}
                                    >
                                      {roleLabel(user.role || "user")}
                                    </Badge>
                                    <Badge
                                      className={cn(
                                        "border",
                                        statusBadgeClass(
                                          user.status || "active",
                                        ),
                                      )}
                                    >
                                      {user.status === "active"
                                        ? "Active"
                                        : "Inactive"}
                                    </Badge>
                                  </div>
                                  <dl className="grid grid-cols-2 gap-3 text-sm">
                                    <div>
                                      <dt className="text-muted-foreground">
                                        Agents
                                      </dt>
                                      <dd className="font-medium text-foreground">
                                        {user.agent_count || 0}
                                      </dd>
                                    </div>
                                    <div>
                                      <dt className="text-muted-foreground">
                                        Messages
                                      </dt>
                                      <dd className="font-medium text-foreground">
                                        {user.message_count || 0}
                                      </dd>
                                    </div>
                                    <div>
                                      <dt className="text-muted-foreground">
                                        Tasks
                                      </dt>
                                      <dd className="font-medium text-foreground">
                                        {user.task_count || 0}
                                      </dd>
                                    </div>
                                    <div>
                                      <dt className="text-muted-foreground">
                                        Organizations
                                      </dt>
                                      <dd className="font-medium text-foreground">
                                        {user.org_count || 0}
                                      </dd>
                                    </div>
                                  </dl>
                                  <Button
                                    variant="outline"
                                    className="w-full border-border bg-background text-foreground hover:bg-muted"
                                    disabled={isSelf || isStatusPending}
                                    aria-label={`${user.status === "active" ? "Deactivate" : "Reactivate"} ${userLabel}`}
                                    onClick={() =>
                                      statusMutation.mutate({
                                        userId: user.id,
                                        isActive: user.status !== "active",
                                        username: userLabel,
                                      })
                                    }
                                  >
                                    {isStatusPending ? (
                                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    ) : null}
                                    {user.status === "active"
                                      ? "Deactivate user"
                                      : "Reactivate user"}
                                  </Button>
                                </div>
                              </div>
                            </TableCell>
                          </TableRow>
                        ) : null}
                      </Fragment>
                    );
                  })
                ) : (
                  <TableRow>
                    <TableCell colSpan={8} className="h-48 text-center">
                      <div className="space-y-2">
                        <div className="text-base font-medium text-foreground">
                          No users matched this view
                        </div>
                        <div className="text-sm text-muted-foreground">
                          Try clearing the search or widening the role filter.
                        </div>
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm text-muted-foreground">
              Page {page} of {pageCount}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                Previous
              </Button>
              {pagination.map((pageNumber) => (
                <Button
                  key={pageNumber}
                  variant={pageNumber === page ? "default" : "outline"}
                  size="sm"
                  onClick={() => setPage(pageNumber)}
                >
                  {pageNumber}
                </Button>
              ))}
              <Button
                variant="outline"
                size="sm"
                disabled={page >= pageCount}
                onClick={() =>
                  setPage((current) => Math.min(pageCount, current + 1))
                }
              >
                Next
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="border-border/70 bg-card/80 shadow-none">
        <CardHeader className="gap-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-2">
              <CardTitle className="text-2xl font-semibold tracking-tight text-white">
                Activity
              </CardTitle>
              <CardDescription className="max-w-3xl text-slate-300">
                Signups and logins over the selected window; dormancy uses a
                fixed platform-wide window. User lists are capped at the first
                200 rows.
              </CardDescription>
            </div>
            <div
              className="flex flex-wrap items-center gap-2"
              role="group"
              aria-label="Activity range"
            >
              {ACTIVITY_RANGES.map((range) => (
                <Button
                  key={range}
                  size="sm"
                  variant={activityDays === range ? "default" : "outline"}
                  className={
                    activityDays === range
                      ? undefined
                      : "border-border bg-background/60 text-foreground hover:bg-muted"
                  }
                  aria-pressed={activityDays === range}
                  onClick={() => setActivityDays(range)}
                >
                  {range} days
                </Button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          {activityReportQuery.isLoading ? (
            <div className="flex h-40 flex-col items-center justify-center gap-3 text-muted-foreground">
              <Loader2 className="h-6 w-6 animate-spin" />
              Loading activity report…
            </div>
          ) : activityReportQuery.isError ? (
            <div className="mx-auto flex h-40 max-w-xl flex-col items-center justify-center gap-3 text-center">
              <ShieldCheck className="h-6 w-6 text-amber-300" />
              <div className="space-y-1">
                <div className="font-medium text-foreground">
                  Activity report could not be loaded
                </div>
                <div className="text-sm text-muted-foreground">
                  {queryErrorMessage(activityReportQuery.error)}
                </div>
              </div>
            </div>
          ) : activityReportQuery.data ? (
            // Labels come from the report itself (period_days), not the
            // selected range: with keepPreviousData the old report stays on
            // screen while a new range loads, and it must keep its own label.
            // The stale content is dimmed until the refetch lands.
            <div
              data-activity-content
              data-stale={
                activityReportQuery.isPlaceholderData ? "true" : undefined
              }
              className={cn(
                "space-y-6 transition-opacity",
                activityReportQuery.isPlaceholderData && "opacity-60",
              )}
            >
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <StatCard
                  title="Login events"
                  value={activityReportQuery.data.login_events}
                  description={`Logins in the last ${activityReportQuery.data.period_days} days.`}
                  icon={<LogIn className="h-5 w-5" />}
                />
                <StatCard
                  title="Pending access requests"
                  value={activityReportQuery.data.pending_access_requests}
                  description="Requests waiting for an approve/deny decision."
                  icon={<Mail className="h-5 w-5" />}
                />
                <StatCard
                  title="Active users"
                  value={activityReportQuery.data.active_users_total}
                  description={`Signed in within the last ${activityReportQuery.data.period_days} days.`}
                  icon={<UserCheck className="h-5 w-5" />}
                />
                <StatCard
                  title="Dormant users"
                  value={activityReportQuery.data.dormant_users_total}
                  description={
                    // Dormancy uses the backend's fixed window, not the
                    // selected range. Older backends omit dormant_days.
                    typeof activityReportQuery.data.dormant_days === "number"
                      ? `No sign-in within the last ${activityReportQuery.data.dormant_days} days.`
                      : "No recent sign-in."
                  }
                  icon={<Moon className="h-5 w-5" />}
                />
              </div>
              <div data-activity-table className="space-y-2">
                <h3 className="text-xs font-semibold uppercase tracking-[0.22em] text-muted-foreground">
                  New signups
                </h3>
                <div className="overflow-hidden rounded-3xl border border-border/70">
                  <Table>
                    <TableHeader className="bg-muted/60">
                      <TableRow className="border-border/70">
                        <TableHead>Username</TableHead>
                        <TableHead>Email</TableHead>
                        <TableHead>Provider</TableHead>
                        <TableHead>Joined</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {activityReportQuery.data.new_signups.length ? (
                        activityReportQuery.data.new_signups.map(
                          (signup, index) => (
                            <TableRow
                              key={signup.username || signup.email || index}
                              className="border-border/70 bg-card/20"
                            >
                              <TableCell className="text-sm text-foreground">
                                {signup.username || "—"}
                              </TableCell>
                              <TableCell className="text-sm text-muted-foreground">
                                {signup.email || "—"}
                              </TableCell>
                              <TableCell className="text-sm capitalize text-muted-foreground">
                                {signup.auth_provider || "—"}
                              </TableCell>
                              <TableCell className="text-sm text-muted-foreground">
                                {formatDate(signup.created_at)}
                              </TableCell>
                            </TableRow>
                          ),
                        )
                      ) : (
                        <TableRow>
                          <TableCell
                            colSpan={4}
                            className="h-20 text-center text-sm text-muted-foreground"
                          >
                            No signups in this window.
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                </div>
              </div>
              <ActivityUsersTable
                title="Active users"
                emptyLabel="No active users in this window."
                users={activityReportQuery.data.active_users}
                total={activityReportQuery.data.active_users_total}
              />
              <ActivityUsersTable
                title="Dormant users"
                emptyLabel="No dormant users in this window."
                users={activityReportQuery.data.dormant_users}
                total={activityReportQuery.data.dormant_users_total}
              />
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card className="border-border/70 bg-card/80 shadow-none">
        <CardHeader className="gap-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle className="text-2xl font-semibold tracking-tight text-white">
                  Access requests
                </CardTitle>
                {pendingAccessCount > 0 ? (
                  <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-100">
                    {pendingAccessCount} pending
                  </Badge>
                ) : null}
              </div>
              <CardDescription className="max-w-3xl text-slate-300">
                Review who is waiting at the invite-only gate. Approve to grant
                access (and re-enable a matching disabled account), deny to
                block, or reset to re-open a decision.
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <div className="w-44 space-y-1">
                <Label htmlFor="access-status-filter" className="sr-only">
                  Status filter
                </Label>
                <Select
                  value={accessStatusFilter}
                  onValueChange={(value: AccessRequestStatus | "all") =>
                    setAccessStatusFilter(value)
                  }
                >
                  <SelectTrigger
                    id="access-status-filter"
                    className="border-border bg-background text-foreground"
                  >
                    <SelectValue placeholder="Pending" />
                  </SelectTrigger>
                  <SelectContent>
                    {ACCESS_REQUEST_FILTERS.map((status) => (
                      <SelectItem key={status} value={status}>
                        {ACCESS_REQUEST_FILTER_LABELS[status]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                variant="outline"
                className="border-border bg-background/60 text-foreground hover:bg-muted"
                onClick={() =>
                  void queryClient.invalidateQueries({
                    queryKey: ["admin-page", "access-requests"],
                  })
                }
              >
                {accessRequestsQuery.isFetching ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="mr-2 h-4 w-4" />
                )}
                Refresh
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="overflow-hidden rounded-3xl border border-border/70">
            <Table>
              <TableHeader className="bg-muted/60">
                <TableRow className="border-border/70">
                  <TableHead>Email</TableHead>
                  <TableHead>Full name</TableHead>
                  <TableHead>GitHub</TableHead>
                  <TableHead>Requested</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accessRequestsQuery.isLoading ? (
                  <TableRow>
                    <TableCell colSpan={6} className="h-40 text-center">
                      <div className="flex flex-col items-center justify-center gap-3 text-muted-foreground">
                        <Loader2 className="h-6 w-6 animate-spin" />
                        Loading access requests…
                      </div>
                    </TableCell>
                  </TableRow>
                ) : accessRequestsQuery.isError ? (
                  <TableRow>
                    <TableCell colSpan={6} className="h-40 text-center">
                      <div className="mx-auto flex max-w-xl flex-col items-center justify-center gap-3 text-center">
                        <ShieldCheck className="h-6 w-6 text-amber-300" />
                        <div className="space-y-1">
                          <div className="font-medium text-foreground">
                            Access requests could not be loaded
                          </div>
                          <div className="text-sm text-muted-foreground">
                            {queryErrorMessage(accessRequestsQuery.error)}
                          </div>
                        </div>
                      </div>
                    </TableCell>
                  </TableRow>
                ) : accessRequestsQuery.data?.requests.length ? (
                  accessRequestsQuery.data.requests.map((request) => {
                    const isRowPending =
                      accessRequestMutation.isPending &&
                      accessRequestMutation.variables?.id === request.id;
                    const email = request.email || "—";

                    return (
                      <TableRow
                        key={request.id}
                        className="border-border/70 bg-card/20"
                      >
                        <TableCell>
                          <div className="flex items-center gap-2 text-sm text-foreground">
                            <Mail className="h-4 w-4 text-muted-foreground" />
                            <span>{email}</span>
                          </div>
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {request.full_name || "—"}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {request.github_username ? (
                            <span className="flex items-center gap-1.5">
                              <Github className="h-4 w-4" />
                              {request.github_username}
                            </span>
                          ) : (
                            "—"
                          )}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {formatDate(request.created_at)}
                        </TableCell>
                        <TableCell>
                          <Badge
                            className={cn(
                              "border font-medium capitalize",
                              accessStatusBadgeClass(request.status),
                            )}
                          >
                            {request.status}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex flex-wrap justify-end gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={
                                isRowPending || request.status === "approved"
                              }
                              aria-label={`Approve ${email}`}
                              onClick={() =>
                                accessRequestMutation.mutate({
                                  id: request.id,
                                  action: "approve",
                                  email: request.email || "",
                                })
                              }
                            >
                              {isRowPending &&
                              accessRequestMutation.variables?.action ===
                                "approve" ? (
                                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Check className="mr-1.5 h-3.5 w-3.5" />
                              )}
                              Approve
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={
                                isRowPending || request.status === "denied"
                              }
                              aria-label={`Deny ${email}`}
                              onClick={() =>
                                accessRequestMutation.mutate({
                                  id: request.id,
                                  action: "deny",
                                  email: request.email || "",
                                })
                              }
                            >
                              {isRowPending &&
                              accessRequestMutation.variables?.action ===
                                "deny" ? (
                                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <X className="mr-1.5 h-3.5 w-3.5" />
                              )}
                              Deny
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={
                                isRowPending || request.status === "pending"
                              }
                              aria-label={`Reset ${email}`}
                              onClick={() =>
                                accessRequestMutation.mutate({
                                  id: request.id,
                                  action: "reset",
                                  email: request.email || "",
                                })
                              }
                            >
                              {isRowPending &&
                              accessRequestMutation.variables?.action ===
                                "reset" ? (
                                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                              )}
                              Reset
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })
                ) : (
                  <TableRow>
                    <TableCell colSpan={6} className="h-40 text-center">
                      <div className="space-y-2">
                        <div className="text-base font-medium text-foreground">
                          No access requests in this view
                        </div>
                        <div className="text-sm text-muted-foreground">
                          Switch the status filter to see approved or denied
                          requests.
                        </div>
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export default AdminPage;
