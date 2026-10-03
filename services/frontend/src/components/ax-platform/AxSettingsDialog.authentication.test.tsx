import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";
import { render, screen, userEvent } from "@/test/utils";
import { AxSettingsDialog } from "./AxSettingsDialog";

const mocks = vi.hoisted(() => ({
  profile: vi.fn(), get: vi.fn(), agents: vi.fn(), keys: vi.fn(), pats: vi.fn(), patAgents: vi.fn(),
  settings: vi.fn(), toast: vi.fn(),
}));
vi.mock("@/lib/api-clean", () => ({
  api: { getUserProfile: mocks.profile, getAgents: mocks.agents, listAgentKeys: mocks.keys },
  apiClient: { get: mocks.get },
  listPersonalAccessKeys: mocks.pats, listPatScopeAgents: mocks.patAgents,
}));
vi.mock("@/hooks/useUserSettings", () => ({
  useUserSettings: () => ({ settings: { compact_mode: false, ai_auto_summarize: false, theme: "system" },
    saving: false, updateSettings: mocks.settings }),
}));
vi.mock("@/components/ui/use-toast", () => ({ useToast: () => ({ toast: mocks.toast }) }));
// Invitation behavior has its own authorization and redemption tests.
vi.mock("./WorkspaceInvitation", () => ({ WorkspaceInvitation: () => <div>Workspace invitations</div> }));

function showSettings(extra = {}) {
  return render(<AxSettingsDialog open onOpenChange={() => undefined} currentSpaceId="space-1"
    currentSpaceName="Test Space" {...extra} />);
}

describe("Settings after credential retirement", () => {
  beforeEach(() => {
    vi.clearAllMocks(); vi.unstubAllEnvs();
    mocks.profile.mockResolvedValue({ id: "user-1", username: "tester", role: "user" });
    mocks.get.mockResolvedValue({ data: { violations: [] } });
    mocks.settings.mockResolvedValue(undefined);
  });

  it("never restores PAT or client-secret settings through the old feature flag", async () => {
    vi.stubEnv("VITE_ENABLE_AGENT_M2M_CREDENTIALS", "true");
    const user = userEvent.setup(); showSettings();
    await screen.findByDisplayValue("tester");
    expect(screen.queryByRole("tab", { name: /credentials|agents/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Monitor" }));
    expect(screen.queryByText(/Personal Access Tokens/i)).not.toBeInTheDocument();
    expect(screen.getByText("Security violations")).toBeInTheDocument();
    expect(mocks.pats).not.toHaveBeenCalled(); expect(mocks.patAgents).not.toHaveBeenCalled();
    expect(mocks.agents).not.toHaveBeenCalled(); expect(mocks.keys).not.toHaveBeenCalled();
  });

  it("keeps profile, invitation, and sign-out available", async () => {
    const logout = vi.fn(), user = userEvent.setup(); showSettings({ onLogout: logout });
    expect(await screen.findByDisplayValue("tester")).toBeInTheDocument();
    expect(screen.getByText("Workspace invitations")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(logout).toHaveBeenCalledOnce();
  });

  it.each([
    ["Toggle compact mode", { compact_mode: true }],
    ["Toggle auto summarize", { ai_auto_summarize: true }],
  ])("continues to persist %s", async (name, patch) => {
    const user = userEvent.setup(); showSettings();
    await user.click(screen.getByRole("switch", { name }));
    expect(mocks.settings).toHaveBeenCalledWith(patch);
    expect(mocks.toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Settings saved" }));
  });

  it("reports failed preference saves", async () => {
    mocks.settings.mockRejectedValueOnce(new Error("unavailable"));
    const user = userEvent.setup(); showSettings();
    await user.click(screen.getByRole("switch", { name: "Toggle compact mode" }));
    expect(mocks.toast).toHaveBeenCalledWith(expect.objectContaining({ title: "Settings failed", variant: "destructive" }));
  });

  it("keeps widget visibility controls working", async () => {
    const toggle = vi.fn(), user = userEvent.setup(); showSettings({ onToggleWidget: toggle });
    await user.click(screen.getByRole("tab", { name: "Widgets" }));
    const switches = screen.getAllByRole("switch");
    const name = switches[0].getAttribute("aria-label")!;
    await user.click(switches[0]);
    expect(toggle).toHaveBeenCalledWith(name.replace(/^Toggle /, "").replace(/ widget$/, ""));
  });

  it("preserves real security violations without representing failed requests as clean", async () => {
    mocks.get.mockRejectedValueOnce(new Error("unavailable"));
    const user = userEvent.setup(); showSettings();
    await user.click(screen.getByRole("tab", { name: "Violations" }));
    expect(await screen.findByText(/Could not load violations/i)).toBeInTheDocument();
    expect(screen.queryByText(/No violations detected/i)).not.toBeInTheDocument();
  });
});
