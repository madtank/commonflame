"""Built-in accounts work independently of deployment location."""
import pytest
from app.core.auth_config import builtin_auth_enabled, local_browser_setup_enabled, registration_mode


@pytest.mark.parametrize('mode,enabled', [('builtin', True), ('local', True), ('BUILTIN', True), ('unsupported-provider', False)])
def test_builtin_mode_and_compatibility_alias(monkeypatch, mode, enabled):
    monkeypatch.setenv('AUTH_MODE', mode)
    assert builtin_auth_enabled() is enabled


def test_builtin_is_default(monkeypatch):
    monkeypatch.delenv('AUTH_MODE', raising=False)
    assert builtin_auth_enabled()


@pytest.mark.parametrize('origin,local', [
    ('http://localhost:3000', True), ('http://127.0.0.1:3000', True),
    ('https://[::1]:3443', True), ('https://workspace.example', False),
    ('http://localhost.attacker.example', False), ('http://user@localhost:3000', False),
    ('http://localhost:3000/path', False), ('file://localhost', False),
])
def test_auto_registration_uses_only_the_configured_loopback_origin(monkeypatch, origin, local):
    monkeypatch.setenv('PUBLIC_URL', origin)
    monkeypatch.setenv('REGISTRATION_MODE', 'auto')
    assert local_browser_setup_enabled() is local
    assert registration_mode() == ('open' if local else 'invite_only')


@pytest.mark.parametrize('mode,expected', [('open', 'open'), ('invite_only', 'invite_only'),
                                         ('closed', 'closed'), ('typo', 'closed')])
def test_registration_policy_is_explicit_and_unknown_values_fail_closed(monkeypatch, mode, expected):
    monkeypatch.setenv('REGISTRATION_MODE', mode)
    assert registration_mode() == expected
