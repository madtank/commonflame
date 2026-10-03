"""Regression tests for FastMCP Apps import compatibility."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastmcp_server import fastmcp_compat


class FastMCPImportCompatibilityTests(unittest.TestCase):
    """Protect FastMCP v3 app metadata imports across package layouts."""

    def test_app_config_import_prefers_public_fastmcp_apps_path(self):
        app_config = type("AppConfig", (), {})
        resource_csp = type("ResourceCSP", (), {})
        calls: list[str] = []

        def fake_import(module_name: str):
            calls.append(module_name)
            if module_name == "fastmcp.apps":
                return SimpleNamespace(AppConfig=app_config, ResourceCSP=resource_csp)
            raise AssertionError("legacy import should not be used when public path exists")

        with patch.object(fastmcp_compat, "import_module", side_effect=fake_import):
            self.assertEqual(
                fastmcp_compat.load_app_config_types(),
                (app_config, resource_csp),
            )

        self.assertEqual(calls, ["fastmcp.apps"])

    def test_app_config_import_falls_back_to_fastmcp_server_apps_path(self):
        app_config = type("LegacyAppConfig", (), {})
        resource_csp = type("LegacyResourceCSP", (), {})
        calls: list[str] = []

        def fake_import(module_name: str):
            calls.append(module_name)
            if module_name == "fastmcp.apps":
                raise ModuleNotFoundError("No module named 'fastmcp.apps'", name="fastmcp.apps")
            if module_name == "fastmcp.server.apps":
                return SimpleNamespace(AppConfig=app_config, ResourceCSP=resource_csp)
            raise AssertionError(f"unexpected import: {module_name}")

        with patch.object(fastmcp_compat, "import_module", side_effect=fake_import):
            self.assertEqual(
                fastmcp_compat.load_app_config_types(),
                (app_config, resource_csp),
            )

        self.assertEqual(calls, ["fastmcp.apps", "fastmcp.server.apps"])

    def test_nested_module_not_found_is_not_swallowed(self):
        error = ModuleNotFoundError("No module named 'dependency'", name="dependency")

        with patch.object(fastmcp_compat, "import_module", side_effect=error):
            with self.assertRaises(ModuleNotFoundError) as raised:
                fastmcp_compat.load_app_config_types()

        self.assertIs(raised.exception, error)


if __name__ == "__main__":
    unittest.main()
