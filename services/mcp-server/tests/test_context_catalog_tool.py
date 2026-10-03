import unittest
from unittest.mock import AsyncMock, patch

from fastmcp_server.tools.context_catalog import handle_catalog_action


class ContextCatalogHandlerTests(unittest.IsolatedAsyncioTestCase):
    """Catalog actions now run through `handle_catalog_action`, dispatched by the
    `context` tool (CTX-ARTIFACTS-002). Space is resolved by the caller, so these
    tests pass an already-resolved ctx and permission bundle."""

    async def asyncSetUp(self) -> None:
        self.ctx = {
            "jwt": "jwt",
            "agent_name": "protocol",
            "agent_id": "agent-1",
            "principal_type": "agent",
            "space_id": "space-1",
        }
        self.ctx_no_space = {**self.ctx, "space_id": None}
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

    async def _call(self, **kwargs):
        action = kwargs.pop("action")
        return await handle_catalog_action(
            action,
            ctx=self.ctx,
            permission_bundle=self.private_permissions,
            **kwargs,
        )

    async def test_missing_space_result_uses_context_contract_without_backend_call(self) -> None:
        with patch(
            "fastmcp_server.tools.context_catalog.api_request_with_context", new=AsyncMock()
        ) as api_request:
            result = await handle_catalog_action(
                "list",
                ctx=self.ctx_no_space,
                permission_bundle=self.private_permissions,
            )

        structured = result.structured_content
        self.assertEqual(structured["kind"], "context")
        self.assertEqual(structured["action"], "list")
        self.assertEqual(structured["state"]["status"], "error")
        self.assertEqual(structured["notice"]["code"], "context_catalog_missing_space")
        api_request.assert_not_awaited()

    async def test_list_defaults_to_metadata_only_backend_request_and_response(self) -> None:
        backend_result = {
            "items": [
                {
                    "id": "cat_123",
                    "title": "Tap Defense review",
                    "artifact_type": "html",
                    "pinned": True,
                    "current_artifact_version_id": "artv_001",
                    "current_artifact": {
                        "id": "artv_001",
                        "sha256": "abc123",
                        "html": "<!doctype html><html>heavy</html>",
                    },
                }
            ],
            "count": 1,
        }
        with patch(
            "fastmcp_server.tools.context_catalog.api_request_with_context",
            new=AsyncMock(return_value=backend_result),
        ) as api_request:
            result = await self._call(action="list")

        request_call = api_request.await_args_list[0]
        self.assertEqual(
            request_call.args[1:],
            ("GET", "/api/v1/spaces/space-1/context-catalog"),
        )
        self.assertEqual(request_call.kwargs["params"], {"include_content": False, "limit": 50})

        structured = result.structured_content
        self.assertEqual(structured["kind"], "context")
        self.assertEqual(structured["version"], 1)
        self.assertEqual(structured["action"], "list")
        self.assertEqual(structured["keys"], ["cat_123"])
        entry = structured["items"][0]
        self.assertEqual(entry["key"], "cat_123")
        self.assertEqual(entry["topic"], "html")
        self.assertEqual(entry["value"]["id"], "cat_123")
        self.assertEqual(entry["value"]["current_artifact"]["id"], "artv_001")
        self.assertEqual(entry["value"]["current_artifact"]["sha256"], "abc123")
        self.assertNotIn("html", entry["value"]["current_artifact"])

    async def test_get_can_opt_into_content_and_surfaces_available_actions(self) -> None:
        backend_result = {
            "id": "cat_123",
            "title": "Review packet",
            "current_artifact": {
                "id": "artv_001",
                "artifact_type": "html",
                "value": {"type": "html", "html": "<p>native</p>"},
            },
            "available_actions": [
                {
                    "id": "review.basic.approve",
                    "canonical_label": "Approve",
                    "confirmation": "required",
                    "source_lane": "trusted_named_action",
                }
            ],
        }
        with patch(
            "fastmcp_server.tools.context_catalog.api_request_with_context",
            new=AsyncMock(return_value=backend_result),
        ) as api_request:
            result = await self._call(
                action="get",
                catalog_entry_id="cat_123",
                include_content=True,
            )

        request_call = api_request.await_args_list[0]
        self.assertEqual(
            request_call.args[1:],
            ("GET", "/api/v1/spaces/space-1/context-catalog/cat_123"),
        )
        self.assertEqual(request_call.kwargs["params"], {"include_content": True})

        structured = result.structured_content
        self.assertEqual(structured["kind"], "context")
        self.assertEqual(structured["action"], "get")
        self.assertEqual(structured["selected_key"], "cat_123")
        self.assertEqual(structured["items"][0]["value"]["current_artifact"]["value"]["type"], "html")
        self.assertEqual(
            structured["catalog_actions"][0],
            {
                "id": "review.basic.approve",
                "canonical_label": "Approve",
                "label": "Approve",
                "confirmation": "required",
                "source_lane": "trusted_named_action",
            },
        )

    async def test_create_from_context_bridges_existing_native_context_key(self) -> None:
        with patch(
            "fastmcp_server.tools.context_catalog.api_request_with_context",
            new=AsyncMock(return_value={"id": "cat_123", "artifact_type": "review.html"}),
        ) as api_request:
            result = await self._call(
                action="create_from_context",
                context_key="demo-html-tictactoe",
                title="Native HTML",
                pinned=True,
            )

        request_call = api_request.await_args_list[0]
        self.assertEqual(
            request_call.args[1:],
            ("POST", "/api/v1/spaces/space-1/context-catalog/from-context"),
        )
        self.assertEqual(
            request_call.kwargs["json_data"],
            {
                "key": "demo-html-tictactoe",
                "action_set_id": "review.basic",
                "title": "Native HTML",
                "pinned": True,
            },
        )
        self.assertEqual(result.structured_content["items"][0]["value"]["id"], "cat_123")

    async def test_create_hashes_structured_artifact_value_for_direct_catalog_create(self) -> None:
        artifact_value = {"type": "html", "html": "<!doctype html><p>native</p>"}
        with patch(
            "fastmcp_server.tools.context_catalog.api_request_with_context",
            new=AsyncMock(return_value={"id": "cat_123", "artifact": {"kind": "html"}}),
        ) as api_request:
            result = await self._call(
                action="create",
                title="Native HTML",
                artifact_type="review.html",
                artifact_value=artifact_value,
                pinned=True,
            )

        request_call = api_request.await_args_list[0]
        self.assertEqual(request_call.args[1:], ("POST", "/api/v1/spaces/space-1/context-catalog"))
        payload = request_call.kwargs["json_data"]
        self.assertEqual(payload["artifact_type"], "review.html")
        self.assertEqual(payload["artifact_kind"], "html")
        self.assertEqual(payload["action_set_id"], "review.basic")
        self.assertNotIn("content_ref", payload)
        self.assertEqual(len(payload["sha256"]), 64)
        self.assertGreater(payload["size_bytes"], 0)

        structured = result.structured_content
        self.assertEqual(structured["items"][0]["value"]["artifact"]["kind"], "html")

    async def test_action_forwards_base_versions_and_returns_conflict_state(self) -> None:
        conflict = {
            "code": "stale_base_version",
            "base_artifact_version_id": "artv_old",
            "current_artifact_version_id": "artv_new",
        }
        with patch(
            "fastmcp_server.tools.context_catalog.api_request_with_context",
            new=AsyncMock(return_value={"status": "conflict", "conflict": conflict}),
        ) as api_request:
            result = await self._call(
                action="invoke_action",
                catalog_entry_id="cat_123",
                action_id="review.basic.approve",
                base_artifact_version_id="artv_old",
                base_state_version_id="state_001",
                idempotency_key="idem-1",
                payload={"comment": "Ship it"},
            )

        request_call = api_request.await_args_list[0]
        self.assertEqual(
            request_call.args[1:],
            ("POST", "/api/v1/spaces/space-1/context-catalog/cat_123/actions"),
        )
        self.assertEqual(
            request_call.kwargs["json_data"],
            {
                "action_id": "approve",
                "base_artifact_version_id": "artv_old",
                "base_state_version_id": "state_001",
                "idempotency_key": "idem-1",
                "payload": {"comment": "Ship it"},
            },
        )
        self.assertEqual(result.structured_content["state"]["conflict"], conflict)

    async def test_auto_action_idempotency_key_includes_payload_hash(self) -> None:
        with patch(
            "fastmcp_server.tools.context_catalog.api_request_with_context",
            new=AsyncMock(return_value={"status": "approved"}),
        ) as api_request:
            await self._call(
                action="invoke_action",
                catalog_entry_id="cat_123",
                action_id="review.basic.approve",
                base_artifact_version_id="artv_001",
                base_state_version_id="state_001",
                payload={"comment": "Ship it"},
            )

        payload = api_request.await_args_list[0].kwargs["json_data"]
        self.assertRegex(payload["idempotency_key"], r"^approve:state_001:[0-9a-f]{8}$")

    async def test_auto_patch_idempotency_key_includes_patch_payload_hash(self) -> None:
        patch_payload = {
            "patch_type": "html_dom_patch",
            "payload": {"operations": [{"op": "replace_text", "selector": "#title", "value": "New"}]},
        }
        with patch(
            "fastmcp_server.tools.context_catalog.api_request_with_context",
            new=AsyncMock(return_value={"status": "proposed", "patch_id": "patch_123"}),
        ) as api_request:
            await self._call(
                action="propose_patch",
                catalog_entry_id="cat_123",
                base_artifact_version_id="artv_001",
                base_state_version_id="state_001",
                patch=patch_payload,
            )

        payload = api_request.await_args_list[0].kwargs["json_data"]
        self.assertRegex(payload["idempotency_key"], r"^patch:html_dom_patch:state_001:[0-9a-f]{8}$")

    async def test_write_blocked_result_uses_context_contract_without_backend_call(self) -> None:
        blocked_permissions = {
            **self.private_permissions,
            "permissions": {
                **self.private_permissions["permissions"],
                "can_create": False,
                "can_update": False,
                "blocked_reason": "Space is read only.",
            },
        }
        with patch(
            "fastmcp_server.tools.context_catalog.api_request_with_context", new=AsyncMock()
        ) as api_request:
            result = await handle_catalog_action(
                "create",
                ctx=self.ctx,
                permission_bundle=blocked_permissions,
                title="Blocked",
                artifact_type="review.html",
                artifact_value={"title": "Blocked"},
            )

        self.assertEqual(result.structured_content["kind"], "context")
        self.assertEqual(result.structured_content["action"], "create")
        self.assertEqual(result.structured_content["notice"]["code"], "context_catalog_write_blocked")
        api_request.assert_not_awaited()

    async def test_get_validation_error_uses_context_contract_without_backend_call(self) -> None:
        with patch(
            "fastmcp_server.tools.context_catalog.api_request_with_context", new=AsyncMock()
        ) as api_request:
            result = await self._call(action="get")

        structured = result.structured_content
        self.assertEqual(structured["kind"], "context")
        self.assertEqual(structured["action"], "get")
        self.assertEqual(structured["state"]["status"], "error")
        self.assertEqual(structured["notice"]["code"], "context_catalog_validation_error")
        self.assertIn("'catalog_entry_id' required", structured["notice"]["message"])
        self.assertEqual(structured["items"], [])
        api_request.assert_not_awaited()

    async def test_invoke_action_validation_error_uses_context_contract_without_backend_call(self) -> None:
        with patch(
            "fastmcp_server.tools.context_catalog.api_request_with_context", new=AsyncMock()
        ) as api_request:
            result = await self._call(
                action="invoke_action",
                catalog_entry_id="cat_123",
                base_artifact_version_id="artv_001",
                base_state_version_id="state_001",
            )

        structured = result.structured_content
        self.assertEqual(structured["kind"], "context")
        self.assertEqual(structured["action"], "invoke_action")
        self.assertEqual(structured["state"]["status"], "error")
        self.assertEqual(structured["notice"]["code"], "context_catalog_validation_error")
        self.assertIn("'action_id' required", structured["notice"]["message"])
        # Error message surfaces the public verb (act), not the internal name.
        self.assertIn("for act action", structured["notice"]["message"])
        self.assertNotIn("invoke_action", structured["notice"]["message"])
        api_request.assert_not_awaited()

    async def test_propose_patch_validation_error_uses_context_contract_without_backend_call(self) -> None:
        with patch(
            "fastmcp_server.tools.context_catalog.api_request_with_context", new=AsyncMock()
        ) as api_request:
            result = await self._call(
                action="propose_patch",
                catalog_entry_id="cat_123",
                base_artifact_version_id="artv_001",
                base_state_version_id="state_001",
            )

        structured = result.structured_content
        self.assertEqual(structured["kind"], "context")
        self.assertEqual(structured["action"], "propose_patch")
        self.assertEqual(structured["state"]["status"], "error")
        self.assertEqual(structured["notice"]["code"], "context_catalog_validation_error")
        self.assertIn("'patch' required", structured["notice"]["message"])
        self.assertIn("for patch action", structured["notice"]["message"])
        self.assertNotIn("propose_patch", structured["notice"]["message"])
        api_request.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
