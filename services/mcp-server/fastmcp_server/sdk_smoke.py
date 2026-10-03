"""Read-only SDK v2 compatibility smoke; receive a bearer credential on stdin.

The caller owns synthetic account/OAuth bootstrap. No credential is accepted
as a command-line argument or printed, and this module never changes data.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import ssl
import sys
from collections.abc import Callable

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
import httpx2


async def verify_sdk_client(client_factory: Callable[..., Client]) -> list[str]:
    """Exercise real SDK negotiation, tools and resources in both eras."""
    verified: list[str] = []
    for mode in ("legacy", "auto"):
        async with client_factory(mode=mode) as client:
            if mode == "legacy":
                assert client.initialize_result is not None, "Initialize handshake missing"
            else:
                assert client.protocol_version == "2026-07-28", "Modern negotiation fell back"
            tools = await client.list_tools()
            assert {"whoami", "agents", "tasks", "messages"} <= {tool.name for tool in tools}
            result = await client.call_tool("whoami", {"action": "get"})
            assert not result.is_error and result.structured_content, "Whoami failed"
            resources = await client.list_resources()
            assert any(str(resource.uri) == "ui://whoami/identity" for resource in resources)
            contents = await client.read_resource("ui://whoami/identity")
            assert contents and "ext-apps-2.0.3.js" in contents[0].text, "Widget resource unavailable"
            verified.append(client.protocol_version or mode)
    return verified


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:3000/mcp")
    parser.add_argument("--ca-file", help="Additional trusted CA PEM for a local HTTPS smoke; verification stays enabled")
    args = parser.parse_args()
    # Keep provider/transport exception bodies out of this proof output. Some
    # OAuth libraries include request details in verbose diagnostics.
    logging.disable(logging.CRITICAL)
    try:
        payload = json.loads(sys.stdin.read(64 * 1024))
        token = payload["access_token"]
        assert isinstance(token, str) and token, "Missing access token"
        tls_context = ssl.create_default_context(cafile=args.ca_file)

        def client_factory(**kwargs):
            def http_client_factory(**options):
                return httpx2.AsyncClient(verify=tls_context, **options)
            transport = StreamableHttpTransport(
                args.url, auth=token, httpx_client_factory=http_client_factory,
            )
            return Client(transport, timeout=20, **kwargs)

        versions = asyncio.run(verify_sdk_client(
            client_factory
        ))
    except Exception as error:
        print(f"SDK smoke failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
    print("PASS SDK2 initialize/discover, tools/list, whoami, resources/list/read: " + ", ".join(versions))


if __name__ == "__main__":
    main()
