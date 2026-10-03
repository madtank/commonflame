"""Regression tests for agent inbox resource notifications."""


import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import mcp.types
from fastmcp import FastMCP
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.context import ServerRequestContext
from fastmcp.exceptions import McpError

from fastmcp_server.resources import inbox_notifications
from fastmcp_server.resources.inbox_notifications import _resolve_subscription
from fastmcp_server.resources.inbox_notifications import (
    _resolve_subscription_with_claims_for_test,
)
from fastmcp_server.resources.inbox_notifications import register_inbox_notifications
from fastmcp_server.server import create_server


class FakeSession:
    def __init__(self) -> None:
        self.updated_resources: list[str] = []

    async def send_resource_updated(self, uri) -> None:
        self.updated_resources.append(str(uri))


class FailingSession:
    async def send_resource_updated(self, uri) -> None:
        raise RuntimeError("session closed")


class InboxNotificationRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        inbox_notifications.inbox_subscriptions = (
            inbox_notifications.InboxSubscriptionRegistry()
        )

    def test_inbox_me_resource_and_subscription_handlers_are_registered(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("fastmcp_server.server.MCP_STATELESS_HTTP", False),
        ):
            server = create_server()

        components = getattr(server.local_provider, "_components", {})
        self.assertIn("resource:ax://inbox/me@", components)
        self.assertIsNotNone(server._mcp_server.get_request_handler('resources/subscribe'))
        self.assertIsNotNone(server._mcp_server.get_request_handler('resources/unsubscribe'))
        capabilities = server._mcp_server.get_capabilities(NotificationOptions(), {})
        self.assertIsNotNone(capabilities.resources)
        self.assertTrue(capabilities.resources.subscribe)

    def test_direct_registration_advertises_subscribe_capability(self) -> None:
        server = FastMCP("capability-regression")

        register_inbox_notifications(server)

        capabilities = server._mcp_server.get_capabilities(NotificationOptions(), {})
        self.assertIsNotNone(capabilities.resources)
        self.assertIs(capabilities.resources.subscribe, True)

    def test_inbox_notifications_are_not_advertised_in_stateless_mode(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("fastmcp_server.server.MCP_STATELESS_HTTP", True),
        ):
            server = create_server()

        self.assertIsNone(server._mcp_server.get_request_handler('resources/subscribe'))
        capabilities = server._mcp_server.get_capabilities(NotificationOptions(), {})
        if capabilities.resources is not None:
            self.assertIsNot(capabilities.resources.subscribe, True)

    def test_inbox_notification_registration_is_idempotent(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("fastmcp_server.server.MCP_STATELESS_HTTP", False),
        ):
            server = create_server()

        subscribe_handler = server._mcp_server.get_request_handler('resources/subscribe').handler
        unsubscribe_handler = server._mcp_server.get_request_handler('resources/unsubscribe').handler

        register_inbox_notifications(server)

        self.assertIs(
            server._mcp_server.get_request_handler('resources/subscribe').handler,
            subscribe_handler,
        )
        self.assertIs(
            server._mcp_server.get_request_handler('resources/unsubscribe').handler,
            unsubscribe_handler,
        )

    def test_named_inbox_subscription_is_limited_to_connected_agent(self) -> None:
        with self.assertRaises(McpError) as error:
            _resolve_subscription_with_claims_for_test(
                "ax://inbox/cipher",
                SimpleNamespace(headers={"x-agent-name": "orion"}),
                token_claims=None,
            )

        self.assertIn("limited to the connected agent", str(error.exception))

    def test_named_inbox_subscription_allows_connected_agent(self) -> None:
        agent_name, uri = _resolve_subscription_with_claims_for_test(
            "ax://inbox/cipher",
            SimpleNamespace(headers={"x-agent-name": "cipher"}),
            token_claims=None,
        )

        self.assertEqual(agent_name, "cipher")
        self.assertEqual(uri, "ax://inbox/cipher")

    def test_non_inbox_subscription_is_rejected_explicitly(self) -> None:
        with self.assertRaises(McpError) as error:
            _resolve_subscription_with_claims_for_test(
                "ax://mission-briefing",
                SimpleNamespace(headers={"x-agent-name": "cipher"}),
                token_claims=None,
            )

        self.assertIn("Only ax://inbox/me", str(error.exception))

    def test_inbox_subscription_rejects_token_claim_mismatch(self) -> None:
        with self.assertRaises(McpError) as error:
            _resolve_subscription_with_claims_for_test(
                "ax://inbox/me",
                SimpleNamespace(headers={"x-agent-name": "cipher"}),
                token_claims={"agent_name": "orion", "agent_id": "agent-orion"},
            )

        self.assertIn("does not match token claims", str(error.exception))

    def test_inbox_subscription_rejects_frontend_user_token(self) -> None:
        with (
            patch.dict(os.environ, {"COGNITO_FRONTEND_CLIENT_ID": "frontend-client"}),
            self.assertRaises(McpError) as error,
        ):
            _resolve_subscription_with_claims_for_test(
                "ax://inbox/me",
                SimpleNamespace(headers={"x-agent-name": "cipher"}),
                token_claims={"client_id": "frontend-client", "typ": "local-user"},
            )

        self.assertIn("Agent token is required", str(error.exception))

    def test_inbox_subscription_rejects_token_without_agent_claim(self) -> None:
        with self.assertRaises(McpError) as error:
            _resolve_subscription_with_claims_for_test(
                "ax://inbox/me",
                SimpleNamespace(headers={"x-agent-name": "cipher"}),
                token_claims={"client_id": "m2m-client"},
            )

        self.assertIn("Agent token claim is required", str(error.exception))

    def test_inbox_subscription_rejects_token_without_agent_id_claim(self) -> None:
        with self.assertRaises(McpError) as error:
            _resolve_subscription_with_claims_for_test(
                "ax://inbox/me",
                SimpleNamespace(headers={"x-agent-name": "cipher"}),
                token_claims={"agent_name": "cipher"},
            )

        self.assertIn("Agent token id claim is required", str(error.exception))

    def test_inbox_subscription_rejects_non_dict_token_claims(self) -> None:
        with (
            patch(
                "fastmcp_server.resources.inbox_notifications.get_access_token",
                return_value=SimpleNamespace(claims="not-a-dict"),
            ),
            self.assertRaises(McpError) as error,
        ):
            _resolve_subscription(
                "ax://inbox/me",
                SimpleNamespace(headers={"x-agent-name": "cipher"}),
            )

        self.assertIn("Agent token claims are required", str(error.exception))

    def test_inbox_subscription_rejects_missing_token_by_default(self) -> None:
        with (
            patch(
                "fastmcp_server.resources.inbox_notifications.get_access_token",
                return_value=None,
            ),
            self.assertRaises(McpError) as error,
        ):
            _resolve_subscription(
                "ax://inbox/me",
                SimpleNamespace(headers={"x-agent-name": "cipher"}),
            )

        self.assertIn("Agent token is required", str(error.exception))

    def test_inbox_subscription_explicit_no_auth_harness_path(self) -> None:
        with patch(
            "fastmcp_server.resources.inbox_notifications.get_access_token",
            return_value=None,
        ):
            resolved = _resolve_subscription(
                "ax://inbox/me",
                SimpleNamespace(headers={"x-agent-name": "cipher"}),
                allow_no_auth=True,
            )

        self.assertEqual(resolved.agent_name, "cipher")
        self.assertIsNone(resolved.agent_id)
        self.assertEqual(resolved.uri, "ax://inbox/me")

    def test_mentioned_agents_trims_sentence_punctuation(self) -> None:
        self.assertEqual(
            inbox_notifications.mentioned_agents("Heads up @orion. Loop in @delta-7,"),
            {"orion", "delta-7"},
        )

    def test_inbox_subscription_fails_closed_when_token_context_breaks(self) -> None:
        async def run() -> None:
            with (
                patch.dict(os.environ, {}, clear=False),
                patch("fastmcp_server.server.MCP_STATELESS_HTTP", False),
            ):
                server = create_server()

            session = FakeSession()
            token = ServerRequestContext(request_id='subscribe-inbox-broken-token-context', meta=None, session=session, lifespan_context={}, request=SimpleNamespace(headers={'x-agent-name': 'test_agent'}), protocol_version='2025-11-25', method='resources/subscribe')
            try:
                subscribe = server._mcp_server.get_request_handler('resources/subscribe').handler
                with patch('fastmcp_server.resources.inbox_notifications.get_access_token', side_effect=RuntimeError('broken context')), self.assertRaises(McpError) as error:
                    await subscribe(token, mcp.types.SubscribeRequest(params=mcp.types.SubscribeRequestParams(uri='ax://inbox/me')).params)
            finally:
                None

            self.assertIn("token context", str(error.exception))

        asyncio.run(run())

    def test_stale_subscription_cleanup_preserves_live_subscribers(self) -> None:
        async def run() -> None:
            registry = inbox_notifications.InboxSubscriptionRegistry()
            dead_session = FailingSession()
            live_session = FakeSession()
            registry.subscribe("cipher", "ax://inbox/me", dead_session)
            registry.subscribe("cipher", "ax://inbox/me", live_session)

            delivered = await registry.notify("cipher")
            delivered_again = await registry.notify("cipher")

            self.assertEqual(delivered, 1)
            self.assertEqual(delivered_again, 1)
            self.assertEqual(
                live_session.updated_resources,
                ["ax://inbox/me", "ax://inbox/me"],
            )

        asyncio.run(run())

    def test_notify_mentioned_inboxes_with_no_mentions_returns_zero(self) -> None:
        async def run() -> None:
            delivered = await inbox_notifications.notify_mentioned_inboxes(
                "No addressed agents here."
            )

            self.assertEqual(delivered, 0)

        asyncio.run(run())

    def test_notify_mentioned_inboxes_notifies_each_mentioned_agent(self) -> None:
        async def run() -> None:
            orion_session = FakeSession()
            cipher_session = FakeSession()
            inbox_notifications.inbox_subscriptions.subscribe(
                "orion", "ax://inbox/me", orion_session
            )
            inbox_notifications.inbox_subscriptions.subscribe(
                "cipher", "ax://inbox/me", cipher_session
            )

            delivered = await inbox_notifications.notify_mentioned_inboxes(
                "Looping in @orion and @cipher."
            )

            self.assertEqual(delivered, 2)
            self.assertEqual(orion_session.updated_resources, ["ax://inbox/me"])
            self.assertEqual(cipher_session.updated_resources, ["ax://inbox/me"])

        asyncio.run(run())

    def test_agent_id_keyed_subscriptions_do_not_receive_name_only_notifications(self) -> None:
        async def run() -> None:
            session = FakeSession()
            inbox_notifications.inbox_subscriptions.subscribe(
                "orion",
                "ax://inbox/me",
                session,
                agent_id="agent-owned-by-user-a",
            )

            delivered = await inbox_notifications.notify_mentioned_inboxes(
                "Looping in @orion."
            )

            self.assertEqual(delivered, 0)
            self.assertEqual(session.updated_resources, [])

        asyncio.run(run())

    def test_empty_agent_id_subscription_does_not_fall_back_to_name_key(self) -> None:
        async def run() -> None:
            session = FakeSession()
            inbox_notifications.inbox_subscriptions.subscribe(
                "orion",
                "ax://inbox/me",
                session,
                agent_id="",
            )

            delivered = await inbox_notifications.notify_mentioned_inboxes(
                "Looping in @orion."
            )

            self.assertEqual(delivered, 0)
            self.assertEqual(session.updated_resources, [])

        asyncio.run(run())

    def test_notification_targets_from_realistic_receipt_prefers_agent_ids(self) -> None:
        targets = inbox_notifications.notification_targets_from_receipt(
            {
                "id": "msg-1",
                "routing": {
                    "intelligence": {
                        "effective_mentions": [
                            {
                                "agent_name": "Orion",
                                "agent_id": "agent-owned-by-user-a",
                            },
                            {
                                "agent_name": "Orion",
                                "agent_id": "agent-owned-by-user-a",
                            },
                        ]
                    }
                },
            }
        )

        self.assertEqual(targets, [("orion", "agent-owned-by-user-a")])

    def test_backend_resolved_agent_id_notification_reaches_matching_subscription(self) -> None:
        async def run() -> None:
            session = FakeSession()
            inbox_notifications.inbox_subscriptions.subscribe(
                "orion",
                "ax://inbox/me",
                session,
                agent_id="agent-owned-by-user-a",
            )

            delivered = await inbox_notifications.notify_message_inboxes(
                {
                    "id": "msg-1",
                    "effective_mentions": [
                        {
                            "agent_name": "orion",
                            "agent_id": "agent-owned-by-user-a",
                        }
                    ],
                },
                fallback_content="@orion",
            )

            self.assertEqual(delivered, 1)
            self.assertEqual(session.updated_resources, ["ax://inbox/me"])

        asyncio.run(run())

    def test_notify_message_inboxes_fallback_only_reaches_no_auth_name_subscription(self) -> None:
        async def run() -> None:
            harness_session = FakeSession()
            production_session = FakeSession()
            inbox_notifications.inbox_subscriptions.subscribe(
                "orion",
                "ax://inbox/me",
                harness_session,
            )
            inbox_notifications.inbox_subscriptions.subscribe(
                "orion",
                "ax://inbox/me",
                production_session,
                agent_id="agent-owned-by-user-a",
            )

            delivered = await inbox_notifications.notify_message_inboxes(
                {
                    "id": "msg-legacy",
                    "routing": {"mentions": ["orion"]},
                },
                fallback_content="Legacy receipt mentions @orion.",
            )

            self.assertEqual(delivered, 1)
            self.assertEqual(harness_session.updated_resources, ["ax://inbox/me"])
            self.assertEqual(production_session.updated_resources, [])

        asyncio.run(run())

    def test_backend_resolved_agent_id_notification_ignores_same_name_other_owner(self) -> None:
        async def run() -> None:
            session = FakeSession()
            inbox_notifications.inbox_subscriptions.subscribe(
                "orion",
                "ax://inbox/me",
                session,
                agent_id="agent-owned-by-user-b",
            )

            delivered = await inbox_notifications.notify_message_inboxes(
                {
                    "id": "msg-1",
                    "effective_mentions": [
                        {
                            "agent_name": "orion",
                            "agent_id": "agent-owned-by-user-a",
                        }
                    ],
                },
                fallback_content="@orion",
            )

            self.assertEqual(delivered, 0)
            self.assertEqual(session.updated_resources, [])

        asyncio.run(run())

    def test_inbox_me_unsubscribe_removes_resource_updated_subscription(self) -> None:
        async def run() -> None:
            with (
                patch.dict(os.environ, {}, clear=False),
                patch("fastmcp_server.server.MCP_STATELESS_HTTP", False),
            ):
                server = create_server()

            session = FakeSession()
            token = ServerRequestContext(request_id='unsubscribe-inbox', meta=None, session=session, lifespan_context={}, request=SimpleNamespace(headers={'x-agent-name': 'test_agent'}), protocol_version='2025-11-25', method='resources/subscribe')
            try:
                subscribe = server._mcp_server.get_request_handler('resources/subscribe').handler
                unsubscribe = server._mcp_server.get_request_handler('resources/unsubscribe').handler
                with patch(
                    "fastmcp_server.resources.inbox_notifications._current_token_claims",
                    return_value={
                        "agent_name": "test_agent",
                        "agent_id": "agent-test",
                    },
                ):
                    request = mcp.types.SubscribeRequest(
                        params=mcp.types.SubscribeRequestParams(uri="ax://inbox/me")
                    )
                    await subscribe(token, request.params)
                    await unsubscribe(token, mcp.types.UnsubscribeRequest(params=mcp.types.UnsubscribeRequestParams(uri='ax://inbox/me')).params)
            finally:
                None

            delivered = await inbox_notifications.notify_inbox_updated(
                "test_agent",
                agent_id="agent-test",
            )

            self.assertEqual(delivered, 0)
            self.assertEqual(session.updated_resources, [])

        asyncio.run(run())

    def test_inbox_me_subscription_receives_resource_updated_notification(self) -> None:
        async def run() -> None:
            with (
                patch.dict(os.environ, {}, clear=False),
                patch("fastmcp_server.server.MCP_STATELESS_HTTP", False),
            ):
                server = create_server()

            session = FakeSession()
            token = ServerRequestContext(request_id='subscribe-inbox', meta=None, session=session, lifespan_context={}, request=SimpleNamespace(headers={'x-agent-name': 'test_agent'}), protocol_version='2025-11-25', method='resources/subscribe')
            try:
                subscribe = server._mcp_server.get_request_handler('resources/subscribe').handler
                with patch(
                    "fastmcp_server.resources.inbox_notifications._current_token_claims",
                    return_value={
                        "agent_name": "test_agent",
                        "agent_id": "agent-test",
                    },
                ):
                    await subscribe(token, mcp.types.SubscribeRequest(params=mcp.types.SubscribeRequestParams(uri='ax://inbox/me')).params)
            finally:
                None

            delivered = await inbox_notifications.notify_inbox_updated(
                "test_agent",
                agent_id="agent-test",
            )

            self.assertEqual(delivered, 1)
            self.assertEqual(session.updated_resources, ["ax://inbox/me"])

        asyncio.run(run())

    def test_inbox_me_subscription_requires_agent_identity(self) -> None:
        async def run() -> None:
            with (
                patch.dict(os.environ, {}, clear=False),
                patch("fastmcp_server.server.MCP_STATELESS_HTTP", False),
            ):
                server = create_server()

            session = FakeSession()
            token = ServerRequestContext(request_id='subscribe-inbox-missing-agent', meta=None, session=session, lifespan_context={}, request=SimpleNamespace(headers={}), protocol_version='2025-11-25', method='resources/subscribe')
            try:
                subscribe = server._mcp_server.get_request_handler('resources/subscribe').handler
                with self.assertRaises(McpError) as error:
                    await subscribe(token, mcp.types.SubscribeRequest(params=mcp.types.SubscribeRequestParams(uri='ax://inbox/me')).params)
            finally:
                None

            self.assertIn("Agent identity is required", str(error.exception))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
