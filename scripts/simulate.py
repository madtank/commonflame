#!/usr/bin/env python3
"""Run real scripted MCP agents; keep private checkpoints out of console output."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def atomic_json(path, value, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Refusing a symlink output file")
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".sim-")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
        os.chmod(name, 0o600 if private else 0o644)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--agents",
        type=int,
        default=5,
        help="Desired total actors; existing actors are reused",
    )
    p.add_argument("--users", type=int)
    p.add_argument(
        "--workspaces",
        type=int,
        help="Team spaces; default one for two actors, otherwise two",
    )
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument("--rounds", type=int, default=2)
    p.add_argument(
        "--pause", type=float, default=0, help="Seconds between conversation rounds"
    )
    p.add_argument(
        "--deadline", type=float, default=1800, help="Maximum run time in seconds"
    )
    p.add_argument(
        "--resume-after-failure",
        action="store_true",
        help="Explicitly continue a reviewed partial run; inspect ambiguous writes first",
    )
    p.add_argument(
        "--project", help="Compose project; otherwise uses the current env/checkout"
    )
    p.add_argument("--state", type=Path, default=Path(".local/simulator/session.json"))
    p.add_argument("--report", type=Path, default=Path(".local/simulator/report.json"))
    args = p.parse_args()
    state = json.loads(args.state.read_text()) if args.state.exists() else None
    if args.users is None:
        args.users = state["topology"]["users"] if state else 2
    if args.workspaces is None:
        args.workspaces = (
            state["topology"]["workspaces"]
            if state
            else min(2, max(1, args.agents // 2))
        )
    if min(args.agents, args.users, args.workspaces, args.concurrency, args.rounds) < 1:
        p.error("Population, topology, concurrency and rounds must be positive")
    if args.agents < 2 * args.workspaces or args.workspaces > 5 * args.users:
        p.error(
            "Use at least two agents per team space and at most five team spaces per human"
        )
    if args.pause < 0 or args.deadline <= 0:
        p.error("Pause must be nonnegative and deadline positive")
    if state and state["topology"] != {
        "users": args.users,
        "workspaces": args.workspaces,
    }:
        p.error("Use a separate state file to change the user/workspace topology")
    if state and args.agents < len(state["agents"]):
        p.error("Existing actors are retained; desired population cannot shrink")
    if state and state.get("incomplete_run") and not args.resume_after_failure:
        p.error(
            "Inspect the partial run report/fixtures, then explicitly use --resume-after-failure"
        )
    if args.state.resolve() == args.report.resolve():
        p.error("Private state and public report must use different files")
    if args.state.is_symlink() or args.report.is_symlink():
        p.error("Refusing symlink state/report files")
    if state:
        os.chmod(args.state, 0o600)
    cfg = {
        key: getattr(args, key)
        for key in [
            "agents",
            "users",
            "workspaces",
            "concurrency",
            "rounds",
            "pause",
            "deadline",
            "resume_after_failure",
        ]
    }
    command = ["docker", "compose"]
    if args.project:
        command += ["-p", args.project]
    command += ["exec", "-T", "mcp", "python", "-m", "fastmcp_server.simulator"]
    # Child stdout is a private checkpoint protocol. Capture stderr too: third-
    # party exception messages must never expose OAuth responses in console logs.
    with tempfile.TemporaryFile(mode="w+") as diagnostic:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=diagnostic,
            text=True,
        )
        proc.stdin.write(json.dumps({"config": cfg, "state": state}) + "\n")
        proc.stdin.close()
        report = None
        for line in proc.stdout:
            event = json.loads(line)
            if "state" in event:
                atomic_json(args.state, event["state"], private=True)
            if "progress" in event:
                print(event["progress"], file=sys.stderr, flush=True)
            if "report" in event:
                report = event["report"]
                atomic_json(args.report, report)
        code = proc.wait()
    if report is None:
        print(
            "Simulator runtime failed; rebuild the MCP image and check its health. Raw output withheld.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("Simulator runner failed; private diagnostics withheld.", file=sys.stderr)
        raise SystemExit(1) from None
