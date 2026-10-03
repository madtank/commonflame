"""
Shared validators for the aX Platform
"""
import re
from typing import Optional


def validate_topic(topic: Optional[str]) -> Optional[str]:
    """
    Validate topic field for context items.

    Topic must contain only lowercase letters, numbers, hyphens, and underscores.
    Uses fullmatch for exact matching (not partial).

    Args:
        topic: Topic string to validate (or None)

    Returns:
        Validated topic or None

    Raises:
        ValueError: If topic contains invalid characters
    """
    if topic is not None:
        if len(topic) > 50:
            raise ValueError('Topic must be at most 50 characters long')
        if not re.fullmatch(r'[a-z0-9_-]+', topic):
            raise ValueError('Topic must contain only lowercase letters, numbers, hyphens, and underscores')
    return topic
