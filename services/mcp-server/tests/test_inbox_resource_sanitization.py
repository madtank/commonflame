"""Regression tests for inbox Markdown resource sanitization."""

from fastmcp_server.resources.inbox import escape_markdown_text


def test_escape_markdown_text_escapes_html_tags_and_markdown_links():
    rendered = escape_markdown_text('<img src=x onerror=alert(1)> [click](javascript:alert(1))')

    assert "<img" not in rendered
    assert "onerror" in rendered
    assert r"&lt;img src=x onerror=alert\(1\)&gt;" in rendered
    assert "[click](javascript:alert(1))" not in rendered
    assert r"\[click\]\(javascript:alert\(1\)\)" in rendered


def test_escape_markdown_text_preserves_plain_text_readability():
    rendered = escape_markdown_text("hello world: 2026-06-06T01:02:03Z")

    assert rendered == "hello world: 2026\\-06\\-06T01:02:03Z"
