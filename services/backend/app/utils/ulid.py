"""Utility helpers for generating ULIDs for monotonic cursors without external deps."""

# @ax:tag area=backend component=ulid_utils tech=python guide=backend/AGENT.md

from __future__ import annotations

import time
from random import SystemRandom
from threading import Lock
from typing import Callable

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_RANDOM = SystemRandom()
_LOCK = Lock()
_LAST_TIMESTAMP = -1
_LAST_RANDOM = 0


def _encode(value: int, length: int) -> str:
    chars = ["0"] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def generate_ulid() -> str:
    """Return a new lexicographically sortable ULID string."""

    global _LAST_TIMESTAMP, _LAST_RANDOM

    with _LOCK:
        timestamp = int(time.time() * 1000)
        if timestamp == _LAST_TIMESTAMP:
            _LAST_RANDOM = (_LAST_RANDOM + 1) & ((1 << 80) - 1)
        else:
            _LAST_TIMESTAMP = timestamp
            _LAST_RANDOM = _RANDOM.getrandbits(80)

        encoded_time = _encode(timestamp & ((1 << 48) - 1), 10)
        encoded_random = _encode(_LAST_RANDOM, 16)

    return encoded_time + encoded_random


def ulid_factory() -> Callable[[], str]:
    """Return a factory compatible with SQLAlchemy default callbacks."""

    return generate_ulid
