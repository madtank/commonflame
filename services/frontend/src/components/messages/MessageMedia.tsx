import { useState, type MouseEvent } from "react";
import { Play, FileText, Download, ImageIcon } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import {
  type ImageMedia,
  type AudioMedia,
  type VideoMedia,
  type YouTubeMedia,
  type FileMedia,
  buildYouTubeEmbedUrl,
  getYouTubeThumbnailUrl,
  normalizeImageSrc,
  normalizeMediaUrl,
  getFileLabel,
  isMarkdownAttachment,
  isTextPreviewableAttachment,
} from "@/lib/media-utils";
import {
  fetchAuthenticatedMediaText,
  openAuthenticatedMediaUrl,
  useAuthenticatedMediaUrl,
} from "@/lib/authenticated-media";
import { ExternalMarkdownLink } from "@/components/ui/external-markdown-link";

export const MessageImage = ({
  src,
  alt,
  title,
  contextKey,
  className,
}: ImageMedia & { className?: string }) => {
  const [open, setOpen] = useState(false);
  const [hasError, setHasError] = useState(false);
  const safeSrc = normalizeMediaUrl(src, { kind: "image" });
  const finalSrc = safeSrc ? normalizeImageSrc(safeSrc) : "";
  const authenticatedImage = useAuthenticatedMediaUrl(finalSrc, {
    enabled: Boolean(finalSrc),
  });
  if (!safeSrc) return null;
  const imageSrc = authenticatedImage.src || finalSrc;
  const label = title || alt || "Image";
  const imageClassName =
    `h-full w-full object-contain ${className || ""}`.trim();

  if (hasError || authenticatedImage.error) {
    return (
      <MessageFileAttachment
        url={safeSrc}
        title={label}
        contextKey={contextKey}
        icon="image"
        subtitle={
          contextKey
            ? "Image attachment · added to context"
            : "Image attachment"
        }
      />
    );
  }

  const handleImageError = () => {
    console.warn("Image failed to load, hiding:", finalSrc);
    setOpen(false);
    setHasError(true);
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        data-testid="message-image-frame"
        className="block aspect-[4/3] min-h-32 w-full max-w-full overflow-hidden rounded-lg bg-gray-100 text-left shadow-md dark:bg-gray-900/40"
        aria-label={`Open image ${label}`}
      >
        {authenticatedImage.requiresAuth && authenticatedImage.loading ? (
          <span className="flex h-full min-h-32 w-full items-center justify-center text-xs text-slate-400">
            Loading image...
          </span>
        ) : (
          <img
            src={imageSrc}
            alt={label}
            className={imageClassName}
            loading="lazy"
            onError={handleImageError}
          />
        )}
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-5xl w-[95vw] border-0 bg-black/90 p-2 sm:p-4">
          <DialogTitle className="sr-only">{label}</DialogTitle>
          <div className="flex max-h-[80vh] items-center justify-center">
            <img
              src={imageSrc}
              alt={label}
              className="max-h-[80vh] w-auto rounded-md"
              onError={handleImageError}
            />
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
};

export const MessageAudioPlayer = ({ src, title }: AudioMedia) => {
  const label = title || getFileLabel(src) || "Audio clip";
  const safeSrc = normalizeMediaUrl(src, { kind: "audio" });
  const authenticatedAudio = useAuthenticatedMediaUrl(safeSrc, {
    enabled: Boolean(safeSrc),
  });
  if (!safeSrc) return null;
  if (authenticatedAudio.error) {
    return (
      <MessageFileAttachment
        url={safeSrc}
        title={label}
        subtitle="Audio attachment"
      />
    );
  }
  const audioSrc = authenticatedAudio.src || safeSrc;
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-900/40">
      <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">{label}</p>
      <audio
        key={audioSrc}
        controls
        preload="metadata"
        className="w-full"
        data-testid="message-audio"
      >
        <source src={audioSrc} />
        Your browser does not support the audio element.
      </audio>
    </div>
  );
};

export const MessageVideoPlayer = ({ src, title, poster }: VideoMedia) => {
  const label = title || getFileLabel(src) || "Video clip";
  const safeSrc = normalizeMediaUrl(src, { kind: "video" });
  const authenticatedVideo = useAuthenticatedMediaUrl(safeSrc, {
    enabled: Boolean(safeSrc),
  });
  if (!safeSrc) return null;
  if (authenticatedVideo.error) {
    return (
      <MessageFileAttachment
        url={safeSrc}
        title={label}
        subtitle="Video attachment"
      />
    );
  }
  const videoSrc = authenticatedVideo.src || safeSrc;
  return (
    <div className="overflow-hidden rounded-lg border border-gray-200 bg-gray-50 dark:border-gray-700 dark:bg-gray-900/40">
      <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
        {label}
      </div>
      <div
        data-testid="message-video-frame"
        className="aspect-video w-full bg-black"
      >
        <video
          key={videoSrc}
          controls
          preload="metadata"
          playsInline
          poster={poster}
          className="h-full w-full bg-black object-contain"
          data-testid="message-video"
        >
          <source src={videoSrc} />
          Your browser does not support the video element.
        </video>
      </div>
    </div>
  );
};

export const MessageYouTubeEmbed = ({
  id,
  url,
  title,
  start,
  listId,
}: YouTubeMedia) => {
  const [isActive, setIsActive] = useState(false);
  const [hasError, setHasError] = useState(false);
  const label = title || "YouTube video";
  const embedUrl = buildYouTubeEmbedUrl(
    { id, url, title, start, listId },
    { autoplay: true },
  );
  const thumbnailUrl = getYouTubeThumbnailUrl(id);

  if (hasError) {
    return null;
  }

  return (
    <div
      className="overflow-hidden rounded-lg border border-gray-200 bg-gray-50 dark:border-gray-700 dark:bg-gray-900/40"
      data-testid="message-youtube"
    >
      <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
        {label}
      </div>
      <div className="relative aspect-video bg-black">
        {isActive ? (
          <iframe
            title={label}
            src={embedUrl}
            loading="lazy"
            referrerPolicy="origin"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowFullScreen
            className="h-full w-full"
          />
        ) : (
          <button
            type="button"
            onClick={() => setIsActive(true)}
            className="group relative h-full w-full"
            aria-label={`Play ${label}`}
          >
            <img
              src={thumbnailUrl}
              alt={label}
              className="h-full w-full object-cover"
              loading="lazy"
              onError={() => {
                console.warn("YouTube thumbnail failed, hiding:", thumbnailUrl);
                setHasError(true);
              }}
            />
            <span className="absolute inset-0 bg-black/30 transition-opacity group-hover:bg-black/40" />
            <span className="absolute left-1/2 top-1/2 flex h-12 w-12 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-white/90 text-gray-900 shadow-lg transition-transform group-hover:scale-105">
              <Play className="h-5 w-5" />
            </span>
          </button>
        )}
      </div>
    </div>
  );
};

export const MessageFileAttachment = ({
  url,
  title,
  mime,
  contextKey,
  subtitle,
  icon = "file",
}: FileMedia & { subtitle?: string; icon?: "file" | "image" }) => {
  const [previewOpen, setPreviewOpen] = useState(false);
  const [textPreview, setTextPreview] = useState<string | null>(null);
  const [textPreviewLoading, setTextPreviewLoading] = useState(false);
  const [textPreviewError, setTextPreviewError] = useState<string | null>(null);
  const safeUrl = normalizeMediaUrl(url);
  if (!safeUrl) return null;
  const label = title || getFileLabel(safeUrl) || "Attachment";
  const Icon = icon === "image" ? ImageIcon : FileText;
  const isMarkdown = isMarkdownAttachment(mime, label || safeUrl);
  const canPreviewText = isTextPreviewableAttachment({
    mime,
    filename: label,
    url: safeUrl,
  });
  const content = (
    <span className="flex items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-3 text-sm text-slate-100 transition-colors hover:bg-white/[0.08]">
      <span className="flex min-w-0 items-center gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-md bg-cyan-500/10 text-cyan-300">
          <Icon className="h-4 w-4" />
        </span>
        <span className="min-w-0">
          <span className="block truncate font-medium">{label}</span>
          <span className="block text-xs text-slate-400">
            {subtitle || (contextKey ? "Added to context" : "Open attachment")}
          </span>
        </span>
      </span>
      <Download className="h-4 w-4 shrink-0 text-slate-400" />
    </span>
  );

  if (canPreviewText) {
    const handleOpenText = () => {
      setPreviewOpen(true);
      if (textPreview || textPreviewLoading) return;
      setTextPreviewLoading(true);
      setTextPreviewError(null);
      void fetchAuthenticatedMediaText(safeUrl)
        .then((value) => setTextPreview(value))
        .catch((error) => {
          setTextPreviewError(
            error instanceof Error
              ? error.message
              : "Attachment preview is unavailable.",
          );
        })
        .finally(() => setTextPreviewLoading(false));
    };

    return (
      <>
        <button
          type="button"
          onClick={handleOpenText}
          className="w-full text-left"
          aria-label={`Open attachment preview ${label}`}
        >
          {content}
        </button>
        <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
          <DialogContent className="flex h-[82vh] max-h-[82vh] max-w-4xl flex-col overflow-hidden border border-white/10 bg-slate-950 p-0 text-slate-100">
            <div className="border-b border-white/10 px-5 py-4">
              <DialogTitle className="truncate text-base font-semibold text-white">
                {label}
              </DialogTitle>
              <p className="mt-1 truncate text-xs text-slate-400">
                {contextKey ? `Context: ${contextKey}` : "Text attachment"}
              </p>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              {textPreviewLoading ? (
                <p className="text-sm text-slate-400">Loading preview...</p>
              ) : textPreviewError ? (
                <p className="text-sm text-rose-300">{textPreviewError}</p>
              ) : isMarkdown ? (
                <div className="prose prose-invert max-w-none text-sm leading-7 [&_pre]:overflow-x-auto [&_pre]:rounded-2xl [&_pre]:bg-black/40 [&_pre]:p-4">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: ({ children, href, ...props }) => (
                        <ExternalMarkdownLink
                          href={href}
                          className="text-cyan-200 hover:underline"
                          {...props}
                        >
                          {children}
                        </ExternalMarkdownLink>
                      ),
                    }}
                  >
                    {textPreview || ""}
                  </ReactMarkdown>
                </div>
              ) : (
                <pre className="whitespace-pre-wrap break-words rounded-2xl bg-black/35 p-4 font-mono text-xs leading-6 text-slate-100">
                  {textPreview || ""}
                </pre>
              )}
            </div>
          </DialogContent>
        </Dialog>
      </>
    );
  }

  const handleClick = async (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    try {
      await openAuthenticatedMediaUrl(safeUrl);
    } catch (error) {
      console.warn("Attachment failed to open:", error);
      window.open(safeUrl, "_blank", "noopener,noreferrer");
    }
  };
  return (
    <a
      href={safeUrl}
      target="_blank"
      rel="noopener noreferrer"
      onClick={handleClick}
      className="block"
    >
      {content}
    </a>
  );
};

export const renderMediaBlocks = (
  images: ImageMedia[],
  audio: AudioMedia[],
  video: VideoMedia[],
  youtube: YouTubeMedia[],
  files: FileMedia[] = [],
) => {
  if (
    images.length === 0 &&
    audio.length === 0 &&
    video.length === 0 &&
    youtube.length === 0 &&
    files.length === 0
  ) {
    return null;
  }

  return (
    <div className="mt-3 space-y-3">
      {images.length > 0 && (
        <div className={images.length === 1 ? "" : "grid gap-2 sm:grid-cols-2"}>
          {images.map((img, idx) => (
            <MessageImage
              key={`${img.src}-${idx}`}
              {...img}
              alt={img.alt || img.title || "Image"}
            />
          ))}
        </div>
      )}
      {audio.length > 0 && (
        <div className="space-y-2">
          {audio.map((track, idx) => (
            <MessageAudioPlayer key={`${track.src}-${idx}`} {...track} />
          ))}
        </div>
      )}
      {video.length > 0 && (
        <div className="space-y-2">
          {video.map((clip, idx) => (
            <MessageVideoPlayer key={`${clip.src}-${idx}`} {...clip} />
          ))}
        </div>
      )}
      {youtube.length > 0 && (
        <div className="space-y-2">
          {youtube.map((item, idx) => (
            <MessageYouTubeEmbed key={`${item.id}-${idx}`} {...item} />
          ))}
        </div>
      )}
      {files.length > 0 && (
        <div className="space-y-2">
          {files.map((file, idx) => (
            <MessageFileAttachment
              key={`${file.url}-${idx}`}
              {...file}
              subtitle={file.contextKey ? "Added to context" : undefined}
            />
          ))}
        </div>
      )}
    </div>
  );
};
