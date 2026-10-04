"""Exercise the real SDK v2 HTTP transport, auth gate and local Apps bundle."""
from contextlib import AsyncExitStack
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth import AccessToken
import httpx2
import pytest

from fastmcp_server.api_client import api_request, extract_agent_context
from fastmcp_server.mcp_ui import D3_ASSET_PATH, MCP_APPS_BRIDGE_PATH, MCP_PUBLIC_ORIGIN, resource_app_config
from fastmcp_server.sdk_smoke import verify_sdk_client
from fastmcp_server.server import create_app, server


@pytest.mark.asyncio
async def test_sdk2_http_legacy_and_modern_tools_resources():
    app = create_app()
    access = AccessToken(token="synthetic-test-token", client_id="sdk-test", scopes=[], claims={
        "agent_id": "agent-sdk-test", "agent_name": "sdk_test", "space_id": "space-test",
    })
    identity = {"id": "agent-sdk-test", "name": "sdk_test", "workspace": {"id": "space-test"}}
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app.app.router.lifespan_context(app.app))
        stack.enter_context(patch.object(server.auth, "verify_token", new=AsyncMock(return_value=access)))
        stack.enter_context(patch("fastmcp_server.tools.whoami.api_request", new=AsyncMock(return_value=identity)))
        stack.enter_context(patch("fastmcp_server.tools.whoami.resolve_space_scoped_permissions", new=AsyncMock(return_value={})))
        stack.enter_context(patch("fastmcp_server.tool_call_notifier.api_request", new=AsyncMock(return_value={})))

        def client_factory(**kwargs):
            def http_client_factory(**options):
                return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **options)
            transport = StreamableHttpTransport(
                "http://testserver/mcp", auth=access.token, httpx_client_factory=http_client_factory,
            )
            return Client(transport, timeout=10, **kwargs)

        versions = await verify_sdk_client(client_factory)
        assert versions[-1] == "2026-07-28"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["tools/call", "tasks/get", "tasks/update", "tasks/cancel", "subscriptions/listen"])
async def test_modern_data_methods_challenge_without_token(method):
    app = create_app()
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/mcp", headers={
            "Mcp-Protocol-Version": "2026-07-28", "Mcp-Method": method,
        }, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {}})
    assert response.status_code == 401
    assert "resource_metadata=" in response.headers["www-authenticate"]


@pytest.mark.asyncio
async def test_local_apps_bundle_and_widget_csp():
    app = create_app()
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as client:
        bundle = await client.get(MCP_APPS_BRIDGE_PATH)
        widget = await client.get("/apps/tasks")
    assert bundle.status_code == 200
    assert hashlib.sha256(bundle.content).hexdigest() == "fb56376b7583ecafb4820bdebc150abee18feb6258ff84b83c2c944ebd9c3602"
    assert "immutable" in bundle.headers["cache-control"]
    assert MCP_PUBLIC_ORIGIN + MCP_APPS_BRIDGE_PATH in widget.text
    assert "__COMMONFLAME_MCP_APPS_BRIDGE_URL__" not in widget.text
    assert "unpkg.com" not in widget.text
    assert MCP_PUBLIC_ORIGIN in resource_app_config().csp.resource_domains


@pytest.mark.asyncio
async def test_graph_uses_integrity_verified_local_d3():
    app = create_app()
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as client:
        bundle = await client.get(D3_ASSET_PATH)
        graph = await client.get("/apps/context/graph")
    assert bundle.status_code == 200
    assert hashlib.sha256(bundle.content).hexdigest() == "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539"
    assert "immutable" in bundle.headers["cache-control"]
    assert graph.status_code == 200
    assert MCP_PUBLIC_ORIGIN + D3_ASSET_PATH in graph.text
    assert "const d3 = globalThis.d3" in graph.text
    assert "__COMMONFLAME_D3_URL__" not in graph.text
    assert "unpkg.com" not in graph.text


@pytest.mark.asyncio
async def test_backend_scope_denial_is_preserved_without_credential_substitution():
    import httpx
    requests = []

    def deny_write(request):
        requests.append(request)
        return httpx.Response(403, json={"detail": "insufficient_scope: tasks.write required"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(deny_write))
    with patch("fastmcp_server.api_client.httpx.AsyncClient", return_value=client):
        result = await api_request("POST", "/api/v1/tasks", "synthetic-read-only-token", json_data={"title": "denied"})
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer synthetic-read-only-token"
    assert result["error"] == "API error 403"
    assert "insufficient_scope" in result["detail"]


@pytest.mark.parametrize("claims", [
    {"sub": "user", "client_id": "client", "scope": "ax-api/mcp:read"},
    {"sub": "user", "agent_name": "fake_agent"},
    {"sub": "user", "typ": "local-user", "agent_id": "fake_agent", "agent_name": "fake_agent"},
])
def test_mutable_route_cannot_promote_user_or_partial_identity(claims):
    context = extract_agent_context(
        SimpleNamespace(token="synthetic", claims=claims),
        SimpleNamespace(headers={"x-agent-name": "fake_agent"}),
    )
    assert context["principal_type"] == "user"
    assert context["agent_name"] is None
    assert context["agent_id"] is None
