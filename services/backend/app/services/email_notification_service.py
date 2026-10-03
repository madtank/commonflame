"""
Email Notification Service

Sends transactional email notifications via GCP-compatible providers.
Supports SendGrid (GCP Marketplace) and generic SMTP as fallback.

Architecture:
- Mention detection happens in messages_notifications.py (existing)
- This service is called when a user (not agent) is @mentioned
- Digest mode batches notifications to avoid spam
- Quiet hours are respected per user timezone
"""

import html
import os
import logging
import json
from datetime import datetime, time, timedelta, timezone
from typing import Optional, List, Dict, Any
from uuid import UUID

import httpx
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.notification_preferences import NotificationPreferences

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────

EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "sendgrid")  # sendgrid | smtp | log
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

FROM_EMAIL = os.getenv("NOTIFICATION_FROM_EMAIL", "notifications@paxai.app")
FROM_NAME = os.getenv("NOTIFICATION_FROM_NAME", "aX Platform")
PLATFORM_URL = os.getenv("FRONTEND_URL", "https://paxai.app")

# Redis key prefix for digest batching
DIGEST_KEY_PREFIX = "notify:digest:"
DIGEST_LOCK_PREFIX = "notify:lock:"


class EmailNotificationService:
    """
    Handles email delivery for user mention notifications.

    Usage:
        service = EmailNotificationService(db_session, redis_client)
        await service.notify_user_mention(
            user_id=uuid,
            mentioned_by="agent_name",
            message_content="Hey @operator, PR is ready",
            space_name="The operator's Workspace",
            space_id=uuid,
        )
    """

    def __init__(self, db: AsyncSession, redis=None):
        self.db = db
        self.redis = redis

    async def notify_user_mention(
        self,
        user_id: UUID,
        mentioned_by: str,
        message_content: str,
        space_name: str = "",
        space_id: Optional[UUID] = None,
        message_id: Optional[UUID] = None,
    ) -> bool:
        """
        Send an email notification when a user is @mentioned.

        Returns True if email was sent/queued, False if skipped.
        """
        # 1. Get user + preferences
        user = await self._get_user(user_id)
        if not user or not user.email:
            logger.debug(f"No email for user {user_id}, skipping notification")
            return False

        prefs = await self._get_or_create_preferences(user_id)

        # 2. Check if notifications are enabled
        if not prefs.email_enabled or not prefs.email_on_mention:
            logger.debug(f"Email notifications disabled for user {user_id}")
            return False

        # 3. Check quiet hours
        if self._is_quiet_hours(prefs):
            logger.debug(f"Quiet hours active for user {user_id}, skipping")
            return False

        # 4. Check rate limit
        if not await self._check_rate_limit(prefs):
            logger.warning(f"Rate limit reached for user {user_id}")
            return False

        # 5. Handle digest mode
        if prefs.digest_mode != "immediate":
            await self._queue_for_digest(
                user_id=user_id,
                mentioned_by=mentioned_by,
                message_content=message_content,
                space_name=space_name,
                message_id=message_id,
            )
            return True

        # 6. Send immediately
        preview = html.escape(message_content[:200]) + ("..." if len(message_content) > 200 else "")
        # Subject is plain text — don't HTML-escape (would produce &#39; entities)
        subject = f"@{mentioned_by} mentioned you in {space_name or 'aX'}"

        html_body = self._build_mention_email(
            user_name=user.username or user.full_name or "there",
            mentioned_by=mentioned_by,
            message_preview=preview,
            space_name=space_name,
            message_id=str(message_id) if message_id else None,
        )

        success = await self._send_email(
            to_email=user.email,
            to_name=user.full_name or user.username or "",
            subject=subject,
            html_body=html_body,
        )

        if success:
            await self._increment_rate_limit(prefs)

        return success

    async def send_digest(self, user_id: UUID) -> bool:
        """
        Flush queued notifications as a single digest email.
        Called by a scheduled task or the digest timer.
        """
        if not self.redis:
            return False

        key = f"{DIGEST_KEY_PREFIX}{user_id}"
        lock_key = f"{DIGEST_LOCK_PREFIX}{user_id}"

        # Acquire lock to prevent duplicate digests
        acquired = await self.redis.set(lock_key, "1", nx=True, ex=30)
        if not acquired:
            return False

        try:
            raw_items = await self.redis.lrange(key, 0, -1)
            if not raw_items:
                return False

            items = [json.loads(item) for item in raw_items]

            user = await self._get_user(user_id)
            if not user or not user.email:
                return False

            prefs = await self._get_or_create_preferences(user_id)
            if not prefs.email_enabled:
                return False

            subject = f"You have {len(items)} new mention{'s' if len(items) > 1 else ''} in aX"
            html_body = self._build_digest_email(
                user_name=user.username or user.full_name or "there",
                items=items,
            )

            success = await self._send_email(
                to_email=user.email,
                to_name=user.full_name or user.username or "",
                subject=subject,
                html_body=html_body,
            )

            if success:
                await self._increment_rate_limit(prefs)
                await self.redis.delete(key)  # Only delete after successful send

            return success
        finally:
            await self.redis.delete(lock_key)

    # ── Private helpers ──────────────────────────────────────────────

    async def _get_user(self, user_id: UUID) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def _get_or_create_preferences(self, user_id: UUID) -> NotificationPreferences:
        result = await self.db.execute(
            select(NotificationPreferences).where(
                NotificationPreferences.user_id == user_id
            )
        )
        prefs = result.scalar_one_or_none()
        if not prefs:
            prefs = NotificationPreferences(user_id=user_id)
            self.db.add(prefs)
            await self.db.flush()
        return prefs

    def _is_quiet_hours(self, prefs: NotificationPreferences) -> bool:
        if not prefs.quiet_hours_enabled:
            return False

        try:
            import pytz

            tz = pytz.timezone(prefs.timezone or "America/Los_Angeles")
            now = datetime.now(tz).time()
            start = prefs.quiet_hours_start or time(23, 0)
            end = prefs.quiet_hours_end or time(8, 0)

            # Handle overnight range (e.g., 23:00 → 08:00)
            if start > end:
                return now >= start or now <= end
            else:
                return start <= now <= end
        except Exception:
            return False

    async def _check_rate_limit(self, prefs: NotificationPreferences) -> bool:
        now = datetime.now(timezone.utc)
        if prefs.hour_reset_at and prefs.hour_reset_at > now:
            return prefs.emails_sent_this_hour < prefs.max_emails_per_hour
        # Reset the counter
        prefs.emails_sent_this_hour = 0
        prefs.hour_reset_at = now + timedelta(hours=1)
        await self.db.flush()
        return True

    async def _increment_rate_limit(self, prefs: NotificationPreferences):
        prefs.emails_sent_this_hour += 1
        await self.db.flush()

    async def _queue_for_digest(self, **kwargs):
        if not self.redis:
            logger.warning("Redis not available for digest queuing")
            return

        key = f"{DIGEST_KEY_PREFIX}{kwargs['user_id']}"
        await self.redis.rpush(key, json.dumps({
            "mentioned_by": kwargs["mentioned_by"],
            "message_content": kwargs["message_content"][:200],
            "space_name": kwargs.get("space_name", ""),
            "message_id": str(kwargs.get("message_id", "")),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
        # Auto-expire digest queue after 24h (safety net)
        await self.redis.expire(key, 86400)

    async def _send_email(
        self,
        to_email: str,
        to_name: str,
        subject: str,
        html_body: str,
    ) -> bool:
        """Send email via configured provider."""
        if EMAIL_PROVIDER == "log":
            logger.info(f"📧 [LOG MODE] To: {to_email}, Subject: {subject}")
            return True

        if EMAIL_PROVIDER == "sendgrid":
            return await self._send_via_sendgrid(to_email, to_name, subject, html_body)

        if EMAIL_PROVIDER == "smtp":
            return await self._send_via_smtp(to_email, to_name, subject, html_body)

        logger.error(f"Unknown email provider: {EMAIL_PROVIDER}")
        return False

    async def _send_via_sendgrid(
        self, to_email: str, to_name: str, subject: str, html_body: str
    ) -> bool:
        if not SENDGRID_API_KEY:
            logger.error("SENDGRID_API_KEY not configured")
            return False

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    headers={
                        "Authorization": f"Bearer {SENDGRID_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "personalizations": [
                            {
                                "to": [{"email": to_email, "name": to_name}],
                                "subject": subject,
                            }
                        ],
                        "from": {"email": FROM_EMAIL, "name": FROM_NAME},
                        "content": [{"type": "text/html", "value": html_body}],
                    },
                    timeout=10.0,
                )

                if response.status_code in (200, 201, 202):
                    logger.info(f"📧 Email sent to {to_email}: {subject}")
                    return True
                else:
                    logger.error(
                        f"SendGrid error {response.status_code}: {response.text}"
                    )
                    return False
        except Exception as e:
            logger.error(f"SendGrid send failed: {e}")
            return False

    async def _send_via_smtp(
        self, to_email: str, to_name: str, subject: str, html_body: str
    ) -> bool:
        """SMTP fallback (synchronous, wrapped in executor)."""
        import asyncio
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        def _send():
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
            msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                if SMTP_USER:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)

        try:
            await asyncio.get_event_loop().run_in_executor(None, _send)
            logger.info(f"📧 Email sent via SMTP to {to_email}: {subject}")
            return True
        except Exception as e:
            logger.error(f"SMTP send failed: {e}")
            return False

    # ── Email templates ──────────────────────────────────────────────

    def _build_mention_email(
        self,
        user_name: str,
        mentioned_by: str,
        message_preview: str,
        space_name: str,
        message_id: Optional[str] = None,
    ) -> str:
        # Escape all user-controlled strings to prevent HTML injection
        user_name = html.escape(user_name)
        mentioned_by = html.escape(mentioned_by)
        message_preview = html.escape(message_preview)
        space_name = html.escape(space_name) if space_name else ""
        view_url = f"{PLATFORM_URL}/spaces"
        if message_id:
            view_url = f"{PLATFORM_URL}/messages/{message_id}"

        return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:0; background:#f5f5f5; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5; padding:24px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:8px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,0.1);">
          <!-- Header -->
          <tr>
            <td style="background:#1a1a2e; padding:20px 32px;">
              <span style="color:#ffffff; font-size:18px; font-weight:600;">aX Platform</span>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding:32px;">
              <p style="margin:0 0 16px; color:#333; font-size:15px;">
                Hi @{user_name},
              </p>
              <p style="margin:0 0 20px; color:#333; font-size:15px;">
                <strong>@{mentioned_by}</strong> mentioned you{f' in <strong>{space_name}</strong>' if space_name else ''}:
              </p>
              <!-- Message preview -->
              <div style="background:#f8f9fa; border-left:3px solid #6366f1; padding:16px 20px; border-radius:0 6px 6px 0; margin:0 0 24px;">
                <p style="margin:0; color:#555; font-size:14px; line-height:1.5; white-space:pre-wrap;">{message_preview}</p>
              </div>
              <a href="{view_url}" style="display:inline-block; background:#6366f1; color:#ffffff; text-decoration:none; padding:10px 24px; border-radius:6px; font-size:14px; font-weight:500;">
                View in aX
              </a>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="padding:20px 32px; border-top:1px solid #eee;">
              <p style="margin:0; color:#999; font-size:12px;">
                You received this because you were @mentioned on <a href="{PLATFORM_URL}" style="color:#6366f1; text-decoration:none;">paxai.app</a>.
                <a href="{PLATFORM_URL}/settings/notifications" style="color:#6366f1; text-decoration:none;">Manage preferences</a>
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    def _build_digest_email(self, user_name: str, items: List[Dict[str, Any]]) -> str:
        user_name = html.escape(user_name)
        items = [{k: html.escape(str(v)) if isinstance(v, str) else v for k, v in item.items()} for item in items]
        mentions_html = ""
        for item in items[:20]:  # Cap at 20 items
            mentions_html += f"""
            <div style="background:#f8f9fa; border-left:3px solid #6366f1; padding:12px 16px; border-radius:0 6px 6px 0; margin:0 0 12px;">
              <p style="margin:0 0 4px; color:#333; font-size:13px; font-weight:600;">@{item['mentioned_by']}{f" in {item['space_name']}" if item.get('space_name') else ''}</p>
              <p style="margin:0; color:#555; font-size:13px; line-height:1.4;">{item['message_content']}</p>
            </div>"""

        return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:0; background:#f5f5f5; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f5; padding:24px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:8px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,0.1);">
          <tr>
            <td style="background:#1a1a2e; padding:20px 32px;">
              <span style="color:#ffffff; font-size:18px; font-weight:600;">aX Platform</span>
            </td>
          </tr>
          <tr>
            <td style="padding:32px;">
              <p style="margin:0 0 16px; color:#333; font-size:15px;">
                Hi @{user_name}, you have {len(items)} new mention{'s' if len(items) > 1 else ''}:
              </p>
              {mentions_html}
              <a href="{PLATFORM_URL}/spaces" style="display:inline-block; background:#6366f1; color:#ffffff; text-decoration:none; padding:10px 24px; border-radius:6px; font-size:14px; font-weight:500; margin-top:12px;">
                View in aX
              </a>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 32px; border-top:1px solid #eee;">
              <p style="margin:0; color:#999; font-size:12px;">
                <a href="{PLATFORM_URL}/settings/notifications" style="color:#6366f1; text-decoration:none;">Manage notification preferences</a>
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
