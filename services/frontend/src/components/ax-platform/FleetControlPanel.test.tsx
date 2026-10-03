import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";
import { render, screen, waitFor, userEvent } from "@/test/utils";
import { FleetControlPanel } from "./FleetControlPanel";
import { api } from "@/lib/api-clean";

vi.mock("@/lib/api-clean", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/api-clean")>("@/lib/api-clean");
  return {
    ...actual,
    api: {
      ...actual.api,
      getFleetControlState: vi.fn(),
      updateFleetControlState: vi.fn(),
    },
  };
});

const fleetState = {
  emergency_stop: false,
  reminder_silence: false,
  reason: "initial green readback",
  actor_id: "admin-1",
  actor_type: "user",
  updated_at: "2026-06-15T20:00:00Z",
  transition_id: "fleet-1",
  audit: [],
};

describe("FleetControlPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getFleetControlState).mockResolvedValue(fleetState);
    vi.mocked(api.updateFleetControlState).mockResolvedValue({
      ...fleetState,
      emergency_stop: true,
      reason: "Process approved canary gate",
      transition_id: "frontend-test",
    });
  });

  it("renders backend readback and safety copy", async () => {
    render(<FleetControlPanel />);

    expect(
      screen.getByRole("heading", { name: /fleet stop\/backoff controls/i }),
    ).toBeInTheDocument();

    expect(await screen.findByText(/fleet enabled/i)).toBeInTheDocument();
    expect(screen.getByText(/initial green readback/i)).toBeInTheDocument();
    expect(screen.getByText(/no destructive fleet stop/i)).toBeInTheDocument();
  });

  it("requires a reason and sends audited emergency-stop updates", async () => {
    const user = userEvent.setup();
    render(<FleetControlPanel />);

    const stopButton = screen.getByRole("button", {
      name: /enable emergency stop/i,
    });
    expect(stopButton).toBeDisabled();

    await user.type(
      screen.getByLabelText(/required reason for any change/i),
      "Process approved canary gate",
    );
    expect(stopButton).toBeEnabled();

    await user.click(stopButton);

    await waitFor(() => {
      expect(api.updateFleetControlState).toHaveBeenCalledWith(
        expect.objectContaining({
          emergency_stop: true,
          reason: "Process approved canary gate",
          transition_id: expect.stringMatching(/^frontend-/),
        }),
      );
    });
  });

  it("fails closed when fleet status cannot be read", async () => {
    vi.mocked(api.getFleetControlState).mockRejectedValueOnce(
      new Error("backend unavailable"),
    );

    const user = userEvent.setup();
    render(<FleetControlPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /do not assume the fleet is enabled/i,
    );

    await user.type(
      screen.getByLabelText(/required reason for any change/i),
      "Process approved canary gate",
    );

    expect(
      screen.getByRole("button", { name: /enable emergency stop/i }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: /silence reminders/i }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: /re-enable dispatch/i }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /resume reminders/i }),
    ).toBeDisabled();
  });
});
