"""Simulator safety and failure semantics; real collaboration is covered by Compose."""

import asyncio
from collections import defaultdict
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from fastmcp_server.simulator import Simulator, SimulationFailure, safe_report


def config():
    return {
        "agents": 2,
        "users": 2,
        "workspaces": 1,
        "concurrency": 2,
        "rounds": 1,
        "pause": 0,
        "deadline": 30,
    }


@pytest.mark.asyncio
async def test_origin_mismatch_stops_before_credentials_are_sent():
    sim = Simulator(config())
    sim.state["origin"] = "https://original.example"
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"issuer": "https://different.example"})

    await sim.http.aclose()
    sim.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with pytest.raises(SimulationFailure, match="state_origin_matches"):
        await sim.humans_and_spaces()
    assert len(requests) == 1 and "Authorization" not in requests[0].headers
    await sim.http.aclose()


@pytest.mark.asyncio
async def test_explicit_rate_limit_wait_is_retryable_but_5xx_is_not(monkeypatch):
    sim = Simulator(config())
    calls = []
    responses = [
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200, json={"ok": True}),
        httpx.Response(502, text="credential-that-must-not-be-logged"),
    ]

    def respond(request):
        calls.append(request)
        return responses.pop(0)

    await sim.http.aclose()
    sim.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    waits = AsyncMock()
    monkeypatch.setattr("fastmcp_server.simulator.asyncio.sleep", waits)
    assert await sim.api("/oauth/token", {"code": "one-use"}, form=True) == {"ok": True}
    waits.assert_awaited_once_with(2)
    with pytest.raises(SimulationFailure, match="http_status_/oauth/token:502"):
        await sim.api("/oauth/token", {"code": "another-one-use"}, form=True)
    assert len(calls) == 3 and sim.throttles == 1
    await sim.http.aclose()


@pytest.mark.asyncio
async def test_tool_wrapped_error_is_not_a_success(monkeypatch):
    sim = Simulator(config())
    sim.tokens[0] = "private-access-token"
    actor = {"index": 0, "named": False}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def call_tool(self, *args):
            return SimpleNamespace(
                is_error=False,
                structured_content={"status": "error", "data": {"error": "denied"}},
            )

    monkeypatch.setattr("fastmcp_server.simulator.Client", FakeClient)
    with pytest.raises(SimulationFailure, match="tasks_success"):
        await sim.tool(actor, "tasks", {"action": "get", "task_id": "not-authorized"})
    await sim.tool(
        actor,
        "tasks",
        {"action": "get", "task_id": "not-authorized"},
        expect_error=True,
    )
    assert sim.checks["negative_mcp_read"] == 1
    await sim.http.aclose()


def test_report_allowlist_excludes_private_fixture_state():
    state = {
        "run_id": "demo",
        "origin": "https://example.test",
        "password": "outer-secret",
        "users": [{"username": "sim-demo", "password": "private-password"}],
        "workspaces": [
            {"id": "space", "name": "Team", "invite_code": "private-invite"}
        ],
        "agents": [
            {"id": "agent", "client_id": "client", "refresh_token": "private-refresh"}
        ],
    }
    report = safe_report(state, config(), {"tasks": [10, 30]}, defaultdict(int), 0)
    rendered = json.dumps(report)
    for private in (
        "outer-secret",
        "private-password",
        "private-invite",
        "private-refresh",
        "client_id",
    ):
        assert private not in rendered
    assert report["tools"]["tasks"]["calls"] == 2 and report["agents"] == 1


@pytest.mark.asyncio
async def test_failure_report_does_not_echo_provider_exception(capsys):
    sim = Simulator(config())

    async def fail():
        raise RuntimeError("password=super-private token=super-private")

    sim.humans_and_spaces = fail
    assert await sim.run() == 1
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    report = events[-1]["report"]
    assert report["failure"]["check"] == "RuntimeError"
    assert "super-private" not in json.dumps(report)
    assert report["rounds_completed"] == 0 and not report["passed"]


@pytest.mark.asyncio
async def test_failed_batch_cancels_other_actors_before_reporting():
    sim = Simulator(config())
    stopped = asyncio.Event()

    async def ongoing():
        try:
            await asyncio.sleep(60)
        finally:
            stopped.set()

    async def failed():
        await asyncio.sleep(0)
        raise SimulationFailure("actor_failed")

    with pytest.raises(SimulationFailure):
        await sim.batch([ongoing(), failed()])
    assert stopped.is_set()
    await sim.http.aclose()


@pytest.mark.asyncio
async def test_partial_run_cannot_mutate_without_explicit_resume(capsys):
    sim = Simulator(config())
    sim.state["incomplete_run"] = True
    sim.humans_and_spaces = AsyncMock()
    assert await sim.run() == 1
    sim.humans_and_spaces.assert_not_awaited()
    report = json.loads(capsys.readouterr().out.splitlines()[-1])["report"]
    assert report["failure"]["check"] == "review_partial_run_before_resuming"
