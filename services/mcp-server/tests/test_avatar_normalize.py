"""Unit tests for _normalize_avatar (agents tool avatar support over MCP).

Covers the agent-friendly input forms and the security posture:
emoji -> inline SVG data URI, raw <svg> wrap, http(s)/data:image passthrough,
empty-clears, dual-input conflict, unsafe scheme rejection, XML escaping.
"""

import base64

import pytest

from fastmcp_server.tools.avatar import normalize_avatar as _normalize_avatar


def _decode_svg(uri: str) -> str:
    assert uri.startswith("data:image/svg+xml;base64,")
    return base64.b64decode(uri.split(",", 1)[1]).decode("utf-8")


def test_emoji_renders_to_svg_data_uri():
    uri = _normalize_avatar(None, "🍑")
    svg = _decode_svg(uri)
    assert "🍑" in svg
    assert svg.startswith("<svg")


def test_multi_codepoint_emoji_not_truncated():
    family = "👨‍👩‍👧‍👦"  # ZWJ family: 7 code points
    svg = _decode_svg(_normalize_avatar(None, family))
    assert family in svg  # never sliced mid-cluster


def test_emoji_text_paragraph_rejected_not_truncated():
    with pytest.raises(ValueError):
        _normalize_avatar(None, "this is a sentence not an emoji")


def test_emoji_input_is_xml_escaped():
    svg = _decode_svg(_normalize_avatar(None, "<&>"))
    assert "<&>" not in svg
    assert "&lt;&amp;&gt;" in svg


def test_empty_emoji_rejected():
    with pytest.raises(ValueError):
        _normalize_avatar(None, "   ")


def test_raw_svg_markup_wrapped():
    uri = _normalize_avatar("<svg xmlns='http://www.w3.org/2000/svg'><circle r='4'/></svg>", None)
    assert "<circle" in _decode_svg(uri)


def test_uppercase_svg_root_rejected_with_actionable_error():
    # XML is case-sensitive: wrapping "<SVG>" would render a broken image.
    with pytest.raises(ValueError, match="lowercase"):
        _normalize_avatar("<SVG></SVG>", None)


def test_http_https_and_data_image_passthrough():
    assert _normalize_avatar("https://cdn.example.com/a.png", None) == "https://cdn.example.com/a.png"
    assert _normalize_avatar("http://cdn.example.com/a.png", None) == "http://cdn.example.com/a.png"
    uri = "data:image/png;base64,AAAA"
    assert _normalize_avatar(uri, None) == uri


def test_empty_string_clears_and_none_means_untouched():
    assert _normalize_avatar("", None) == ""
    assert _normalize_avatar(None, None) is None


def test_dual_input_conflict_is_explicit():
    with pytest.raises(ValueError, match="not both"):
        _normalize_avatar("https://cdn.example.com/a.png", "🍑")


@pytest.mark.parametrize(
    "bad",
    [
        "javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",  # not image/
        "ftp://example.com/a.png",
        "/relative/path.png",
        "just some text",
    ],
)
def test_unsafe_or_non_image_inputs_rejected(bad):
    with pytest.raises(ValueError):
        _normalize_avatar(bad, None)
