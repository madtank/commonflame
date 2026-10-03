import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { Plus, Sparkles } from "lucide-react";

// Support both predefined actions and dynamic emojis
export type ReactionAction =
  | "kudos"
  | "abuse"
  | "flag"
  | "helpful"
  | "excellent"
  | string;

// Dynamic reaction counts - can have any emoji as key
export interface ReactionCounts {
  [emoji: string]: number;
}

// Dynamic user reaction state - tracks which emojis user has reacted with
export interface UserReactionState {
  [emoji: string]: boolean;
}

// AI reaction tracking - which emojis were added by AI
export interface AIReactionState {
  [emoji: string]: boolean;
}

// Default quick-access reactions
const DEFAULT_REACTIONS: { emoji: string; label: string; tooltip: string }[] = [
  { emoji: "👍", label: "Thumbs up", tooltip: "Like this message" },
  { emoji: "👎", label: "Thumbs down", tooltip: "Dislike this message" },
  { emoji: "🔥", label: "Fire", tooltip: "This is fire!" },
  { emoji: "🚀", label: "Rocket", tooltip: "Ship it!" },
  { emoji: "💯", label: "100", tooltip: "Absolutely!" },
];

/**
 * Extract the first grapheme cluster (emoji or character) from a string.
 * Uses Intl.Segmenter when available for correct multi-codepoint emoji handling
 * (skin tone variants, ZWJ sequences like 👨‍💻). Falls back to codepoint check.
 */
function extractFirstEmoji(text: string): string | null {
  if (!text.trim()) return null;

  if (typeof Intl !== "undefined" && "Segmenter" in Intl) {
    const segmenter = new (Intl as any).Segmenter();
    const [first] = segmenter.segment(text.trim());
    const ch = first?.segment as string | undefined;
    // Must be non-ASCII to qualify as an emoji (filter plain text input)
    if (ch && ch.codePointAt(0)! > 127) return ch;
    return null;
  }

  // Fallback: check if first codepoint is outside ASCII range
  const cp = text.trim().codePointAt(0);
  if (cp !== undefined && cp > 127) {
    // Return first full codepoint (handles surrogate pairs)
    return String.fromCodePoint(cp);
  }
  return null;
}

/** OS-specific hint for opening the emoji picker */
const EMOJI_HINT =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform)
    ? "Ctrl ⌘ Space"
    : "Win + .";

interface EmojiPickerInputProps {
  onPick: (emoji: string) => void;
  onClose: () => void;
  anchorEl: HTMLElement | null;
}

/** Common emoji grid for quick selection on all devices */
const EMOJI_GRID = [
  "😀",
  "😂",
  "🥹",
  "😍",
  "🤔",
  "😮",
  "😢",
  "😡",
  "👍",
  "👎",
  "👏",
  "🙌",
  "🔥",
  "💯",
  "🚀",
  "⭐",
  "❤️",
  "💪",
  "🎉",
  "✅",
  "⚡",
  "👀",
  "🤝",
  "💡",
];

function EmojiPickerInput({
  onPick,
  onClose,
  anchorEl,
}: EmojiPickerInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  // useRef<T | null> (not useRef<T>) gives MutableRefObject — current is writable
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [positionStyle, setPositionStyle] = useState({
    bottom: "auto",
    right: "0.5rem",
    left: "0.5rem",
    maxWidth: "calc(100vw - 1rem)",
  });

  // Auto-focus the input when the picker opens (desktop only — avoid keyboard flash on mobile)
  useEffect(() => {
    const isMobile = window.innerWidth < 640 || "ontouchstart" in window;
    if (!isMobile) {
      const t = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(t);
    }
  }, []);

  useLayoutEffect(() => {
    if (!anchorEl) return;

    const updatePosition = () => {
      const rect = anchorEl.getBoundingClientRect();
      const next = {
        bottom: `${Math.max(8, window.innerHeight - rect.top + 6)}px`,
        right: "0.5rem",
        left: "0.5rem",
        maxWidth: "calc(100vw - 1rem)",
      };

      if (window.innerWidth > 480) {
        next.left = "auto";
        next.right = `${Math.max(8, window.innerWidth - rect.right)}px`;
        next.maxWidth = "320px";
      }

      setPositionStyle(next);
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [anchorEl]);

  // Close on outside click — use touchend + mousedown for mobile compat
  useEffect(() => {
    const handler = (e: Event) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("touchend", handler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("touchend", handler);
    };
  }, [onClose]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  const handleInput = (e: React.FormEvent<HTMLInputElement>) => {
    const value = (e.target as HTMLInputElement).value;
    const emoji = extractFirstEmoji(value);
    if (emoji) {
      onPick(emoji);
      onClose();
    }
  };

  return (
    <div
      ref={containerRef}
      className="fixed z-50 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl shadow-lg p-3"
      style={positionStyle}
      role="dialog"
      aria-label="Emoji picker"
    >
      {/* Emoji grid — works on mobile without keyboard */}
      <div className="grid grid-cols-8 gap-1 mb-2">
        {EMOJI_GRID.map((emoji) => (
          <button
            key={emoji}
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onPick(emoji);
              onClose();
            }}
            className="w-9 h-9 flex items-center justify-center text-xl rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 active:bg-gray-200 dark:active:bg-gray-700 transition-colors"
          >
            {emoji}
          </button>
        ))}
      </div>

      {/* Text input for search/paste — tap to open native keyboard + emoji */}
      <input
        ref={inputRef}
        type="text"
        onInput={handleInput}
        onKeyDown={handleKeyDown}
        placeholder="Search or type emoji…"
        className="w-full text-sm bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-blue-500 placeholder:text-gray-400 dark:placeholder:text-gray-500"
        aria-label="Type or paste an emoji"
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
      />
      <p className="mt-1.5 text-[10px] text-gray-400 dark:text-gray-500 text-center select-none">
        Tap an emoji or type to search • {EMOJI_HINT} for more
      </p>
    </div>
  );
}

interface EmojiReactionsProps {
  messageId: string | number;
  counts: ReactionCounts;
  userReactions: UserReactionState;
  aiReactions?: AIReactionState;
  disabled?: boolean;
  onReact: (
    messageId: string | number,
    emoji: string,
    toggledOn: boolean,
  ) => void | Promise<void>;
}

export function EmojiReactions({
  messageId,
  counts,
  userReactions,
  aiReactions,
  disabled,
  onReact,
}: EmojiReactionsProps) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerTriggerRef = useRef<HTMLButtonElement | null>(null);

  const handle = useCallback(
    (emoji: string) => {
      if (disabled) return;
      const current = !!userReactions[emoji];
      onReact(messageId, emoji, !current);
    },
    [disabled, messageId, onReact, userReactions],
  );

  const handlePick = useCallback(
    (emoji: string) => {
      if (disabled) return;
      onReact(messageId, emoji, true);
    },
    [disabled, messageId, onReact],
  );

  // Combine default reactions with any additional emojis that have been used
  const allReactions = new Map<
    string,
    {
      count: number;
      active: boolean;
      isAI: boolean;
      label: string;
      tooltip: string;
    }
  >();

  DEFAULT_REACTIONS.forEach((r) => {
    allReactions.set(r.emoji, {
      count: counts[r.emoji] || 0,
      active: !!userReactions[r.emoji],
      isAI: !!aiReactions?.[r.emoji],
      label: r.label,
      tooltip: r.tooltip,
    });
  });

  Object.entries(counts).forEach(([emoji, count]) => {
    if (!allReactions.has(emoji) && count > 0) {
      allReactions.set(emoji, {
        count,
        active: !!userReactions[emoji],
        isAI: !!aiReactions?.[emoji],
        label: emoji,
        tooltip: aiReactions?.[emoji]
          ? `AI reacted with ${emoji}`
          : `React with ${emoji}`,
      });
    }
  });

  return (
    <div
      className="flex flex-row flex-wrap gap-1 mt-2 -ml-1"
      role="group"
      aria-label="Reactions"
    >
      {Array.from(allReactions.entries()).map(([emoji, data]) => {
        const isDefault = DEFAULT_REACTIONS.some((r) => r.emoji === emoji);
        if (!isDefault && data.count === 0) return null;

        const aiStyle =
          data.isAI && data.count > 0
            ? "ring-1 ring-indigo-400/50 bg-gradient-to-br from-indigo-50 to-purple-50 dark:from-indigo-950/50 dark:to-purple-950/50"
            : "";

        return (
          <button
            key={emoji}
            type="button"
            title={data.isAI ? `AI: ${data.tooltip}` : data.tooltip}
            aria-label={data.label}
            aria-pressed={data.active}
            onClick={(e) => {
              e.stopPropagation();
              handle(emoji);
            }}
            className={`min-w-[44px] h-[36px] px-1 flex items-center justify-center rounded-md border text-sm transition-colors touch-manipulation focus:outline-none focus:ring-2 focus:ring-blue-500
              ${data.active ? "bg-blue-100 dark:bg-blue-900 border-blue-300 dark:border-blue-700" : "bg-white/60 dark:bg-gray-800/60 border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700"}
              ${aiStyle}
              disabled:opacity-50 disabled:cursor-not-allowed`}
            disabled={disabled}
          >
            {data.isAI && data.count > 0 && (
              <Sparkles
                className="w-2.5 h-2.5 text-indigo-400 mr-0.5 flex-shrink-0"
                aria-label="AI-generated reaction"
              />
            )}
            <span className="text-base leading-none select-none">{emoji}</span>
            {data.count > 0 && (
              <span className="ml-1 text-[11px] text-gray-600 dark:text-gray-300 select-none">
                {data.count}
              </span>
            )}
          </button>
        );
      })}

      {/* Add-any-emoji picker */}
      {!disabled && (
        <div className="relative">
          <button
            ref={pickerTriggerRef}
            type="button"
            title="Add reaction"
            aria-label="Add reaction"
            aria-expanded={pickerOpen}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              setPickerOpen((o) => !o);
            }}
            className="h-[36px] w-[36px] flex items-center justify-center rounded-md border border-dashed border-gray-300 dark:border-gray-600 text-gray-400 dark:text-gray-500 hover:border-gray-400 dark:hover:border-gray-500 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <Plus className="w-3.5 h-3.5" />
          </button>

          {pickerOpen && (
            <EmojiPickerInput
              onPick={handlePick}
              onClose={() => setPickerOpen(false)}
              anchorEl={pickerTriggerRef.current}
            />
          )}
        </div>
      )}
    </div>
  );
}

export function emptyReactionCounts(): ReactionCounts {
  return {};
}
