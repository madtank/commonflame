"""Add GitHub SSO fields to users table

Revision ID: 016_github_sso
Revises: a9361136f5c1
Create Date: 2025-08-06

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '016_github_sso'
down_revision = 'a9361136f5c1'
branch_labels = None
depends_on = None


def upgrade():
    """Add GitHub SSO fields to users table"""

    # Add GitHub-specific fields
    op.add_column('users', sa.Column('github_id', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('github_username', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('github_avatar_url', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('auth_provider', sa.String(50), nullable=False, server_default='local'))

    # Make password_hash nullable for GitHub-only users
    op.alter_column('users', 'password_hash',
                    existing_type=sa.String(255),
                    nullable=True)

    # Create unique index on github_id
    op.create_index('ix_users_github_id', 'users', ['github_id'], unique=True, postgresql_where=sa.text('github_id IS NOT NULL'))

    # Update existing users to have 'local' auth_provider
    op.execute("UPDATE users SET auth_provider = 'local' WHERE auth_provider IS NULL")

    print("✅ GitHub SSO fields added to users table")
    print("   - github_id: Unique GitHub user ID")
    print("   - github_username: GitHub username")
    print("   - github_avatar_url: Profile picture from GitHub")
    print("   - auth_provider: 'github' or 'local'")
    print("   - password_hash: Now nullable for GitHub users")


def downgrade():
    """Remove GitHub SSO fields from users table"""

    # Drop the unique index first
    op.drop_index('ix_users_github_id', table_name='users')

    # Make password_hash required again (set a default for existing GitHub users)
    op.execute("UPDATE users SET password_hash = 'github_sso_user' WHERE password_hash IS NULL")
    op.alter_column('users', 'password_hash',
                    existing_type=sa.String(255),
                    nullable=False)

    # Remove GitHub fields
    op.drop_column('users', 'auth_provider')
    op.drop_column('users', 'github_avatar_url')
    op.drop_column('users', 'github_username')
    op.drop_column('users', 'github_id')

    print("⚠️  GitHub SSO fields removed from users table")
