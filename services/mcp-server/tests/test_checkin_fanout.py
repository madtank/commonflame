"""Regression test for check-in prompt mention fan-out.

The _build_ax_checkin_prompt helper is a template for a future private
concierge briefing endpoint. It must not be posted as a real channel
message. This test still guards the template because any future accidental
use of the shared message pipeline would otherwise let @handle tokens inside
Context / Recent inbox blocks become real dispatch recipients.

See: orion/task-checkin-fanout-fix
"""

import re
import unittest

from fastmcp_server.tools.messages import _build_ax_checkin_prompt


# ── Mention extraction copied from ax-backend MentionsService ──
# This must stay in sync with the backend regex to catch regressions.
_CODE_FENCE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_INLINE_CODE = re.compile(r"`[^`]+`")
_MENTION = re.compile(r"(?<![a-zA-Z0-9])@([\w-]+)(?=\s|$|[^\w-])")


def _extract_mentions(text: str) -> set[str]:
    """Simulate backend MentionsService.parse_mentions."""
    stripped = _CODE_FENCE.sub("", text)
    stripped = _INLINE_CODE.sub("", stripped)
    return {m.lower() for m in _MENTION.findall(stripped) if m}


def _fake_inbox(actors: list[str]) -> dict:
    return {
        "count": len(actors),
        "unread_count": 0,
        "messages": [
            {
                "id": f"m-{i}",
                "content": f"working on task {i}",
                "sender_name": actor,
                "agent_name": actor,
                "public_actor_label": actor,
                "role": "agent",
                "created_at": f"2026-04-11T23:{i:02d}:00+00:00",
            }
            for i, actor in enumerate(actors)
        ],
    }


class CheckinFanoutTests(unittest.TestCase):
    """Verify that check-in prompts don't fan out to inbox actors."""

    def test_only_routing_mentions_survive(self):
        """Context/Recent inbox @handles must NOT leak past the code fence."""
        inbox = _fake_inbox([
            "backend_sentinel",
            "frontend_sentinel",
            "mcp_sentinel",
            "orion",
        ])
        prompt = _build_ax_checkin_prompt(
            "ChatGPT", inbox, reason="pre-PR check", status=None,
        )
        mentions = _extract_mentions(prompt)
        # Only the explicit routing line should survive
        self.assertIn("ax", mentions, "Commonflame routing mention missing")
        self.assertIn("chatgpt", mentions, "requester mention missing")
        # Sentinel handles must NOT appear
        for handle in ("backend_sentinel", "frontend_sentinel", "mcp_sentinel", "orion"):
            self.assertNotIn(
                handle,
                mentions,
                f"@{handle} leaked out of the code fence — fan-out bug!",
            )

    def test_with_agent_status(self):
        """Same assertion when status= is provided."""
        inbox = _fake_inbox(["relay", "anvil"])
        prompt = _build_ax_checkin_prompt(
            "night_owl", inbox, reason="daily check", status="idle",
        )
        mentions = _extract_mentions(prompt)
        self.assertIn("ax", mentions)
        self.assertIn("night_owl", mentions)
        self.assertNotIn("relay", mentions)
        self.assertNotIn("anvil", mentions)

    def test_empty_inbox(self):
        """Empty inbox produces no extra mentions."""
        inbox = {"count": 0, "unread_count": 0, "messages": []}
        prompt = _build_ax_checkin_prompt(
            "orion", inbox, reason="boot check", status=None,
        )
        mentions = _extract_mentions(prompt)
        self.assertEqual(mentions, {"ax", "orion"})


if __name__ == "__main__":
    unittest.main()
