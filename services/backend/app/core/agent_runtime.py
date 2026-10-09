"""Runtime labels describe managed cloud execution, not OAuth identity defaults."""
from app.core.models_config import AVAILABLE_MODELS, DEFAULT_MODEL


def display_model(agent) -> str | None:
    # The database default populates external identities too; it does not
    # attest to the model (or even existence of a model) in an independent host.
    if agent.origin not in (None, "cloud"):
        return None
    return agent.model or DEFAULT_MODEL


def display_model_tier(agent) -> str | None:
    model = display_model(agent)
    return AVAILABLE_MODELS.get(model, {}).get("tier_required", "free") if model else None
