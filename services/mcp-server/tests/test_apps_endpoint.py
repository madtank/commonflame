"""Tests for public widget app registry exposure."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from fastmcp_server.apps_endpoint import apps_get, apps_list
from fastmcp_server.mcp_ui import EXPERIMENTAL_GAMES_FLAG


class AppsEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        app = Starlette(
            routes=[
                Route("/apps", apps_list),
                Route("/apps/{name:path}", apps_get),
            ]
        )
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_registry_hides_unwired_alerts_and_experimental_games_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(EXPERIMENTAL_GAMES_FLAG, None)
            response = self.client.get("/apps")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("alerts", payload)
        self.assertNotIn("games", payload)

    def test_game_widget_slug_is_not_served_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(EXPERIMENTAL_GAMES_FLAG, None)
            response = self.client.get("/apps/game-board")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "Widget 'game-board' not found")

    def test_games_are_listed_and_served_when_flag_is_enabled(self) -> None:
        with patch.dict(os.environ, {EXPERIMENTAL_GAMES_FLAG: "true"}, clear=False):
            list_response = self.client.get("/apps")
            serve_response = self.client.get("/apps/game-board")

        self.assertEqual(list_response.status_code, 200)
        payload = list_response.json()
        self.assertEqual(payload["games"]["resource_uri"], "ui://games/tic-tac-toe")
        self.assertEqual(serve_response.status_code, 200)
        self.assertIn("text/html", serve_response.headers["content-type"])
        self.assertIn("function gameTitle()", serve_response.text)
        self.assertIn("function safeSourceHref(value)", serve_response.text)

    def test_alert_widget_slug_is_not_served_until_contract_exists(self) -> None:
        response = self.client.get("/apps/alerts")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "Widget 'alerts' not found")
