import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastmcp import FastMCP

from fastmcp_server.tools.context import register_context_tool


class ContextToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.mcp = FastMCP("test")
        register_context_tool(self.mcp)
        self.tool = await self.mcp.get_tool("context")
        self.token = SimpleNamespace(
            token="jwt",
            claims={"agent_name": "protocol", "space_id": "space-1"},
        )
        self.request = SimpleNamespace(headers={})
        self.private_permissions = {
            "space_context": {
                "id": "space-1",
                "name": "Personal workspace",
                "visibility": "private",
                "role": "admin",
                "is_personal": True,
                "scope": "private_workspace",
            },
            "permissions": {
                "mode": "private_full_access",
                "read_only": False,
                "blocked_reason": None,
                "can_create": True,
                "can_update": True,
                "can_delete": True,
                "can_archive": False,
                "can_control": False,
                "can_write_memory": False,
                "can_use_hitl_approval": True,
            },
        }

    async def test_action_is_required_in_tool_schema(self) -> None:
        parameters = self.tool.parameters

        self.assertIn("action", parameters.get("required", []))
        self.assertNotIn("default", parameters["properties"]["action"])

    async def test_value_schema_is_json_typed_for_tool_bridges(self) -> None:
        value_schema = self.tool.parameters["properties"]["value"]

        self.assertNotIn({}, value_schema.get("anyOf", []))
        schema_types = {
            entry.get("type")
            for entry in value_schema.get("anyOf", [])
            if isinstance(entry, dict)
        }
        self.assertGreaterEqual(
            schema_types,
            {"string", "integer", "number", "boolean", "object", "array", "null"},
        )

    async def test_action_forms_keeps_simple_context_economical(self) -> None:
        contract = self.tool.meta["ax/actionForms"]

        self.assertEqual(contract["default_mode"], "simple_key_value")
        set_form = contract["actions"]["set"]
        self.assertEqual(set_form["mode"], "simple_key_value")
        self.assertEqual(set_form["required"], ["action", "key", "value"])
        self.assertEqual(
            set(set_form["parameters"]),
            {"action", "key", "value", "ttl", "topic", "content_type", "render_as"},
        )
        self.assertNotIn("catalog_entry_id", set_form["parameters"])
        self.assertNotIn("base_artifact_version_id", set_form["parameters"])
        self.assertNotIn("action_id", set_form["parameters"])

    async def test_action_forms_separates_governed_catalog_fields(self) -> None:
        contract = self.tool.meta["ax/actionForms"]

        self.assertEqual(contract["version"], 1)
        triggers = contract["catalog_mode_triggers"]
        self.assertEqual(triggers["tier"], "catalog")
        self.assertEqual(triggers["to"], "catalog")
        self.assertEqual(set(triggers["actions"]), {"create", "act", "patch"})

        catalog_params = set(contract["catalog_parameters"])
        self.assertIn("catalog_entry_id", catalog_params)
        self.assertIn("base_artifact_version_id", catalog_params)
        self.assertIn("idempotency_key", catalog_params)

        act_form = contract["actions"]["act"]
        self.assertEqual(act_form["mode"], "governed_catalog")
        self.assertEqual(
            act_form["required"],
            [
                "action",
                "catalog_entry_id",
                "action_id",
                "base_artifact_version_id",
                "base_state_version_id",
            ],
        )
        self.assertIn("idempotency_key", act_form["parameters"])

        patch_form = contract["actions"]["patch"]
        self.assertEqual(patch_form["mode"], "governed_catalog")
        self.assertIn("base_artifact_version_id", patch_form["required"])
        self.assertIn("base_state_version_id", patch_form["required"])

        create_form = contract["actions"]["create"]
        self.assertNotIn("idempotency_key", create_form["parameters"])
        promote_form = contract["actions"]["promote"]
        self.assertEqual(promote_form["parameters"], ["action", "key", "to"])
        self.assertEqual(promote_form["required"], ["action", "key"])
        promote_catalog_form = contract["actions"]["promote_catalog"]
        self.assertEqual(promote_catalog_form["action"], "promote")
        self.assertEqual(promote_catalog_form["alias_of"], "promote")
        self.assertEqual(promote_catalog_form["mode"], "governed_catalog")
        self.assertEqual(promote_catalog_form["required"], ["action", "key", "to"])
        self.assertIn("title", promote_catalog_form["parameters"])
        self.assertIn("artifact_type", promote_catalog_form["parameters"])
        list_catalog_form = contract["actions"]["list_catalog"]
        self.assertEqual(list_catalog_form["alias_of"], "list")
        self.assertNotIn("offset", list_catalog_form["parameters"])

    async def test_action_forms_is_consistent_with_tool_schema(self) -> None:
        contract = self.tool.meta["ax/actionForms"]
        schema_parameters = set(self.tool.parameters["properties"])
        advertised_parameters = {
            parameter
            for form in contract["actions"].values()
            for parameter in form["parameters"]
        }

        self.assertLessEqual(advertised_parameters, schema_parameters)
        self.assertEqual(schema_parameters - advertised_parameters, {"space_id"})
        self.assertNotIn("value", set(contract["catalog_parameters"]))
        for name, form in contract["actions"].items():
            with self.subTest(action=name):
                self.assertLessEqual(set(form["required"]), set(form["parameters"]))

        governed_parameters = {
            parameter
            for name, form in contract["actions"].items()
            # promote_catalog intentionally includes simple-mode `key`/`context_key` fields;
            # the catalog-only invariant is enforced on the pure catalog forms below.
            if form["mode"] == "governed_catalog" and name != "promote_catalog"
            for parameter in form["parameters"]
            if parameter not in {"action", "value"}
        }
        self.assertLessEqual(governed_parameters, set(contract["catalog_parameters"]))
        self.assertEqual(contract["catalog_parameters"], sorted(contract["catalog_parameters"]))

    async def _call_tool(self, **kwargs):
        with patch(
            "fastmcp_server.tools.context.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=self.private_permissions),
        ):
            return await self.tool.fn(
                token=self.token,
                request=self.request,
                **kwargs,
            )

    async def test_list_returns_v2_envelope(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "status:protocol": {
                        "value": "Working on context UI",
                        "agent_name": "user:jacob",
                        "created_at": "2026-03-13T18:00:00+00:00",
                        "ttl": 3600,
                        "expires_at": 1773457200.0,
                        "topic": "status",
                    }
                }
            ),
        ):
            result = await self._call_tool(action="list")

        structured = result.structured_content
        self.assertEqual(structured["kind"], "context")
        self.assertEqual(structured["version"], 2)
        self.assertEqual(structured["action"], "list")
        self.assertEqual(structured["kind"], "context")
        self.assertEqual(structured["version"], 2)
        self.assertEqual(structured["count"], 1)
        self.assertEqual(structured["keys"], ["status:protocol"])
        self.assertTrue(structured["permissions"]["can_delete"])
        self.assertEqual(result.meta["ax_context_contract"], "context.v1")

    async def test_user_list_adds_explicit_space_query_override(self) -> None:
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
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(return_value={"items": [], "count": 0}),
            ) as api_request,
        ):
            await self._call_tool(action="list")

        first_call = api_request.await_args_list[0]
        self.assertEqual(first_call.kwargs["space_id"], "current-ui-space")
        self.assertEqual(
            first_call.kwargs["params"],
            {"limit": 50, "space_id": "current-ui-space"},
        )

    async def test_list_forwards_offset_and_reports_page_metadata(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "items": [{"key": "page/item", "value": "small body"}],
                    "count": 1000,
                }
            ),
        ) as api_request:
            result = await self._call_tool(action="list", limit=25, offset=50)

        first_call = api_request.await_args_list[0]
        self.assertEqual(first_call.kwargs["params"], {"limit": 25, "offset": 50})

        structured = result.structured_content
        self.assertEqual(structured["count"], 1000)
        self.assertEqual(structured["returned"], 1)
        self.assertEqual(structured["limit"], 25)
        self.assertEqual(structured["offset"], 50)
        self.assertTrue(structured["has_more"])

    async def test_user_set_adds_explicit_space_body_override(self) -> None:
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
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(
                    side_effect=[
                        {"ok": True},
                        {"key": "status:protocol", "value": "green"},
                    ]
                ),
            ) as api_request,
        ):
            await self._call_tool(action="set", key="status:protocol", value="green")

        set_call = api_request.await_args_list[0]
        self.assertEqual(set_call.kwargs["space_id"], "current-ui-space")
        self.assertEqual(
            set_call.kwargs["json_data"],
            {
                "key": "status:protocol",
                "value": "green",
                "space_id": "current-ui-space",
            },
        )

        detail_call = api_request.await_args_list[1]
        self.assertEqual(
            detail_call.kwargs["params"],
            {"space_id": "current-ui-space"},
        )

    async def test_set_accepts_explicit_content_type_for_no_extension_html_key(self) -> None:
        html = "<!doctype html><html><body>Dragon Maker</body></html>"
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"ok": True},
                    {
                        "key": "dragon-game",
                        "value": {
                            "type": "file_upload",
                            "filename": "dragon-game.html",
                            "content_type": "text/html",
                            "content": html,
                        },
                    },
                ]
            ),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=html,
                content_type="text/html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {
                "key": "dragon-game",
                "value": {
                    "type": "file_upload",
                    "filename": "dragon-game.html",
                    "content_type": "text/html",
                    "content": html,
                },
            },
        )
        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_saved_renderable")
        self.assertEqual(structured["items"][0]["file_upload"]["content_type"], "text/html")

    async def test_set_accepts_render_as_aliases_for_no_extension_html_key(self) -> None:
        html = "<!doctype html><html><body>Dragon Maker</body></html>"
        for render_as in ("html", "HTML", "text/html"):
            with self.subTest(render_as=render_as):
                key = f"dragon-game-{render_as.replace('/', '-')}"
                wrapped_value = {
                    "type": "file_upload",
                    "filename": f"{key}.html",
                    "content_type": "text/html",
                    "content": html,
                }
                with patch(
                    "fastmcp_server.tools.context.api_request",
                    new=AsyncMock(side_effect=[{"ok": True}, {"key": key, "value": wrapped_value}]),
                ) as api_request:
                    result = await self._call_tool(
                        action="set",
                        key=key,
                        value=html,
                        render_as=render_as,
                    )

                set_call = api_request.await_args_list[0]
                self.assertEqual(
                    set_call.kwargs["json_data"],
                    {"key": key, "value": wrapped_value},
                )
                self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_accepts_render_as_aliases_for_no_extension_inline_artifacts(self) -> None:
        cases = (
            ("svg", "image/svg+xml", ".svg", "<svg><text>Dragon Maker</text></svg>"),
            ("json", "application/json", ".json", '{"name":"Dragon Maker"}'),
            ("markdown", "text/markdown", ".md", "# Dragon Maker"),
        )
        for render_as, expected_content_type, expected_suffix, value in cases:
            with self.subTest(render_as=render_as):
                key = f"dragon-game-{render_as}"
                wrapped_value = {
                    "type": "file_upload",
                    "filename": f"{key}{expected_suffix}",
                    "content_type": expected_content_type,
                    "content": value,
                }
                with patch(
                    "fastmcp_server.tools.context.api_request",
                    new=AsyncMock(side_effect=[{"ok": True}, {"key": key, "value": wrapped_value}]),
                ) as api_request:
                    result = await self._call_tool(
                        action="set",
                        key=key,
                        value=value,
                        render_as=render_as,
                    )

                set_call = api_request.await_args_list[0]
                self.assertEqual(
                    set_call.kwargs["json_data"],
                    {"key": key, "value": wrapped_value},
                )
                self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_ignores_unknown_render_as_alias(self) -> None:
        value = "Dragon Maker"
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=value,
                render_as="not-a-real-type",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved")

    async def test_set_wraps_structured_html_without_leaking_source_body_keys(self) -> None:
        html = "<!doctype html><html><body>Dragon Maker</body></html>"
        value = {
            "type": "html",
            "html": html,
            "title": "Dragon Maker",
            "summary": "Playable demo",
        }
        wrapped_value = {
            "title": "Dragon Maker",
            "summary": "Playable demo",
            "type": "file_upload",
            "filename": "dragon-game.html",
            "content_type": "text/html",
            "content": html,
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": wrapped_value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=value,
                content_type="text/html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": wrapped_value},
        )
        self.assertNotIn("html", set_call.kwargs["json_data"]["value"])
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_wraps_structured_html_content_field(self) -> None:
        html = "<!doctype html><html><body>Dragon Maker</body></html>"
        value = {
            "content": html,
            "title": "Dragon Maker",
        }
        wrapped_value = {
            "title": "Dragon Maker",
            "type": "file_upload",
            "filename": "dragon-game.html",
            "content_type": "text/html",
            "content": html,
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": wrapped_value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=value,
                content_type="text/html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": wrapped_value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_leaves_dict_without_inline_content_unwrapped(self) -> None:
        value = {"metadata": "Dragon Maker"}
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=value,
                content_type="text/html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved")

    async def test_set_leaves_unsupported_content_type_unwrapped(self) -> None:
        value = "https://cdn.example.test/dragon-game.png"
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=value,
                content_type="image/png",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved")

    async def test_set_content_type_precedes_render_as_when_unsupported(self) -> None:
        html = "<!doctype html><html><body>Dragon Maker</body></html>"
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": html}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=html,
                content_type="image/png",
                render_as="html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": html},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved")

    async def test_set_unknown_content_type_does_not_fall_through_to_render_as(self) -> None:
        html = "<!doctype html><html><body>Dragon Maker</body></html>"
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": html}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=html,
                content_type="foobar",
                render_as="html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": html},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved")

    async def test_set_explicit_content_type_overrides_file_upload_content_type(self) -> None:
        value = {
            "type": "file_upload",
            "filename": "dragon-game.jpg",
            "content_type": "image/jpeg",
            "url": "https://cdn.example.test/dragon-game.html",
        }
        wrapped_value = {
            "type": "file_upload",
            "filename": "dragon-game.jpg",
            "content_type": "text/html",
            "url": "https://cdn.example.test/dragon-game.html",
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": wrapped_value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=value,
                content_type="text/html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": wrapped_value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_explicit_content_type_rewrites_mismatched_key_extension(self) -> None:
        html = "<!doctype html><html><body>Dragon Maker</body></html>"
        wrapped_value = {
            "type": "file_upload",
            "filename": "chart.html",
            "content_type": "text/html",
            "content": html,
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "chart.xml", "value": wrapped_value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="chart.xml",
                value=html,
                content_type="text/html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "chart.xml", "value": wrapped_value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_wraps_url_locator_with_explicit_content_type(self) -> None:
        url = "https://cdn.example.test/dragon-game.html?version=1"
        wrapped_value = {
            "type": "file_upload",
            "filename": "dragon-game.html",
            "content_type": "text/html",
            "url": url,
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": wrapped_value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=url,
                content_type="text/html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": wrapped_value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_wraps_url_locator_with_render_as_alias(self) -> None:
        url = "https://cdn.example.test/dragon-game.html?version=1"
        wrapped_value = {
            "type": "file_upload",
            "filename": "dragon-game.html",
            "content_type": "text/html",
            "url": url,
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": wrapped_value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=url,
                render_as="html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": wrapped_value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_content_type_wins_over_render_as_when_both_are_valid(self) -> None:
        html = "<!doctype html><html><body>Dragon Maker</body></html>"
        wrapped_value = {
            "type": "file_upload",
            "filename": "dragon-game.html",
            "content_type": "text/html",
            "content": html,
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": wrapped_value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=html,
                content_type="text/html",
                render_as="svg",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": wrapped_value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_wraps_text_xml_with_xml_filename(self) -> None:
        xml = "<note><body>Dragon Maker</body></note>"
        wrapped_value = {
            "type": "file_upload",
            "filename": "dragon-feed.xml",
            "content_type": "text/xml",
            "content": xml,
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-feed", "value": wrapped_value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-feed",
                value=xml,
                content_type="text/xml",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-feed", "value": wrapped_value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_strips_content_type_params_for_explicit_renderable_save(self) -> None:
        html = "<!doctype html><html><body>Dragon Maker</body></html>"
        wrapped_value = {
            "type": "file_upload",
            "filename": "dragon-game.html",
            "content_type": "text/html",
            "content": html,
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": wrapped_value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=html,
                content_type="text/html; charset=utf-8",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": wrapped_value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_explicit_content_type_wraps_key_with_known_extension(self) -> None:
        html = "<!doctype html><html><body>Dragon Maker</body></html>"
        wrapped_value = {
            "type": "file_upload",
            "filename": "dragon-game.html",
            "content_type": "text/html",
            "content": html,
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game.html", "value": wrapped_value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game.html",
                value=html,
                content_type="text/html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game.html", "value": wrapped_value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_render_as_wraps_key_with_known_extension(self) -> None:
        html = "<!doctype html><html><body>Dragon Maker</body></html>"
        wrapped_value = {
            "type": "file_upload",
            "filename": "dragon-game.html",
            "content_type": "text/html",
            "content": html,
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game.txt", "value": wrapped_value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game.txt",
                value=html,
                render_as="html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game.txt", "value": wrapped_value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_wraps_data_url_with_explicit_content_type(self) -> None:
        data_url = "data:text/html;base64,PGh0bWw+PC9odG1sPg=="
        wrapped_value = {
            "type": "file_upload",
            "filename": "dragon-game.html",
            "content_type": "text/html",
            "url": data_url,
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[{"ok": True}, {"key": "dragon-game", "value": wrapped_value}]),
        ) as api_request:
            result = await self._call_tool(
                action="set",
                key="dragon-game",
                value=data_url,
                content_type="text/html",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game", "value": wrapped_value},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_infers_html_for_no_extension_renderable_value(self) -> None:
        html = "<html><body>Playable</body></html>"
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"ok": True},
                    {
                        "key": "dragon-game-html",
                        "value": {
                            "type": "file_upload",
                            "filename": "dragon-game-html.html",
                            "content_type": "text/html",
                            "content": html,
                        },
                    },
                ]
            ),
        ) as api_request:
            result = await self._call_tool(action="set", key="dragon-game-html", value=html)

        set_call = api_request.await_args_list[0]
        self.assertEqual(set_call.kwargs["json_data"]["value"]["content_type"], "text/html")
        self.assertEqual(set_call.kwargs["json_data"]["value"]["filename"], "dragon-game-html.html")
        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_saved_renderable")
        self.assertIn("text/html", structured["notice"]["message"])

    async def test_set_does_not_infer_html_for_known_extension_key(self) -> None:
        html = "<html><body>Playable</body></html>"
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"ok": True},
                    {"key": "dragon-game.html", "value": html},
                ]
            ),
        ) as api_request:
            result = await self._call_tool(action="set", key="dragon-game.html", value=html)

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game.html", "value": html},
        )
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved")

    async def test_set_infers_html_with_leading_whitespace(self) -> None:
        html = "  \n\t<!doctype html><html><body>Playable</body></html>"
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"ok": True},
                    {
                        "key": "dragon-game-html",
                        "value": {
                            "type": "file_upload",
                            "filename": "dragon-game-html.html",
                            "content_type": "text/html",
                            "content": html,
                        },
                    },
                ]
            ),
        ) as api_request:
            result = await self._call_tool(action="set", key="dragon-game-html", value=html)

        set_call = api_request.await_args_list[0]
        self.assertEqual(set_call.kwargs["json_data"]["value"]["content_type"], "text/html")
        self.assertEqual(result.structured_content["notice"]["code"], "context_saved_renderable")

    async def test_set_does_not_infer_html_for_html_prefixed_custom_tag(self) -> None:
        markup = "<html-custom>Playable</html-custom>"
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"ok": True},
                    {"key": "dragon-game-htmlish", "value": markup},
                ]
            ),
        ) as api_request:
            await self._call_tool(action="set", key="dragon-game-htmlish", value=markup)

        set_call = api_request.await_args_list[0]
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "dragon-game-htmlish", "value": markup},
        )

    async def test_user_set_ignores_tool_space_id_for_switch_guard(self) -> None:
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
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(
                    side_effect=[
                        {"ok": True},
                        {"key": "status:protocol", "value": "green"},
                    ]
                ),
            ) as api_request,
        ):
            result = await self._call_tool(
                action="set",
                key="status:protocol",
                value="green",
                space_id="other-ui-space",
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(set_call.kwargs["space_id"], "current-ui-space")
        self.assertNotIn("error", result.structured_content)

    async def test_route_bound_agent_set_uses_permission_resolved_space_header(self) -> None:
        self.token.claims = {
            "sub": "user-1",
            "client_id": "mcp-client",
            "aud": "ax-mcp",
            "scope": "ax-api/mcp:read ax-api/mcp:write",
        }
        self.request = SimpleNamespace(headers={"x-agent-name": "cipher"})
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.private_permissions),
            ),
            patch(
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(
                    side_effect=[
                        {"ok": True},
                        {"key": "cipher:html", "value": {"type": "html"}},
                    ]
                ),
            ) as api_request,
        ):
            await self.tool.fn(
                action="set",
                key="cipher:html",
                value={"type": "html"},
                space_id=None,
                token=self.token,
                request=self.request,
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(set_call.kwargs["space_id"], "space-1")
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "cipher:html", "value": {"type": "html"}},
        )

        detail_call = api_request.await_args_list[1]
        self.assertEqual(detail_call.kwargs["space_id"], "space-1")

    async def test_route_bound_agent_set_accepts_matching_explicit_space_id(self) -> None:
        self.token.claims = {
            "sub": "user-1",
            "client_id": "mcp-client",
            "aud": "ax-mcp",
            "scope": "ax-api/mcp:read ax-api/mcp:write",
        }
        self.request = SimpleNamespace(headers={"x-agent-name": "cipher"})
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.private_permissions),
            ),
            patch(
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(
                    side_effect=[
                        {"ok": True},
                        {"key": "cipher:html", "value": {"type": "html"}},
                    ]
                ),
            ) as api_request,
        ):
            await self.tool.fn(
                action="set",
                key="cipher:html",
                value={"type": "html"},
                space_id="space-1",
                token=self.token,
                request=self.request,
            )

        set_call = api_request.await_args_list[0]
        self.assertEqual(set_call.kwargs["space_id"], "space-1")
        self.assertEqual(
            set_call.kwargs["json_data"],
            {"key": "cipher:html", "value": {"type": "html"}},
        )

    async def test_agent_set_allows_shared_space_collaboration(self) -> None:
        self.token.claims = {
            "agent_name": "protocol",
            "space_id": "team-space",
        }
        shared_permissions = {
            "space_context": {
                "id": "team-space",
                "name": "Team Space",
                "visibility": "invite_only",
                "role": "member",
                "is_personal": False,
                "scope": "shared_space",
            },
            "permissions": {
                "mode": "shared_collaborative_space",
                "read_only": False,
                "blocked_reason": None,
                "can_create": True,
                "can_update": True,
                "can_delete": True,
                "can_archive": False,
                "can_control": False,
                "can_write_memory": False,
                "can_use_hitl_approval": False,
            },
        }
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=shared_permissions),
            ),
            patch(
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(
                    side_effect=[
                        {"ok": True},
                        {"key": "team/upload", "value": {"type": "pdf"}},
                    ]
                ),
            ) as api_request,
        ):
            result = await self.tool.fn(
                action="set",
                key="team/upload",
                value={"type": "pdf", "url": "https://example.test/team.pdf"},
                token=self.token,
                request=self.request,
            )

        self.assertNotEqual(result.structured_content["notice"]["code"], "context_set_blocked")
        set_call = api_request.await_args_list[0]
        self.assertEqual(set_call.args[0], "POST")
        self.assertEqual(set_call.kwargs["space_id"], "team-space")

    async def test_route_bound_agent_set_rejects_different_explicit_space_id(self) -> None:
        self.token.claims = {
            "sub": "user-1",
            "client_id": "mcp-client",
            "aud": "ax-mcp",
            "scope": "ax-api/mcp:read ax-api/mcp:write",
        }
        self.request = SimpleNamespace(headers={"x-agent-name": "cipher"})
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.private_permissions),
            ),
            patch(
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(),
            ) as api_request,
        ):
            result = await self.tool.fn(
                action="set",
                key="cipher:html",
                value={"type": "html"},
                space_id="space-2",
                token=self.token,
                request=self.request,
            )

        api_request.assert_not_awaited()
        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_space_switch_required")
        self.assertIn('spaces(action="switch"', structured["notice"]["message"])
        self.assertEqual(structured["error"], "context_space_switch_required")
        self.assertEqual(structured["requested_space_id"], "space-2")
        self.assertEqual(structured["current_space_id"], "space-1")

    async def test_route_bound_agent_delete_rejects_different_explicit_space_id(self) -> None:
        self.token.claims = {
            "sub": "user-1",
            "client_id": "mcp-client",
            "aud": "ax-mcp",
            "scope": "ax-api/mcp:read ax-api/mcp:write",
        }
        self.request = SimpleNamespace(headers={"x-agent-name": "cipher"})
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.private_permissions),
            ),
            patch(
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(),
            ) as api_request,
        ):
            result = await self.tool.fn(
                action="delete",
                key="cipher:html",
                space_id="space-2",
                token=self.token,
                request=self.request,
            )

        api_request.assert_not_awaited()
        structured = result.structured_content
        self.assertEqual(structured["action"], "delete")
        self.assertEqual(structured["notice"]["code"], "context_space_switch_required")
        self.assertEqual(structured["error"], "context_space_switch_required")

    async def test_route_bound_agent_promote_rejects_different_explicit_space_id(self) -> None:
        self.token.claims = {
            "sub": "user-1",
            "client_id": "mcp-client",
            "aud": "ax-mcp",
            "scope": "ax-api/mcp:read ax-api/mcp:write",
        }
        self.request = SimpleNamespace(headers={"x-agent-name": "cipher"})
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.private_permissions),
            ),
            patch(
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(),
            ) as api_request,
        ):
            result = await self.tool.fn(
                action="promote",
                key="cipher:html",
                artifact_type="RESEARCH",
                space_id="space-2",
                token=self.token,
                request=self.request,
            )

        api_request.assert_not_awaited()
        structured = result.structured_content
        self.assertEqual(structured["action"], "promote")
        self.assertEqual(structured["notice"]["code"], "context_space_switch_required")
        self.assertEqual(structured["error"], "context_space_switch_required")

    async def test_get_returns_v2_envelope(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "key": "scratch:deploy-checklist",
                    "value": {
                        "value": {"subject": "Deploy checklist", "note": "Canary first, then production."},
                        "agent_name": "user:jacob",
                        "created_at": "2026-03-13T18:05:00+00:00",
                        "ttl": 86400,
                        "expires_at": 1773540300.0,
                        "topic": "scratchpad",
                    },
                    "source": "redis",
                }
            ),
        ):
            result = await self._call_tool(action="get", key="scratch:deploy-checklist")

        structured = result.structured_content
        self.assertEqual(structured["kind"], "context")
        self.assertEqual(structured["action"], "get")
        item = structured["items"][0]
        self.assertEqual(item["key"], "scratch:deploy-checklist")
        self.assertEqual(item["topic"], "scratchpad")

    async def test_approve_re_saves_value_without_ttl(self) -> None:
        existing = {
            "key": "doc:report",
            "value": '{"type": "file_upload", "filename": "report.pdf"}',
            "topic": "documents",
            "ttl": 3600,
            "expires_at": 1773457200.0,
        }
        approved = {
            "key": "doc:report",
            "value": '{"type": "file_upload", "filename": "report.pdf"}',
            "topic": "documents",
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(side_effect=[existing, {"ok": True}, approved]),
        ) as api_request:
            result = await self._call_tool(action="approve", key="doc:report")

        # 1) GET current, 2) POST without ttl, 3) GET refreshed detail
        self.assertEqual(api_request.await_count, 3)
        post_call = api_request.await_args_list[1]
        self.assertEqual(post_call.args[0], "POST")
        self.assertEqual(post_call.args[1], "/api/v1/context")
        json_data = post_call.kwargs["json_data"]
        self.assertEqual(json_data["key"], "doc:report")
        self.assertEqual(
            json_data["value"], '{"type": "file_upload", "filename": "report.pdf"}'
        )
        self.assertEqual(json_data["topic"], "documents")
        self.assertNotIn("ttl", json_data)

        structured = result.structured_content
        self.assertEqual(structured["action"], "get")
        self.assertEqual(structured["selected_key"], "doc:report")
        self.assertEqual(structured["notice"]["code"], "context_approved")

    async def test_approve_preserves_fetch_errors(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(return_value={"error": "backend unavailable", "status": 503}),
        ) as api_request:
            result = await self._call_tool(action="approve", key="doc:report")

        self.assertEqual(api_request.await_count, 1)
        structured = result.structured_content
        self.assertEqual(structured["action"], "get")
        self.assertEqual(structured["selected_key"], "doc:report")
        self.assertEqual(structured["error"], "backend unavailable")
        self.assertEqual(structured["notice"]["code"], "context_approve_fetch_failed")

    async def test_approve_missing_key_errors(self) -> None:
        result = await self._call_tool(action="approve")
        self.assertEqual(result, {"error": "'key' required for approve action"})

    async def test_approve_blocked_when_no_write_permission(self) -> None:
        readonly_permissions = {
            "space_context": {
                "id": "shared-space",
                "name": "Shared Space",
                "visibility": "private",
                "role": "member",
                "is_personal": False,
                "scope": "shared_space",
            },
            "permissions": {
                "mode": "agent_authored_limited",
                "read_only": True,
                "blocked_reason": "Agent-authored actions require user approval. Use a user quick action to make changes.",
                "can_create": False,
                "can_update": False,
                "can_delete": False,
                "can_archive": False,
                "can_control": False,
                "can_write_memory": False,
                "can_use_hitl_approval": False,
            },
        }
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=readonly_permissions),
            ),
            patch(
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(return_value={"key": "doc:report", "value": "x"}),
            ) as api_request,
        ):
            result = await self.tool.fn(
                action="approve",
                key="doc:report",
                token=self.token,
                request=self.request,
            )

        # No POST happened — only the GET to render the blocked-state widget.
        for call in api_request.await_args_list:
            self.assertNotEqual(call.args[0], "POST")
        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_approve_blocked")

    async def test_decline_deletes_and_refreshes_list(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"ok": True},  # DELETE
                    {"items": [], "count": 0},  # list refresh
                ]
            ),
        ) as api_request:
            result = await self._call_tool(action="decline", key="doc:report")

        self.assertEqual(api_request.await_count, 2)
        delete_call = api_request.await_args_list[0]
        self.assertEqual(delete_call.args[0], "DELETE")
        self.assertEqual(delete_call.args[1], "/api/v1/context/doc%3Areport")

        structured = result.structured_content
        self.assertEqual(structured["action"], "list")
        self.assertEqual(structured["notice"]["code"], "context_declined")

    async def test_decline_blocked_in_shared_space(self) -> None:
        shared_permissions = {
            "space_context": {
                "id": "shared-space",
                "name": "Shared Space",
                "visibility": "private",
                "role": "member",
                "is_personal": False,
                "scope": "shared_space",
            },
            "permissions": {
                "mode": "agent_authored_limited",
                "read_only": True,
                "blocked_reason": "Agent-authored actions require user approval. Use a user quick action to make changes.",
                "can_create": False,
                "can_update": False,
                "can_delete": False,
                "can_archive": False,
                "can_control": False,
                "can_write_memory": False,
                "can_use_hitl_approval": False,
            },
        }
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=shared_permissions),
            ),
            patch(
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(return_value={"key": "doc:report", "value": "x"}),
            ) as api_request,
        ):
            result = await self.tool.fn(
                action="decline",
                key="doc:report",
                token=self.token,
                request=self.request,
            )

        for call in api_request.await_args_list:
            self.assertNotEqual(call.args[0], "DELETE")
        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_decline_blocked")

    async def test_list_normalizes_spaced_file_upload_json_strings(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "items": [
                        {
                            "key": "fixture-hello-pdf",
                            "value": (
                                '{"type": "file upload", "filename": "hello.pdf", '
                                '"content type": "application/pdf", "size": 128, '
                                '"url": "data:application/pdf;base64,JVBERi0xLjQ="}'
                            ),
                            "topic": "fixture-hello-pdf",
                        }
                    ],
                    "count": 1,
                }
            ),
        ):
            result = await self._call_tool(action="list")

        item = result.structured_content["items"][0]
        self.assertNotIn("value", item)
        self.assertNotIn("file_content", item)
        self.assertEqual(item["file_upload"]["filename"], "hello.pdf")
        self.assertEqual(item["file_upload"]["content_type"], "application/pdf")
        self.assertEqual(item["file_upload"]["size"], 128)
        self.assertEqual(item["file_upload"]["url_kind"], "data")
        self.assertTrue(item["file_upload"]["url_omitted"])

    async def test_list_normalizes_camel_case_file_upload_metadata(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "items": [
                        {
                            "key": "fixture-audio",
                            "value": {
                                "kind": "attachment",
                                "fileName": "tone.wav",
                                "mediaType": "audio/wav",
                                "downloadUrl": "https://example.test/tone.wav",
                                "sizeBytes": "1024.0",
                            },
                            "topic": "fixture-audio",
                        }
                    ],
                    "count": 1,
                }
            ),
        ):
            result = await self._call_tool(action="list")

        item = result.structured_content["items"][0]
        self.assertNotIn("value", item)
        self.assertNotIn("file_content", item)
        self.assertEqual(item["file_upload"]["filename"], "tone.wav")
        self.assertEqual(item["file_upload"]["content_type"], "audio/wav")
        self.assertEqual(item["file_upload"]["url"], "https://example.test/tone.wav")
        self.assertEqual(item["file_upload"]["size"], 1024)

    async def test_list_normalizes_declared_artifact_file_shapes(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "items": [
                        {
                            "key": "fixture-pdf",
                            "value": {
                                "type": "pdf",
                                "title": "launch-plan.pdf",
                                "url": "https://example.test/launch-plan",
                            },
                            "topic": "fixture-pdf",
                        },
                        {
                            "key": "fixture-image",
                            "value": {
                                "artifact_type": "image",
                                "fileName": "swatch.png",
                                "dataUrl": "data:image/png;base64,AAAA",
                            },
                            "topic": "fixture-image",
                        },
                    ],
                    "count": 2,
                }
            ),
        ):
            result = await self._call_tool(action="list")

        pdf, image = result.structured_content["items"]
        self.assertNotIn("value", pdf)
        self.assertEqual(pdf["file_upload"]["filename"], "launch-plan.pdf")
        self.assertEqual(pdf["file_upload"]["content_type"], "application/pdf")
        self.assertEqual(pdf["file_upload"]["url"], "https://example.test/launch-plan")
        self.assertNotIn("value", image)
        self.assertEqual(image["file_upload"]["filename"], "swatch.png")
        self.assertEqual(image["file_upload"]["content_type"], "image/png")
        self.assertEqual(image["file_upload"]["url_kind"], "data")
        self.assertTrue(image["file_upload"]["url_omitted"])

    async def test_list_normalizes_declared_inline_svg_artifacts(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "items": [
                        {
                            "key": "fixture-svg",
                            "value": {
                                "type": "svg",
                                "content": '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
                            },
                            "topic": "fixture-svg",
                        }
                    ],
                    "count": 1,
                }
            ),
        ):
            result = await self._call_tool(action="list")

        item = result.structured_content["items"][0]
        self.assertNotIn("value", item)
        self.assertNotIn("file_content", item)
        self.assertEqual(item["file_upload"]["content_type"], "image/svg+xml")

    async def test_list_infers_inline_file_artifacts_from_key_extensions(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "items": [
                        {
                            "key": "upload:1779859638523:report.json:d590",
                            "value": '{"ok": true, "count": 2}',
                            "topic": "fixtures",
                        },
                        {
                            "key": "assets/logo.svg",
                            "value": '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
                            "topic": "fixtures",
                        },
                    ],
                    "count": 2,
                }
            ),
        ):
            result = await self._call_tool(action="list")

        json_item, svg_item = result.structured_content["items"]
        self.assertNotIn("value", json_item)
        self.assertNotIn("file_content", json_item)
        self.assertEqual(json_item["file_upload"]["filename"], "report.json")
        self.assertEqual(json_item["file_upload"]["content_type"], "application/json")
        self.assertNotIn("value", svg_item)
        self.assertNotIn("file_content", svg_item)
        self.assertEqual(svg_item["file_upload"]["filename"], "logo.svg")
        self.assertEqual(svg_item["file_upload"]["content_type"], "image/svg+xml")

    async def test_list_infers_url_file_artifacts_from_key_extensions(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "items": [
                        {
                            "key": "uploads/swatch.png",
                            "value": {"url": "https://example.test/object?id=123", "size": 512},
                            "topic": "fixtures",
                        },
                        {
                            "key": "assets/logo.svg?cache=123#badge",
                            "value": "https://example.test/logo.svg",
                            "topic": "fixtures",
                        }
                    ],
                    "count": 2,
                }
            ),
        ):
            result = await self._call_tool(action="list")

        items_by_filename = {item["file_upload"]["filename"]: item for item in result.structured_content["items"]}
        image_item = items_by_filename["swatch.png"]
        svg_item = items_by_filename["logo.svg"]
        self.assertNotIn("value", image_item)
        self.assertEqual(image_item["file_upload"]["content_type"], "image/png")
        self.assertEqual(image_item["file_upload"]["url"], "https://example.test/object?id=123")
        self.assertNotIn("value", svg_item)
        self.assertEqual(svg_item["file_upload"]["content_type"], "image/svg+xml")
        self.assertEqual(svg_item["file_upload"]["url"], "https://example.test/logo.svg")
        self.assertNotIn("file_content", svg_item)

    async def test_list_keeps_plain_json_objects_with_url_and_size_as_json_content(self) -> None:
        plain_json = {
            "url": "https://api.example.test/v1/users",
            "size": 25,
            "label": "café",
            "status": "ok",
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "items": [
                        {
                            "key": "api/response.json",
                            "value": plain_json,
                            "topic": "fixtures",
                        }
                    ],
                    "count": 1,
                }
            ),
        ):
            result = await self._call_tool(action="list")

        item = result.structured_content["items"][0]
        self.assertNotIn("value", item)
        self.assertNotIn("file_content", item)
        self.assertEqual(item["file_upload"]["filename"], "response.json")
        self.assertEqual(item["file_upload"]["content_type"], "application/json")
        self.assertIn("response.json", item["value_preview"])

    async def test_list_projects_vault_items_as_permanent(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "items": [
                        {
                            "key": "vault/runbooks/missile-command",
                            "value": "Saved durable artifact.",
                            "storage": "vault",
                            "ttl": 604800,
                            "expires_at": 1773540300.0,
                        },
                        {
                            "key": "workspace/synthesis",
                            "value": "Shared intelligence should be durable.",
                            "storage": "workspace_intelligence",
                            "ttl": 604800,
                            "expires_at": 1773540300.0,
                        }
                    ],
                    "count": 2,
                }
            ),
        ):
            result = await self._call_tool(action="list")

        vault_item, intelligence_item = result.structured_content["items"]
        self.assertEqual(vault_item["storage"], "vault")
        self.assertNotIn("ttl", vault_item)
        self.assertNotIn("expires_at", vault_item)
        self.assertEqual(intelligence_item["storage"], "workspace_intelligence")
        self.assertNotIn("ttl", intelligence_item)
        self.assertNotIn("expires_at", intelligence_item)

    async def test_list_does_not_rewrite_plain_title_or_url_values_as_uploads(self) -> None:
        plain_link = {
            "status": "watching",
            "url": "https://example.test/status",
            "title": "Status board",
        }
        plain_title = {"title": "Q2 plan"}
        plain_typed_note = {
            "type": "text",
            "content": "This is a plain structured note, not a text file.",
            "status": "draft",
        }
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "items": [
                        {"key": "ops/service-link", "value": plain_link, "topic": "ops"},
                        {"key": "plans/q2", "value": plain_title, "topic": "plans"},
                        {"key": "notes/plain-draft", "value": plain_typed_note, "topic": "notes"},
                    ],
                    "count": 3,
                }
            ),
        ):
            result = await self._call_tool(action="list")

        items = result.structured_content["items"]
        self.assertNotIn("value", items[0])
        self.assertNotIn("file_upload", items[0])
        self.assertEqual(items[0]["value_type"], "object")
        self.assertGreater(items[0]["value_size"], 0)
        self.assertIn("Status board", items[0]["value_preview"])
        self.assertNotIn("value", items[1])
        self.assertNotIn("file_upload", items[1])
        self.assertNotIn("value", items[2])
        self.assertNotIn("file_upload", items[2])

    async def test_list_does_not_recurse_into_doubly_encoded_upload_strings(self) -> None:
        double_encoded = json.dumps(
            json.dumps(
                {
                    "type": "file_upload",
                    "filename": "nested.pdf",
                    "content_type": "application/pdf",
                }
            )
        )
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "items": [{"key": "nested/upload", "value": double_encoded, "topic": "nested"}],
                    "count": 1,
                }
            ),
        ):
            result = await self._call_tool(action="list")

        item = result.structured_content["items"][0]
        self.assertNotIn("value", item)
        self.assertNotIn("file_upload", item)
        self.assertEqual(item["value_type"], "string")

    async def test_delete_is_blocked_in_shared_space(self) -> None:
        shared_permissions = {
            "space_context": {
                "id": "shared-space",
                "name": "Shared Space",
                "visibility": "private",
                "role": "member",
                "is_personal": False,
                "scope": "shared_space",
            },
            "permissions": {
                "mode": "agent_authored_limited",
                "read_only": True,
                "blocked_reason": "Agent-authored actions require user approval. Use a user quick action to make changes.",
                "can_create": False,
                "can_update": False,
                "can_delete": False,
                "can_archive": False,
                "can_control": False,
                "can_write_memory": False,
                "can_use_hitl_approval": False,
            },
        }
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=shared_permissions),
            ),
            patch(
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(
                    return_value={
                        "key": "status:protocol",
                        "value": "Working on context UI",
                    },
                ),
            ),
        ):
            result = await self.tool.fn(
                action="delete",
                key="status:protocol",
                token=self.token,
                request=self.request,
            )

        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_delete_blocked")

    async def test_terminate_deletes_explicit_keys_and_refreshes_list(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"ok": True},
                    {"ok": True},
                    {"items": [{"key": "upload:still-good"}], "count": 1},
                ]
            ),
        ) as api_request:
            result = await self._call_tool(
                action="terminate",
                keys=["upload:bad.pdf", "upload:bad.png"],
                prefix="upload:",
            )

        self.assertEqual(api_request.await_args_list[0].args[0], "DELETE")
        self.assertEqual(api_request.await_args_list[0].args[1], "/api/v1/context/upload%3Abad.pdf")
        self.assertEqual(api_request.await_args_list[1].args[0], "DELETE")
        self.assertEqual(api_request.await_args_list[1].args[1], "/api/v1/context/upload%3Abad.png")
        self.assertEqual(api_request.await_args_list[2].args[0], "GET")
        self.assertEqual(api_request.await_args_list[2].kwargs["params"], {"prefix": "upload:", "limit": 50})

        structured = result.structured_content
        self.assertEqual(structured["action"], "terminate")
        self.assertEqual(structured["notice"]["code"], "context_terminated")
        self.assertEqual(structured["terminated_keys"], ["upload:bad.pdf", "upload:bad.png"])

    async def test_terminate_can_select_keys_by_prefix(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"items": [{"key": "upload:bad.pdf"}, {"key": "upload:bad.png"}], "count": 2},
                    {"ok": True},
                    {"ok": True},
                    {"items": [], "count": 0},
                ]
            ),
        ) as api_request:
            result = await self._call_tool(
                action="terminate",
                prefix="upload:",
                limit=10,
            )

        self.assertEqual(api_request.await_args_list[0].args[0], "GET")
        self.assertEqual(api_request.await_args_list[0].kwargs["params"], {"prefix": "upload:", "limit": 10})
        self.assertEqual(api_request.await_args_list[1].args[0], "DELETE")
        self.assertEqual(api_request.await_args_list[2].args[0], "DELETE")

        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_terminated")
        self.assertEqual(structured["terminated_count"], 2)

    async def test_terminate_can_select_keys_by_topic(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"items": [{"key": "topic:a"}, {"key": "topic:b"}], "count": 2},
                    {"ok": True},
                    {"ok": True},
                    {"items": [], "count": 0},
                ]
            ),
        ) as api_request:
            result = await self._call_tool(
                action="terminate",
                topic="qa-context-corpus",
                limit=10,
            )

        self.assertEqual(api_request.await_args_list[0].args[0], "GET")
        self.assertEqual(api_request.await_args_list[0].kwargs["params"], {"topic": "qa-context-corpus", "limit": 10})
        self.assertEqual(api_request.await_args_list[1].args[0], "DELETE")
        self.assertEqual(api_request.await_args_list[2].args[0], "DELETE")

        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_terminated")
        self.assertEqual(structured["terminated_keys"], ["topic:a", "topic:b"])

    async def test_terminate_warns_when_selector_matches_no_candidates(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"items": [], "count": 0},
                    {"items": [], "count": 0},
                ]
            ),
        ) as api_request:
            result = await self._call_tool(
                action="terminate",
                prefix="missing:",
                limit=10,
            )

        self.assertEqual(api_request.await_args_list[0].args[0], "GET")
        self.assertEqual(api_request.await_args_list[1].args[0], "GET")
        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_terminate_no_candidates")
        self.assertEqual(structured["notice"]["severity"], "warning")
        self.assertEqual(structured["terminated_count"], 0)
        self.assertEqual(structured["terminated_keys"], [])

    async def test_terminate_caps_large_selector_limit(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"items": [], "count": 0},
                    {"items": [], "count": 0},
                ]
            ),
        ) as api_request:
            await self._call_tool(
                action="terminate",
                prefix="qa-context-corpus/",
                limit=200,
            )

        self.assertEqual(
            api_request.await_args_list[0].kwargs["params"],
            {"prefix": "qa-context-corpus/", "limit": 100},
        )

    async def test_terminate_uses_default_limit_for_invalid_limit(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"items": [], "count": 0},
                    {"items": [], "count": 0},
                ]
            ),
        ) as api_request:
            await self._call_tool(
                action="terminate",
                prefix="qa-context-corpus/",
                limit="not-a-number",
            )

        self.assertEqual(
            api_request.await_args_list[0].kwargs["params"],
            {"prefix": "qa-context-corpus/", "limit": 50},
        )

    async def test_terminate_reports_partial_delete_failures(self) -> None:
        calls = []

        async def fake_api_request(method, path, *args, **kwargs):
            calls.append((method, path, kwargs))
            if method == "DELETE" and path.endswith("upload%3Abad.png"):
                return {"error": "not_found"}
            if method == "DELETE":
                return {"ok": True}
            return {"items": [], "count": 0}

        with patch("fastmcp_server.tools.context.api_request", new=fake_api_request):
            result = await self._call_tool(
                action="terminate",
                keys=["upload:bad.pdf", "upload:bad.png"],
                prefix="upload:",
            )

        delete_paths = {path for method, path, _kwargs in calls if method == "DELETE"}
        self.assertEqual(
            delete_paths,
            {
                "/api/v1/context/upload%3Abad.pdf",
                "/api/v1/context/upload%3Abad.png",
            },
        )
        self.assertEqual(calls[-1][0], "GET")

        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_terminate_partial")
        self.assertEqual(structured["terminated_keys"], ["upload:bad.pdf"])
        self.assertEqual(structured["terminated_count"], 1)
        self.assertEqual(structured["failed_keys"], ["upload:bad.png"])

    async def test_terminate_reports_delete_exceptions_as_failed_keys(self) -> None:
        async def fake_api_request(method, path, *args, **kwargs):
            if method == "DELETE" and path.endswith("upload%3Atimeout.png"):
                raise TimeoutError("backend timed out")
            if method == "DELETE":
                return {"ok": True}
            return {"items": [], "count": 0}

        with patch("fastmcp_server.tools.context.api_request", new=fake_api_request):
            result = await self._call_tool(
                action="terminate",
                keys=["upload:bad.pdf", "upload:timeout.png"],
                prefix="upload:",
            )

        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_terminate_partial")
        self.assertEqual(structured["terminated_keys"], ["upload:bad.pdf"])
        self.assertEqual(structured["terminated_count"], 1)
        self.assertEqual(structured["failed_keys"], ["upload:timeout.png"])

    async def test_terminate_requires_selector(self) -> None:
        result = await self._call_tool(action="terminate")

        self.assertEqual(result["error"], "'keys', 'key', 'prefix', or 'topic' required for terminate action")

    async def test_terminate_accepts_singular_key_for_symmetry(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                side_effect=[
                    {"ok": True},
                    {"items": [], "count": 0},
                ]
            ),
        ) as api_request:
            result = await self._call_tool(action="terminate", key="scratchpad/current-focus")

        self.assertEqual(api_request.await_args_list[0].args[0], "DELETE")
        self.assertEqual(
            api_request.await_args_list[0].args[1],
            "/api/v1/context/scratchpad%2Fcurrent-focus",
        )
        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_terminated")
        self.assertEqual(structured["notice"]["message"], "Terminated 1 context entry.")
        self.assertEqual(structured["terminated_keys"], ["scratchpad/current-focus"])

    async def test_terminate_is_blocked_in_shared_space(self) -> None:
        shared_permissions = {
            "space_context": {
                "id": "shared-space",
                "name": "Shared Space",
                "visibility": "private",
                "role": "member",
                "is_personal": False,
                "scope": "shared_space",
            },
            "permissions": {
                "mode": "agent_authored_limited",
                "read_only": True,
                "blocked_reason": "Agent-authored actions require user approval. Use a user quick action to make changes.",
                "can_create": False,
                "can_update": False,
                "can_delete": False,
                "can_archive": False,
                "can_control": False,
                "can_write_memory": False,
                "can_use_hitl_approval": False,
            },
        }
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=shared_permissions),
            ),
            patch(
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(return_value={"items": [], "count": 0}),
            ),
        ):
            result = await self.tool.fn(
                action="terminate",
                prefix="upload:",
                token=self.token,
                request=self.request,
            )

        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_terminate_blocked")

    async def test_promote_posts_context_key_to_vault(self) -> None:
        with patch(
            "fastmcp_server.tools.context.api_request",
            new=AsyncMock(
                return_value={
                    "status": "created",
                    "id": "intel-1",
                    "key": "research:mcp-oauth",
                    "space_id": "space-1",
                    "agent_id": "protocol",
                    "artifact_type": "RESEARCH",
                    "version": 1,
                    "access_url": "/api/v1/spaces/space-1/intelligence/research:mcp-oauth",
                }
            ),
        ) as api_request:
            result = await self._call_tool(
                action="promote",
                key="research:mcp-oauth",
                artifact_type="RESEARCH",
            )

        promote_call = api_request.await_args_list[0]
        self.assertEqual(
            promote_call.args[1],
            "/api/v1/spaces/space-1/intelligence/promote",
        )
        self.assertEqual(
            promote_call.kwargs["json_data"],
            {"key": "research:mcp-oauth", "artifact_type": "RESEARCH"},
        )

        structured = result.structured_content
        self.assertEqual(structured["action"], "promote")
        self.assertEqual(structured["selected_key"], "research:mcp-oauth")
        self.assertEqual(structured["notice"]["code"], "context_promoted")
        self.assertEqual(structured["items"][0]["storage"], "vault")


class ContextCatalogDispatchTests(unittest.IsolatedAsyncioTestCase):
    """CTX-ARTIFACTS-002: the context tool dispatches governed-catalog actions to
    handle_catalog_action, mapping public verbs to internal action names and
    resolving the space from the session (never an explicit space_id)."""

    async def asyncSetUp(self) -> None:
        self.mcp = FastMCP("test")
        register_context_tool(self.mcp)
        self.tool = await self.mcp.get_tool("context")
        # Token WITHOUT a space_id claim — space must be resolved from the bundle.
        self.token = SimpleNamespace(token="jwt", claims={"agent_name": "protocol"})
        self.request = SimpleNamespace(headers={})
        self.permissions = {
            "space_context": {"id": "space-1", "name": "WS", "role": "admin"},
            "permissions": {"can_create": True, "can_update": True, "can_delete": True},
        }

    async def _dispatch(self, **kwargs):
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.permissions),
            ),
            patch(
                "fastmcp_server.tools.context.handle_catalog_action",
                new=AsyncMock(return_value="HANDLER_RESULT"),
            ) as handler,
        ):
            result = await self.tool.fn(token=self.token, request=self.request, **kwargs)
        return result, handler

    async def test_create_dispatches_to_catalog_create(self) -> None:
        result, handler = await self._dispatch(
            action="create",
            title="Native HTML",
            artifact_type="review.html",
            value={"type": "html", "html": "<p>x</p>"},
        )
        self.assertEqual(result, "HANDLER_RESULT")
        self.assertEqual(handler.await_args.args[0], "create")
        kwargs = handler.await_args.kwargs
        self.assertEqual(kwargs["title"], "Native HTML")
        self.assertEqual(kwargs["artifact_type"], "review.html")
        # value falls through to artifact_value
        self.assertEqual(kwargs["artifact_value"], {"type": "html", "html": "<p>x</p>"})
        # space-implied: handler receives ctx with space resolved from the bundle
        self.assertEqual(kwargs["ctx"]["space_id"], "space-1")

    async def test_act_maps_to_invoke_action(self) -> None:
        _, handler = await self._dispatch(
            action="act",
            catalog_entry_id="cat_1",
            action_id="review.basic.approve",
            base_artifact_version_id="artv_1",
            base_state_version_id="state_1",
        )
        self.assertEqual(handler.await_args.args[0], "invoke_action")
        self.assertEqual(handler.await_args.kwargs["catalog_entry_id"], "cat_1")

    async def test_patch_maps_to_propose_patch(self) -> None:
        _, handler = await self._dispatch(
            action="patch",
            catalog_entry_id="cat_1",
            base_artifact_version_id="artv_1",
            base_state_version_id="state_1",
            patch={"patch_type": "html_dom_patch", "payload": {}},
        )
        self.assertEqual(handler.await_args.args[0], "propose_patch")

    async def test_promote_to_catalog_maps_to_create_from_context(self) -> None:
        _, handler = await self._dispatch(action="promote", to="catalog", key="demo-html")
        self.assertEqual(handler.await_args.args[0], "create_from_context")
        # the existing context key flows through as context_key
        self.assertEqual(handler.await_args.kwargs["context_key"], "demo-html")

    async def test_list_tier_catalog_dispatches_to_handler(self) -> None:
        _, handler = await self._dispatch(action="list", tier="catalog")
        self.assertEqual(handler.await_args.args[0], "list")

    async def test_catalog_read_ignores_mismatched_space_id(self) -> None:
        # Reads are not space-switch-guarded; a passed space_id is ignored and the
        # space always resolves from the session (permission bundle).
        _, handler = await self._dispatch(action="list", tier="catalog", space_id="other-space")
        self.assertEqual(handler.await_args.args[0], "list")
        self.assertEqual(handler.await_args.kwargs["ctx"]["space_id"], "space-1")

    async def test_get_tier_catalog_dispatches_to_handler(self) -> None:
        _, handler = await self._dispatch(action="get", tier="catalog", catalog_entry_id="cat_1")
        self.assertEqual(handler.await_args.args[0], "get")
        self.assertEqual(handler.await_args.kwargs["catalog_entry_id"], "cat_1")

    async def test_promote_default_does_not_dispatch_to_catalog(self) -> None:
        # promote without to=catalog must stay on the existing vault path.
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.permissions),
            ),
            patch(
                "fastmcp_server.tools.context.handle_catalog_action",
                new=AsyncMock(),
            ) as handler,
            patch(
                "fastmcp_server.tools.context.api_request",
                new=AsyncMock(return_value={"key": "foo", "status": "promoted"}),
            ),
        ):
            await self.tool.fn(token=self.token, request=self.request, action="promote", key="foo")
        handler.assert_not_awaited()

    async def test_catalog_write_with_mismatched_space_id_is_guarded(self) -> None:
        # create/act/patch are catalog writes — a mismatched space_id must be
        # rejected like set/delete/promote, not silently written to current space.
        for action in ("create", "act", "patch"):
            with (
                patch(
                    "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                    new=AsyncMock(return_value=self.permissions),
                ),
                patch(
                    "fastmcp_server.tools.context.handle_catalog_action",
                    new=AsyncMock(),
                ) as handler,
            ):
                result = await self.tool.fn(
                    token=self.token,
                    request=self.request,
                    action=action,
                    space_id="other-space",
                )
            self.assertEqual(
                result.structured_content["error"], "context_space_switch_required", action
            )
            handler.assert_not_awaited()

    async def test_promote_to_catalog_with_mismatched_space_id_is_guarded(self) -> None:
        # promote(to=catalog) is a catalog write covered by the existing promote
        # entry in _CONTEXT_WRITE_ACTIONS — a mismatched space_id must be rejected.
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.permissions),
            ),
            patch(
                "fastmcp_server.tools.context.handle_catalog_action",
                new=AsyncMock(),
            ) as handler,
        ):
            result = await self.tool.fn(
                token=self.token,
                request=self.request,
                action="promote",
                to="catalog",
                key="demo-html",
                space_id="other-space",
            )
        self.assertEqual(result.structured_content["error"], "context_space_switch_required")
        handler.assert_not_awaited()

    async def test_promote_to_vault_with_mismatched_space_id_is_guarded(self) -> None:
        # Symmetry: promote(to=vault) (the default durable tier) is also guarded.
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.permissions),
            ),
            patch("fastmcp_server.tools.context.handle_catalog_action", new=AsyncMock()) as handler,
            patch("fastmcp_server.tools.context.api_request", new=AsyncMock()) as api,
        ):
            result = await self.tool.fn(
                token=self.token, request=self.request,
                action="promote", to="vault", key="foo", space_id="other-space",
            )
        self.assertEqual(result.structured_content["error"], "context_space_switch_required")
        handler.assert_not_awaited()
        api.assert_not_awaited()

    async def test_get_tier_catalog_missing_id_validation_end_to_end(self) -> None:
        # Full dispatch -> real handler (not mocked): missing catalog_entry_id
        # must surface the catalog validation error without a backend call.
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.permissions),
            ),
            patch(
                "fastmcp_server.tools.context_catalog.api_request_with_context",
                new=AsyncMock(),
            ) as api,
        ):
            result = await self.tool.fn(
                token=self.token, request=self.request, action="get", tier="catalog",
            )
        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_catalog_validation_error")
        self.assertIn("'catalog_entry_id' required", structured["notice"]["message"])
        api.assert_not_awaited()

    async def test_promote_to_catalog_missing_key_validation_end_to_end(self) -> None:
        # Full dispatch -> real handler: promote(to=catalog) maps to
        # create_from_context, which needs a key; the error surfaces the public
        # verb (promote(to=catalog)), not the internal create_from_context.
        with (
            patch(
                "fastmcp_server.tools.context.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.permissions),
            ),
            patch(
                "fastmcp_server.tools.context_catalog.api_request_with_context",
                new=AsyncMock(),
            ) as api,
        ):
            result = await self.tool.fn(
                token=self.token, request=self.request, action="promote", to="catalog",
            )
        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "context_catalog_validation_error")
        self.assertIn("'context_key' required", structured["notice"]["message"])
        self.assertIn("promote(to=catalog)", structured["notice"]["message"])
        self.assertNotIn("create_from_context", structured["notice"]["message"])
        api.assert_not_awaited()


class ListValueBoundingTests(unittest.TestCase):
    """context(list) must return summaries, not full artifact/JSON bodies (#49) —
    prevents the multi-hundred-KB list responses that exceed model token limits."""

    def test_project_list_item_omits_small_value_body(self) -> None:
        from fastmcp_server.tools.context import _project_list_item

        item = {
            "key": "status:brief",
            "value": {"status": "green", "note": "short but still body content"},
            "topic": "status",
        }

        projected = _project_list_item(item)

        self.assertNotIn("value", projected)
        self.assertNotIn("file_content", projected)
        self.assertEqual(projected["value_type"], "object")
        self.assertGreater(projected["value_size"], 0)
        self.assertIn("short but still body content", projected["value_preview"])

    def test_context_widget_result_projects_raw_list_rows(self) -> None:
        from fastmcp_server.tools.context import _context_widget_result

        result = _context_widget_result(
            {"items": [{"key": "raw/item", "value": {"body": "should not leak"}}], "count": 1},
            "list",
        )

        item = result.structured_content["items"][0]
        self.assertNotIn("value", item)
        self.assertEqual(item["value_type"], "object")

    def test_project_list_item_flags_truncated_value(self) -> None:
        from fastmcp_server.tools.context import _project_list_item

        item = {"key": "vault:big", "value": {"blob": "z" * 5000}, "summary": "big index"}
        projected = _project_list_item(item)
        self.assertNotIn("value", projected)
        self.assertTrue(projected["value_omitted"])
        self.assertEqual(projected["value_type"], "object")
        self.assertGreater(projected["value_size"], 5000)
        self.assertEqual(projected["summary"], "big index")

    def test_project_list_item_labels_bare_html_artifact_by_content_type(self) -> None:
        from fastmcp_server.tools.context import _project_list_item

        item = {
            "key": "iframe-host-contract",
            "value": {
                "type": "html",
                "title": "iframe-host contract",
                "html": "<!doctype html><html></html>",
            },
        }
        projected = _project_list_item(item)
        # Value body omitted, but the kind is surfaced so the explorer labels it
        # HTML instead of the generic "ctx" catch-all.
        self.assertNotIn("value", projected)
        self.assertEqual(projected.get("content_type"), "text/html")

    def test_list_projection_preserves_html_kind_end_to_end(self) -> None:
        from fastmcp_server.tools.context import _project_list_item, _context_widget_result

        # Pass 1 (enrich): derive content_type from the value, then omit the body.
        enriched = _project_list_item(
            {"key": "iframe-host-contract", "value": {"type": "html", "html": "<!doctype html>"}}
        )
        self.assertEqual(enriched.get("content_type"), "text/html")
        # Pass 2: _context_widget_result re-extracts (via _normalize_context_item, which
        # must keep content_type) then re-projects the value-less row. content_type must survive.
        res = _context_widget_result({"items": [enriched], "count": 1}, "list")
        self.assertEqual(res.structured_content["items"][0].get("content_type"), "text/html")

    def test_project_list_item_does_not_label_plain_objects(self) -> None:
        from fastmcp_server.tools.context import _project_list_item

        projected = _project_list_item({"key": "status:brief", "value": {"status": "green"}})
        self.assertNotIn("content_type", projected)

    def test_widget_result_respects_requested_limit(self) -> None:
        from fastmcp_server.tools.context import _context_widget_result

        items = [{"key": f"k{i}", "value": {"n": i}} for i in range(120)]
        res = _context_widget_result({"items": items, "count": 120, "limit": 200}, "list")
        # Explicit larger limit shows all 120 (under the hard max), not capped to 50.
        self.assertEqual(res.structured_content["returned"], 120)
        self.assertNotIn("list_capped", res.structured_content)

    def test_widget_result_defaults_to_lean_cap(self) -> None:
        from fastmcp_server.tools.context import _context_widget_result

        items = [{"key": f"k{i}", "value": {"n": i}} for i in range(120)]
        res = _context_widget_result({"items": items, "count": 120}, "list")  # no limit
        self.assertEqual(res.structured_content["returned"], 50)
        self.assertTrue(res.structured_content["list_capped"])

    def test_widget_result_clamps_to_hard_max(self) -> None:
        from fastmcp_server.tools.context import _context_widget_result, _CONTEXT_LIST_HARD_MAX

        items = [{"key": f"k{i}", "value": {"n": i}} for i in range(_CONTEXT_LIST_HARD_MAX + 25)]
        res = _context_widget_result(
            {"items": items, "count": len(items), "limit": 9999}, "list"
        )
        self.assertEqual(res.structured_content["returned"], _CONTEXT_LIST_HARD_MAX)
        self.assertTrue(res.structured_content["list_capped"])


if __name__ == "__main__":
    unittest.main()
