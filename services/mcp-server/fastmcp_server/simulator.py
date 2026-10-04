"""Scripted integration actors. Private NDJSON checkpoints are consumed by the runner."""

from __future__ import annotations

import asyncio
import base64
from collections import defaultdict
import json
import logging
import math
import secrets
import sys
import time

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

SCOPES = (
    "openid offline_access ax-api/mcp:read ax-api/mcp:write agents.read "
    "spaces.read tasks.read tasks.write messages.read messages.write context.read context.write"
)


class SimulationFailure(Exception):
    """Only a fixed local check label, never a provider response."""


def require(condition, check):
    if not condition:
        raise SimulationFailure(check)


def claims(token):
    # Inspection only; the API and real MCP resource validate these credentials.
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def output(event):
    print(json.dumps(event), flush=True)


def percentile(values, fraction):
    ordered = sorted(values)
    return round(
        ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))],
        2,
    )


def safe_report(state, cfg, timings, checks, started, failure=None, throttles=0):
    return {
        "run_id": state.get("run_id"),
        "scripted": True,
        "passed": failure is None,
        "failure": failure,
        "agents": len(state.get("agents", [])),
        "users": [u["username"] for u in state.get("users", [])],
        "workspaces": [
            {"id": s["id"], "name": s["name"]} for s in state.get("workspaces", [])
        ],
        "requested_agents": cfg["agents"],
        "concurrency": cfg["concurrency"],
        "rounds_requested": cfg["rounds"],
        "rounds_completed": checks.get("rounds_completed", 0),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "http_429_waits": throttles,
        "checks": dict(checks),
        "tools": {
            name: {
                "calls": len(values),
                "p50_ms": percentile(values, 0.5),
                "p95_ms": percentile(values, 0.95),
            }
            for name, values in timings.items()
            if values
        },
    }


class Simulator:
    def __init__(self, cfg, state=None):
        self.cfg = cfg
        self.state = state or {
            "version": 1,
            "run_id": secrets.token_hex(5),
            "topology": {"users": cfg["users"], "workspaces": cfg["workspaces"]},
            "users": [],
            "workspaces": [],
            "agents": [],
        }
        require(self.state.get("version") == 1, "supported_state_version")
        require(
            self.state["topology"]
            == {"users": cfg["users"], "workspaces": cfg["workspaces"]},
            "state_topology_matches",
        )
        require(cfg["agents"] >= len(self.state["agents"]), "population_cannot_shrink")
        self.base = "http://frontend:3000"
        self.http = httpx.AsyncClient(timeout=30, follow_redirects=False)
        self.tokens = {}
        self.humans = {}
        self.timings = defaultdict(list)
        self.checks = defaultdict(int)
        self.throttles = 0
        self.stage = "discovery"
        self.started = time.monotonic()
        self.semaphore = asyncio.Semaphore(cfg["concurrency"])

    def checkpoint(self):
        output({"state": self.state})

    def progress(self, stage):
        self.stage = stage
        output({"progress": stage})

    async def batch(self, coroutines):
        tasks = [asyncio.create_task(c) for c in coroutines]
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def api(
        self, path, body=None, token=None, form=False, expected=(200,), method=None
    ):
        headers = {"Origin": self.state.get("origin", self.base)}
        if token:
            headers["Authorization"] = "Bearer " + token
        for _ in range(30):
            response = await self.http.request(
                method or ("GET" if body is None else "POST"),
                self.base + path,
                headers=headers,
                **({"data": body} if form else {"json": body})
                if body is not None
                else {},
            )
            if response.status_code != 429:
                require(
                    response.status_code in expected,
                    "http_status_"
                    + path.split("?")[0]
                    + ":"
                    + str(response.status_code),
                )
                return response.json()
            # The limiter rejects before the operation. Transport failures and
            # 5xx responses are never replayed, especially one-use auth grants.
            wait = min(60, max(1, float(response.headers.get("Retry-After", "5"))))
            self.throttles += 1
            output(
                {"progress": "Rate limited; waiting " + str(round(wait)) + " seconds"}
            )
            await asyncio.sleep(wait)
        raise SimulationFailure("rate_limit_retry_budget")

    async def tool(self, actor, name, args, expect_error=False):
        route = "/mcp/agents/" + actor["name"] if actor["named"] else "/mcp"
        started = time.monotonic()
        async with Client(
            StreamableHttpTransport(
                self.base + route, auth=self.tokens[actor["index"]]
            ),
            timeout=30,
        ) as client:
            result = await client.call_tool(name, args)
        self.timings[name].append((time.monotonic() - started) * 1000)
        data = result.structured_content or {}
        failed = (
            result.is_error
            or bool(data.get("error"))
            or data.get("status") == "error"
            or (data.get("notice") or {}).get("severity") == "error"
        )
        require(
            failed if expect_error else not failed,
            name + "_expected_error" if expect_error else name + "_success",
        )
        self.checks["negative_mcp_read" if expect_error else "mcp_calls"] += 1
        return data

    async def humans_and_spaces(self):
        self.progress("Creating/reusing simulated users and joining team workspaces")
        metadata = await self.api("/.well-known/oauth-authorization-server")
        origin = metadata["issuer"].rstrip("/")
        require(self.state.get("origin", origin) == origin, "state_origin_matches")
        self.state["origin"] = origin
        self.checkpoint()
        status = await self.api("/auth/local/status")
        require(status["auth_mode"] == "builtin", "builtin_auth")
        for index in range(self.cfg["users"]):
            if index >= len(self.state["users"]):
                require(
                    status["setup_required"] or status["signup"] == "open",
                    "public_signup_available",
                )
                user = {
                    "username": "sim_" + self.state["run_id"] + "_" + str(index),
                    "password": secrets.token_urlsafe(32),
                }
                if status["setup_required"]:
                    require(
                        status["setup_flow"] == "browser",
                        "protected_owner_setup_requires_operator",
                    )
                    login = await self.api("/auth/local/setup", user)
                    status["setup_required"] = False
                else:
                    login = await self.api("/auth/local/signup", user)
                user["home_id"] = login["space_id"]
                self.state["users"].append(user)
                self.checkpoint()
                self.checks["signup"] += 1
            else:
                user = self.state["users"][index]
                login = await self.api(
                    "/auth/local/login",
                    {"username": user["username"], "password": user["password"]},
                )
                self.checks["login"] += 1
            self.humans[index] = login["access_token"]
        for index in range(self.cfg["workspaces"]):
            owner = index % self.cfg["users"]
            if index >= len(self.state["workspaces"]):
                space = await self.api(
                    "/api/spaces/create",
                    {
                        "name": "Simulator "
                        + self.state["run_id"]
                        + " team "
                        + str(index + 1),
                        "description": "Scripted MCP integration fixture; no autonomous model.",
                        "visibility": "invite_only",
                    },
                    self.humans[owner],
                )
                self.state["workspaces"].append(
                    {
                        "id": space["id"],
                        "name": space["name"],
                        "owner": owner,
                        "members": [owner],
                    }
                )
                self.checkpoint()
                self.checks["workspace_created"] += 1
            space = self.state["workspaces"][index]
            self.humans[owner] = (
                await self.api(
                    "/api/spaces/switch", {"space_id": space["id"]}, self.humans[owner]
                )
            )["new_token"]
            for member in range(self.cfg["users"]):
                if member in space["members"]:
                    continue
                invite = await self.api(
                    "/api/spaces/" + space["id"] + "/invites",
                    {"max_uses": 1},
                    self.humans[owner],
                    expected=(200, 201),
                )
                joined = await self.api(
                    "/api/spaces/join",
                    {"invite_code": invite["invite_code"]},
                    self.humans[member],
                )
                self.humans[member] = joined["new_token"]
                space["members"].append(member)
                self.checkpoint()
                self.checks["human_joined"] += 1

        if "private_probe_task" not in self.state:
            home = self.state["users"][0]["home_id"]
            self.humans[0] = (
                await self.api("/api/spaces/switch", {"space_id": home}, self.humans[0])
            )["new_token"]
            task = await self.api(
                "/api/v1/tasks",
                {"title": "Simulator private home isolation probe", "space_id": home},
                self.humans[0],
            )
            self.state["private_probe_task"] = task.get("task", task)["id"]
            self.checkpoint()

    async def onboard(self, index, sponsor_token, workspace):
        async with self.semaphore:
            label = "sim-" + self.state["run_id"] + "-" + str(index + 1).zfill(4)
            named = index % 2 == 0
            resource = (
                self.state["origin"] + "/mcp" + ("/agents/" + label if named else "")
            )
            client = await self.api(
                "/oauth/register",
                {
                    "client_name": label,
                    "token_endpoint_auth_method": "none",
                    "grant_types": [
                        "urn:ietf:params:oauth:grant-type:device_code",
                        "refresh_token",
                    ],
                    "scope": SCOPES,
                },
                expected=(201,),
            )
            device = await self.api(
                "/oauth/device/code",
                {
                    "client_id": client["client_id"],
                    "resource": resource,
                    "scope": SCOPES,
                },
                form=True,
            )
            params = {
                "client_id": client["client_id"],
                "resource": resource,
                "device_code": device["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
            pending = await self.api("/oauth/token", params, form=True, expected=(400,))
            require(
                pending.get("error") == "authorization_pending",
                "pending_before_sponsor_approval",
            )
            await self.api(
                "/oauth/device/approve",
                {"user_code": device["user_code"], "approved": "true"},
                sponsor_token,
                form=True,
            )
            await asyncio.sleep(device.get("interval", 5))
            pair = await self.api("/oauth/token", params, form=True)
            identity = claims(pair["access_token"])
            require(
                identity["space_id"] == workspace["id"], "approved_workspace_binding"
            )
            actor = {
                "index": index,
                "name": identity["agent_name"],
                "id": identity["agent_id"],
                "workspace": self.state["workspaces"].index(workspace),
                "sponsor": (index // self.cfg["workspaces"]) % self.cfg["users"],
                "named": named,
                "resource": resource,
                "client_id": client["client_id"],
                "refresh_token": pair["refresh_token"],
            }
            self.state["agents"].append(actor)
            self.state["agents"].sort(key=lambda a: a["index"])
            self.tokens[index] = pair["access_token"]
            self.checkpoint()
            self.checks["agent_approved"] += 1
            output(
                {
                    "progress": "Approved simulator agent "
                    + str(index + 1)
                    + " of "
                    + str(self.cfg["agents"])
                }
            )

    async def refresh(self, actor):
        previous = actor["refresh_token"]
        pair = await self.api(
            "/oauth/token",
            {
                "grant_type": "refresh_token",
                "client_id": actor["client_id"],
                "refresh_token": previous,
                "resource": actor["resource"],
            },
            form=True,
        )
        identity = claims(pair["access_token"])
        require(identity["agent_id"] == actor["id"], "refresh_preserves_agent_identity")
        require(pair["refresh_token"] != previous, "refresh_rotates_credential")
        actor["refresh_token"] = pair["refresh_token"]
        self.tokens[actor["index"]] = pair["access_token"]
        self.checkpoint()
        self.checks["refresh"] += 1

    async def actors(self):
        self.progress("Approving/reusing independent OAuth agent identities")
        for actor in self.state["agents"]:
            await self.refresh(actor)
        existing = {a["index"] for a in self.state["agents"]}
        for space_index, space in enumerate(self.state["workspaces"]):
            pending = [
                i
                for i in range(self.cfg["agents"])
                if i not in existing and i % self.cfg["workspaces"] == space_index
            ]
            sponsors = {}
            for index in {
                (i // self.cfg["workspaces"]) % self.cfg["users"] for i in pending
            }:
                self.humans[index] = (
                    await self.api(
                        "/api/spaces/switch",
                        {"space_id": space["id"]},
                        self.humans[index],
                    )
                )["new_token"]
                sponsors[index] = self.humans[index]
            await self.batch(
                self.onboard(
                    i,
                    sponsors[(i // self.cfg["workspaces"]) % self.cfg["users"]],
                    space,
                )
                for i in pending
            )
        require(
            len({a["id"] for a in self.state["agents"]}) == self.cfg["agents"],
            "independent_agent_ids",
        )
        self.checks["independent_identity_set"] += 1

    async def create_work(self, actor, round_id):
        async with self.semaphore:
            identity = await self.tool(actor, "whoami", {"action": "get"})
            require(
                actor["id"] in json.dumps(identity),
                "mcp_identity_matches_approved_agent",
            )
            await self.tool(actor, "agents", {"action": "list", "view_scope": "space"})
            await self.tool(actor, "spaces", {"action": "list"})
            title = "Simulator " + round_id + " actor " + str(actor["index"] + 1)
            created = await self.tool(
                actor,
                "tasks",
                {
                    "action": "create",
                    "title": title,
                    "description": "Scripted handoff; verify saved state.",
                },
            )
            task_id = created.get("data", {}).get("task", {}).get("id")
            require(task_id, "mcp_task_receipt")
            saved = await self.api(
                "/api/v1/tasks/" + task_id, token=self.tokens[actor["index"]]
            )
            task = saved.get("task", saved)
            require(task.get("title") == title, "mcp_task_persisted")
            return {"actor": actor, "task_id": task["id"], "title": title}

    async def handoff(self, work, reviewer):
        async with self.semaphore:
            creator = work["actor"]
            context_key = work["title"].replace(" ", "-")
            await self.tool(
                creator,
                "context",
                {
                    "action": "set",
                    "key": context_key,
                    "value": {"task_id": work["task_id"], "handoff": work["title"]},
                },
            )
            shared = await self.tool(
                reviewer, "context", {"action": "get", "key": context_key}
            )
            require(
                work["task_id"] in json.dumps(shared), "reviewer_reads_shared_context"
            )
            self.checks["shared_context"] += 1
            await self.tool(
                creator,
                "tasks",
                {
                    "action": "update",
                    "task_id": work["task_id"],
                    "assignee_type": "agent",
                    "assignee_id": reviewer["id"],
                },
            )
            text = "[SIMULATOR] " + work["title"] + " ready for @" + reviewer["name"]
            sent = await self.tool(
                creator, "messages", {"action": "send", "content": text, "bypass": True}
            )
            message_id = (sent.get("data", {}).get("sent") or {}).get("id")
            require(message_id, "handoff_message_receipt")
            message = await self.api(
                "/api/messages/" + message_id, token=self.tokens[creator["index"]]
            )
            require(
                message
                and message["author_id"] == creator["id"]
                and message["sender_type"] == "agent",
                "handoff_authorship_persisted",
            )
            await self.tool(
                reviewer,
                "messages",
                {
                    "action": "check",
                    "reason": "Simulator handoff",
                    "show_own_messages": True,
                    "curate": False,
                },
            )
            read = await self.tool(
                reviewer, "tasks", {"action": "get", "task_id": work["task_id"]}
            )
            require(work["title"] in json.dumps(read), "reviewer_reads_saved_task")
            await self.tool(
                reviewer,
                "tasks",
                {
                    "action": "update",
                    "task_id": work["task_id"],
                    "status": "in_progress",
                },
            )
            replied = await self.tool(
                reviewer,
                "messages",
                {
                    "action": "send",
                    "content": "[SIMULATOR] Reviewed " + work["title"],
                    "reply_to": message["id"],
                    "bypass": True,
                },
            )
            reply_id = (replied.get("data", {}).get("sent") or {}).get("id")
            require(reply_id, "reply_message_receipt")
            found = await self.tool(reviewer, "search", {"query": work["title"]})
            items = found.get("data", {}).get("items", [])
            require(
                work["title"] in json.dumps(items), "search_finds_saved_conversation"
            )
            self.checks["conversation_search"] += 1
            await self.tool(
                reviewer,
                "tasks",
                {"action": "update", "task_id": work["task_id"], "status": "completed"},
            )
            task = await self.api(
                "/api/v1/tasks/" + work["task_id"], token=self.tokens[reviewer["index"]]
            )
            task = task.get("task", task)
            require(task["status"] == "completed", "task_completion_persisted")
            reply = await self.api(
                "/api/messages/" + reply_id, token=self.tokens[reviewer["index"]]
            )
            require(
                reply and reply["author_id"] == reviewer["id"],
                "reply_authorship_persisted",
            )
            require(
                str(reply.get("parent_id") or reply.get("parent_message_id"))
                == str(message["id"]),
                "reply_thread_persisted",
            )
            self.checks["completed_handoffs"] += 1
            work["message_id"] = message["id"]
            work["reply_id"] = reply["id"]

    async def verify_humans_and_isolation(self, works):
        for space_index, space in enumerate(self.state["workspaces"]):
            owner = space["owner"]
            token = (
                await self.api(
                    "/api/spaces/switch", {"space_id": space["id"]}, self.humans[owner]
                )
            )["new_token"]
            self.humans[owner] = token
            for work in [w for w in works if w["actor"]["workspace"] == space_index]:
                task = await self.api("/api/v1/tasks/" + work["task_id"], token=token)
                require(
                    task.get("task", task)["title"] == work["title"],
                    "human_reads_mcp_task",
                )
                for field in ("message_id", "reply_id"):
                    saved = await self.api("/api/messages/" + work[field], token=token)
                    require(
                        saved["sender_type"] == "agent"
                        and saved["space_id"] == space["id"],
                        "human_reads_agent_conversation",
                    )
                self.checks["human_verified_handoffs"] += 1
                foreign = next(
                    (w for w in works if w["actor"]["workspace"] != space_index), None
                )
                targets = [self.state["private_probe_task"]]
                if foreign:
                    targets.append(foreign["task_id"])
                for task_id in targets:
                    await self.api(
                        "/api/v1/tasks/" + task_id,
                        token=self.tokens[work["actor"]["index"]],
                        expected=(403, 404),
                    )
                    await self.tool(
                        work["actor"],
                        "tasks",
                        {"action": "get", "task_id": task_id},
                        expect_error=True,
                    )
                    self.checks["workspace_isolation"] += 1

    async def run(self):
        failure = None
        try:
            async with asyncio.timeout(self.cfg["deadline"]):
                require(
                    not self.state.get("incomplete_run")
                    or self.cfg.get("resume_after_failure"),
                    "review_partial_run_before_resuming",
                )
                self.state["incomplete_run"] = True
                self.checkpoint()
                await self.humans_and_spaces()
                await self.actors()
                for number in range(self.cfg["rounds"]):
                    self.progress(
                        "Conversation round "
                        + str(number + 1)
                        + "/"
                        + str(self.cfg["rounds"])
                    )
                    if number:
                        for actor in self.state["agents"]:
                            await self.refresh(actor)
                    round_id = self.state["run_id"] + "-" + secrets.token_hex(3)
                    works = await self.batch(
                        self.create_work(a, round_id) for a in self.state["agents"]
                    )
                    reviewers = {}
                    for space_index in range(self.cfg["workspaces"]):
                        group = [
                            w["actor"]
                            for w in works
                            if w["actor"]["workspace"] == space_index
                        ]
                        for i, actor in enumerate(group):
                            reviewers[actor["index"]] = group[(i + 1) % len(group)]
                    await self.batch(
                        self.handoff(w, reviewers[w["actor"]["index"]]) for w in works
                    )
                    await self.verify_humans_and_isolation(works)
                    self.checks["rounds_completed"] += 1
                    if number + 1 < self.cfg["rounds"]:
                        await asyncio.sleep(self.cfg["pause"])
        except Exception as error:
            failure = {
                "stage": self.stage,
                "check": str(error)
                if isinstance(error, SimulationFailure)
                else type(error).__name__,
            }
        finally:
            if failure is None:
                self.state["incomplete_run"] = False
                self.checkpoint()
            await self.http.aclose()
        report = safe_report(
            self.state,
            self.cfg,
            self.timings,
            self.checks,
            self.started,
            failure,
            self.throttles,
        )
        output({"report": report})
        return 0 if failure is None else 1


def main():
    logging.disable(logging.CRITICAL)
    try:
        payload = json.loads(sys.stdin.readline())
        simulator = Simulator(payload["config"], payload.get("state"))
        code = asyncio.run(simulator.run())
    except Exception:
        # Malformed/private input must not be echoed.
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
