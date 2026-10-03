#!/usr/bin/env python3
"""Exercise an isolated running stack without printing any credentials.

Creates one synthetic local account/workspace per run. No existing account or
private data is read. Requires Docker Compose and Python's standard library.
"""
import argparse
import base64
import hashlib
import http.cookiejar
import json
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPSHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--health-only", action="store_true")
    parser.add_argument("--ca-file", help="Trust this test CA for HTTPS; certificate verification stays enabled")
    parser.add_argument("--sdk-python", help="Optional MCP SDK v2 Python runtime for testing the public URL directly")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    public_mcp = f"{base}/mcp"
    cookies = http.cookiejar.CookieJar()
    tls = ssl.create_default_context(cafile=args.ca_file)
    client = build_opener(HTTPCookieProcessor(cookies), HTTPSHandler(context=tls))

    def request(path, payload=None, *, token=None, form=False, expected=200, method=None, opener=client):
        headers = {"Accept": "application/json, text/event-stream", "Origin": base}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = None
        if payload is not None:
            body = urlencode(payload).encode() if form else json.dumps(payload).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        req = Request(base + path, data=body, headers=headers, method=method)
        try:
            response = opener.open(req, timeout=25)
        except HTTPError as error:
            response = error
        data = response.read().decode()
        if response.status != expected:
            # Response bodies may carry credentials; report status and route only.
            raise AssertionError(f"{path}: expected {expected}, received {response.status}")
        if data.startswith("event:") or data.startswith("data:"):
            frames = [line[5:].strip() for line in data.splitlines() if line.startswith("data:")]
            value = json.loads(frames[-1]) if frames else {}
        else:
            try:
                value = json.loads(data)
            except json.JSONDecodeError:
                value = data
        return value, response.headers

    request("/health")
    metadata, _ = request("/.well-known/oauth-authorization-server")
    assert metadata["device_authorization_endpoint"] == base + "/oauth/device/code"
    assert metadata["token_endpoint"] == base + "/oauth/token"
    jwks, _ = request("/.well-known/jwks.json")
    assert jwks.get("keys"), "JWKS must contain persisted public signing keys"
    # The catalog contains tool schemas only and is intentionally public.
    catalog, _ = request("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    public_names = {tool["name"] for tool in catalog.get("result", {}).get("tools", [])}
    assert {"whoami", "agents", "tasks", "messages", "spaces", "context", "search"} <= public_names
    # Every advertised tool must challenge before any backend data can be read.
    for name in public_names:
        _, challenge = request("/mcp", {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": {}},
        }, expected=401)
        assert "resource_metadata" in challenge.get("WWW-Authenticate", ""), "MCP must advertise auth discovery"
    request("/mcp", {"jsonrpc": "2.0", "id": 3, "method": "resources/read",
                     "params": {"uri": "ax://mission-briefing"}}, expected=401)
    auth_guide, _ = request("/auth.md")
    assert "{{ORIGIN}}" not in auth_guide and base + "/mcp" in auth_guide
    bridge, _ = request("/mcp/assets/ext-apps-2.0.3.js")
    assert len(bridge) > 100000, "MCP Apps bridge must be available locally"
    print("PASS health, native OAuth discovery, JWKS, auth.md, unauthenticated MCP challenge")
    metadata, _ = request("/.well-known/oauth-authorization-server")
    assert "client_credentials" not in metadata["grant_types_supported"]
    unsupported, _ = request("/oauth/token", {"grant_type": "client_credentials"}, form=True, expected=400)
    assert unsupported.get("error") == "unsupported_grant_type"
    for path in ("/auth/exchange", "/api/v1/keys",
                 "/api/v1/agents/00000000-0000-0000-0000-000000000000/keys"):
        request(path, {}, expected=404)
    # The unproxied historical /credentials prefix hits the static UI and rejects
    # POST with 405. The API route inventory independently asserts it is unmounted.
    request("/credentials/agent-pat", {}, expected=405)
    request("/auth/me", token="axp_u_retired.fixture", expected=401)
    request("/api/v1/tasks", token="axp_u_retired.fixture", expected=401)
    print("PASS retired PAT, client-secret, and alternate API authentication entry points")
    if args.health_only:
        return

    username = "smoke_" + secrets.token_hex(5)
    password = secrets.token_urlsafe(32)
    create_script = """import asyncio,json,sys
from scripts.create_local_user import create_user
data=json.load(sys.stdin)
asyncio.run(create_user(data['username'],data['password']))
print('Synthetic account created')
"""
    status, _ = request("/auth/local/status")
    assert status["auth_mode"] == "builtin" and status["signup"] in {"open", "invite_only", "closed"}
    if status["setup_required"]:
        # Operator capability is captured directly into memory, never a URL,
        # shell argument, source file, or test log.
        owner_body = {"username": username, "password": password}
        if status["setup_flow"] == "token":
            setup_file = "/run/keys/smoke-owner-" + secrets.token_hex(6) + ".token"
            subprocess.run(["docker", "compose", "exec", "-T", "backend", "python", "-m",
                            "scripts.create_setup_token", "--output", setup_file],
                           text=True, check=True, capture_output=True)
            read_setup = "from pathlib import Path; import sys; p=Path(sys.argv[1]); assert p.stat().st_mode & 0o777 == 0o600; print(p.read_text().strip())"
            owner_body["token"] = subprocess.run(["docker", "compose", "exec", "-T", "backend", "python", "-c",
                                          read_setup, setup_file], text=True, check=True, capture_output=True).stdout.strip()
        request("/auth/local/setup", {**owner_body, "token": secrets.token_urlsafe(32)}, expected=400)
        login, headers = request("/auth/local/setup", owner_body)
        time.sleep(5.1)  # Respect the setup endpoint's two-attempt burst window.
        request("/auth/local/setup", owner_body, expected=409)
        status, _ = request("/auth/local/status")
        assert not status["setup_required"], "Owner setup must stay closed"
        print("PASS first-owner setup, invalid capability rejection, permanent setup closure")
    else:
        subprocess.run(["docker", "compose", "exec", "-T", "backend", "python", "-c", create_script],
                       input=json.dumps({"username": username, "password": password}), text=True,
                       check=True, capture_output=True)
        login, headers = request("/auth/local/login", {"username": username, "password": password})
    access = login["access_token"]
    space_id = login["space_id"]
    assert "HttpOnly" in headers.get("Set-Cookie", ""), "Refresh cookie must be HttpOnly"
    if base.startswith("https://"):
        assert "Secure" in headers.get("Set-Cookie", ""), "Hosted refresh cookie must be Secure"
    request("/auth/me", token=access)
    request("/api/v1/spaces", token=access)
    request("/auth/local/login", {"username": username, "password": "incorrect-password"}, expected=401)
    refreshed, _ = request("/auth/local/refresh", {})
    access = refreshed["access_token"]
    request("/auth/me", token=access)
    print("PASS built-in account, login, wrong-password rejection, authenticated API, refresh")

    # Open signup gives a new human their own workspace, never the owner's data.
    signup_cookies = http.cookiejar.CookieJar()
    signup_client = build_opener(HTTPCookieProcessor(signup_cookies), HTTPSHandler(context=tls))
    signup_body = {"username": "another_" + secrets.token_hex(5), "password": secrets.token_urlsafe(32)}
    if status["signup"] == "open":
        another, _ = request("/auth/local/signup", signup_body, opener=signup_client)
        assert another["space_id"] != space_id and another["user"]["role"] == "user"
        spaces, _ = request("/api/v1/spaces", token=another["access_token"], opener=signup_client)
        space_rows = spaces if isinstance(spaces, list) else spaces.get("spaces", [])
        assert space_id not in {str(space.get("id")) for space in space_rows}
        request("/auth/local/logout", {}, opener=signup_client)
        print("PASS token-free signup, separate private workspace, no instance admin privilege")
        time.sleep(5.1)
    else:
        request("/auth/local/signup", signup_body, opener=signup_client, expected=403)
        print("PASS hosted registration policy enforced without an invitation")
        time.sleep(5.1)

    if status["signup"] != "closed":
        invite_cookies = http.cookiejar.CookieJar()
        invite_client = build_opener(HTTPCookieProcessor(invite_cookies), HTTPSHandler(context=tls))
        invite_body = {"username": "invited_" + secrets.token_hex(5), "password": secrets.token_urlsafe(32)}
        request("/auth/local/invites", {}, expected=401)
        request("/auth/local/signup", {**invite_body, "token": secrets.token_urlsafe(32)},
                expected=400, opener=invite_client)
        invitation, _ = request("/auth/local/invites", {}, token=access, expected=201)
        invited, _ = request("/auth/local/signup", {**invite_body, "token": invitation["token"]}, opener=invite_client)
        assert invited["space_id"] == space_id, "Invite must join only its sponsoring workspace"
        time.sleep(5.1)  # Respect the signup endpoint's two-attempt burst window.
        request("/auth/local/signup", {**invite_body, "token": invitation["token"]}, expected=400, opener=invite_client)
        request("/auth/local/invites", {}, token=invited["access_token"], expected=403, opener=invite_client)
        request("/auth/local/logout", {}, opener=invite_client)
        request("/auth/local/refresh", {}, expected=401, opener=invite_client)
        print("PASS one-time workspace invitation, invalid/replayed invite rejection, member cannot invite")
    else:
        request("/auth/local/invites", {}, token=access, expected=403)
        permission, _ = request("/auth/local/invites", token=access)
        assert not permission["can_invite"]
        print("PASS closed registration disables invitation creation")

    scopes = "openid offline_access ax-api/mcp:read ax-api/mcp:write agents.read spaces.read tasks.read tasks.write messages.read messages.write"
    registration, _ = request("/oauth/register", {
        "client_name": "Waystation smoke client", "token_endpoint_auth_method": "none",
        "grant_types": ["urn:ietf:params:oauth:grant-type:device_code", "refresh_token"],
        "scope": scopes,
    }, expected=201)
    client_id = registration["client_id"]
    agent_resource = public_mcp
    device, _ = request("/oauth/device/code", {
        "client_id": client_id, "scope": scopes, "resource": agent_resource,
    }, form=True)
    token_params = {"client_id": client_id, "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device["device_code"], "resource": agent_resource}
    pending, _ = request("/oauth/token", token_params, form=True, expected=400)
    assert pending.get("error") == "authorization_pending"
    request("/oauth/device/approve", {"user_code": device["user_code"], "approved": "true"}, token=access, form=True)
    time.sleep(device.get("interval", 5))
    pair, _ = request("/oauth/token", token_params, form=True)
    agent_access = pair["access_token"]
    print("PASS OAuth client registration, device code, pending consent, approval, token issuance")

    def token_claims(value):
        # Inspection only: the authenticated MCP calls below verify signatures
        # and audiences through the owning resource server.
        encoded = value.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))

    original_claims = token_claims(agent_access)
    assert original_claims.get("agent_name"), "Canonical OAuth resource must carry its signed agent name"
    assert original_claims.get("space_id") == original_claims["authorized_space_id"] == space_id
    assert original_claims["iss"] == metadata["issuer"] == base, "JWT issuer must match OAuth discovery"
    assert original_claims["aud"] == public_mcp
    rotated, _ = request("/oauth/token", {
        "grant_type": "refresh_token", "client_id": client_id,
        "refresh_token": pair["refresh_token"], "resource": agent_resource,
    }, form=True)
    assert rotated["refresh_token"] != pair["refresh_token"], "OAuth refresh must rotate"
    assert token_claims(rotated["access_token"])["agent_id"] == original_claims["agent_id"]
    replay, _ = request("/oauth/token", {
        "grant_type": "refresh_token", "client_id": client_id,
        "refresh_token": pair["refresh_token"], "resource": agent_resource,
    }, form=True, expected=400)
    assert replay.get("error") == "invalid_grant", "Rotated refresh credentials must not replay"
    agent_access = rotated["access_token"]
    print("PASS OAuth refresh rotation, agent identity preservation, refresh replay rejection")

    callback_uri = "http://127.0.0.1:9999/waystation-smoke-callback"
    pkce_client, _ = request("/oauth/register", {
        "client_name": "Waystation PKCE smoke client", "redirect_uris": [callback_uri],
        "token_endpoint_auth_method": "none", "response_types": ["code"],
        "grant_types": ["authorization_code", "refresh_token"], "scope": scopes,
    }, expected=201)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    pkce_params = {
        "response_type": "code", "client_id": pkce_client["client_id"],
        "redirect_uri": callback_uri, "scope": scopes, "state": secrets.token_urlsafe(24),
        "resource": agent_resource, "code_challenge": challenge, "code_challenge_method": "S256",
    }
    consent_page, _ = request("/oauth/authorize?" + urlencode(pkce_params))
    assert "Approve this agent connection" in consent_page, "Unauthenticated OAuth entry must show human consent"
    request("/oauth/authorize", pkce_params, expected=401)
    request("/oauth/authorize", pkce_params, token=access, expected=400)
    request("/oauth/authorize", {**pkce_params, "approved": True, "redirect_uri": "https://invalid.example/callback"},
            token=access, expected=400)
    denied, _ = request("/oauth/authorize", {**pkce_params, "approved": False}, token=access)
    denied_callback = parse_qs(urlparse(denied["redirect_uri"]).query)
    assert denied_callback.get("error") == ["access_denied"] and "code" not in denied_callback
    assert denied_callback.get("state") == [pkce_params["state"]]
    time.sleep(5.1)  # Keep adversarial checks within the normal consent burst limit.
    approval, _ = request("/oauth/authorize", {**pkce_params, "approved": True}, token=access)
    callback = parse_qs(urlparse(approval["redirect_uri"]).query)
    assert callback.get("state") == [pkce_params["state"]], "OAuth state must survive approval"
    code_params = {
        "grant_type": "authorization_code", "client_id": pkce_client["client_id"],
        "code": callback["code"][0], "redirect_uri": callback_uri,
        "code_verifier": verifier, "resource": agent_resource,
    }
    wrong_pkce, _ = request("/oauth/token", {**code_params, "code_verifier": secrets.token_urlsafe(48)},
                            form=True, expected=400)
    assert wrong_pkce.get("error") == "invalid_grant", "Incorrect PKCE verifier must fail"
    pkce_pair, _ = request("/oauth/token", code_params, form=True)
    pkce_claims = token_claims(pkce_pair["access_token"])
    assert pkce_claims["agent_id"] != original_claims["agent_id"], "Different clients must have distinct sponsored identities"
    assert pkce_claims["authorized_space_id"] == original_claims["authorized_space_id"] == space_id
    assert pkce_claims["aud"] == public_mcp, "Named routes must use the canonical protected-resource audience"
    consumed, _ = request("/oauth/token", code_params, form=True, expected=400)
    assert consumed.get("error") == "invalid_grant", "Authorization codes must be single use"
    print("PASS explicit native PKCE approval/denial, redirect/state binding, verifier rejection, code replay rejection")

    # Real SDK negotiation, separate from the low-level wire checks below.
    # CI uses the built MCP image; local HTTPS QA can use its isolated SDK venv.
    if args.sdk_python:
        sdk_command = [args.sdk_python, "-m", "fastmcp_server.sdk_smoke", "--url", public_mcp]
        if args.ca_file:
            sdk_command += ["--ca-file", str(Path(args.ca_file).resolve())]
        sdk_cwd = "services/mcp-server"
    else:
        sdk_command = ["docker", "compose", "exec", "-T", "mcp", "python", "-m",
                       "fastmcp_server.sdk_smoke", "--url", "http://frontend:3000/mcp"]
        sdk_cwd = None
    for credential in (agent_access, pkce_pair["access_token"]):
        subprocess.run(sdk_command, input=json.dumps({"access_token": credential}),
                       text=True, check=True, capture_output=True, cwd=sdk_cwd)
    print("PASS real MCP SDK2 legacy and current protocol, authenticated tools/resources for both sponsored clients")

    sequence = 0

    def rpc(method, params=None):
        nonlocal sequence
        sequence += 1
        payload = {"jsonrpc": "2.0", "id": sequence, "method": method}
        if params is not None:
            payload["params"] = params
        result, _ = request("/mcp", payload, token=agent_access)
        assert "error" not in result, f"MCP {method} returned a protocol error"
        output = result.get("result", {})
        assert not output.get("isError"), f"MCP {method} returned a tool error"
        return output

    rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "waystation-smoke", "version": "0.1.0"}})
    tools = rpc("tools/list")
    names = {tool["name"] for tool in tools.get("tools", [])}
    assert {"whoami", "agents", "tasks", "messages", "spaces"} <= names
    rpc("tools/call", {"name": "whoami", "arguments": {}})
    rpc("tools/call", {"name": "agents", "arguments": {"action": "list", "space_id": space_id}})
    print("PASS stateless MCP initialize, tool discovery, whoami, authenticated agents tool")

    # Do not accept an MCP wrapper's success flag as evidence of delivery. Some
    # legacy widgets wrap backend errors as ordinary structured tool results.
    roster, _ = request("/api/v1/agents?view_scope=all", token=access)
    assert original_claims["agent_id"] in {str(row["id"]) for row in roster["agents"]}
    widget_roster, _ = request("/mcp", {
        "jsonrpc": "2.0", "id": 900, "method": "tools/call",
        "params": {"name": "agents", "arguments": {"action": "list", "view_scope": "space"}},
    }, token=access)
    widget_items = widget_roster["result"]["structuredContent"]["data"]["items"]
    assert original_claims["agent_id"] in {str(row["id"]) for row in widget_items}
    agent_title = "Agent MCP task " + secrets.token_hex(6)
    rpc("tools/call", {"name": "tasks", "arguments": {
        "action": "create", "title": agent_title, "description": "Sponsored MCP task roundtrip",
    }})
    saved_tasks, _ = request("/api/v1/tasks", token=access)
    task_rows = saved_tasks if isinstance(saved_tasks, list) else saved_tasks.get("tasks", [])
    agent_task = next(row for row in task_rows if row["title"] == agent_title)
    rpc("tools/call", {"name": "tasks", "arguments": {"action": "get", "task_id": agent_task["id"]}})
    agent_content = "Sponsored MCP message roundtrip " + secrets.token_hex(6)
    rpc("tools/call", {"name": "messages", "arguments": {
        "action": "send", "content": agent_content, "bypass": True,
    }})
    saved_messages, _ = request("/api/messages", token=access)
    agent_message = next(row for row in saved_messages["messages"] if row["content"] == agent_content)
    assert agent_message["sender_type"] == "agent"
    assert agent_message["author_id"] == original_claims["agent_id"]
    assert str(agent_message["space_id"]) == space_id
    rpc("tools/call", {"name": "messages", "arguments": {
        "action": "check", "reason": "Verify sponsored message persistence", "show_own_messages": True,
    }})
    print("PASS sponsored agent in roster, MCP task create/read, durable agent-authored message visible to human")

    task_title = "Waystation smoke task"
    task, _ = request("/api/v1/tasks", {"title": task_title, "description": "Synthetic integration check",
                                      "space_id": space_id}, token=access)
    task_id = task.get("id") or task.get("task", {}).get("id")
    assert task_id, "Task creation must return a durable ID"
    saved_task, _ = request(f"/api/v1/tasks/{task_id}", token=access)
    saved_task = saved_task.get("task", saved_task)
    assert saved_task.get("id") == task_id, "Readback must return the created task"
    assert saved_task.get("title") == task_title, "Task title must persist"
    assert str(saved_task.get("space_id")) == space_id, "Task must remain in the authenticated workspace"
    print("PASS task create/read persistence")

    content = "Waystation synthetic message roundtrip " + secrets.token_hex(6)
    created_message, _ = request("/api/messages", {"content": content, "channel": "main"}, token=access)
    message_id = created_message.get("id")
    assert message_id, "Message creation must return a durable ID"
    saved_message, _ = request(f"/api/messages/{message_id}", token=access)
    assert saved_message["content"] == content, "Message content must persist"
    assert str(saved_message["space_id"]) == space_id, "Message must remain in the authenticated workspace"
    assert saved_message["sender_type"] == "user", "Browser messages must preserve human authorship"
    print("PASS message create/read persistence and human authorship")

    # The stream credential stays in a header and memory; no ?token URLs enter
    # proxy/access logs. Read the initial frame and close the stream immediately.
    stream_request = Request(base + "/api/sse/messages", headers={
        "Accept": "text/event-stream", "Authorization": "Bearer " + access, "Origin": base,
    })
    with client.open(stream_request, timeout=20) as stream:
        assert stream.status == 200
        event_type, event_data = None, None
        for _ in range(12):
            line = stream.readline().decode().strip()
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                event_data = json.loads(line[5:].strip())
            elif not line and event_data is not None:
                break
        assert event_type == "connected", "SSE must confirm its authenticated connection"
        assert event_data["space_id"] == space_id, "SSE must subscribe to the authenticated workspace"
        assert event_data["space_ids"] == [space_id], "Private local session must not hydrate another workspace"
    print("PASS bearer-authenticated SSE connection and workspace scope")
    request("/auth/local/logout", {})
    request("/auth/local/refresh", {}, expected=401)
    print("PASS logout and refresh revocation")
    print("Full-stack smoke check passed. Synthetic account/workspace remains for auditability.")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, subprocess.CalledProcessError, OSError, KeyError) as error:
        # Do not echo subprocess output, request headers, or token responses.
        print(f"Smoke check failed: {type(error).__name__}: {str(error) if not isinstance(error, subprocess.CalledProcessError) else 'Docker setup or SDK command failed'}", file=sys.stderr)
        sys.exit(1)
