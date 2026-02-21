"""phase10_deep_foundation

Add outcome_stage, volatility metrics, LP removal tracking,
creator funding trace fields.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-18 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. TokenOutcome: track which stage detected the outcome
    op.add_column('token_outcomes', sa.Column('outcome_stage', sa.String(20), nullable=True))

    # 2. TokenSnapshot: volatility and LP metrics
    op.add_column('token_snapshots', sa.Column('volatility_5m', sa.Numeric(), nullable=True))
    op.add_column('token_snapshots', sa.Column('volatility_1h', sa.Numeric(), nullable=True))
    op.add_column('token_snapshots', sa.Column('lp_removed_pct', sa.Numeric(), nullable=True))

    # 3. CreatorProfile: funding trace
    op.add_column('creator_profiles', sa.Column('is_first_launch', sa.Boolean(), nullable=True))
    op.add_column('creator_profiles', sa.Column('funded_by', sa.String(64), nullable=True))
    op.add_column('creator_profiles', sa.Column('funding_risk_score', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('creator_profiles', 'funding_risk_score')
    op.drop_column('creator_profiles', 'funded_by')
    op.drop_column('creator_profiles', 'is_first_launch')
    op.drop_column('token_snapshots', 'lp_removed_pct')
    op.drop_column('token_snapshots', 'volatility_1h')
    op.drop_column('token_snapshots', 'volatility_5m')
    op.drop_column('token_outcomes', 'outcome_stage')
