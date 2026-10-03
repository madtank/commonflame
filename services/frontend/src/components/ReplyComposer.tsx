import { useState, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";

interface ReplyComposerProps {
  parentId: number;
  parentUsername: string;
  onCancel: () => void;
  onSend: (content: string, parentId: number) => Promise<void> | void;
  autoMention?: boolean; // default true: prefill @username
  initialValue?: string; // optional manual override (wins over autoMention)
}

// Lightweight inline reply composer inserted under a message card.
export function ReplyComposer({
  parentId,
  parentUsername,
  onCancel,
  onSend,
  autoMention = true,
  initialValue,
}: ReplyComposerProps) {
  const prefill =
    initialValue !== undefined
      ? initialValue
      : autoMention
        ? `@${parentUsername} `
        : "";
  const [value, setValue] = useState(prefill);
  const [submitting, setSubmitting] = useState(false);
  const ref = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    ref.current?.focus();
    // Place cursor at end for mobile Safari reliability
    if (ref.current) {
      const el = ref.current;
      el.selectionStart = el.selectionEnd = el.value.length;
    }
  }, []);

  const submit = async () => {
    if (!value.trim() || submitting) return;
    setSubmitting(true);
    try {
      await onSend(value.trim(), parentId);
      setValue("");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="mt-2 rounded-md border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 p-2 space-y-2">
      <textarea
        ref={ref}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            onCancel();
          }
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={`Reply or react with emojis...`}
        className="w-full resize-none text-sm bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-500 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
        rows={2}
      />
      <div className="flex items-center justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onCancel}
          disabled={submitting}
        >
          Cancel
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={submit}
          disabled={!value.trim() || submitting}
          className="bg-blue-600 hover:bg-blue-700"
        >
          {submitting ? "Sending..." : "Send"}
        </Button>
      </div>
      <p className="text-[10px] text-gray-500 dark:text-gray-400">
        Press Enter to send • Esc to cancel
      </p>
    </div>
  );
}
