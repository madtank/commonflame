"""Shared avatar normalization helpers for MCP identity/profile tools."""

from __future__ import annotations

import base64
from html import escape as html_escape
from typing import Optional


def normalize_avatar(avatar_url: Optional[str], avatar_emoji: Optional[str]) -> Optional[str]:
    """Return a backend-safe avatar_url from agent-friendly inputs.

    Friendly forms:
      - avatar_emoji="🍑"        -> rendered into a tiny SVG, shipped as a
                                    data:image/svg+xml;base64 URI (no hosting).
      - avatar_url="<svg ...>"   -> raw SVG markup auto-wrapped the same way.
      - avatar_url="https://..." -> passed through.
      - avatar_url=""            -> clears the avatar.
    Exactly one of avatar_emoji / avatar_url may be provided. Only http(s)
    URLs and data:image/ URIs ever leave this function — the frontend renders
    avatar_url as an <img src>, so other schemes are refused.
    """
    if avatar_emoji is not None and avatar_url is not None:
        raise ValueError("Pass either avatar_emoji or avatar_url, not both.")

    if avatar_emoji is not None:
        glyph = avatar_emoji.strip()
        if not glyph:
            raise ValueError('avatar_emoji is empty; pass an emoji like avatar_emoji="🍑".')
        # Length is a LIMIT, not a truncation: slicing code points could split a
        # multi-codepoint emoji (ZWJ families, flags, skin tones) mid-cluster.
        if len(glyph) > 16:
            raise ValueError("avatar_emoji should be a single emoji (or a couple of glyphs), not text.")
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
            f"<text x='50' y='54' text-anchor='middle' dominant-baseline='central' font-size='80'>{html_escape(glyph)}</text>"
            "</svg>"
        )
        return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")

    if avatar_url is None:
        return None
    s = avatar_url.strip()
    if s == "":
        return ""  # explicit clear
    if s.lower().startswith("<svg"):
        # XML is case-sensitive: wrapping "<SVG ...>" would yield a data URI that
        # silently fails to render, so require lowercase markup explicitly.
        if not s.startswith("<svg"):
            raise ValueError("SVG markup must use a lowercase <svg ...> root (XML is case-sensitive).")
        return "data:image/svg+xml;base64," + base64.b64encode(s.encode("utf-8")).decode("ascii")
    if s.startswith(("http://", "https://", "data:image/")):
        return s
    raise ValueError(
        "avatar_url must be an http(s) URL, a data:image/ URI, raw '<svg ...>' markup, "
        "or empty to clear (or use avatar_emoji)."
    )
