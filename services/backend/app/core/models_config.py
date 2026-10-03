"""
Dynamic LLM Models Configuration

This is the SINGLE SOURCE OF TRUTH for available cloud agent models.
Frontend and agent_runner should fetch from /api/v1/agents/models endpoint.

All 42 model IDs verified working on us-west-2 on-demand via converse() (2026-03-12).
Source: ax-agents-extract/space_agent/config.py BEDROCK_MODELS dict.

To add a new model:
1. Add entry to AVAILABLE_MODELS below
2. That's it! Frontend will automatically show it, validation will work.
"""

from typing import Literal

# Model tier requirements
TierRequirement = Literal["free", "plus", "admin"]

# Available models configuration
# Key: short alias (stored in database)
# Value: model metadata including bedrock_id for dispatch
AVAILABLE_MODELS: dict[str, dict] = {
    # ── Anthropic Claude ─────────────────────────────────────────────
    "sonnet-4.6": {
        "name": "Claude Sonnet 4.6",
        "bedrock_id": "us.anthropic.claude-sonnet-4-6",
        "provider": "Anthropic",
        "tier_required": "free",
        "is_default": True,
        "sort_order": 1,
    },
    "sonnet-4.5": {
        "name": "Claude Sonnet 4.5",
        "bedrock_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "provider": "Anthropic",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 2,
    },
    "sonnet-4": {
        "name": "Claude Sonnet 4",
        "bedrock_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
        "provider": "Anthropic",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 3,
    },
    "haiku-4.5": {
        "name": "Claude Haiku 4.5",
        "bedrock_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "provider": "Anthropic",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 4,
    },
    "haiku-3": {
        "name": "Claude Haiku 3",
        "bedrock_id": "us.anthropic.claude-3-haiku-20240307-v1:0",
        "provider": "Anthropic",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 5,
    },
    "opus-4.6": {
        "name": "Claude Opus 4.6",
        "bedrock_id": "us.anthropic.claude-opus-4-6-v1",
        "provider": "Anthropic",
        "tier_required": "plus",
        "is_default": False,
        "sort_order": 6,
    },
    "opus-4.5": {
        "name": "Claude Opus 4.5",
        "bedrock_id": "us.anthropic.claude-opus-4-5-20251101-v1:0",
        "provider": "Anthropic",
        "tier_required": "plus",
        "is_default": False,
        "sort_order": 7,
    },
    "opus-4.1": {
        "name": "Claude Opus 4.1",
        "bedrock_id": "us.anthropic.claude-opus-4-1-20250805-v1:0",
        "provider": "Anthropic",
        "tier_required": "plus",
        "is_default": False,
        "sort_order": 8,
    },
    # ── Amazon Nova ──────────────────────────────────────────────────
    "nova-micro": {
        "name": "Amazon Nova Micro",
        "bedrock_id": "us.amazon.nova-micro-v1:0",
        "provider": "Amazon",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 10,
    },
    "nova-lite": {
        "name": "Amazon Nova Lite",
        "bedrock_id": "us.amazon.nova-lite-v1:0",
        "provider": "Amazon",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 11,
    },
    "nova-pro": {
        "name": "Amazon Nova Pro",
        "bedrock_id": "us.amazon.nova-pro-v1:0",
        "provider": "Amazon",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 12,
    },
    "nova-2-lite": {
        "name": "Amazon Nova 2 Lite",
        "bedrock_id": "us.amazon.nova-2-lite-v1:0",
        "provider": "Amazon",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 13,
    },
    "nova-premier": {
        "name": "Amazon Nova Premier",
        "bedrock_id": "us.amazon.nova-premier-v1:0",
        "provider": "Amazon",
        "tier_required": "plus",
        "is_default": False,
        "sort_order": 14,
    },
    # ── Meta Llama ───────────────────────────────────────────────────
    "llama-4-scout": {
        "name": "Llama 4 Scout",
        "bedrock_id": "us.meta.llama4-scout-17b-instruct-v1:0",
        "provider": "Meta",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 20,
    },
    "llama-4-maverick": {
        "name": "Llama 4 Maverick",
        "bedrock_id": "us.meta.llama4-maverick-17b-instruct-v1:0",
        "provider": "Meta",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 21,
    },
    "llama-3.3-70b": {
        "name": "Llama 3.3 70B",
        "bedrock_id": "us.meta.llama3-3-70b-instruct-v1:0",
        "provider": "Meta",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 22,
    },
    "llama-3.2-90b": {
        "name": "Llama 3.2 90B",
        "bedrock_id": "us.meta.llama3-2-90b-instruct-v1:0",
        "provider": "Meta",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 23,
    },
    "llama-3.1-70b": {
        "name": "Llama 3.1 70B",
        "bedrock_id": "us.meta.llama3-1-70b-instruct-v1:0",
        "provider": "Meta",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 24,
    },
    "llama-3.1-8b": {
        "name": "Llama 3.1 8B",
        "bedrock_id": "us.meta.llama3-1-8b-instruct-v1:0",
        "provider": "Meta",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 25,
    },
    # ── Moonshot AI (Kimi) ───────────────────────────────────────────
    "kimi-k2.5": {
        "name": "Kimi K2.5",
        "bedrock_id": "moonshotai.kimi-k2.5",
        "provider": "Moonshot AI",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 30,
    },
    # ── DeepSeek ─────────────────────────────────────────────────────
    "deepseek-v3": {
        "name": "DeepSeek V3",
        "bedrock_id": "deepseek.v3-v1:0",
        "provider": "DeepSeek",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 31,
    },
    "deepseek-v3.2": {
        "name": "DeepSeek V3.2",
        "bedrock_id": "deepseek.v3.2",
        "provider": "DeepSeek",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 32,
    },
    # ── Mistral AI ───────────────────────────────────────────────────
    "mistral-large-3": {
        "name": "Mistral Large 3",
        "bedrock_id": "mistral.mistral-large-3-675b-instruct",
        "provider": "Mistral",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 40,
    },
    "magistral-small": {
        "name": "Magistral Small",
        "bedrock_id": "mistral.magistral-small-2509",
        "provider": "Mistral",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 41,
    },
    "devstral-2": {
        "name": "Devstral 2",
        "bedrock_id": "mistral.devstral-2-123b",
        "provider": "Mistral",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 42,
    },
    "ministral-14b": {
        "name": "Ministral 14B",
        "bedrock_id": "mistral.ministral-3-14b-instruct",
        "provider": "Mistral",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 43,
    },
    "ministral-8b": {
        "name": "Ministral 8B",
        "bedrock_id": "mistral.ministral-3-8b-instruct",
        "provider": "Mistral",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 44,
    },
    # ── Qwen ─────────────────────────────────────────────────────────
    "qwen3-235b": {
        "name": "Qwen3 235B",
        "bedrock_id": "qwen.qwen3-235b-a22b-2507-v1:0",
        "provider": "Qwen",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 50,
    },
    "qwen3-32b": {
        "name": "Qwen3 32B",
        "bedrock_id": "qwen.qwen3-32b-v1:0",
        "provider": "Qwen",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 51,
    },
    "qwen3-coder-480b": {
        "name": "Qwen3 Coder 480B",
        "bedrock_id": "qwen.qwen3-coder-480b-a35b-v1:0",
        "provider": "Qwen",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 52,
    },
    "qwen3-coder-30b": {
        "name": "Qwen3 Coder 30B",
        "bedrock_id": "qwen.qwen3-coder-30b-a3b-v1:0",
        "provider": "Qwen",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 53,
    },
    "qwen3-next-80b": {
        "name": "Qwen3 Next 80B",
        "bedrock_id": "qwen.qwen3-next-80b-a3b",
        "provider": "Qwen",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 54,
    },
    # ── Google (Gemma) ───────────────────────────────────────────────
    "gemma-3-27b": {
        "name": "Gemma 3 27B",
        "bedrock_id": "google.gemma-3-27b-it",
        "provider": "Google",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 60,
    },
    "gemma-3-12b": {
        "name": "Gemma 3 12B",
        "bedrock_id": "google.gemma-3-12b-it",
        "provider": "Google",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 61,
    },
    "gemma-3-4b": {
        "name": "Gemma 3 4B",
        "bedrock_id": "google.gemma-3-4b-it",
        "provider": "Google",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 62,
    },
    # ── NVIDIA ───────────────────────────────────────────────────────
    "nemotron-nano-30b": {
        "name": "Nemotron Nano 30B",
        "bedrock_id": "nvidia.nemotron-nano-3-30b",
        "provider": "NVIDIA",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 70,
    },
    "nemotron-nano-12b": {
        "name": "Nemotron Nano 12B",
        "bedrock_id": "nvidia.nemotron-nano-12b-v2",
        "provider": "NVIDIA",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 71,
    },
    "nemotron-nano-9b": {
        "name": "Nemotron Nano 9B",
        "bedrock_id": "nvidia.nemotron-nano-9b-v2",
        "provider": "NVIDIA",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 72,
    },
    # ── Z.AI (GLM) ──────────────────────────────────────────────────
    "glm-4.7": {
        "name": "GLM 4.7",
        "bedrock_id": "zai.glm-4.7",
        "provider": "Z.AI",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 80,
    },
    "glm-4.7-flash": {
        "name": "GLM 4.7 Flash",
        "bedrock_id": "zai.glm-4.7-flash",
        "provider": "Z.AI",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 81,
    },
    # ── Cohere ───────────────────────────────────────────────────────
    "command-r-plus": {
        "name": "Command R+",
        "bedrock_id": "cohere.command-r-plus-v1:0",
        "provider": "Cohere",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 90,
    },
    "command-r": {
        "name": "Command R",
        "bedrock_id": "cohere.command-r-v1:0",
        "provider": "Cohere",
        "tier_required": "free",
        "is_default": False,
        "sort_order": 91,
    },
}

# Default model ID (short alias stored in DB)
DEFAULT_MODEL = "sonnet-4.6"

# Role hierarchy for permission checks (based on user.role, NOT org.tier)
# User roles: "user" (default), "plus", "admin"
ROLE_HIERARCHY = {
    "user": 0,        # Default free tier
    "free": 0,        # Alias
    "plus": 1,        # Paid subscription
    "admin": 2,       # Full access
}


def resolve_bedrock_model_id(alias: str) -> str:
    """Resolve a short model alias to the full Bedrock model ID.

    Falls back to the alias itself if not found (supports passing
    raw Bedrock IDs through unchanged).
    """
    model_info = AVAILABLE_MODELS.get(alias)
    if model_info:
        return model_info["bedrock_id"]
    return alias


def get_available_models() -> dict[str, dict]:
    """Get all available models with metadata."""
    return AVAILABLE_MODELS


def get_default_model() -> str:
    """Get the default model ID."""
    return DEFAULT_MODEL


def get_models_for_user_role(user_role: str) -> list[dict]:
    """Get models available for a user's role, sorted by sort_order.

    Args:
        user_role: The user's role ("user", "plus", "admin")

    Returns list of model info dicts with 'id' field added.
    """
    role_level = ROLE_HIERARCHY.get(user_role.lower(), 0)

    available = []
    for model_id, model_info in AVAILABLE_MODELS.items():
        required_level = ROLE_HIERARCHY.get(model_info["tier_required"], 0)
        if role_level >= required_level:
            available.append({
                "id": model_id,
                **model_info,
                "available": True,
            })
        else:
            # Include but mark as unavailable (for UI to show locked state)
            available.append({
                "id": model_id,
                **model_info,
                "available": False,
            })

    return sorted(available, key=lambda m: m["sort_order"])


# Keep old name for backward compatibility during transition
get_models_for_tier = get_models_for_user_role


def validate_model_for_user_role(model: str, user_role: str) -> tuple[bool, str | None]:
    """Validate model selection based on user's role.

    Args:
        model: The model ID to validate
        user_role: The user's role ("user", "plus", "admin")

    Returns (is_valid, error_message).
    """
    if model not in AVAILABLE_MODELS:
        valid_models = ", ".join(sorted(AVAILABLE_MODELS.keys()))
        return False, f"Invalid model '{model}'. Valid models: {valid_models}"

    model_info = AVAILABLE_MODELS[model]
    required_tier = model_info["tier_required"]

    user_role_level = ROLE_HIERARCHY.get(user_role.lower(), 0)
    required_level = ROLE_HIERARCHY.get(required_tier, 0)

    if user_role_level < required_level:
        return False, f"🚀 {model_info['name']} is a Plus feature! Visit ax-platform.com or join our Discord to upgrade."

    return True, None


# Keep old name for backward compatibility during transition
validate_model_for_tier = validate_model_for_user_role


def is_valid_model(model: str) -> bool:
    """Check if a model ID is valid."""
    return model in AVAILABLE_MODELS
