/**
 * Tests for widget display rules.
 *
 * Spec: WIDGET-002 §5.1 — Universal Display Rules
 * - "Hide: technical transport/runtime metadata"
 * - Developer-facing notices like "Tool call completed: tasks" should be
 *   suppressed or auto-dismissed, not shown persistently to users.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import {
  shouldShowHostNotice,
  createAutoDismissNotice,
  type HostNotice,
} from "./widget-display-rules";

describe("shouldShowHostNotice", () => {
  it("suppresses 'Tool call completed' info notices", () => {
    const notice: HostNotice = {
      tone: "info",
      text: "Tool call completed: tasks",
    };
    expect(shouldShowHostNotice(notice)).toBe(false);
  });

  it("suppresses any 'Tool call completed' variant", () => {
    expect(
      shouldShowHostNotice({
        tone: "info",
        text: "Tool call completed: whoami",
      }),
    ).toBe(false);
    expect(
      shouldShowHostNotice({
        tone: "info",
        text: "Tool call completed: agents",
      }),
    ).toBe(false);
  });

  it("allows error notices through", () => {
    const notice: HostNotice = {
      tone: "error",
      text: "Tool call completed: tasks",
    };
    expect(shouldShowHostNotice(notice)).toBe(true);
  });

  it("allows non-tool-call info notices through", () => {
    const notice: HostNotice = {
      tone: "info",
      text: "Widget loaded successfully",
    };
    expect(shouldShowHostNotice(notice)).toBe(true);
  });

  it("returns false for null", () => {
    expect(shouldShowHostNotice(null)).toBe(false);
  });
});

describe("createAutoDismissNotice", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("calls clear callback after timeout for info notices", () => {
    const onClear = vi.fn();
    const notice: HostNotice = { tone: "info", text: "Widget loaded" };
    let timeoutCallback: (() => void) | null = null;
    const clearTimeoutSpy = vi.spyOn(globalThis, "clearTimeout");
    vi.spyOn(globalThis, "setTimeout").mockImplementation((callback) => {
      timeoutCallback = callback as () => void;
      return 123 as unknown as ReturnType<typeof setTimeout>;
    });

    const cleanup = createAutoDismissNotice(notice, onClear, 3000);

    expect(onClear).not.toHaveBeenCalled();
    expect(globalThis.setTimeout).toHaveBeenCalledWith(onClear, 3000);
    timeoutCallback?.();
    expect(onClear).toHaveBeenCalledOnce();

    cleanup();
    expect(clearTimeoutSpy).toHaveBeenCalledWith(123);
  });

  it("does not auto-dismiss error notices", () => {
    const onClear = vi.fn();
    const notice: HostNotice = { tone: "error", text: "Connection failed" };
    const setTimeoutSpy = vi.spyOn(globalThis, "setTimeout");

    const cleanup = createAutoDismissNotice(notice, onClear, 3000);

    expect(setTimeoutSpy).not.toHaveBeenCalled();
    expect(onClear).not.toHaveBeenCalled();

    cleanup();
  });
});
