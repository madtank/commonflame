"""Tests for Cognito audience configuration."""

from __future__ import annotations

import importlib
import os
import unittest


class AuthConfigAudienceTests(unittest.TestCase):
    def test_includes_explicit_m2m_client_id(self) -> None:
        original = {
            "COGNITO_FRONTEND_CLIENT_ID": os.environ.get("COGNITO_FRONTEND_CLIENT_ID"),
            "COGNITO_MCP_CLIENT_ID": os.environ.get("COGNITO_MCP_CLIENT_ID"),
            "COGNITO_MCP_M2M_CLIENT_ID": os.environ.get("COGNITO_MCP_M2M_CLIENT_ID"),
        }

        try:
            os.environ["COGNITO_FRONTEND_CLIENT_ID"] = "frontend-id"
            os.environ["COGNITO_MCP_CLIENT_ID"] = "interactive-id"
            os.environ["COGNITO_MCP_M2M_CLIENT_ID"] = "m2m-id"

            import app.core.auth_config as auth_config

            auth_config = importlib.reload(auth_config)
            self.assertEqual(
                auth_config.ALLOWED_AUDIENCES,
                ["frontend-id", "interactive-id", "m2m-id"],
            )
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            import app.core.auth_config as auth_config
            importlib.reload(auth_config)


if __name__ == "__main__":
    unittest.main()
