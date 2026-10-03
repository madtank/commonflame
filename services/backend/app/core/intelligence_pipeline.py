"""
Intelligence Pipeline for aX Platform

Two concerns live here:
1. ProcessingContext / ProcessingStage — the message-level intelligence protocol
   used by dispatch_executor.py to flow structured intelligence from aX.
2. Legacy log-analytics classes (TrendAnalyzer, AgentBehaviorAnalyzer, etc.)
   used by /intelligence/* monitoring endpoints in api/main.py.
"""

import asyncio
import json
import time
import logging
from typing import Dict, List, Any, Optional, Protocol, Tuple
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from dataclasses import dataclass, field
import re
import math

from app.core.async_logging import LogEventType


# ---------------------------------------------------------------------------
# Message-level intelligence protocol (aX consolidation)
# ---------------------------------------------------------------------------

@dataclass
class ProcessingContext:
    """Flows through all pipeline stages. Each stage reads/writes fields."""

    message_id: str
    space_id: str
    content: str
    metadata: dict = field(default_factory=dict)
    # Control flow
    blocked: bool = False
    block_reason: str | None = None
    # Intelligence output (aX fills these)
    intelligence: dict | None = None  # {summary, spam, toxicity, quality, security_score, ...}
    routing: str | None = None  # "handle" | "delegate" | "queue" | "silent"
    target: str | None = None  # agent name if delegating
    visible_reply: str | None = None  # only if aX produces a reply


class ProcessingStage(Protocol):
    """Interface for any pipeline stage — pre, core, or post."""

    stage_type: str  # "pre" | "core" | "post"

    async def process(self, ctx: ProcessingContext) -> ProcessingContext: ...


# ---------------------------------------------------------------------------
# Legacy log-analytics (monitoring endpoints)
# ---------------------------------------------------------------------------


@dataclass
class TrendingTopic:
    """A trending topic with metadata"""
    term: str
    frequency: int
    growth_rate: float  # Percentage growth over time period
    contexts: List[str]  # Where it appears (search, tasks, messages)
    agents: List[str]   # Which agents are using it
    time_range: str     # When it's trending
    confidence: float   # 0-1 confidence score


@dataclass
class AgentInsight:
    """Insights about agent behavior and performance"""
    agent_name: str
    activity_score: float
    collaboration_score: float
    productivity_metrics: Dict[str, Any]
    specializations: List[str]
    recent_patterns: List[str]


@dataclass
class PlatformIntelligence:
    """Complete platform intelligence snapshot"""
    timestamp: datetime
    trending_topics: List[TrendingTopic]
    agent_insights: List[AgentInsight]
    platform_metrics: Dict[str, Any]
    predictions: Dict[str, Any]
    health_indicators: Dict[str, Any]


class IntelligencePipeline:
    """
    Core intelligence engine that processes logs and generates insights
    """

    def __init__(self):
        self.logger = logging.getLogger("intelligence_pipeline")
        self.log_buffer = []
        self.analysis_cache = {}
        self.cache_ttl = 300  # 5 minutes

        # Intelligence algorithms
        self.trend_analyzer = TrendAnalyzer()
        self.agent_analyzer = AgentBehaviorAnalyzer()
        self.prediction_engine = PredictionEngine()

    async def process_log_entry(self, log_entry: Dict[str, Any]):
        """Process a single log entry for intelligence extraction"""
        try:
            # Add to processing buffer
            self.log_buffer.append({
                **log_entry,
                'processed_at': time.time()
            })

            # Process buffer when it reaches threshold or time limit
            if (len(self.log_buffer) >= 50 or
                (self.log_buffer and
                 time.time() - self.log_buffer[0]['processed_at'] > 60)):
                await self._process_buffer()

        except Exception as e:
            self.logger.error(f"Error processing log entry: {e}")

    async def _process_buffer(self):
        """Process accumulated log entries"""
        if not self.log_buffer:
            return

        buffer_copy = self.log_buffer.copy()
        self.log_buffer.clear()

        # Extract intelligence from buffer
        await asyncio.gather(
            self.trend_analyzer.analyze_trends(buffer_copy),
            self.agent_analyzer.analyze_behavior(buffer_copy),
            self.prediction_engine.update_predictions(buffer_copy)
        )

    async def get_platform_intelligence(self,
                                      time_range: str = "1h",
                                      include_predictions: bool = True) -> PlatformIntelligence:
        """Generate comprehensive platform intelligence"""
        cache_key = f"intelligence_{time_range}_{include_predictions}"

        # Check cache
        if cache_key in self.analysis_cache:
            cached_time, cached_data = self.analysis_cache[cache_key]
            if time.time() - cached_time < self.cache_ttl:
                return cached_data

        # Generate fresh intelligence
        intelligence = await self._generate_intelligence(time_range, include_predictions)

        # Cache results
        self.analysis_cache[cache_key] = (time.time(), intelligence)

        return intelligence

    async def _generate_intelligence(self, time_range: str, include_predictions: bool) -> PlatformIntelligence:
        """Generate fresh platform intelligence"""

        # Parallel intelligence generation
        trending_task = self.trend_analyzer.get_trending_topics(time_range)
        agent_insights_task = self.agent_analyzer.get_agent_insights(time_range)
        platform_metrics_task = self._get_platform_metrics(time_range)

        trending_topics, agent_insights, platform_metrics = await asyncio.gather(
            trending_task, agent_insights_task, platform_metrics_task
        )

        predictions = {}
        if include_predictions:
            predictions = await self.prediction_engine.get_predictions()

        # Health indicators
        health_indicators = self._calculate_health_indicators(
            trending_topics, agent_insights, platform_metrics
        )

        return PlatformIntelligence(
            timestamp=datetime.utcnow(),
            trending_topics=trending_topics,
            agent_insights=agent_insights,
            platform_metrics=platform_metrics,
            predictions=predictions,
            health_indicators=health_indicators
        )

    async def _get_platform_metrics(self, time_range: str) -> Dict[str, Any]:
        """Calculate platform-wide metrics"""
        # This would integrate with the logging system
        return {
            "total_requests": 0,  # Placeholder - would query actual logs
            "unique_agents": 0,
            "avg_response_time": 0.0,
            "error_rate": 0.0,
            "growth_rate": 0.0
        }

    def _calculate_health_indicators(self,
                                   trending_topics: List[TrendingTopic],
                                   agent_insights: List[AgentInsight],
                                   platform_metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate platform health indicators"""

        # Collaboration health (how well agents work together)
        avg_collaboration = sum(ai.collaboration_score for ai in agent_insights) / max(1, len(agent_insights))

        # Topic diversity (variety of trending topics indicates healthy platform)
        topic_diversity = len(set(tt.term for tt in trending_topics)) / max(1, len(trending_topics))

        # Activity health (consistent agent activity)
        avg_activity = sum(ai.activity_score for ai in agent_insights) / max(1, len(agent_insights))

        return {
            "collaboration_health": min(1.0, avg_collaboration),
            "topic_diversity": min(1.0, topic_diversity),
            "activity_health": min(1.0, avg_activity),
            "overall_health": min(1.0, (avg_collaboration + topic_diversity + avg_activity) / 3)
        }


class TrendAnalyzer:
    """Analyzes trends from log data"""

    def __init__(self):
        self.trend_history = defaultdict(list)
        self.stop_words = {
            'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with',
            'by', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has', 'had',
            'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might',
            'can', 'this', 'that', 'these', 'those', 'a', 'an', 'task', 'message',
            'agent', 'search', 'create', 'update', 'delete', 'get', 'post', 'api'
        }

    async def analyze_trends(self, log_entries: List[Dict[str, Any]]):
        """Analyze trends from log entries"""
        current_time = time.time()

        # Extract terms from different contexts
        search_terms = self._extract_search_terms(log_entries)
        task_terms = self._extract_task_terms(log_entries)
        content_terms = self._extract_content_terms(log_entries)

        # Combine and weight terms
        all_terms = Counter()
        all_terms.update({term: count * 3 for term, count in search_terms.items()})  # Search terms are highly weighted
        all_terms.update({term: count * 2 for term, count in task_terms.items()})    # Task terms are medium weighted
        all_terms.update(content_terms)  # Content terms are base weighted

        # Update trend history
        for term, count in all_terms.items():
            self.trend_history[term].append((current_time, count))

        # Clean old history (keep last 24 hours)
        cutoff_time = current_time - 86400  # 24 hours
        for term in self.trend_history:
            self.trend_history[term] = [
                (timestamp, count) for timestamp, count in self.trend_history[term]
                if timestamp > cutoff_time
            ]

    def _extract_search_terms(self, log_entries: List[Dict[str, Any]]) -> Counter:
        """Extract terms from search queries"""
        terms = Counter()

        for entry in log_entries:
            if entry.get('event_type') == 'search_performed':
                metadata = entry.get('metadata', {})
                query = metadata.get('query', '')
                if query:
                    # Extract meaningful terms
                    clean_terms = self._clean_and_tokenize(query)
                    terms.update(clean_terms)

        return terms

    def _extract_task_terms(self, log_entries: List[Dict[str, Any]]) -> Counter:
        """Extract terms from task titles and descriptions"""
        terms = Counter()

        for entry in log_entries:
            if entry.get('event_type') == 'task_created':
                metadata = entry.get('metadata', {})
                title = metadata.get('title', '')
                if title:
                    clean_terms = self._clean_and_tokenize(title)
                    terms.update(clean_terms)

        return terms

    def _extract_content_terms(self, log_entries: List[Dict[str, Any]]) -> Counter:
        """Extract terms from message content"""
        terms = Counter()

        for entry in log_entries:
            if entry.get('event_type') == 'message_sent':
                metadata = entry.get('metadata', {})
                content_preview = metadata.get('content_preview', '')
                if content_preview:
                    clean_terms = self._clean_and_tokenize(content_preview)
                    terms.update(clean_terms)

        return terms

    def _clean_and_tokenize(self, text: str) -> List[str]:
        """Clean and tokenize text for analysis"""
        # Convert to lowercase and remove special characters
        text = re.sub(r'[^\w\s]', ' ', text.lower())

        # Split into words
        words = text.split()

        # Filter stop words and short words
        meaningful_words = [
            word for word in words
            if len(word) > 2 and word not in self.stop_words
        ]

        return meaningful_words

    async def get_trending_topics(self, time_range: str) -> List[TrendingTopic]:
        """Get current trending topics"""
        current_time = time.time()

        # Parse time range
        hours = self._parse_time_range(time_range)
        cutoff_time = current_time - (hours * 3600)

        trending = []

        for term, history in self.trend_history.items():
            if not history:
                continue

            # Get recent activity
            recent_activity = [
                count for timestamp, count in history
                if timestamp > cutoff_time
            ]

            if not recent_activity:
                continue

            # Calculate trend metrics
            total_frequency = sum(recent_activity)

            # Calculate growth rate (compare first half vs second half of time period)
            if len(recent_activity) >= 4:
                mid_point = len(recent_activity) // 2
                early_avg = sum(recent_activity[:mid_point]) / mid_point
                late_avg = sum(recent_activity[mid_point:]) / (len(recent_activity) - mid_point)
                growth_rate = ((late_avg - early_avg) / max(0.1, early_avg)) * 100
            else:
                growth_rate = 0.0

            # Calculate confidence based on frequency and consistency
            confidence = min(1.0, total_frequency / 10.0)  # Higher frequency = higher confidence

            if total_frequency >= 1:  # Lower threshold for testing - minimum 1 occurrence
                trending.append(TrendingTopic(
                    term=term,
                    frequency=total_frequency,
                    growth_rate=growth_rate,
                    contexts=["search", "tasks", "messages"],  # Simplified for MVP
                    agents=[],  # Would extract from log entries
                    time_range=time_range,
                    confidence=confidence
                ))

        # Sort by weighted score (frequency + growth rate + confidence)
        trending.sort(key=lambda t: t.frequency * (1 + t.growth_rate/100) * t.confidence, reverse=True)

        return trending[:10]  # Top 10 trending topics

    def _parse_time_range(self, time_range: str) -> float:
        """Parse time range string to hours"""
        if time_range.endswith('m'):
            return float(time_range[:-1]) / 60
        elif time_range.endswith('h'):
            return float(time_range[:-1])
        elif time_range.endswith('d'):
            return float(time_range[:-1]) * 24
        else:
            return 1.0  # Default to 1 hour


class AgentBehaviorAnalyzer:
    """Analyzes agent behavior patterns"""

    def __init__(self):
        self.agent_profiles = defaultdict(dict)

    async def analyze_behavior(self, log_entries: List[Dict[str, Any]]):
        """Analyze agent behavior from log entries"""
        agent_activities = defaultdict(list)

        # Group activities by agent
        for entry in log_entries:
            agent_name = entry.get('agent_name')
            if agent_name and agent_name != 'unknown':
                agent_activities[agent_name].append(entry)

        # Update agent profiles
        for agent_name, activities in agent_activities.items():
            await self._update_agent_profile(agent_name, activities)

    async def _update_agent_profile(self, agent_name: str, activities: List[Dict[str, Any]]):
        """Update individual agent profile"""
        profile = self.agent_profiles[agent_name]

        # Activity analysis
        activity_types = Counter(activity.get('event_type') for activity in activities)

        # Performance analysis
        durations = [
            activity.get('duration_ms', 0) for activity in activities
            if activity.get('duration_ms')
        ]
        avg_duration = sum(durations) / max(1, len(durations))

        # Update profile
        profile.update({
            'recent_activity_count': len(activities),
            'activity_types': dict(activity_types),
            'avg_response_time': avg_duration,
            'last_active': max(activity.get('timestamp', 0) for activity in activities),
        })

    async def get_agent_insights(self, time_range: str) -> List[AgentInsight]:
        """Generate agent insights"""
        insights = []

        for agent_name, profile in self.agent_profiles.items():
            # Calculate activity score
            activity_score = min(1.0, profile.get('recent_activity_count', 0) / 10.0)

            # Calculate collaboration score (simplified)
            collaboration_score = 0.7  # Placeholder - would analyze cross-agent interactions

            # Productivity metrics
            productivity_metrics = {
                'avg_response_time': profile.get('avg_response_time', 0),
                'activity_count': profile.get('recent_activity_count', 0),
                'success_rate': 0.95  # Placeholder
            }

            # Specializations (based on activity types)
            activity_types = profile.get('activity_types', {})
            specializations = [
                activity_type for activity_type, count in activity_types.items()
                if count > 2  # Specializes in activities they do frequently
            ]

            insights.append(AgentInsight(
                agent_name=agent_name,
                activity_score=activity_score,
                collaboration_score=collaboration_score,
                productivity_metrics=productivity_metrics,
                specializations=specializations,
                recent_patterns=[]  # Placeholder for pattern analysis
            ))

        return insights


class PredictionEngine:
    """Generates predictions based on platform data"""

    def __init__(self):
        self.prediction_models = {}

    async def update_predictions(self, log_entries: List[Dict[str, Any]]):
        """Update prediction models with new data"""
        # Placeholder for ML model updates
        pass

    async def get_predictions(self) -> Dict[str, Any]:
        """Generate platform predictions"""
        return {
            "next_trending_topics": ["database", "optimization", "security"],
            "peak_activity_hours": [9, 14, 16],  # 9am, 2pm, 4pm
            "collaboration_opportunities": [
                {
                    "agents": ["code_weaver", "mcp_tester"],
                    "suggested_project": "MCP optimization",
                    "confidence": 0.85
                }
            ],
            "platform_growth_forecast": {
                "next_week_activity": "+15%",
                "new_agent_likelihood": 0.7,
                "feature_demand": ["messaging", "task_automation"]
            }
        }


# Global intelligence pipeline instance
intelligence_pipeline = IntelligencePipeline()


# Integration functions for the logging system
async def process_log_for_intelligence(log_entry: Dict[str, Any]):
    """Process log entry for intelligence extraction"""
    await intelligence_pipeline.process_log_entry(log_entry)


async def get_platform_intelligence_snapshot(time_range: str = "1h") -> Dict[str, Any]:
    """Get comprehensive platform intelligence"""
    intelligence = await intelligence_pipeline.get_platform_intelligence(time_range)

    return {
        "timestamp": intelligence.timestamp.isoformat(),
        "trending_topics": [
            {
                "term": topic.term,
                "frequency": topic.frequency,
                "growth_rate": round(topic.growth_rate, 2),
                "confidence": round(topic.confidence, 2),
                "contexts": topic.contexts
            }
            for topic in intelligence.trending_topics
        ],
        "agent_insights": [
            {
                "agent_name": insight.agent_name,
                "activity_score": round(insight.activity_score, 2),
                "collaboration_score": round(insight.collaboration_score, 2),
                "specializations": insight.specializations,
                "productivity": insight.productivity_metrics
            }
            for insight in intelligence.agent_insights
        ],
        "platform_metrics": intelligence.platform_metrics,
        "predictions": intelligence.predictions,
        "health_indicators": {
            k: round(v, 2) if isinstance(v, float) else v
            for k, v in intelligence.health_indicators.items()
        }
    }
