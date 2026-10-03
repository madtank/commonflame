import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastmcp import FastMCP

from fastmcp_server.mcp_ui import EXPERIMENTAL_GAMES_FLAG
from fastmcp_server.tools.games import register_games_tool


class GamesToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.games_flag = patch.dict(os.environ, {EXPERIMENTAL_GAMES_FLAG: "true"}, clear=False)
        self.games_flag.start()
        self.addCleanup(self.games_flag.stop)
        self.mcp = FastMCP("test")
        register_games_tool(self.mcp)
        self.tool = await self.mcp.get_tool("games")
        self.token = SimpleNamespace(
            token="jwt",
            claims={"agent_name": "orion", "space_id": "space-1"},
        )
        self.request = SimpleNamespace(headers={})

    async def _call_tool(self, **kwargs):
        return await self.tool.fn(
            token=self.token,
            request=self.request,
            **kwargs,
        )

    async def test_create_returns_game_widget_and_stores_context_state(self) -> None:
        with patch(
            "fastmcp_server.tools.games.api_request_with_context",
            new=AsyncMock(return_value={"status": "ok"}),
        ) as api_request:
            result = await self._call_tool(
                action="create",
                game_id="game-1",
                player_x="orion",
                player_o="mcp_sentinel",
            )

        structured = result.structured_content
        self.assertEqual(structured["kind"], "game_state")
        self.assertEqual(structured["version"], 1)
        self.assertEqual(structured["state"], "active")
        self.assertEqual(result.meta["ui"]["resourceUri"], "ui://games/tic-tac-toe")
        game = structured["data"]["game"]
        self.assertEqual(game["game_id"], "game-1")
        self.assertEqual(game["context_key"], "game:tic_tac_toe:game-1")
        self.assertEqual(game["players"][0]["handle"], "orion")
        self.assertEqual(game["players"][1]["handle"], "mcp_sentinel")
        self.assertEqual(structured["notice"]["code"], "game_created")

        first_call = api_request.await_args_list[0]
        self.assertEqual(first_call.args[1:], ("POST", "/api/v1/context"))
        payload = first_call.kwargs["json_data"]
        self.assertEqual(payload["key"], "game:tic_tac_toe:game-1")
        self.assertEqual(payload["topic"], "game")
        self.assertEqual(payload["value"]["board"], [[None, None, None], [None, None, None], [None, None, None]])

    async def test_move_applies_legal_move_and_persists_updated_state(self) -> None:
        existing_state = {
            "kind": "game_state",
            "game": "tic_tac_toe",
            "version": 1,
            "game_id": "game-1",
            "context_key": "game:tic_tac_toe:game-1",
            "players": [
                {"mark": "X", "id": "orion", "handle": "orion"},
                {"mark": "O", "id": "mcp_sentinel", "handle": "mcp_sentinel"},
            ],
            "turn": {"mark": "X", "player": "orion"},
            "board": [[None, None, None], [None, None, None], [None, None, None]],
            "status": "active",
            "winner": None,
            "winning_line": [],
            "move_number": 0,
            "events": [],
        }
        with patch(
            "fastmcp_server.tools.games.api_request_with_context",
            new=AsyncMock(
                side_effect=[
                    {"key": "game:tic_tac_toe:game-1", "value": {"value": existing_state}},
                    {"status": "ok"},
                ]
            ),
        ) as api_request:
            result = await self._call_tool(
                action="move",
                game_id="game-1",
                cell="b2",
                player="orion",
            )

        game = result.structured_content["data"]["game"]
        self.assertEqual(game["board"][1][1], "X")
        self.assertEqual(game["turn"], {"mark": "O", "player": "mcp_sentinel"})
        self.assertEqual(game["move_number"], 1)
        self.assertEqual(game["events"][0]["type"], "move_applied")
        self.assertEqual(result.structured_content["notice"]["code"], "move_applied")

        store_call = api_request.await_args_list[1]
        self.assertEqual(store_call.args[1:], ("POST", "/api/v1/context"))
        self.assertEqual(store_call.kwargs["json_data"]["value"]["board"][1][1], "X")

    async def test_create_trivia_loads_context_source_and_hides_answer_key(self) -> None:
        source_value = {
            "query": "MCP app signal cards",
            "summary": "# MCP Apps\n\nWidgets use shared context and HITL drafts.",
            "links": ["https://example.test/spec"],
            "tool": "web_fetch",
        }
        with patch(
            "fastmcp_server.tools.games.api_request_with_context",
            new=AsyncMock(
                side_effect=[
                    {"key": "research:mcp-apps", "value": {"value": source_value}},
                    {"status": "ok"},
                ]
            ),
        ) as api_request:
            result = await self._call_tool(
                action="create_trivia",
                game_id="trivia-1",
                source_key="research:mcp-apps",
            )

        structured = result.structured_content
        game = structured["data"]["game"]
        self.assertEqual(game["game"], "ax_trivia")
        self.assertEqual(game["context_key"], "game:ax_trivia:trivia-1")
        self.assertEqual(game["source"]["key"], "research:mcp-apps")
        self.assertEqual(game["source"]["title"], "MCP app signal cards")
        self.assertNotIn("answer_index", game["questions"][0])
        self.assertNotIn("answer_index", structured["data"]["current_question"])

        self.assertEqual(api_request.await_args_list[0].args[1:], ("GET", "/api/v1/context/research%3Amcp-apps"))
        store_payload = api_request.await_args_list[1].kwargs["json_data"]
        self.assertEqual(store_payload["key"], "game:ax_trivia:trivia-1")
        self.assertIn("answer_index", store_payload["value"]["questions"][0])

    async def test_answer_trivia_scores_and_persists_updated_state(self) -> None:
        existing_state = {
            "kind": "game_state",
            "game": "ax_trivia",
            "version": 1,
            "game_id": "trivia-1",
            "context_key": "game:ax_trivia:trivia-1",
            "players": [{"id": "orion", "handle": "orion", "display_name": "orion"}],
            "turn": {"player": "orion"},
            "status": "active",
            "score": 0,
            "current_question_index": 0,
            "questions": [
                {
                    "id": "q1",
                    "prompt": "What renders the game?",
                    "choices": ["MCP Apps", "DNS", "Email", "Cron"],
                    "answer_index": 0,
                    "explanation": "MCP Apps render widgets.",
                }
            ],
            "answers": [],
            "events": [],
        }
        with patch(
            "fastmcp_server.tools.games.api_request_with_context",
            new=AsyncMock(
                side_effect=[
                    {"error": "not found"},
                    {"key": "game:ax_trivia:trivia-1", "value": {"value": existing_state}},
                    {"status": "ok"},
                ]
            ),
        ) as api_request:
            result = await self._call_tool(action="answer", game_id="trivia-1", choice=0)

        game = result.structured_content["data"]["game"]
        self.assertEqual(game["status"], "complete")
        self.assertEqual(game["score"], 1)
        self.assertEqual(game["answers"][0]["correct"], True)
        self.assertNotIn("answer_index", game["questions"][0])
        store_call = api_request.await_args_list[2]
        self.assertEqual(store_call.kwargs["json_data"]["value"]["score"], 1)
        self.assertEqual(store_call.kwargs["json_data"]["value"]["answers"][0]["choice"], "MCP Apps")

    async def test_move_rejects_occupied_cell_without_persisting(self) -> None:
        existing_state = {
            "game_id": "game-1",
            "context_key": "game:tic_tac_toe:game-1",
            "players": [
                {"mark": "X", "id": "orion", "handle": "orion"},
                {"mark": "O", "id": "mcp_sentinel", "handle": "mcp_sentinel"},
            ],
            "turn": {"mark": "O", "player": "mcp_sentinel"},
            "board": [["X", None, None], [None, None, None], [None, None, None]],
            "status": "active",
            "events": [],
        }
        with patch(
            "fastmcp_server.tools.games.api_request_with_context",
            new=AsyncMock(return_value={"key": "game:tic_tac_toe:game-1", "value": {"value": existing_state}}),
        ) as api_request:
            result = await self._call_tool(
                action="move",
                game_id="game-1",
                cell="a1",
                player="mcp_sentinel",
            )

        self.assertEqual(result.structured_content["notice"]["code"], "cell_occupied")
        self.assertEqual(result.structured_content["notice"]["severity"], "error")
        self.assertEqual(api_request.await_count, 1)

    async def test_move_detects_winner(self) -> None:
        existing_state = {
            "game_id": "game-1",
            "context_key": "game:tic_tac_toe:game-1",
            "players": [
                {"mark": "X", "id": "orion", "handle": "orion"},
                {"mark": "O", "id": "mcp_sentinel", "handle": "mcp_sentinel"},
            ],
            "turn": {"mark": "X", "player": "orion"},
            "board": [["X", "X", None], ["O", None, None], ["O", None, None]],
            "status": "active",
            "winner": None,
            "winning_line": [],
            "move_number": 4,
            "events": [],
        }
        with patch(
            "fastmcp_server.tools.games.api_request_with_context",
            new=AsyncMock(
                side_effect=[
                    {"key": "game:tic_tac_toe:game-1", "value": {"value": existing_state}},
                    {"status": "ok"},
                ]
            ),
        ):
            result = await self._call_tool(
                action="move",
                game_id="game-1",
                cell="c1",
                player="orion",
            )

        game = result.structured_content["data"]["game"]
        self.assertEqual(game["status"], "complete")
        self.assertEqual(game["winner"], "X")
        self.assertEqual(game["winning_line"], ["a1", "b1", "c1"])
        self.assertEqual(result.structured_content["notice"]["code"], "game_won")

    async def test_user_create_adds_explicit_space_body_override(self) -> None:
        self.token.claims = {
            "sub": "user-1",
            "client_id": "frontend-client",
            "space_id": "stale-space",
        }
        self.request = SimpleNamespace(
            headers={
                "x-agent-name": "Waystation",
                "x-space-id": "current-ui-space",
            }
        )
        with (
            patch(
                "fastmcp_server.api_client._FRONTEND_CLIENT_IDS",
                frozenset({"frontend-client"}),
            ),
            patch(
                "fastmcp_server.tools.games.api_request_with_context",
                new=AsyncMock(return_value={"status": "ok"}),
            ) as api_request,
        ):
            await self._call_tool(
                action="create",
                game_id="game-1",
                player_x="madtank",
                player_o="orion",
            )

        payload = api_request.await_args_list[0].kwargs["json_data"]
        self.assertEqual(payload["space_id"], "current-ui-space")
        self.assertEqual(payload["value"]["space_id"], "current-ui-space")


if __name__ == "__main__":
    unittest.main()
