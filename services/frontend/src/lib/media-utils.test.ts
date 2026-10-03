import { describe, it, expect } from "vitest";
import {
  normalizeImageSrc,
  isAllowedMediaUrl,
  normalizeMediaUrl,
  stripTrailingPunctuation,
  getUrlExtension,
  getFileLabel,
  getMediaKindFromMime,
  getMediaKindFromUrl,
  getMediaKind,
  isMarkdownAttachment,
  isTextPreviewableAttachment,
  parseYouTubeStart,
  sanitizeYouTubeListId,
  extractYouTubeId,
  parseYouTubeUrl,
  getYouTubeThumbnailUrl,
  extractAttachments,
  extractMediaFromContent,
  limitMediaItems,
} from "./media-utils";

// ============================================================================
// URL Utilities
// ============================================================================

describe("normalizeImageSrc", () => {
  it("converts GitHub blob URLs to raw", () => {
    expect(
      normalizeImageSrc("https://github.com/user/repo/blob/main/img.png"),
    ).toBe("https://github.com/user/repo/raw/main/img.png");
  });

  it("leaves non-GitHub URLs unchanged", () => {
    expect(normalizeImageSrc("https://example.com/img.png")).toBe(
      "https://example.com/img.png",
    );
  });

  it("leaves already-raw GitHub URLs unchanged", () => {
    expect(
      normalizeImageSrc("https://github.com/user/repo/raw/main/img.png"),
    ).toBe("https://github.com/user/repo/raw/main/img.png");
  });
});

describe("isAllowedMediaUrl", () => {
  it("allows http URLs", () => {
    expect(isAllowedMediaUrl("https://example.com/img.png")).toBe(true);
  });

  it("allows relative URLs", () => {
    expect(isAllowedMediaUrl("/images/photo.jpg")).toBe(true);
    expect(isAllowedMediaUrl("./photo.jpg")).toBe(true);
  });

  it("allows blob URLs", () => {
    expect(isAllowedMediaUrl("blob:https://example.com/abc")).toBe(true);
  });

  it("rejects protocol-relative URLs", () => {
    expect(isAllowedMediaUrl("//example.com/img.png")).toBe(false);
  });

  it("rejects empty strings", () => {
    expect(isAllowedMediaUrl("")).toBe(false);
  });

  it("allows valid data:image URLs with image kind", () => {
    expect(isAllowedMediaUrl("data:image/png;base64,abc", "image")).toBe(true);
  });

  it("rejects data:image URLs without matching kind", () => {
    expect(isAllowedMediaUrl("data:image/png;base64,abc", "audio")).toBe(false);
  });

  it("rejects data URLs without kind", () => {
    expect(isAllowedMediaUrl("data:image/png;base64,abc")).toBe(false);
  });
});

describe("normalizeMediaUrl", () => {
  it("prepends https to www. URLs", () => {
    expect(normalizeMediaUrl("www.example.com/img.png")).toBe(
      "https://www.example.com/img.png",
    );
  });

  it("returns empty for disallowed URLs", () => {
    expect(normalizeMediaUrl("//bad.com/img.png")).toBe("");
  });

  it("returns empty for empty input", () => {
    expect(normalizeMediaUrl("")).toBe("");
  });
});

describe("stripTrailingPunctuation", () => {
  it("strips trailing period", () => {
    expect(stripTrailingPunctuation("https://example.com.")).toBe(
      "https://example.com",
    );
  });

  it("strips trailing paren and period", () => {
    expect(stripTrailingPunctuation("https://example.com).")).toBe(
      "https://example.com",
    );
  });

  it("leaves clean URLs alone", () => {
    expect(stripTrailingPunctuation("https://example.com/path")).toBe(
      "https://example.com/path",
    );
  });
});

describe("getUrlExtension", () => {
  it("extracts extension", () => {
    expect(getUrlExtension("https://example.com/photo.jpg")).toBe("jpg");
  });

  it("ignores query strings", () => {
    expect(getUrlExtension("https://example.com/photo.png?w=100")).toBe("png");
  });

  it("returns last dot segment for paths without file extension", () => {
    // NOTE: Known limitation — getUrlExtension doesn't isolate the filename,
    // so URLs without a file extension but with dots in the domain return
    // unexpected results. This test documents the current behavior.
    expect(getUrlExtension("https://example.com/path")).toBe("com/path");
  });
});

describe("getFileLabel", () => {
  it("extracts filename", () => {
    expect(getFileLabel("https://example.com/photos/sunset.jpg")).toBe(
      "sunset.jpg",
    );
  });

  it("decodes URI components", () => {
    expect(getFileLabel("https://example.com/my%20photo.jpg")).toBe(
      "my photo.jpg",
    );
  });

  it("returns empty for bare domain", () => {
    expect(getFileLabel("https://example.com/")).toBe("");
  });
});

describe("attachment preview detection", () => {
  it("treats plain text uploads as previewable", () => {
    expect(
      isTextPreviewableAttachment({
        mime: "text/plain",
        filename: "notes.txt",
        url: "/api/v1/uploads/files/a.txt",
      }),
    ).toBe(true);
  });

  it("treats code-like filenames as previewable even when MIME is generic", () => {
    expect(
      isTextPreviewableAttachment({
        mime: "application/octet-stream",
        filename: "agent-plan.tsx",
        url: "/api/v1/uploads/files/random.bin",
      }),
    ).toBe(true);
  });

  it("does not preview binary office documents as text", () => {
    expect(
      isTextPreviewableAttachment({
        mime: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename: "report.docx",
        url: "/api/v1/uploads/files/report.docx",
      }),
    ).toBe(false);
  });

  it("detects markdown by MIME or filename", () => {
    expect(isMarkdownAttachment("text/markdown", "readme.txt")).toBe(true);
    expect(isMarkdownAttachment("text/plain", "readme.md")).toBe(true);
  });
});

// ============================================================================
// Media Kind Detection
// ============================================================================

describe("getMediaKindFromMime", () => {
  it("detects image", () => {
    expect(getMediaKindFromMime("image/png")).toBe("image");
  });

  it("detects audio", () => {
    expect(getMediaKindFromMime("audio/mpeg")).toBe("audio");
  });

  it("detects video", () => {
    expect(getMediaKindFromMime("video/mp4")).toBe("video");
  });

  it("returns null for unknown", () => {
    expect(getMediaKindFromMime("application/json")).toBeNull();
  });

  it("returns null for undefined", () => {
    expect(getMediaKindFromMime(undefined)).toBeNull();
  });
});

describe("getMediaKindFromUrl", () => {
  it("detects image from extension", () => {
    expect(getMediaKindFromUrl("https://example.com/img.png")).toBe("image");
  });

  it("detects audio from extension", () => {
    expect(getMediaKindFromUrl("https://example.com/song.mp3")).toBe("audio");
  });

  it("detects video from extension", () => {
    expect(getMediaKindFromUrl("https://example.com/clip.mp4")).toBe("video");
  });

  it("detects from data URI", () => {
    expect(getMediaKindFromUrl("data:image/png;base64,abc")).toBe("image");
  });

  it("returns null for unknown", () => {
    expect(getMediaKindFromUrl("https://example.com/doc.pdf")).toBeNull();
  });
});

describe("getMediaKind", () => {
  it("prefers MIME over URL", () => {
    expect(getMediaKind("https://example.com/file.mp3", "video/mp4")).toBe(
      "video",
    );
  });

  it("falls back to URL when no MIME", () => {
    expect(getMediaKind("https://example.com/file.mp3")).toBe("audio");
  });
});

// ============================================================================
// YouTube Utilities
// ============================================================================

describe("parseYouTubeStart", () => {
  it("parses plain seconds", () => {
    expect(parseYouTubeStart("123")).toBe(123);
  });

  it("parses h/m/s format", () => {
    expect(parseYouTubeStart("1h2m3s")).toBe(3723);
  });

  it("parses minutes and seconds", () => {
    expect(parseYouTubeStart("5m30s")).toBe(330);
  });

  it("returns undefined for null", () => {
    expect(parseYouTubeStart(null)).toBeUndefined();
  });

  it("returns undefined for empty", () => {
    expect(parseYouTubeStart("")).toBeUndefined();
  });
});

describe("sanitizeYouTubeListId", () => {
  it("accepts valid list IDs", () => {
    expect(sanitizeYouTubeListId("PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf")).toBe(
      "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
    );
  });

  it("rejects IDs with special chars", () => {
    expect(sanitizeYouTubeListId("list<script>")).toBeUndefined();
  });

  it("returns undefined for null", () => {
    expect(sanitizeYouTubeListId(null)).toBeUndefined();
  });
});

describe("extractYouTubeId", () => {
  it("extracts from /watch?v=", () => {
    const url = new URL("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
    expect(extractYouTubeId(url)).toBe("dQw4w9WgXcQ");
  });

  it("extracts from youtu.be short URL", () => {
    const url = new URL("https://youtu.be/dQw4w9WgXcQ");
    expect(extractYouTubeId(url)).toBe("dQw4w9WgXcQ");
  });

  it("extracts from /shorts/", () => {
    const url = new URL("https://youtube.com/shorts/abc123");
    expect(extractYouTubeId(url)).toBe("abc123");
  });

  it("extracts from /embed/", () => {
    const url = new URL("https://youtube.com/embed/abc123");
    expect(extractYouTubeId(url)).toBe("abc123");
  });

  it("returns null for non-YouTube", () => {
    const url = new URL("https://example.com/watch?v=abc");
    expect(extractYouTubeId(url)).toBeNull();
  });
});

describe("parseYouTubeUrl", () => {
  it("parses full YouTube URL with timestamp", () => {
    const result = parseYouTubeUrl(
      "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30",
    );
    expect(result).toEqual({
      id: "dQw4w9WgXcQ",
      url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30",
      start: 30,
      listId: undefined,
    });
  });

  it("returns null for non-YouTube URL", () => {
    expect(parseYouTubeUrl("https://example.com/video")).toBeNull();
  });

  it("returns null for empty string", () => {
    expect(parseYouTubeUrl("")).toBeNull();
  });
});

describe("getYouTubeThumbnailUrl", () => {
  it("returns correct thumbnail URL", () => {
    expect(getYouTubeThumbnailUrl("abc123")).toBe(
      "https://i.ytimg.com/vi/abc123/hqdefault.jpg",
    );
  });
});

// ============================================================================
// Attachment Extraction
// ============================================================================

describe("extractAttachments", () => {
  it("returns empty for undefined", () => {
    expect(extractAttachments(undefined)).toEqual([]);
  });

  it("extracts from attachments array", () => {
    const post = {
      attachments: [{ url: "https://example.com/file.png", name: "test" }],
    };
    expect(extractAttachments(post)).toEqual([
      {
        url: "https://example.com/file.png",
        title: "test",
        mime: undefined,
        poster: undefined,
      },
    ]);
  });

  it("extracts from string array", () => {
    const post = {
      attachments: ["https://example.com/file.png"],
    };
    expect(extractAttachments(post)).toEqual([
      { url: "https://example.com/file.png" },
    ]);
  });

  it("extracts from metadata.file_url", () => {
    const post = {
      metadata: { file_url: "https://example.com/doc.pdf" },
    };
    expect(extractAttachments(post)).toEqual([
      { url: "https://example.com/doc.pdf" },
    ]);
  });

  it("extracts accepted attachment metadata from message records", () => {
    const post = {
      metadata: {
        accepted_attachments: [
          {
            url: "/api/v1/uploads/files/screenshot.png",
            filename: "screenshot.png",
            content_type: "image/png",
            context_key: "upload:123:screenshot.png:abc",
          },
        ],
      },
    };

    expect(extractAttachments(post)).toEqual([
      {
        url: "/api/v1/uploads/files/screenshot.png",
        title: "screenshot.png",
        mime: "image/png",
        poster: undefined,
        contextKey: "upload:123:screenshot.png:abc",
      },
    ]);
  });
});

// ============================================================================
// Content Media Extraction
// ============================================================================

describe("extractMediaFromContent", () => {
  it("extracts markdown images", () => {
    const result = extractMediaFromContent(
      "![alt text](https://example.com/img.png)",
    );
    expect(result.images).toHaveLength(1);
    expect(result.images[0].src).toBe("https://example.com/img.png");
    expect(result.images[0].alt).toBe("alt text");
  });

  it("extracts YouTube URLs from plain text", () => {
    const result = extractMediaFromContent(
      "Check this out: https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    );
    expect(result.youtube).toHaveLength(1);
    expect(result.youtube[0].id).toBe("dQw4w9WgXcQ");
  });

  it("deduplicates media", () => {
    const content =
      "![](https://example.com/img.png) and https://example.com/img.png";
    const result = extractMediaFromContent(content);
    expect(result.images).toHaveLength(1);
  });

  it("returns clean text without media markup", () => {
    const result = extractMediaFromContent(
      "Hello ![](https://example.com/img.png) world",
    );
    expect(result.textContent).toBe("Hello world");
  });

  it("extracts document attachments from post metadata", () => {
    const result = extractMediaFromContent("", {
      attachments: [
        {
          url: "https://example.com/report.pdf",
          name: "Quarterly Report",
          mime_type: "application/pdf",
        },
      ],
    });
    expect(result.files).toEqual([
      {
        url: "https://example.com/report.pdf",
        title: "Quarterly Report",
        mime: "application/pdf",
      },
    ]);
  });

  it("renders image attachments from accepted attachment metadata", () => {
    const result = extractMediaFromContent("see attached", {
      metadata: {
        accepted_attachments: [
          {
            url: "/api/v1/uploads/files/widget-feedback.png",
            filename: "widget-feedback.png",
            content_type: "image/png",
            context_key: "upload:123:widget-feedback.png:abc",
          },
        ],
      },
    });

    expect(result.textContent).toBe("see attached");
    expect(result.images).toEqual([
      {
        src: "/api/v1/uploads/files/widget-feedback.png",
        alt: "widget-feedback.png",
        title: "widget-feedback.png",
        contextKey: "upload:123:widget-feedback.png:abc",
      },
    ]);
  });

  it("handles empty content", () => {
    const result = extractMediaFromContent("");
    expect(result.images).toHaveLength(0);
    expect(result.files).toHaveLength(0);
    expect(result.textContent).toBe("");
  });
});

// ============================================================================
// Limit Media Items
// ============================================================================

describe("limitMediaItems", () => {
  it("limits total items", () => {
    const images = [
      { src: "a.jpg", alt: "a" },
      { src: "b.jpg", alt: "b" },
    ];
    const audio = [{ src: "c.mp3" }];
    const result = limitMediaItems(images, audio, [], [], [], 2);
    expect(result.visibleImages).toHaveLength(2);
    expect(result.visibleAudio).toHaveLength(0);
    expect(result.visibleCount).toBe(2);
  });

  it("prioritizes images first", () => {
    const images = [{ src: "a.jpg", alt: "a" }];
    const audio = [{ src: "b.mp3" }];
    const video = [{ src: "c.mp4" }];
    const result = limitMediaItems(images, audio, video, [], [], 2);
    expect(result.visibleImages).toHaveLength(1);
    expect(result.visibleAudio).toHaveLength(1);
    expect(result.visibleVideo).toHaveLength(0);
  });

  it("handles zero limit", () => {
    const images = [{ src: "a.jpg", alt: "a" }];
    const result = limitMediaItems(images, [], [], [], [], 0);
    expect(result.visibleCount).toBe(0);
  });

  it("includes file attachments after other media when space remains", () => {
    const images = [{ src: "a.jpg", alt: "a" }];
    const files = [{ url: "report.pdf", title: "report" }];
    const result = limitMediaItems(images, [], [], [], files, 2);
    expect(result.visibleImages).toHaveLength(1);
    expect(result.visibleFiles).toHaveLength(1);
    expect(result.visibleCount).toBe(2);
  });
});
