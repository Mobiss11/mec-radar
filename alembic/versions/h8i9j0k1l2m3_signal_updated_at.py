"""Add updated_at column to signals table.

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-02-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "h8i9j0k1l2m3"
down_revision = "g7h8i9j0k1l2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signals",
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Backfill: set updated_at = created_at for existing rows
    op.execute("UPDATE signals SET updated_at = created_at WHERE updated_at IS NULL")
    op.create_index("idx_signals_status_updated", "signals", ["status", "updated_at"])


def downgrade() -> None:
    op.drop_index("idx_signals_status_updated", table_name="signals")
    op.drop_column("signals", "updated_at")
