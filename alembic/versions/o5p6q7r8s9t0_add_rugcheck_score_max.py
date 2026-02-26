"""Add rugcheck_score_max column to token_security (Phase 53: anti-rug protection).

Monotonic max-ever rugcheck score that never decreases.
Prevents scammer manipulation where rugcheck API returns low score
after previously returning dangerous score (e.g. 3501 → 1).

Revision ID: o5p6q7r8s9t0
Revises: m3n4o5p6q7r8
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "o5p6q7r8s9t0"
down_revision = "m3n4o5p6q7r8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "token_security",
        sa.Column("rugcheck_score_max", sa.Integer(), nullable=True),
    )
    # Backfill from existing rugcheck_score
    op.execute(
        "UPDATE token_security SET rugcheck_score_max = rugcheck_score "
        "WHERE rugcheck_score IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("token_security", "rugcheck_score_max")
