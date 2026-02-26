"""Add is_micro_entry column to positions for Phase 51 micro-snipe.

Tracks micro-snipe positions that were opened at PRE_SCAN (T+5s)
before full scoring. Flag cleared to 0 on top-up at INITIAL/MIN_2.

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

revision = "n4o5p6q7r8s9"
down_revision = "m3n4o5p6q7r8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "positions",
        sa.Column("is_micro_entry", sa.Integer(), server_default="0", nullable=True),
    )


def downgrade() -> None:
    op.drop_column("positions", "is_micro_entry")
