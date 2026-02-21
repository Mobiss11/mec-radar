"""position_symbol

Add symbol column to positions table for readable alerts.

Revision ID: g7h8i9j0k1l2
Revises: f6g7h8i9j0k1
Create Date: 2026-02-19 19:23:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "g7h8i9j0k1l2"
down_revision = "f6g7h8i9j0k1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("symbol", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("positions", "symbol")
