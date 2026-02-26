"""Add source and copied_from_wallet to positions and trades for copy trading.

Phase 57: Copy Trading Engine — track which positions came from signal vs copy trading,
and which wallet was being copied. Also updates unique constraint to include source.

Revision ID: q7r8s9t0u1v2
Revises: o5p6q7r8s9t0
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

revision = "q7r8s9t0u1v2"
down_revision = "o5p6q7r8s9t0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add source and copied_from_wallet to positions
    op.add_column("positions", sa.Column("source", sa.String(50), nullable=True))
    op.add_column("positions", sa.Column("copied_from_wallet", sa.String(64), nullable=True))

    # Add source and copied_from_wallet to trades
    op.add_column("trades", sa.Column("source", sa.String(50), nullable=True))
    op.add_column("trades", sa.Column("copied_from_wallet", sa.String(64), nullable=True))

    # Backfill existing rows as 'signal' source
    op.execute("UPDATE positions SET source = 'signal' WHERE source IS NULL")
    op.execute("UPDATE trades SET source = 'signal' WHERE source IS NULL")

    # Drop old unique constraint (token_id, is_paper) WHERE status='open'
    op.drop_index("uq_positions_open_paper", table_name="positions")

    # Recreate with source included — allows both signal and copy_trade
    # positions for the same token
    op.create_index(
        "uq_positions_open_paper",
        "positions",
        ["token_id", "is_paper", "source"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    # Index for copy trade position queries
    op.create_index(
        "idx_positions_source_open",
        "positions",
        ["source", "status"],
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("idx_positions_source_open", table_name="positions")

    # Restore old unique constraint without source
    op.drop_index("uq_positions_open_paper", table_name="positions")
    op.create_index(
        "uq_positions_open_paper",
        "positions",
        ["token_id", "is_paper"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    op.drop_column("trades", "copied_from_wallet")
    op.drop_column("trades", "source")
    op.drop_column("positions", "copied_from_wallet")
    op.drop_column("positions", "source")
