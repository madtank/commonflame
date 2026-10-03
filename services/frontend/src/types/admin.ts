export type AdminRole =
  | "user"
  | "plus"
  | "agent_manager"
  | "admin"
  | "super_admin";

export type AdminUserStatus = "active" | "inactive";

export interface AdminUser {
  id: string;
  username?: string | null;
  email?: string | null;
  role?: AdminRole | string | null;
  status?: AdminUserStatus | string | null;
  created_at?: string | null;
  last_activity?: string | null;
  last_message_at?: string | null;
  agent_count?: number;
  message_count?: number;
  task_count?: number;
  org_count?: number;
  activity_status?: string | null;
  violations_count?: number;
}

export interface AdminUsersResponse {
  users: AdminUser[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}
