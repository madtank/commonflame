"""
Portkey Configuration Service
Manages Portkey headers, routing, and configuration for different endpoints
"""
import json
import os
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum


class EndpointCategory(Enum):
    """Categories of endpoints requiring different guardrail configurations"""
    MESSAGE_INPUT = "message_input"
    TASK_INPUT = "task_input"
    AGENT_REGISTRATION = "agent_registration"
    MESSAGE_OUTPUT = "message_output"
    MCP_OUTPUT = "mcp_output"


@dataclass
class PortkeyConfig:
    """Portkey configuration for specific endpoint category"""
    config_name: str
    provider: str = "openai"
    api_key: str = "dummy"  # Guardrails don't need real API key
    guardrails: List[Dict[str, Any]] = None
    timeout: int = 10
    retry_attempts: int = 2

    def to_headers(self) -> Dict[str, str]:
        """Convert to Portkey request headers"""
        config_dict = {
            "provider": self.provider,
            "api_key": self.api_key,
            "guardrails": self.guardrails or [],
            "timeout": self.timeout,
            "retry": self.retry_attempts
        }

        return {
            "Content-Type": "application/json",
            "x-portkey-config": json.dumps(config_dict),
            "x-portkey-provider": self.provider,
            "x-portkey-api-key": self.api_key
        }


class PortkeyConfigService:
    """Service for managing Portkey configurations"""

    def __init__(self):
        # Use Docker service name for internal communication, fallback to localhost for dev
        self.portkey_url = os.getenv("PORTKEY_URL", os.getenv("VITE_PORTKEY_URL", "http://portkey-gateway:8787"))
        if "localhost" in self.portkey_url or "127.0.0.1" in self.portkey_url:
            # In development, use localhost
            self.portkey_url = "http://localhost:8787"
        self.configs: Dict[EndpointCategory, PortkeyConfig] = {}
        self._initialize_configs()

    def _initialize_configs(self):
        """Initialize default configurations for different endpoint categories"""

        # Message Input Guardrails - Strictest protection
        self.configs[EndpointCategory.MESSAGE_INPUT] = PortkeyConfig(
            config_name="message_input_guardrails",
            guardrails=[
                {
                    "id": "prompt_injection_detector",
                    "type": "prompt_injection",
                    "enabled": True,
                    "action": "deny",
                    "confidence_threshold": 0.7,
                    "description": "Detect and block prompt injection attempts"
                },
                {
                    "id": "pii_detector",
                    "type": "pii",
                    "enabled": True,
                    "action": "sanitize",
                    "pii_types": ["email", "phone", "ssn", "credit_card", "address", "name"],
                    "description": "Detect and sanitize PII in user messages"
                },
                {
                    "id": "toxic_content_filter",
                    "type": "toxicity",
                    "enabled": True,
                    "action": "deny",
                    "categories": ["hate", "harassment", "violence", "sexual", "self_harm"],
                    "threshold": 0.8,
                    "description": "Filter toxic and harmful content"
                },
                {
                    "id": "cross_tenant_detector",
                    "type": "regex",
                    "enabled": True,
                    "action": "log",
                    "patterns": [
                        r"(?i)(user|org|tenant)[_-]?id[:\s]*[a-f0-9-]{36}",
                        r"(?i)switch[_\s]+to[_\s]+(user|tenant|org)",
                        r"(?i)(impersonate|access[_\s]+as)[_\s]+\w+",
                        r"(?i)(admin|root|superuser)[_\s]+(access|mode|privileges)"
                    ],
                    "description": "Detect potential cross-tenant access attempts"
                },
                {
                    "id": "gibberish_detector",
                    "type": "gibberish",
                    "enabled": True,
                    "action": "log",
                    "threshold": 0.9,
                    "description": "Detect nonsensical or randomly generated content"
                }
            ]
        )

        # Task Input Guardrails - Medium protection
        self.configs[EndpointCategory.TASK_INPUT] = PortkeyConfig(
            config_name="task_input_guardrails",
            guardrails=[
                {
                    "id": "task_prompt_injection",
                    "type": "prompt_injection",
                    "enabled": True,
                    "action": "log",  # Log but don't block tasks
                    "confidence_threshold": 0.8,
                    "description": "Monitor for prompt injection in task descriptions"
                },
                {
                    "id": "task_pii_detector",
                    "type": "pii",
                    "enabled": True,
                    "action": "sanitize",
                    "pii_types": ["email", "phone", "ssn", "credit_card"],
                    "description": "Sanitize PII in task descriptions"
                },
                {
                    "id": "code_injection_detector",
                    "type": "code",
                    "enabled": True,
                    "action": "log",
                    "languages": ["sql", "javascript", "python", "bash"],
                    "description": "Detect potential code injection in task requirements"
                },
                {
                    "id": "task_content_filter",
                    "type": "toxicity",
                    "enabled": True,
                    "action": "deny",
                    "categories": ["hate", "harassment", "violence"],
                    "threshold": 0.9,
                    "description": "Filter inappropriate task content"
                }
            ]
        )

        # Agent Registration Guardrails - Basic protection
        self.configs[EndpointCategory.AGENT_REGISTRATION] = PortkeyConfig(
            config_name="agent_registration_guardrails",
            guardrails=[
                {
                    "id": "agent_name_filter",
                    "type": "regex",
                    "enabled": True,
                    "action": "deny",
                    "patterns": [
                        r"(?i)(admin|root|system|superuser)",
                        r"(?i)(test|demo|example)_?\d*$",
                        r"[<>\"'&;()]",  # Potentially dangerous characters
                    ],
                    "description": "Filter inappropriate agent names"
                },
                {
                    "id": "agent_description_pii",
                    "type": "pii",
                    "enabled": True,
                    "action": "sanitize",
                    "pii_types": ["email", "phone", "address"],
                    "description": "Remove PII from agent descriptions"
                }
            ]
        )

        # Message Output Guardrails - Protect responses going to users
        self.configs[EndpointCategory.MESSAGE_OUTPUT] = PortkeyConfig(
            config_name="message_output_guardrails",
            guardrails=[
                {
                    "id": "response_pii_detector",
                    "type": "pii",
                    "enabled": True,
                    "action": "sanitize",
                    "pii_types": ["email", "phone", "ssn", "credit_card", "address"],
                    "description": "Sanitize PII in message responses"
                },
                {
                    "id": "data_leakage_detector",
                    "type": "regex",
                    "enabled": True,
                    "action": "sanitize",
                    "patterns": [
                        r"(?i)(password|pwd)[:\s=]*[^\s]+",
                        r"(?i)(token|key|secret)[:\s=]*[a-zA-Z0-9_-]{8,}",
                        r"(?i)api[_-]?key[:\s=]*[a-zA-Z0-9_-]+",
                        r"(?i)(database|db)[_\s]+(url|connection|string)[:\s=]*[^\s]+"
                    ],
                    "description": "Prevent sensitive data leakage in responses"
                },
                {
                    "id": "response_content_filter",
                    "type": "toxicity",
                    "enabled": True,
                    "action": "sanitize",
                    "categories": ["hate", "harassment", "violence", "sexual"],
                    "threshold": 0.7,
                    "description": "Filter inappropriate content in responses"
                },
                {
                    "id": "cross_tenant_response_filter",
                    "type": "regex",
                    "enabled": True,
                    "action": "sanitize",
                    "patterns": [
                        r"user[_-]?id[:\s]*[a-f0-9-]{36}",
                        r"org[_-]?id[:\s]*[a-f0-9-]{36}",
                        r"@\w+\.(com|org|net|io)",  # Email addresses that might belong to other orgs
                    ],
                    "description": "Prevent cross-tenant data exposure in responses"
                }
            ]
        )

        # MCP Output Guardrails - Protect MCP/SSE responses
        self.configs[EndpointCategory.MCP_OUTPUT] = PortkeyConfig(
            config_name="mcp_output_guardrails",
            guardrails=[
                {
                    "id": "mcp_pii_detector",
                    "type": "pii",
                    "enabled": True,
                    "action": "sanitize",
                    "pii_types": ["email", "phone", "ssn", "credit_card"],
                    "description": "Sanitize PII in MCP responses"
                },
                {
                    "id": "mcp_data_leakage_detector",
                    "type": "regex",
                    "enabled": True,
                    "action": "deny",
                    "patterns": [
                        r"(?i)(password|pwd|secret|token|key)[:\s=]*[^\s]+",
                        r"(?i)(internal|private|confidential)[_\s]+\w+",
                        r"(?i)(database|db|sql)[_\s]+(query|connection|url)"
                    ],
                    "description": "Prevent internal data leakage through MCP"
                },
                {
                    "id": "mcp_protocol_injection",
                    "type": "regex",
                    "enabled": True,
                    "action": "deny",
                    "patterns": [
                        r"(?i)(mcp|protocol)[_\s]+(inject|override|bypass)",
                        r"(?i)(agent[_\s]+card|capability)[_\s]+(fake|spoof)",
                        r"(?i)(host[_\s]+agent|server)[_\s]+(compromise|takeover)"
                    ],
                    "description": "Detect MCP protocol manipulation attempts"
                }
            ]
        )

    def get_config(self, category: EndpointCategory) -> PortkeyConfig:
        """Get configuration for specific endpoint category"""
        return self.configs.get(category, self.configs[EndpointCategory.MESSAGE_INPUT])

    def get_headers(self, category: EndpointCategory, user_context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """Get Portkey headers for specific endpoint category"""
        config = self.get_config(category)
        headers = config.to_headers()

        # Add user context for tenant-aware filtering
        if user_context:
            headers["x-portkey-user-id"] = user_context.get("user_id", "")
            headers["x-portkey-org-id"] = user_context.get("space_id", "")
            headers["x-portkey-metadata"] = json.dumps(user_context)

        return headers

    def update_config(self, category: EndpointCategory, guardrails: List[Dict[str, Any]]):
        """Update guardrails configuration for specific category"""
        if category in self.configs:
            self.configs[category].guardrails = guardrails

    def enable_guardrail(self, category: EndpointCategory, guardrail_id: str):
        """Enable specific guardrail for category"""
        config = self.configs.get(category)
        if config and config.guardrails:
            for guardrail in config.guardrails:
                if guardrail.get("id") == guardrail_id:
                    guardrail["enabled"] = True

    def disable_guardrail(self, category: EndpointCategory, guardrail_id: str):
        """Disable specific guardrail for category"""
        config = self.configs.get(category)
        if config and config.guardrails:
            for guardrail in config.guardrails:
                if guardrail.get("id") == guardrail_id:
                    guardrail["enabled"] = False

    def get_endpoint_category(self, endpoint: str, method: str = "POST") -> EndpointCategory:
        """Determine endpoint category based on URL path"""
        if "/auth/messages" in endpoint and method == "POST":
            return EndpointCategory.MESSAGE_INPUT
        elif "/auth/messages" in endpoint and method == "GET":
            return EndpointCategory.MESSAGE_OUTPUT
        elif "/api/tasks" in endpoint and method == "POST":
            return EndpointCategory.TASK_INPUT
        elif "/mcp/register_agent" in endpoint:
            return EndpointCategory.AGENT_REGISTRATION
        elif "/mcp/" in endpoint or "/sse" in endpoint:
            return EndpointCategory.MCP_OUTPUT
        else:
            # Default to message input for strictest protection
            return EndpointCategory.MESSAGE_INPUT

    def create_payload(
        self,
        content: str,
        user_context: Dict[str, Any],
        model: str = "gpt-3.5-turbo"
    ) -> Dict[str, Any]:
        """Create Portkey request payload for guardrail validation"""
        return {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 1,  # Minimal tokens since we only want guardrail validation
            "metadata": user_context
        }


# Singleton instance
portkey_config_service = PortkeyConfigService()
