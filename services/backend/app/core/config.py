"""
Application configuration
Environment-aware settings following security best practices
"""

import os

from pydantic_settings import BaseSettings, NoDecode
from pydantic import field_validator
from typing import Annotated

from .secrets import get_database_url, get_jwt_secret


class Settings(BaseSettings):
    """Application settings from environment variables"""

    # Application
    app_name: str = "Waystation API"
    environment: str = os.getenv("ENVIRONMENT", "development")
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    api_v1_prefix: str = "/api/v1"

    # Database
    database_url: str = get_database_url() or os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://localhost:5432/axmarketplace_dev",  # Safe default for local dev
    )
    # Private IP fallback: If set, database.py will verify this connection first.
    # If reachable, it becomes the primary. Otherwise falls back to database_url.
    # Rollback: unset this var or set DB_PREFER_PRIVATE=false
    database_url_private: str = os.getenv("DATABASE_URL_PRIVATE", "")
    db_prefer_private: bool = os.getenv("DB_PREFER_PRIVATE", "true").lower() != "false"

    # JWT (legacy support only - OAuth 2.1 is the default auth path)
    jwt_secret_key: str = get_jwt_secret()
    jwt_access_token_expire_minutes: int = int(
        os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "1440")
    )  # Default: 24 hours (1440 minutes)
    jwt_refresh_token_expire_days: int = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    enable_legacy_jwt: bool = os.getenv("ENABLE_LEGACY_JWT", "false").lower() == "true"

    # Redis - Environment-aware defaults
    redis_url: str = os.getenv("REDIS_URL") or (
        "redis://localhost:6379"  # Local development: Docker Redis on host port 6380
        if os.getenv("ENVIRONMENT", "development") == "development"
        else "redis://localhost:6379"  # Production fallback
    )

    # CORS (cipher emphasized this is critical)
    @property
    def cors_origins(self) -> list[str]:
        """Get CORS origins from environment or defaults"""
        env_origins = os.getenv("CORS_ORIGINS")
        if env_origins:
            return [origin.strip() for origin in env_origins.split(",")]

        # Environment-based default origins
        if self.environment == "production":
            return [
                "https://paxai.app",  # Canonical production host
                "https://www.paxai.app",  # Production alias
                "https://next.paxai.app",  # Compatibility alias during transition
                "https://pax-platform-frontend-lyqp5y74jq-uc.a.run.app",  # CI/CD frontend service (legacy GCP)
            ]
        else:
            # Development origins
            return [
                "http://localhost:3000",  # Frontend dev
                "http://localhost:3001",  # Frontend alternative
                "http://localhost:3002",  # Frontend (cipher mentioned this port)
                "http://localhost:49524",  # Vite dev server (dynamic port)
                "http://localhost:8001",  # Self (for API docs)
                "http://127.0.0.1:3000",  # Frontend dev (127.0.0.1 for password saving)
                "http://127.0.0.1:3001",  # Frontend alternative (127.0.0.1)
                "http://127.0.0.1:3002",  # Frontend (127.0.0.1)
                "http://127.0.0.1:8001",  # Self (for API docs via 127.0.0.1)
                "http://10.0.0.91:3000",  # Local network frontend (iPhone/mobile testing)
                "http://10.0.0.91:8001",  # Local network API (iPhone/mobile testing)
            ]

    # API Server
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8001"))

    # MCP Configuration
    @property
    def mcp_server_url(self) -> str:
        """Get MCP server URL based on environment"""
        # Check for explicit override first
        explicit_url = os.getenv("MCP_SERVER_URL")
        if explicit_url:
            return explicit_url

        # Environment-based defaults
        if self.environment == "production":
            return "https://paxai.app"
        elif self.environment == "staging":
            return os.getenv("STAGING_API_URL", "https://staging-api.paxai.app")
        else:
            # Development default - MCP server runs on port 8002
            return "http://localhost:8002"

    @property
    def mcp_base_config(self) -> dict:
        """Get base MCP configuration for agent registration"""
        base_url = self.mcp_server_url
        use_https = base_url.startswith("https://")

        return {
            "base_url": base_url,
            "endpoint": f"{base_url}/mcp",  # Unified endpoint for streamable HTTP
            "transport": "streamable-http",  # Using streamable HTTP transport
            "allow_http": not use_https,
        }

    # Streamable HTTP Configuration
    streamable_http_only: bool = os.getenv("STREAMABLE_HTTP_ONLY", "true").lower() == "true"
    mcp_stream_history_ttl: int = int(os.getenv("MCP_STREAM_HISTORY_TTL", "600"))  # 10 minutes
    mcp_session_ttl: int = int(os.getenv("MCP_SESSION_TTL", "3600"))  # 1 hour

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    enable_sql_logging: bool = os.getenv("ENABLE_SQL_LOGGING", "false").lower() == "true"
    enable_request_logging: bool = os.getenv("ENABLE_REQUEST_LOGGING", "false").lower() == "true"

    # Managed agent / GCP configuration
    gcp_project_id: str = os.getenv("GCP_PROJECT_ID", "jax-platform-prod")
    gcp_location: str = os.getenv("GCP_LOCATION", "us-central1")
    agents_global_kill_switch: bool = os.getenv("AGENTS_GLOBAL_KILL_SWITCH", "false").lower() == "true"
    # Agent Lifecycle (ALC): gates the sweep that *acts* (nudge/suggest/archive). Default OFF —
    # the ladder stays in shadow mode (compute only) until calibrated. See docs/plans/2026-05-29-agent-lifecycle-design.md
    enable_agent_lifecycle_actions: bool = os.getenv("ENABLE_AGENT_LIFECYCLE_ACTIONS", "false").lower() == "true"

    # Gemini API (used by cloud agents)
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    # DEPRECATED: Use agent_runner_stable_url instead
    # Legacy env var kept for backwards compatibility during migration
    agent_runner_url: str = os.getenv("AGENT_RUNNER_URL", "http://agent_runner:8080")
    agent_runner_api_key: str = os.getenv("AGENT_RUNNER_API_KEY", "")

    # Internal dispatch API key (for Cloud Tasks → Backend authentication)
    # Should be a strong random secret, different from agent_runner_api_key
    # SECURITY: In production with USE_CLOUD_TASKS=true, this MUST be set
    internal_dispatch_api_key: str = os.getenv("INTERNAL_DISPATCH_API_KEY", os.getenv("AGENT_RUNNER_API_KEY", ""))

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Fail fast if dispatch API key missing in production with queue enabled
        if (
            self.environment == "production"
            and self.use_cloud_tasks
            and not self.internal_dispatch_api_key
        ):
            raise ValueError(
                "INTERNAL_DISPATCH_API_KEY must be set in production when USE_CLOUD_TASKS=true"
            )

    # Backend API URL for internal service communication (dispatch worker → backend)
    # In Docker: http://ax-backend-api:8080, in production this should be set explicitly.
    backend_api_url: str = os.getenv("BACKEND_API_URL", "http://ax-backend-api:8080")

    # Webhook callback URL - external URL that webhook agents can reach to call back
    # In Docker: http://host.docker.internal:8001, in production this should be set explicitly.
    # Falls back to backend_api_url if not set (works for production where they're the same)
    webhook_callback_url: str = os.getenv("WEBHOOK_CALLBACK_URL", "")

    # Autonomous agent mesh check-ins
    autonomous_agent_mesh_names: Annotated[list[str], NoDecode] = [
        name.strip()
        for name in os.getenv(
            "AUTONOMOUS_AGENT_MESH_NAMES",
            "",
        ).split(",")
        if name.strip()
    ]
    @field_validator("autonomous_agent_mesh_names", mode="before")
    @classmethod
    def _parse_mesh_names(cls, value):
        if isinstance(value, str):
            return [name.strip() for name in value.split(",") if name.strip()]
        return value

    autonomous_agent_mesh_interval_minutes: int = int(os.getenv("AUTONOMOUS_AGENT_MESH_INTERVAL_MINUTES", "20"))
    autonomous_agent_mesh_context_ttl_seconds: int = int(os.getenv("AUTONOMOUS_AGENT_MESH_CONTEXT_TTL_SECONDS", "1800"))

    # Cloud Tasks dispatch queue configuration
    # USE_CLOUD_TASKS=true enables queue-based dispatch (prod), false uses sync HTTP (local)
    use_cloud_tasks: bool = os.getenv("USE_CLOUD_TASKS", "false").lower() in ("true", "1", "yes")
    dispatch_queue_name: str = os.getenv("DISPATCH_QUEUE_NAME", "agent-dispatch")
    dispatch_dlq_name: str = os.getenv("DISPATCH_DLQ_NAME", "agent-dispatch-dlq")
    gcp_region: str = os.getenv("GCP_REGION", "us-central1")
    # Redis stream maxlen for local dev dispatch queue (bounded backlog)
    dispatch_stream_maxlen: int = int(os.getenv("DISPATCH_STREAM_MAXLEN", "10000"))

    # Agent Runner URLs (Cloud Run)
    # STABLE = production-ready, EXPERIMENTAL = new features (image gen, etc.)
    # Infrastructure: Google Cloud Run containers in us-central1
    # Fallback chain for backwards compat: STABLE_URL → V1_URL → AGENT_RUNNER_URL
    agent_runner_stable_url: str = os.getenv(
        "AGENT_RUNNER_STABLE_URL",
        os.getenv("AGENT_RUNNER_V1_URL", os.getenv("AGENT_RUNNER_URL", "http://agent_runner:8080"))
    )
    agent_runner_experimental_url: str = os.getenv(
        "AGENT_RUNNER_EXPERIMENTAL_URL",
        os.getenv("AGENT_RUNNER_V2_URL", "")
    )  # Empty = experimental not available

    # DEPRECATED: Old V1/V2 naming - use STABLE/EXPERIMENTAL instead
    # Kept for backwards compatibility, will be removed in future release
    agent_runner_v1_url: str = os.getenv("AGENT_RUNNER_V1_URL", os.getenv("AGENT_RUNNER_URL", "http://agent_runner:8080"))
    agent_runner_v2_url: str = os.getenv("AGENT_RUNNER_V2_URL", "")

    # Cloud agent HTTP timeout (seconds) - how long to wait for agent runner response
    # Default 300s (5 minutes) to handle image generation and complex tool chains
    # Increase for agents that may run longer tasks in the future
    cloud_agent_http_timeout_seconds: int = int(os.getenv("CLOUD_AGENT_HTTP_TIMEOUT_SECONDS", "300"))

    # Bedrock AgentCore (Mode A: shared agent, per-space sessions)
    bedrock_agent_id: str = os.getenv("BEDROCK_AGENT_ID", "")  # From Terraform output
    bedrock_agent_alias_id: str = os.getenv("BEDROCK_AGENT_ALIAS_ID", "")  # stable or canary
    bedrock_agent_region: str = os.getenv("BEDROCK_AGENT_REGION", os.getenv("AWS_REGION", "us-west-2"))
    bedrock_agent_enable_trace: bool = os.getenv("BEDROCK_AGENT_ENABLE_TRACE", "false").lower() == "true"

    # Local Space Agent container
    space_agent_runtime_backend: str = os.getenv("SPACE_AGENT_RUNTIME_BACKEND", "container")
    space_agent_url: str = os.getenv("SPACE_AGENT_URL", "http://space_agent:8000")
    space_agent_timeout: int = int(os.getenv("SPACE_AGENT_TIMEOUT", "120"))

    # Cloud agent creation restriction (admin-only during stabilization)
    # Set to "true" to allow all users, "false" or "admin" to restrict to admins only
    cloud_agent_creation_enabled: bool = os.getenv("CLOUD_AGENT_CREATION_ENABLED", "true").lower() == "true"
    auto_join_nexus_on_signup: bool = os.getenv("AUTO_JOIN_NEXUS_ON_SIGNUP", "false").lower() == "true"

    # Cloud agent rate limits (fail-safe defaults)
    cloud_agent_burst_limit: int = int(
        os.getenv("CLOUD_AGENT_BURST_LIMIT", "10")
    )  # short-window anti-loop (cloud→cloud)
    cloud_agent_burst_window_seconds: int = int(os.getenv("CLOUD_AGENT_BURST_WINDOW_SECONDS", "60"))
    cloud_agent_sustained_limit: int = int(os.getenv("CLOUD_AGENT_SUSTAINED_LIMIT", "50"))  # rolling 5m guardrail
    cloud_agent_sustained_window_seconds: int = int(os.getenv("CLOUD_AGENT_SUSTAINED_WINDOW_SECONDS", "300"))
    cloud_agent_daily_agent_limit: int = int(os.getenv("CLOUD_AGENT_DAILY_AGENT_LIMIT", "150"))  # cloud→cloud daily
    cloud_agent_daily_org_limit: int = int(os.getenv("CLOUD_AGENT_DAILY_ORG_LIMIT", "0"))

    # Agent context window - how many messages of history to send to cloud agents
    agent_history_limit: int = int(os.getenv("AGENT_HISTORY_LIMIT", "100"))  # default 100 messages
    cloud_agent_per_user_daily_limit: int = int(os.getenv("CLOUD_AGENT_PER_USER_DAILY_LIMIT", "500"))  # owner aggregate
    cloud_agent_consecutive_limit: int = int(
        os.getenv("CLOUD_AGENT_CONSECUTIVE_LIMIT", "5")
    )  # Sentinel: enable 3-5 consecutive over 5m for cloud↔cloud loop prevention
    cloud_agent_consecutive_ttl_seconds: int = int(os.getenv("CLOUD_AGENT_CONSECUTIVE_TTL_SECONDS", "300"))  # 5 minutes
    cloud_agent_throttle_pause_seconds: int = int(
        os.getenv("CLOUD_AGENT_THROTTLE_PAUSE_SECONDS", "120")
    )  # Pause duration when cloud-to-cloud rate limit is hit (2 minutes default)

    # Cloud agent cost monitoring (warn-only caps)
    cloud_agent_daily_budget_cents: int = int(os.getenv("CLOUD_AGENT_DAILY_BUDGET_CENTS", "0"))
    cloud_agent_org_daily_budget_cents: int = int(os.getenv("CLOUD_AGENT_ORG_DAILY_BUDGET_CENTS", "0"))
    cloud_agent_user_daily_budget_cents: int = int(os.getenv("CLOUD_AGENT_USER_DAILY_BUDGET_CENTS", "0"))
    cloud_agent_platform_daily_budget_cents: int = int(os.getenv("CLOUD_AGENT_PLATFORM_DAILY_BUDGET_CENTS", "0"))

    # Cloud agent tiered defaults (limits + budgets).
    # Paid/admin tiers get more headroom, but still have finite caps to protect
    # aX/system agents and other automation from runaway loops.
    cloud_agent_tiers: dict = {
        "regular": {
            "burst_limit": 5,
            "sustained_limit": 50,
            "daily_agent_limit": 150,
            "daily_user_limit": 500,
            "user_budget_cents": 0,
            "org_budget_cents": 0,
            "model": "flash",
        },
        "plus": {
            "burst_limit": 10,
            "sustained_limit": 100,
            "daily_agent_limit": 300,  # 2x regular
            "daily_user_limit": 1000,  # 2x regular, still capped
            "user_budget_cents": 0,
            "org_budget_cents": 0,
            "model": "flash+pro",
        },
        "admin": {
            "burst_limit": 10,
            "sustained_limit": 100,
            "daily_agent_limit": 300,  # 2x regular
            "daily_user_limit": 1000,  # bounded; admins are not unlimited
            "user_budget_cents": 0,
            "org_budget_cents": 0,
            "model": "pro+custom",
        },
        # Legacy aliases for backwards compatibility
        "free": {"_alias": "regular"},
        "pro": {"_alias": "plus"},
        "enterprise": {"_alias": "admin"},
    }

    # Messages tool tuning
    messages_platform_stats_enabled: bool = os.getenv("MESSAGES_PLATFORM_STATS_ENABLED", "true").lower() == "true"
    messages_platform_stats_timeout: float = float(os.getenv("MESSAGES_PLATFORM_STATS_TIMEOUT", "1.0"))

    # Gemini 2.5 Flash pricing (per 1M tokens) - Update these when pricing changes!
    gemini_input_cost_per_1m: float = float(os.getenv("GEMINI_INPUT_COST_PER_1M", "0.075"))  # $0.075/1M
    gemini_output_cost_per_1m: float = float(os.getenv("GEMINI_OUTPUT_COST_PER_1M", "0.30"))  # $0.30/1M

    # Invite-only waitlist gate (IP-protection lockdown Phase 2, now default-open).
    # Master toggle: when False the gate is a no-op. Public/community-first signup
    # is the default; set WAITLIST_ENABLED=true only when the operator explicitly chooses
    # to re-close the front door.
    waitlist_enabled: bool = os.getenv("WAITLIST_ENABLED", "false").lower() == "true"
    # Where the "new access request" notification is sent (the operator — SES sandbox OK).
    waitlist_notify_email: str = os.getenv("WAITLIST_NOTIFY_EMAIL", "")
    # Verified SES sender identity.
    ses_sender_email: str = os.getenv("SES_SENDER_EMAIL", "")
    ses_region: str = os.getenv("SES_REGION", os.getenv("AWS_REGION", "us-west-2"))
    # HS256 secret for the one-click approve-link token. Real value from env.
    approve_link_secret: str = os.getenv("APPROVE_LINK_SECRET", "dev-approve-link-secret-change-me")
    # Base URL the approve link points at (canonical prod host).
    approve_link_base_url: str = os.getenv("APPROVE_LINK_BASE_URL", "https://paxai.app")
    # Always-approve FLOOR for the access gate: comma-separated emails AND
    # github-ids that are NEVER gated (owner safety net — a bad seed must never
    # lock the owner out). Parsed to a lower-cased set at gate time.
    access_always_approve: str = os.getenv(
        "ACCESS_ALWAYS_APPROVE",
        "",
    )
    # Lockdown-rollback alerting (SES, reuses waitlist sender/recipient).
    # Kill switch for both new-signup and dormant-return alert emails.
    signup_alerts_enabled: bool = os.getenv("SIGNUP_ALERTS_ENABLED", "false").lower() == "true"
    # A human user signing in after this many days of inactivity triggers an alert.
    dormant_alert_days: int = int(os.getenv("DORMANT_ALERT_DAYS", "14"))

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # Allow extra environment variables to be ignored


import logging as _logging

_logger = _logging.getLogger(__name__)

# Known weak default for the approve-link secret (FINDING 1).
_DEFAULT_APPROVE_LINK_SECRET = "dev-approve-link-secret-change-me"


def validate_security_settings(settings: "Settings") -> None:
    """Hard startup guard for security-critical settings.

    Raises ``RuntimeError`` if production is misconfigured. In non-production
    environments a weak/default approve-link secret is allowed (a warning is
    logged) so local dev keeps working.

    FINDING 1: production must not boot with an empty, default, or short
    (< 32 char) ``approve_link_secret`` when the waitlist/approve-link gate is
    enabled. Default-open signup does not use approve links, so it must not make
    production startup depend on approve-link secret provisioning.
    """
    secret = getattr(settings, "approve_link_secret", "") or ""
    waitlist_enabled = getattr(settings, "waitlist_enabled", True)

    if not waitlist_enabled:
        return

    if settings.environment == "production":
        if not secret or secret == _DEFAULT_APPROVE_LINK_SECRET or len(secret) < 32:
            raise RuntimeError(
                "APPROVE_LINK_SECRET is weak or unset in production. "
                "Set a strong (>= 32 char) APPROVE_LINK_SECRET that is not the "
                "known default before starting the service."
            )
    else:
        if not secret or secret == _DEFAULT_APPROVE_LINK_SECRET or len(secret) < 32:
            _logger.warning(
                "APPROVE_LINK_SECRET is weak/default (%s env) — acceptable for "
                "local development only; production startup will refuse this.",
                settings.environment,
            )


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get application settings"""
    return settings
