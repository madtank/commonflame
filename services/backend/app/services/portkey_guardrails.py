"""
Portkey Guardrails Service
Handles input/output validation using Portkey AI gateway
"""
import asyncio
import json
import logging
import os
from typing import Dict, Any, Optional, Tuple, List
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
import aiohttp
from fastapi import HTTPException

from ..models.guardrail_violation import GuardrailViolation
from ..models.guardrail_config import GuardrailConfig
from ..models.user import User
from ..core.database import get_db_session
from sqlalchemy import select

logger = logging.getLogger(__name__)


class ViolationType(Enum):
    """Types of guardrail violations"""
    PROMPT_INJECTION = "prompt_injection"
    PII_DETECTED = "pii_detected"
    CONTENT_FILTER = "content_filter"
    REGEX_VIOLATION = "regex_violation"
    JSON_SCHEMA_VIOLATION = "json_schema_violation"
    CODE_INJECTION = "code_injection"
    GIBBERISH_CONTENT = "gibberish_content"
    TOXIC_CONTENT = "toxic_content"
    CROSS_TENANT_RISK = "cross_tenant_risk"


class GuardrailAction(Enum):
    """Actions to take when guardrail is triggered"""
    DENY = "deny"
    LOG = "log"
    SANITIZE = "sanitize"
    FALLBACK = "fallback"
    RETRY = "retry"


class ContentType(Enum):
    """Type of content being validated"""
    INPUT = "input"
    OUTPUT = "output"


@dataclass
class GuardrailResult:
    """Result of guardrail validation"""
    passed: bool
    violation_type: Optional[ViolationType] = None
    severity: str = "medium"  # low, medium, high, critical
    description: str = ""
    original_content: str = ""
    sanitized_content: Optional[str] = None
    confidence_score: Optional[int] = None
    portkey_response: Optional[Dict] = None
    action: GuardrailAction = GuardrailAction.LOG
    cross_tenant_risk: bool = False


class PortkeyGuardrailsService:
    """Service for validating content through Portkey AI gateway"""

    def __init__(self, portkey_url: str = None):
        # Use Docker service name for internal communication, fallback to localhost for dev
        if portkey_url is None:
            portkey_url = os.getenv("PORTKEY_URL", os.getenv("VITE_PORTKEY_URL", "http://portkey-gateway:8787"))
            if "localhost" in portkey_url or "127.0.0.1" in portkey_url:
                portkey_url = "http://localhost:8787"
        self.portkey_url = portkey_url
        self.session: Optional[aiohttp.ClientSession] = None

        # Configuration cache (loaded from database)
        self._config_cache = {}
        self._cache_timestamp = None
        self._cache_ttl = 300  # 5 minutes cache TTL

    async def _load_configurations_from_db(self, space_id: str, endpoint: str) -> List[Dict[str, Any]]:
        """Load guardrail configurations from database for given organization and endpoint"""
        try:
            from ..core.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                # Query configurations that match the endpoint and are enabled
                query = (
                    select(GuardrailConfig)
                    .where(GuardrailConfig.space_id == space_id)
                    .where(GuardrailConfig.enabled == True)
                    .order_by(GuardrailConfig.priority.desc())  # Higher priority first
                )

                result = await db.execute(query)
                configs = result.scalars().all()

                # Filter configurations that match the endpoint
                matching_configs = []
                for config in configs:
                    if config.matches_endpoint(endpoint):
                        # Convert database config to service format
                        service_config = {
                            "name": config.config_name,
                            "type": config.config_type,
                            "enabled": config.enabled,
                            "action": config.action_on_violation,
                            "priority": config.priority,
                            **config.portkey_config  # Merge Portkey-specific configuration
                        }
                        matching_configs.append(service_config)

                logger.info(f"🔍 LOADED {len(matching_configs)} guardrail configurations for org {space_id} endpoint {endpoint}")
                return matching_configs

        except Exception as e:
            logger.error(f"❌ FAILED TO LOAD CONFIGURATIONS FROM DB: {e}")
            # Return fallback configurations if database loading fails
            return self._get_fallback_configurations()

    def _get_fallback_configurations(self) -> List[Dict[str, Any]]:
        """Fallback configurations when database loading fails"""
        return [
            {
                "name": "prompt_injection_detector",
                "type": "prompt_injection",
                "enabled": True,
                "action": "deny",
                "severity_threshold": "medium"
            },
            {
                "name": "pii_detector",
                "type": "pii_detection",
                "enabled": True,
                "action": "deny",  # Changed from sanitize to deny for security
                "pii_types": ["ssn"]
            }
        ]

    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()

    async def validate_input(
        self,
        content: str,
        user: User,
        endpoint: str,
        context: Optional[Dict[str, Any]] = None
    ) -> GuardrailResult:
        """Validate input content before processing"""
        return await self._validate_content(
            content=content,
            user=user,
            endpoint=endpoint,
            content_type=ContentType.INPUT,
            context=context
        )

    async def validate_output(
        self,
        content: str,
        user: User,
        endpoint: str,
        context: Optional[Dict[str, Any]] = None
    ) -> GuardrailResult:
        """Validate output content before returning to user"""
        return await self._validate_content(
            content=content,
            user=user,
            endpoint=endpoint,
            content_type=ContentType.OUTPUT,
            context=context
        )

    async def _validate_content(
        self,
        content: str,
        user: User,
        endpoint: str,
        content_type: ContentType,
        config: Dict[str, Any] = None,  # Made optional - will load from DB
        context: Optional[Dict[str, Any]] = None
    ) -> GuardrailResult:
        """Internal method to validate content using Portkey AI + database-driven guardrails"""

        try:
            logger.warning(f"🔍 GUARDRAILS SERVICE CALLED: '{content[:30]}...' for {user.username} on {endpoint}")

            # Load configurations from database for this organization and endpoint
            effective_org = getattr(user, '_effective_space_id', None) or getattr(user, 'current_space_id', None) or user.space_id
            guardrail_configs = await self._load_configurations_from_db(str(effective_org), endpoint)
            logger.warning(f"🔍 DATABASE CONFIGS LOADED: {len(guardrail_configs)} configurations for org {effective_org}")

            # FIRST: Try Portkey AI-powered validation
            try:
                portkey_result = await self._validate_with_portkey(content, user, endpoint, content_type)
                logger.warning(f"🔍 PORTKEY RESULT: result={portkey_result is not None}, passed={portkey_result.passed if portkey_result else 'N/A'}")
                if portkey_result and not portkey_result.passed:
                    logger.warning(f"Portkey AI violation detected: {portkey_result.violation_type} - {portkey_result.description}")
                    return portkey_result
                elif portkey_result and portkey_result.passed:
                    logger.warning(f"🔍 PORTKEY PASSED: Content approved by Portkey AI, checking database guardrails")
            except Exception as e:
                logger.warning(f"Portkey validation failed, falling back to database guardrails: {e}")

            # SECOND: Run database-loaded guardrails (sorted by priority)
            logger.warning(f"🔍 DATABASE GUARDRAILS: Found {len(guardrail_configs)} guardrails to check")
            for i, guardrail in enumerate(guardrail_configs):
                logger.warning(f"🔍 CHECKING GUARDRAIL {i+1}: {guardrail.get('name', 'unnamed')} (type: {guardrail.get('type', 'unknown')}, enabled: {guardrail.get('enabled', True)})")
                if not guardrail.get("enabled", True):
                    logger.warning(f"🔍 SKIPPING GUARDRAIL {i+1}: disabled")
                    continue

                result = await self._run_guardrail(guardrail, content, user, endpoint, content_type, context)
                logger.warning(f"🔍 GUARDRAIL {i+1} RESULT: passed={result.passed}, violation_type={result.violation_type}")
                if not result.passed:
                    # First violation triggers action
                    logger.warning(f"Database guardrail violation detected: {result.violation_type} - {result.description}")
                    return result

            # All guardrails passed
            logger.debug(f"Content passed all guardrails for {endpoint}")
            return GuardrailResult(
                passed=True,
                original_content=content,
                action=GuardrailAction.LOG
            )

        except Exception as e:
            logger.error(f"Guardrails validation error: {e} - allowing content through")
            return GuardrailResult(
                passed=True,
                original_content=content,
                description=f"Guardrail service error: {str(e)}",
                action=GuardrailAction.LOG
            )

    async def _validate_with_portkey(
        self,
        content: str,
        user: User,
        endpoint: str,
        content_type: ContentType
    ) -> Optional[GuardrailResult]:
        """Validate content using Portkey AI Gateway"""

        if not self.session:
            self.session = aiohttp.ClientSession()

        try:
            # Portkey guardrails API payload
            payload = {
                "text": content,
                "user_id": str(user.id),
                "space_id": str(getattr(user, '_effective_space_id', None) or getattr(user, 'current_space_id', None) or user.space_id),
                "endpoint": endpoint,
                "content_type": content_type.value,
                "guardrails": {
                    "pii_detection": True,
                    "prompt_injection": True,
                    "content_filter": True,
                    "toxic_detection": True
                }
            }

            # Call Portkey guardrails endpoint
            async with self.session.post(
                f"{self.portkey_url}/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer fake-key-for-guardrails"
                },
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": content}],
                    "guardrails": payload["guardrails"]
                },
                timeout=5.0
            ) as response:

                # If Portkey blocks the request, it returns specific error codes
                if response.status == 400:
                    result_data = await response.json()

                    # Parse Portkey violation response
                    if "guardrail" in str(result_data).lower():
                        return GuardrailResult(
                            passed=False,
                            violation_type=ViolationType.PII_DETECTED,  # Default
                            severity="high",
                            description=f"Portkey AI blocked content: {result_data.get('error', 'Security violation')}",
                            original_content=content,
                            action=GuardrailAction.DENY,
                            portkey_response=result_data
                        )

                # If request succeeds, content is safe
                elif response.status == 200:
                    return GuardrailResult(
                        passed=True,
                        original_content=content,
                        description="Portkey AI validation passed",
                        action=GuardrailAction.LOG
                    )

        except asyncio.TimeoutError:
            logger.warning("Portkey validation timeout - falling back to local patterns")
            return None
        except Exception as e:
            logger.warning(f"Portkey validation error: {e} - falling back to local patterns")
            return None

        return None  # Let local patterns handle it

    async def _run_guardrail(
        self,
        guardrail: Dict[str, Any],
        content: str,
        user: User,
        endpoint: str,
        content_type: ContentType,
        context: Optional[Dict[str, Any]] = None
    ) -> GuardrailResult:
        """Run a specific guardrail check"""

        guardrail_type = guardrail.get("type", "unknown")
        guardrail_name = guardrail.get("name", "unnamed")

        try:
            if guardrail_type == "prompt_injection":
                return await self._check_prompt_injection(guardrail, content, user, endpoint)
            elif guardrail_type == "pii_detection":
                return await self._check_pii_detection(guardrail, content, user, endpoint)
            elif guardrail_type == "content_filter":
                return await self._check_content_filter(guardrail, content, user, endpoint)
            elif guardrail_type == "regex":
                return await self._check_regex_patterns(guardrail, content, user, endpoint)
            else:
                logger.warning(f"Unknown guardrail type: {guardrail_type}")
                return GuardrailResult(passed=True, original_content=content)

        except Exception as e:
            logger.error(f"Error running guardrail {guardrail_name}: {e}")
            return GuardrailResult(passed=True, original_content=content)

    async def _check_prompt_injection(
        self, guardrail: Dict[str, Any], content: str, user: User, endpoint: str
    ) -> GuardrailResult:
        """Check for prompt injection attacks"""

        logger.warning(f"🔍 PROMPT INJECTION CHECK: '{content[:50]}...' for user {user.username}")
        logger.warning(f"🔍 GUARDRAIL CONFIG: {guardrail}")
        logger.warning(f"🔍 ACTION TYPE: {guardrail.get('action', 'not_set')}")
        logger.warning(f"🔍 FULL CONTENT TO CHECK: '{content}'")

        # Essential prompt injection patterns - significantly simplified
        injection_patterns = [
            # Only the most critical instruction bypasses
            r"ignore\s+(all\s+)?(previous|your)\s+(instructions?|prompts?|rules?)",
            r"forget\s+(everything|all|previous)\s+(instructions?|prompts?|rules?)",
            r"system\s*:\s*you\s+are\s+now",
            r"new\s+instructions?\s*:\s*you\s+are",

            # Critical command injection only
            r";\s*(rm|sudo|chmod)\s+",
            r"\|\s*(rm|sudo|chmod)\s+",
            r"&&\s*(rm|sudo|chmod)\s+",
        ]

        import re

        content_lower = content.lower()
        logger.warning(f"🔍 PATTERN MATCHING: Testing '{content_lower[:50]}...' against {len(injection_patterns)} patterns")

        for i, pattern in enumerate(injection_patterns):
            logger.warning(f"🔍 TESTING PATTERN {i+1}/{len(injection_patterns)}: '{pattern}'")
            match = re.search(pattern, content_lower, re.IGNORECASE | re.MULTILINE)
            if match:
                logger.warning(f"🚨 PATTERN MATCHED! Pattern '{pattern}' found match: '{match.group()}'")
                action = GuardrailAction.DENY if guardrail.get("action") == "deny" else GuardrailAction.LOG
                severity = guardrail.get("severity_threshold", "high")

                logger.warning(f"🔍 VIOLATION ACTION: {action.value}, SEVERITY: {severity}")

                violation_response = {
                    "guardrail": guardrail.get("name", "prompt_injection_detector"),
                    "pattern_matched": pattern,
                    "action": action.value
                }

                result = await self._handle_guardrail_violation(
                    violation_response, content, user, endpoint, ContentType.INPUT, action.value
                )
                logger.warning(f"🔍 VIOLATION RESULT: passed={result.passed}, action={result.action}")
                return result
            else:
                logger.warning(f"🔍 PATTERN {i+1} NO MATCH: '{pattern}'")

        logger.warning(f"🔍 ALL PATTERNS CHECKED - NO MATCHES FOUND")
        return GuardrailResult(passed=True, original_content=content)

    async def _check_pii_detection(
        self, guardrail: Dict[str, Any], content: str, user: User, endpoint: str
    ) -> GuardrailResult:
        """Check for PII (Personally Identifiable Information)"""

        # Significantly relaxed PII patterns - only truly critical data
        pii_patterns = {
            "ssn": r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b",  # Only explicit SSN format with separators
        }

        import re

        detected_pii = []
        pii_types = guardrail.get("pii_types", ["ssn"])

        for pii_type in pii_types:
            if pii_type in pii_patterns:
                matches = re.findall(pii_patterns[pii_type], content)
                if matches:
                    detected_pii.extend([(pii_type, match) for match in matches])

        if detected_pii:
            action = GuardrailAction.SANITIZE if guardrail.get("action") == "sanitize" else GuardrailAction.LOG

            # Create sanitized content
            sanitized_content = content
            for pii_type, match in detected_pii:
                sanitized_content = sanitized_content.replace(match, f"[{pii_type.upper()}_REDACTED]")

            violation_response = {
                "guardrail": guardrail.get("name", "pii_detector"),
                "detected_pii": detected_pii,
                "action": action.value,
                "sanitized_content": sanitized_content
            }

            return await self._handle_guardrail_violation(
                violation_response, content, user, endpoint, ContentType.INPUT, action.value
            )

        return GuardrailResult(passed=True, original_content=content)

    async def _check_content_filter(
        self, guardrail: Dict[str, Any], content: str, user: User, endpoint: str
    ) -> GuardrailResult:
        """Check for toxic/harmful content"""

        # Very minimal toxic content patterns - only extreme cases
        toxic_patterns = [
            # Only truly threatening content
            r"\b(kill|murder)\s+(you|them|him|her)\b",
            r"\bi\s+will\s+(kill|murder|hurt)\s+you\b",
        ]

        import re

        content_lower = content.lower()
        categories = guardrail.get("categories", ["hate", "harassment", "violence", "sexual"])

        for pattern in toxic_patterns:
            if re.search(pattern, content_lower, re.IGNORECASE):
                action = GuardrailAction.DENY if guardrail.get("action") == "deny" else GuardrailAction.LOG

                violation_response = {
                    "guardrail": guardrail.get("name", "content_filter"),
                    "pattern_matched": pattern,
                    "categories": categories,
                    "action": action.value
                }

                return await self._handle_guardrail_violation(
                    violation_response, content, user, endpoint, ContentType.INPUT, action.value
                )

        return GuardrailResult(passed=True, original_content=content)

    async def _check_regex_patterns(
        self, guardrail: Dict[str, Any], content: str, user: User, endpoint: str
    ) -> GuardrailResult:
        """Check content against custom regex patterns"""

        import re

        patterns = guardrail.get("patterns", [])
        for pattern in patterns:
            if re.search(pattern, content, re.IGNORECASE | re.MULTILINE):
                action = GuardrailAction.LOG  # Regex patterns typically log for analysis
                if guardrail.get("action") == "deny":
                    action = GuardrailAction.DENY

                # Check for cross-tenant risk specifically
                cross_tenant_risk = self._detect_cross_tenant_risk(content, {})

                violation_response = {
                    "guardrail": guardrail.get("name", "regex_detector"),
                    "pattern_matched": pattern,
                    "action": action.value,
                    "cross_tenant_risk": cross_tenant_risk
                }

                return await self._handle_guardrail_violation(
                    violation_response, content, user, endpoint, ContentType.INPUT, action.value
                )

        return GuardrailResult(passed=True, original_content=content)

    async def _handle_guardrail_violation(
        self,
        portkey_response: Dict[str, Any],
        content: str,
        user: User,
        endpoint: str,
        content_type: ContentType,
        action: str
    ) -> GuardrailResult:
        """Handle detected guardrail violation"""

        # Parse Portkey response to extract violation details
        violation_type = self._extract_violation_type(portkey_response)
        severity = self._extract_severity(portkey_response)
        description = self._extract_description(portkey_response)
        confidence_score = self._extract_confidence(portkey_response)
        sanitized_content = self._extract_sanitized_content(portkey_response)

        # Check for cross-tenant risks
        cross_tenant_risk = self._detect_cross_tenant_risk(content, portkey_response)

        # Determine action based on violation type and severity
        guardrail_action = self._determine_action(violation_type, severity, action)

        # Log violation to database
        await self._log_violation(
            user=user,
            endpoint=endpoint,
            content_type=content_type,
            violation_type=violation_type,
            severity=severity,
            description=description,
            original_content=content,
            sanitized_content=sanitized_content,
            portkey_response=portkey_response,
            confidence_score=confidence_score,
            cross_tenant_risk=cross_tenant_risk
        )

        return GuardrailResult(
            passed=False,
            violation_type=violation_type,
            severity=severity,
            description=description,
            original_content=content,
            sanitized_content=sanitized_content,
            confidence_score=confidence_score,
            portkey_response=portkey_response,
            action=guardrail_action,
            cross_tenant_risk=cross_tenant_risk
        )

    def _extract_violation_type(self, response: Dict[str, Any]) -> ViolationType:
        """Extract violation type from Portkey response"""
        # Parse Portkey response format to determine violation type
        if "prompt_injection" in str(response).lower():
            return ViolationType.PROMPT_INJECTION
        elif "pii" in str(response).lower():
            return ViolationType.PII_DETECTED
        elif "toxic" in str(response).lower() or "harassment" in str(response).lower():
            return ViolationType.TOXIC_CONTENT
        elif "regex" in str(response).lower():
            return ViolationType.REGEX_VIOLATION
        else:
            return ViolationType.CONTENT_FILTER

    def _extract_severity(self, response: Dict[str, Any]) -> str:
        """Extract severity from Portkey response"""
        # Default to medium, extract from response if available
        return response.get("severity", "medium")

    def _extract_description(self, response: Dict[str, Any]) -> str:
        """Extract description from Portkey response"""
        return response.get("message", "Guardrail violation detected")

    def _extract_confidence(self, response: Dict[str, Any]) -> Optional[int]:
        """Extract confidence score from Portkey response"""
        return response.get("confidence", None)

    def _extract_sanitized_content(self, response: Dict[str, Any]) -> Optional[str]:
        """Extract sanitized content from Portkey response"""
        return response.get("sanitized_content", None)

    def _detect_cross_tenant_risk(self, content: str, response: Dict[str, Any]) -> bool:
        """Detect potential cross-tenant security risks"""
        # Check for patterns that might indicate cross-tenant data access attempts
        risk_patterns = [
            r"user[_-]?id[:\s]*[a-f0-9-]{36}",
            r"org[_-]?id[:\s]*[a-f0-9-]{36}",
            r"tenant[_-]?id[:\s]*\w+",
            r"switch[_\s]+to[_\s]+user",
            r"impersonate[_\s]+user",
            r"access[_\s]+as[_\s]+admin"
        ]

        import re
        for pattern in risk_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return True

        return False

    def _determine_action(self, violation_type: ViolationType, severity: str, portkey_action: str) -> GuardrailAction:
        """Determine what action to take based on violation"""

        # Critical violations always deny
        if severity == "critical":
            return GuardrailAction.DENY

        # High severity prompt injections deny
        if violation_type == ViolationType.PROMPT_INJECTION and severity == "high":
            return GuardrailAction.DENY

        # PII can be sanitized
        if violation_type == ViolationType.PII_DETECTED:
            return GuardrailAction.SANITIZE

        # Follow Portkey recommendation for others
        if portkey_action == "deny":
            return GuardrailAction.DENY
        else:
            return GuardrailAction.LOG

    async def _log_violation(
        self,
        user: User,
        endpoint: str,
        content_type: ContentType,
        violation_type: ViolationType,
        severity: str,
        description: str,
        original_content: str,
        sanitized_content: Optional[str],
        portkey_response: Dict[str, Any],
        confidence_score: Optional[int],
        cross_tenant_risk: bool
    ):
        """Log violation to database"""
        try:
            from ..core.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                violation = GuardrailViolation(
                    space_id=getattr(user, '_effective_space_id', None) or getattr(user, 'current_space_id', None) or user.space_id,  # Use effective org context
                    user_id=user.id,
                    violation_type=violation_type.value,
                    severity=severity,
                    description=description,
                    endpoint=endpoint,
                    method="POST",  # Most violations will be from POST requests
                    content_type=content_type.value,
                    original_content=original_content,
                    sanitized_content=sanitized_content,
                    portkey_response=portkey_response,
                    portkey_status_code=246 if severity in ["low", "medium"] else 446,
                    confidence_score=confidence_score,
                    cross_tenant_risk=cross_tenant_risk,
                    tenant_isolation_level="organization"
                )

                db.add(violation)

                # Increment user's violations_count
                from sqlalchemy import update
                await db.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(violations_count=User.violations_count + 1)
                )

                await db.commit()

                # Log to application logs as well
                log_level = logging.WARNING if severity in ["high", "critical"] else logging.INFO
                logger.log(
                    log_level,
                    f"Guardrail violation: {violation_type.value} | User: {user.username} | "
                    f"Endpoint: {endpoint} | Severity: {severity} | Cross-tenant: {cross_tenant_risk}"
                )

        except Exception as e:
            logger.error(f"Failed to log guardrail violation: {e}")


# Singleton instance
guardrails_service = PortkeyGuardrailsService()
