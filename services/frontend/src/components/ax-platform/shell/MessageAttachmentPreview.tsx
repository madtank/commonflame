import { useState, type MouseEvent } from "react";
import { ImageIcon, Download, Paperclip } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { ExternalMarkdownLink } from "@/components/ui/external-markdown-link";
import {
  MessageAudioPlayer,
  MessageVideoPlayer,
} from "@/components/messages/MessageMedia";
import {
  fetchAuthenticatedMediaText,
  openAuthenticatedMediaUrl,
  useAuthenticatedMediaUrl,
} from "@/lib/authenticated-media";
import {
  getMediaKind,
  isMarkdownAttachment,
  isTextPreviewableAttachment,
} from "@/lib/media-utils";
import { cn } from "@/lib/utils";

import type { ChatEntryAttachment } from "./transcript-model";

function formatAttachmentSize(sizeBytes?: number) {
  const value = typeof sizeBytes === "number" ? sizeBytes : 0;
  if (!Number.isFinite(value) || value <= 0) return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function isBrowserFetchableMediaAttachmentUrl(url?: string) {
  const value = (url || "").trim();
  return (
    /^https?:\/\//i.test(value) ||
    value.startsWith("/api/") ||
    value.startsWith("blob:") ||
    /^data:(?:audio|video)\//i.test(value)
  );
}

export function MessageAttachmentPreview({
  attachment,
  userTone,
}: {
  attachment: ChatEntryAttachment;
  userTone: boolean;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [textPreview, setTextPreview] = useState<string | null>(null);
  const [textPreviewLoading, setTextPreviewLoading] = useState(false);
  const [textPreviewError, setTextPreviewError] = useState<string | null>(null);
  const label = attachment.name || "Attachment";
  const mediaKind =
    attachment.url && attachment.contentType
      ? getMediaKind(attachment.url, attachment.contentType)
      : null;
  const isImage = attachment.contentType?.startsWith("image/");
  const isMarkdown = isMarkdownAttachment(
    attachment.contentType,
    attachment.name,
  );
  const canPreviewText = isTextPreviewableAttachment({
    mime: attachment.contentType,
    filename: attachment.name,
    url: attachment.url,
  });
  const authenticatedPreview = useAuthenticatedMediaUrl(attachment.url || "", {
    enabled: Boolean(isImage && attachment.url && !imageFailed),
  });
  const previewSrc =
    authenticatedPreview.src ||
    (!authenticatedPreview.requiresAuth ? attachment.url : "");
  const canPreviewImage = Boolean(
    isImage && previewSrc && !imageFailed && !authenticatedPreview.error,
  );
  const canPlayInline = isBrowserFetchableMediaAttachmentUrl(attachment.url);

  if (attachment.url && canPlayInline && mediaKind === "audio") {
    return <MessageAudioPlayer src={attachment.url} title={label} />;
  }

  if (attachment.url && canPlayInline && mediaKind === "video") {
    return <MessageVideoPlayer src={attachment.url} title={label} />;
  }

  const sizeLabel = formatAttachmentSize(attachment.sizeBytes);
  const detailParts = [
    isImage ? "Image attachment" : "Attachment",
    sizeLabel,
    attachment.contextKey ? "Added to context" : "",
  ].filter(Boolean);
  const content = (
    <span
      className={cn(
        "inline-flex w-full max-w-full items-center gap-2 overflow-hidden rounded-xl border px-2.5 py-2 text-left text-xs sm:max-w-sm",
        userTone
          ? "border-white/20 bg-white/10 text-white/90"
          : "border-white/10 bg-white/[0.04] text-slate-200",
      )}
      title={
        attachment.contextKey
          ? `${label}\nContext: ${attachment.contextKey}`
          : label
      }
    >
      <span
        className={cn(
          "flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-lg border",
          userTone
            ? "border-white/15 bg-white/10"
            : "border-white/10 bg-slate-950/30",
        )}
      >
        {canPreviewImage ? (
          <img
            src={previewSrc}
            alt={label}
            className="h-full w-full object-cover"
            loading="lazy"
            onError={() => setImageFailed(true)}
          />
        ) : isImage ? (
          <ImageIcon className="h-4 w-4 text-cyan-200" />
        ) : (
          <Paperclip className="h-4 w-4 text-cyan-200" />
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{label}</span>
        <span
          className={cn(
            "block truncate",
            userTone ? "text-white/65" : "text-slate-400",
          )}
        >
          {detailParts.join(" · ")}
        </span>
      </span>
      <Download className="h-4 w-4 shrink-0 text-slate-400" />
    </span>
  );

  if (!attachment.url) return content;

  if (isImage) {
    return (
      <>
        <button
          type="button"
          className="max-w-full text-left"
          onClick={() => setPreviewOpen(true)}
          aria-label={`Open image ${label}`}
        >
          {content}
        </button>
        <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
          <DialogContent className="max-w-5xl w-[95vw] border-0 bg-black/90 p-2 sm:p-4">
            <DialogTitle className="sr-only">{label}</DialogTitle>
            <div className="flex min-h-48 max-h-[80vh] items-center justify-center">
              {canPreviewImage ? (
                <img
                  src={previewSrc}
                  alt={label}
                  className="max-h-[80vh] w-auto rounded-md"
                  onError={() => setImageFailed(true)}
                />
              ) : authenticatedPreview.loading ? (
                <span className="text-sm text-slate-300">Loading image...</span>
              ) : (
                <span className="text-sm text-slate-300">
                  Image preview is unavailable.
                </span>
              )}
            </div>
          </DialogContent>
        </Dialog>
      </>
    );
  }

  if (canPreviewText) {
    const handleOpenText = () => {
      setPreviewOpen(true);
      if (textPreview || textPreviewLoading) return;
      setTextPreviewLoading(true);
      setTextPreviewError(null);
      void fetchAuthenticatedMediaText(attachment.url || "")
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
          className="max-w-full text-left"
          onClick={handleOpenText}
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
              <p className="mt-1 text-xs text-slate-400">
                {attachment.contextKey
                  ? `Context: ${attachment.contextKey}`
                  : "Text attachment"}
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
      await openAuthenticatedMediaUrl(attachment.url || "");
    } catch (error) {
      console.warn("Attachment failed to open:", error);
      window.open(attachment.url, "_blank", "noopener,noreferrer");
    }
  };

  return (
    <a
      href={attachment.url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={handleClick}
      className="inline-block max-w-full"
    >
      {content}
    </a>
  );
}
