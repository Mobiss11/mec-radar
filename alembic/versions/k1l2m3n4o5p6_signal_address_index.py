"""Add index on signals.token_address for fast address-based lookups.

Used by signal deduplication check and external queries.

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-02-20
"""

from alembic import op

revision = "k1l2m3n4o5p6"
down_revision = "j0k1l2m3n4o5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("idx_signals_address", "signals", ["token_address"])


def downgrade() -> None:
    op.drop_index("idx_signals_address", table_name="signals")
