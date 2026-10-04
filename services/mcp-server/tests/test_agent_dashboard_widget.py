"""Tests for the static agents MCP app widget."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


WIDGET_PATH = (
    Path(__file__).resolve().parents[1]
    / "fastmcp_server"
    / "resources"
    / "static"
    / "widgets"
    / "agent-dashboard.html"
)


NODE_AVAILABLE = shutil.which("node") is not None


AVAILABILITY_HELPER_PRELUDE = """
function agentControl(agent) {
  return agent && agent.control && typeof agent.control === 'object' ? agent.control : {};
}
function agentAvailability(agent) {
  return agent && agent.availability && typeof agent.availability === 'object' ? agent.availability : {};
}
function agentSetup(agent) {
  return agent && agent.setup && typeof agent.setup === 'object' ? agent.setup : {};
}
function agentIsDisabled(agent) {
  const control = agentControl(agent);
  const controlState = String(control.state || control.status || control.kind || '').toLowerCase();
  return Boolean(
    control.is_disabled
    || control.disabled
    || (agent && agent.is_disabled)
    || (agent && agent.disabled)
    || controlState === 'break'
    || controlState === 'disabled'
  );
}
"""


def _widget_function_source(function_name: str) -> str:
    html = WIDGET_PATH.read_text(encoding="utf-8")
    start = f"// @test-extract:start {function_name}"
    end = f"// @test-extract:end {function_name}"
    start_index = html.find(start)
    end_index = html.find(end)
    if start_index < 0 or end_index < 0 or end_index <= start_index:
        raise AssertionError(
            f"Could not find extract markers for {function_name} in agent-dashboard.html"
        )
    return html[start_index + len(start) : end_index]


def _call_widget_function(function_name: str, *args: object, prelude: str = "") -> str:
    html = WIDGET_PATH.read_text(encoding="utf-8")
    source = _widget_function_source(function_name)
    script = f"""
const fs = require('fs');
const html = fs.readFileSync(0, 'utf8');
const prelude = {json.dumps(prelude)};
const source = {json.dumps(source)};
const fn = new Function(prelude + "\\n" + source + "; return {function_name};")();
process.stdout.write(JSON.stringify(fn(...{json.dumps(args)})));
"""
    result = subprocess.run(
        ["node", "-e", script],
        input=html,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout or "node widget extraction failed")
    return json.loads(result.stdout)


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node not available for static widget JS evaluation")
def test_agents_widget_uses_quick_action_emoji_mapping_for_known_agent_handles() -> None:
    assert _call_widget_function("quickActionAgentEmoji", "canary", "Canary") == "🐤"
    assert _call_widget_function("quickActionAgentEmoji", "atlas", "Atlas") == "🗺️"
    assert _call_widget_function("quickActionAgentEmoji", "nyx", "Nyx") == "🌘"


def test_agents_widget_uses_quick_action_emoji_mapping_for_ax_display_name() -> None:
    assert _call_widget_function("quickActionAgentEmoji", "space_agent", "Commonflame") == "✨"


def test_roster_status_controls_include_explicit_availability_filters() -> None:
    html = WIDGET_PATH.read_text(encoding="utf-8")

    assert '["all", "All"]' in html
    assert '["online", "Online"]' in html
    assert '["routable", "Routable/queued"]' in html
    assert '["needs_setup", "Needs setup"]' in html
    assert '["inactive", "Dormant/offline"]' in html
    assert '"data-testid": "agent-status-" + filter' in html
    assert 'state.statusFilter = filter;' in html


def test_inactive_roster_toggle_is_native_button_with_expanded_state() -> None:
    html = WIDGET_PATH.read_text(encoding="utf-8")

    assert '"button"' in html
    assert 'className: "inactive-toggle"' in html
    assert '"aria-expanded": revealDormant ? "true" : "false"' in html
    assert 'role: "button"' not in html
    assert 'tabindex: "0"' not in html


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node not available for static widget JS evaluation")
def test_agents_widget_prefers_explicit_live_availability_label_over_stale_lifecycle() -> None:
    agent = {
        "messageability": {"availability_label": "online", "lifecycle_state": "dormant"},
        "availability": {"state": "unavailable", "reason": "offline"},
        "lifecycle_state": "dormant",
    }

    assert (
        _call_widget_function(
            "availabilityLabelKey",
            agent,
            prelude=AVAILABILITY_HELPER_PRELUDE,
        )
        == "online"
    )


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node not available for static widget JS evaluation")
def test_agents_widget_derives_live_and_idle_labels_from_availability_state() -> None:
    assert (
        _call_widget_function(
            "availabilityLabelKey",
            {"availability": {"state": "available"}, "lifecycle_state": "dormant"},
            prelude=AVAILABILITY_HELPER_PRELUDE,
        )
        == "online"
    )
    assert (
        _call_widget_function(
            "availabilityLabelKey",
            {"availability": {"state": "degraded", "reason": "recently_seen"}},
            prelude=AVAILABILITY_HELPER_PRELUDE,
        )
        == "idle"
    )


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node not available for static widget JS evaluation")
def test_agents_widget_maps_setup_and_disabled_before_dormant_fallbacks() -> None:
    assert (
        _call_widget_function(
            "availabilityLabelKey",
            {"setup": {"required": True}, "availability": {"state": "unavailable"}},
            prelude=AVAILABILITY_HELPER_PRELUDE,
        )
        == "needs_setup"
    )
    assert (
        _call_widget_function(
            "availabilityLabelKey",
            {"control": {"state": "disabled"}, "messageability": {"availability_label": "online"}},
            prelude=AVAILABILITY_HELPER_PRELUDE,
        )
        == "disabled"
    )


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node not available for static widget JS evaluation")
def test_agents_widget_normalized_status_uses_live_labels_before_stale_fields() -> None:
    availability_source = _widget_function_source("availabilityLabelKey")
    prelude = AVAILABILITY_HELPER_PRELUDE + "\n" + availability_source

    assert (
        _call_widget_function(
            "normalizedStatus",
            {"messageability": {"availability_label": "idle"}, "status": "offline"},
            prelude=prelude,
        )
        == "active"
    )
    assert (
        _call_widget_function(
            "normalizedStatus",
            {"control": {"state": "disabled"}, "messageability": {"availability_label": "online"}},
            prelude=prelude,
        )
        == "disabled"
    )


def test_agents_widget_all_status_is_explicit_complete_universe_filter() -> None:
    html = WIDGET_PATH.read_text(encoding="utf-8")

    assert '["all", "All"]' in html
    assert 'function dormantRevealForcedByView()' in html
    assert 'state.statusFilter === "all" || state.search.trim() || state.statusFilter === "inactive"' in html
    assert 'return Boolean(dormantRevealForcedByView() || state.showInactive);' in html
    assert 'All means the complete selected-scope universe' in html
    assert 'state.statusFilter = filter;' in html
    assert 'state.statusFilter = state.statusFilter === filter ? "all"' not in html


def test_agents_widget_separates_online_from_routable_and_lifecycle_labels() -> None:
    html = WIDGET_PATH.read_text(encoding="utf-8")

    assert 'idle:        { text: "Routable/queued"' in html
    assert '["online", "Online"]' in html
    assert '["routable", "Routable/queued"]' in html
    assert '["inactive", "Dormant/offline"]' in html
    assert 'if (filter === "online") return availabilityKey === "online";' in html
    assert '"Routable/queued" is the user-facing name for the backend "idle" key.' in html
    assert 'if (filter === "routable") return availabilityKey === "idle";' in html
    assert 'if (filter === "disabled") return agentIsDisabled(agent);' in html
