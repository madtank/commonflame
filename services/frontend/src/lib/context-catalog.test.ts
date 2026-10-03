import { afterEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "@/lib/api-clean";
import {
  extractContextCatalogEntryFromWidget,
  getContextCatalogEntry,
  invokeContextCatalogAction,
  listContextCatalogEntries,
} from "./context-catalog";

vi.mock("@/lib/api-clean", () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

describe("context catalog API client", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("lists catalog entries metadata-only by default", async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { items: [] } });

    await expect(
      listContextCatalogEntries("space-1", { pinned: true }),
    ).resolves.toEqual({ items: [] });

    expect(apiClient.get).toHaveBeenCalledWith(
      "/api/v1/spaces/space-1/context-catalog",
      {
        params: {
          include_content: false,
          pinned: true,
        },
      },
    );
  });

  it("gets a catalog entry with content only when requested", async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { id: "cat-review", title: "Review packet" },
    });

    await getContextCatalogEntry("space-1", "cat-review", {
      includeContent: true,
    });

    expect(apiClient.get).toHaveBeenCalledWith(
      "/api/v1/spaces/space-1/context-catalog/cat-review",
      { params: { include_content: true } },
    );
  });

  it("invokes trusted actions with backend-required base versions", async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ok: true, audit_event_id: "aud-1" },
    });

    await invokeContextCatalogAction("space-1", "cat-review", {
      context_object_id: "ctxobj-1",
      action_id: "approve",
      source_lane: "trusted_named_action",
      base_artifact_version_id: "artv-1",
      base_state_version_id: "stv-1",
      idempotency_key: "idem-1",
      payload: { comment: "Looks good" },
    });

    expect(apiClient.post).toHaveBeenCalledWith(
      "/api/v1/spaces/space-1/context-catalog/cat-review/actions",
      {
        context_object_id: "ctxobj-1",
        catalog_entry_id: "cat-review",
        action_id: "approve",
        source_lane: "trusted_named_action",
        base_artifact_version_id: "artv-1",
        base_state_version_id: "stv-1",
        idempotency_key: "idem-1",
        payload: { comment: "Looks good" },
      },
    );
  });

  it("extracts catalog entries from widget initial data", () => {
    expect(
      extractContextCatalogEntryFromWidget({
        initial_data: {
          context_catalog_entry: {
            id: "cat-html-review",
            title: "HTML Review Packet",
          },
        },
      }),
    ).toMatchObject({
      id: "cat-html-review",
      title: "HTML Review Packet",
    });
  });
});
