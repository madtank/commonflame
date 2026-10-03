"""
AI-Powered Outreach Service

SECURITY LEVEL: MAXIMUM
Requires: super_admin role + BETA_MODE enabled

This service provides AI-generated outreach content with strict guardrails
to prevent rogue AI behavior and ensure all generated content is reviewed
before being sent to users.
"""

import os
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
import json

logger = logging.getLogger(__name__)


class AIOutreachGuardrails:
    """
    Comprehensive guardrails for AI-generated outreach

    Security principles:
    1. Human-in-the-loop: All AI content requires explicit approval
    2. Content validation: Block spam, inappropriate, or harmful content
    3. Rate limiting: Prevent mass automated messaging
    4. Audit trail: Log all AI interactions
    5. Opt-out respect: Never contact users who opted out
    """

    # Blocked patterns (case-insensitive)
    BLOCKED_PATTERNS = [
        # Spam indicators
        "click here now", "limited time offer", "act fast",
        "guaranteed", "free money", "earn $$",
        # Inappropriate
        "urgent action required", "account suspended",
        # Pushy sales
        "buy now", "special discount expires",
    ]

    # Required disclaimers
    REQUIRED_DISCLAIMER = (
        "\n\n---\n"
        "This message was AI-assisted. "
        "To opt out of outreach, reply with 'STOP' or contact support."
    )

    # Maximum content length
    MAX_LENGTH = 1000

    # Maximum recipients per batch
    MAX_BATCH_SIZE = 10

    @classmethod
    def validate_content(cls, content: str) -> tuple[bool, Optional[str]]:
        """
        Validate AI-generated content against guardrails

        Returns:
            (is_valid, error_message)
        """
        if not content or len(content.strip()) == 0:
            return False, "Content cannot be empty"

        if len(content) > cls.MAX_LENGTH:
            return False, f"Content exceeds maximum length ({cls.MAX_LENGTH} chars)"

        content_lower = content.lower()

        # Check for blocked patterns
        for pattern in cls.BLOCKED_PATTERNS:
            if pattern.lower() in content_lower:
                return False, f"Content contains blocked pattern: '{pattern}'"

        # Ensure disclaimer is present
        if cls.REQUIRED_DISCLAIMER.strip() not in content:
            return False, "AI-generated content must include required disclaimer"

        return True, None

    @classmethod
    def add_disclaimer(cls, content: str) -> str:
        """Add required disclaimer to content"""
        if cls.REQUIRED_DISCLAIMER.strip() in content:
            return content
        return content + cls.REQUIRED_DISCLAIMER


class AIOutreachService:
    """
    AI-powered outreach service with Gemini integration

    Security features:
    - Requires super_admin role
    - Requires BETA_MODE=true
    - All content validated by guardrails
    - Audit trail for all operations
    - Human approval required before sending
    """

    def __init__(self):
        self.gemini_available = self._check_gemini_availability()
        self.guardrails = AIOutreachGuardrails()
        self.audit_log = []

    def _check_gemini_availability(self) -> bool:
        """Check if Gemini API is available"""
        try:
            # Check for GCP credentials
            if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
                logger.warning("Gemini AI not available: GOOGLE_APPLICATION_CREDENTIALS not set")
                return False

            # Try importing google.generativeai
            import google.generativeai as genai

            # Check for API key
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            if not api_key:
                logger.warning("Gemini AI not available: API key not configured")
                return False

            logger.info("✅ Gemini AI available for outreach")
            return True

        except ImportError:
            logger.warning("Gemini AI not available: google-generativeai not installed")
            return False
        except Exception as e:
            logger.error(f"Error checking Gemini availability: {e}")
            return False

    async def generate_outreach_email(
        self,
        user_data: Dict[str, Any],
        campaign: str,
        tone: str = "friendly",
        context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate personalized outreach email using AI

        Args:
            user_data: User information (username, activity, engagement metrics)
            campaign: Campaign type (activation, feature_launch, re_engagement)
            tone: Desired tone (friendly, professional, enthusiastic)
            context: Additional context for personalization

        Returns:
            Dict with generated content, metadata, and validation status
        """

        if not self.gemini_available:
            return {
                "success": False,
                "error": "AI service not available. Install google-generativeai and configure API key.",
                "fallback": self._get_template_email(user_data, campaign)
            }

        try:
            import google.generativeai as genai

            # Configure Gemini
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-pro')

            # Build prompt
            prompt = self._build_prompt(user_data, campaign, tone, context)

            # Generate content
            logger.info(f"Generating AI outreach for user {user_data.get('username')} (campaign: {campaign})")
            response = model.generate_content(prompt)

            if not response or not response.text:
                raise Exception("Gemini returned empty response")

            generated_content = response.text.strip()

            # Add disclaimer
            generated_content = self.guardrails.add_disclaimer(generated_content)

            # Validate content
            is_valid, error = self.guardrails.validate_content(generated_content)

            # Audit log
            audit_entry = {
                "timestamp": datetime.now().isoformat(),
                "user_id": user_data.get("id"),
                "campaign": campaign,
                "tone": tone,
                "content_length": len(generated_content),
                "validation_passed": is_valid,
                "validation_error": error
            }
            self.audit_log.append(audit_entry)

            result = {
                "success": True,
                "content": generated_content,
                "validation": {
                    "passed": is_valid,
                    "error": error
                },
                "metadata": {
                    "campaign": campaign,
                    "tone": tone,
                    "generated_at": datetime.now().isoformat(),
                    "word_count": len(generated_content.split()),
                    "char_count": len(generated_content)
                },
                "audit_id": len(self.audit_log) - 1
            }

            if not is_valid:
                result["warning"] = "Content failed validation and requires manual review"

            return result

        except Exception as e:
            logger.error(f"Error generating AI outreach: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "fallback": self._get_template_email(user_data, campaign)
            }

    def _build_prompt(
        self,
        user_data: Dict[str, Any],
        campaign: str,
        tone: str,
        context: Optional[str]
    ) -> str:
        """Build AI prompt for outreach generation"""

        username = user_data.get("username", "there")
        agent_count = user_data.get("agent_count", 0)
        message_count = user_data.get("message_count", 0)
        task_count = user_data.get("task_count", 0)
        days_since_creation = user_data.get("days_since_creation", 0)
        activity_status = user_data.get("activity_status", "unknown")

        # Campaign-specific instructions
        campaign_instructions = {
            "activation": (
                "The user signed up but hasn't fully engaged yet. "
                "Encourage them to create their first AI agent and explore the platform."
            ),
            "feature_launch": (
                "We're announcing exciting new features. "
                "Highlight how these features will benefit their workflow."
            ),
            "re_engagement": (
                "The user was active before but has been dormant. "
                "Welcome them back and share what's new since they left."
            ),
            "power_user": (
                "This is a highly engaged power user. "
                "Thank them for their contribution and ask for feedback or testimonial."
            )
        }

        instruction = campaign_instructions.get(
            campaign,
            "Write a friendly outreach email."
        )

        prompt = f"""
You are a helpful AI assistant writing a personalized outreach email for aX Platform.

USER INFORMATION:
- Username: {username}
- Days since signup: {days_since_creation}
- Activity status: {activity_status}
- Agents created: {agent_count}
- Messages sent: {message_count}
- Tasks created: {task_count}

CAMPAIGN: {campaign}
TONE: {tone}
INSTRUCTION: {instruction}

{"ADDITIONAL CONTEXT: " + context if context else ""}

REQUIREMENTS:
1. Keep it under 300 words
2. Be genuinely helpful and {tone}
3. Personalize based on the user's activity level
4. NO sales pressure or urgency tactics
5. NO spam patterns (like "click here now", "limited time", etc.)
6. Include a clear value proposition
7. End with a simple, low-pressure call to action
8. DO NOT include subject line (we'll add that separately)
9. Sign off with "The aX Team" only

Write the email now:
"""

        return prompt

    def _get_template_email(
        self,
        user_data: Dict[str, Any],
        campaign: str
    ) -> str:
        """Fallback template email when AI is not available"""

        username = user_data.get("username", "there")

        templates = {
            "activation": f"""
Hi {username},

Welcome to aX Platform! We noticed you signed up recently, and we wanted to reach out to see if you need any help getting started.

Creating your first AI agent takes just a few minutes, and it opens up a world of possibilities for automation and collaboration.

If you have any questions, feel free to reply to this email or check out our documentation.

Looking forward to seeing what you build!

The aX Team

---
To opt out of outreach, reply with 'STOP' or contact support.
""",
            "feature_launch": f"""
Hi {username},

We've just launched some exciting new features on aX Platform, and we think you'll love them!

Based on your activity, these updates should make your workflow even smoother and more powerful.

Check them out when you get a chance, and let us know what you think!

The aX Team

---
To opt out of outreach, reply with 'STOP' or contact support.
""",
            "re_engagement": f"""
Hi {username},

It's been a while since we've seen you on aX Platform! We wanted to reach out and let you know about some of the improvements we've made.

Your account is still active and ready whenever you want to jump back in.

We'd love to have you back!

The aX Team

---
To opt out of outreach, reply with 'STOP' or contact support.
"""
        }

        return templates.get(campaign, templates["activation"])

    async def generate_batch_outreach(
        self,
        users: List[Dict[str, Any]],
        campaign: str,
        tone: str = "friendly",
        context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate outreach content for multiple users

        Security: Limited to max batch size to prevent mass automated messaging
        """

        if len(users) > self.guardrails.MAX_BATCH_SIZE:
            return {
                "success": False,
                "error": f"Batch size exceeds maximum ({self.guardrails.MAX_BATCH_SIZE}). Process in smaller batches."
            }

        results = []
        for user in users:
            result = await self.generate_outreach_email(user, campaign, tone, context)
            results.append({
                "user_id": user.get("id"),
                "username": user.get("username"),
                **result
            })

        # Summary
        successful = sum(1 for r in results if r.get("success"))
        validated = sum(1 for r in results if r.get("validation", {}).get("passed"))

        return {
            "success": True,
            "batch_size": len(users),
            "successful": successful,
            "validated": validated,
            "results": results,
            "warning": "All AI-generated content requires human review before sending"
        }

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Get audit log of all AI operations"""
        return self.audit_log.copy()


# Singleton instance
_ai_outreach_service = None


def get_ai_outreach_service() -> AIOutreachService:
    """Get or create AI outreach service singleton"""
    global _ai_outreach_service
    if _ai_outreach_service is None:
        _ai_outreach_service = AIOutreachService()
    return _ai_outreach_service
