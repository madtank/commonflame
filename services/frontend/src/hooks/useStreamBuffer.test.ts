import { describe, it, expect, afterEach, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useStreamBuffer, stripModelArtifacts } from "./useStreamBuffer";

/**
 * Helper: mock requestAnimationFrame as synchronous callback + advance timers.
 * The typewriter uses rAF, so we need to trigger it manually in tests.
 */
function flushRaf() {
  // Trigger any pending rAF callbacks by advancing a frame (~16ms)
  vi.advanceTimersByTime(16);
}

/** Flush enough rAF frames to render all buffered content */
function flushAllRaf(iterations = 200) {
  for (let i = 0; i < iterations; i++) {
    vi.advanceTimersByTime(16);
  }
}

describe("useStreamBuffer", () => {
  beforeEach(() => {
    vi.useFakeTimers({
      toFake: ["Date", "setTimeout", "clearTimeout"],
    });
    // Mock requestAnimationFrame to use setTimeout so fake timers control it.
    // Keep the fake-timer surface narrow so Vitest does not capture unrelated
    // hook/testing-library internals when this file runs in CI with the full suite.
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      return window.setTimeout(cb, 16) as unknown as number;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      window.clearTimeout(id);
    });
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("returns null streaming entry initially", () => {
    const { result } = renderHook(() => useStreamBuffer());
    expect(result.current.streamingEntry).toBeNull();
    expect(result.current.streamTiming.deltaCount).toBe(0);
  });

  it("sets entry on processing event", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "queued",
      });
    });

    expect(result.current.streamingEntry).toMatchObject({
      id: "msg-1",
      content: "",
      agentName: "TestAgent",
      statusLabel: "queued",
    });
  });

  it("renders delta content progressively via typewriter", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "streaming",
      });
    });

    act(() => {
      result.current.onDelta({ id: "msg-1", delta: "Hello world" });
    });

    // After one rAF tick, only a few characters should be rendered (typewriter)
    act(() => flushRaf());
    const partialLen = result.current.streamingEntry?.content.length ?? 0;
    expect(partialLen).toBeGreaterThan(0);
    expect(partialLen).toBeLessThanOrEqual("Hello world".length);

    // After many frames, all content is rendered
    act(() => flushAllRaf());
    expect(result.current.streamingEntry?.content).toBe("Hello world");
    expect(result.current.streamingEntry?.statusLabel).toBe("streaming");
  });

  it("paces follow-up streaming renders so tiny deltas batch into fewer layout updates", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "streaming",
      });
    });

    act(() => {
      result.current.onDelta({ id: "msg-1", delta: "H" });
      flushRaf();
    });
    expect(result.current.streamingEntry?.content).toBe("H");

    act(() => {
      result.current.onDelta({ id: "msg-1", delta: "i" });
      flushRaf();
    });
    expect(result.current.streamingEntry?.content).toBe("H");

    act(() => vi.advanceTimersByTime(48));
    expect(result.current.streamingEntry?.content).toBe("Hi");
  });

  it("does NOT dump all text at once — typewriter buffers it", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "streaming",
      });
    });

    // Send a large chunk all at once
    const bigText = "A".repeat(200);
    act(() => {
      result.current.onDelta({ id: "msg-1", delta: bigText });
    });

    // After just one frame, should NOT have all 200 characters
    act(() => flushRaf());
    const len = result.current.streamingEntry?.content.length ?? 0;
    expect(len).toBeGreaterThan(0);
    expect(len).toBeLessThanOrEqual(200);
  });

  it("catches up quickly when buffer is large", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "streaming",
      });
    });

    // Send 500 characters
    const bigText = "B".repeat(500);
    act(() => {
      result.current.onDelta({ id: "msg-1", delta: bigText });
    });

    // After ~20 frames (~320ms) should have caught up due to adaptive rate
    act(() => {
      for (let i = 0; i < 20; i++) vi.advanceTimersByTime(16);
    });
    const len = result.current.streamingEntry?.content.length ?? 0;
    // Adaptive rate should render significantly more than 20*3=60 chars
    expect(len).toBeGreaterThan(100);
  });

  it("does NOT clear streaming when final message arrives", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "streaming",
      });
    });

    act(() => {
      result.current.onDelta({ id: "msg-1", delta: "Hello " });
      flushAllRaf();
    });

    expect(result.current.streamingEntry?.content).toBe("Hello ");

    // Final message arrives — should NOT wipe streaming entry
    act(() => {
      result.current.onFinalMessage();
    });

    expect(result.current.streamingEntry).not.toBeNull();
    expect(result.current.streamingEntry?.content).toBe("Hello ");
  });

  it("keeps rendering buffered content after onFinalMessage fires", () => {
    const { result } = renderHook(() => useStreamBuffer());
    const finalChunk = "Full content here ".repeat(12);

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "streaming",
      });
    });

    // Send content but don't flush rAF fully
    act(() => {
      result.current.onDelta({ id: "msg-1", delta: finalChunk });
      flushRaf(); // Only partial render
    });

    const partialLen = result.current.streamingEntry?.content.length ?? 0;
    expect(partialLen).toBeGreaterThan(0);
    expect(partialLen).toBeLessThan(finalChunk.length);

    // onFinalMessage should switch into catch-up mode without dumping
    // the whole chunk instantly.
    act(() => {
      result.current.onFinalMessage();
    });

    expect(result.current.streamingEntry?.content.length ?? 0).toBeGreaterThan(
      0,
    );
    expect(result.current.streamingEntry?.content).not.toBe(finalChunk);

    act(() => {
      for (let i = 0; i < 20; i++) {
        flushRaf();
      }
    });

    expect(result.current.streamingEntry?.content).toBe(finalChunk);
    expect(result.current.isCatchingUp).toBe(false);
  });

  it("holds streaming entry after onFinalMessage then auto-resets", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "streaming",
      });
    });

    act(() => {
      result.current.onDelta({ id: "msg-1", delta: "content" });
      flushAllRaf();
    });

    act(() => {
      result.current.onFinalMessage();
    });

    // Immediately after onFinalMessage, entry is still visible
    expect(result.current.holdingFinal).toBe(true);
    expect(result.current.streamingEntry).not.toBeNull();

    // After hold + auto-reset delay, entry is cleared
    act(() => vi.advanceTimersByTime(10_000));
    expect(result.current.streamingEntry).toBeNull();
  });

  it("clears streaming only after explicit reset", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "streaming",
      });
    });

    act(() => {
      result.current.onDelta({ id: "msg-1", delta: "content" });
      flushAllRaf();
    });

    act(() => {
      result.current.onFinalMessage();
    });

    // Still visible
    expect(result.current.streamingEntry).not.toBeNull();

    // Explicit reset clears it
    act(() => {
      result.current.reset();
    });

    expect(result.current.streamingEntry).toBeNull();
  });

  it("handles ID mismatch between processing and delta events", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "queued",
      });
    });

    // Delta arrives with a different ID — should start fresh content for new ID
    act(() => {
      result.current.onDelta({ id: "msg-2", delta: "new message" });
      flushAllRaf();
    });

    expect(result.current.streamingEntry?.id).toBe("msg-2");
    expect(result.current.streamingEntry?.content).toBe("new message");
  });

  it("auto-resets after onFinalMessage if reset() was not called", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "streaming",
      });
    });

    act(() => {
      result.current.onDelta({ id: "msg-1", delta: "Hello" });
      flushAllRaf();
    });

    expect(result.current.streamingEntry).not.toBeNull();

    act(() => {
      result.current.onFinalMessage();
    });

    // Still visible immediately after onFinalMessage
    expect(result.current.streamingEntry).not.toBeNull();

    // After 2s hold + 3s auto-reset = 5s total
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(result.current.streamingEntry).toBeNull();
    expect(result.current.streamTiming.sendStartedAt).toBeNull();
  });

  it("does NOT auto-reset if explicit reset() is called before timeout", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "streaming",
      });
    });

    act(() => {
      result.current.onDelta({ id: "msg-1", delta: "Hello" });
      flushAllRaf();
    });

    act(() => {
      result.current.onFinalMessage();
    });

    // Explicit reset before timeout
    act(() => {
      result.current.reset();
    });

    expect(result.current.streamingEntry).toBeNull();

    // Advancing past timeout should not cause errors or double-reset
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(result.current.streamingEntry).toBeNull();
  });

  it("cleans up timers on unmount", () => {
    const { result, unmount } = renderHook(() => useStreamBuffer());
    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "A",
        statusLabel: "streaming",
      });
    });
    act(() => {
      result.current.onDelta({ id: "msg-1", delta: "x" });
      flushAllRaf();
    });
    act(() => {
      result.current.onFinalMessage();
    });
    unmount();
    // Should not throw or warn
    act(() => {
      vi.advanceTimersByTime(10000);
    });
  });

  it("accumulates timing stats correctly", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-1",
        agentName: "TestAgent",
        statusLabel: "streaming",
      });
    });

    act(() => {
      result.current.onDelta({ id: "msg-1", delta: "a" });
      result.current.onDelta({ id: "msg-1", delta: "b" });
      result.current.onDelta({ id: "msg-1", delta: "c" });
      flushAllRaf();
    });

    // Timing state is flushed when onFinalMessage fires
    act(() => {
      result.current.onFinalMessage();
    });

    const timing = result.current.streamTiming;
    // 1 processing + 3 deltas + 1 finalMessage = 5 events
    expect(timing.deltaCount).toBe(3);
    expect(timing.eventCount).toBe(5);
    expect(timing.firstDeltaAt).toBeTypeOf("number");
    expect(timing.firstAgentProcessingAt).toBeTypeOf("number");
    expect(timing.finalMessageAt).toBeTypeOf("number");
  });

  it("carries Gateway phase fields (activity, progress, reason) onto the streaming entry", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-phase",
        agentName: "cli-managed-bot",
        statusLabel: "tool_call",
        toolName: "shell",
        activity: "Running git status",
        progress: { current: 2, total: 5, unit: "files" },
      });
    });

    expect(result.current.streamingEntry).toMatchObject({
      id: "msg-phase",
      statusLabel: "tool_call",
      toolName: "shell",
      activity: "Running git status",
      progress: { current: 2, total: 5, unit: "files" },
    });
  });

  it("preserves prior phase fields across successive onProcessing calls for the same id", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-phase-2",
        agentName: "cli-managed-bot",
        statusLabel: "tool_call",
        toolName: "shell",
        activity: "Running git status",
      });
    });

    // Follow-up tool_complete lacks tool_name but should keep the prior value
    // so the UI can render "X is working" without losing tool context mid-flight.
    act(() => {
      result.current.onProcessing({
        id: "msg-phase-2",
        agentName: "cli-managed-bot",
        statusLabel: "tool_complete",
      });
    });

    expect(result.current.streamingEntry).toMatchObject({
      id: "msg-phase-2",
      statusLabel: "tool_complete",
      toolName: "shell",
      activity: "Running git status",
    });
  });

  it("resets phase fields when a new stream id takes over", () => {
    const { result } = renderHook(() => useStreamBuffer());

    act(() => {
      result.current.onProcessing({
        id: "msg-first",
        agentName: "cli-managed-bot",
        statusLabel: "tool_call",
        toolName: "shell",
        activity: "Running git status",
      });
    });

    act(() => {
      result.current.onProcessing({
        id: "msg-second",
        agentName: "cli-managed-bot",
        statusLabel: "queued",
      });
    });

    expect(result.current.streamingEntry).toMatchObject({
      id: "msg-second",
      statusLabel: "queued",
      toolName: null,
      activity: null,
    });
  });
});

describe("stripModelArtifacts", () => {
  it("returns plain text unchanged", () => {
    expect(stripModelArtifacts("Hello world")).toBe("Hello world");
  });

  it("strips complete <thinking>...</thinking> blocks", () => {
    expect(
      stripModelArtifacts("<thinking>internal reasoning</thinking>Hello world"),
    ).toBe("Hello world");
  });

  it("strips multiple <thinking> blocks", () => {
    expect(
      stripModelArtifacts(
        "<thinking>first</thinking>Hello <thinking>second</thinking>world",
      ),
    ).toBe("Hello world");
  });

  it("strips incomplete <thinking> block at the end (still streaming)", () => {
    expect(stripModelArtifacts("<thinking>The user wants to know about")).toBe(
      "",
    );
  });

  it("strips <thinking> block followed by visible content then another incomplete block", () => {
    expect(
      stripModelArtifacts(
        "<thinking>reasoning</thinking>Hello world<thinking>more reasoning",
      ),
    ).toBe("Hello world");
  });

  it("strips partial <thinking tag being typed character by character", () => {
    expect(stripModelArtifacts("<t")).toBe("");
    expect(stripModelArtifacts("<th")).toBe("");
    expect(stripModelArtifacts("<thi")).toBe("");
    expect(stripModelArtifacts("<thin")).toBe("");
    expect(stripModelArtifacts("<think")).toBe("");
    expect(stripModelArtifacts("<thinki")).toBe("");
    expect(stripModelArtifacts("<thinkin")).toBe("");
    expect(stripModelArtifacts("<thinking")).toBe("");
  });

  it("does not strip partial tag in the middle of text", () => {
    // Only strips partial tags at the END of the string
    expect(stripModelArtifacts("Hello <t")).toBe("Hello ");
    expect(stripModelArtifacts("Some text <thinking")).toBe("Some text ");
  });

  it("strips trailing {ax_intel...} JSON (complete)", () => {
    expect(
      stripModelArtifacts(
        'Hello world {"ax_intel": {"routing_decision": "handle"}}',
      ),
    ).toBe("Hello world");
  });

  it("strips trailing {ax_intel...} JSON (partial/incomplete)", () => {
    expect(stripModelArtifacts('Hello world {"ax_intel": {"routing')).toBe(
      "Hello world",
    );
  });

  it("strips ax_intel with single quotes", () => {
    expect(
      stripModelArtifacts("Hello world {'ax_intel': {'routing': 'handle'}}"),
    ).toBe("Hello world");
  });

  it("handles combined thinking blocks and ax_intel JSON", () => {
    const raw =
      '<thinking>reasoning</thinking>Hello world<thinking>more</thinking> {"ax_intel": {"visible": true}}';
    expect(stripModelArtifacts(raw)).toBe("Hello world");
  });

  it("preserves trailing whitespace on visible content (no trim)", () => {
    expect(stripModelArtifacts("Hello ")).toBe("Hello ");
  });

  it("returns empty string when content is only thinking tags", () => {
    expect(
      stripModelArtifacts(
        "<thinking>The user asked a question. I will answer.</thinking>",
      ),
    ).toBe("");
  });

  it("returns empty string for empty input", () => {
    expect(stripModelArtifacts("")).toBe("");
  });
});
