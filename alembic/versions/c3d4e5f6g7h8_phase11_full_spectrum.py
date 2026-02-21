"""phase11_full_spectrum

Add rugcheck_score, rugcheck_risks to token_security.
Add jupiter_price to token_snapshots.
Add index on tokens(creator_address, first_seen_at) for creator repeat detection.

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6a7
Create Date: 2026-02-19 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = "c3d4e5f6g7h8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # token_security: rugcheck fields
    op.add_column(
        "token_security",
        sa.Column("rugcheck_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "token_security",
        sa.Column("rugcheck_risks", sa.String(2000), nullable=True),
    )

    # token_snapshots: jupiter price cross-validation
    op.add_column(
        "token_snapshots",
        sa.Column("jupiter_price", sa.Numeric(), nullable=True),
    )

    # Index for creator repeat launch detection
    op.create_index(
        "ix_tokens_creator_first_seen",
        "tokens",
        ["creator_address", "first_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tokens_creator_first_seen", table_name="tokens")
    op.drop_column("token_snapshots", "jupiter_price")
    op.drop_column("token_security", "rugcheck_risks")
    op.drop_column("token_security", "rugcheck_score")
