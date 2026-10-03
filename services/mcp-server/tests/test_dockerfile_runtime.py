"""Regression tests for the production Docker runtime contract.

The container runs uvicorn in multi-worker factory mode so ALB /health probes
aren't starved behind a busy MCP request, but forces a single worker when
stateful Streamable HTTP is enabled (in-process sessions can't span workers).
The worker-selection must treat MCP_STATELESS_HTTP truthily/falsily — a value
like "False" or "0" MUST resolve to one worker, not silently run many.
"""

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _cmd_shell_script() -> str:
    """Return the `sh -c` script embedded in the Dockerfile CMD."""
    for line in (ROOT / "Dockerfile").read_text().splitlines():
        if line.startswith("CMD "):
            argv = json.loads(line[len("CMD "):])
            # ["sh", "-c", "<script>"]
            assert argv[:2] == ["sh", "-c"], argv
            return argv[2]
    raise AssertionError("no CMD [sh -c ...] found in Dockerfile")


def _resolve_workers(stateless_value) -> int:
    """Run the Dockerfile's worker-selection logic and return $WORKERS."""
    script = _cmd_shell_script()
    # Drop the `exec uvicorn ...` tail and echo the resolved worker count.
    prefix = script.split("exec uvicorn")[0]
    test_script = prefix + "echo $WORKERS"
    env = {"PATH": "/usr/bin:/bin"}
    if stateless_value is not None:
        env["MCP_STATELESS_HTTP"] = stateless_value
    result = subprocess.run(
        ["sh", "-c", test_script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    match = re.search(r"(\d+)", result.stdout)
    assert match, f"no worker count in output: {result.stdout!r}"
    return int(match.group(1))


def test_dockerfile_uses_multi_worker_uvicorn_factory_runtime():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "ENV MCP_UVICORN_WORKERS=2" in dockerfile
    assert "uvicorn fastmcp_server.server:create_app --factory" in dockerfile
    assert "--workers $WORKERS" in dockerfile
    assert '["python3", "-m", "fastmcp_server.server"]' not in dockerfile


def test_stateless_defaults_to_multiple_workers():
    assert _resolve_workers(None) == 2          # unset → default stateless
    assert _resolve_workers("true") == 2
    assert _resolve_workers("") == 2            # empty → default stateless


def test_stateful_forces_single_worker_for_any_falsy_value():
    # A stateful deployment (in-process sessions) must run exactly one worker,
    # regardless of how the operator spells the falsy value.
    for value in ("false", "False", "FALSE", "0", "no", "off"):
        assert _resolve_workers(value) == 1, f"{value!r} should force 1 worker"
