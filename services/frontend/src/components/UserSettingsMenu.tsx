import { useState, useEffect } from "react";
import {
  Settings,
  LogOut,
  Beaker,
  Radio,
  Flag,
  Sparkles,
  Sun,
  Moon,
} from "lucide-react";
import { useFeatureFlags } from "@/hooks/useFeatureFlags";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { storage } from "@/lib/storage";
import { updateUserFeatureFlags } from "@/lib/api-clean";
import { resolveDarkMode, THEME_TOGGLE_ENABLED } from "@/lib/theme";
import { useUserSettings } from "@/hooks/useUserSettings";
import type { UserFeatureFlags } from "@/types";

interface UserSettingsMenuProps {
  onLogout: () => void;
  isAdmin?: boolean;
  onToggleSseDebug?: () => void;
  sseDebugOpen?: boolean;
  onOpenSettings?: () => void;
}

export function UserSettingsMenu({
  onLogout,
  isAdmin,
  onToggleSseDebug,
  sseDebugOpen,
  onOpenSettings,
}: UserSettingsMenuProps) {
  const [v2Enabled, setV2Enabled] = useState(false);
  const [isUpdating, setIsUpdating] = useState(false);
  const [canAccessBeta, setCanAccessBeta] = useState(false);
  const {
    flags: serverFlags,
    loaded: flagsLoaded,
    setFlag,
  } = useFeatureFlags();
  const {
    settings,
    saving: settingsSaving,
    updateSettings,
  } = useUserSettings();
  const [togglingFlag, setTogglingFlag] = useState<string | null>(null);

  // Load initial state from user metadata
  useEffect(() => {
    const metadata = storage.getUserMetadata?.();
    const flags: UserFeatureFlags | undefined = metadata?.feature_flags;
    setV2Enabled(flags?.v2_enabled ?? false);
    setCanAccessBeta(flags?.can_access_beta_features ?? false);
  }, []);

  const handleV2Toggle = async () => {
    const newValue = !v2Enabled;
    setIsUpdating(true);

    try {
      // Update on backend
      await updateUserFeatureFlags({ v2_enabled: newValue });

      // Update local state and storage
      setV2Enabled(newValue);
      const metadata = storage.getUserMetadata?.() ?? {};
      storage.setUserMetadata({
        ...metadata,
        feature_flags: {
          ...metadata.feature_flags,
          v2_enabled: newValue,
        },
      });
    } catch (error) {
      console.error("Failed to update v2 flag:", error);
      // Revert on error
      setV2Enabled(!newValue);
    } finally {
      setIsUpdating(false);
    }
  };

  const isDarkTheme = resolveDarkMode(settings.theme);

  const handleThemeToggle = async () => {
    try {
      await updateSettings({ theme: isDarkTheme ? "light" : "dark" });
    } catch (error) {
      console.error("Failed to update theme:", error);
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="p-2"
          aria-label="User settings"
        >
          <Settings className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        {onOpenSettings && isAdmin && (
          <>
            <DropdownMenuItem
              onClick={onOpenSettings}
              className="cursor-pointer"
            >
              <Settings className="h-4 w-4 mr-2" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuSeparator />
          </>
        )}
        {THEME_TOGGLE_ENABLED && (
          <>
            <DropdownMenuItem
              onClick={handleThemeToggle}
              disabled={settingsSaving}
              className="flex items-center justify-between cursor-pointer"
            >
              <div className="flex items-center gap-2">
                {isDarkTheme ? (
                  <Moon className="h-4 w-4 text-indigo-500" />
                ) : (
                  <Sun className="h-4 w-4 text-amber-500" />
                )}
                <div className="flex flex-col">
                  <span className="text-sm">Dark mode</span>
                  <span className="text-xs text-muted-foreground">
                    {isDarkTheme
                      ? "Switch to light theme"
                      : "Switch to dark theme"}
                  </span>
                </div>
              </div>
              <div
                className={`w-8 h-4 rounded-full transition-colors ${
                  isDarkTheme ? "bg-indigo-500" : "bg-amber-400"
                }`}
              >
                <div
                  className={`w-3 h-3 mt-0.5 rounded-full bg-white shadow transition-transform ${
                    isDarkTheme ? "translate-x-4" : "translate-x-0.5"
                  }`}
                />
              </div>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
          </>
        )}

        {/* V2 Toggle - only shown if user has beta access */}
        {canAccessBeta && (
          <>
            <DropdownMenuItem
              onClick={handleV2Toggle}
              disabled={isUpdating}
              className="flex items-center justify-between cursor-pointer"
            >
              <div className="flex items-center gap-2">
                <Beaker className="h-4 w-4 text-purple-500" />
                <div className="flex flex-col">
                  <span className="text-sm">Agent v2 Engine</span>
                  <span className="text-xs text-muted-foreground">
                    Experimental MCP runner
                  </span>
                </div>
              </div>
              <div
                className={`w-8 h-4 rounded-full transition-colors ${
                  v2Enabled ? "bg-purple-500" : "bg-gray-300 dark:bg-gray-600"
                }`}
              >
                <div
                  className={`w-3 h-3 mt-0.5 rounded-full bg-white shadow transition-transform ${
                    v2Enabled ? "translate-x-4" : "translate-x-0.5"
                  }`}
                />
              </div>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
          </>
        )}

        {/* SSE Debug - only shown for admins */}
        {isAdmin && onToggleSseDebug && (
          <>
            <DropdownMenuItem
              onClick={onToggleSseDebug}
              className="flex items-center justify-between cursor-pointer"
            >
              <div className="flex items-center gap-2">
                <Radio className="h-4 w-4 text-green-500" />
                <div className="flex flex-col">
                  <span className="text-sm">SSE Debug</span>
                  <span className="text-xs text-muted-foreground">
                    View real-time events
                  </span>
                </div>
              </div>
              <div
                className={`w-8 h-4 rounded-full transition-colors ${
                  sseDebugOpen ? "bg-green-500" : "bg-gray-300 dark:bg-gray-600"
                }`}
              >
                <div
                  className={`w-3 h-3 mt-0.5 rounded-full bg-white shadow transition-transform ${
                    sseDebugOpen ? "translate-x-4" : "translate-x-0.5"
                  }`}
                />
              </div>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
          </>
        )}

        {/* Server-side Feature Flags */}
        {flagsLoaded && Object.keys(serverFlags).length > 0 && (
          <>
            <div className="px-2 py-1.5">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground mb-1">
                <Flag className="h-3 w-3" />
                Feature Flags
              </div>
              {Object.entries(serverFlags).map(([name, enabled]) => (
                <DropdownMenuItem
                  key={name}
                  onClick={async (e) => {
                    e.preventDefault();
                    setTogglingFlag(name);
                    await setFlag(name, !enabled);
                    setTogglingFlag(null);
                  }}
                  disabled={togglingFlag === name}
                  className="flex items-center justify-between cursor-pointer py-1"
                >
                  <span className="text-sm font-mono truncate max-w-[140px]">
                    {name.replace(/^ax_/, "").replace(/_/g, " ")}
                  </span>
                  <div
                    className={`w-8 h-4 rounded-full transition-colors flex-shrink-0 ${
                      enabled ? "bg-blue-500" : "bg-gray-300 dark:bg-gray-600"
                    }`}
                  >
                    <div
                      className={`w-3 h-3 mt-0.5 rounded-full bg-white shadow transition-transform ${
                        enabled ? "translate-x-4" : "translate-x-0.5"
                      }`}
                    />
                  </div>
                </DropdownMenuItem>
              ))}
            </div>
            <DropdownMenuSeparator />
          </>
        )}

        <DropdownMenuItem
          onClick={() => {
            localStorage.setItem("ax_ui_mode", "modern");
            window.location.href = "/";
          }}
          className="cursor-pointer"
        >
          <Sparkles className="h-4 w-4 mr-2 text-cyan-500" />
          <div className="flex flex-col">
            <span className="text-sm">Switch to new UI</span>
            <span className="text-xs text-muted-foreground">
              Try the new Waystation experience
            </span>
          </div>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={onLogout}
          className="text-red-600 dark:text-red-400 cursor-pointer"
        >
          <LogOut className="h-4 w-4 mr-2" />
          Logout
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
