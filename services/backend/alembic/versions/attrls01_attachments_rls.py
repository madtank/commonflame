"""Enable RLS and space isolation policy on attachments table.

Revision ID: attrls01_attachments_rls
Revises: ("authspec02", "upl01_attachments")  # gitleaks:allow migration revision identifier, not a credential
Create Date: 2026-04-07

The attachments table was created in upl01_create_attachments_table without
RLS enabled, and was not included in rls01_reenable_rls's ALL_RLS_TABLES list.
This means cross-space attachment reads were not enforced at the database
level — authenticated users in Space B could query attachments belonging to
Space A by guessing the storage_key.

The fix is twofold:
  1. (application layer) The uploads.py download endpoint now filters by
     Attachment.space_id == session.space_id explicitly (defense-in-depth).
  2. (database layer) This migration enables RLS + adds a space isolation
     policy matching the pattern in rls01_reenable_rls.py.

After this migration, any SELECT/INSERT/UPDATE/DELETE on `attachments`
requires either:
  - app.current_space_id matches attachment.space_id, OR
  - app.is_privileged = 'true' (SystemSession / AdminSession bypass)

Note: the attachment-linking path in messages.py previously used a raw
AsyncSessionLocal() without setting RLS context. It has been updated in
the same PR to reuse the request's SecureSession (session.db), so the
RLS policy added here will not break legitimate attachment→message linking.

Migration graph note: this revision merges two previously-independent
heads into a single head. `down_revision` is a tuple so the migration
depends on BOTH:
  - `authspec02` — latest revision on the credential/auth chain
  - `upl01_attachments` — the migration that creates the attachments table
Without the tuple, `alembic upgrade heads` could run `attrls01` before
`upl01_attachments` on a fresh DB where the auth branch is further along
than the upload branch, and the `ALTER TABLE attachments ...` statements
would fail because the table does not yet exist. Caught during PR review
by @backend_sentinel.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "attrls01_attachments_rls"
# Tuple down_revision merges the auth chain head (authspec02) with the
# uploads chain head (upl01_attachments) so this migration is guaranteed
# to run only after BOTH predecessors have been applied. This also
# collapses the repo's two stray heads into one.
down_revision = ("authspec02", "upl01_attachments")  # gitleaks:allow migration revision identifier, not a credential
branch_labels = None
depends_on = None


# Matches the expressions used by rls01_reenable_rls.py — keep in sync.
_PRIV = "current_setting('app.is_privileged', true) = 'true'"
_SPACE = "space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid"


def upgrade():
    # Enable and force RLS on attachments.
    # FORCE ROW LEVEL SECURITY means even the table owner is subject to the
    # policy (except with is_privileged bypass), matching the pattern used
    # for every other space-scoped table.
    op.execute("ALTER TABLE attachments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE attachments FORCE ROW LEVEL SECURITY")

    # Drop any pre-existing policy with this name (defensive — should not exist)
    op.execute("DROP POLICY IF EXISTS attachments_space_isolation ON attachments")

    # Create the space isolation policy.
    op.execute(f"""
        CREATE POLICY attachments_space_isolation ON attachments
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)


def downgrade():
    op.execute("DROP POLICY IF EXISTS attachments_space_isolation ON attachments")
    op.execute("ALTER TABLE attachments DISABLE ROW LEVEL SECURITY")
