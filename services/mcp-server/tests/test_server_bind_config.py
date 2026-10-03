"""Tests for direct Uvicorn bind configuration."""

from fastmcp_server import server


def test_uvicorn_bind_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("FASTMCP_HOST", raising=False)

    config = server.uvicorn_bind_config()

    assert config["host"] == "127.0.0.1"
    assert config["port"] == server.FASTMCP_PORT


def test_uvicorn_bind_all_interfaces_requires_explicit_env(monkeypatch):
    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")

    config = server.uvicorn_bind_config()

    assert config["host"] == "0.0.0.0"
    assert config["port"] == server.FASTMCP_PORT
