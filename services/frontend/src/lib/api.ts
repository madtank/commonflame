import axios from "axios";
import { config } from "../config/environment";
import { storage } from "./storage";
import { TASK_ROUTES } from "./api-routes";

// Connect to FastAPI backend using environment configuration
const API_URL = config.apiUrl;

// Create axios instance with default config
const apiClient = axios.create({
  baseURL: API_URL,
  headers: {
    "Content-Type": "application/json",
  },
});

// Add token to requests if available
apiClient.interceptors.request.use(async (config) => {
  const token = await storage.getUserTokenAsync();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export const api = {
  // Authentication Methods
  async login(email: string, password: string) {
    try {
      const response = await apiClient.post("/auth/login", {
        email,
        password,
      });
      return response.data;
    } catch (error: any) {
      console.error("Login error:", error);
      throw error;
    }
  },

  async register(userData: {
    email: string;
    password: string;
    username: string;
    first_name?: string;
    last_name?: string;
    organization?: string;
  }) {
    try {
      const response = await apiClient.post("/auth/register", userData);
      return response.data;
    } catch (error: any) {
      console.error("Registration error:", error);
      throw error;
    }
  },

  // User Messages
  async getUserMessages(timeHours?: number, limit: number = 100) {
    try {
      // Client default/page cap: 100 to avoid 429s and large payloads
      const pageSize = Math.max(1, Math.min(limit || 100, 100));
      let url = `/auth/messages?limit=${pageSize}`;
      if (timeHours !== undefined) {
        url += `&hours=${timeHours}`;
      }

      const response = await apiClient.get(url);
      return response.data;
    } catch (error: any) {
      console.error("Error fetching user messages:", error);
      return { posts: [] };
    }
  },

  // User Agents
  async getUserAgents() {
    try {
      const response = await apiClient.get("/auth/agents");
      return response.data;
    } catch (error: any) {
      console.error("Error fetching user agents:", error);
      return { agents: [] };
    }
  },

  // Search Messages
  async searchMessages(query: string, filters: any = {}) {
    try {
      const response = await apiClient.post("/api/search/messages", {
        query,
        days_back: filters.days_back || 7,
        limit: filters.limit || 20,
        offset: filters.offset || 0,
        agent_filter: filters.agent_filter || undefined,
        channel_filter: filters.channel_filter || undefined,
      });
      return response.data;
    } catch (error: any) {
      console.error("Search error:", error);
      return {
        success: false,
        error: error.message || "Search failed",
        messages: [],
        topics: [],
      };
    }
  },

  // Get Trending Topics
  async getTrendingTopics(days_back: number = 30) {
    // Try the dedicated endpoint first, but expect it might not exist
    try {
      const response = await apiClient.get(
        `/api/search/trending-topics?days_back=${days_back}`,
      );
      return response.data;
    } catch (error: any) {
      // If endpoint doesn't exist (404) or other error, use fallback
      console.log(
        `🔄 Trending topics endpoint not available (${error.response?.status || "unknown"}), using fallback analysis...`,
      );
    }

    // Fallback: analyze recent messages to extract trending topics
    try {
      console.log(
        `📊 Analyzing messages from past ${days_back} days for trending topics...`,
      );
      const messages = await this.getUserMessages(days_back * 24, 10000); // Increase limit for better analysis
      const hashtagCounts: Record<string, number> = {};

      if (messages.posts && messages.posts.length > 0) {
        console.log(
          `🔍 Analyzing ${messages.posts.length} messages for hashtags...`,
        );
        messages.posts.forEach((post: any) => {
          const content = post.content || "";
          const hashtags = content.match(/#\w+/g) || [];
          hashtags.forEach((hashtag: string) => {
            const cleanTag = hashtag.toLowerCase(); // Normalize case
            hashtagCounts[cleanTag] = (hashtagCounts[cleanTag] || 0) + 1;
          });
        });
      }

      // Convert to array and sort by count, filter out low-count topics
      const trendingTopics = Object.entries(hashtagCounts)
        .map(([topic, count]) => ({ topic, count }))
        .filter(({ count }) => count > 1) // Only topics mentioned more than once
        .sort((a, b) => b.count - a.count);

      console.log(
        `✅ Found ${trendingTopics.length} trending topics from fallback analysis`,
      );

      return {
        success: true,
        trending_topics: trendingTopics,
        total_topics: trendingTopics.length,
        days_analyzed: days_back,
        source: "fallback_analysis",
      };
    } catch (fallbackError: any) {
      console.error(
        "❌ Fallback trending topics analysis failed:",
        fallbackError,
      );
      return {
        success: false,
        error: `Failed to analyze trending topics: ${fallbackError.message}`,
        trending_topics: [],
        total_topics: 0,
        source: "error",
      };
    }
  },

  // Task Management
  async getTasks() {
    try {
      const response = await apiClient.get(TASK_ROUTES.collection);
      return response.data;
    } catch (error: any) {
      console.error("Error fetching tasks:", error);
      return { tasks: [] };
    }
  },

  async createTask(task: any) {
    try {
      const response = await apiClient.post(TASK_ROUTES.writeCollection, task);
      return response.data;
    } catch (error: any) {
      console.error("Error creating task:", error);
      throw error;
    }
  },

  async updateTask(taskId: number, updates: any) {
    try {
      const response = await apiClient.put(
        TASK_ROUTES.writeItem(taskId),
        updates,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error updating task:", error);
      throw error;
    }
  },

  async deleteTask(taskId: number) {
    try {
      const response = await apiClient.delete(TASK_ROUTES.writeItem(taskId));
      return response.data;
    } catch (error: any) {
      console.error("Error deleting task:", error);
      throw error;
    }
  },
};
