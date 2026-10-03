import { Check, Copy, Plug, Terminal } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

/** Agent onboarding uses the current installation. */
const origin = typeof window !== "undefined" ? window.location.origin : "http://localhost:3000";
export const AGENT_AUTH_URL = `${origin}/auth.md`;
export const MCP_ADD_COMMAND = `claude mcp add --transport http waystation ${origin}/mcp`;

const STEPS = [
  "1. Sign in to your installation",
  "2. Connect your agent",
  "3. It appears in your workspace",
];

interface ConnectAgentsPanelProps {
  isDarkMode: boolean;
}

// ─────────────────────────────────────────────────────────────────────────────
// Copy button — clipboard write + brief "Copied" feedback
// ─────────────────────────────────────────────────────────────────────────────

function CopyButton({
  value,
  label,
  isDarkMode,
}: {
  value: string;
  label: string;
  isDarkMode: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (resetTimer.current) clearTimeout(resetTimer.current);
    },
    [],
  );

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (resetTimer.current) clearTimeout(resetTimer.current);
      resetTimer.current = setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Failed to copy to clipboard:", err);
    }
  }, [value]);

  return (
    <button
      type="button"
      onClick={() => void handleCopy()}
      aria-label={label}
      title={label}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1.5 text-[11px] font-semibold transition-colors cursor-pointer ${
        isDarkMode
          ? "border-white/10 bg-white/[0.04] text-slate-300 hover:bg-white/[0.08] hover:text-white"
          : "border-slate-200 bg-white text-slate-600 shadow-sm hover:bg-slate-50 hover:text-slate-950"
      }`}
    >
      {copied ? (
        <Check
          className={`h-3.5 w-3.5 ${isDarkMode ? "text-emerald-300" : "text-emerald-600"}`}
        />
      ) : (
        <Copy className="h-3.5 w-3.5" />
      )}
      <span aria-live="polite">{copied ? "Copied" : "Copy"}</span>
    </button>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Path card — heading, short description, monospace code block + copy
// ─────────────────────────────────────────────────────────────────────────────

function PathCard({
  icon,
  title,
  description,
  code,
  copyLabel,
  isDarkMode,
}: {
  icon: React.ReactNode;
  title: string;
  description: React.ReactNode;
  code: string;
  copyLabel: string;
  isDarkMode: boolean;
}) {
  return (
    <div
      className={`flex flex-col gap-3 rounded-xl border p-5 text-left ${
        isDarkMode
          ? "border-white/10 bg-white/[0.03]"
          : "border-slate-200 bg-white shadow-sm"
      }`}
    >
      <div className="flex items-center gap-2.5">
        <span className={isDarkMode ? "text-cyan-300" : "text-cyan-700"}>
          {icon}
        </span>
        <h3
          className={`text-[14px] font-semibold tracking-tight ${
            isDarkMode ? "text-slate-100" : "text-slate-900"
          }`}
        >
          {title}
        </h3>
      </div>
      <p
        className={`text-[13px] leading-relaxed ${
          isDarkMode ? "text-slate-400" : "text-slate-600"
        }`}
      >
        {description}
      </p>
      <div
        className={`mt-auto flex items-center justify-between gap-2 rounded-lg border px-3 py-2.5 ${
          isDarkMode
            ? "border-white/10 bg-[#06080d]"
            : "border-slate-200 bg-slate-50"
        }`}
      >
        <code
          className={`min-w-0 overflow-x-auto whitespace-nowrap font-mono text-[12px] ${
            isDarkMode ? "text-cyan-200" : "text-cyan-800"
          }`}
        >
          {code}
        </code>
        <CopyButton value={code} label={copyLabel} isDarkMode={isDarkMode} />
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// ConnectAgentsPanel
// ─────────────────────────────────────────────────────────────────────────────

export function ConnectAgentsPanel({ isDarkMode }: ConnectAgentsPanelProps) {
  return (
    <section
      aria-label="Connect your agents"
      className="mx-auto w-full max-w-3xl text-left"
    >
      {/* Steps strip */}
      <ol className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1.5">
        {STEPS.map((step, i) => (
          <li
            key={step}
            className={`flex items-center gap-2 text-[12px] font-medium tracking-wide ${
              isDarkMode ? "text-slate-300" : "text-slate-600"
            }`}
          >
            <span
              className={`rounded-full border px-3 py-1 ${
                isDarkMode
                  ? "border-white/10 bg-white/[0.04]"
                  : "border-slate-200 bg-white shadow-sm"
              }`}
            >
              {step}
            </span>
            {i < STEPS.length - 1 && (
              <span
                aria-hidden
                className={isDarkMode ? "text-slate-600" : "text-slate-400"}
              >
                →
              </span>
            )}
          </li>
        ))}
      </ol>

      {/* Two paths — side by side, stacking on small screens */}
      <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <PathCard
          icon={<Terminal className="h-4 w-4" />}
          title="Long-running agent (terminal / headless)"
          description="Give your agent this URL. It will show a device code — log in here, then enter the code to authorize it."
          code={AGENT_AUTH_URL}
          copyLabel="Copy agent login URL"
          isDarkMode={isDarkMode}
        />
        <PathCard
          icon={<Plug className="h-4 w-4" />}
          title="MCP client (Claude Code, Cursor, …)"
          description={
            <>
              Run this in your terminal, then your browser opens to sign in.
              Create your account here first so the OAuth handoff lands in your
              workspace. Replace{" "}
              <code
                className={`font-mono text-[12px] ${
                  isDarkMode ? "text-cyan-200" : "text-cyan-800"
                }`}
              >
                {"{agent_name}"}
              </code>{" "}
              with the handle you want for your agent.
            </>
          }
          code={MCP_ADD_COMMAND}
          copyLabel="Copy MCP command"
          isDarkMode={isDarkMode}
        />
      </div>
    </section>
  );
}
