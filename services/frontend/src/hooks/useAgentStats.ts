import { useState, useEffect } from 'react';
import AgentStatsService, { AgentStats } from '@/services/agentStatsService';

interface Agent {
  id: string;
  name: string;
  created_at?: string;
}

interface UseAgentStatsReturn {
  stats: Record<string, AgentStats>;
  loading: boolean;
  error: string | null;
  refreshStats: () => Promise<void>;
}

/**
 * Custom hook to fetch and manage agent statistics
 *
 * @param agents - Array of agent objects with id, name, and optional created_at
 * @param autoRefresh - Whether to automatically refresh stats periodically (default: false)
 * @param refreshInterval - Refresh interval in milliseconds (default: 5 minutes)
 */
export const useAgentStats = (
  agents: Agent[],
  autoRefresh: boolean = false,
  refreshInterval: number = 5 * 60 * 1000 // 5 minutes
): UseAgentStatsReturn => {
  const [stats, setStats] = useState<Record<string, AgentStats>>({});
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStats = async () => {
    if (agents.length === 0) {
      setStats({});
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const agentStatsService = new AgentStatsService();
      const statsData = await agentStatsService.getBatchAgentStats(agents);
      setStats(statsData);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to fetch agent stats';
      setError(errorMessage);
      console.error('Error fetching agent stats:', err);
    } finally {
      setLoading(false);
    }
  };

  const refreshStats = async () => {
    await fetchStats();
  };

  // Initial fetch when agents change
  useEffect(() => {
    fetchStats();
  }, [agents.length, JSON.stringify(agents.map(a => ({ id: a.id, name: a.name })))]);

  // Auto-refresh setup
  useEffect(() => {
    if (!autoRefresh) return;

    const interval = setInterval(fetchStats, refreshInterval);
    return () => clearInterval(interval);
  }, [autoRefresh, refreshInterval, agents]);

  return {
    stats,
    loading,
    error,
    refreshStats
  };
};

/**
 * Hook for fetching stats for a single agent
 */
export const useSingleAgentStats = (agentId: string, agentName: string, agentCreatedAt?: string) => {
  const [stats, setStats] = useState<AgentStats | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStats = async () => {
    if (!agentId || !agentName) return;

    setLoading(true);
    setError(null);

    try {
      const agentStatsService = new AgentStatsService();
      const statsData = await agentStatsService.getAgentStats(agentId, agentName, agentCreatedAt);
      setStats(statsData);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to fetch agent stats';
      setError(errorMessage);
      console.error('Error fetching single agent stats:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStats();
  }, [agentId, agentName]);

  return {
    stats,
    loading,
    error,
    refreshStats: fetchStats
  };
};

export default useAgentStats;
