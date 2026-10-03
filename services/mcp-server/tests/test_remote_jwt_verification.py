"""Verify signed JWTs against JWKS using the actual FastMCP verifier."""

import asyncio
import json
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
import httpx2 as httpx
from fastmcp.server.auth.providers.jwt import RSAKeyPair
from jwt.algorithms import RSAAlgorithm
import pytest

from fastmcp_server.ax_remote_auth import AxRemoteAuthProvider


@pytest.mark.parametrize("case", ["valid", "wrong_issuer", "wrong_audience", "named_audience", "expired", "wrong_signature"])
def test_remote_resource_token_validation(case):
    keypair = RSAKeyPair.generate()
    public_key = serialization.load_pem_public_key(keypair.public_key.encode())
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk.update({"kid": "ax-backend-test", "alg": "RS256", "use": "sig"})

    provider = AxRemoteAuthProvider(
        auth_server_url="http://localhost:3000",
        auth_server_internal_url="http://backend:8080",
        backend_jwks_uri="http://backend:8080/.well-known/jwks.json",
        backend_issuer="http://localhost:3000",
        mcp_server_url="http://localhost:3000",
    )
    signing_key = RSAKeyPair.generate() if case == "wrong_signature" else keypair
    audience = {
        "wrong_audience": "http://other.local/mcp",
        "named_audience": "http://localhost:3000/mcp/agents/sdk_test",
    }.get(case, "http://localhost:3000/mcp")
    token = signing_key.create_token(
        issuer="untrusted" if case == "wrong_issuer" else "http://localhost:3000",
        audience=audience,
        expires_in_seconds=-60 if case == "expired" else 60,
        scopes=["openid", "messages.read"],
        kid="ax-backend-test",
    )
    requests = []

    def jwks_response(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={"keys": [jwk]})

    async def verify():
        async with httpx.AsyncClient(transport=httpx.MockTransport(jwks_response)) as client:
            # Exercise real signature/expiry/issuer/audience validation; replace only network I/O.
            with patch.object(provider.token_verifier, "_http_client", client):
                return await provider.verify_token(token)

    access = asyncio.run(verify())
    assert requests == ["http://backend:8080/.well-known/jwks.json"]
    if case == "valid":
        assert access is not None
        assert access.claims["iss"] == "http://localhost:3000"
        assert access.claims["aud"] == "http://localhost:3000/mcp"
    else:
        assert access is None
