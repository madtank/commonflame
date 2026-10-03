"""Safe defaults for hermetic tests; integration tests override their DB URL."""
import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("AUTH_MODE", "local")
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-only-session-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://localhost/waystation_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
