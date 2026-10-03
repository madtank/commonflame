"""Built-in accounts work independently of deployment location."""
import pytest
from app.core.auth_config import builtin_auth_enabled


@pytest.mark.parametrize('mode,enabled', [('builtin', True), ('local', True), ('BUILTIN', True), ('unsupported-provider', False)])
def test_builtin_mode_and_compatibility_alias(monkeypatch, mode, enabled):
    monkeypatch.setenv('AUTH_MODE', mode)
    assert builtin_auth_enabled() is enabled


def test_builtin_is_default(monkeypatch):
    monkeypatch.delenv('AUTH_MODE', raising=False)
    assert builtin_auth_enabled()
