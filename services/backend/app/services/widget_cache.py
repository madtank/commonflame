import hashlib


def widget_cache_key(
    *,
    message_id: str,
    resource_uri: str,
    space_id: str,
    tool_call_id: str | None = None,
) -> str:
    """Build a stable widget cache key.

    Some messages carry multiple widgets that share the same resource_uri, so
    tool_call_id must participate in the cache key when present.
    """
    cache_hash = hashlib.md5(
        f"{message_id}:{resource_uri}:{tool_call_id or ''}:{space_id}".encode()
    ).hexdigest()
    return f"ax:widget-cache:{cache_hash}"
