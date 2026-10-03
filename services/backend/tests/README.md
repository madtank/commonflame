# Backend checks

Install requirements.txt and requirements-test.txt, then run `pytest -m "not integration"`.
The hermetic suite covers local password sessions, OAuth public surfaces, authorization and agent lifecycle.
Retained OAuth/DCR database integration tests are labeled `integration` and require an isolated PostgreSQL database.
The root `scripts/smoke-test.py` is the supported full-stack verification against the current Compose deployment.
