import { useEffect, useMemo, useState } from "react";

/**
 * Specialized renderer for MCP pause messages.
 *
 * When an agent calls `messages(action="stop", …)` the backend inserts a
 * synthetic `agent_pause` row with metadata describing the pause duration,
 * expiry timestamp and reason code. This component is the single place where
 * that metadata is translated into UI, so keep it well-documented and easy to
 * extend (future work may add richer media, contextual tips, etc.).
 */

export interface AgentPauseMetadata {
  id?: number | string;
  content?: string | null;
  username?: string | null;
  agent_id?: string | number | null;
  uploaded_at?: string | null;
  pause_duration?: number | string | null;
  pause_expires_at?: string | null;
  pause_reason?: string | null;
  pause_reason_text?: string | null;
  pause_emoji?: string | null;
  metadata?: {
    content?: string | null;
    emoji?: string | null;
    reason?: string | null;
    reason_text?: string | null;
    pause_duration?: number | string | null;
    pause_expires_at?: string | null;
    pause_started_at?: string | null;
    uploaded_at?: string | null;
    agent_name?: string | null;
    agent_id?: string | number | null;
  } | null;
}

interface AgentPauseMessageProps {
  message: AgentPauseMetadata;
  className?: string;
  onResume?: () => void | Promise<void>;
  resumeLabel?: string;
  resumePending?: boolean;
  resumeDisabled?: boolean;
  isCleared?: boolean;
}

const DEFAULT_EMOJI = "💤";
const DEFAULT_TEXT = "Taking a break";
const DEFAULT_AGENT_DISPLAY = "This agent";
const CUSTOM_REASON_MAX_CHARS = 120;

/**
 * Keep this map in sync with
 * backend/mcp_modular/tools/message_helpers/stop_reasons.py.
 */
const STOP_REASON_LABELS: Record<string, string> = {
  break: "taking a break",
  need_user_input: "waiting for your input",
  need_feedback: "needing your feedback",
  thinking: "thinking this through…",
  processing: "processing your request…",
  researching: "researching this task…",
  ready_when_you_are: "ready when you are",
  recharging: "recharging for a moment",
  stretching: "stretching neural nets",
  coffee_break: "on a quick coffee break",
};

/**
 * Extended pictographic regex for capturing the leading emoji cluster (includes
 * skin tones + ZWJ). Using the constructor keeps the Unicode flag explicit.
 */
const LEADING_EMOJI_REGEX = new RegExp(
  "^((?:\\p{Extended_Pictographic}(?:\\uFE0F|\\uFE0E)?)(?:\\u200D(?:\\p{Extended_Pictographic}(?:\\uFE0F|\\uFE0E)?))*)(?:\\s+)?(.*)$",
  "u",
);

const extractPauseReason = (message: AgentPauseMetadata): string | null =>
  message.pause_reason ?? message.metadata?.reason ?? null;

const extractPauseReasonText = (message: AgentPauseMetadata): string | null =>
  message.pause_reason_text ?? message.metadata?.reason_text ?? null;

const extractPauseDuration = (
  message: AgentPauseMetadata,
): AgentPauseMetadata["pause_duration"] => {
  if (message.pause_duration !== undefined && message.pause_duration !== null) {
    return message.pause_duration;
  }
  return message.metadata?.pause_duration ?? null;
};

const extractPauseExpiresAt = (message: AgentPauseMetadata): string | null => {
  if (message.pause_expires_at) {
    return message.pause_expires_at;
  }
  return message.metadata?.pause_expires_at ?? null;
};

const extractUploadedAt = (message: AgentPauseMetadata): string | null => {
  if (message.uploaded_at) {
    return message.uploaded_at;
  }
  return (
    message.metadata?.uploaded_at ?? message.metadata?.pause_started_at ?? null
  );
};

const normalizeDurationSeconds = (
  value: AgentPauseMetadata["pause_duration"],
): number | null => {
  if (value === null || value === undefined) {
    return null;
  }
  const numeric = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(numeric) || numeric <= 0) {
    return null;
  }
  return numeric;
};

const resolveExpiryTimestamp = (
  pauseExpiresAt: string | null,
  uploadedAt: string | null,
  durationSeconds: number | null,
): number | null => {
  if (pauseExpiresAt) {
    // Ensure we parse as UTC
    const timestamp = new Date(pauseExpiresAt).getTime();
    if (!Number.isNaN(timestamp)) {
      return timestamp;
    }
  }

  if (durationSeconds) {
    if (uploadedAt) {
      const uploadedTimestamp = new Date(uploadedAt).getTime();
      if (!Number.isNaN(uploadedTimestamp)) {
        return uploadedTimestamp + durationSeconds * 1000;
      }
    }
    return Date.now() + durationSeconds * 1000;
  }

  return null;
};

const computeSecondsLeft = (
  expiryTimestamp: number | null,
  fallbackDuration: number | null,
): number => {
  if (expiryTimestamp) {
    return Math.max(0, Math.ceil((expiryTimestamp - Date.now()) / 1000));
  }
  if (fallbackDuration) {
    return Math.max(0, Math.ceil(fallbackDuration));
  }
  return 0;
};

const parseContent = (rawContent?: string | null) => {
  const trimmed = (rawContent || "").trim();
  if (!trimmed) {
    return { emoji: DEFAULT_EMOJI, text: DEFAULT_TEXT };
  }

  const match = trimmed.match(LEADING_EMOJI_REGEX);
  if (match && match[1]) {
    const emoji = match[1].trim() || DEFAULT_EMOJI;
    const text = (match[2] || "").trim() || DEFAULT_TEXT;
    return { emoji, text };
  }

  return { emoji: DEFAULT_EMOJI, text: trimmed };
};

const extractAgentNameFromText = (text?: string | null) => {
  if (!text) return null;
  const parts = text.trim().split(/\s+/);
  return parts[0] || null;
};

const deriveContentSource = (message: AgentPauseMetadata): string | null => {
  if (message.content && message.content.trim()) {
    return message.content;
  }

  const metadataContent = message.metadata?.content;
  if (metadataContent && metadataContent.trim()) {
    return metadataContent;
  }

  const emoji = message.pause_emoji || message.metadata?.emoji || DEFAULT_EMOJI;
  const agentLabel =
    message.metadata?.agent_name || message.username || DEFAULT_AGENT_DISPLAY;
  const reasonCode = extractPauseReason(message);
  const reasonLabel =
    reasonCode && STOP_REASON_LABELS[reasonCode]
      ? STOP_REASON_LABELS[reasonCode]
      : DEFAULT_TEXT.toLowerCase();
  return `${emoji} ${agentLabel} ${reasonLabel}`.trim();
};

export function AgentPauseMessage({
  message,
  className,
  onResume,
  resumeLabel = "Resume now",
  resumePending = false,
  resumeDisabled = false,
  isCleared = false,
}: AgentPauseMessageProps) {
  const rawContent = useMemo(
    () => deriveContentSource(message),
    [
      message.content,
      message.metadata,
      message.pause_reason,
      message.pause_emoji,
      message.username,
    ],
  );

  const parsedContent = useMemo(() => parseContent(rawContent), [rawContent]);
  const displayEmoji =
    message.pause_emoji || message.metadata?.emoji || parsedContent.emoji;
  const reasonTextFromMetadata = extractPauseReasonText(message);

  const reasonRaw = extractPauseReason(message);
  const normalizedReasonCode = reasonRaw?.toLowerCase().trim() ?? null;
  const mappedReasonLabel = normalizedReasonCode
    ? STOP_REASON_LABELS[normalizedReasonCode]
    : undefined;

  const durationSeconds = useMemo(
    () => normalizeDurationSeconds(extractPauseDuration(message)),
    [message.pause_duration, message.metadata],
  );

  const expiryTimestamp = useMemo(
    () =>
      resolveExpiryTimestamp(
        extractPauseExpiresAt(message),
        extractUploadedAt(message),
        durationSeconds,
      ),
    [
      message.pause_expires_at,
      message.metadata,
      message.uploaded_at,
      durationSeconds,
    ],
  );

  const [secondsLeft, setSecondsLeft] = useState(() =>
    computeSecondsLeft(expiryTimestamp, durationSeconds),
  );

  useEffect(() => {
    setSecondsLeft(() => computeSecondsLeft(expiryTimestamp, durationSeconds));

    if (!expiryTimestamp || expiryTimestamp <= Date.now()) {
      return;
    }

    const intervalId = window.setInterval(() => {
      setSecondsLeft(() => {
        const next = computeSecondsLeft(expiryTimestamp, durationSeconds);
        if (next <= 0) {
          window.clearInterval(intervalId);
        }
        return next;
      });
    }, 1000);

    return () => window.clearInterval(intervalId);
  }, [expiryTimestamp, durationSeconds]);

  const resumeTimeLabel = useMemo(() => {
    if (!expiryTimestamp) return null;
    try {
      const resumeDate = new Date(expiryTimestamp);
      return resumeDate.toLocaleString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return null;
    }
  }, [expiryTimestamp]);

  const agentDisplayName = useMemo(
    () =>
      message.metadata?.agent_name ||
      message.username ||
      (() => {
        const candidate = extractAgentNameFromText(parsedContent.text);
        if (candidate && !/^this$/i.test(candidate)) {
          return candidate;
        }
        return null;
      })() ||
      DEFAULT_AGENT_DISPLAY,
    [message.username, message.metadata, parsedContent.text],
  );

  const displayText = useMemo(() => {
    if (reasonTextFromMetadata) {
      return `${agentDisplayName} ${reasonTextFromMetadata}`;
    }
    if (mappedReasonLabel) {
      return `${agentDisplayName} ${mappedReasonLabel}`;
    }
    if (reasonRaw) {
      return `${agentDisplayName} ${reasonRaw}`;
    }
    return parsedContent.text;
  }, [agentDisplayName, mappedReasonLabel, reasonRaw, parsedContent.text]);

  const truncatedText = useMemo(() => {
    if (!displayText) return displayText;
    if (displayText.length <= CUSTOM_REASON_MAX_CHARS) {
      return displayText;
    }
    return `${displayText.slice(0, CUSTOM_REASON_MAX_CHARS - 1)}…`;
  }, [displayText]);

  const countdownLabel = `${Math.max(0, secondsLeft)}s`;
  const showCountdown = !isCleared && secondsLeft > 0;
  const showResumeButton = Boolean(onResume) && !isCleared && secondsLeft > 0;
  const showReadyStatus = isCleared || secondsLeft <= 0;
  const readyLabel = isCleared ? "Resumed" : "Ready";

  const classes = ["agent-pause-message", "flash-effect", className]
    .filter(Boolean)
    .join(" ")
    .trim();

  return (
    <div className={classes}>
      <div className="agent-pause-body">
        <span className="agent-pause-emoji" aria-hidden="true">
          {displayEmoji}
        </span>
        <span
          className="agent-pause-text"
          title={
            reasonTextFromMetadata || reasonRaw
              ? `${displayText}\nTip: Start pause reasons with an emoji (e.g., "🔍 investigating logs") for quicker scanning.`
              : displayText || undefined
          }
        >
          {truncatedText}
        </span>
      </div>
      <div className="agent-pause-actions">
        {showCountdown && (
          <span
            className="agent-pause-countdown"
            aria-live="polite"
            title={
              resumeTimeLabel ? `Resumes at ${resumeTimeLabel}` : undefined
            }
          >
            {countdownLabel}
          </span>
        )}
        {showReadyStatus && (
          <span
            className="agent-pause-status"
            data-variant={isCleared ? "manual" : "auto"}
          >
            {readyLabel}
          </span>
        )}
        {showResumeButton && (
          <button
            type="button"
            className="agent-pause-resume"
            onClick={onResume}
            disabled={resumeDisabled || resumePending}
          >
            {resumePending && (
              <span className="agent-pause-resume-spinner" aria-hidden="true" />
            )}
            <span>{resumePending ? "Resuming…" : resumeLabel}</span>
          </button>
        )}
      </div>
    </div>
  );
}
