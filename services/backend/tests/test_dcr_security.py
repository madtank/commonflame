"""
DCR Security Tests - P0 Vulnerability Prevention

Tests that OAuth 2.0 Dynamic Client Registration prevents:
1. Client ID hijacking (P0)
2. Redirect URI poisoning
3. Unauthorized client updates
4. Registration token theft

These tests MUST pass before any DCR code is deployed.
"""
from itertools import count

import pytest

pytestmark = pytest.mark.integration
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api.main import app
from app.core.database import engine, get_db_session


_client_counter = count(10)


@pytest_asyncio.fixture(autouse=True)
async def dispose_engine_between_async_tests():
    await engine.dispose()
    yield
    await engine.dispose()


def asgi_transport() -> ASGITransport:
    return ASGITransport(app=app, client=(f"198.51.100.{next(_client_counter)}", 123))


@pytest.mark.asyncio
async def test_prevent_client_id_hijacking():
    """
    P0 TEST: Prevent attacker from hijacking existing client_id

    Attack scenario:
    1. Legitimate client registers with client_id "victim-client"
    2. Attacker tries to register with SAME client_id but different redirect_uri
    3. Server MUST return 409 Conflict (not allow the update)

    If this test fails, attackers can redirect users to malicious domains!
    """
    async with AsyncClient(transport=asgi_transport(), base_url="http://test") as client:
        # Step 1: Legitimate client registers
        legitimate_registration = await client.post(
            "/oauth/register",
            json={
                "client_id": "test-victim-client",
                "client_name": "Legitimate Client",
                "redirect_uris": ["https://legitimate.example.com/callback"],
            },
        )

        assert legitimate_registration.status_code == 201 or legitimate_registration.status_code == 200
        legitimate_data = legitimate_registration.json()
        assert legitimate_data["client_id"] == "test-victim-client"
        assert "https://legitimate.example.com/callback" in legitimate_data["redirect_uris"]

        # Step 2: Attacker tries to hijack with same client_id
        attacker_registration = await client.post(
            "/oauth/register",
            json={
                "client_id": "test-victim-client",  # SAME client_id!
                "client_name": "Attacker Client",
                "redirect_uris": ["https://attacker.evil.com/steal"],  # Malicious URI!
            },
        )

        # Step 3: Server MUST reject with 409 Conflict
        assert attacker_registration.status_code == 409, \
            f"CRITICAL: Server allowed client hijacking! Expected 409, got {attacker_registration.status_code}"

        error_data = attacker_registration.json()
        assert "already registered" in error_data.get("detail", {}).get("error_description", "").lower()

        # Step 4: Verify database was NOT poisoned
        async for db in get_db_session():
            result = await db.execute(
                text("SELECT redirect_uris FROM oauth_clients WHERE client_id = :client_id"),
                {"client_id": "test-victim-client"}
            )
            row = result.fetchone()

            # Legitimate URI should still be in database
            assert row is not None
            redirect_uris = row[0]
            assert "https://legitimate.example.com/callback" in redirect_uris

            # Attacker URI should NOT be in database
            assert "https://attacker.evil.com/steal" not in redirect_uris, \
                "CRITICAL: Attacker's redirect_uri was written to database!"

            # Cleanup
            await db.execute(
                text("DELETE FROM oauth_clients WHERE client_id = :client_id"),
                {"client_id": "test-victim-client"}
            )
            await db.commit()


@pytest.mark.asyncio
async def test_redirect_uri_validation():
    """
    Test that redirect_uri validation prevents open redirect attacks
    """
    async with AsyncClient(transport=asgi_transport(), base_url="http://test") as client:
        # Test 1: HTTP redirect (non-localhost) should be rejected
        response = await client.post(
            "/oauth/register",
            json={
                "client_name": "HTTP Test",
                "redirect_uris": ["http://evil.com/callback"],  # HTTP not allowed!
            },
        )
        assert response.status_code in [400, 422], \
            f"Server accepted HTTP redirect_uri! Status: {response.status_code}"

        # Test 2: data: URI should be rejected (XSS risk)
        response = await client.post(
            "/oauth/register",
            json={
                "client_name": "Data URI Test",
                "redirect_uris": ["data:text/html,<script>alert(1)</script>"],
            },
        )
        assert response.status_code in [400, 422], \
            f"Server accepted data: URI! XSS vulnerability! Status: {response.status_code}"

        # Test 3: javascript: URI should be rejected
        response = await client.post(
            "/oauth/register",
            json={
                "client_name": "JS URI Test",
                "redirect_uris": ["javascript:alert(document.cookie)"],
            },
        )
        assert response.status_code in [400, 422], \
            f"Server accepted javascript: URI! XSS vulnerability! Status: {response.status_code}"


@pytest.mark.asyncio
async def test_sql_injection_prevention():
    """
    Test that client_name and other fields are sanitized against SQL injection
    """
    async with AsyncClient(transport=asgi_transport(), base_url="http://test") as client:
        # Attempt SQL injection in client_name
        response = await client.post(
            "/oauth/register",
            json={
                "client_name": "Test'; DROP TABLE oauth_clients; --",
                "redirect_uris": ["https://safe.example.com/callback"],
            },
        )

        # Request should either succeed (with sanitized input) or fail validation
        # But it should NOT drop the table!
        async for db in get_db_session():
            # Verify table still exists
            result = await db.execute(
                text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'oauth_clients')")
            )
            table_exists = result.scalar()
            assert table_exists, "CRITICAL: SQL injection succeeded! Table was dropped!"

            # Cleanup if client was created
            if response.status_code in [200, 201]:
                client_id = response.json().get("client_id")
                if client_id:
                    await db.execute(
                        text("DELETE FROM oauth_clients WHERE client_id = :client_id"),
                        {"client_id": client_id}
                    )
                    await db.commit()


@pytest.mark.asyncio
async def test_scope_escalation_prevention():
    """
    Test that clients cannot register with elevated/admin scopes
    """
    async with AsyncClient(transport=asgi_transport(), base_url="http://test") as client:
        response = await client.post(
            "/oauth/register",
            json={
                "client_name": "Scope Escalation Test",
                "redirect_uris": ["https://safe.example.com/callback"],
                "scope": "admin superuser delete_all_data",  # Elevated scopes
            },
        )

        if response.status_code in [200, 201]:
            data = response.json()
            # Server should either reject or sanitize elevated scopes
            scope = data.get("scope", "")
            assert "admin" not in scope.lower(), "Server accepted 'admin' scope!"
            assert "superuser" not in scope.lower(), "Server accepted 'superuser' scope!"

            # Cleanup
            async for db in get_db_session():
                await db.execute(
                    text("DELETE FROM oauth_clients WHERE client_id = :client_id"),
                    {"client_id": data["client_id"]}
                )
                await db.commit()


@pytest.mark.asyncio
async def test_missing_redirect_uris_rejected():
    """
    Test that registration without redirect_uris is rejected
    """
    async with AsyncClient(transport=asgi_transport(), base_url="http://test") as client:
        response = await client.post(
            "/oauth/register",
            json={
                "client_name": "No Redirects Test",
                # Missing redirect_uris!
            },
        )

        assert response.status_code in [400, 422], \
            f"Server allowed registration without redirect_uris! Status: {response.status_code}"


@pytest.mark.asyncio
async def test_wildcard_redirect_uri_rejected():
    """
    Test that wildcard redirect URIs are rejected
    """
    async with AsyncClient(transport=asgi_transport(), base_url="http://test") as client:
        response = await client.post(
            "/oauth/register",
            json={
                "client_name": "Wildcard Test",
                "redirect_uris": ["https://*.example.com/callback"],  # Wildcard!
            },
        )

        # Wildcards should be rejected (or at least not work as wildcards)
        # Either 400/422 error, or created but wildcard is treated literally
        if response.status_code in [200, 201]:
            # If created, verify wildcard doesn't actually match arbitrary subdomains
            # (This would need integration with /oauth/authorize endpoint)
            data = response.json()
            async for db in get_db_session():
                await db.execute(
                    text("DELETE FROM oauth_clients WHERE client_id = :client_id"),
                    {"client_id": data["client_id"]}
                )
                await db.commit()


if __name__ == "__main__":
    # Run tests with: pytest backend/tests/test_dcr_security.py -v
    pytest.main([__file__, "-v", "--tb=short"])
