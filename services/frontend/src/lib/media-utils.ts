/**
 * Media Utilities
 * Extracted from MessageList.tsx for maintainability
 * Handles media detection, URL normalization, YouTube parsing, and content extraction
 */

// ============================================================================
// Types
// ============================================================================

export type MediaKind = "image" | "audio" | "video";

export interface ImageMedia {
  src: string;
  alt: string;
  title?: string;
  contextKey?: string;
}

export interface AudioMedia {
  src: string;
  title?: string;
}

export interface VideoMedia {
  src: string;
  title?: string;
  poster?: string;
}

export interface YouTubeMedia {
  id: string;
  url: string;
  title?: string;
  start?: number;
  listId?: string;
}

export interface FileMedia {
  url: string;
  title?: string;
  mime?: string;
  contextKey?: string;
}

export interface ExtractedMedia {
  textContent: string;
  images: ImageMedia[];
  audio: AudioMedia[];
  video: VideoMedia[];
  youtube: YouTubeMedia[];
  files: FileMedia[];
}

export interface Attachment {
  url: string;
  title?: string;
  mime?: string;
  poster?: string;
  contextKey?: string;
}

// ============================================================================
// Constants
// ============================================================================

export const IMAGE_EXTENSIONS = new Set(["jpg", "jpeg", "png", "gif", "webp"]);
export const AUDIO_EXTENSIONS = new Set(["mp3", "wav", "ogg", "m4a"]);
export const VIDEO_EXTENSIONS = new Set(["mp4", "webm", "mov"]);
export const DOCUMENT_EXTENSIONS = new Set([
  "pdf",
  "txt",
  "md",
  "markdown",
  "csv",
  "json",
  "doc",
  "docx",
  "xls",
  "xlsx",
  "ppt",
  "pptx",
]);

export const TEXT_PREVIEW_EXTENSIONS = new Set([
  "txt",
  "md",
  "markdown",
  "csv",
  "json",
  "jsonl",
  "ndjson",
  "xml",
  "yaml",
  "yml",
  "toml",
  "ini",
  "log",
  "py",
  "js",
  "jsx",
  "ts",
  "tsx",
  "css",
  "scss",
  "sql",
  "sh",
  "bash",
  "zsh",
  "go",
  "rs",
  "java",
  "rb",
  "php",
  "swift",
  "kt",
  "kts",
  "c",
  "h",
  "cpp",
  "hpp",
  "diff",
  "patch",
]);

export const MARKDOWN_EXTENSIONS = new Set(["md", "markdown"]);

export const YOUTUBE_HOSTNAMES = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtu.be",
  "www.youtu.be",
  "youtube-nocookie.com",
  "www.youtube-nocookie.com",
]);

const SAFE_DATA_IMAGE_REGEX = /^data:image\/(png|jpe?g|gif|webp);/i;
const SAFE_DATA_AUDIO_REGEX = /^data:audio\//i;
const SAFE_DATA_VIDEO_REGEX = /^data:video\//i;

// ============================================================================
// URL Utilities
// ============================================================================

/**
 * Normalize GitHub blob URLs to raw URLs for direct image access
 */
export const normalizeImageSrc = (src: string): string => {
  let finalSrc = src;
  if (
    finalSrc &&
    finalSrc.includes("github.com") &&
    finalSrc.includes("/blob/")
  ) {
    finalSrc = finalSrc.replace("/blob/", "/raw/");
  }
  return finalSrc;
};

/**
 * Check if a URL is allowed for media embedding
 */
export const isAllowedMediaUrl = (url: string, kind?: MediaKind): boolean => {
  const trimmed = (url || "").trim();
  if (!trimmed) return false;

  if (trimmed.startsWith("data:")) {
    if (kind === "image") return SAFE_DATA_IMAGE_REGEX.test(trimmed);
    if (kind === "audio") return SAFE_DATA_AUDIO_REGEX.test(trimmed);
    if (kind === "video") return SAFE_DATA_VIDEO_REGEX.test(trimmed);
    return false;
  }

  if (trimmed.startsWith("blob:")) return true;
  if (trimmed.startsWith("//")) return false;

  if (
    trimmed.startsWith("/") ||
    trimmed.startsWith("./") ||
    trimmed.startsWith("../")
  ) {
    return true;
  }

  try {
    const parsed = new URL(trimmed);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
};

/**
 * Normalize and validate a media URL
 */
export const normalizeMediaUrl = (
  url: string,
  options: { kind?: MediaKind } = {},
): string => {
  const trimmed = (url || "").trim();
  if (!trimmed) return "";

  const normalized = trimmed.startsWith("www.")
    ? `https://${trimmed}`
    : trimmed;

  if (!isAllowedMediaUrl(normalized, options.kind)) return "";
  return normalized;
};

/**
 * Strip trailing punctuation from URLs
 */
export const stripTrailingPunctuation = (value: string): string =>
  value.replace(/[)\].,!?]+$/g, "");

/**
 * Get file extension from URL
 */
export const getUrlExtension = (value: string): string => {
  const cleaned = value.split("?")[0].split("#")[0];
  const lastDot = cleaned.lastIndexOf(".");
  if (lastDot === -1) return "";
  return cleaned.substring(lastDot + 1).toLowerCase();
};

/**
 * Get filename from URL
 */
export const getFileLabel = (url: string): string => {
  const cleaned = url.split("?")[0].split("#")[0];
  const filename = cleaned.split("/").pop() || "";
  if (!filename) return "";
  try {
    return decodeURIComponent(filename);
  } catch {
    return filename;
  }
};

// ============================================================================
// Media Kind Detection
// ============================================================================

/**
 * Detect media kind from MIME type
 */
export const getMediaKindFromMime = (mime?: string): MediaKind | null => {
  if (!mime) return null;
  const normalized = mime.toLowerCase();
  if (normalized.startsWith("image/")) return "image";
  if (normalized.startsWith("audio/")) return "audio";
  if (normalized.startsWith("video/")) return "video";
  return null;
};

/**
 * Detect media kind from URL
 */
export const getMediaKindFromUrl = (url: string): MediaKind | null => {
  const lower = url.toLowerCase();
  if (lower.startsWith("data:image/")) return "image";
  if (lower.startsWith("data:audio/")) return "audio";
  if (lower.startsWith("data:video/")) return "video";

  const ext = getUrlExtension(lower);
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  if (AUDIO_EXTENSIONS.has(ext)) return "audio";
  if (VIDEO_EXTENSIONS.has(ext)) return "video";
  return null;
};

/**
 * Detect media kind from URL or MIME type
 */
export const getMediaKind = (url: string, mime?: string): MediaKind | null =>
  getMediaKindFromMime(mime) ?? getMediaKindFromUrl(url);

export const isDocumentMime = (mime?: string): boolean => {
  if (!mime) return false;
  const normalized = mime.toLowerCase();
  return (
    normalized === "application/pdf" ||
    normalized === "text/plain" ||
    normalized === "text/markdown" ||
    normalized === "text/csv" ||
    normalized === "application/json" ||
    normalized.startsWith("application/msword") ||
    normalized.startsWith("application/vnd.openxmlformats-officedocument") ||
    normalized.startsWith("application/vnd.ms-")
  );
};

export const isDocumentUrl = (url: string): boolean => {
  const ext = getUrlExtension(url.toLowerCase());
  return DOCUMENT_EXTENSIONS.has(ext);
};

export const isMarkdownAttachment = (
  mime?: string | null,
  filenameOrUrl?: string | null,
): boolean => {
  const normalizedMime = (mime || "").toLowerCase();
  if (
    normalizedMime === "text/markdown" ||
    normalizedMime === "text/x-markdown"
  ) {
    return true;
  }

  const ext = getUrlExtension((filenameOrUrl || "").toLowerCase());
  return MARKDOWN_EXTENSIONS.has(ext);
};

export const isTextPreviewableAttachment = ({
  mime,
  filename,
  url,
}: {
  mime?: string | null;
  filename?: string | null;
  url?: string | null;
}): boolean => {
  const normalizedMime = (mime || "").toLowerCase();
  if (normalizedMime.startsWith("text/")) return true;
  if (
    normalizedMime === "application/json" ||
    normalizedMime === "application/xml" ||
    normalizedMime === "application/yaml" ||
    normalizedMime === "application/x-yaml" ||
    normalizedMime === "application/toml" ||
    normalizedMime === "application/x-ndjson" ||
    normalizedMime === "application/javascript" ||
    normalizedMime === "application/typescript"
  ) {
    return true;
  }

  const filenameExt = getUrlExtension((filename || "").toLowerCase());
  if (filenameExt && TEXT_PREVIEW_EXTENSIONS.has(filenameExt)) return true;

  const urlExt = getUrlExtension((url || "").toLowerCase());
  return Boolean(urlExt && TEXT_PREVIEW_EXTENSIONS.has(urlExt));
};

export const getAttachmentKind = (
  url: string,
  mime?: string,
): "image" | "audio" | "video" | "file" | null => {
  const mediaKind = getMediaKind(url, mime);
  if (mediaKind) return mediaKind;
  if (isDocumentMime(mime) || isDocumentUrl(url)) return "file";
  return null;
};

// ============================================================================
// YouTube Utilities
// ============================================================================

/**
 * Parse YouTube timestamp (e.g., "1h2m3s" or "123")
 */
export const parseYouTubeStart = (
  value?: string | null,
): number | undefined => {
  if (!value) return undefined;

  if (/^\d+$/.test(value)) {
    return Number(value);
  }

  const match = value.match(/(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?/i);
  if (!match) return undefined;

  const hours = Number(match[1] || 0);
  const minutes = Number(match[2] || 0);
  const seconds = Number(match[3] || 0);
  const total = hours * 3600 + minutes * 60 + seconds;

  return total > 0 ? total : undefined;
};

/**
 * Sanitize YouTube playlist ID
 */
export const sanitizeYouTubeListId = (
  value?: string | null,
): string | undefined => {
  if (!value) return undefined;
  const trimmed = value.trim();
  if (!/^[a-zA-Z0-9_-]+$/.test(trimmed)) return undefined;
  return trimmed;
};

/**
 * Extract YouTube video ID from URL
 */
export const extractYouTubeId = (url: URL): string | null => {
  const host = url.hostname.toLowerCase();
  if (!YOUTUBE_HOSTNAMES.has(host)) return null;

  if (host.includes("youtu.be")) {
    const pathParts = url.pathname.split("/").filter(Boolean);
    return pathParts[0] || null;
  }

  const pathParts = url.pathname.split("/").filter(Boolean);
  if (url.pathname === "/watch" || url.pathname === "/watch/") {
    return url.searchParams.get("v");
  }

  if (
    pathParts[0] === "shorts" ||
    pathParts[0] === "embed" ||
    pathParts[0] === "live" ||
    pathParts[0] === "v"
  ) {
    return pathParts[1];
  }

  return null;
};

/**
 * Parse YouTube URL into structured data
 */
export const parseYouTubeUrl = (value: string): YouTubeMedia | null => {
  const normalized = normalizeMediaUrl(value);
  if (!normalized) return null;

  let parsed: URL;
  try {
    parsed = new URL(normalized);
  } catch {
    return null;
  }

  const id = extractYouTubeId(parsed);
  if (!id) return null;

  const start = parseYouTubeStart(
    parsed.searchParams.get("t") || parsed.searchParams.get("start"),
  );
  const listId = sanitizeYouTubeListId(parsed.searchParams.get("list"));

  return { id, url: normalized, start, listId };
};

/**
 * Build YouTube embed URL with privacy-enhanced mode
 */
export const buildYouTubeEmbedUrl = (
  item: YouTubeMedia,
  options: { autoplay?: boolean } = {},
): string => {
  const params = new URLSearchParams();
  params.set("rel", "0");
  params.set("modestbranding", "1");
  params.set("playsinline", "1");

  // origin is required for YouTube postMessage API and fixes playback issues
  if (typeof window !== "undefined") {
    params.set("origin", window.location.origin);
  }

  if (options.autoplay) {
    params.set("autoplay", "1");
  }
  if (item.start) {
    params.set("start", String(item.start));
  }
  if (item.listId) {
    params.set("list", item.listId);
  }

  const base = `https://www.youtube-nocookie.com/embed/${item.id}`;
  const query = params.toString();
  return query ? `${base}?${query}` : base;
};

/**
 * Get YouTube thumbnail URL
 */
export const getYouTubeThumbnailUrl = (id: string): string =>
  `https://i.ytimg.com/vi/${id}/hqdefault.jpg`;

// ============================================================================
// Attachment Extraction
// ============================================================================

/**
 * Extract attachments from a post object
 */
export const extractAttachments = (post?: {
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}): Attachment[] => {
  if (!post) return [];

  const items: Attachment[] = [];
  const meta = (post.metadata || {}) as Record<string, unknown>;
  const anyPost = post as Record<string, unknown>;

  const candidates = [
    anyPost.attachments,
    anyPost.files,
    anyPost.file_urls,
    anyPost.fileUrls,
    anyPost.accepted_attachments,
    anyPost.context_uploads,
    meta.attachments,
    meta.accepted_attachments,
    meta.context_uploads,
    meta.files,
    meta.file_urls,
    meta.fileUrls,
  ];

  const normalizeItem = (item: unknown): Attachment | null => {
    if (!item) return null;

    if (typeof item === "string") {
      return { url: item };
    }

    if (typeof item === "object" && item !== null) {
      const obj = item as Record<string, unknown>;
      const url =
        obj.url ||
        obj.file_url ||
        obj.fileUrl ||
        obj.download_url ||
        obj.preview_url;

      if (typeof url !== "string") return null;

      return {
        url,
        title:
          (obj.name as string) ||
          (obj.filename as string) ||
          (obj.title as string) ||
          (obj.context_key as string) ||
          (obj.key as string),
        mime:
          (obj.mime as string) ||
          (obj.mime_type as string) ||
          (obj.content_type as string),
        poster:
          (obj.thumbnail_url as string) ||
          (obj.poster_url as string) ||
          (obj.preview_url as string),
        contextKey:
          (obj.context_key as string) ||
          (obj.contextKey as string) ||
          (obj.key as string),
      };
    }

    return null;
  };

  candidates.forEach((candidate) => {
    if (!Array.isArray(candidate)) return;
    candidate.forEach((entry) => {
      const normalized = normalizeItem(entry);
      if (normalized) items.push(normalized);
    });
  });

  const singleUrls = [
    meta.file_url,
    meta.fileUrl,
    anyPost.file_url,
    anyPost.fileUrl,
  ];

  singleUrls.forEach((url) => {
    if (typeof url === "string") {
      items.push({ url });
    }
  });

  return items;
};

// ============================================================================
// Content Media Extraction
// ============================================================================

/**
 * Extract and separate media from markdown/HTML/plain URLs in content
 * Returns the cleaned text content and categorized media arrays
 */
export const extractMediaFromContent = (
  content: string,
  post?: { metadata?: Record<string, unknown>; [key: string]: unknown },
): ExtractedMedia => {
  const images: ImageMedia[] = [];
  const audio: AudioMedia[] = [];
  const video: VideoMedia[] = [];
  const youtube: YouTubeMedia[] = [];
  const files: FileMedia[] = [];
  const seen = new Set<string>();

  const addMedia = (
    kind: MediaKind,
    src: string,
    options: {
      alt?: string;
      title?: string;
      poster?: string;
      contextKey?: string;
    } = {},
  ): boolean => {
    const normalized = normalizeMediaUrl(src, { kind });
    if (!normalized) return false;

    const key = `${kind}:${normalized}`;
    if (seen.has(key)) return true;
    seen.add(key);

    if (kind === "image") {
      images.push({
        src: normalized,
        alt:
          options.alt || options.title || getFileLabel(normalized) || "Image",
        title: options.title,
        ...(options.contextKey ? { contextKey: options.contextKey } : {}),
      });
    } else if (kind === "audio") {
      audio.push({ src: normalized, title: options.title });
    } else if (kind === "video") {
      video.push({
        src: normalized,
        title: options.title,
        poster: options.poster,
      });
    }
    return true;
  };

  const addYouTube = (
    src: string,
    options: { title?: string } = {},
  ): boolean => {
    const parsed = parseYouTubeUrl(src);
    if (!parsed) return false;

    const key = `youtube:${parsed.id}:${parsed.start ?? 0}:${parsed.listId ?? ""}`;
    if (seen.has(key)) return true;
    seen.add(key);

    youtube.push({
      ...parsed,
      title: options.title,
    });
    return true;
  };

  const addFile = (
    src: string,
    options: { title?: string; mime?: string; contextKey?: string } = {},
  ): boolean => {
    const normalized = normalizeMediaUrl(src);
    if (!normalized) return false;
    if (!isDocumentMime(options.mime) && !isDocumentUrl(normalized))
      return false;

    const key = `file:${normalized}`;
    if (seen.has(key)) return true;
    seen.add(key);

    files.push({
      url: normalized,
      title: options.title,
      mime: options.mime,
      ...(options.contextKey ? { contextKey: options.contextKey } : {}),
    });
    return true;
  };

  let textContent = content || "";

  // Markdown images: ![alt](src)
  const markdownImageRegex = /!\[(.*?)\]\((.*?)\)/g;
  textContent = textContent.replace(markdownImageRegex, (match, alt, src) =>
    addMedia("image", src, { alt }) ? "" : match,
  );

  // HTML images
  const htmlImageRegex =
    /<img[^>]+src=["']([^"']+)["'][^>]*(?:alt=["']([^"']*?)["'])?[^>]*\/?>/gi;
  textContent = textContent.replace(htmlImageRegex, (match, src, alt) =>
    addMedia("image", src, { alt }) ? "" : match,
  );

  // HTML audio
  const htmlAudioRegex =
    /<audio[^>]+src=["']([^"']+)["'][^>]*>(?:.*?<\/audio>)?/gi;
  textContent = textContent.replace(htmlAudioRegex, (match, src) =>
    addMedia("audio", src) ? "" : match,
  );

  // HTML video
  const htmlVideoRegex =
    /<video[^>]+src=["']([^"']+)["'][^>]*>(?:.*?<\/video>)?/gi;
  textContent = textContent.replace(htmlVideoRegex, (match, src) =>
    addMedia("video", src) ? "" : match,
  );

  // HTML source elements
  const htmlSourceRegex = /<source[^>]+src=["']([^"']+)["'][^>]*\/?>/gi;
  textContent = textContent.replace(htmlSourceRegex, (match, src) => {
    const kind = getMediaKind(src);
    if (kind && addMedia(kind, src)) return "";
    return match;
  });

  // HTML iframes (YouTube embeds)
  const htmlIframeRegex =
    /<iframe[^>]+src=["']([^"']+)["'][^>]*>(?:.*?<\/iframe>)?/gi;
  textContent = textContent.replace(htmlIframeRegex, (match, src) => {
    if (addYouTube(src)) return "";
    return match;
  });

  // Markdown links that might be media
  const markdownLinkRegex = /\[(.*?)\]\((.*?)\)/g;
  textContent = textContent.replace(markdownLinkRegex, (match, label, url) => {
    if (addYouTube(url, { title: label })) {
      return label;
    }
    const kind = getMediaKind(url);
    if (kind && addMedia(kind, url, { title: label })) {
      return label;
    }
    return match;
  });

  // Plain URLs
  const urlRegex = /(https?:\/\/[^\s<]+|www\.[^\s<]+)/g;
  textContent = textContent.replace(urlRegex, (match) => {
    const cleaned = stripTrailingPunctuation(match);
    if (addYouTube(cleaned)) {
      return "";
    }
    const kind = getMediaKind(cleaned);
    if (kind && addMedia(kind, cleaned)) {
      return "";
    }
    return match;
  });

  // Process attachments from post metadata
  const attachments = extractAttachments(post);
  attachments.forEach((attachment) => {
    const kind = getMediaKind(attachment.url, attachment.mime);
    if (kind) {
      addMedia(kind, attachment.url, {
        title: attachment.title,
        poster: attachment.poster,
        contextKey: attachment.contextKey,
      });
      return;
    }
    if (
      addFile(attachment.url, {
        title: attachment.title,
        mime: attachment.mime,
        contextKey: attachment.contextKey,
      })
    ) {
      return;
    }
    addYouTube(attachment.url, { title: attachment.title });
  });

  // Clean up extra whitespace
  textContent = textContent
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]{2,}/g, " ")
    .trim();

  return { textContent, images, audio, video, youtube, files };
};

/**
 * Limit media items to a maximum count, prioritizing images first
 */
export const limitMediaItems = (
  images: ImageMedia[],
  audio: AudioMedia[],
  video: VideoMedia[],
  youtube: YouTubeMedia[],
  files: FileMedia[],
  limit: number,
): {
  visibleImages: ImageMedia[];
  visibleAudio: AudioMedia[];
  visibleVideo: VideoMedia[];
  visibleYouTube: YouTubeMedia[];
  visibleFiles: FileMedia[];
  visibleCount: number;
} => {
  let remaining = Math.max(0, limit);

  const visibleImages = images.slice(0, remaining);
  remaining -= visibleImages.length;

  const visibleAudio = audio.slice(0, remaining);
  remaining -= visibleAudio.length;

  const visibleVideo = video.slice(0, remaining);
  remaining -= visibleVideo.length;

  const visibleYouTube = youtube.slice(0, remaining);
  remaining -= visibleYouTube.length;

  const visibleFiles = files.slice(0, remaining);

  const visibleCount =
    visibleImages.length +
    visibleAudio.length +
    visibleVideo.length +
    visibleYouTube.length +
    visibleFiles.length;

  return {
    visibleImages,
    visibleAudio,
    visibleVideo,
    visibleYouTube,
    visibleFiles,
    visibleCount,
  };
};
