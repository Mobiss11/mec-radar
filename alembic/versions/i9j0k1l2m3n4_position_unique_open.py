"""Add partial unique index on positions (token_id, is_paper) WHERE status='open'.

Prevents duplicate open paper positions from concurrent workers.

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-02-20 12:30:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "i9j0k1l2m3n4"
down_revision = "h8i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_positions_open_paper",
        "positions",
        ["token_id", "is_paper"],
        unique=True,
        postgresql_where="status = 'open'",
    )


def downgrade() -> None:
    op.drop_index("uq_positions_open_paper", table_name="positions")
