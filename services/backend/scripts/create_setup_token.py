"""Issue one expiring owner capability into a private operator file."""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
import uuid

from sqlalchemy import select, text, update

from app.api.v1.local_auth import has_builtin_accounts
from app.core.database import AsyncSessionLocal
from app.core.security import hash_token
from app.models.account_invite import AccountInvite


async def issue_setup_token(output: Path, expires_in_minutes: int = 60):
    # Do not print the capability or put it in a URL/argument. O_EXCL prevents
    # clobbering another file; symlinks are never followed.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        value = "setup_" + secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT set_config('app.is_privileged', 'true', true)"))
            await db.execute(text("SELECT pg_advisory_xact_lock(840220261003)"))
            if await has_builtin_accounts(db):
                raise ValueError("Owner setup is already complete")
            # Rotation invalidates older outstanding setup capabilities.
            await db.execute(update(AccountInvite).where(
                AccountInvite.kind == "owner_setup", AccountInvite.consumed_at.is_(None)
            ).values(consumed_at=now))
            db.add(AccountInvite(id=uuid.uuid4(), token_hash=hash_token(value), kind="owner_setup",
                                 expires_at=now + timedelta(minutes=expires_in_minutes)))
            with os.fdopen(descriptor, "w") as stream:
                descriptor = None
                stream.write(value + "\n")
            await db.commit()
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        output.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/run/keys/owner-setup.token"))
    parser.add_argument("--expires-in-minutes", type=int, choices=range(5, 121), default=60)
    args = parser.parse_args()
    try:
        asyncio.run(issue_setup_token(args.output, args.expires_in_minutes))
    except (ValueError, FileExistsError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"Owner setup capability saved privately to {args.output}; expires in {args.expires_in_minutes} minutes")


if __name__ == "__main__":
    main()
