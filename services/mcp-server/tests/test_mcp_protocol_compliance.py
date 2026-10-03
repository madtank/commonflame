"""Protocol-level MCP transport compliance regression tests."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import jsonschema
from sse_starlette.sse import AppStatus
from fastmcp import FastMCP
from fastmcp.server.http import create_streamable_http_app
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from fastmcp_server.mcp_ui import (
    WIDGET_RESOURCE_DOMAINS,
    build_notice,
    build_tool_output,
    read_only_annotations,
    resource_app_config,
    resource_meta,
    tool_action_forms,
    tool_app_config,
    tool_meta,
    tool_output_schema,
    widget_tool_result,
)
from fastmcp_server.server import create_app, server as ax_mcp_server


_ACCEPT_HEADER = {"accept": "application/json, text/event-stream"}

# Tools already wired with output_schema= at their @mcp.tool site
# (OUTPUT-SCHEMA-001). Extend as the remaining decorators are wired; the
# end state is all 7 platform tools.
SCHEMA_WIRED_TOOLS = ["agents", "context", "messages", "search", "spaces", "whoami", "tasks"]

# Expected `kind` enums per spec §2.3 (specs/MCP-APPS/output-schema.md).
# Drift here means a new envelope kind was added without updating
# _TOOL_OUTPUT_SHAPES (or vice versa).
EXPECTED_KIND_ENUMS = {
    "whoami": ["whoami_profile"],
    "messages": [
        "message_error",
        "message_timeline",
        "message_draft",
        "message_mutation",
        "message_sent",
    ],
    "tasks": ["task_collection", "task_detail", "task_reminder_pause"],
    "agents": ["agent_collection", "agent_groups"],
    "spaces": ["space_detail", "space_collection", "space_members"],
    "context": ["context"],
    "search": ["search_results"],
}


def _schema_kind_enum(schema: dict) -> list | None:
    """Extract the advertised kind enum from a tool outputSchema."""
    for variant in schema.get("anyOf", [schema]):
        kind = variant.get("properties", {}).get("kind", {})
        if "enum" in kind:
            return kind["enum"]
    return None


def _reset_sse_app_status() -> None:
    AppStatus.should_exit = False
    AppStatus.should_exit_event = None


def _decode_sse_jsonrpc(response) -> dict:
    payload = response.text
    for line in payload.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError(f"No JSON-RPC payload found in response: {payload!r}")


class MCPProtocolTransportTests(unittest.TestCase):
    def call_mcp(self, method: str, params: dict | None = None, *, request_id: str | int = "1") -> dict:
        _reset_sse_app_status()
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            },
            headers=_ACCEPT_HEADER,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "text/event-stream")
        return _decode_sse_jsonrpc(response)

    def test_initialize_without_token_returns_oauth_discovery_challenge(self) -> None:
        _reset_sse_app_status()
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": "initialize",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "0"},
                    },
                },
                headers={**_ACCEPT_HEADER, "x-agent-name": "protocol tester"},
            )

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["error"], "authorization_required")
        challenge = response.headers["WWW-Authenticate"]
        self.assertIn('Bearer realm="mcp"', challenge)
        self.assertIn("resource_metadata=", challenge)
        self.assertIn("/.well-known/oauth-protected-resource/mcp/agents/protocol%20tester", challenge)
        self.assertIn("resource=", challenge)
        self.assertIn("/mcp/agents/protocol%20tester", challenge)

    def test_anonymous_initialized_notification_does_not_open_stateful_session(self) -> None:
        _reset_sse_app_status()
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                headers={**_ACCEPT_HEADER, "origin": "https://glama.ai"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.text, "")
        self.assertEqual(response.headers["access-control-allow-origin"], "*")

    def test_anonymous_logging_setlevel_does_not_open_stateful_session(self) -> None:
        payload = self.call_mcp(
            "logging/setLevel",
            {"level": "debug"},
            request_id="logging-set-level",
        )

        self.assertEqual(payload["id"], "logging-set-level")
        self.assertEqual(payload["error"]["code"], -32601)
        self.assertIn("Method not found", payload["error"]["message"])

    def test_tools_list_exposes_openai_metadata_and_annotations_for_all_registered_tools(self) -> None:
        payload = self.call_mcp("tools/list", request_id="tools-list")
        tools = payload["result"]["tools"]
        self.assertGreaterEqual(len(tools), 7)

        for tool in tools:
            with self.subTest(tool=tool["name"]):
                annotations = tool["annotations"]
                meta = tool["_meta"]
                self.assertIn("readOnlyHint", annotations)
                self.assertIn("destructiveHint", annotations)
                self.assertIn("openWorldHint", annotations)
                self.assertEqual(meta["securitySchemes"][0]["type"], "oauth2")
                self.assertEqual(
                    meta["securitySchemes"][0]["scopes"],
                    ["openid", "ax-api/mcp:read", "ax-api/mcp:write"],
                )
                self.assertTrue(meta["openai/outputTemplate"].startswith("ui://"))
                self.assertTrue(meta["openai/widgetAccessible"])
                self.assertEqual(meta["ui"]["resourceUri"], meta["openai/outputTemplate"])

    def test_glama_score_metadata_treats_context_as_bounded_first_party_tool(self) -> None:
        payload = self.call_mcp("tools/list", request_id="tools-list-context-glama")
        tools = {tool["name"]: tool for tool in payload["result"]["tools"]}
        context_tool = tools["context"]

        self.assertEqual(
            context_tool["annotations"],
            {
                "readOnlyHint": False,
                "destructiveHint": False,
                "openWorldHint": False,
            },
        )
        context_description = context_tool["description"].lower()
        self.assertIn("bounded", context_description)
        self.assertIn("permission-gated", context_description)

    def test_context_metadata_advertises_action_specific_parameter_contract(self) -> None:
        payload = self.call_mcp("tools/list", request_id="tools-list-context-actions")
        tools = {tool["name"]: tool for tool in payload["result"]["tools"]}
        contract = tools["context"]["_meta"]["ax/actionForms"]

        self.assertEqual(contract["default_mode"], "simple_key_value")
        self.assertNotIn(
            "catalog_entry_id",
            contract["actions"]["set"]["parameters"],
        )
        self.assertEqual(
            contract["actions"]["get_catalog"]["required"],
            ["action", "tier", "catalog_entry_id"],
        )
        self.assertEqual(contract["actions"]["act"]["mode"], "governed_catalog")
        self.assertIn(
            "base_artifact_version_id",
            contract["actions"]["act"]["required"],
        )
        self.assertNotIn("offset", contract["actions"]["list_catalog"]["parameters"])
        self.assertNotIn("idempotency_key", contract["actions"]["create"]["parameters"])

    def test_glama_score_metadata_documents_whoami_action_parameters(self) -> None:
        payload = self.call_mcp("tools/list", request_id="tools-list-whoami-glama")
        tools = {tool["name"]: tool for tool in payload["result"]["tools"]}
        whoami_schema = tools["whoami"]["inputSchema"]
        properties = whoami_schema["properties"]

        required_fields = {
            "action": "use get",
            "bio": "update",
            "specialization": "update",
            "capabilities": "update",
            "preferences": "update",
            "projects": "update",
            "avatar_url": "update",
            "avatar_emoji": "update",
            "key": "remember",
            "value": "remember",
            "target_agent": "follow",
            "relationship_type": "follow",
            "space_id": "accepted",
        }
        for name, expected_text in required_fields.items():
            with self.subTest(parameter=name):
                self.assertIn(name, properties)
                description = properties[name].get("description", "").lower()
                self.assertIn(expected_text, description)

    def test_glama_score_metadata_every_tool_parameter_has_a_description(self) -> None:
        payload = self.call_mcp("tools/list", request_id="tools-list-param-descriptions")
        tools = payload["result"]["tools"]
        self.assertGreaterEqual(len(tools), 7)

        for tool in tools:
            properties = tool["inputSchema"].get("properties", {})
            self.assertTrue(properties, f"tool {tool['name']} exposes no parameters")
            for parameter_name, parameter_schema in properties.items():
                with self.subTest(tool=tool["name"], parameter=parameter_name):
                    description = parameter_schema.get("description", "")
                    self.assertTrue(
                        description.strip(),
                        f"{tool['name']}.{parameter_name} is missing a parameter description",
                    )

    def test_host_metadata_advertises_action_specific_forms_for_widgets(self) -> None:
        payload = self.call_mcp("tools/list", request_id="tools-list-action-forms")
        tools = {tool["name"]: tool for tool in payload["result"]["tools"]}

        expected_tools = {"agents", "messages", "search", "spaces", "whoami", "tasks"}
        for tool_name in expected_tools:
            with self.subTest(tool=tool_name):
                contract = tools[tool_name]["_meta"]["ax/actionForms"]
                self.assertEqual(contract, tool_action_forms(tool_name))
                self.assertEqual(contract["version"], 1)
                self.assertIn(contract["default_mode"], {form["mode"] for form in contract["actions"].values()})

                schema_parameters = set(tools[tool_name]["inputSchema"].get("properties", {}))
                advertised = {
                    parameter
                    for form in contract["actions"].values()
                    for parameter in form["parameters"]
                }
                hidden = set(contract.get("hidden_parameters", []))
                self.assertLessEqual(advertised | hidden, schema_parameters)
                for action_name, form in contract["actions"].items():
                    with self.subTest(tool=tool_name, action=action_name):
                        self.assertLessEqual(set(form["required"]), set(form["parameters"]))
                        self.assertTrue(
                            set(form["parameters"]).isdisjoint(hidden),
                            f"{tool_name}.{action_name} advertises globally hidden parameters",
                        )

        agents_contract = tools["agents"]["_meta"]["ax/actionForms"]
        agents_update = agents_contract["actions"]["update"]
        self.assertNotIn("name", agents_update["parameters"])
        self.assertIn("model", agents_update["parameters"])
        self.assertIn("status", agents_update["parameters"])
        self.assertIn("enabled_tools", agents_update["parameters"])
        self.assertIn("declared_capabilities", agents_update["parameters"])
        self.assertEqual(
            agents_contract["actions"]["set_placement"]["required"],
            ["action", "agent_id", "space_id", "pinned"],
        )
        whoami_update = tools["whoami"]["_meta"]["ax/actionForms"]["actions"]["update"]
        self.assertIn("avatar_url", whoami_update["parameters"])
        self.assertIn("avatar_emoji", whoami_update["parameters"])

        task_meta = tools["tasks"]["_meta"]
        self.assertNotIn("ax/actionParameterContract", task_meta)
        task_contract = task_meta["ax/actionForms"]
        self.assertEqual(task_contract["actions"]["nudge"]["parameters"], ["action", "task_id"])
        self.assertNotIn("description", task_contract["actions"]["list"]["parameters"])
        self.assertNotIn("snoozed_until", task_contract["actions"]["create"]["parameters"])
        update_form = task_contract["actions"]["update"]
        self.assertEqual(update_form["required"], ["task_id"])
        self.assertIn("task_id", update_form["parameters"])
        self.assertNotIn("reminder_interval_minutes", update_form["parameters"])
        self.assertNotIn("next_reminder_at", update_form["parameters"])
        self.assertNotIn("cancel_reminder", update_form["parameters"])

    def test_apps_registry_exposes_widget_action_form_metadata(self) -> None:
        _reset_sse_app_status()
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.get("/apps/")

        self.assertEqual(response.status_code, 200, response.text)
        registry = response.json()
        self.assertIn("action_forms", registry["tasks"])
        self.assertNotIn("action_parameter_contract", registry["tasks"])
        self.assertEqual(
            registry["tasks"]["action_forms"]["actions"]["nudge"]["required"],
            ["action", "task_id"],
        )
        self.assertEqual(
            registry["context"]["action_forms"]["actions"]["get_catalog"]["required"],
            ["action", "tier", "catalog_entry_id"],
        )
        self.assertEqual(registry["messages"]["surface"], "timeline")

    def test_resources_read_without_token_returns_oauth_challenge(self) -> None:
        _reset_sse_app_status()
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": "resource-read",
                    "method": "resources/read",
                    "params": {"uri": "ui://whoami/identity"},
                },
                headers=_ACCEPT_HEADER,
            )

        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["error"], "authorization_required")
        self.assertIn("WWW-Authenticate", response.headers)

    def test_registered_widget_resource_still_serves_openai_metadata_on_positive_path(self) -> None:
        """Keep positive-path coverage for widget HTML/resource metadata."""
        _reset_sse_app_status()
        mcp_app = create_streamable_http_app(
            server=ax_mcp_server,
            streamable_http_path="/mcp",
            auth=None,
            stateless_http=True,
        )
        app = Starlette(routes=[Mount("/", app=mcp_app)], lifespan=mcp_app.lifespan)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": "resource-read-positive",
                    "method": "resources/read",
                    "params": {"uri": "ui://whoami/identity"},
                },
                headers=_ACCEPT_HEADER,
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = _decode_sse_jsonrpc(response)
        content = payload["result"]["contents"][0]
        self.assertEqual(content["uri"], "ui://whoami/identity")
        self.assertEqual(content["mimeType"], "text/html;profile=mcp-app")
        self.assertIn("<!DOCTYPE html>", content["text"])
        self.assertIn("Agent identity card", content["_meta"]["openai/widgetDescription"])
        self.assertTrue(content["_meta"]["openai/widgetPrefersBorder"])
        self.assertEqual(
            content["_meta"]["openai/widgetCSP"]["resource_domains"],
            WIDGET_RESOURCE_DOMAINS,
        )

    def test_glama_connector_metadata_is_public_and_env_configured(self) -> None:
        _reset_sse_app_status()
        with patch.dict(
            "os.environ",
            {"GLAMA_MAINTAINER_EMAILS": "owner@example.com, ops@example.com"},
        ):
            with TestClient(create_app(), raise_server_exceptions=False) as client:
                response = client.get("/.well-known/glama.json")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(
            response.json(),
            {
                "$schema": "https://glama.ai/mcp/schemas/connector.json",
                "maintainers": [
                    {"email": "owner@example.com"},
                    {"email": "ops@example.com"},
                ],
            },
        )


class AnonymousDiscoveryHardeningTests(unittest.TestCase):
    """MCP-DISCOVERY-001 review fixes for #301: body cap (P1) + CORS (P2)."""

    def test_oversized_anonymous_body_is_rejected_not_buffered(self) -> None:
        # An unauthenticated POST /mcp past the discovery body cap must be
        # rejected cleanly (413) instead of buffered in full — otherwise the
        # discovery middleware reintroduces a memory-pressure DoS. Build the
        # body just over the actual cap so this stays a true boundary test if
        # the constant changes.
        from fastmcp_server.server import MAX_ANONYMOUS_DISCOVERY_BODY_BYTES

        _reset_sse_app_status()
        padding = "x" * (MAX_ANONYMOUS_DISCOVERY_BODY_BYTES + 1)
        with patch.dict("os.environ", {"CORS_ORIGINS": "https://glama.ai"}):
            with TestClient(create_app(), raise_server_exceptions=False) as client:
                response = client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": "big",
                        "method": "tools/list",
                        "params": {"pad": padding},
                    },
                    headers={**_ACCEPT_HEADER, "origin": "https://glama.ai"},
                )
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"), "https://glama.ai"
        )

    def test_discovery_response_carries_cors_for_allowed_origin(self) -> None:
        # Browser cross-origin discovery: the short-circuit response must carry
        # Access-Control-Allow-Origin for a configured origin.
        _reset_sse_app_status()
        with patch.dict("os.environ", {"CORS_ORIGINS": "https://glama.ai"}):
            with TestClient(create_app(), raise_server_exceptions=False) as client:
                response = client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": "1",
                        "method": "tools/list",
                        "params": {},
                    },
                    headers={**_ACCEPT_HEADER, "origin": "https://glama.ai"},
                )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"), "https://glama.ai"
        )

    def test_anonymous_initialize_is_oauth_challenge_not_protocol_echo(self) -> None:
        # Initialize is protected by policy. Unsupported protocolVersion strings
        # must not be reflected by a public discovery response; clients should
        # discover OAuth metadata from the transport-level 401 and retry authed.
        _reset_sse_app_status()
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": "init",
                    "method": "initialize",
                    "params": {"protocolVersion": "not-a-real-version<script>"},
                },
                headers=_ACCEPT_HEADER,
            )
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["error"], "authorization_required")
        self.assertIn("WWW-Authenticate", response.headers)
        self.assertNotIn("not-a-real-version<script>", response.text)


    def test_anonymous_ping_returns_jsonrpc_empty_result_without_session(self) -> None:
        # Glama/catalog liveness probes can issue MCP ping before authenticating.
        # Keep it side-effect-free and do not pass it into FastMCP's stateful
        # session manager, which can leave anonymous probes waiting for timeout.
        _reset_sse_app_status()
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": "ping", "method": "ping", "params": {}},
                headers=_ACCEPT_HEADER,
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = _decode_sse_jsonrpc(response)
        self.assertEqual(payload, {"jsonrpc": "2.0", "id": "ping", "result": {}})

    def test_origin_allowed_helper(self) -> None:
        from fastmcp_server.server import _origin_allowed

        self.assertTrue(_origin_allowed("https://glama.ai", ["https://glama.ai"]))
        self.assertTrue(_origin_allowed("https://anything", ["*"]))
        self.assertFalse(_origin_allowed("https://evil.example", ["https://glama.ai"]))
        self.assertFalse(_origin_allowed("https://x", []))

    def test_discovery_response_cors_wildcard(self) -> None:
        # Default CORS_ORIGINS="*" must echo a wildcard allow-origin so open
        # browser discovery works.
        _reset_sse_app_status()
        with patch.dict("os.environ", {"CORS_ORIGINS": "*"}):
            with TestClient(create_app(), raise_server_exceptions=False) as client:
                response = client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": {}},
                    headers={**_ACCEPT_HEADER, "origin": "https://example.com"},
                )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "*")

    def test_anonymous_tools_list_is_cached_and_stable(self) -> None:
        # HEALTH-RESILIENCE-001: repeated anonymous tools/list must return an
        # identical tools array and build it only once (the per-call Pydantic
        # serialization is the CPU sink that wedges /health under scan load).
        _reset_sse_app_status()
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            def fetch(req_id: str) -> dict:
                resp = client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}},
                    headers=_ACCEPT_HEADER,
                )
                self.assertEqual(resp.status_code, 200, resp.text)
                return _decode_sse_jsonrpc(resp)

            first = fetch("a")
            second = fetch("b")
        # Same tools payload, only the JSON-RPC id differs.
        self.assertEqual(first["result"], second["result"])
        self.assertEqual(first["id"], "a")
        self.assertEqual(second["id"], "b")
        self.assertGreaterEqual(len(first["result"]["tools"]), 7)

    def test_anonymous_unknown_method_is_cheap_rejected_not_replayed(self) -> None:
        # HEALTH-RESILIENCE-001: a non-MCP vendor method must get a cheap
        # -32601 from the discovery middleware, never replaying into the full
        # stack / Pydantic ClientRequest validation that pegs CPU under scan.
        _reset_sse_app_status()
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": "vendor",
                    "method": "ai.smithery/events/list",
                    "params": {},
                },
                headers=_ACCEPT_HEADER,
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = _decode_sse_jsonrpc(response)
        self.assertEqual(payload["id"], "vendor")
        self.assertEqual(payload["error"]["code"], -32601)

    def test_anonymous_protected_method_still_gets_auth_challenge(self) -> None:
        # HEALTH-RESILIENCE-001: a real protected method (tools/call) must still
        # replay into the authed stack so the client receives the OAuth 401
        # challenge — the fast-reject must not swallow auth discovery.
        _reset_sse_app_status()
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": "call",
                    "method": "tools/call",
                    "params": {"name": "whoami", "arguments": {}},
                },
                headers=_ACCEPT_HEADER,
            )
        # Not a cheap -32601 method-not-found; the protected method reaches the
        # authed stack (401 challenge / auth-required), proving the allowlist
        # preserves auth discovery.
        self.assertNotEqual(response.status_code, 200)


class MCPProtocolToolCallShapeTests(unittest.TestCase):
    @staticmethod
    def _create_protocol_test_app() -> Starlette:
        mcp = FastMCP(name="Protocol Test MCP")

        @mcp.resource(
            "ui://whoami/identity",
            title="Agent Identity",
            description="Protocol test widget resource.",
            app=resource_app_config(),
            meta=resource_meta("whoami"),
        )
        async def _resource() -> str:
            return "<!DOCTYPE html><html><body>Protocol widget</body></html>"

        @mcp.tool(
            annotations=read_only_annotations(),
            app=tool_app_config("whoami"),
            meta=tool_meta("whoami"),
        )
        async def whoami() -> object:
            return widget_tool_result(
                "whoami",
                action="get",
                content="Protocol widget ready",
                structured_content=build_tool_output(
                    "identity_card",
                    1,
                    "ready",
                    {"identity": {"handle": "protocol-bot"}},
                ),
            )

        mcp_app = create_streamable_http_app(
            server=mcp,
            streamable_http_path="/mcp",
            stateless_http=True,
        )
        return Starlette(routes=[Mount("/", app=mcp_app)], lifespan=mcp_app.lifespan)

    def call_mcp(self, method: str, params: dict | None = None, *, request_id: str | int = "1") -> dict:
        _reset_sse_app_status()
        with TestClient(self._create_protocol_test_app(), raise_server_exceptions=False) as client:
            response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            },
            headers=_ACCEPT_HEADER,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return _decode_sse_jsonrpc(response)

    def test_tools_call_serializes_structured_content_and_widget_result_meta(self) -> None:
        payload = self.call_mcp(
            "tools/call",
            {"name": "whoami", "arguments": {}},
            request_id="tool-call",
        )

        result = payload["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["text"], "Protocol widget ready")
        self.assertEqual(result["structuredContent"]["kind"], "identity_card")
        self.assertEqual(result["structuredContent"]["state"], "ready")
        self.assertEqual(
            result["structuredContent"]["data"]["identity"]["handle"],
            "protocol-bot",
        )
        self.assertEqual(result["_meta"]["ui"]["resourceUri"], "ui://whoami/identity")
        self.assertEqual(result["_meta"]["ui/resourceUri"], "ui://whoami/identity")


class ToolOutputSchemaTests(unittest.TestCase):
    """outputSchema advertisement + truthfulness gates (OUTPUT-SCHEMA-001)."""

    def call_mcp(self, method: str, params: dict | None = None, *, request_id: str | int = "1") -> dict:
        _reset_sse_app_status()
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            },
            headers=_ACCEPT_HEADER,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return _decode_sse_jsonrpc(response)

    def _declared_output_schemas(self) -> dict[str, dict]:
        payload = self.call_mcp("tools/list", request_id="tools-list-output-schemas")
        return {
            tool["name"]: tool["outputSchema"]
            for tool in payload["result"]["tools"]
            if tool.get("outputSchema") is not None
        }

    def test_declared_output_schemas_are_valid_envelope_object_schemas(self) -> None:
        declared = self._declared_output_schemas()
        self.assertTrue(declared, "no tool advertises an outputSchema")

        for tool_name, schema in declared.items():
            with self.subTest(tool=tool_name):
                self.assertIsInstance(schema, dict)
                # Syntactically valid JSON Schema (same library the MCP SDK
                # uses for output validation at runtime).
                jsonschema.validators.validator_for(schema).check_schema(schema)
                # MCP requires a top-level object schema; every anyOf variant
                # must also be an object.
                self.assertEqual(schema.get("type"), "object")
                for variant in schema.get("anyOf", [schema]):
                    self.assertEqual(variant.get("type"), "object")
                # Envelope shape: a closed kind enum matching spec §2.3.
                kind_enum = _schema_kind_enum(schema)
                self.assertIsNotNone(
                    kind_enum,
                    f"{tool_name} outputSchema has no kind enum",
                )
                self.assertIn(tool_name, EXPECTED_KIND_ENUMS)
                self.assertEqual(kind_enum, EXPECTED_KIND_ENUMS[tool_name])

    def test_schema_wired_tools_advertise_output_schema(self) -> None:
        declared = self._declared_output_schemas()
        for tool_name in SCHEMA_WIRED_TOOLS:
            with self.subTest(tool=tool_name):
                self.assertIn(
                    tool_name,
                    declared,
                    f"{tool_name} is wired but tools/list has no outputSchema",
                )
                self.assertEqual(declared[tool_name], tool_output_schema(tool_name))

    def test_builder_rejects_unknown_tools_and_schemas_serialize(self) -> None:
        with self.assertRaises(KeyError):
            tool_output_schema("nope")
        for tool_name in EXPECTED_KIND_ENUMS:
            with self.subTest(tool=tool_name):
                schema = tool_output_schema(tool_name)
                jsonschema.validators.validator_for(schema).check_schema(schema)
                self.assertEqual(schema, json.loads(json.dumps(schema)))

    def test_representative_payloads_validate_against_builder_schemas(self) -> None:
        bare_error = {"error": "space_id is required"}
        backend_error = {"error": "API error 500", "detail": "internal error"}
        fixtures: dict[str, list[dict]] = {
            "whoami": [
                build_tool_output(
                    "whoami_profile",
                    2,
                    "ready",
                    {"identity": {"agent_name": "protocol"}},
                    actions=[{"id": "refresh", "label": "Refresh"}],
                )
                | {"active_tab": "profile", "notice": build_notice("Saved.")},
                # Raw fallback branch: follow success + list-error fallback.
                {"action": "follow", "target_agent": "orion", "status": "ok"},
                {"action": "list", "memories": [], "count": 0},
            ],
            "messages": [
                build_tool_output(
                    "message_timeline",
                    2,
                    "ready",
                    {"messages": [], "count": 0},
                )
                | {"notice": build_notice("Inbox empty.")},
                build_tool_output(
                    "message_error",
                    2,
                    "error",
                    {},
                    errors=[{"message": "content is required"}],
                ),
            ],
            "tasks": [
                build_tool_output("task_collection", 2, "ready", {"tasks": []}),
                bare_error,
                backend_error,
                {
                    "error": "Task status change was not persisted",
                    "code": "task_status_not_persisted",
                    "task": {"id": "t-1"},
                    "requested_status": "completed",
                    "actual_status": "open",
                },
            ],
            "agents": [
                build_tool_output("agent_collection", 2, "ready", {"agents": []}),
                bare_error,
                backend_error,
            ],
            "spaces": [
                build_tool_output("space_collection", 2, "ready", {"spaces": []}),
                bare_error,
                backend_error,
            ],
            "context": [
                # Ephemeral shape: version 2, flat keys, no state/data.
                {
                    "kind": "context",
                    "version": 2,
                    "action": "list",
                    "count": 1,
                    "keys": ["design/nav"],
                    "items": [{"key": "design/nav"}],
                    "notice": build_notice("1 key."),
                },
                # Catalog shape: version 1, object state, nested catalog.
                {
                    "kind": "context",
                    "version": 1,
                    "state": {"action": "list", "status": "ok", "conflict": None},
                    "catalog": {"entries": []},
                    "catalog_actions": [{"id": "promote"}],
                },
                bare_error,
                backend_error,
            ],
            "search": [
                build_tool_output(
                    "search_results",
                    2,
                    "empty",
                    {
                        "query": "deploy",
                        "items": [],
                        "count": 0,
                        "total": 0,
                        "has_more": False,
                        "pagination": {"limit": 20, "offset": 0, "next_offset": None},
                    },
                    actions=[{"id": "next_page", "label": "Next"}],
                ),
            ],
        }
        self.assertEqual(set(fixtures), set(EXPECTED_KIND_ENUMS))
        for tool_name, payloads in fixtures.items():
            schema = tool_output_schema(tool_name)
            for index, payload in enumerate(payloads):
                with self.subTest(tool=tool_name, fixture=index):
                    jsonschema.validate(payload, schema)

    def test_empty_and_bogus_kind_payloads_fail_for_every_tool(self) -> None:
        bogus = {"kind": "bogus", "version": 2, "state": "ready", "data": {}}
        for tool_name in EXPECTED_KIND_ENUMS:
            schema = tool_output_schema(tool_name)
            for payload in ({}, bogus):
                with self.subTest(tool=tool_name, payload=payload):
                    with self.assertRaises(jsonschema.ValidationError):
                        jsonschema.validate(payload, schema)
