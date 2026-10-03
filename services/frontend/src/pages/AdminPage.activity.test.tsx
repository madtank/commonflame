import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";
import { render, screen, userEvent, waitFor, within } from "@/test/utils";
import { AdminPage } from "./AdminPage";
import type { ActivityReport } from "@/lib/api-clean";

const {
  getUserProfileMock,
  adminUsersMock,
  adminAccessRequestsMock,
  getActivityReportMock,
} = vi.hoisted(() => ({
  getUserProfileMock: vi.fn(),
  adminUsersMock: vi.fn(),
  adminAccessRequestsMock: vi.fn(),
  getActivityReportMock: vi.fn(),
}));

vi.mock("@/lib/api-clean", () => ({
  api: {
    getUserProfile: getUserProfileMock,
    adminUsers: adminUsersMock,
    adminUpdateUserRole: vi.fn(),
    adminUpdateUserStatus: vi.fn(),
    adminAccessRequests: adminAccessRequestsMock,
    adminApproveAccessRequest: vi.fn(),
    adminDenyAccessRequest: vi.fn(),
    adminResetAccessRequest: vi.fn(),
    getActivityReport: getActivityReportMock,
  },
}));

vi.mock("@/components/ui/use-toast", () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

const baseReport: ActivityReport = {
  period_days: 7,
  new_signups: [
    {
      username: "fresh_face",
      email: "fresh@example.com",
      auth_provider: "github",
      created_at: "2026-06-09T10:00:00Z",
    },
  ],
  login_events: 42,
  active_users: [
    {
      username: "busy_bee",
      email: "busy@example.com",
      last_login_at: "2026-06-10T08:00:00Z",
    },
  ],
  active_users_total: 1,
  dormant_users: [
    {
      username: "sleepy_sam",
      email: "sleepy@example.com",
      last_login_at: null,
    },
  ],
  dormant_users_total: 1,
  dormant_days: 14,
  pending_access_requests: 3,
};

function tableByHeading(headingName: RegExp) {
  const heading = screen.getByRole("heading", { name: headingName });
  const region = heading.closest("[data-activity-table]");
  expect(region).not.toBeNull();
  return within(region as HTMLElement);
}

describe("AdminPage activity report", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getUserProfileMock.mockResolvedValue({ id: "admin-user", role: "admin" });
    adminUsersMock.mockResolvedValue({
      users: [],
      total: 0,
      limit: 50,
      offset: 0,
      has_more: false,
    });
    adminAccessRequestsMock.mockResolvedValue({
      requests: [],
      counts: { pending: 0, approved: 0, denied: 0 },
      total: 0,
    });
    getActivityReportMock.mockResolvedValue(baseReport);
  });

  it("renders summary stats and the three activity tables", async () => {
    render(<AdminPage />);

    expect(await screen.findByText("fresh_face")).toBeInTheDocument();
    expect(getActivityReportMock).toHaveBeenCalledWith(7);

    // Summary chips
    expect(screen.getByText(/login events/i)).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText(/pending access requests/i)).toBeInTheDocument();
    // Chip titles and table headings both mention active/dormant users.
    expect(screen.getAllByText(/active users/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/dormant users/i).length).toBeGreaterThan(0);

    // New signups table
    const signups = tableByHeading(/new signups/i);
    expect(signups.getByText("fresh_face")).toBeInTheDocument();
    expect(signups.getByText("fresh@example.com")).toBeInTheDocument();
    expect(signups.getByText(/github/i)).toBeInTheDocument();

    // Active users table
    const active = tableByHeading(/active users/i);
    expect(active.getByText("busy_bee")).toBeInTheDocument();
    expect(active.getByText("busy@example.com")).toBeInTheDocument();

    // Dormant users table
    const dormant = tableByHeading(/dormant users/i);
    expect(dormant.getByText("sleepy_sam")).toBeInTheDocument();
    expect(dormant.getByText("sleepy@example.com")).toBeInTheDocument();
  });

  it("renders 'never' for users that have no last login", async () => {
    render(<AdminPage />);

    await screen.findByText("sleepy_sam");
    const dormant = tableByHeading(/dormant users/i);
    expect(dormant.getByText(/never/i)).toBeInTheDocument();
  });

  it("refetches with days=30 when switching the range", async () => {
    const user = userEvent.setup();
    render(<AdminPage />);

    await screen.findByText("fresh_face");
    getActivityReportMock.mockResolvedValue({ ...baseReport, period_days: 30 });

    await user.click(screen.getByRole("button", { name: /30 days/i }));

    await waitFor(() => {
      expect(getActivityReportMock).toHaveBeenCalledWith(30);
    });
  });

  it("labels stats with the loaded report's period while a new range is in flight, and dims the stale content", async () => {
    const user = userEvent.setup();
    render(<AdminPage />);

    await screen.findByText("fresh_face");
    expect(
      screen.getByText(/logins in the last 7 days\./i),
    ).toBeInTheDocument();

    // Make the 30-day fetch hang so the previous 7-day report stays on screen.
    let resolveReport!: (report: ActivityReport) => void;
    getActivityReportMock.mockImplementation(
      () =>
        new Promise<ActivityReport>((resolve) => {
          resolveReport = resolve;
        }),
    );

    await user.click(screen.getByRole("button", { name: /30 days/i }));

    await waitFor(() => {
      expect(getActivityReportMock).toHaveBeenCalledWith(30);
    });

    // The stale data must keep its own period label, not the new selection.
    expect(
      screen.getByText(/logins in the last 7 days\./i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/logins in the last 30 days\./i),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/signed in within the last 7 days\./i),
    ).toBeInTheDocument();

    // And the section is visibly dimmed while the refetch is in flight.
    const content = document.querySelector(
      "[data-activity-content]",
    ) as HTMLElement;
    expect(content).not.toBeNull();
    expect(content).toHaveClass("opacity-60");
    expect(content).toHaveAttribute("data-stale", "true");

    resolveReport({ ...baseReport, period_days: 30, login_events: 99 });

    expect(
      await screen.findByText(/logins in the last 30 days\./i),
    ).toBeInTheDocument();
    await waitFor(() => {
      const fresh = document.querySelector(
        "[data-activity-content]",
      ) as HTMLElement;
      expect(fresh).not.toHaveClass("opacity-60");
      expect(fresh).not.toHaveAttribute("data-stale");
    });
  });

  it("refreshes the activity report after an access request decision", async () => {
    adminAccessRequestsMock.mockResolvedValue({
      requests: [
        {
          id: "req-1",
          email: "newbie@example.com",
          full_name: "New Bie",
          github_username: null,
          status: "pending",
          created_at: "2026-06-09T00:00:00Z",
        },
      ],
      counts: { pending: 1, approved: 0, denied: 0 },
      total: 1,
    });

    const user = userEvent.setup();
    render(<AdminPage />);

    await screen.findByText("fresh_face");
    await screen.findByRole("button", {
      name: /approve newbie@example\.com/i,
    });
    const callsBeforeDecision = getActivityReportMock.mock.calls.length;

    await user.click(
      screen.getByRole("button", { name: /approve newbie@example\.com/i }),
    );

    // Approving changes pending_access_requests, so the report (and its
    // pending-count chip) must be refetched, not left stale.
    await waitFor(() => {
      expect(getActivityReportMock.mock.calls.length).toBeGreaterThan(
        callsBeforeDecision,
      );
    });
  });

  it("describes dormancy with the backend's fixed window regardless of the selected range", async () => {
    const user = userEvent.setup();
    render(<AdminPage />);

    await screen.findByText("fresh_face");
    expect(
      screen.getByText(/no sign-in within the last 14 days\./i),
    ).toBeInTheDocument();

    getActivityReportMock.mockResolvedValue({ ...baseReport, period_days: 30 });
    await user.click(screen.getByRole("button", { name: /30 days/i }));

    await waitFor(() => {
      expect(getActivityReportMock).toHaveBeenCalledWith(30);
    });
    // Still the backend's fixed dormancy window, not the selected range.
    expect(
      screen.getByText(/no sign-in within the last 14 days\./i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/no sign-in within the last 30 days\./i),
    ).not.toBeInTheDocument();
  });

  it("falls back to generic dormancy copy when dormant_days is absent", async () => {
    const { dormant_days: _omitted, ...legacyReport } = baseReport;
    getActivityReportMock.mockResolvedValue(legacyReport);
    render(<AdminPage />);

    await screen.findByText("fresh_face");
    expect(screen.getByText(/no recent sign-in\./i)).toBeInTheDocument();
    expect(
      screen.queryByText(/no sign-in within the last \d+ days\./i),
    ).not.toBeInTheDocument();
  });

  it("shows a truncation notice when a list is capped below its total", async () => {
    getActivityReportMock.mockResolvedValue({
      ...baseReport,
      active_users_total: 250,
      dormant_users_total: 480,
    });
    render(<AdminPage />);

    await screen.findByText("busy_bee");
    expect(screen.getByText(/showing first 1 of 250/i)).toBeInTheDocument();
    expect(screen.getByText(/showing first 1 of 480/i)).toBeInTheDocument();
  });

  it("renders an error state when the report fails to load", async () => {
    getActivityReportMock.mockRejectedValue(new Error("activity boom"));
    render(<AdminPage />);

    expect(
      await screen.findByText(/activity report could not be loaded/i),
    ).toBeInTheDocument();
    expect(screen.getByText("activity boom")).toBeInTheDocument();
  });
});
