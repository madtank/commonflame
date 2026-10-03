# Contributing

Thanks for trying Waystation. The aim of this release is to make the existing
work useful and easy to explore. Feedback and focused contributions are welcome;
there is no commitment to a release schedule, support SLA, or feature roadmap.

For a bug report, include your OS, Docker/Compose version, agent host/version,
the step that failed, expected behavior, and redacted diagnostics. Never include
passwords, bearer/refresh credentials, signing keys, private uploads, or database
dumps. For a suspected vulnerability, use GitHub private vulnerability reporting
when enabled, rather than placing exploit details or credentials in a public issue.

Start with a small issue or discussion of a proposed change. Keep pull requests
focused and explain what changed and how you verified it. Changes to account
setup, OAuth, scopes, workspace isolation, or credential handling need regression
proof for the affected flow. Additional features are optional contributions,
not requirements for the initial alpha.

## Local checks

```sh
python3 scripts/check-secrets.py
cd services/frontend
npm ci --ignore-scripts
npm run type-check
npm run test:run
npm run build
```

Backend tests use Python 3.11 with requirements.txt and pytest/pytest-asyncio/
pytest-mock; run `python -m pytest -q -m "not integration" tests/`. The 32 retained
historical integration tests are not part of the supported hermetic regression
suite. The complete MCP suite also needs Node for its widget JavaScript checks;
see services/mcp-server/README.md. GitHub Actions defines the full check commands
and an isolated Compose smoke installation.

Do not copy this installation's `.env`, volumes, or credentials into a pull
request. Respect third-party license notices. Project licensing must be settled
before external code contributions are accepted.
