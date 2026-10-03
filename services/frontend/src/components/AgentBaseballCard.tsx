import React from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Bot,
  MessageSquare,
  CheckCircle,
  Clock,
  TrendingUp,
  Settings,
  Calendar,
  Zap,
} from "lucide-react";
import { humanizeAgentType } from "@/lib/display-utils";

interface Agent {
  id: string;
  name: string;
  agent_type: string;
  description?: string;
  status: "active" | "busy" | "idle" | "offline";
  created_at?: string;
}

interface AgentStats {
  totalMessages: number;
  tasksCompleted: number;
  tasksInProgress: number;
  averageResponseTime: string;
  successRate: number;
  daysActive: number;
  lastActive: string;
}

interface AgentBaseballCardProps {
  agent: Agent;
  stats: AgentStats;
  onClick?: () => void;
  onGetConfig?: (agentId: string) => void;
}

const statusColors: Record<Agent["status"], string> = {
  active: "bg-green-500",
  busy: "bg-yellow-500",
  idle: "bg-gray-400",
  offline: "bg-red-500",
};

const statusLabels: Record<Agent["status"], string> = {
  active: "Active",
  busy: "Busy",
  idle: "Idle",
  offline: "Offline",
};

/**
 * AgentBaseballCard - A baseball card style display for agent stats
 * Displays agent info and performance metrics in a collectible card format
 */
export const AgentBaseballCard: React.FC<AgentBaseballCardProps> = ({
  agent,
  stats,
  onClick,
  onGetConfig,
}) => {
  const handleConfigClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onGetConfig?.(agent.id);
  };

  return (
    <Card
      className="relative overflow-hidden cursor-pointer transition-all duration-200 hover:shadow-lg hover:scale-[1.02] bg-gradient-to-br from-white to-gray-50 dark:from-gray-800 dark:to-gray-900 border-2 border-gray-200 dark:border-gray-700"
      onClick={onClick}
      role="article"
      aria-label={`Agent card for ${agent.name}`}
    >
      {/* Status indicator bar */}
      <div
        className={`absolute top-0 left-0 right-0 h-1 ${statusColors[agent.status]}`}
      />

      <CardHeader className="pb-2 pt-4">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            {/* Agent Avatar */}
            <div className="w-12 h-12 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center shadow-md">
              <Bot className="w-7 h-7 text-white" />
            </div>
            <div>
              <CardTitle className="text-lg font-bold text-gray-900 dark:text-white">
                {agent.name}
              </CardTitle>
              <div className="flex items-center gap-2 mt-1">
                <Badge variant="secondary" className="text-xs">
                  {humanizeAgentType(agent.agent_type)}
                </Badge>
                <span
                  className={`inline-flex items-center gap-1 text-xs ${
                    agent.status === "active"
                      ? "text-green-600"
                      : agent.status === "busy"
                        ? "text-yellow-600"
                        : "text-gray-500"
                  }`}
                >
                  <span
                    className={`w-2 h-2 rounded-full ${statusColors[agent.status]}`}
                  />
                  {statusLabels[agent.status]}
                </span>
              </div>
            </div>
          </div>

          {/* Config button */}
          {onGetConfig && (
            <button
              onClick={handleConfigClick}
              className="p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
              aria-label={`Get config for ${agent.name}`}
            >
              <Settings className="w-4 h-4 text-gray-500" />
            </button>
          )}
        </div>

        {agent.description && (
          <p className="text-sm text-gray-600 dark:text-gray-400 mt-2 line-clamp-2">
            {agent.description}
          </p>
        )}
      </CardHeader>

      <CardContent className="pt-2">
        {/* Stats Grid */}
        <div className="grid grid-cols-2 gap-3 mt-2">
          {/* Messages */}
          <div className="flex items-center gap-2 p-2 rounded-lg bg-blue-50 dark:bg-blue-900/20">
            <MessageSquare className="w-4 h-4 text-blue-600 dark:text-blue-400" />
            <div>
              <div className="text-lg font-bold text-blue-600 dark:text-blue-400">
                {stats.totalMessages.toLocaleString()}
              </div>
              <div className="text-xs text-gray-500">Messages</div>
            </div>
          </div>

          {/* Tasks Completed */}
          <div className="flex items-center gap-2 p-2 rounded-lg bg-green-50 dark:bg-green-900/20">
            <CheckCircle className="w-4 h-4 text-green-600 dark:text-green-400" />
            <div>
              <div className="text-lg font-bold text-green-600 dark:text-green-400">
                {stats.tasksCompleted.toLocaleString()}
              </div>
              <div className="text-xs text-gray-500">Completed</div>
            </div>
          </div>

          {/* In Progress */}
          <div className="flex items-center gap-2 p-2 rounded-lg bg-orange-50 dark:bg-orange-900/20">
            <Clock className="w-4 h-4 text-orange-600 dark:text-orange-400" />
            <div>
              <div className="text-lg font-bold text-orange-600 dark:text-orange-400">
                {stats.tasksInProgress}
              </div>
              <div className="text-xs text-gray-500">In Progress</div>
            </div>
          </div>

          {/* Success Rate */}
          <div className="flex items-center gap-2 p-2 rounded-lg bg-purple-50 dark:bg-purple-900/20">
            <TrendingUp className="w-4 h-4 text-purple-600 dark:text-purple-400" />
            <div>
              <div className="text-lg font-bold text-purple-600 dark:text-purple-400">
                {stats.successRate}%
              </div>
              <div className="text-xs text-gray-500">Success</div>
            </div>
          </div>
        </div>

        {/* Footer stats */}
        <div className="flex items-center justify-between mt-4 pt-3 border-t border-gray-200 dark:border-gray-700 text-xs text-gray-500">
          <div className="flex items-center gap-1">
            <Zap className="w-3 h-3" />
            <span>Avg: {stats.averageResponseTime}</span>
          </div>
          <div className="flex items-center gap-1">
            <Calendar className="w-3 h-3" />
            <span>{stats.daysActive} days active</span>
          </div>
        </div>

        <div className="text-xs text-gray-400 text-right mt-1">
          Last active: {stats.lastActive}
        </div>
      </CardContent>
    </Card>
  );
};

export default AgentBaseballCard;
