import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OrganizationSwitcher } from "@/components/OrganizationSwitcher";
import { api } from "@/lib/api-clean";

vi.mock("@/lib/api-clean", () => ({
  api: {
    getOrganizations: vi.fn(),
    switchOrganization: vi.fn(),
  },
}));

vi.mock("@/lib/storage", () => ({
  storage: {
    getCurrentOrganization: vi.fn(() => null),
    setCurrentOrganization: vi.fn(),
    getUserTokenAsync: vi.fn(() => Promise.resolve("test-token")),
    setUserToken: vi.fn(),
  },
}));

const renderSwitcher = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <OrganizationSwitcher />
    </QueryClientProvider>,
  );
};

describe("OrganizationSwitcher", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses the shared space taxonomy so personal archive spaces are not shown as HOME", async () => {
    vi.mocked(api.getOrganizations).mockResolvedValue([
      {
        id: "home",
        name: "codex_uat",
        slug: "codex-uat",
        visibility: "private",
        description: "Personal workspace for Codex UAT",
        member_count: 1,
        is_member: true,
        is_current: false,
        is_personal: true,
        created_at: "2026-05-05T00:00:00Z",
      },
      {
        id: "archive1",
        name: "archive1",
        slug: "archive1",
        visibility: "private",
        member_count: 1,
        is_member: true,
        is_current: true,
        is_personal: true,
        created_at: "2026-05-05T00:00:00Z",
      },
    ]);

    renderSwitcher();

    expect(
      await screen.findByRole("button", {
        name: "Current workspace: PRIVATE archive1",
      }),
    ).toBeInTheDocument();
  });

  it("renders compact TEAM labels without trailing slug/member noise in the trigger", async () => {
    vi.mocked(api.getOrganizations).mockResolvedValue([
      {
        id: "gateway-uat",
        name: "Gateway UAT Workspace",
        slug: "gateway-uat",
        visibility: "shared",
        member_count: 4,
        is_member: true,
        is_current: true,
        is_personal: false,
        created_at: "2026-05-05T00:00:00Z",
      },
    ]);

    renderSwitcher();

    expect(
      await screen.findByRole("button", {
        name: "Current workspace: TEAM Gateway UAT",
      }),
    ).toBeInTheDocument();
  });

  it("keeps the selected space name shrinkable on mobile and tight again at the desktop breakpoint", async () => {
    vi.mocked(api.getOrganizations).mockResolvedValue([
      {
        id: "private-space",
        name: "madtank's Workspace",
        slug: "madtank-workspace",
        visibility: "private",
        member_count: 1,
        is_member: true,
        is_current: true,
        is_personal: true,
        created_at: "2026-05-05T00:00:00Z",
      },
    ]);

    renderSwitcher();

    const trigger = await screen.findByRole("button", {
      name: "Current workspace: HOME madtank's",
    });
    const selectedName = screen.getByText("HOME madtank's");

    expect(trigger.className).toContain("max-w-full");
    expect(trigger.className).toContain("sm:w-fit");
    expect(trigger.className).not.toContain("calc(100vw-2rem)");
    expect(selectedName.className).toContain("flex-1");
    expect(selectedName.className).toContain("sm:flex-none");
  });
});
