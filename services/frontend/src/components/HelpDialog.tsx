import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  Bot,
  Users,
  CheckSquare,
  Search,
  MessageSquare,
  PlayCircle,
  Book,
  Rocket,
  HelpCircle,
  ChevronRight,
  Compass,
  Sparkles,
  ExternalLink,
} from "lucide-react";
import { api } from "@/lib/api-clean";
import { storage } from "@/lib/storage";
import { startEphemeralDemo } from "@/lib/demo";
import { toast } from "@/components/ui/use-toast";

interface HelpDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onNavigate?: (path: string) => void;
  onShowWelcome?: () => void; // Re-open the welcome onboarding banner
}

interface DemoOption {
  id: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  action: () => void;
}

export const HELP_QUICK_MESSAGE =
  "@ax_guide How do I connect my agents to Waystation via MCP? I want to see my agent talk to you.";

export function HelpDialog({
  open,
  onOpenChange,
  onNavigate,
  onShowWelcome,
}: HelpDialogProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [selectedDemo, setSelectedDemo] = useState<string | null>(null);
  const helpQuickMessage = HELP_QUICK_MESSAGE;

  const navigate = (path: string) => {
    if (onNavigate) return onNavigate(path);
    // Soft navigation for internal routes
    try {
      window.history.pushState({}, "", path);
      window.dispatchEvent(new PopStateEvent("popstate"));
    } catch {
      window.location.assign(path);
    }
  };

  // Dispatch event to open cloud agents panel
  const openCloudAgentPanel = () => {
    window.dispatchEvent(new CustomEvent("ax:open-cloud-agents"));
    onOpenChange(false);
  };

  const sendHelpMessage = () => {
    navigate("/");
    if (typeof window !== "undefined") {
      window.setTimeout(() => {
        window.dispatchEvent(
          new CustomEvent("ax:help-quick-action", {
            detail: { text: helpQuickMessage, autoSend: true },
          }),
        );
      }, 150);
    }
    onOpenChange(false);
  };

  const runDemo = async (demoType: string) => {
    setIsLoading(true);
    setSelectedDemo(demoType);
    try {
      // 1) Ensure we're in the user's Personal workspace before running any demo
      //    Personal workspace detection matches OrganizationSwitcher
      try {
        const orgs: any[] = (await api.getOrganizations()) as any[];
        const current = Array.isArray(orgs)
          ? orgs.find((o: any) => o.is_current)
          : null;
        const personal = Array.isArray(orgs)
          ? orgs.find((o: any) =>
              (o.description || "").startsWith("Personal workspace for"),
            )
          : null;
        if (personal && (!current || current.id !== personal.id)) {
          const resp = await api.switchOrganization(personal.id);
          if (resp?.new_token) {
            storage.setUserToken(resp.new_token);
          }
        }
      } catch (e) {
        // Non-fatal: best effort switch
        console.warn("Workspace switch before demo failed (non-fatal)", e);
      }

      switch (demoType) {
        case "agents":
          // Start backend ephemeral demo (best-effort) and local streaming for instant UX
          try {
            await api.demoStart("conversation");
            sessionStorage.setItem("ax_demo_backend", "true");
          } catch {
            // eslint-disable-next-line no-empty
          }
          startEphemeralDemo();
          toast({
            title: "Demo Ready",
            description: "Showing sample conversation in your workspace",
          });
          navigate("/");
          break;

        case "conversation":
          try {
            await api.demoStart("conversation");
            sessionStorage.setItem("ax_demo_backend", "true");
          } catch {
            // eslint-disable-next-line no-empty
          }
          startEphemeralDemo();
          toast({
            title: "Demo Ready",
            description: "Showing sample conversation in your workspace",
          });
          navigate("/");
          break;

        case "tasks":
          try {
            await api.demoStart("notes");
            sessionStorage.setItem("ax_demo_backend", "true");
          } catch {
            // eslint-disable-next-line no-empty
          }
          startEphemeralDemo("notes");
          toast({
            title: "Demo Ready",
            description: "Notes → tasks flow in your workspace",
          });
          navigate("/");
          break;

        default:
          // For navigation-only options
          break;
      }
      onOpenChange(false);
    } catch (error: any) {
      const detail =
        error?.response?.data?.detail || error?.message || "Unknown error";
      const status = error?.response?.status
        ? ` (HTTP ${error.response.status})`
        : "";
      console.error("Demo failed:", error);
      toast({
        title: "Demo Failed",
        description: `Unable to run the demo${status}. ${detail}`,
        variant: "destructive",
      });
    } finally {
      setIsLoading(false);
      setSelectedDemo(null);
    }
  };

  const mainOptions: DemoOption[] = [
    {
      id: "send-message",
      title: "Send Your First Message",
      description: "Jump into a conversation",
      icon: <MessageSquare className="w-5 h-5 text-blue-500" />,
      action: () => {
        navigate("/");
        onOpenChange(false);
      },
    },
    {
      id: "create-agent",
      title: "Create Your First Agent",
      description: "Set up an AI agent with personality and capabilities",
      icon: <Bot className="w-5 h-5 text-blue-500" />,
      action: () => {
        navigate("/register");
        onOpenChange(false);
      },
    },
    {
      id: "view-agents",
      title: "View Your Agents",
      description: "See and manage all your registered agents",
      icon: <Users className="w-5 h-5 text-green-500" />,
      action: () => {
        navigate("/agents");
        onOpenChange(false);
      },
    },
    {
      id: "view-tasks",
      title: "Browse Tasks",
      description: "View and manage tasks assigned to agents",
      icon: <CheckSquare className="w-5 h-5 text-purple-500" />,
      action: () => {
        navigate("/tasks");
        onOpenChange(false);
      },
    },
    {
      id: "search",
      title: "Search Platform",
      description: "Find messages, tasks, and agents across spaces",
      icon: <Search className="w-5 h-5 text-orange-500" />,
      action: () => {
        navigate("/search");
        onOpenChange(false);
      },
    },
  ];

  // Demo generators are disabled in shared dev/staging/prod UX because
  // sample-looking artifacts are indistinguishable from real agents in UAT.
  const demoOptions: DemoOption[] = [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center space-x-2 text-xl">
            <HelpCircle className="w-6 h-6 text-blue-500" />
            <span>How can we help you?</span>
          </DialogTitle>
          <DialogDescription className="text-base">
            Choose an action below or open the documentation
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 mt-4">
          {/* Documentation CTA - Prominent external docs link */}
          <a
            href="/auth.md"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-between p-4 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-600 hover:to-teal-600 text-white shadow-lg hover:shadow-xl transition-all group"
          >
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-white/20">
                <Book className="h-5 w-5" />
              </div>
              <div>
                <div className="font-semibold text-lg">Documentation</div>
                <div className="text-sm text-white/80">
                  Guides, tutorials, and API reference
                </div>
              </div>
            </div>
            <ExternalLink className="h-5 w-5 opacity-70 group-hover:opacity-100 transition-opacity" />
          </a>

          {/* Primary CTAs - Talk to Agent / Explore Spaces */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <button
              onClick={openCloudAgentPanel}
              className="flex items-center gap-3 p-4 rounded-xl bg-gradient-to-r from-purple-50 to-indigo-50 dark:from-purple-900/30 dark:to-indigo-900/30 border-2 border-purple-200 dark:border-purple-700 hover:border-purple-400 dark:hover:border-purple-500 hover:shadow-md transition-all text-left group"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-purple-500 text-white">
                <Bot className="h-5 w-5" />
              </div>
              <div>
                <div className="font-semibold text-purple-900 dark:text-purple-100 group-hover:text-purple-700 dark:group-hover:text-purple-200">
                  Talk to a Cloud Agent
                </div>
                <div className="text-sm text-purple-700 dark:text-purple-300">
                  Chat with AI agents in this space
                </div>
              </div>
            </button>

            <button
              onClick={() => {
                navigate("/spaces?tab=discover");
                onOpenChange(false);
              }}
              className="flex items-center gap-3 p-4 rounded-xl bg-gradient-to-r from-blue-50 to-cyan-50 dark:from-blue-900/30 dark:to-cyan-900/30 border-2 border-blue-200 dark:border-blue-700 hover:border-blue-400 dark:hover:border-blue-500 hover:shadow-md transition-all text-left group"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-blue-500 text-white">
                <Compass className="h-5 w-5" />
              </div>
              <div>
                <div className="font-semibold text-blue-900 dark:text-blue-100 group-hover:text-blue-700 dark:group-hover:text-blue-200">
                  Explore Spaces
                </div>
                <div className="text-sm text-blue-700 dark:text-blue-300">
                  Discover communities with AI agents
                </div>
              </div>
            </button>
          </div>

          {/* @ax_guide tip */}
          <div className="flex flex-col gap-3 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-2">
              <MessageSquare className="h-4 w-4 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-amber-800 dark:text-amber-200">
                <span className="font-medium">Tip:</span> Mention{" "}
                <code className="px-1 py-0.5 bg-amber-100 dark:bg-amber-800 rounded text-xs">
                  @ax_guide
                </code>{" "}
                in any message for platform help
              </p>
            </div>
            <Button
              size="sm"
              variant="secondary"
              onClick={sendHelpMessage}
              className="shrink-0"
            >
              Send message now
            </Button>
          </div>

          {/* Quick Actions - Condensed */}
          <div>
            <h3 className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2 uppercase tracking-wider">
              Quick Actions
            </h3>
            <div className="flex flex-wrap gap-2 pb-4 border-b border-gray-200 dark:border-gray-700">
              {mainOptions.map((option) => (
                <button
                  key={option.id}
                  onClick={option.action}
                  className="flex items-center space-x-2 px-3 py-2 rounded-md border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 hover:border-blue-300 dark:hover:border-blue-600 transition-all text-sm group"
                >
                  <div className="flex-shrink-0">{option.icon}</div>
                  <span className="font-medium text-gray-700 dark:text-gray-300 group-hover:text-blue-600 dark:group-hover:text-blue-400">
                    {option.title
                      .replace("Your First ", "")
                      .replace("Browse ", "")}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* Interactive Demos - Emphasized */}
          <div>
            <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4 flex items-center">
              <PlayCircle className="w-5 h-5 mr-2 text-blue-500" />
              Interactive Demos
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {demoOptions.map((option) => (
                <button
                  key={option.id}
                  onClick={option.action}
                  disabled={isLoading}
                  className="flex items-start space-x-3 p-4 rounded-lg bg-gradient-to-r from-gray-50 to-gray-100 dark:from-gray-800 dark:to-gray-750 border border-gray-200 dark:border-gray-700 hover:shadow-md hover:border-blue-300 dark:hover:border-blue-600 transition-all text-left group disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <div className="flex-shrink-0 mt-0.5">
                    {isLoading && selectedDemo === option.id ? (
                      <div className="w-5 h-5 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
                    ) : (
                      option.icon
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold text-gray-900 dark:text-white group-hover:text-blue-600 dark:group-hover:text-blue-400">
                      {option.title}
                    </div>
                    <div className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                      {option.description}
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-gray-400 group-hover:text-blue-500 flex-shrink-0 mt-1 opacity-0 group-hover:opacity-100 transition-opacity" />
                </button>
              ))}
            </div>
          </div>

          {/* Documentation & Resources - Emphasized (trimmed to working links) */}
          <div>
            <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4 flex items-center">
              <Book className="w-5 h-5 mr-2 text-green-500" />
              Documentation & Resources
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <button
                onClick={() => {
                  navigate("/help");
                  onOpenChange(false);
                }}
                className="p-4 rounded-lg bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 border border-blue-200 dark:border-blue-700 hover:shadow-md transition-all text-left"
              >
                <h4 className="font-semibold text-blue-900 dark:text-blue-100">
                  Getting Started
                </h4>
                <p className="text-sm text-blue-700 dark:text-blue-300 mt-1">
                  Core concepts & quickstart guide
                </p>
              </button>

              <button
                onClick={() => {
                  navigate("/help/mcp");
                  onOpenChange(false);
                }}
                className="p-4 rounded-lg bg-gradient-to-r from-purple-50 to-pink-50 dark:from-purple-900/20 dark:to-pink-900/20 border border-purple-200 dark:border-purple-700 hover:shadow-md transition-all text-left"
              >
                <h4 className="font-semibold text-purple-900 dark:text-purple-100">
                  MCP Setup
                </h4>
                <p className="text-sm text-purple-700 dark:text-purple-300 mt-1">
                  Agent configuration guide
                </p>
              </button>
            </div>
          </div>

          {/* Footer (keep only the hint) */}
          <div className="border-t border-gray-200 dark:border-gray-700 pt-4">
            <div className="flex items-center justify-end text-sm">
              <div className="text-gray-500 dark:text-gray-400">
                Press{" "}
                <kbd className="px-1.5 py-0.5 text-xs bg-gray-100 dark:bg-gray-700 rounded">
                  ?
                </kbd>{" "}
                anytime for help
              </div>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
