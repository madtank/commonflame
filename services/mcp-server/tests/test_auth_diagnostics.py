"""Tests for early auth validation and diagnostics.

These checks keep ChatGPT Apps discovery compatible while failing fast when a
request already presents a stale or invalid bearer token.
"""


from types import SimpleNamespace
from unittest.mock import AsyncMock

import json
import os
import unittest


async def _collect_asgi_response(app, scope):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages


async def _collect_asgi_response_with_body(app, scope, body: bytes):
    messages = []
    receive_calls = 0

    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages


class AuthDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_bearer_token_is_discovery_compatible(self):
        from fastmcp_server.auth_checks import build_auth_diagnostics

        auth = AsyncMock()
        diagnostics = await build_auth_diagnostics(auth, [])

        self.assertEqual(diagnostics["status"], "unauthenticated")
        self.assertFalse(diagnostics["token_present"])
        self.assertTrue(diagnostics["discovery_compatible"])
        auth.verify_token.assert_not_called()

    async def test_invalid_bearer_token_returns_rejected_diagnostics(self):
        from fastmcp_server.auth_checks import build_auth_diagnostics

        auth = AsyncMock()
        auth.verify_token.return_value = None

        diagnostics = await build_auth_diagnostics(
            auth,
            [(b"authorization", b"Bearer stale-token")],
        )

        self.assertEqual(diagnostics["status"], "invalid")
        self.assertTrue(diagnostics["token_present"])
        self.assertFalse(diagnostics["token_valid"])
        self.assertFalse(diagnostics["discovery_compatible"])
        auth.verify_token.assert_awaited_once_with("stale-token")

    async def test_verifier_exception_is_treated_as_invalid(self):
        from fastmcp_server.auth_checks import build_auth_diagnostics

        auth = AsyncMock()
        auth.verify_token.side_effect = RuntimeError("boom")

        diagnostics = await build_auth_diagnostics(
            auth,
            [(b"authorization", b"Bearer broken-token")],
        )

        self.assertEqual(diagnostics["status"], "invalid")
        self.assertFalse(diagnostics["token_valid"])
        self.assertFalse(diagnostics["discovery_compatible"])

    async def test_valid_token_reports_principal_summary(self):
        from fastmcp_server.auth_checks import build_auth_diagnostics

        auth = AsyncMock()
        auth.verify_token.return_value = SimpleNamespace(
            claims={
                "sub": "user-123",
                "agent_id": "agent-456",
                "agent_name": "agentx",
                "space_id": "space-789",
                "client_id": "frontend-client",
                "scope": "openid profile",
                "tools_allowed": ["whoami"],
                "typ": "local-user",
            }
        )

        diagnostics = await build_auth_diagnostics(
            auth,
            [(b"authorization", b"Bearer good-token")],
        )

        self.assertEqual(diagnostics["status"], "ok")
        self.assertEqual(diagnostics["principal_hint"], "space_agent")
        self.assertEqual(diagnostics["subject"], "user-123")
        self.assertEqual(diagnostics["agent_name"], "agentx")
        self.assertEqual(diagnostics["space_id"], "space-789")
        self.assertEqual(diagnostics["scopes"], ["openid", "profile"])
        self.assertTrue(diagnostics["tools_allowed"])

    async def test_m2m_token_reports_m2m_principal_hint(self):
        from fastmcp_server.auth_checks import build_auth_diagnostics

        auth = AsyncMock()
        auth.verify_token.return_value = SimpleNamespace(
            claims={
                "sub": "m2m-client",
                "token_use": "access",
                "client_id": "mcp-m2m-client",
                "scope": "ax-api/mcp:read ax-api/mcp:write",
            }
        )

        diagnostics = await build_auth_diagnostics(
            auth,
            [(b"authorization", b"Bearer good-token")],
        )

        self.assertEqual(diagnostics["status"], "ok")
        self.assertEqual(diagnostics["principal_hint"], "m2m_client")

    async def test_openid_scope_keeps_user_session_principal_hint(self):
        from fastmcp_server.auth_checks import build_auth_diagnostics

        auth = AsyncMock()
        auth.verify_token.return_value = SimpleNamespace(
            claims={
                "sub": "user-123",
                "token_use": "access",
                "username": "madtank",
                "client_id": "frontend-client",
                "scope": "openid ax-api/mcp:read",
                "typ": "local-user",
            }
        )

        diagnostics = await build_auth_diagnostics(
            auth,
            [(b"authorization", b"Bearer user-token")],
        )

        self.assertEqual(diagnostics["status"], "ok")
        self.assertEqual(diagnostics["principal_hint"], "user_session")

    async def test_missing_scope_defaults_to_user_session_principal_hint(self):
        from fastmcp_server.auth_checks import build_auth_diagnostics

        auth = AsyncMock()
        auth.verify_token.return_value = SimpleNamespace(
            claims={
                "sub": "user-123",
                "client_id": "frontend-client",
                "typ": "local-user",
            }
        )

        diagnostics = await build_auth_diagnostics(
            auth,
            [(b"authorization", b"Bearer user-token")],
        )

        self.assertEqual(diagnostics["status"], "ok")
        self.assertEqual(diagnostics["principal_hint"], "user_session")

    async def test_empty_scope_string_defaults_to_user_session_principal_hint(self):
        from fastmcp_server.auth_checks import build_auth_diagnostics

        auth = AsyncMock()
        auth.verify_token.return_value = SimpleNamespace(
            claims={
                "sub": "user-123",
                "client_id": "frontend-client",
                "scope": "",
                "typ": "local-user",
            }
        )

        diagnostics = await build_auth_diagnostics(
            auth,
            [(b"authorization", b"Bearer user-token")],
        )

        self.assertEqual(diagnostics["status"], "ok")
        self.assertEqual(diagnostics["principal_hint"], "user_session")

    async def test_empty_scope_list_defaults_to_user_session_principal_hint(self):
        from fastmcp_server.auth_checks import build_auth_diagnostics

        auth = AsyncMock()
        auth.verify_token.return_value = SimpleNamespace(
            claims={
                "sub": "user-123",
                "client_id": "frontend-client",
                "scope": [],
                "typ": "local-user",
            }
        )

        diagnostics = await build_auth_diagnostics(
            auth,
            [(b"authorization", b"Bearer user-token")],
        )

        self.assertEqual(diagnostics["status"], "ok")
        self.assertEqual(diagnostics["principal_hint"], "user_session")

    async def test_early_validation_middleware_blocks_invalid_token_before_mcp(self):
        from fastmcp_server.auth_checks import EarlyBearerAuthValidationMiddleware

        auth = AsyncMock()
        auth.verify_token.return_value = None
        downstream_called = False

        async def downstream(scope, receive, send):
            nonlocal downstream_called
            downstream_called = True

        middleware = EarlyBearerAuthValidationMiddleware(downstream, auth)
        with unittest.mock.patch.dict(os.environ, {"MCP_SERVER_URL": "http://localhost:3000"}):
            messages = await _collect_asgi_response(
                middleware,
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/mcp",
                    "headers": [
                        (b"authorization", b"Bearer stale-token"),
                        (b"x-agent-name", b"spark tester"),
                    ],
                },
            )

        self.assertFalse(downstream_called)
        start = next(msg for msg in messages if msg["type"] == "http.response.start")
        self.assertEqual(start["status"], 401)
        headers = dict(start["headers"])
        challenge = headers[b"www-authenticate"].decode("utf-8")
        self.assertIn('error="invalid_token"', challenge)
        self.assertIn('error_description="The access token expired or is invalid"', challenge)
        self.assertIn(
            'resource_metadata="http://localhost:3000/.well-known/oauth-protected-resource/mcp/agents/spark%20tester"',
            challenge,
        )
        self.assertIn('scope="openid offline_access ax-api/mcp:read ax-api/mcp:write"', challenge)
        auth.verify_token.assert_awaited_once_with("stale-token")

    async def test_early_validation_middleware_allows_unauthenticated_discovery(self):
        from fastmcp_server.auth_checks import EarlyBearerAuthValidationMiddleware

        auth = AsyncMock()
        downstream_called = False

        async def downstream(scope, receive, send):
            nonlocal downstream_called
            downstream_called = True
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [],
                }
            )
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        middleware = EarlyBearerAuthValidationMiddleware(downstream, auth)
        messages = await _collect_asgi_response(
            middleware,
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [],
            },
        )

        self.assertTrue(downstream_called)
        start = next(msg for msg in messages if msg["type"] == "http.response.start")
        self.assertEqual(start["status"], 204)
        auth.verify_token.assert_not_called()

    async def test_initialize_without_token_returns_oauth_challenge(self):
        from fastmcp_server.auth_checks import MCPProtectedMethodAuthChallengeMiddleware

        downstream_called = False

        async def downstream(scope, receive, send):
            nonlocal downstream_called
            downstream_called = True

        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "0"},
                },
            }
        ).encode("utf-8")
        middleware = MCPProtectedMethodAuthChallengeMiddleware(downstream)

        with unittest.mock.patch.dict(os.environ, {"MCP_SERVER_URL": "http://localhost:3000"}):
            messages = await _collect_asgi_response_with_body(
                middleware,
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/mcp",
                    "headers": [(b"x-agent-name", b"spark tester")],
                },
                body,
            )

        # `initialize` must NOT reach downstream unauthenticated — anonymous
        # initialize opens stateful sessions that scanners use to wedge the
        # server (specs/MCP-INIT-AUTH-001/spec.md).
        self.assertFalse(downstream_called)
        start = next(msg for msg in messages if msg["type"] == "http.response.start")
        self.assertEqual(start["status"], 401)
        headers = dict(start["headers"])
        challenge = headers[b"www-authenticate"].decode("utf-8")
        self.assertIn('Bearer realm="mcp"', challenge)
        self.assertIn(
            'resource_metadata="http://localhost:3000/.well-known/oauth-protected-resource/mcp/agents/spark%20tester"',
            challenge,
        )
        self.assertIn(
            'resource="http://localhost:3000/mcp/agents/spark%20tester"',
            challenge,
        )
        self.assertIn('scope="openid offline_access ax-api/mcp:read ax-api/mcp:write"', challenge)

    async def test_tools_call_without_token_returns_oauth_challenge(self):
        from fastmcp_server.auth_checks import MCPProtectedMethodAuthChallengeMiddleware

        async def downstream(scope, receive, send):
            raise AssertionError("downstream should not be called")

        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"whoami","arguments":{}}}'
        middleware = MCPProtectedMethodAuthChallengeMiddleware(downstream)

        with unittest.mock.patch.dict(os.environ, {"MCP_SERVER_URL": "http://localhost:3000"}):
            messages = await _collect_asgi_response_with_body(
                middleware,
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/mcp",
                    "headers": [(b"x-agent-name", b"spark tester")],
                },
                body,
            )

        start = next(msg for msg in messages if msg["type"] == "http.response.start")
        self.assertEqual(start["status"], 401)
        headers = dict(start["headers"])
        challenge = headers[b"www-authenticate"].decode("utf-8")
        self.assertIn('scope="openid offline_access ax-api/mcp:read ax-api/mcp:write"', challenge)

    async def test_remote_auth_challenge_requests_ax_scopes(self):
        from fastmcp_server.auth_checks import MCPProtectedMethodAuthChallengeMiddleware

        async def downstream(scope, receive, send):
            raise AssertionError("downstream should not be called")

        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"whoami","arguments":{}}}'
        middleware = MCPProtectedMethodAuthChallengeMiddleware(downstream)

        with unittest.mock.patch.dict(
            os.environ,
            {
                "AX_AUTH_MODE": "remote",
                "MCP_SERVER_URL": "http://localhost:3000",
            },
        ):
            messages = await _collect_asgi_response_with_body(
                middleware,
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/mcp",
                    "headers": [(b"x-agent-name", b"spark tester")],
                },
                body,
            )

        start = next(msg for msg in messages if msg["type"] == "http.response.start")
        self.assertEqual(start["status"], 401)
        headers = dict(start["headers"])
        challenge = headers[b"www-authenticate"].decode("utf-8")
        self.assertIn('scope="openid offline_access ax-api/mcp:read ax-api/mcp:write"', challenge)

    async def test_anonymous_protected_methods_still_return_oauth_challenge(self):
        from fastmcp_server.auth_checks import MCPProtectedMethodAuthChallengeMiddleware

        async def downstream(scope, receive, send):
            raise AssertionError("downstream should not be called")

        for method, params in (
            ("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}}),
            ("tools/call", {"name": "whoami", "arguments": {}}),
            ("resources/read", {"uri": "ax://inbox"}),
            ("prompts/get", {"name": "startup"}),
        ):
            with self.subTest(method=method):
                body = json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                ).encode("utf-8")
                middleware = MCPProtectedMethodAuthChallengeMiddleware(downstream)
                with unittest.mock.patch.dict(os.environ, {"MCP_SERVER_URL": "http://localhost:3000"}):
                    messages = await _collect_asgi_response_with_body(
                        middleware,
                        {
                            "type": "http",
                            "method": "POST",
                            "path": "/mcp",
                            "headers": [(b"x-agent-name", b"spark tester")],
                        },
                        body,
                    )

                start = next(msg for msg in messages if msg["type"] == "http.response.start")
                self.assertEqual(start["status"], 401)
                headers = dict(start["headers"])
                challenge = headers[b"www-authenticate"].decode("utf-8")
                self.assertIn('Bearer realm="mcp"', challenge)

    async def test_unprotected_method_replays_body_to_downstream(self):
        from fastmcp_server.auth_checks import MCPProtectedMethodAuthChallengeMiddleware

        downstream_body = None

        async def downstream(scope, receive, send):
            nonlocal downstream_body
            message = await receive()
            downstream_body = message.get("body", b"")
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [],
                }
            )
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
        middleware = MCPProtectedMethodAuthChallengeMiddleware(downstream)
        messages = await _collect_asgi_response_with_body(
            middleware,
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [],
            },
            body,
        )

        self.assertEqual(downstream_body, body)
        start = next(msg for msg in messages if msg["type"] == "http.response.start")
        self.assertEqual(start["status"], 204)


if __name__ == "__main__":
    unittest.main()
