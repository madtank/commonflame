"""Provider-independent built-in account configuration."""
import os
from urllib.parse import urlparse


def builtin_auth_enabled() -> bool:
    # "local" is a compatibility alias, not a deployment boundary.
    return os.getenv("AUTH_MODE", "builtin").strip().lower() in {"builtin", "local"}


def local_browser_setup_enabled() -> bool:
    """Use operator configuration, never a request Host/forwarded header."""
    origin = urlparse(os.getenv("PUBLIC_URL", os.getenv("FRONTEND_URL", "http://localhost:3000")))
    return (origin.scheme in {"http", "https"}
            and origin.hostname in {"localhost", "127.0.0.1", "::1"}
            and not origin.username and not origin.password
            and origin.path in {"", "/"} and not origin.query and not origin.fragment)


def registration_mode() -> str:
    mode = os.getenv("REGISTRATION_MODE", "auto").strip().lower()
    if mode == "auto":
        return "open" if local_browser_setup_enabled() else "invite_only"
    # A typo must never accidentally open a hosted installation.
    return mode if mode in {"open", "invite_only", "closed"} else "closed"
