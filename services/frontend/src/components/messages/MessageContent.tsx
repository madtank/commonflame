import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { hasActiveSelection } from "./utils";
import { MessageImage } from "./MessageMedia";
import { cn } from "@/lib/utils";
import { sanitizeMessageContent } from "@/lib/content-sanitizer";
import { getMediaKindFromUrl, normalizeMediaUrl } from "@/lib/media-utils";
import { ExternalMarkdownLink } from "@/components/ui/external-markdown-link";

/**
 * Extended rehype-sanitize schema.
 * Extends the default GFM schema to explicitly allow <audio> and <source>
 * with a locked-down attribute set (src, controls, preload only).
 * No arbitrary iframes, no embeds, no data: or blob: src values.
 */
const sanitizeSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "audio", "source"],
  attributes: {
    ...defaultSchema.attributes,
    audio: ["controls", "preload"], // src intentionally omitted — extracted by media pipeline before reaching ReactMarkdown
    source: ["src", "type"], // type needed for MIME hint; src validated by isAllowedMediaUrl upstream
  },
};

export interface ParsedReasoningContent {
  answer: string;
  reasoning: string | null;
}

const splitLeadingItalicReasoning = (
  body: string,
): { reasoning: string; answer: string } | null => {
  const trimmed = body.trim();
  const marker = trimmed[0];
  if (marker !== "_" && marker !== "*") return null;

  let closeIndex = -1;
  for (let i = 1; i < trimmed.length; i += 1) {
    if (trimmed[i] === marker && trimmed[i - 1] !== "\\") {
      closeIndex = i;
      break;
    }
  }

  if (closeIndex <= 1) return null;

  const reasoning = trimmed.slice(1, closeIndex).trim();
  if (!reasoning) return null;

  // trimStart supports no-separator payloads like: _trace_Done — answer
  const answer = trimmed.slice(closeIndex + 1).trimStart();
  return { reasoning, answer };
};

export const parseReasoningContent = (text: string): ParsedReasoningContent => {
  const raw = String(text || "");
  if (!raw.trim()) return { answer: "", reasoning: null };

  const headerMatch = /(^|\n)\s*Reasoning:\s*/i.exec(raw);
  if (!headerMatch || headerMatch.index == null) {
    return { answer: raw, reasoning: null };
  }

  const markerOffset = headerMatch[0].toLowerCase().lastIndexOf("reasoning:");
  const headerStart = headerMatch.index + (headerMatch[1]?.length || 0);
  const afterHeader = raw.slice(
    headerStart + markerOffset + "Reasoning:".length,
  );
  const answerPrefix = raw.slice(0, headerStart).trim();
  const body = afterHeader.trim();

  if (!body) {
    return { answer: answerPrefix, reasoning: null };
  }

  let reasoning = "";
  let answer = "";

  const paragraphBreak = /\n\s*\n/.exec(body);
  if (paragraphBreak && paragraphBreak.index > 0) {
    reasoning = body.slice(0, paragraphBreak.index).trim();
    answer = body.slice(paragraphBreak.index + paragraphBreak[0].length).trim();
  }

  if (!reasoning) {
    const answerHeading = /\n(?:Final\s+answer|Answer|Result)\s*[:—-]\s*/i.exec(
      body,
    );
    if (answerHeading && answerHeading.index > 0) {
      reasoning = body.slice(0, answerHeading.index).trim();
      answer = body.slice(answerHeading.index + answerHeading[0].length).trim();
    }
  }

  if (!reasoning) {
    const doneMarker = /\sDone\s+[—-]\s*/i.exec(body);
    if (doneMarker && doneMarker.index > 0) {
      reasoning = body.slice(0, doneMarker.index).trim();
      answer = body.slice(doneMarker.index + 1).trim(); // keep "Done —" in answer
    }
  }

  if (!reasoning) {
    const italicSplit = splitLeadingItalicReasoning(body);
    if (italicSplit) {
      reasoning = italicSplit.reasoning;
      answer = italicSplit.answer;
    }
  }

  if (!reasoning) {
    // No trustworthy delimiter between reasoning/answer: render as plain answer
    // to avoid collapsing the entire payload into a hidden reasoning box.
    return {
      answer: [answerPrefix, body].filter(Boolean).join("\n\n"),
      reasoning: null,
    };
  }

  const mergedAnswer = [answerPrefix, answer].filter(Boolean).join("\n\n");
  return {
    answer: mergedAnswer,
    reasoning: reasoning || null,
  };
};

interface ReasoningContentProps {
  text: string;
  onHashtagClick?: (hashtag: string) => void;
  onAgentClick?: (agent: string) => void;
  collapseByDefault?: boolean;
  autoCollapse?: boolean;
  sanitizeSegments?: boolean;
  showReasoningPanel?: boolean;
}

export const ReasoningContent = ({
  text,
  onHashtagClick,
  onAgentClick,
  collapseByDefault = false,
  autoCollapse = false,
  sanitizeSegments = false,
  showReasoningPanel = true,
}: ReasoningContentProps) => {
  const parsed = useMemo(() => parseReasoningContent(text), [text]);
  const hadReasoningRef = useRef(false);
  const [isExpanded, setIsExpanded] = useState(!collapseByDefault);

  useEffect(() => {
    if (parsed.reasoning && !hadReasoningRef.current) {
      setIsExpanded(!collapseByDefault);
    }
    if (!parsed.reasoning) {
      setIsExpanded(!collapseByDefault);
    }
    hadReasoningRef.current = !!parsed.reasoning;
  }, [parsed.reasoning, collapseByDefault]);

  useEffect(() => {
    if (autoCollapse && parsed.reasoning) {
      setIsExpanded(false);
    }
  }, [autoCollapse, parsed.reasoning]);

  const answerText = sanitizeSegments
    ? sanitizeMessageContent(parsed.answer)
    : parsed.answer;
  const reasoningText = sanitizeSegments
    ? sanitizeMessageContent(parsed.reasoning || "")
    : parsed.reasoning || "";

  if (!parsed.reasoning) {
    const fallbackText = sanitizeSegments ? sanitizeMessageContent(text) : text;
    return (
      <>{renderMarkdownContent(fallbackText, onHashtagClick, onAgentClick)}</>
    );
  }

  if (!showReasoningPanel) {
    // Non-streaming timeline path: show final answer only.
    // If parser couldn't isolate an answer, fall back to full content to avoid blank messages.
    const finalAnswer = answerText.trim();
    const fallbackText = sanitizeSegments ? sanitizeMessageContent(text) : text;
    const contentToRender = finalAnswer || fallbackText;
    return (
      <>
        {renderMarkdownContent(contentToRender, onHashtagClick, onAgentClick)}
      </>
    );
  }

  // If sanitizer strips both sections, fall back to the original rendered content path.
  if (!answerText && !reasoningText) {
    const fallbackText = sanitizeSegments ? sanitizeMessageContent(text) : text;
    return (
      <>{renderMarkdownContent(fallbackText, onHashtagClick, onAgentClick)}</>
    );
  }

  return (
    <div className="space-y-2">
      {answerText ? (
        <div data-testid="reasoning-answer">
          {renderMarkdownContent(answerText, onHashtagClick, onAgentClick)}
        </div>
      ) : null}

      <div className="rounded-md border border-gray-200 dark:border-gray-700 bg-gray-100/70 dark:bg-gray-800/60 px-3 py-2">
        <button
          type="button"
          onClick={() => setIsExpanded((prev) => !prev)}
          className="text-xs font-medium text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-gray-100"
        >
          {isExpanded ? "Hide reasoning" : "Show reasoning"}
        </button>

        {isExpanded && reasoningText ? (
          <div
            data-testid="reasoning-content"
            className="mt-2 text-xs text-gray-600 dark:text-gray-300"
          >
            {renderMarkdownContent(reasoningText, onHashtagClick, onAgentClick)}
          </div>
        ) : null}
      </div>
    </div>
  );
};

// Helper function to highlight @mentions and #hashtags in text
export const highlightMentions = (
  text: string,
  onHashtagClick?: (hashtag: string) => void,
  onAgentClick?: (agent: string) => void,
): React.ReactNode => {
  const parts = text.split(/(@[a-zA-Z0-9_-]+|#[\w-]+)/g);
  return parts.map((part, index) => {
    if (part.match(/@[a-zA-Z0-9_-]+/)) {
      return (
        <span
          key={index}
          className="bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 px-1 py-0.5 rounded text-xs font-medium hover:bg-blue-200 dark:hover:bg-blue-800 transition-colors select-text"
          onPointerUp={(e) => {
            if (hasActiveSelection()) return;
            e.stopPropagation();
            onAgentClick?.(part.substring(1));
          }}
          title={`Filter posts by ${part}`}
          draggable={false}
        >
          {part}
        </span>
      );
    }
    if (part.match(/#[\w-]+/)) {
      return (
        <span
          key={index}
          className="bg-purple-100 dark:bg-purple-900 text-purple-700 dark:text-purple-300 px-1 py-0.5 rounded text-xs font-medium hover:bg-purple-200 dark:hover:bg-purple-800 transition-colors select-text"
          onPointerUp={(e) => {
            if (hasActiveSelection()) return;
            e.stopPropagation();
            onHashtagClick?.(part.substring(1));
          }}
          title={`Filter posts by ${part}`}
          draggable={false}
        >
          {part}
        </span>
      );
    }
    return part;
  });
};

// Render markdown with highlighted mentions and hashtags
export const renderMarkdownContent = (
  text: string,
  onHashtagClick?: (hashtag: string) => void,
  onAgentClick?: (agent: string) => void,
): React.ReactNode => {
  // Custom component for rendering paragraphs with highlighted mentions
  const ParagraphWithMentions = ({ children, ...props }: any) => {
    const processChildren = (children: any): any => {
      if (typeof children === "string") {
        return highlightMentions(children, onHashtagClick, onAgentClick);
      }
      if (Array.isArray(children)) {
        return children.map((child, i) => {
          if (typeof child === "string") {
            return (
              <span key={i}>
                {highlightMentions(child, onHashtagClick, onAgentClick)}
              </span>
            );
          }
          return child;
        });
      }
      return children;
    };

    const processed = processChildren(children);
    return (
      <p className="break-words overflow-wrap-anywhere" {...props}>
        {processed}
      </p>
    );
  };

  return (
    <div className="prose prose-sm dark:prose-invert min-w-0 max-w-none select-text overflow-x-hidden break-words overflow-wrap-anywhere prose-p:my-1 prose-headings:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0 prose-pre:my-1 prose-blockquote:my-1 prose-code:rounded prose-code:bg-gray-100 dark:prose-code:bg-gray-800">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeSanitize, sanitizeSchema]]}
        components={{
          p: ParagraphWithMentions,
          img: ({ src, alt }: any) => {
            if (!src) return null;
            return <MessageImage src={src} alt={alt} className="my-2" />;
          },
          pre: ({ children, ...props }: any) => (
            <pre
              className="max-w-full overflow-x-hidden whitespace-pre-wrap break-all bg-gray-100 p-2 rounded dark:bg-gray-800"
              {...props}
            >
              {children}
            </pre>
          ),
          code: ({ inline, className, children, ...props }: any) =>
            inline ? (
              <code
                className="overflow-wrap-anywhere break-words rounded bg-gray-100 px-1 py-0.5 text-sm dark:bg-gray-800"
                {...props}
              >
                {children}
              </code>
            ) : (
              <code
                className={cn(
                  "overflow-wrap-anywhere text-xs whitespace-pre-wrap break-words",
                  className,
                )}
                {...props}
              >
                {children}
              </code>
            ),
          a: ({ children, href, ...props }: any) => {
            // Render audio links as inline <audio> players.
            // We render a bare <audio> here (not the full MessageAudioPlayer div) because
            // ReactMarkdown wraps <a> elements in <p>, and a block-level div inside <p>
            // is invalid HTML. <audio> is phrasing content and is valid inside <p>.
            // Full styled players are rendered by renderMediaBlocks (via extractMediaFromContent
            // in MessageBubble) — this is belt-and-suspenders for edge-case links.
            if (href && getMediaKindFromUrl(href) === "audio") {
              const safeSrc = normalizeMediaUrl(href, { kind: "audio" });
              if (safeSrc) {
                return (
                  <audio
                    controls
                    preload="metadata"
                    data-testid="message-audio"
                    className="max-w-full"
                    aria-label={
                      typeof children === "string" ? children : "Audio clip"
                    }
                  >
                    <source src={safeSrc} />
                  </audio>
                );
              }
            }
            return (
              <ExternalMarkdownLink
                href={href}
                className="select-text break-all text-blue-600 hover:underline dark:text-blue-400"
                {...props}
              >
                {children}
              </ExternalMarkdownLink>
            );
          },
          blockquote: ({ children, ...props }: any) => (
            <blockquote
              className="border-l-4 border-gray-300 dark:border-gray-600 pl-4 italic"
              {...props}
            >
              {children}
            </blockquote>
          ),
          ul: ({ children, ...props }: any) => (
            <ul className="list-disc list-inside space-y-1 my-2" {...props}>
              {children}
            </ul>
          ),
          ol: ({ children, ...props }: any) => (
            <ol className="list-decimal list-inside space-y-1 my-2" {...props}>
              {children}
            </ol>
          ),
          li: ({ children, ...props }: any) => (
            <li className="text-gray-600 dark:text-gray-300" {...props}>
              {children}
            </li>
          ),
          table: ({ children, ...props }: any) => (
            <div
              className="my-2 w-full max-w-full overflow-x-hidden"
              role="region"
              aria-label="Markdown table"
            >
              <table
                className="w-full max-w-full table-fixed border-collapse border border-gray-300 dark:border-gray-600"
                {...props}
              >
                {children}
              </table>
            </div>
          ),
          th: ({ children, ...props }: any) => (
            <th
              className="break-words border border-gray-300 bg-gray-100 px-2 py-1 align-top dark:border-gray-600 dark:bg-gray-800"
              {...props}
            >
              {children}
            </th>
          ),
          td: ({ children, ...props }: any) => (
            <td
              className="break-words border border-gray-300 px-2 py-1 align-top dark:border-gray-600"
              {...props}
            >
              {children}
            </td>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
};
