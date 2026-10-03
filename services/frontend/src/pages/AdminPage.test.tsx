import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";
import { render, screen, userEvent, waitFor } from "@/test/utils";
import { AdminPage } from "./AdminPage";

const {
  getUserProfileMock,
  adminUsersMock,
  adminUpdateUserRoleMock,
  adminUpdateUserStatusMock,
  adminAccessRequestsMock,
  adminApproveAccessRequestMock,
  adminDenyAccessRequestMock,
  adminResetAccessRequestMock,
  getActivityReportMock,
} = vi.hoisted(() => ({
  getUserProfileMock: vi.fn(),
  adminUsersMock: vi.fn(),
  adminUpdateUserRoleMock: vi.fn(),
  adminUpdateUserStatusMock: vi.fn(),
  adminAccessRequestsMock: vi.fn(),
  adminApproveAccessRequestMock: vi.fn(),
  adminDenyAccessRequestMock: vi.fn(),
  adminResetAccessRequestMock: vi.fn(),
  getActivityReportMock: vi.fn(),
}));

vi.mock("@/lib/api-clean", () => ({
  api: {
    getUserProfile: getUserProfileMock,
    adminUsers: adminUsersMock,
    adminUpdateUserRole: adminUpdateUserRoleMock,
    adminUpdateUserStatus: adminUpdateUserStatusMock,
    adminAccessRequests: adminAccessRequestsMock,
    adminApproveAccessRequest: adminApproveAccessRequestMock,
    adminDenyAccessRequest: adminDenyAccessRequestMock,
    adminResetAccessRequest: adminResetAccessRequestMock,
    getActivityReport: getActivityReportMock,
  },
}));

vi.mock("@/components/ui/use-toast", () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

describe("AdminPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getUserProfileMock.mockResolvedValue({
      id: "admin-user",
      role: "admin",
    });
    adminAccessRequestsMock.mockResolvedValue({
      requests: [],
      counts: { pending: 0, approved: 0, denied: 0 },
      total: 0,
    });
    getActivityReportMock.mockResolvedValue({
      period_days: 7,
      new_signups: [],
      login_events: 0,
      active_users: [],
      active_users_total: 0,
      dormant_users: [],
      dormant_users_total: 0,
      dormant_days: 14,
      pending_access_requests: 0,
    });
    adminUsersMock.mockResolvedValue({
      users: [
        {
          id: "user-1",
          username: "alice",
          email: "alice@example.com",
          role: "user",
          status: "active",
          agent_count: 2,
          message_count: 14,
          task_count: 3,
          org_count: 1,
          created_at: "2026-03-25T00:00:00Z",
          last_message_at: "2026-03-28T12:00:00Z",
          activity_status: "Active Recently",
        },
        {
          id: "user-2",
          username: null,
          email: "bob@example.com",
          role: "plus",
          status: "inactive",
          agent_count: 0,
          message_count: 0,
          task_count: 0,
          org_count: 1,
          created_at: "2026-03-20T00:00:00Z",
          last_message_at: null,
          activity_status: "Dormant",
        },
      ],
      total: 2,
      limit: 50,
      offset: 0,
      has_more: false,
    });
    adminUpdateUserRoleMock.mockResolvedValue({ success: true });
    adminUpdateUserStatusMock.mockResolvedValue({ success: true });
  });

  it("renders admin users and exposes a manage action", async () => {
    render(<AdminPage />);

    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.getAllByText("bob@example.com")).toHaveLength(2);
    expect(
      screen.getByRole("button", { name: /manage alice/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /manage bob@example.com/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Promote users without the legacy dashboard"),
    ).toBeInTheDocument();
  });

  it("promotes a user to plus from the inline management panel", async () => {
    const user = userEvent.setup();
    render(<AdminPage />);

    await user.click(
      await screen.findByRole("button", { name: /manage alice/i }),
    );
    await user.click(
      screen.getByRole("button", { name: /set alice to plus/i }),
    );

    await waitFor(() => {
      expect(adminUpdateUserRoleMock).toHaveBeenCalledWith("user-1", {
        role: "plus",
      });
    });
  });

  it("shows the backend error instead of an empty table when admin users fail to load", async () => {
    adminUsersMock.mockRejectedValueOnce({
      response: { data: { detail: "Admin access required" } },
    });

    render(<AdminPage />);

    expect(
      await screen.findByText("Admin users could not be loaded"),
    ).toBeInTheDocument();
    expect(screen.getByText("Admin access required")).toBeInTheDocument();
  });
});
