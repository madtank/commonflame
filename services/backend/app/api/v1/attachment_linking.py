"""Helpers for safely linking uploaded attachments to sent messages."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import or_, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attachment import Attachment


def accepted_attachment_ids(
    accepted_attachments: Iterable[dict],
    *,
    logger: logging.Logger,
    message_id: UUID,
) -> list[UUID]:
    """Extract valid attachment UUIDs from the client metadata envelope."""
    attachment_ids: list[UUID] = []
    for attachment in accepted_attachments:
        attachment_id = attachment.get("id")
        if not attachment_id:
            continue
        try:
            attachment_ids.append(UUID(str(attachment_id)))
        except ValueError:
            logger.warning(
                "Invalid attachment id on message send: %s msg=%s",
                attachment_id,
                message_id,
            )
    return attachment_ids


async def link_user_uploads_to_message(
    db: AsyncSession,
    *,
    accepted_attachments: Iterable[dict],
    upload_owner_id: UUID | None,
    space_id: UUID,
    message_id: UUID,
    logger: logging.Logger,
) -> int:
    """Attach the caller's own unlinked uploads to a newly-created message.

    Attachment IDs are visible in shared spaces, so linking must not rely on
    id+space alone. Upload records are currently owned by the authenticated
    user backing the session, even when the effective principal is an agent, so
    callers must pass the upload owner's user id here.
    """
    attachment_ids = accepted_attachment_ids(
        accepted_attachments,
        logger=logger,
        message_id=message_id,
    )
    if not attachment_ids:
        return 0

    if upload_owner_id is None:
        logger.warning(
            "Skipping attachment link without upload owner: requested=%d "
            "space=%s msg=%s",
            len(attachment_ids),
            space_id,
            message_id,
        )
        return 0

    result = await db.execute(
        sa_update(Attachment)
        .where(
            Attachment.id.in_(attachment_ids),
            Attachment.space_id == space_id,
            Attachment.user_id == upload_owner_id,
            or_(Attachment.message_id.is_(None), Attachment.message_id == message_id),
        )
        .values(message_id=message_id)
    )
    updated = int(result.rowcount or 0)
    if updated != len(attachment_ids):
        logger.warning(
            "Attachment linking mismatch: requested %d, updated %d "
            "(possible cross-user, already-linked, cross-space, or missing IDs). "
            "upload_owner=%s space=%s msg=%s",
            len(attachment_ids),
            updated,
            upload_owner_id,
            space_id,
            message_id,
        )
    return updated
