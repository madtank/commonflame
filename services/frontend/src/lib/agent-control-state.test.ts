import { describe, expect, it } from "vitest";

import {
  formatAgentControlUntil,
  getAgentControlPresentation,
} from "./agent-control-state";

describe("agent control state", () => {
  const now = Date.parse("2026-03-21T00:00:00Z");

  it("treats missing state as active", () => {
    expect(getAgentControlPresentation(null, now)).toMatchObject({
      mode: "active",
      label: "Active",
      canReenable: false,
    });
  });

  it("renders timed disable as taking a break", () => {
    expect(
      getAgentControlPresentation(
        {
          is_disabled: true,
          disabled_by: ["agent"],
          disabled_reason: "Cooling down",
          disabled_until: "2026-03-21T00:05:00Z",
        },
        now,
      ),
    ).toMatchObject({
      mode: "disable_temporary",
      label: "Taking a break",
      detail: "Cooling down",
      until: "2026-03-21T00:05:00.000Z",
      canReenable: true,
    });
  });

  it("renders indefinite disable as disabled", () => {
    expect(
      getAgentControlPresentation(
        {
          is_disabled: true,
          disabled_by: ["agent"],
          disabled_reason: "Disabled by owner",
        },
        now,
      ),
    ).toMatchObject({
      mode: "disable_indefinite",
      label: "Disabled",
      detail: "Disabled by owner",
      canReenable: true,
    });
  });

  it("renders no-reply distinctly from disabled", () => {
    expect(
      getAgentControlPresentation(
        {
          is_disabled: false,
          disabled_by: [],
          no_reply: true,
          no_reply_reason: "Not the best fit",
        },
        now,
      ),
    ).toMatchObject({
      mode: "no_reply",
      label: "Chose not to reply",
      detail: "Not the best fit",
      canReenable: true,
    });
  });

  it("renders routing-only mode as a visible badge state", () => {
    expect(
      getAgentControlPresentation(
        {
          is_disabled: false,
          disabled_by: [],
          routing_only: true,
          routing_only_reason: "Delegate only",
        },
        now,
      ),
    ).toMatchObject({
      mode: "routing_only",
      label: "Routing only",
      detail: "Delegate only",
      canReenable: true,
    });
  });

  it("formats active-until timestamps safely", () => {
    expect(formatAgentControlUntil("2026-03-21T00:05:00Z")).toBeTruthy();
    expect(formatAgentControlUntil("not-a-date")).toBeNull();
    expect(formatAgentControlUntil(null)).toBeNull();
  });
});
