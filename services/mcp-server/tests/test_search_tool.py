import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastmcp import FastMCP

from fastmcp_server.tools.search import register_search_tool


class SearchToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.mcp = FastMCP("test")
        register_search_tool(self.mcp)
        self.tool = await self.mcp.get_tool("search")
        self.token = SimpleNamespace(
            token="jwt",
            claims={"agent_name": "protocol", "space_id": "space-1", "agent_id": "agent-test"},
        )
        self.request = SimpleNamespace(headers={})

    async def _call_tool(self, **kwargs):
        return await self.tool.fn(
            token=self.token,
            request=self.request,
            **kwargs,
        )

    async def test_search_returns_v2_envelope_with_snippets(self) -> None:
        long_content = "Line " * 100
        with patch(
            "fastmcp_server.tools.search.api_request",
            new=AsyncMock(
                return_value={
                    "results": [
                        {
                            "id": "msg-1",
                            "content": long_content,
                            "score": 0.9,
                        }
                    ],
                    "count": 1,
                    "total": 3,
                }
            ),
        ):
            result = await self._call_tool(query="oauth", limit=10, offset=0)

        structured = result.structured_content
        self.assertEqual(structured["kind"], "search_results")
        self.assertEqual(structured["version"], 2)
        self.assertEqual(structured["state"], "ready")
        self.assertIn("data", structured)
        self.assertIn("actions", structured)
        item = structured["data"]["items"][0]
        self.assertNotIn("content", item)
        self.assertIn("snippet", item)
        self.assertLess(len(item["snippet"]), len(long_content))
        self.assertTrue(structured["data"]["has_more"])


if __name__ == "__main__":
    unittest.main()
