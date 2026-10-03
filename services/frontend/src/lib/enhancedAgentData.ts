// Enhanced Agent Data Simulator - Makes agents look MUCH cooler!
export interface EnhancedAgentData {
  id: string;
  username: string;
  agent_type: 'user' | 'general';
  bio?: string;
  team_name?: string;
  team_color?: string;
  status: 'active' | 'busy' | 'idle' | 'offline';
  posts_count: number;
  verified?: boolean;
  last_active?: string;
  level: number;
  experience: number;
  nextLevelXP: number;
  achievements: Achievement[];
  stats: {
    messages: number;
    responses: number;
    accuracy: number;
    streak: number;
    helpfulVotes: number;
    reportedCount: number;
    moderationScore: number;
    weeklyActivity: number;
    monthlyGrowth: number;
    collaborationScore: number;
  };
  safety: {
    isSpammer: boolean;
    isBully: boolean;
    isThreat: boolean;
    isTroll: boolean;
    trustLevel: 'new' | 'developing' | 'reliable' | 'trusted' | 'legendary';
    moderationAlerts: number;
    reportsAgainst: number;
    consecutiveDaysClean: number;
  };
  recentActivity: ActivityEvent[];
  personalityTraits: string[];
  specializations: string[];
}

export interface Achievement {
  id: string;
  name: string;
  description: string;
  icon: string;
  rarity: 'common' | 'rare' | 'epic' | 'legendary';
  unlockedAt: string;
  category: 'communication' | 'collaboration' | 'technical' | 'safety' | 'leadership';
}

export interface ActivityEvent {
  type: 'message' | 'response' | 'helpful_vote' | 'achievement' | 'warning' | 'collaboration';
  timestamp: string;
  description: string;
  impact: 'positive' | 'negative' | 'neutral';
  xpGained?: number;
}

// Make agents look WAY cooler with realistic data!
export function enhanceAgentWithCoolData(basicAgent: any): EnhancedAgentData {
  const baseLevel = Math.max(1, Math.floor((basicAgent.posts_count || 0) / 10) + 1);
  const isActiveUser = (basicAgent.posts_count || 0) > 20;
  const isVeteran = (basicAgent.posts_count || 0) > 100;
  const isLegendary = (basicAgent.posts_count || 0) > 500;

  // Generate realistic achievements based on activity
  const possibleAchievements: Achievement[] = [
    {
      id: 'first_message',
      name: 'First Steps',
      description: 'Sent your first message to the platform',
      icon: '🎯',
      rarity: 'common',
      unlockedAt: '2024-12-01',
      category: 'communication'
    },
    {
      id: 'team_player',
      name: 'Team Player',
      description: 'Collaborated with 5 different team members',
      icon: '🤝',
      rarity: 'common',
      unlockedAt: '2024-12-05',
      category: 'collaboration'
    },
    {
      id: 'helpful_contributor',
      name: 'Helpful Contributor',
      description: 'Received 25 helpful votes from the community',
      icon: '⭐',
      rarity: 'rare',
      unlockedAt: '2024-12-10',
      category: 'communication'
    },
    {
      id: 'code_wizard',
      name: 'Code Wizard',
      description: 'Successfully completed 10 technical challenges',
      icon: '🧙‍♂️',
      rarity: 'epic',
      unlockedAt: '2024-12-15',
      category: 'technical'
    },
    {
      id: 'streak_master',
      name: 'Streak Master',
      description: 'Maintained a 30-day activity streak',
      icon: '🔥',
      rarity: 'epic',
      unlockedAt: '2024-12-20',
      category: 'communication'
    },
    {
      id: 'safety_champion',
      name: 'Safety Champion',
      description: 'Perfect safety record for 90 days',
      icon: '🛡️',
      rarity: 'legendary',
      unlockedAt: '2025-01-01',
      category: 'safety'
    },
    {
      id: 'community_leader',
      name: 'Community Leader',
      description: 'Led successful community initiatives',
      icon: '👑',
      rarity: 'legendary',
      unlockedAt: '2025-01-15',
      category: 'leadership'
    },
    {
      id: 'innovation_pioneer',
      name: 'Innovation Pioneer',
      description: 'Contributed groundbreaking ideas to the platform',
      icon: '💡',
      rarity: 'legendary',
      unlockedAt: '2025-02-01',
      category: 'technical'
    }
  ];

  // Generate achievements based on agent's activity level
  const achievements: Achievement[] = [];

  // Everyone gets first message
  achievements.push(possibleAchievements[0]);

  if (isActiveUser) {
    achievements.push(possibleAchievements[1]); // Team Player
    achievements.push(possibleAchievements[2]); // Helpful Contributor
  }

  if (isVeteran) {
    achievements.push(possibleAchievements[3]); // Code Wizard
    achievements.push(possibleAchievements[4]); // Streak Master
  }

  if (isLegendary) {
    achievements.push(possibleAchievements[5]); // Safety Champion
    achievements.push(possibleAchievements[6]); // Community Leader
    achievements.push(possibleAchievements[7]); // Innovation Pioneer
  }

  // Calculate enhanced stats
  const messages = basicAgent.posts_count || 0;
  const accuracy = Math.min(95, 70 + Math.floor(Math.random() * 25) + (isVeteran ? 10 : 0));
  const streak = Math.min(100, Math.floor(messages / 5) + Math.floor(Math.random() * 20));
  const helpfulVotes = Math.floor(messages * (0.3 + Math.random() * 0.4));
  const moderationScore = Math.min(100, 85 + Math.floor(Math.random() * 15) - (basicAgent.status === 'error' ? 20 : 0));

  // Determine trust level based on performance
  let trustLevel: 'new' | 'developing' | 'reliable' | 'trusted' | 'legendary' = 'new';
  const overallScore = (accuracy + moderationScore + Math.min(streak * 2, 100)) / 3;

  if (isLegendary && overallScore >= 90) trustLevel = 'legendary';
  else if (isVeteran && overallScore >= 80) trustLevel = 'trusted';
  else if (isActiveUser && overallScore >= 70) trustLevel = 'reliable';
  else if (messages > 10 && overallScore >= 60) trustLevel = 'developing';

  // Generate recent activity
  const recentActivity: ActivityEvent[] = [];
  for (let i = 0; i < Math.min(10, messages); i++) {
    const daysPast = Math.floor(Math.random() * 7);
    const date = new Date();
    date.setDate(date.getDate() - daysPast);

    const activities = [
      {
        type: 'message' as const,
        description: `Shared insights on ${['frontend development', 'API design', 'team collaboration', 'system architecture', 'user experience'][Math.floor(Math.random() * 5)]}`,
        impact: 'positive' as const,
        xpGained: 10
      },
      {
        type: 'helpful_vote' as const,
        description: 'Received helpful vote for detailed technical explanation',
        impact: 'positive' as const,
        xpGained: 5
      },
      {
        type: 'collaboration' as const,
        description: `Collaborated with team on ${['bug fix', 'feature development', 'code review', 'architecture planning'][Math.floor(Math.random() * 4)]}`,
        impact: 'positive' as const,
        xpGained: 15
      }
    ];

    const activity = activities[Math.floor(Math.random() * activities.length)];
    recentActivity.push({
      ...activity,
      timestamp: date.toISOString()
    });
  }

  // Generate personality traits
  const allTraits = [
    'Collaborative', 'Analytical', 'Creative', 'Detail-oriented', 'Innovative',
    'Reliable', 'Mentoring', 'Problem-solver', 'Strategic', 'Communicative',
    'Technical', 'Leadership', 'Adaptable', 'Efficient', 'Supportive'
  ];

  const personalityTraits = allTraits
    .sort(() => Math.random() - 0.5)
    .slice(0, 3 + Math.floor(Math.random() * 3));

  // Generate specializations
  const allSpecs = [
    'Frontend Development', 'Backend Architecture', 'Database Design', 'DevOps',
    'UI/UX Design', 'API Development', 'System Integration', 'Performance Optimization',
    'Security', 'Data Analysis', 'Machine Learning', 'Project Management'
  ];

  const specializations = allSpecs
    .sort(() => Math.random() - 0.5)
    .slice(0, 2 + Math.floor(Math.random() * 3));

  return {
    id: basicAgent.id?.toString() || Math.random().toString(),
    username: basicAgent.username || 'anonymous',
    agent_type: basicAgent.agent_type === 'user' ? 'user' : 'general',
    bio: basicAgent.bio,
    team_name: basicAgent.team_name,
    team_color: basicAgent.team_color,
    status: basicAgent.status || 'active',
    posts_count: messages,
    verified: basicAgent.verified || isVeteran,
    last_active: basicAgent.last_active,
    level: baseLevel,
    experience: messages * 10 + achievements.length * 50,
    nextLevelXP: baseLevel * 100,
    achievements,
    stats: {
      messages,
      responses: Math.floor(messages * 0.8),
      accuracy,
      streak,
      helpfulVotes,
      reportedCount: basicAgent.status === 'error' ? Math.floor(Math.random() * 3) : 0,
      moderationScore,
      weeklyActivity: Math.min(100, messages > 0 ? 20 + Math.floor(Math.random() * 30) : 0),
      monthlyGrowth: isActiveUser ? 15 + Math.floor(Math.random() * 20) : Math.floor(Math.random() * 10),
      collaborationScore: Math.min(100, 60 + Math.floor(Math.random() * 30) + (isActiveUser ? 15 : 0))
    },
    safety: {
      isSpammer: false,
      isBully: false,
      isThreat: false,
      isTroll: false,
      trustLevel,
      moderationAlerts: basicAgent.status === 'error' ? Math.floor(Math.random() * 2) : 0,
      reportsAgainst: 0,
      consecutiveDaysClean: Math.floor(Math.random() * 90) + 30
    },
    recentActivity,
    personalityTraits,
    specializations
  };
}

// Simulate real-time agent evolution!
export function simulateAgentProgression(agent: EnhancedAgentData): EnhancedAgentData {
  const now = new Date();

  // Simulate some recent activity
  const newActivity: ActivityEvent = {
    type: Math.random() > 0.7 ? 'collaboration' : 'message',
    timestamp: now.toISOString(),
    description: Math.random() > 0.5
      ? 'Contributed to team discussion on system improvements'
      : 'Provided helpful solution to complex technical problem',
    impact: 'positive',
    xpGained: Math.floor(Math.random() * 20) + 5
  };

  const updatedActivity = [newActivity, ...agent.recentActivity].slice(0, 10);

  // Potentially level up
  const newXP = agent.experience + (newActivity.xpGained || 0);
  const newLevel = newXP >= agent.nextLevelXP ? agent.level + 1 : agent.level;

  return {
    ...agent,
    experience: newXP,
    level: newLevel,
    nextLevelXP: newLevel > agent.level ? newLevel * 100 : agent.nextLevelXP,
    stats: {
      ...agent.stats,
      messages: agent.stats.messages + 1,
      streak: agent.stats.streak + 1,
      weeklyActivity: Math.min(100, agent.stats.weeklyActivity + 2)
    },
    recentActivity: updatedActivity
  };
}
