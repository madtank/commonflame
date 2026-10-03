import {
  isActiveProcessingStatus,
  isSuppressedProcessingPayload,
  isTerminalProcessingStatus,
  normalizeProcessingStatus,
} from "./processing-lifecycle";

describe("processing lifecycle", () => {
  it("normalizes status strings safely", () => {
    expect(normalizeProcessingStatus(" Completed ")).toBe("completed");
    expect(normalizeProcessingStatus(null)).toBe("");
  });

  it("treats terminal processing states as final", () => {
    expect(isTerminalProcessingStatus("completed")).toBe(true);
    expect(isTerminalProcessingStatus("done")).toBe(true);
    expect(isTerminalProcessingStatus("resolved")).toBe(true);
    expect(isTerminalProcessingStatus("failed")).toBe(true);
    expect(isTerminalProcessingStatus("timeout")).toBe(true);
  });

  it("does not treat active processing states as terminal", () => {
    expect(isTerminalProcessingStatus("queued")).toBe(false);
    expect(isTerminalProcessingStatus("thinking")).toBe(false);
    expect(isTerminalProcessingStatus("streaming")).toBe(false);
    expect(isTerminalProcessingStatus("working")).toBe(false);
  });

  it("treats CLI channel working signals as active processing", () => {
    expect(isActiveProcessingStatus("working")).toBe(true);
    expect(isActiveProcessingStatus(" processing ")).toBe(true);
    expect(isActiveProcessingStatus("completed")).toBe(false);
  });

  it("treats suppress=true as a hard stream teardown signal", () => {
    expect(isSuppressedProcessingPayload({ suppress: true })).toBe(true);
    expect(isSuppressedProcessingPayload({ suppress: false })).toBe(false);
    expect(isSuppressedProcessingPayload({ status: "completed" })).toBe(false);
    expect(isSuppressedProcessingPayload(null)).toBe(false);
  });
});
