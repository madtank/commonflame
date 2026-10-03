"""
File Upload API endpoint
Handles file uploads for chat messages and agent context.
"""

import asyncio
import logging
import mimetypes
import os
import re
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.authorization import verify_space_actor_access
from ...core.database import get_db_session, set_rls_context
from ...core.jwt_verify import get_current_user_from_token
from ...core.rls import SecureSessionDep
from ...models.attachment import Attachment
from ...models.space import Space
from ...models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])

INLINE_SAFE_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "video/mp4",
    "video/webm",
    "application/pdf",
    "text/plain",
}

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
PUBLIC_SPACE_BLOCKED_TYPES = {
    "text/html",
    "application/xhtml+xml",
}
ALLOWED_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    # Audio: already inline-safe (INLINE_SAFE_CONTENT_TYPES) for playback; allow
    # the upload itself so agents can attach voice/audio clips end-to-end. Include
    # the MIME values mimetypes actually returns for extensionless/octet-stream
    # agent uploads: voice.wav -> audio/x-wav, voice.webm -> video/webm.
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "video/webm",
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/javascript",
    "text/typescript",
    "text/x-python",
    "text/x-script.python",
    "application/pdf",
    "application/json",
    "application/javascript",
    "application/x-python-code",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
    "application/x-zip-compressed",
}
# Archive types that only land in spaces where the membership implies trust.
# Rejected in public spaces so a stranger can't drop a blob on everyone.
TRUSTED_SPACE_ONLY_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
}
TRUSTED_SPACE_VISIBILITIES = {"private", "invite_only"}
# ZIP magic bytes — local file header, end-of-central-directory (empty archive),
# and spanned-archive marker. Blocks MIME spoofing via Content-Type.
ZIP_MAGIC_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
DANGEROUS_BUT_ALLOWED_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "text/javascript",
    "text/typescript",
    "application/javascript",
    "text/x-python",
    "text/x-script.python",
    "application/x-python-code",
}
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
BASE_URL = os.getenv("BASE_URL", os.getenv("FRONTEND_URL", ""))
_s3_client = None


class UploadObjectNotFound(Exception):
    """Raised when attachment metadata exists but the backing object is missing."""


def _raise_upload_unavailable() -> None:
    """Return a generic non-leaking status that survives CloudFront API proxying."""
    raise HTTPException(status_code=410, detail="File unavailable")


def detect_content_type(file: UploadFile, filename: str | None) -> str:
    """Resolve a usable content type for uploaded files."""
    content_type = (file.content_type or "").strip().lower()
    if content_type and content_type != "application/octet-stream":
        return content_type

    guessed_type, _ = mimetypes.guess_type(filename or "")
    return guessed_type or "application/octet-stream"


class UploadResponse(BaseModel):
    id: str  # Canonical ID — matches frontend SpaceAgentUploadResult.id
    attachment_id: str  # Alias for backward compat
    file_id: str
    url: str
    filename: str
    original_filename: str
    content_type: str
    size: int


def _safe_download_filename(filename: str | None) -> str:
    original = (filename or "upload").strip()
    if not original:
        original = "upload"
    safe = SAFE_FILENAME_RE.sub("_", original).strip("._")
    return safe or "upload"


def _principal_is_agent(user: User) -> bool:
    return getattr(user, "_principal_type", None) == "agent"


def _principal_agent_id(user: User) -> str | None:
    return getattr(user, "_agent_id", None) or getattr(user, "_principal_agent_id", None)


def _parse_upload_space_hint(space_id: str | None) -> uuid.UUID | None:
    if not space_id:
        return None
    try:
        return uuid.UUID(str(space_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid space_id") from exc


def _attachment_media_type(attachment: Attachment, filepath: Path) -> str:
    stored_content_type = (getattr(attachment, "content_type", "") or "").strip().lower()
    if stored_content_type:
        return stored_content_type

    guessed_type, _ = mimetypes.guess_type(filepath.name)
    return guessed_type or "application/octet-stream"


def _upload_storage_backend() -> str:
    return os.getenv("UPLOAD_STORAGE_BACKEND", "local").strip().lower()


def _upload_s3_bucket() -> str | None:
    bucket = os.getenv("UPLOAD_S3_BUCKET", "").strip()
    return bucket or None


def _is_trusted_space_visibility(space_visibility: str | None) -> bool:
    return (space_visibility or "private").lower() in TRUSTED_SPACE_VISIBILITIES


def _is_upload_content_type_allowed(resolved_content_type: str, *, space_visibility: str | None) -> bool:
    """Keep public uploads conservative while private spaces stay useful for rich artifacts."""
    if _is_trusted_space_visibility(space_visibility):
        return True
    if resolved_content_type in PUBLIC_SPACE_BLOCKED_TYPES:
        return False
    return resolved_content_type in ALLOWED_TYPES


async def _upload_space_visibility(db: AsyncSession, target_space_id: uuid.UUID) -> str:
    space = await db.get(Space, target_space_id)
    raw_visibility = getattr(space, "visibility", None)
    return raw_visibility.lower() if isinstance(raw_visibility, str) and raw_visibility else "private"


def _use_s3_upload_storage() -> bool:
    return _upload_storage_backend() == "s3"


def _require_upload_s3_bucket() -> str:
    bucket = _upload_s3_bucket()
    if not bucket:
        raise HTTPException(status_code=500, detail="UPLOAD_S3_BUCKET is required for S3 upload storage")
    return bucket


def _upload_s3_key(filename: str, *, space_id: uuid.UUID | str | None = None) -> str:
    prefix = os.getenv("UPLOAD_S3_PREFIX", "uploads").strip("/")
    parts = [part for part in (prefix, str(space_id) if space_id else None, filename) if part]
    return "/".join(parts)


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        import boto3

        _s3_client = boto3.client("s3", region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"))
    return _s3_client


async def _store_upload_bytes(
    filename: str,
    content: bytes,
    *,
    content_type: str,
    space_id: uuid.UUID | str | None = None,
) -> None:
    if _use_s3_upload_storage():
        put_kwargs = {
            "Bucket": _require_upload_s3_bucket(),
            "Key": _upload_s3_key(filename, space_id=space_id),
            "Body": content,
            "ContentType": content_type,
            "Metadata": {"space_id": str(space_id or "")},
        }
        sse = os.getenv("UPLOAD_S3_SERVER_SIDE_ENCRYPTION", "AES256").strip()
        if sse:
            put_kwargs["ServerSideEncryption"] = sse
        await asyncio.to_thread(_get_s3_client().put_object, **put_kwargs)
        return

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filepath = UPLOAD_DIR / filename
    filepath.write_bytes(content)


def _upload_response_headers(
    *,
    attachment: Attachment,
    resolved_media_type: str,
    download_name: str,
    content_length: int | None = None,
) -> dict[str, str]:
    disposition_type = "inline" if resolved_media_type in INLINE_SAFE_CONTENT_TYPES else "attachment"
    quoted_filename = quote(_safe_download_filename(attachment.filename or download_name))
    headers = {
        "Cache-Control": "private, max-age=3600",
        "Content-Disposition": f"{disposition_type}; filename*=UTF-8''{quoted_filename}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'",
        "Accept-Ranges": "bytes",
    }
    if content_length is None:
        content_length = getattr(attachment, "size_bytes", None)
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    return headers


def _parse_byte_range(range_header: str | None, *, size_bytes: int | None) -> tuple[int, int] | None:
    """Parse a single HTTP byte range.

    Browser media metadata probes use byte-range requests to make uploaded MP3s
    seekable and compute duration. Only a single satisfiable range is proxied to
    S3; malformed, multi-range, or unknown-size requests fall back to a normal
    200 response rather than exposing storage errors to callers.
    """
    if not range_header or size_bytes is None or size_bytes < 1:
        return None

    value = range_header.strip().lower()
    if not value.startswith("bytes="):
        return None
    spec = value.removeprefix("bytes=").strip()
    if "," in spec or "-" not in spec:
        return None

    start_text, end_text = spec.split("-", 1)
    try:
        if start_text == "":
            suffix_length = int(end_text)
            if suffix_length <= 0:
                return None
            start = max(size_bytes - suffix_length, 0)
            end = size_bytes - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size_bytes - 1
    except ValueError:
        return None

    if start < 0 or end < start or start >= size_bytes:
        return None
    return start, min(end, size_bytes - 1)


async def _s3_upload_response(
    *,
    attachment: Attachment,
    filename: str,
    resolved_media_type: str,
    range_header: str | None = None,
) -> StreamingResponse:
    total_size = getattr(attachment, "size_bytes", None)
    byte_range = _parse_byte_range(range_header, size_bytes=total_size)
    get_kwargs = {
        "Bucket": _require_upload_s3_bucket(),
        "Key": _upload_s3_key(filename, space_id=attachment.space_id),
    }
    if byte_range is not None:
        start, end = byte_range
        get_kwargs["Range"] = f"bytes={start}-{end}"

    try:
        obj = await asyncio.to_thread(_get_s3_client().get_object, **get_kwargs)
    except Exception as exc:
        error = getattr(exc, "response", {}).get("Error", {}) if hasattr(exc, "response") else {}
        if error.get("Code") in {"NoSuchKey", "NoSuchVersion", "404", "NotFound"}:
            logger.warning(
                "Upload metadata exists but S3 object is missing: attachment_id=%s storage_key=%s s3_error=%s",
                attachment.id,
                filename,
                error.get("Code"),
            )
            raise UploadObjectNotFound from exc
        logger.exception("Failed to read upload from S3: attachment_id=%s storage_key=%s", attachment.id, filename)
        raise HTTPException(status_code=502, detail="Failed to read upload storage") from exc

    body = obj["Body"]

    def iter_chunks():
        try:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            body.close()

    headers = _upload_response_headers(
        attachment=attachment,
        resolved_media_type=resolved_media_type,
        download_name=filename,
        content_length=(byte_range[1] - byte_range[0] + 1) if byte_range is not None else None,
    )
    status_code = 200
    if byte_range is not None and total_size is not None:
        start, end = byte_range
        headers["Content-Range"] = f"bytes {start}-{end}/{total_size}"
        status_code = status.HTTP_206_PARTIAL_CONTENT

    return StreamingResponse(
        iter_chunks(),
        media_type=resolved_media_type,
        headers=headers,
        status_code=status_code,
    )


def _local_upload_response(
    *,
    attachment: Attachment,
    filename: str,
    resolved_media_type: str,
) -> FileResponse:
    filepath = UPLOAD_DIR / filename
    if not filepath.exists():
        logger.warning(
            "Upload metadata exists but file is missing: attachment_id=%s space=%s storage_key=%s",
            attachment.id,
            attachment.space_id,
            filename,
        )
        raise UploadObjectNotFound

    return FileResponse(
        filepath,
        media_type=resolved_media_type,
        headers=_upload_response_headers(
            attachment=attachment,
            resolved_media_type=resolved_media_type,
            download_name=filepath.name,
        ),
    )


async def _resolve_upload_space_id(
    db: AsyncSession,
    *,
    user: User,
    requested_space_id: str | None,
    is_agent: bool | None = None,
    agent_id: str | None = None,
) -> uuid.UUID:
    raw_space_id = (
        requested_space_id
        or getattr(user, "_effective_space_id", None)
        or user.current_space_id
        or user.space_id
    )
    try:
        target_space_id = uuid.UUID(str(raw_space_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid or missing space_id") from exc

    await verify_space_actor_access(
        db,
        user_id=user.id,
        space_id=target_space_id,
        is_agent=_principal_is_agent(user) if is_agent is None else is_agent,
        agent_id=_principal_agent_id(user) if agent_id is None else agent_id,
    )
    return target_space_id


@router.post("/", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    space_id: str | None = Form(None),
    current_user: User = Depends(get_current_user_from_token),
    db: AsyncSession = Depends(get_db_session),
):
    """Upload a supported file and return attachment metadata."""
    target_space_id = await _resolve_upload_space_id(
        db,
        user=current_user,
        requested_space_id=space_id,
    )

    resolved_content_type = detect_content_type(file, file.filename)
    space_visibility = await _upload_space_visibility(db, target_space_id)
    if not _is_upload_content_type_allowed(resolved_content_type, space_visibility=space_visibility):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {resolved_content_type}",
        )
    if resolved_content_type in TRUSTED_SPACE_ONLY_TYPES:
        if space_visibility not in TRUSTED_SPACE_VISIBILITIES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=(
                    f"Archive uploads ({resolved_content_type}) are only allowed "
                    "in private or invite-only spaces."
                ),
            )
    if resolved_content_type in DANGEROUS_BUT_ALLOWED_TYPES:
        logger.warning(
            "Upload accepted potentially active file type: user=%s space=%s type=%s filename=%s",
            current_user.id,
            target_space_id,
            resolved_content_type,
            file.filename,
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File too large. Max size: {MAX_FILE_SIZE // (1024 * 1024)}MB",
        )
    if resolved_content_type in TRUSTED_SPACE_ONLY_TYPES and not content.startswith(ZIP_MAGIC_SIGNATURES):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File declared as zip but payload is not a valid zip archive.",
        )

    file_id = uuid.uuid4().hex
    original_filename = file.filename or "upload"
    ext = Path(original_filename).suffix.lower()
    if not ext:
        guessed_ext = mimetypes.guess_extension(resolved_content_type) or ""
        ext = guessed_ext.lower()
    filename = f"{file_id}{ext}"

    await _store_upload_bytes(
        filename,
        content,
        content_type=resolved_content_type,
        space_id=target_space_id,
    )

    attachment = Attachment(
        space_id=target_space_id,
        user_id=current_user.id,
        storage_key=filename,
        filename=original_filename,
        content_type=resolved_content_type,
        size_bytes=len(content),
    )
    db.add(attachment)
    await db.commit()
    await db.refresh(attachment)

    logger.info(
        "Upload: user=%s attachment_id=%s file_id=%s file=%s size=%s type=%s original_name=%s",
        current_user.id,
        attachment.id,
        file_id,
        filename,
        len(content),
        resolved_content_type,
        file.filename,
    )

    url = f"{BASE_URL}/api/v1/uploads/files/{filename}" if BASE_URL else f"/api/v1/uploads/files/{filename}"

    return UploadResponse(
        id=str(attachment.id),
        attachment_id=str(attachment.id),
        file_id=file_id,
        url=url,
        filename=filename,
        original_filename=original_filename,
        content_type=resolved_content_type,
        size=len(content),
    )


@router.get("/files/{filename}")
async def get_upload(filename: str, request: Request, session: SecureSessionDep, space_id: str | None = Query(None)):
    """Serve an uploaded file with safe headers.

    Requires authentication. Download URLs are intentionally opaque and do not
    carry space state, so the attachment metadata is resolved by storage key
    first. The caller is then authorized against the attachment's owning space
    before the file is returned. This keeps files shared through messages and
    context viewable after the browser's active space changes.
    """
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    requested_space_id = _parse_upload_space_hint(space_id)

    # Lookup must not depend on the caller's currently selected space. Use a
    # privileged read to locate the attachment row by storage key, then perform
    # an explicit membership/agent-access check against the owning space.
    await session.db.execute(text("SELECT set_config('app.is_privileged', 'true', true)"))
    try:
        result = await session.db.execute(select(Attachment).where(Attachment.storage_key == filename))
    finally:
        await session.db.execute(text("SELECT set_config('app.is_privileged', 'false', true)"))
    attachment = result.scalar_one_or_none()
    if attachment is None:
        _raise_upload_unavailable()

    attachment_space_id = uuid.UUID(str(attachment.space_id))
    if requested_space_id is not None and requested_space_id != attachment_space_id:
        # Return a generic unavailable response so callers with stale hints do
        # not learn whether the storage key exists in another space.
        _raise_upload_unavailable()

    try:
        await verify_space_actor_access(
            session.db,
            user_id=session.user.id,
            space_id=attachment_space_id,
            is_agent=session.is_agent,
            agent_id=session.agent_id,
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            _raise_upload_unavailable()
        raise
    await set_rls_context(
        session.db,
        user_id=str(session.user.id),
        space_id=str(attachment_space_id),
        agent_id=session.agent_id,
    )

    resolved_media_type = _attachment_media_type(attachment, Path(filename))
    if _use_s3_upload_storage():
        try:
            return await _s3_upload_response(
                attachment=attachment,
                filename=filename,
                resolved_media_type=resolved_media_type,
                range_header=request.headers.get("range"),
            )
        except UploadObjectNotFound:
            logger.warning(
                "S3 upload missing; falling back to local upload storage: attachment_id=%s storage_key=%s",
                attachment.id,
                filename,
            )
            try:
                return _local_upload_response(
                    attachment=attachment,
                    filename=filename,
                    resolved_media_type=resolved_media_type,
                )
            except UploadObjectNotFound:
                _raise_upload_unavailable()

    try:
        return _local_upload_response(
            attachment=attachment,
            filename=filename,
            resolved_media_type=resolved_media_type,
        )
    except UploadObjectNotFound:
        _raise_upload_unavailable()
