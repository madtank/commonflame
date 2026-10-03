"""
OS detection utilities for cross-platform MCP configuration
"""
import os
from typing import Optional
from pathlib import Path


def get_user_home_path(user_agent: Optional[str] = None, request_headers: Optional[dict] = None) -> str:
    """
    Get the correct home directory path based on the user's OS.

    Args:
        user_agent: User-Agent header from the request
        request_headers: Full request headers for additional context

    Returns:
        Home directory path in the correct format for the user's OS
    """
    # In Docker, we need to determine the HOST OS, not the container OS
    # Try to get from environment first (can be set during registration)
    host_os = os.getenv("HOST_OS", "").lower()

    # If not set, try to detect from User-Agent
    if not host_os and user_agent:
        user_agent_lower = user_agent.lower()
        if "windows" in user_agent_lower or "win32" in user_agent_lower or "win64" in user_agent_lower:
            host_os = "windows"
        elif "mac" in user_agent_lower or "darwin" in user_agent_lower:
            host_os = "darwin"
        else:
            host_os = "linux"  # Default to Linux for unknown

    # Get the appropriate home directory
    if host_os == "windows":
        # Windows uses %USERPROFILE% or %HOMEDRIVE%%HOMEPATH%
        # For MCP config, we need to use environment variable syntax
        return "%USERPROFILE%"
    else:
        # macOS and Linux can use $HOME
        # For Docker, we need the HOST's home directory
        host_home = os.getenv("HOST_HOME_DIR")
        if host_home:
            # Running in Docker with HOST_HOME_DIR set
            return host_home
        else:
            # Running locally or HOST_HOME_DIR not set
            return str(Path.home())


def format_config_path(base_path: str, relative_path: str, os_type: Optional[str] = None) -> str:
    """
    Format a configuration path for the target OS.

    Args:
        base_path: Base path (home directory)
        relative_path: Relative path from home (e.g., ".mcp-auth/production/...")
        os_type: Target OS type (windows, darwin, linux)

    Returns:
        Properly formatted path for the target OS
    """
    if os_type == "windows":
        # Windows path format
        # Convert forward slashes to backslashes
        windows_relative = relative_path.replace('/', '\\').lstrip('.')
        full_path = f"{base_path}\\{windows_relative}"
        # Remove leading backslash if present
        return full_path.replace("\\\\", "\\")
    else:
        # Unix-style path (macOS, Linux)
        # Ensure proper path joining
        if relative_path.startswith("."):
            return f"{base_path}/{relative_path}"
        else:
            return f"{base_path}/.{relative_path}"
