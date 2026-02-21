"""phase16_enrichment

Add holder growth, website, telegram, LLM columns to token_snapshots.

Revision ID: f6g7h8i9j0k1
Revises: e5f6g7h8i9j0
Create Date: 2026-02-19 23:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "f6g7h8i9j0k1"
down_revision: Union[str, None] = "e5f6g7h8i9j0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phase 16: Holder growth velocity
    op.add_column(
        "token_snapshots",
        sa.Column("holder_growth_pct", sa.Numeric(), nullable=True),
    )

    # Phase 16: Website/domain
    op.add_column(
        "token_snapshots",
        sa.Column("has_website", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "token_snapshots",
        sa.Column("domain_age_days", sa.Integer(), nullable=True),
    )

    # Phase 16: Telegram community
    op.add_column(
        "token_snapshots",
        sa.Column("tg_member_count", sa.Integer(), nullable=True),
    )

    # Phase 16: LLM risk score
    op.add_column(
        "token_snapshots",
        sa.Column("llm_risk_score", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("token_snapshots", "llm_risk_score")
    op.drop_column("token_snapshots", "tg_member_count")
    op.drop_column("token_snapshots", "domain_age_days")
    op.drop_column("token_snapshots", "has_website")
    op.drop_column("token_snapshots", "holder_growth_pct")
