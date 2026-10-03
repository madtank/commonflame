"""Best-effort SES admin notifications: waitlist access requests, demo requests,
and signup/dormant-return alerts. Never raises — failures are logged.

Design: docs/plans/2026-05-28-invite-only-waitlist-gate-design.md
"""

import html
import logging
import threading

from .config import get_settings

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()


def _get_ses_client():
    """Lazy singleton boto3 SES client (mirrors bedrock_embed pattern)."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        import boto3
        from botocore.config import Config as BotoConfig

        region = get_settings().ses_region
        _client = boto3.client(
            "ses",
            region_name=region,
            # Auth-path callers await these sends; keep worst-case latency tight
            # (boto3 defaults are 60s connect + 60s read with retries).
            config=BotoConfig(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}),
        )
        logger.info("SES client initialised (region=%s)", region)
    return _client


def _build_notification_html(
    *,
    requester_email: str,
    full_name: str | None,
    github_username: str | None,
    approve_url: str,
) -> str:
    """Build the HTML notification body, escaping all user-derived values.

    FINDING 3: ``full_name``, ``requester_email`` and ``github_username`` are
    user-controlled and must be HTML-escaped to prevent injection. The
    ``approve_url`` is internally generated and is the only trusted markup.
    """
    name = html.escape(full_name or "(no name)")
    email = html.escape(requester_email)
    handle = html.escape(github_username or "(no github)")

    return (
        "<p>A new person signed in and is on the aX waitlist:</p>"
        f"<ul><li><b>Name:</b> {name}</li>"
        f"<li><b>Email:</b> {email}</li>"
        f"<li><b>GitHub:</b> {handle}</li></ul>"
        f'<p><a href="{approve_url}">✅ Approve access</a></p>'
    )


def _send_admin_email(
    *, subject: str, body_text: str, body_html: str, log_key: str, requester_email: str
) -> bool:
    """Send a best-effort SES email to the waitlist/admin recipient.

    Request-access and request-demo notifications intentionally share the same
    sender, recipient, SES region, lazy boto3 client, and failure semantics.
    """

    settings = get_settings()
    try:
        client = _get_ses_client()
        response = client.send_email(
            Source=settings.ses_sender_email,
            Destination={"ToAddresses": [settings.waitlist_notify_email]},
            Message={
                "Subject": {"Data": subject},
                "Body": {
                    "Text": {"Data": body_text},
                    "Html": {"Data": body_html},
                },
            },
        )
        logger.info(
            "%s_ACCEPTED requester=%s message_id=%s",
            log_key.removesuffix("_FAILED"),
            requester_email,
            response.get("MessageId", ""),
        )
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort, never raise
        logger.warning(
            "%s requester=%s error=%s: %s",
            log_key,
            requester_email,
            type(exc).__name__,
            exc,
        )
        return False


def send_waitlist_notification(
    *,
    requester_email: str,
    full_name: str | None,
    github_username: str | None,
    approve_url: str,
) -> bool:
    """Email the operator about a new access request with a one-click approve link.

    Returns True on success, False on any failure. Never raises.
    """
    name = full_name or "(no name)"
    handle = github_username or "(no github)"

    subject = f"aX access request: {name} ({requester_email})"
    body_text = (
        "A new person signed in and is on the aX waitlist:\n\n"
        f"  Name:   {name}\n"
        f"  Email:  {requester_email}\n"
        f"  GitHub: {handle}\n\n"
        f"Approve them with one click:\n{approve_url}\n"
    )
    body_html = _build_notification_html(
        requester_email=requester_email,
        full_name=full_name,
        github_username=github_username,
        approve_url=approve_url,
    )

    return _send_admin_email(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        log_key="WAITLIST_SES_SEND_FAILED",
        requester_email=requester_email,
    )


def _build_request_demo_html(
    *,
    requester_email: str,
    full_name: str | None,
    company: str | None,
    role: str | None,
    interest: str,
) -> str:
    """Build an escaped HTML notification body for public demo requests."""

    name = html.escape(full_name or "(no name)")
    email = html.escape(requester_email)
    company_text = html.escape(company or "(not provided)")
    role_text = html.escape(role or "(not provided)")
    interest_text = html.escape(interest).replace("\n", "<br>")

    return (
        "<p>A new person requested an aX demo:</p>"
        f"<ul><li><b>Name:</b> {name}</li>"
        f"<li><b>Email:</b> {email}</li>"
        f"<li><b>Company:</b> {company_text}</li>"
        f"<li><b>Role:</b> {role_text}</li></ul>"
        f"<p><b>Interest:</b><br>{interest_text}</p>"
    )


def send_request_demo_notification(
    *,
    requester_email: str,
    full_name: str | None,
    company: str | None,
    role: str | None,
    interest: str,
) -> bool:
    """Email the operator about a public request-demo contact/interest submission.

    Returns True on success, False on any failure. Never raises.
    """

    name = full_name or "(no name)"
    company_text = company or "(not provided)"
    role_text = role or "(not provided)"

    subject = f"aX demo request: {name} ({requester_email})"
    body_text = (
        "A new person requested an aX demo:\n\n"
        f"  Name:     {name}\n"
        f"  Email:    {requester_email}\n"
        f"  Company:  {company_text}\n"
        f"  Role:     {role_text}\n\n"
        f"Interest:\n{interest}\n"
    )
    body_html = _build_request_demo_html(
        requester_email=requester_email,
        full_name=full_name,
        company=company,
        role=role,
        interest=interest,
    )

    return _send_admin_email(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        log_key="REQUEST_DEMO_SES_SEND_FAILED",
        requester_email=requester_email,
    )


def send_signup_alert_email(
    *,
    username: str | None,
    email: str | None,
    github_username: str | None,
    auth_provider: str | None,
) -> bool:
    """Email the operator about a brand-new self-service signup. Never raises."""
    who = username or "(no username)"
    addr = email or "(no email)"
    handle = github_username or "(no github)"
    provider = auth_provider or "(unknown)"

    subject = f"aX new signup: {who} ({addr})"
    body_text = (
        "A new account was just created on paxai.app:\n\n"
        f"  Username: {who}\n"
        f"  Email:    {addr}\n"
        f"  GitHub:   {handle}\n"
        f"  Provider: {provider}\n\n"
        "Review users in the admin console: https://paxai.app/admin\n"
    )
    body_html = (
        "<p>A new account was just created on paxai.app:</p>"
        f"<ul><li><b>Username:</b> {html.escape(who)}</li>"
        f"<li><b>Email:</b> {html.escape(addr)}</li>"
        f"<li><b>GitHub:</b> {html.escape(handle)}</li>"
        f"<li><b>Provider:</b> {html.escape(provider)}</li></ul>"
        '<p><a href="https://paxai.app/admin">Open admin console</a></p>'
    )
    return _send_admin_email(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        log_key="SIGNUP_ALERT_SES_SEND_FAILED",
        requester_email=addr,
    )


def send_dormant_return_email(
    *,
    username: str | None,
    email: str | None,
    last_seen: str,
    dormant_days: int,
) -> bool:
    """Email the operator when a user dormant for >= dormant_days signs in again."""
    who = username or "(no username)"
    addr = email or "(no email)"

    subject = f"aX dormant user returned: {who}"
    body_text = (
        f"{who} ({addr}) just signed in after being inactive for at least "
        f"{dormant_days} days.\n\n"
        f"  Last seen: {last_seen}\n\n"
        "Activity report: https://paxai.app/admin\n"
    )
    body_html = (
        f"<p><b>{html.escape(who)}</b> ({html.escape(addr)}) just signed in after "
        f"being inactive for at least {dormant_days} days.</p>"
        f"<p>Last seen: {html.escape(last_seen)}</p>"
        '<p><a href="https://paxai.app/admin">Open activity report</a></p>'
    )
    return _send_admin_email(
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        log_key="DORMANT_RETURN_SES_SEND_FAILED",
        requester_email=addr,
    )
