#!/usr/bin/env python3
"""Verify autonomous account/team onboarding on an isolated loopback stack.

Uses normal account and OAuth APIs, never database edits or impersonation.
Creates synthetic fixtures; private credentials remain in the ignored state file.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError("Unexpected redirect; credentials were not forwarded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--state", type=Path, default=Path(".local/self-service/state.json"))
    parser.add_argument("--verify", action="store_true", help="Read existing fixtures after restart")
    parser.add_argument("--restricted", action="store_true", help="Check unknown signup is blocked")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    origin = urlparse(base)
    assert origin.scheme == "http" and origin.hostname in {"localhost", "127.0.0.1", "::1"}
    assert not origin.username and not origin.password and not origin.query and not origin.fragment
    assert origin.path == "", "Use a plain loopback origin"
    client = build_opener(ProxyHandler({}), NoRedirect())

    def request(url, body=None, token=None, form=False, expected=(200,)):
        url = base + url if url.startswith("/") else url
        assert url.startswith(base + "/"), "Discovered endpoints must stay on this test origin"
        headers = {"Accept": "application/json, text/event-stream", "Origin": base}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = None
        if body is not None:
            data = (urlencode(body) if form else json.dumps(body)).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        try:
            response = client.open(Request(url, data=data, headers=headers), timeout=30)
        except HTTPError as error:
            response = error
        assert response.status in expected, f"{urlparse(url).path}: HTTP {response.status}"
        raw = response.read().decode()
        if raw.startswith(("event:", "data:")):
            raw = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")][-1]
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        return value, response.headers

    # Only the origin/auth.md is supplied. MCP and OAuth URLs come from discovery.
    guide, _ = request("/auth.md")
    mcp = re.search(r"public MCP endpoint is \*\*(https?://[^*]+)\*\*", guide)[1]
    _, headers = request(mcp, {"jsonrpc":"2.0", "id":1, "method":"tools/call",
                              "params":{"name":"whoami", "arguments":{}}}, expected=(401,))
    metadata_url = re.search(r'resource_metadata="([^"]+)"', headers["WWW-Authenticate"])[1]
    resource, _ = request(metadata_url)
    auth_server = resource["authorization_servers"][0].rstrip("/")
    metadata, _ = request(auth_server + "/.well-known/oauth-authorization-server")
    status, _ = request("/auth/local/status")
    assert not status["setup_required"], "Bootstrap an owner first with normal /setup"
    print("PASS auth.md → protected challenge → resource metadata → OAuth discovery")

    if args.restricted:
        assert status["signup"] in {"invite_only", "closed"}
        request("/auth/local/signup", {"username":"unknown_agent_"+secrets.token_hex(5),
                                      "password":secrets.token_urlsafe(32)}, expected=(403,))
        print("PASS restricted policy blocks unknown unattended account signup")
        return

    def tool(actor, name, arguments, error=False):
        data, _ = request(mcp, {"jsonrpc":"2.0", "id":1, "method":"tools/call",
                               "params":{"name":name, "arguments":arguments}}, actor["access_token"])
        result = data["result"]
        structured = result.get("structuredContent", {})
        failed = bool(result.get("isError") or structured.get("error") or
                      structured.get("notice", {}).get("severity") == "error")
        assert failed == error, f"Unexpected {name} tool outcome"
        return structured

    def authorize(account, label):
        scope = "openid offline_access ax-api/mcp:read ax-api/mcp:write"
        assert set(scope.split()) <= set(metadata["scopes_supported"])
        registration, _ = request(metadata["registration_endpoint"], {
            "client_name":label, "redirect_uris":[], "response_types":[],
            "token_endpoint_auth_method":"none", "scope":scope,
            "grant_types":["urn:ietf:params:oauth:grant-type:device_code", "refresh_token"],
        }, expected=(201,))
        device, _ = request(metadata["device_authorization_endpoint"], {
            "client_id":registration["client_id"], "scope":scope, "resource":resource["resource"],
        }, form=True)
        polling = {"grant_type":"urn:ietf:params:oauth:grant-type:device_code",
                   "client_id":registration["client_id"], "device_code":device["device_code"],
                   "resource":resource["resource"]}
        pending, _ = request(metadata["token_endpoint"], polling, form=True, expected=(400,))
        assert pending["error"] == "authorization_pending"
        request("/oauth/device/approve", {"user_code":device["user_code"], "approved":"true"},
                account["access_token"], form=True)
        time.sleep(device["interval"])
        pair, _ = request(metadata["token_endpoint"], polling, form=True)
        identity = tool(pair, "whoami", {"action":"get"})
        pair["identity"] = identity
        return pair

    def save(state):
        assert not args.state.is_symlink()
        args.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = args.state.with_name(".state-" + secrets.token_hex(6))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(state, output)
        os.replace(temporary, args.state)

    if args.verify:
        assert not args.state.is_symlink() and args.state.stat().st_mode & 0o777 == 0o600
        state = json.loads(args.state.read_text())
        assert state["origin"] == base
    else:
        assert not args.state.exists(), "Use --verify or a new private state path"
        assert status["signup"] == "open"
        accounts = []
        for _ in range(2):
            credentials = {"username":"agent_owner_"+secrets.token_hex(5), "password":secrets.token_urlsafe(32)}
            account, _ = request("/auth/local/signup", credentials)
            account.update(credentials)
            accounts.append(account)
            time.sleep(5.1)
        owner, joiner = accounts
        team, _ = request("/api/spaces/create", {"name":"Autonomous self-service QA " + secrets.token_hex(3),
                          "visibility":"invite_only"}, owner["access_token"], expected=(200,201))
        switched, _ = request("/api/spaces/switch", {"space_id":team["id"]}, owner["access_token"])
        owner["access_token"] = switched["new_token"]
        own_workers = [authorize(owner, "Self-service planner"), authorize(owner, "Self-service reviewer")]
        private_worker = authorize(joiner, "Private-workspace worker")
        task, _ = request("/api/v1/tasks", {"title":"Controlled shared-team probe", "priority":"medium"},
                          owner["access_token"], expected=(200,201))
        task_id = task.get("task", task)["id"]
        request("/api/v1/tasks/"+task_id, token=joiner["access_token"], expected=(403,404))
        request("/api/v1/tasks/"+task_id, token=private_worker["access_token"], expected=(403,404))
        tool(private_worker, "tasks", {"action":"get", "task_id":task_id}, error=True)
        request("/api/spaces/switch", {"space_id":team["id"]}, joiner["access_token"], expected=(403,404))
        print("PASS autonomous accounts, own team, two distinct workers, foreign-workspace read/switch denial")
        invitation, _ = request("/api/spaces/"+team["id"]+"/invites", {"max_uses":1,"expires_hours":1},
                                owner["access_token"], expected=(200,201))
        joined, _ = request("/api/spaces/join", {"invite_code":invitation["invite_code"]}, joiner["access_token"])
        joiner["access_token"] = joined["new_token"]
        request("/api/spaces/"+team["id"]+"/invites", {}, joiner["access_token"], expected=(403,))
        shared_worker = authorize(joiner, "Invited-team worker")
        # Joining the owning account must not expand its old private worker grant.
        tool(private_worker, "tasks", {"action":"get", "task_id":task_id}, error=True)
        tool(shared_worker, "tasks", {"action":"get", "task_id":task_id})
        state = {"origin":base, "accounts":accounts, "team":team["id"], "task":task_id,
                 "workers":own_workers+[shared_worker], "private_worker":private_worker}
        save(state)
        print("PASS owner-approved membership, member cannot invite, new team grant without old-grant expansion")

    identities = []
    for worker in state["workers"]:
        identity = tool(worker, "whoami", {"action":"get"})
        assert identity == worker["identity"], "Signed identity must survive restart"
        identities.append(json.dumps(identity, sort_keys=True))
        tool(worker, "tasks", {"action":"get", "task_id":state["task"]})
    assert len(set(identities)) == 3
    tool(state["private_worker"], "tasks", {"action":"get", "task_id":state["task"]}, error=True)
    print("PASS saved team task, three independent identities, continued private-grant isolation")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Responses and exception strings may carry credentials. Never print them.
        print("Self-service check failed: " + type(error).__name__ + "; private diagnostics withheld")
        raise SystemExit(1) from None
