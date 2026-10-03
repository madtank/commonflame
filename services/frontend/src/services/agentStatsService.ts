/**
 * Agent Statistics Service
 * Fetches agent activity data from the Waystation Marketplace API
 */
import { config } from "../config/environment";
import { TASK_ROUTES } from "../lib/api-routes";

export interface AgentStats {
  totalMessages: number;
  tasksCompleted: number;
  tasksInProgress: number;
  averageResponseTime: string;
  successRate: number;
  daysActive: number;
  lastActive: string;
}

interface MessageResponse {
  messages: Array<{
    id: string;
    content: string;
    created_at: string;
    author: {
      id: string;
      name: string;
      type: "agent" | "user";
    };
  }>;
  total: number;
}

interface TaskResponse {
  tasks: Array<{
    id: string;
    title: string;
    status: string;
    created_at: string;
    completed_at?: string;
    assigned_agent?: {
      id: string;
      name: string;
    };
    posted_by?: {
      id: string;
      name: string;
      type: "agent" | "user";
    };
  }>;
  total: number;
}

class AgentStatsService {
  private baseURL: string;
  private token: string;

  constructor() {
    // Use environment configuration from config
    this.baseURL = config.apiUrl;
    this.token = this.getAuthToken();
  }

  private getAuthToken(): string {
    // Get token from localStorage, cookies, or your auth context
    if (typeof window !== "undefined") {
      return localStorage.getItem("auth_token") || "";
    }
    return "";
  }

  private async fetchWithAuth(url: string): Promise<Response> {
    const response = await fetch(`${this.baseURL}${url}`, {
      headers: {
        Authorization: `Bearer ${this.token}`,
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      throw new Error(
        `API request failed: ${response.status} ${response.statusText}`,
      );
    }

    return response;
  }

  /**
   * Get agent messages count by filtering messages where author matches
   */
  async getAgentMessages(
    agentName: string,
  ): Promise<{ total: number; messages: any[] }> {
    try {
      // Use the same endpoint as the main app - use 100 limit to prevent 429
      const response = await this.fetchWithAuth("/auth/messages?limit=100");
      const data = await response.json();

      console.log(`🔍 [Agent Stats] Raw messages response for ${agentName}:`, {
        dataKeys: Object.keys(data),
        postsCount: data.posts?.length || 0,
        samplePost: data.posts?.[0],
      });

      // The API returns { posts: [...] } format
      const allMessages = data.posts || [];

      // Filter messages by agent name - check multiple possible username fields
      const agentMessages = allMessages.filter((message: any) => {
        const messageAuthor =
          message.username || message.author || message.agent_username || "";
        const matches = messageAuthor.toLowerCase() === agentName.toLowerCase();

        if (matches) {
          console.log(`✅ [Agent Stats] Found message from ${agentName}:`, {
            id: message.id,
            content: message.content?.substring(0, 50) + "...",
            username: message.username,
            author: message.author,
            agent_username: message.agent_username,
          });
        }

        return matches;
      });

      console.log(
        `📊 [Agent Stats] Found ${agentMessages.length} messages for agent ${agentName} out of ${allMessages.length} total`,
      );

      return {
        total: agentMessages.length,
        messages: agentMessages,
      };
    } catch (error) {
      console.error(
        `❌ [Agent Stats] Error fetching messages for agent ${agentName}:`,
        error,
      );
      return { total: 0, messages: [] };
    }
  }

  /**
   * Get agent completed tasks by filtering tasks where assigned_agent matches
   */
  async getAgentCompletedTasks(
    agentId: string,
    agentName: string,
  ): Promise<{ completed: number; inProgress: number; tasks: any[] }> {
    try {
      // Use the same endpoint as the main app
      const response = await this.fetchWithAuth(
        `${TASK_ROUTES.collection}?limit=500`,
      );
      const data = await response.json();

      console.log(`🔍 [Agent Stats] Raw tasks response for ${agentName}:`, {
        dataKeys: Object.keys(data),
        tasksCount: data.tasks?.length || 0,
        sampleTask: data.tasks?.[0],
      });

      // The API returns { tasks: [...] } format
      const allTasks = data.tasks || [];

      // Filter tasks by agent - check multiple possible assignment fields
      const agentTasks = allTasks.filter((task: any) => {
        // Check various ways the agent might be associated with the task
        const assignedTo =
          task.assigned_to || task.claimed_by || task.assigned_agent_id || "";
        const assignedToName = task.assigned_agent_name || "";
        const createdBy =
          task.created_by || task.posted_by?.username || task.posted_by || "";

        const isAssignedById = assignedTo === agentId;
        const isAssignedByName =
          assignedToName.toLowerCase() === agentName.toLowerCase();
        const isCreatedByName =
          createdBy.toLowerCase() === agentName.toLowerCase();

        const matches = isAssignedById || isAssignedByName || isCreatedByName;

        if (matches) {
          console.log(`✅ [Agent Stats] Found task for ${agentName}:`, {
            id: task.id,
            title: task.title?.substring(0, 50) + "...",
            status: task.status,
            assigned_to: assignedTo,
            assigned_agent_name: assignedToName,
            created_by: createdBy,
            matchReason: isAssignedById
              ? "assignedById"
              : isAssignedByName
                ? "assignedByName"
                : "createdByName",
          });
        }

        return matches;
      });

      const completedTasks = agentTasks.filter(
        (task) => task.status === "completed",
      );
      const inProgressTasks = agentTasks.filter((task) =>
        ["in_progress", "assigned", "claimed", "active"].includes(task.status),
      );

      console.log(
        `📊 [Agent Stats] Found ${agentTasks.length} total tasks for agent ${agentName}: ${completedTasks.length} completed, ${inProgressTasks.length} in progress`,
      );

      return {
        completed: completedTasks.length,
        inProgress: inProgressTasks.length,
        tasks: agentTasks,
      };
    } catch (error) {
      console.error(
        `❌ [Agent Stats] Error fetching tasks for agent ${agentName}:`,
        error,
      );
      return { completed: 0, inProgress: 0, tasks: [] };
    }
  }

  /**
   * Calculate additional stats from messages and tasks
   */
  private calculateAdditionalStats(
    messages: any[],
    tasks: any[],
    agentCreatedAt?: string,
  ): {
    averageResponseTime: string;
    successRate: number;
    daysActive: number;
    lastActive: string;
  } {
    // Calculate days active since agent creation
    const createdDate = agentCreatedAt ? new Date(agentCreatedAt) : new Date();
    const now = new Date();
    const daysActive = Math.floor(
      (now.getTime() - createdDate.getTime()) / (1000 * 60 * 60 * 24),
    );

    // Find last activity from messages or tasks
    const allDates = [
      ...messages.map((m) => new Date(m.created_at)),
      ...tasks.map((t) => new Date(t.created_at)),
    ];
    const lastActiveDate =
      allDates.length > 0
        ? new Date(Math.max(...allDates.map((d) => d.getTime())))
        : createdDate;

    // Format last active
    const lastActive = this.formatRelativeTime(lastActiveDate);

    // Calculate success rate (completed / total tasks, default to 95% if no tasks)
    const totalTasks = tasks.length;
    const completedTasks = tasks.filter((t) => t.status === "completed").length;
    const successRate =
      totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 95;

    // Simple average response time calculation (you can enhance this)
    const averageResponseTime =
      messages.length > 20
        ? "< 2min"
        : messages.length > 10
          ? "< 5min"
          : messages.length > 0
            ? "< 10min"
            : "N/A";

    return {
      averageResponseTime,
      successRate,
      daysActive: Math.max(1, daysActive), // At least 1 day
      lastActive,
    };
  }

  /**
   * Format relative time (e.g., "2 hours ago", "3 days ago")
   */
  private formatRelativeTime(date: Date): string {
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / (1000 * 60));
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffMins < 60) return `${diffMins} mins ago`;
    if (diffHours < 24) return `${diffHours} hours ago`;
    if (diffDays < 30) return `${diffDays} days ago`;
    return date.toLocaleDateString();
  }

  /**
   * Get comprehensive stats for an agent
   */
  async getAgentStats(
    agentId: string,
    agentName: string,
    agentCreatedAt?: string,
  ): Promise<AgentStats> {
    try {
      // Fetch messages and tasks in parallel
      const [messagesResult, tasksResult] = await Promise.all([
        this.getAgentMessages(agentName),
        this.getAgentCompletedTasks(agentId, agentName),
      ]);

      // Calculate additional stats
      const additionalStats = this.calculateAdditionalStats(
        messagesResult.messages,
        tasksResult.tasks,
        agentCreatedAt,
      );

      return {
        totalMessages: messagesResult.total,
        tasksCompleted: tasksResult.completed,
        tasksInProgress: tasksResult.inProgress,
        ...additionalStats,
      };
    } catch (error) {
      console.error(`Error getting stats for agent ${agentName}:`, error);
      // Return default stats on error
      return {
        totalMessages: 0,
        tasksCompleted: 0,
        tasksInProgress: 0,
        averageResponseTime: "N/A",
        successRate: 0,
        daysActive: 1,
        lastActive: "Unknown",
      };
    }
  }

  /**
   * Batch fetch stats for multiple agents (more efficient)
   */
  async getBatchAgentStats(
    agents: Array<{ id: string; name: string; created_at?: string }>,
  ): Promise<Record<string, AgentStats>> {
    try {
      console.log(
        `🔄 [Agent Stats] Fetching batch stats for ${agents.length} agents:`,
        agents.map((a) => a.name),
      );

      // Fetch all messages and tasks once using the correct endpoints
      const [messagesResponse, tasksResponse] = await Promise.all([
        this.fetchWithAuth("/auth/messages?limit=500"),
        this.fetchWithAuth(`${TASK_ROUTES.collection}?limit=500`),
      ]);

      const messagesData = await messagesResponse.json();
      const tasksData = await tasksResponse.json();

      console.log(
        `📊 [Agent Stats] Loaded ${messagesData.posts?.length || 0} messages and ${tasksData.tasks?.length || 0} tasks`,
      );

      // Process stats for each agent
      const statsMap: Record<string, AgentStats> = {};

      for (const agent of agents) {
        console.log(
          `🔍 [Agent Stats] Processing agent: ${agent.name} (${agent.id})`,
        );

        // Filter messages for this agent
        const agentMessages = (messagesData.posts || []).filter(
          (message: any) => {
            const messageAuthor =
              message.username ||
              message.author ||
              message.agent_username ||
              "";
            return messageAuthor.toLowerCase() === agent.name.toLowerCase();
          },
        );

        // Filter tasks for this agent
        const agentTasks = (tasksData.tasks || []).filter((task: any) => {
          const assignedTo =
            task.assigned_to || task.claimed_by || task.assigned_agent_id || "";
          const assignedToName = task.assigned_agent_name || "";
          const createdBy =
            task.created_by || task.posted_by?.username || task.posted_by || "";

          return (
            assignedTo === agent.id ||
            assignedToName.toLowerCase() === agent.name.toLowerCase() ||
            createdBy.toLowerCase() === agent.name.toLowerCase()
          );
        });

        const completedTasks = agentTasks.filter(
          (task) => task.status === "completed",
        );
        const inProgressTasks = agentTasks.filter((task) =>
          ["in_progress", "assigned", "claimed", "active"].includes(
            task.status,
          ),
        );

        // Calculate additional stats
        const additionalStats = this.calculateAdditionalStats(
          agentMessages,
          agentTasks,
          agent.created_at,
        );

        console.log(
          `📊 [Agent Stats] ${agent.name}: ${agentMessages.length} messages, ${completedTasks.length} completed, ${inProgressTasks.length} in progress`,
        );

        statsMap[agent.id] = {
          totalMessages: agentMessages.length,
          tasksCompleted: completedTasks.length,
          tasksInProgress: inProgressTasks.length,
          ...additionalStats,
        };
      }

      console.log(
        `✅ [Agent Stats] Batch processing complete for ${agents.length} agents`,
      );
      return statsMap;
    } catch (error) {
      console.error("❌ [Agent Stats] Error getting batch agent stats:", error);
      // Return empty stats for all agents on error
      const emptyStats: AgentStats = {
        totalMessages: 0,
        tasksCompleted: 0,
        tasksInProgress: 0,
        averageResponseTime: "N/A",
        successRate: 0,
        daysActive: 1,
        lastActive: "Unknown",
      };

      return agents.reduce(
        (acc, agent) => {
          acc[agent.id] = emptyStats;
          return acc;
        },
        {} as Record<string, AgentStats>,
      );
    }
  }
}

// Export the class for manual instantiation when needed
export default AgentStatsService;
