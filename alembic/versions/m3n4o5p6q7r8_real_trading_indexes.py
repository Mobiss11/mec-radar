"""Add indexes for real trading — positions (is_paper, status) and trades (tx_hash).

Partial indexes optimize real trader queries:
- Real open positions lookup (is_paper=0 AND status='open')
- Transaction hash lookups for on-chain verification

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-02-21
"""

from alembic import op

revision = "m3n4o5p6q7r8"
down_revision = "l2m3n4o5p6q7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partial index for real open positions — used by RealTrader queries
    op.create_index(
        "idx_positions_real_open",
        "positions",
        ["is_paper", "status"],
        postgresql_where="is_paper = 0 AND status = 'open'",
    )

    # Partial index for tx_hash lookups — real trades have non-null tx_hash
    op.create_index(
        "idx_trades_tx_hash",
        "trades",
        ["tx_hash"],
        postgresql_where="tx_hash IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("idx_trades_tx_hash", table_name="trades")
    op.drop_index("idx_positions_real_open", table_name="positions")
