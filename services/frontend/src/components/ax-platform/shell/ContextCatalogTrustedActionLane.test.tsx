import { render, screen, userEvent, waitFor } from "@/test/utils";
import { describe, expect, it, vi } from "vitest";

import { ContextCatalogTrustedActionLane } from "./ContextCatalogTrustedActionLane";
import type { ContextCatalogEntry } from "@/lib/context-catalog";

function buildEntry(
  overrides: Partial<ContextCatalogEntry> = {},
): ContextCatalogEntry {
  return {
    id: "cat-review",
    title: "Q2 Review Packet",
    artifact_type: "html.review",
    status: "active",
    current_context_object_id: "ctxobj-1",
    current_artifact_version_id: "artv-1",
    current_state_version_id: "stv-1",
    current_artifact_sha256: "1234567890abcdef",
    available_actions: [
      {
        id: "approve",
        canonical_label: "Approve",
        label: "Ship it from artifact",
        source_lane: "trusted_named_action",
        confirmation: "required",
        trusted_lane_only: true,
      },
      {
        id: "place_mark",
        canonical_label: "Place mark",
        source_lane: "in_world_proposal",
        confirmation: "none",
      },
    ],
    ...overrides,
  };
}

describe("ContextCatalogTrustedActionLane", () => {
  it("renders host-owned canonical action labels and filters sandbox proposal actions", async () => {
    const onInvokeAction = vi.fn().mockResolvedValue({ ok: true });

    render(
      <ContextCatalogTrustedActionLane
        entry={buildEntry()}
        actorLabel="madtank"
        onInvokeAction={onInvokeAction}
      />,
    );

    expect(screen.getByTestId("context-catalog-trusted-lane")).toBeVisible();
    expect(screen.getByText("Q2 Review Packet")).toBeInTheDocument();
    expect(screen.getByText("Artifact artv-1")).toBeInTheDocument();
    expect(screen.getByText("Actor madtank")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.queryByText("Ship it from artifact")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Place mark" }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(screen.getByRole("dialog")).toHaveTextContent("Confirm Approve");
    await userEvent.click(
      screen.getByRole("button", { name: "Confirm Approve" }),
    );

    await waitFor(() => {
      expect(onInvokeAction).toHaveBeenCalledWith({
        context_object_id: "ctxobj-1",
        action_id: "approve",
        source_lane: "trusted_named_action",
        base_artifact_version_id: "artv-1",
        base_state_version_id: "stv-1",
        idempotency_key: expect.stringMatching(/^ctxcat-cat-review-approve-/),
        payload: {},
      });
    });
  });

  it("shows explicit conflict state with base and current versions", () => {
    render(
      <ContextCatalogTrustedActionLane
        entry={buildEntry({
          conflict: {
            error: "stale_base_version",
            catalog_entry_id: "cat-review",
            base_artifact_version_id: "artv-1",
            current_artifact_version_id: "artv-3",
            base_state_version_id: "stv-1",
            current_state_version_id: "stv-8",
            suggested_resolution: "refresh_and_reapply",
          },
        })}
        actorLabel="madtank"
        onInvokeAction={vi.fn()}
      />,
    );

    expect(screen.getByTestId("context-catalog-conflict")).toHaveTextContent(
      "Version conflict",
    );
    expect(screen.getByText("Base artv-1 / stv-1")).toBeInTheDocument();
    expect(screen.getByText("Current artv-3 / stv-8")).toBeInTheDocument();
  });

  it("promotes action conflicts into the version conflict UI", async () => {
    const onInvokeAction = vi.fn().mockResolvedValue({
      conflict: {
        error: "stale_base_version",
        base_artifact_version_id: "artv-1",
        current_artifact_version_id: "artv-4",
        base_state_version_id: "stv-1",
        current_state_version_id: "stv-9",
      },
    });

    render(
      <ContextCatalogTrustedActionLane
        entry={buildEntry({
          available_actions: [
            {
              id: "reject",
              canonical_label: "Reject",
              source_lane: "trusted_named_action",
              confirmation: "none",
            },
          ],
        })}
        onInvokeAction={onInvokeAction}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() => {
      expect(screen.getByTestId("context-catalog-conflict")).toHaveTextContent(
        "Version conflict",
      );
    });
    expect(screen.getByText("Base artv-1 / stv-1")).toBeInTheDocument();
    expect(screen.getByText("Current artv-4 / stv-9")).toBeInTheDocument();
  });

  it("uses returned version ids for follow-up trusted actions", async () => {
    const onInvokeAction = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        new_artifact_version_id: "artv-2",
        new_state_version_id: "stv-2",
        status: "approved",
      })
      .mockResolvedValueOnce({ ok: true, status: "recorded" });

    render(
      <ContextCatalogTrustedActionLane
        entry={buildEntry({
          available_actions: [
            {
              id: "ok",
              canonical_label: "OK",
              source_lane: "trusted_named_action",
              confirmation: "none",
            },
          ],
        })}
        onInvokeAction={onInvokeAction}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "OK" }));

    await waitFor(() => {
      expect(screen.getByText("Artifact artv-2")).toBeInTheDocument();
      expect(screen.getByText("State stv-2")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: "OK" }));

    await waitFor(() => {
      expect(onInvokeAction).toHaveBeenCalledTimes(2);
    });
    expect(onInvokeAction).toHaveBeenLastCalledWith(
      expect.objectContaining({
        base_artifact_version_id: "artv-2",
        base_state_version_id: "stv-2",
      }),
    );
  });

  it("uses fresher entry versions when parent props update after a local action", async () => {
    const onInvokeAction = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        new_artifact_version_id: "artv-2",
        new_state_version_id: "stv-2",
        status: "approved",
      })
      .mockResolvedValueOnce({ ok: true, status: "recorded" });
    const entry = buildEntry({
      available_actions: [
        {
          id: "ok",
          canonical_label: "OK",
          source_lane: "trusted_named_action",
          confirmation: "none",
        },
      ],
    });

    const { rerender } = render(
      <ContextCatalogTrustedActionLane
        entry={entry}
        onInvokeAction={onInvokeAction}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "OK" }));

    await waitFor(() => {
      expect(screen.getByText("Artifact artv-2")).toBeInTheDocument();
      expect(screen.getByText("State stv-2")).toBeInTheDocument();
    });

    rerender(
      <ContextCatalogTrustedActionLane
        entry={buildEntry({
          ...entry,
          current_artifact_version_id: "artv-3",
          current_state_version_id: "stv-3",
        })}
        onInvokeAction={onInvokeAction}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Artifact artv-3")).toBeInTheDocument();
      expect(screen.getByText("State stv-3")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: "OK" }));

    await waitFor(() => {
      expect(onInvokeAction).toHaveBeenCalledTimes(2);
    });
    expect(onInvokeAction).toHaveBeenLastCalledWith(
      expect.objectContaining({
        base_artifact_version_id: "artv-3",
        base_state_version_id: "stv-3",
      }),
    );
  });
});
