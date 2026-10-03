#!/usr/bin/env python3
"""Conservative source credential check. Never print matched credential values.

Gitleaks in CI is an additional gate; this small scanner also runs offline.
Fixtures that generate keys/tokens at runtime are allowed. Embedded values are not.
"""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = {
    "AWS access key": r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "GitHub credential": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b",
    "provider credential": r"\b(?:sk-ant-[A-Za-z0-9_-]{25,}|xox[baprs]-[A-Za-z0-9-]{20,})\b",
    "embedded JWT": r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{15,}",
    "embedded private key": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/=]{30,}",
    "credential in URL": r"(?:redis|postgres(?:ql)?(?:\+asyncpg)?)://[^\s:'\"]*:[^\s@${}'\"]{12,}@",
}
tracked = subprocess.check_output(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
).decode().split("\0")
findings = []
for relative in dict.fromkeys(tracked):
    if not relative:
        continue
    path = ROOT / relative
    if path.is_symlink() or not path.is_file():
        continue
    data = path.read_bytes()
    if b"\0" in data:
        continue
    content = data.decode("utf-8", errors="replace")
    for label, pattern in PATTERNS.items():
        for match in re.finditer(pattern, content):
            line = content.count("\n", 0, match.start()) + 1
            findings.append(f"{relative}:{line}: {label}")
if findings:
    print("Potential embedded credentials (values redacted):", file=sys.stderr)
    print("\n".join(findings), file=sys.stderr)
    sys.exit(1)
print(f"Credential pattern check passed ({len(tracked)-1} files inspected).")
