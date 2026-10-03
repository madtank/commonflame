# ruff: noqa: E402
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

# Import all models to ensure they're registered (must come after Base definition)
from .access_request import AccessRequest
from .agent import Agent
from .agent_key import AgentKey
from .agent_space_access import AgentSpaceAccess
from .agent_groups import AgentGroup, AgentGroupMember
from .feature_flag import FeatureFlag
from .agent_relationship import AgentRelationship
from .attachment import Attachment
from .guardrail_config import GuardrailConfig
from .guardrail_violation import GuardrailViolation
from .mention import Mention
from .message import Message
from .message_intelligence import MessageIntelligence
from .oauth_as import OAuthAuthorizationCode, OAuthClient, OAuthDeviceCode, OAuthRefreshToken
from .space import Space
from .space_invite_code import SpaceInviteCode
from .space_membership import SpaceMembership
from .pending_relationship_request import PendingRelationshipRequest
from .refresh_token import RefreshToken
from .task import Task
from .task_note import TaskNote
from .user import User
from .user_audit_event import UserAuditEvent
from .user_outreach import UserOutreach


from .notification_preferences import NotificationPreferences
from .user_settings import UserSettings
from .workspace_intelligence import WorkspaceIntelligence, WorkspaceIntelligenceHistory, ArtifactType
from .guest_space import SpaceInvite, SpaceMember, SpaceChannelSetting
from .concierge_routing_log import ConciergeRoutingLog
from .conversation_card import ConversationCard  # noqa: F401
from .message_feedback import MessageFeedback
from .credential import Credential
from .credential_fingerprint import CredentialFingerprint
from .tool_call import ToolCall
from .agent_management import (
    AgentSpaceOverride,
    AgentManagementProposal,
    AgentManagementApproval,
    AgentManagementAudit,
    AgentManagementOutbox,
)
from .interactive_context import (
    ContextArtifactVersion,
    ContextAuditEvent,
    ContextCatalogEntry,
    ContextObject,
    ContextPatch,
    ContextStateVersion,
)

# Backward compatibility aliases
Organization = Space
OrganizationMembership = SpaceMembership
OrganizationInvite = SpaceInviteCode

__all__ = [
    "AccessRequest",
    "Agent",
    "AgentKey",
    "AgentSpaceAccess",
    "AgentGroup",
    "AgentGroupMember",
    "FeatureFlag",
    "AgentRelationship",
    "ArtifactType",
    "Base",
    "NotificationPreferences",
    "GuardrailConfig",
    "GuardrailViolation",
    "Mention",
    "Message",
    "MessageIntelligence",
    "Organization",
    "OrganizationInvite",
    "OrganizationMembership",
    "OAuthAuthorizationCode",
    "OAuthClient",
    "OAuthDeviceCode",
    "OAuthRefreshToken",
    "PendingRelationshipRequest",
    "RefreshToken",
    "Space",
    "SpaceInviteCode",
    "SpaceMembership",
    "Task",
    "TaskNote",
    "User",
    "UserAuditEvent",
    "UserOutreach",
    "UserSettings",
    "WorkspaceIntelligence",
    "WorkspaceIntelligenceHistory",
    "ConciergeRoutingLog",
    "ConversationCard",
    "MessageFeedback",
    "Credential",
    "CredentialFingerprint",
    "ToolCall",
    "AgentSpaceOverride",
    "AgentManagementProposal",
    "AgentManagementApproval",
    "AgentManagementAudit",
    "AgentManagementOutbox",
    "ContextArtifactVersion",
    "ContextAuditEvent",
    "ContextCatalogEntry",
    "ContextObject",
    "ContextPatch",
    "ContextStateVersion",
]
