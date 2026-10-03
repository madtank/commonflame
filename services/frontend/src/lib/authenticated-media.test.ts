import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import {
  fetchAuthenticatedMediaText,
  fetchAuthenticatedMediaBlobUrl,
  requiresAuthenticatedMediaFetch,
  useAuthenticatedMediaUrl,
} from "./authenticated-media";
import { storage } from "./storage";

describe("authenticated media helpers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("requires authenticated fetch for same-origin API media URLs", () => {
    expect(requiresAuthenticatedMediaFetch("/api/v1/uploads/files/a.png")).toBe(
      true,
    );
    expect(
      requiresAuthenticatedMediaFetch(
        `${window.location.origin}/api/v1/uploads/files/a.png`,
      ),
    ).toBe(true);
    expect(requiresAuthenticatedMediaFetch("https://example.com/a.png")).toBe(
      false,
    );
    expect(requiresAuthenticatedMediaFetch("data:image/png;base64,abc")).toBe(
      false,
    );
  });

  it("fetches same-origin media with the stored bearer token", async () => {
    vi.spyOn(storage, "getUserTokenAsync").mockResolvedValue("user-token");
    const blob = new Blob(["png"], { type: "image/png" });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: vi.fn().mockResolvedValue(blob),
    });
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn().mockReturnValue("blob:authenticated"),
    });

    await expect(
      fetchAuthenticatedMediaBlobUrl("/api/v1/uploads/files/a.png"),
    ).resolves.toBe("blob:authenticated");

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/uploads/files/a.png", {
      credentials: "include",
      headers: { Authorization: "Bearer user-token" },
    });
    expect(URL.createObjectURL).toHaveBeenCalledWith(blob);
  });

  it("raises a useful error when the authenticated fetch fails", async () => {
    vi.spyOn(storage, "getUserTokenAsync").mockResolvedValue("user-token");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
      }),
    );

    await expect(
      fetchAuthenticatedMediaBlobUrl("/api/v1/uploads/files/a.png"),
    ).rejects.toThrow("Attachment fetch failed (401)");
  });

  it("fetches authenticated text attachments with the stored bearer token", async () => {
    vi.spyOn(storage, "getUserTokenAsync").mockResolvedValue("user-token");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: vi.fn().mockResolvedValue("# Preview"),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchAuthenticatedMediaText("/api/v1/uploads/files/a.md"),
    ).resolves.toBe("# Preview");

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/uploads/files/a.md", {
      credentials: "include",
      headers: { Authorization: "Bearer user-token" },
    });
  });

  it("revokes auth blob URLs that resolve after hook cleanup", async () => {
    vi.spyOn(storage, "getUserTokenAsync").mockResolvedValue("user-token");
    const blob = new Blob(["png"], { type: "image/png" });
    let resolveBlob!: (value: Blob) => void;
    const blobPromise = new Promise<Blob>((resolve) => {
      resolveBlob = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        blob: vi.fn().mockReturnValue(blobPromise),
      }),
    );
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn().mockReturnValue("blob:late"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });

    const { unmount } = renderHook(() =>
      useAuthenticatedMediaUrl("/api/v1/uploads/files/a.png"),
    );

    unmount();
    await act(async () => {
      resolveBlob(blob);
      await blobPromise;
    });

    await waitFor(() => {
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:late");
    });
  });
});
