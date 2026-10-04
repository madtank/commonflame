#!/usr/bin/env python3
"""Report container state and allowlisted error categories, never raw logs."""
import json
import re
import subprocess


def capture(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


for service in ("backend", "frontend", "mcp", "postgres", "redis"):
    ids = capture("docker", "compose", "ps", "-aq", service).split()
    for container in ids:
        state = json.loads(capture("docker", "inspect", "--format", "{{json .State}}", container))
        print(service, {key: state.get(key) for key in ("Status", "OOMKilled", "ExitCode")})
    logs = capture("docker", "compose", "logs", "--no-color", "--tail", "200", service)
    categories = (
        "upstream sent too big header", "upstream sent invalid header",
        "upstream prematurely closed connection", "upstream timed out",
        "Connection refused", "Connection reset by peer", "no live upstreams",
        "host not found", "Error in ASGI application", "Traceback (most recent call last)",
    )
    for category in categories:
        count = logs.count(category)
        if count:
            print(f"{service}: {category} ({count})")
    # Only exception class names and traceback source locations are retained.
    # SQL parameters, request URLs, bodies, headers and messages are discarded.
    for line in logs.splitlines():
        exception = re.search(r"\b([A-Za-z_][A-Za-z_0-9]*(?:Error|Exception)):", line)
        frame = re.search(r'File "(/(?:app|usr/local/lib/python3\.11)/[^"\n]+)", line (\d+), in ([A-Za-z_][A-Za-z_0-9]*)', line)
        if exception:
            print(f"{service}: exception class {exception.group(1)}")
        if frame:
            print(f"{service}: {frame.group(1)}:{frame.group(2)} in {frame.group(3)}")
