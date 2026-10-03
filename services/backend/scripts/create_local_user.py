"""Create a local user and private workspace with an interactive password."""
import argparse
import asyncio
import getpass
import re
import sys
import uuid

from sqlalchemy import select, text
from app.api.v1.local_auth import password_hasher
from app.core.database import AsyncSessionLocal
from app.models.space import Space
from app.models.space_membership import SpaceMembership
from app.models.user import User


async def create_user(username: str, password: str, full_name: str | None = None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,49}", username):
        raise ValueError("Username must be 3-50 letters, numbers, dots, dashes or underscores")
    if len(password) < 12 or len(password) > 512:
        raise ValueError("Password must contain 12-512 characters")
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.is_privileged', 'true', true)"))
        exists = await db.execute(select(User).where(User.username == username))
        if exists.scalar_one_or_none():
            raise ValueError("Username already exists; existing users are never overwritten")
        user_id, space_id = uuid.uuid4(), uuid.uuid4()
        space = Space(id=space_id, name=f"{username}'s Workspace",
                      slug=f"{username.lower()}-{str(space_id)[:8]}", visibility="private")
        db.add(space)
        await db.flush()
        user = User(id=user_id, space_id=space_id, current_space_id=space_id,
                    username=username, email=f"{username}@waystation.local",
                    full_name=full_name or username, role="user", auth_provider="local",
                    password_hash=password_hasher.hash(password), active=True, token_version=0)
        db.add(user)
        await db.flush()
        space.created_by = user_id
        db.add(SpaceMembership(user_id=user_id, space_id=space_id, role="admin"))
        await db.commit()
        return username


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username")
    parser.add_argument("--full-name")
    parser.add_argument("--password-stdin", action="store_true",
                        help="Read password from stdin for local automation; never use a CLI password argument")
    args = parser.parse_args()
    username = args.username or input("Username: ").strip()
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Password (at least 12 characters): ")
        if password != getpass.getpass("Confirm password: "):
            raise SystemExit("Passwords do not match")
    try:
        asyncio.run(create_user(username, password, args.full_name))
    except ValueError as error:
        raise SystemExit(str(error))
    print(f"Created {username} with a private workspace. Sign in at /auth/login.")


if __name__ == "__main__":
    main()
