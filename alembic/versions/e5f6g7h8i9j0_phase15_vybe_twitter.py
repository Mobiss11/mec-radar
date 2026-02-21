"""phase15_vybe_twitter

Add Vybe holder PnL and Twitter social signal columns to token_snapshots.

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-02-19 22:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "e5f6g7h8i9j0"
down_revision: Union[str, None] = "d4e5f6g7h8i9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phase 15: Vybe holder PnL
    op.add_column(
        "token_snapshots",
        sa.Column("holders_in_profit_pct", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "token_snapshots",
        sa.Column("vybe_top_holder_pct", sa.Numeric(), nullable=True),
    )

    # Phase 15: Twitter social signals
    op.add_column(
        "token_snapshots",
        sa.Column("twitter_mentions", sa.Integer(), nullable=True),
    )
    op.add_column(
        "token_snapshots",
        sa.Column("twitter_kol_mentions", sa.Integer(), nullable=True),
    )
    op.add_column(
        "token_snapshots",
        sa.Column("twitter_max_likes", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("token_snapshots", "twitter_max_likes")
    op.drop_column("token_snapshots", "twitter_kol_mentions")
    op.drop_column("token_snapshots", "twitter_mentions")
    op.drop_column("token_snapshots", "vybe_top_holder_pct")
    op.drop_column("token_snapshots", "holders_in_profit_pct")
