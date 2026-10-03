"""
Guardrails Middleware
FastAPI middleware to integrate Portkey guardrails with existing endpoints
"""
import json
import time
from typing import Callable, Dict, Any, Optional
from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import logging

from ..services.portkey_guardrails import guardrails_service, GuardrailAction
from ..services.portkey_config import portkey_config_service, EndpointCategory
from ..models.user import User

logger = logging.getLogger(__name__)


class GuardrailsMiddleware(BaseHTTPMiddleware):
    """Middleware to apply Portkey guardrails to API requests and responses"""

    def __init__(self, app, enabled: bool = True):
        super().__init__(app)
        self.enabled = enabled
        logger.warning(f"🛡️ GUARDRAILS MIDDLEWARE LOADED: enabled={enabled}")

        # Endpoints that require input validation
        # NOTE: MCP endpoints are excluded because they handle their own guardrails
        # to return proper JSON-RPC error responses instead of HTTP errors
        self.input_endpoints = {
            "/auth/messages": EndpointCategory.MESSAGE_INPUT,
            # "/mcp/messages": EndpointCategory.MESSAGE_INPUT,  # REMOVED: MCP handles own guardrails
            "/api/tasks": EndpointCategory.TASK_INPUT,
            "/api/tasks/.*?/notes": EndpointCategory.TASK_INPUT,  # Task notes creation/updates
            # "/mcp/register_agent": EndpointCategory.AGENT_REGISTRATION,  # REMOVED: MCP handles own guardrails
        }


        # Endpoints that require output validation
        # NOTE: MCP endpoints are excluded for same reason as input validation
        self.output_endpoints = {
            "/auth/messages": EndpointCategory.MESSAGE_OUTPUT,
            # "/mcp/messages": EndpointCategory.MESSAGE_OUTPUT,  # REMOVED: MCP handles own guardrails
            # "/mcp/": EndpointCategory.MCP_OUTPUT,  # REMOVED: MCP handles own guardrails
            # "/sse": EndpointCategory.MCP_OUTPUT,  # REMOVED: MCP handles own guardrails
        }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request through guardrails if applicable"""

        logger.info(f"🛡️ GUARDRAILS DISPATCH: {request.method} {request.url.path}")

        if not self.enabled:
            return await call_next(request)

        start_time = time.time()

        try:
            # Check if this endpoint needs input validation
            needs_input_validation = self._needs_input_validation(request)

            if needs_input_validation:
                # Validate input before processing
                validation_result = await self._validate_input(request)

                if not validation_result["passed"]:
                    # Return appropriate response based on guardrail action
                    violation_response = await self._handle_input_violation(validation_result)
                    if violation_response is not None:
                        return violation_response

                # Modify request if content was sanitized
                if validation_result.get("sanitized_content"):
                    request = await self._update_request_content(request, validation_result["sanitized_content"])

            # Process the request
            response = await call_next(request)

            # Check if this endpoint needs output validation
            needs_output_validation = self._needs_output_validation(request)

            if needs_output_validation and hasattr(response, 'body'):
                # Validate output before returning
                response = await self._validate_output(request, response)

            # Add guardrails performance headers
            processing_time = time.time() - start_time
            response.headers["X-Guardrails-Time"] = str(round(processing_time * 1000, 2))
            response.headers["X-Guardrails-Enabled"] = "true"

            return response

        except Exception as e:
            logger.error(f"Guardrails middleware error: {e}")
            # Don't block requests due to guardrails failures
            response = await call_next(request)
            response.headers["X-Guardrails-Error"] = "true"
            return response

    def _needs_input_validation(self, request: Request) -> bool:
        """Check if request needs input validation"""
        if request.method not in ["POST", "PUT", "PATCH"]:
            return False

        path = request.url.path
        import re

        # Check against endpoint patterns (some may be regex)
        for endpoint_pattern in self.input_endpoints.keys():
            if endpoint_pattern.startswith("/") and ".*?" in endpoint_pattern:
                # This is a regex pattern
                if re.match(endpoint_pattern, path):
                    return True
            else:
                # This is a simple string match
                if endpoint_pattern in path:
                    return True

        return False

    def _needs_output_validation(self, request: Request) -> bool:
        """Check if response needs output validation"""
        path = request.url.path
        return any(endpoint in path for endpoint in self.output_endpoints.keys())

    async def _validate_input(self, request: Request) -> Dict[str, Any]:
        """Validate request input content"""
        logger.warning(f"🔍 VALIDATE_INPUT CALLED: {request.method} {request.url.path}")
        try:
            # Get request body
            body = await request.body()
            logger.warning(f"🔍 BODY LENGTH: {len(body) if body else 0} bytes")
            if not body:
                logger.warning("🔍 NO BODY - RETURNING PASSED")
                return {"passed": True}

            # Parse JSON body
            try:
                data = json.loads(body)
                logger.warning(f"🔍 JSON PARSED SUCCESSFULLY: {list(data.keys())}")
                logger.warning(f"🔍 FULL DATA STRUCTURE: {data}")
            except json.JSONDecodeError as e:
                logger.warning(f"🔍 JSON PARSE ERROR: {e}")
                return {"passed": True}  # Non-JSON requests pass through

            # Extract content to validate
            content = self._extract_content_from_request(data, request.url.path)
            logger.warning(f"🔍 CONTENT EXTRACTED: '{content[:30]}...' (length: {len(content)})")
            if not content:
                logger.warning("🔍 NO CONTENT - RETURNING PASSED")
                return {"passed": True}

            # Get user context early for emergency logging
            user_context = await self._get_user_context(request)
            logger.warning(f"🔍 USER CONTEXT CHECK: User context = {user_context is not None}")
            if user_context:
                logger.warning(f"🔍 USER CONTEXT DETAILS: username={user_context.get('username')}, role={user_context.get('role')}")

            # EMERGENCY BLOCKING - Hard-coded patterns as failsafe
            emergency_block = self._emergency_pii_check(content)
            if emergency_block:
                logger.warning(f"🚨 EMERGENCY PII BLOCKING: {emergency_block['violation_type']} detected")

                # CRITICAL FIX: Log emergency violations to database
                if user_context:
                    await self._log_emergency_violation(
                        user=user_context["user"],
                        endpoint=request.url.path,
                        violation_type=emergency_block["violation_type"],
                        description=f"Emergency PII blocking: {emergency_block['description']}",
                        original_content=content
                    )

                return {
                    "passed": False,
                    "violation_type": emergency_block["violation_type"],
                    "severity": "critical",
                    "description": f"Emergency PII blocking: {emergency_block['description']}",
                    "action": "deny",
                    "sanitized_content": None,
                    "cross_tenant_risk": False
                }

            # EMERGENCY PROMPT INJECTION BLOCKING - Hard-coded patterns as failsafe
            emergency_injection_block = self._emergency_prompt_injection_check(content)
            if emergency_injection_block:
                logger.warning(f"🚨 EMERGENCY PROMPT INJECTION BLOCKING: {emergency_injection_block['violation_type']} detected")

                # CRITICAL FIX: Log emergency violations to database
                if user_context:
                    await self._log_emergency_violation(
                        user=user_context["user"],
                        endpoint=request.url.path,
                        violation_type=emergency_injection_block["violation_type"],
                        description=f"Emergency prompt injection blocking: {emergency_injection_block['description']}",
                        original_content=content
                    )

                return {
                    "passed": False,
                    "violation_type": emergency_injection_block["violation_type"],
                    "severity": "critical",
                    "description": f"Emergency prompt injection blocking: {emergency_injection_block['description']}",
                    "action": "deny",
                    "sanitized_content": None,
                    "cross_tenant_risk": False
                }
            else:
                logger.warning(f"🔍 EMERGENCY CHECK PASSED: '{content[:30]}...' - proceeding to main guardrails")

            if not user_context:
                logger.warning("⚠️ GUARDRAILS: Skipping validation - no user context")
                return {"passed": True}  # No user context, skip validation

            # Determine endpoint category
            category = portkey_config_service.get_endpoint_category(request.url.path, request.method)

            # Validate through Portkey
            async with guardrails_service:
                result = await guardrails_service.validate_input(
                    content=content,
                    user=user_context["user"],
                    endpoint=request.url.path,
                    context={
                        "method": request.method,
                        "category": category.value,
                        "ip_address": request.client.host if request.client else None,
                        "user_agent": request.headers.get("user-agent")
                    }
                )

            return {
                "passed": result.passed,
                "violation_type": result.violation_type.value if result.violation_type else None,
                "severity": result.severity,
                "description": result.description,
                "action": result.action.value,
                "sanitized_content": result.sanitized_content,
                "cross_tenant_risk": result.cross_tenant_risk
            }

        except Exception as e:
            logger.error(f"Input validation error: {e}")
            # On validation errors, do emergency PII check as fallback
            try:
                body = await request.body()
                if body:
                    data = json.loads(body)
                    content = self._extract_content_from_request(data, request.url.path)
                    emergency_block = self._emergency_pii_check(content)
                    if emergency_block:
                        logger.warning(f"🚨 FALLBACK EMERGENCY PII BLOCKING: {emergency_block['violation_type']}")
                        return {
                            "passed": False,
                            "violation_type": emergency_block["violation_type"],
                            "severity": "critical",
                            "description": f"Fallback PII blocking: {emergency_block['description']}",
                            "action": "deny",
                            "sanitized_content": None,
                            "cross_tenant_risk": False
                        }

                    # Fallback emergency prompt injection check
                    emergency_injection_block = self._emergency_prompt_injection_check(content)
                    if emergency_injection_block:
                        logger.warning(f"🚨 FALLBACK EMERGENCY PROMPT INJECTION BLOCKING: {emergency_injection_block['violation_type']}")
                        return {
                            "passed": False,
                            "violation_type": emergency_injection_block["violation_type"],
                            "severity": "critical",
                            "description": f"Fallback prompt injection blocking: {emergency_injection_block['description']}",
                            "action": "deny",
                            "sanitized_content": None,
                            "cross_tenant_risk": False
                        }
            except:
                pass
            return {"passed": True}  # Allow through on validation errors

    async def _validate_output(self, request: Request, response: Response) -> Response:
        """Validate response output content"""
        try:
            # Only validate JSON responses
            if not response.headers.get("content-type", "").startswith("application/json"):
                return response

            # Get response body
            body = b""
            async for chunk in response.body_iterator:
                body += chunk

            if not body:
                return response

            # Parse response data
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return response

            # Extract content to validate
            content = self._extract_content_from_response(data)
            if not content:
                return response

            # Get user context
            user_context = await self._get_user_context(request)
            if not user_context:
                return response

            # Determine endpoint category
            category = portkey_config_service.get_endpoint_category(request.url.path, "GET")

            # Validate through Portkey
            async with guardrails_service:
                result = await guardrails_service.validate_output(
                    content=content,
                    user=user_context["user"],
                    endpoint=request.url.path,
                    context={
                        "method": request.method,
                        "category": category.value,
                        "response_type": "api_response"
                    }
                )

            # Handle output violations
            if not result.passed:
                if result.action == GuardrailAction.DENY:
                    # Replace response with error
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": "Response blocked by guardrails",
                            "type": "guardrail_violation",
                            "violation_type": result.violation_type.value if result.violation_type else "content_filter"
                        }
                    )
                elif result.action == GuardrailAction.SANITIZE and result.sanitized_content:
                    # Replace content with sanitized version
                    sanitized_data = self._update_response_content(data, result.sanitized_content)
                    return JSONResponse(
                        status_code=response.status_code,
                        content=sanitized_data,
                        headers=dict(response.headers)
                    )

            # Return original response
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers)
            )

        except Exception as e:
            logger.error(f"Output validation error: {e}")
            return response  # Return original response on validation errors

    def _extract_content_from_request(self, data: Dict[str, Any], path: str) -> str:
        """Extract content to validate from request data"""
        import re

        if "/auth/messages" in path:
            return data.get("content", "")
        elif "/mcp/messages" in path:
            # MCP uses JSON-RPC format: params.arguments.content
            params = data.get("params", {})
            arguments = params.get("arguments", {})
            return arguments.get("content", "")
        elif re.match(r"/api/tasks/[^/]+/notes", path):
            # Task notes endpoint - extract note content
            note_content = data.get("note", "")
            note_type = data.get("note_type", "")
            return f"{note_type}: {note_content}".strip() if note_type else note_content
        elif "/api/tasks" in path and "/notes" not in path:
            # Regular task creation/update - combine title and description
            title = data.get("title", "")
            description = data.get("description", "")
            return f"{title}\n{description}".strip()
        elif "/mcp/register_agent" in path:
            # Combine agent name and bio
            username = data.get("username", "")
            bio = data.get("bio", "")
            return f"{username}\n{bio}".strip()
        else:
            # Generic content extraction
            return str(data.get("content", data.get("text", data.get("message", ""))))

    def _extract_content_from_response(self, data: Dict[str, Any]) -> str:
        """Extract content to validate from response data"""
        if isinstance(data, dict):
            # Try common content fields
            for field in ["content", "message", "text", "description", "response"]:
                if field in data and isinstance(data[field], str):
                    return data[field]

            # Check for nested content in arrays
            if "posts" in data and isinstance(data["posts"], list):
                contents = []
                for post in data["posts"]:
                    if isinstance(post, dict) and "content" in post:
                        contents.append(post["content"])
                return "\n".join(contents)

            if "messages" in data and isinstance(data["messages"], list):
                contents = []
                for msg in data["messages"]:
                    if isinstance(msg, dict) and "content" in msg:
                        contents.append(msg["content"])
                return "\n".join(contents)

        return ""

    async def _get_user_context(self, request: Request) -> Dict[str, Any]:
        """Extract user context from request for guardrails validation"""
        try:
            # Debug: Log all headers for MCP requests
            if "/mcp/" in request.url.path:
                logger.warning(f"🔍 MCP REQUEST HEADERS: {dict(request.headers)}")

            # Extract JWT token from Authorization header
            auth_header = request.headers.get("authorization")
            logger.warning(f"🔍 AUTH HEADER: {auth_header[:50]}..." if auth_header else "🔍 AUTH HEADER: None")
            if not auth_header or not auth_header.startswith("Bearer "):
                logger.warning("🔍 NO VALID AUTH HEADER FOUND")
                return None

            token = auth_header[7:]  # Remove "Bearer " prefix

            # Verify JWT token using the existing security system
            from ..core.security import verify_token
            from ..core.database import AsyncSessionLocal
            from ..models.user import User
            from sqlalchemy import select

            token_payload = verify_token(token)
            logger.warning(f"🔍 JWT VERIFICATION: payload={token_payload is not None}, type={getattr(token_payload, 'type', None) if token_payload else None}")
            if not token_payload or token_payload.type not in ["access", "agent"]:
                if token_payload is None:
                    logger.debug("🔍 JWT VERIFICATION: token invalid or not a JWT (opaque token or expired)")
                else:
                    logger.warning(f"🔍 JWT VERIFICATION FAILED: unexpected token type '{token_payload.type}' (expected 'access' or 'agent')")
                return None

            # Get user from database
            async with AsyncSessionLocal() as db:
                logger.warning(f"🔍 DATABASE LOOKUP: user_id={token_payload.sub}, token_version={token_payload.token_version}")
                result = await db.execute(
                    select(User)
                    .where(User.id == token_payload.sub)
                    .where(User.token_version == token_payload.token_version)
                    .where(User.active == True)
                )
                user = result.scalar_one_or_none()
                logger.warning(f"🔍 USER FOUND: {user is not None}, username={getattr(user, 'username', None) if user else None}")

                if not user:
                    logger.warning("🔍 NO USER FOUND IN DATABASE")
                    return None

                # Add the effective org context from JWT token as a runtime property
                # This ensures guardrails use the correct organization context
                user._effective_space_id = token_payload.space_id

                return {
                    "user": user,
                    "token_payload": token_payload,
                    "user_id": str(user.id),
                    "space_id": str(token_payload.space_id),  # Use effective space from token
                    "username": user.username,
                    "role": user.role
                }

        except Exception as e:
            logger.error(f"Error extracting user context: {e}")
            return None

    async def _handle_input_violation(self, validation_result: Dict[str, Any]) -> Optional[Response]:
        """Handle input validation violations with user-friendly messages"""
        action = validation_result.get("action", "log")
        violation_type = validation_result.get("violation_type", "content_filter")

        if action == "deny":
            # Create user-friendly messages based on violation type
            user_messages = {
                "pii_detected": {
                    "title": "🛡️ Sensitive Information Detected",
                    "message": "Your message contains sensitive information (SSN) that has been blocked for security reasons.",
                    "suggestion": "Please remove any Social Security Numbers and try again."
                },
                # Specific PII types (from emergency patterns)
                "ssn": {
                    "title": "🛡️ Personal Information Detected",
                    "message": "Your message contains a Social Security Number that has been blocked for security reasons.",
                    "suggestion": "Please remove any Social Security Numbers and try again."
                },
                "prompt_injection": {
                    "title": "⚠️ Security Alert",
                    "message": "Your message contains content that appears to be a security threat or system manipulation attempt.",
                    "suggestion": "Please rephrase your message and avoid commands or system instructions."
                },
                "toxic_content": {
                    "title": "🚫 Content Policy Violation",
                    "message": "Your message contains threatening content that violates our community guidelines.",
                    "suggestion": "Please keep your messages respectful and avoid threats."
                },
                "content_filter": {
                    "title": "🔒 Content Blocked",
                    "message": "Your message has been blocked by our security filters.",
                    "suggestion": "Please review your message and try again."
                }
            }

            user_msg = user_messages.get(violation_type, user_messages["content_filter"])

            return JSONResponse(
                status_code=400,
                content={
                    "error": "guardrail_violation",
                    "type": "security_block",
                    "violation_type": violation_type,
                    "title": user_msg["title"],
                    "message": user_msg["message"],
                    "suggestion": user_msg["suggestion"],
                    "severity": validation_result.get("severity", "medium"),
                    "timestamp": validation_result.get("timestamp"),
                    # Technical details for debugging (can be hidden from users)
                    "debug": {
                        "description": validation_result.get("description", "Security filter triggered"),
                        "original_error": "Request blocked by guardrails"
                    }
                }
            )
        elif action == "sanitize" and validation_result.get("sanitized_content"):
            # For sanitization, we should let the user know content was modified
            # But still process the request - add a header to indicate sanitization
            return None

        # For "log" action, return None to continue processing normally
        return None

    async def _update_request_content(self, request: Request, sanitized_content: str) -> Request:
        """Update request with sanitized content"""
        # This is complex to implement properly in middleware
        # For now, log the sanitization and continue with original content
        logger.info(f"Content sanitized for {request.url.path}: {sanitized_content[:100]}...")
        return request

    def _emergency_pii_check(self, content: str) -> Optional[Dict[str, str]]:
        """Emergency hard-coded PII patterns as failsafe - CRITICAL SECURITY"""
        import re

        # Only truly critical PII patterns - significantly relaxed
        critical_patterns = {
            "ssn": {
                "patterns": [
                    r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b",  # Only explicit SSN format XXX-XX-XXXX or XXX XX XXXX
                ],
                "description": "Social Security Number detected"
            }
        }

        content_lower = content.lower()

        for violation_type, pattern_info in critical_patterns.items():
            for pattern in pattern_info["patterns"]:
                if re.search(pattern, content, re.IGNORECASE):
                    # Additional validation for numbers to avoid false positives
                    if violation_type in ["ssn", "credit_card"]:
                        # Extract the matched number
                        matches = re.findall(pattern, content, re.IGNORECASE)
                        for match in matches:
                            # Remove formatting characters
                            clean_number = re.sub(r'[-\s]', '', match)

                            # SSN validation
                            if violation_type == "ssn" and len(clean_number) == 9:
                                # Avoid false positives for years, zip codes, etc.
                                if not clean_number.startswith(('19', '20', '000', '666')):
                                    return {
                                        "violation_type": violation_type,
                                        "description": pattern_info["description"],
                                        "matched_pattern": pattern
                                    }

                            # Credit card validation (basic length check)
                            elif violation_type == "credit_card" and 13 <= len(clean_number) <= 19:
                                return {
                                    "violation_type": violation_type,
                                    "description": pattern_info["description"],
                                    "matched_pattern": pattern
                                }

        return None

    def _emergency_prompt_injection_check(self, content: str) -> Optional[Dict[str, str]]:
        """Emergency hard-coded prompt injection patterns as failsafe - CRITICAL SECURITY"""
        import re

        # Essential prompt injection patterns - significantly relaxed
        critical_injection_patterns = [
            # Only the most critical instruction bypasses
            r"ignore\s+(all\s+)?(previous|your)\s+(instructions?|prompts?|rules?)",
            r"forget\s+(everything|all|previous)\s+(instructions?|prompts?|rules?)",
            r"system\s*:\s*you\s+are\s+now",
            r"new\s+instructions?\s*:\s*you\s+are",

            # Only truly dangerous commands
            r";\s*(rm|sudo|chmod)\s+[\-/]",
            r"\|\s*(rm|sudo|chmod)\s+[\-/]",
            r"&&\s*(rm|sudo|chmod)\s+[\-/]",
        ]

        content_lower = content.lower()

        for pattern in critical_injection_patterns:
            if re.search(pattern, content_lower, re.IGNORECASE | re.MULTILINE):
                return {
                    "violation_type": "prompt_injection",
                    "description": "Emergency prompt injection pattern detected",
                    "matched_pattern": pattern
                }

        return None

    def _update_response_content(self, data: Dict[str, Any], sanitized_content: str) -> Dict[str, Any]:
        """Update response data with sanitized content"""
        # Update the first content field found
        for field in ["content", "message", "text", "description", "response"]:
            if field in data:
                data[field] = sanitized_content
                break

        return data

    async def _log_emergency_violation(
        self,
        user: User,
        endpoint: str,
        violation_type: str,
        description: str,
        original_content: str
    ):
        """Log emergency violation to database for admin console visibility"""
        try:
            logger.warning(f"📝 LOGGING EMERGENCY VIOLATION: {violation_type} for user {user.username}")

            from ..core.database import AsyncSessionLocal
            from ..models.guardrail_violation import GuardrailViolation
            from datetime import datetime

            async with AsyncSessionLocal() as db:
                violation = GuardrailViolation(
                    space_id=user._effective_space_id,  # Use effective space context
                    user_id=user.id,
                    violation_type=violation_type,
                    severity="critical",
                    description=description,
                    endpoint=endpoint,
                    method="POST",
                    content_type="input",
                    original_content=original_content[:500],  # Truncate for storage
                    sanitized_content=None,
                    portkey_response={"emergency_block": True, "pattern_matched": True},
                    portkey_status_code=446,  # Critical violation status
                    confidence_score=100,  # Emergency patterns are 100% confident
                    cross_tenant_risk=False,
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

                logger.warning(f"✅ EMERGENCY VIOLATION LOGGED: {violation.id} - {violation_type}; User violations_count incremented")

        except Exception as e:
            logger.error(f"❌ FAILED TO LOG EMERGENCY VIOLATION: {e}")
            # Don't let logging failures affect security blocking


def create_guardrails_middleware(enabled: bool = True) -> GuardrailsMiddleware:
    """Factory function to create guardrails middleware"""
    return lambda app: GuardrailsMiddleware(app, enabled=enabled)
