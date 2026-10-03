import { Badge } from "@/components/ui/badge";
import {
  Flame,
  Zap,
  TrendingUp,
  Moon,
  Sparkles,
  Activity,
  BadgeCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface ActivityBadgeProps {
  state:
    | "hot"
    | "active"
    | "warming"
    | "quiet"
    | "fresh"
    | "ice"
    | "night"
    | "busy";
  score: number;
  agentCount?: number;
  className?: string;
}

export function ActivityBadge({
  state,
  score,
  className,
  ...props
}: ActivityBadgeProps) {
  const getStateConfig = () => {
    // Priority check: If agents are present in a low-activity state, upgrade the badge
    if (
      (state === "ice" || state === "quiet" || state === "fresh") &&
      (props.agentCount || 0) > 0
    ) {
      return {
        icon: <BadgeCheck className="w-3 h-3 mr-1" />,
        label: "Agents Ready",
        className:
          "bg-emerald-100 text-emerald-700 border-emerald-400 dark:bg-emerald-950/30 dark:text-emerald-400 dark:border-emerald-700",
      };
    }

    switch (state) {
      case "hot":
        return {
          icon: <Flame className="w-3 h-3 mr-1 animate-pulse" />,
          label: "Hot",
          className:
            "bg-gradient-to-r from-orange-100 to-red-100 text-red-700 border-red-400 dark:from-orange-950/30 dark:to-red-950/30 dark:text-red-400 dark:border-red-700",
        };
      case "active":
        return {
          icon: <Zap className="w-3 h-3 mr-1" />,
          label: "Active",
          className:
            "bg-gradient-to-r from-green-100 to-emerald-100 text-green-700 border-green-400 dark:from-green-950/30 dark:to-emerald-950/30 dark:text-green-400 dark:border-green-700",
        };
      case "warming":
        return {
          icon: <TrendingUp className="w-3 h-3 mr-1" />,
          label: "Warming",
          className:
            "bg-gradient-to-r from-yellow-100 to-amber-100 text-yellow-700 border-yellow-400 dark:from-yellow-950/30 dark:to-amber-950/30 dark:text-yellow-400 dark:border-yellow-700",
        };
      case "quiet":
        return {
          icon: <Moon className="w-3 h-3 mr-1 opacity-60" />,
          label: "Quiet",
          className:
            "bg-gray-100 text-gray-500 border-gray-300 dark:bg-slate-800/60 dark:text-slate-400 dark:border-slate-700",
        };
      case "fresh":
        return {
          icon: <Sparkles className="w-3 h-3 mr-1" />,
          label: "Fresh",
          className:
            "bg-gradient-to-r from-purple-100 to-blue-100 text-purple-700 border-purple-400 dark:from-purple-950/30 dark:to-blue-950/30 dark:text-purple-400 dark:border-purple-700",
        };
      case "ice":
        return {
          icon: <Activity className="w-3 h-3 mr-1 opacity-60" />,
          label: "Standby",
          className:
            "bg-gray-100 text-gray-600 border-gray-300 dark:bg-slate-800/60 dark:text-slate-400 dark:border-slate-700",
        };
      case "night":
        return {
          icon: <Moon className="w-3 h-3 mr-1" />,
          label: "Night",
          className:
            "bg-indigo-100 text-indigo-700 border-indigo-400 dark:bg-indigo-950/30 dark:text-indigo-400 dark:border-indigo-700",
        };
      case "busy":
        return {
          icon: <Activity className="w-3 h-3 mr-1 animate-pulse" />,
          label: "Busy",
          className:
            "bg-gradient-to-r from-red-100 to-orange-100 text-red-700 border-red-400 dark:from-red-950/30 dark:to-orange-950/30 dark:text-red-400 dark:border-red-700",
        };
      default:
        return {
          icon: <Activity className="w-3 h-3 mr-1" />,
          label: "Unknown",
          className:
            "bg-gray-100 text-gray-600 border-gray-300 dark:bg-gray-900/30 dark:text-gray-400 dark:border-gray-700",
        };
    }
  };

  const config = getStateConfig();

  return (
    <Badge
      variant="outline"
      className={cn(config.className, className)}
      title={`Activity score: ${score}/100`}
    >
      {config.icon}
      {config.label}
    </Badge>
  );
}
