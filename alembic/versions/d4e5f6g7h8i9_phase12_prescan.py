"""phase12_prescan

Add bundled_buy_detected, lp_burned_pct_raydium, goplus_score to token_security.
Add pumpfun_dead_tokens to creator_profiles.

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-02-19 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "d4e5f6g7h8i9"
down_revision: Union[str, None] = "c3d4e5f6g7h8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # token_security: Phase 12 fields
    op.add_column(
        "token_security",
        sa.Column("bundled_buy_detected", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "token_security",
        sa.Column("lp_burned_pct_raydium", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "token_security",
        sa.Column("goplus_score", sa.String(2000), nullable=True),
    )

    # creator_profiles: pump.fun dead token count
    op.add_column(
        "creator_profiles",
        sa.Column("pumpfun_dead_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("creator_profiles", "pumpfun_dead_tokens")
    op.drop_column("token_security", "goplus_score")
    op.drop_column("token_security", "lp_burned_pct_raydium")
    op.drop_column("token_security", "bundled_buy_detected")
