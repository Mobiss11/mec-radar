"""Add signal dedup partial unique index, snapshot token_stage index, position token_status index.

P0: Prevents signal duplication across parallel workers via partial unique index.
P1: Adds missing composite indexes for common query patterns.

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-02-20
"""

from alembic import op

revision = "l2m3n4o5p6q7"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # P0: Signal dedup — partial unique index on active signals
    op.create_index(
        "uq_signals_token_status_active",
        "signals",
        ["token_id", "status"],
        unique=True,
        postgresql_where="status IN ('strong_buy', 'buy', 'watch')",
    )

    # P1: Snapshot lookup by token + stage (enrichment pipeline)
    op.create_index(
        "idx_snapshots_token_stage",
        "token_snapshots",
        ["token_id", "stage"],
    )

    # P1: Position lookup by token + status (paper trader queries)
    op.create_index(
        "idx_positions_token_status",
        "positions",
        ["token_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_positions_token_status", table_name="positions")
    op.drop_index("idx_snapshots_token_stage", table_name="token_snapshots")
    op.drop_index("uq_signals_token_status_active", table_name="signals")
