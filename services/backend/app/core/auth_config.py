"""Provider-independent built-in account configuration."""
import os


def builtin_auth_enabled() -> bool:
    # "local" is a compatibility alias, not a deployment boundary.
    return os.getenv("AUTH_MODE", "builtin").strip().lower() in {"builtin", "local"}
