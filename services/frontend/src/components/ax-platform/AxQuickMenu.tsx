import { useCallback, useEffect, useRef, useState } from "react";
import { Settings, LogOut, FileText, Moon, Users } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { useUserSettings } from "@/hooks/useUserSettings";
import { getStoredThemeState, THEME_CHANGE_EVENT } from "@/lib/theme";

// Keep Auto summarize user-visible; only gate the redundant Summary cards toggle.
const showSummaryCardsToggle =
  import.meta.env.VITE_AX_SHOW_SUMMARY_CARDS === "true";

interface AxQuickMenuProps {
  username?: string | null;
  email?: string | null;
  spaceName: string;
  agentCount: number;
  cardsEnabled: boolean;
  onToggleCards: () => void;
  onOpenSettings: () => void;
  showSummaryCardsToggle?: boolean;
  onLogout?: () => void;
}

export function AxQuickMenu({
  username,
  email,
  spaceName,
  agentCount,
  cardsEnabled,
  onToggleCards,
  onOpenSettings,
  onLogout,
  showSummaryCardsToggle = false,
}: AxQuickMenuProps) {
  const [open, setOpen] = useState(false);
  const [isDarkMode, setIsDarkMode] = useState(
    () => getStoredThemeState().isDarkMode,
  );
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { settings, updateSettings } = useUserSettings();

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (
        menuRef.current &&
        !menuRef.current.contains(e.target as Node) &&
        triggerRef.current &&
        !triggerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  useEffect(() => {
    setIsDarkMode(getStoredThemeState().isDarkMode);
  }, [settings.theme]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;

    const handleThemeChange = (event: Event) => {
      const detail = (event as CustomEvent<{ isDarkMode?: boolean }>).detail;
      if (typeof detail?.isDarkMode === "boolean") {
        setIsDarkMode(detail.isDarkMode);
        return;
      }
      setIsDarkMode(getStoredThemeState().isDarkMode);
    };

    window.addEventListener(THEME_CHANGE_EVENT, handleThemeChange);

    return () => {
      window.removeEventListener(THEME_CHANGE_EVENT, handleThemeChange);
    };
  }, []);

  const handleToggleSummarize = useCallback(() => {
    updateSettings({ ai_auto_summarize: !settings.ai_auto_summarize });
  }, [settings.ai_auto_summarize, updateSettings]);

  const handleToggleTheme = useCallback(
    (checked: boolean) => {
      void updateSettings({ theme: checked ? "dark" : "light" });
    },
    [updateSettings],
  );

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid="ax-settings-button"
        className={cn(
          "inline-flex shrink-0 items-center gap-2 rounded-2xl border px-3 py-2 text-sm transition",
          isDarkMode
            ? "border-white/10 bg-white/[0.04] text-slate-200 hover:border-cyan-300/35 hover:text-white"
            : "border-slate-200 bg-white text-slate-700 shadow-sm hover:border-cyan-300/45 hover:text-slate-950",
        )}
        aria-label="Open menu"
        title="Settings"
        aria-expanded={open}
        aria-haspopup="true"
      >
        <Settings className="h-4 w-4" />
      </button>

      {open && (
        <div
          ref={menuRef}
          role="menu"
          className={cn(
            "absolute right-0 top-full z-50 mt-2 w-72 origin-top-right",
            "rounded-xl border border-slate-200 bg-white text-gray-900 shadow-xl backdrop-blur-lg dark:border-white/10 dark:bg-slate-800 dark:text-gray-100",
            "animate-in fade-in slide-in-from-top-2 duration-150",
          )}
        >
          {/* User info */}
          <div className="border-b border-slate-200 px-4 py-3 dark:border-white/10">
            <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
              {username || "User"}
            </div>
            {email && (
              <div className="mt-0.5 truncate text-xs text-gray-500 dark:text-slate-400">
                {email}
              </div>
            )}
          </div>

          {/* Space info */}
          <div className="border-b border-slate-200 px-4 py-2.5 dark:border-white/10">
            <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 dark:border-white/10 dark:bg-slate-900/60">
              <div className="flex min-w-0 items-center gap-2 text-xs font-medium text-slate-800 dark:text-slate-200">
                <Users className="h-3.5 w-3.5 shrink-0 text-slate-500 dark:text-slate-400" />
                <span className="truncate">{spaceName}</span>
              </div>
              <span className="shrink-0 text-xs font-medium text-slate-600 dark:text-slate-400">
                {agentCount} agent{agentCount !== 1 ? "s" : ""}
              </span>
            </div>
          </div>

          {/* Quick toggles */}
          <div className="space-y-1 px-2 py-2">
            {showSummaryCardsToggle && (
              <ToggleRow
                icon={<FileText className="h-3.5 w-3.5" />}
                label="Summary cards"
                checked={cardsEnabled}
                onChange={onToggleCards}
              />
            )}
            <ToggleRow
              icon={<FileText className="h-3.5 w-3.5" />}
              label="Auto summarize"
              checked={settings.ai_auto_summarize}
              onChange={handleToggleSummarize}
            />
            <ToggleRow
              icon={<Moon className="h-3.5 w-3.5" />}
              label="Dark mode"
              checked={isDarkMode}
              onChange={handleToggleTheme}
            />
          </div>

          {/* Footer actions */}
          <div className="border-t border-slate-200 px-2 py-2 dark:border-white/10">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onOpenSettings();
              }}
              className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-slate-700 transition hover:bg-slate-100 hover:text-slate-950 dark:text-gray-100 dark:hover:bg-white/[0.06] dark:hover:text-white"
            >
              <Settings className="h-3.5 w-3.5" />
              All settings…
            </button>
            {onLogout && (
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  onLogout();
                }}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-red-600 transition hover:bg-red-50 hover:text-red-700 dark:text-red-400 dark:hover:bg-red-500/10 dark:hover:text-red-300"
              >
                <LogOut className="h-3.5 w-3.5" />
                Sign out
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ToggleRow({
  icon,
  label,
  checked,
  onChange,
}: {
  icon: React.ReactNode;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div
      className="flex items-center justify-between rounded-lg px-2.5 py-2 transition hover:bg-slate-100 dark:hover:bg-white/[0.06]"
      role="menuitemcheckbox"
      aria-checked={checked}
    >
      <div className="flex items-center gap-2.5 text-sm text-gray-700 dark:text-gray-100">
        {icon}
        {label}
      </div>
      <Switch
        checked={checked}
        onCheckedChange={onChange}
        aria-label={`Toggle ${label}`}
      />
    </div>
  );
}
