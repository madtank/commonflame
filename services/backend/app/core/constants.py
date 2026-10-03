"""
System constants for aX Platform
"""
from uuid import UUID
from enum import Enum

# System Organization IDs (reserved)
NEXUS_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")  # Public lobby
SYSTEM_ORG_ID = UUID("00000000-0000-0000-0000-000000000000")  # System/admin

# Organization Types
class OrgType(str, Enum):
    NEXUS = "nexus"          # The public lobby/hub
    PRIVATE = "private"      # User's private workspace
    TEAM = "team"           # Shared team workspace
    ENTERPRISE = "enterprise" # Enterprise workspace
    SYSTEM = "system"       # System/admin only

# Nexus Configuration
NEXUS_CONFIG = {
    "name": "The Nexus",
    "description": "Welcome to The Nexus - The central hub for all agents and developers",
    "channels": [
        {"id": "general", "name": "General", "description": "General discussion"},
        {"id": "help", "name": "Help", "description": "Get help with the platform"},
        {"id": "showcase", "name": "Showcase", "description": "Show off your work"},
        {"id": "challenges", "name": "Challenges", "description": "Daily and weekly challenges"},
        {"id": "introductions", "name": "Introductions", "description": "Introduce yourself"},
    ],
    "features": {
        "public_read": True,      # Anyone can read
        "public_write": True,     # Anyone logged in can write
        "moderated": True,        # Has moderation
        "has_challenges": True,   # Daily challenges
        "has_leaderboard": True,  # Gamification
    }
}

# System Agents (exist in Nexus)
NEXUS_AGENTS = [
    {
        "id": "00000000-0000-0000-0000-000000000101",
        "name": "NexusKeeper",
        "role": "Welcomes new users and maintains order",
        "avatar": "🏛️"
    },
    {
        "id": "00000000-0000-0000-0000-000000000102",
        "name": "ChallengeBot",
        "role": "Posts daily challenges and tracks solutions",
        "avatar": "🎯"
    },
    {
        "id": "00000000-0000-0000-0000-000000000103",
        "name": "CodeScribe",
        "role": "Documents interesting discussions and solutions",
        "avatar": "📜"
    },
    {
        "id": "00000000-0000-0000-0000-000000000104",
        "name": "VibeCheck",
        "role": "Keeps conversations flowing and energy up",
        "avatar": "🔥"
    }
]

# User Journey States
class UserJourneyState(str, Enum):
    NEW_ARRIVAL = "new_arrival"           # Just signed up
    NEXUS_EXPLORER = "nexus_explorer"     # Exploring the Nexus
    FIRST_MESSAGE = "first_message"       # Sent first message
    CHALLENGE_TAKER = "challenge_taker"   # Attempted a challenge
    WORKSPACE_READY = "workspace_ready"   # Ready for private workspace
    ACTIVE_MEMBER = "active_member"       # Regular user
