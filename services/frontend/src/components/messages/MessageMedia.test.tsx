/**
 * FE-03: Audio/Media Rendering — Component Tests
 *
 * QA scope (locked by @quantum_phoenix_307):
 * - <audio controls> renders inline for external MP3 URLs
 * - Only src, controls, preload attributes pass rehype-sanitize
 * - No iframe/embed passthrough
 * - XSS: javascript: or data: URI as audio src → blocked/not executed
 * - Non-audio URLs render as plain links (no regression)
 * - Phase 2 (images) is out of scope for this story
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import {
  MessageAudioPlayer,
  MessageImage,
  MessageVideoPlayer,
} from "./MessageMedia";
import { renderMarkdownContent } from "./MessageContent";
import { normalizeMediaUrl } from "@/lib/media-utils";
import { storage } from "@/lib/storage";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ============================================================================
// MessageAudioPlayer component
// ============================================================================

describe("MessageAudioPlayer", () => {
  it("renders an <audio> element with controls", () => {
    const { container } = render(
      <MessageAudioPlayer src="https://example.com/clip.mp3" />,
    );
    const audio = container.querySelector("audio");
    expect(audio).not.toBeNull();
    expect(audio?.hasAttribute("controls")).toBe(true);
  });

  it("renders with data-testid for QA", () => {
    const { container } = render(
      <MessageAudioPlayer src="https://example.com/clip.mp3" />,
    );
    expect(
      container.querySelector('[data-testid="message-audio"]'),
    ).not.toBeNull();
  });

  it("shows the filename as label when no title is given", () => {
    render(<MessageAudioPlayer src="https://example.com/my-clip.mp3" />);
    expect(screen.getByText("my-clip.mp3")).toBeInTheDocument();
  });

  it("shows the title when provided", () => {
    render(
      <MessageAudioPlayer
        src="https://example.com/clip.mp3"
        title="My Audio"
      />,
    );
    expect(screen.getByText("My Audio")).toBeInTheDocument();
  });

  it("renders <source> as child of <audio>", () => {
    const { container } = render(
      <MessageAudioPlayer src="https://example.com/clip.mp3" />,
    );
    const source = container.querySelector("audio source");
    expect(source).not.toBeNull();
    expect(source?.getAttribute("src")).toBe("https://example.com/clip.mp3");
  });

  it("sets preload=metadata", () => {
    const { container } = render(
      <MessageAudioPlayer src="https://example.com/clip.mp3" />,
    );
    expect(container.querySelector("audio")?.getAttribute("preload")).toBe(
      "metadata",
    );
  });

  it("uses authenticated blob URLs for same-origin uploaded audio", async () => {
    vi.spyOn(storage, "getUserTokenAsync").mockResolvedValue("user-token");
    const blob = new Blob(["ogg"], { type: "audio/ogg" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        blob: vi.fn().mockResolvedValue(blob),
      }),
    );
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn().mockReturnValue("blob:audio-upload"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });

    const { container } = render(
      <MessageAudioPlayer src="/api/v1/uploads/files/clip.ogg" title="Clip" />,
    );
    const initialAudio = container.querySelector("audio");

    await waitFor(() => {
      expect(container.querySelector("audio source")).toHaveAttribute(
        "src",
        "blob:audio-upload",
      );
    });
    expect(container.querySelector("audio")).not.toBe(initialAudio);
  });

  it("falls back to an attachment link when uploaded audio cannot be fetched", async () => {
    vi.spyOn(storage, "getUserTokenAsync").mockResolvedValue("user-token");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 403,
      }),
    );

    render(
      <MessageAudioPlayer
        src="/api/v1/uploads/files/expired.ogg"
        title="Expired clip"
      />,
    );

    expect(await screen.findByText("Audio attachment")).toBeInTheDocument();
    expect(screen.queryByTestId("message-audio")).not.toBeInTheDocument();
  });
});

describe("MessageImage", () => {
  it("reserves a bounded aspect-ratio frame before the image decodes", () => {
    const { container } = render(
      <MessageImage src="https://example.com/image.png" alt="Chart" />,
    );

    const frame = screen.getByTestId("message-image-frame");
    const img = container.querySelector("img");

    expect(frame.className).toContain("aspect-[4/3]");
    expect(frame.className).toContain("min-h-32");
    expect(img?.className).toContain("h-full");
    expect(img?.className).toContain("w-full");
  });
});

describe("MessageVideoPlayer", () => {
  it("reserves a 16:9 frame before video metadata loads", () => {
    const { container } = render(
      <MessageVideoPlayer src="https://example.com/clip.mp4" title="Clip" />,
    );

    const frame = screen.getByTestId("message-video-frame");
    const video = container.querySelector("video");

    expect(frame.className).toContain("aspect-video");
    expect(video?.className).toContain("h-full");
    expect(video?.className).toContain("w-full");
  });

  it("uses authenticated blob URLs for same-origin uploaded video", async () => {
    vi.spyOn(storage, "getUserTokenAsync").mockResolvedValue("user-token");
    const blob = new Blob(["mp4"], { type: "video/mp4" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        blob: vi.fn().mockResolvedValue(blob),
      }),
    );
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn().mockReturnValue("blob:video-upload"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });

    const { container } = render(
      <MessageVideoPlayer src="/api/v1/uploads/files/clip.mp4" title="Clip" />,
    );
    const initialVideo = container.querySelector("video");

    await waitFor(() => {
      expect(container.querySelector("video source")).toHaveAttribute(
        "src",
        "blob:video-upload",
      );
    });
    expect(container.querySelector("video")).not.toBe(initialVideo);
  });
});

// ============================================================================
// renderMarkdownContent — audio link rendering
// ============================================================================

describe("renderMarkdownContent — audio links", () => {
  it("renders an MP3 link as <audio> player, not a plain anchor", () => {
    const { container } = render(
      <>{renderMarkdownContent("https://example.com/sound.mp3")}</>,
    );
    // Should have audio element
    expect(container.querySelector("audio")).not.toBeNull();
    // Should NOT have a plain <a> pointing to the mp3
    const anchors = Array.from(container.querySelectorAll("a"));
    const mp3Anchor = anchors.find((a) =>
      a.getAttribute("href")?.endsWith(".mp3"),
    );
    expect(mp3Anchor).toBeUndefined();
  });

  it("renders a .wav link as <audio> player", () => {
    const { container } = render(
      <>{renderMarkdownContent("https://example.com/beep.wav")}</>,
    );
    expect(container.querySelector("audio")).not.toBeNull();
  });

  it("renders a .ogg link as <audio> player", () => {
    const { container } = render(
      <>{renderMarkdownContent("https://example.com/track.ogg")}</>,
    );
    expect(container.querySelector("audio")).not.toBeNull();
  });

  it("renders a .m4a link as <audio> player", () => {
    const { container } = render(
      <>{renderMarkdownContent("https://example.com/voice.m4a")}</>,
    );
    expect(container.querySelector("audio")).not.toBeNull();
  });

  it("renders a markdown audio link as player (not anchor)", () => {
    const { container } = render(
      <>
        {renderMarkdownContent(
          "[Listen here](https://cdn.example.com/audio.mp3)",
        )}
      </>,
    );
    expect(container.querySelector("audio")).not.toBeNull();
  });

  it("renders a non-audio link as a plain anchor (no regression)", () => {
    const { container } = render(
      <>{renderMarkdownContent("[Visit](https://example.com/page)")}</>,
    );
    expect(container.querySelector("audio")).toBeNull();
    const anchor = container.querySelector("a");
    expect(anchor).not.toBeNull();
    expect(anchor?.getAttribute("href")).toBe("https://example.com/page");
    expect(anchor?.getAttribute("target")).toBe("_blank");
    expect(anchor?.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("renders a PDF link as a plain anchor (no regression)", () => {
    const { container } = render(
      <>{renderMarkdownContent("[Report](https://example.com/report.pdf)")}</>,
    );
    expect(container.querySelector("audio")).toBeNull();
    expect(container.querySelector("a")).not.toBeNull();
  });
});

// ============================================================================
// XSS — normalizeMediaUrl blocks dangerous protocols
// ============================================================================

describe("XSS: normalizeMediaUrl blocks dangerous audio src values", () => {
  it("rejects javascript: URI", () => {
    // eslint-disable-next-line no-script-url
    expect(normalizeMediaUrl("javascript:alert(1)", { kind: "audio" })).toBe(
      "",
    );
  });

  it("rejects data: URI without matching audio kind", () => {
    expect(
      normalizeMediaUrl("data:application/javascript;base64,abc", {
        kind: "audio",
      }),
    ).toBe("");
  });

  it("rejects vbscript: URI", () => {
    expect(normalizeMediaUrl("vbscript:msgbox(1)", { kind: "audio" })).toBe("");
  });

  it("rejects protocol-relative URLs", () => {
    expect(normalizeMediaUrl("//evil.com/clip.mp3", { kind: "audio" })).toBe(
      "",
    );
  });

  it("accepts valid https audio URL", () => {
    const result = normalizeMediaUrl("https://cdn.example.com/clip.mp3", {
      kind: "audio",
    });
    expect(result).toBe("https://cdn.example.com/clip.mp3");
  });
});

// ============================================================================
// No iframe/embed passthrough
// ============================================================================

describe("renderMarkdownContent — no iframe or embed passthrough", () => {
  it("does not render <iframe> from markdown", () => {
    const { container } = render(
      <>{renderMarkdownContent('<iframe src="https://evil.com" />')}</>,
    );
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("does not render <embed> from markdown", () => {
    const { container } = render(
      <>{renderMarkdownContent('<embed src="https://evil.com/x.swf" />')}</>,
    );
    expect(container.querySelector("embed")).toBeNull();
  });
});

// ============================================================================
// renderMarkdownContent — message bubble markdown rendering
// ============================================================================

describe("renderMarkdownContent — message bubble markdown rendering", () => {
  it("wraps markdown tables in a horizontal-scroll region", () => {
    const { container } = render(
      <>
        {renderMarkdownContent(`| Column A | Column B |
| --- | --- |
| value longvalue | value second |`)}
      </>,
    );

    const region = container.querySelector(
      '[role="region"][aria-label="Markdown table"]',
    );
    expect(region).not.toBeNull();
    expect(region?.querySelector("table")).not.toBeNull();
  });

  it("renders unordered and ordered lists with spacing classes", () => {
    const { container } = render(
      <>{renderMarkdownContent("- first\n- second\n1. one\n2. two")}</>,
    );

    const unordered = container.querySelector("ul");
    const ordered = container.querySelector("ol");
    expect(unordered).not.toBeNull();
    expect(ordered).not.toBeNull();
    expect(unordered?.className).toContain("list-disc");
    expect(ordered?.className).toContain("list-decimal");
  });

  it("renders block quotes with stable styling", () => {
    const { container } = render(
      <>{renderMarkdownContent("> quoted message")}</>,
    );
    const blockquote = container.querySelector("blockquote");
    expect(blockquote).not.toBeNull();
    expect(blockquote?.className).toContain("border-l-4");
  });

  it("turns unsafe javascript: links into text, not anchors", () => {
    const { container } = render(
      <>{renderMarkdownContent("[Bad](javascript:alert(1))")}</>,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("Bad");
  });

  it("normalizes code blocks to readable wrapping behavior", () => {
    const { container } = render(
      <>
        {renderMarkdownContent(
          "```\nconst x = 'This is a very long token that should wrap without clipping';\n```",
        )}
      </>,
    );

    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre).toHaveClass("overflow-x-hidden");
    expect(pre).toHaveClass("whitespace-pre-wrap");
    expect(pre).toHaveClass("break-all");
    expect(pre?.className).not.toContain("overflow-x-auto");
    expect(container.querySelector("code")).not.toBeNull();
  });
});
