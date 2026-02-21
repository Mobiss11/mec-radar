"""phase9_schema_updates

Add score_v3 to snapshots, signal_id + close_reason to positions,
wallet_clusters table.

Revision ID: a1b2c3d4e5f6
Revises: 9dfac8341079
Create Date: 2026-02-18 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '9dfac8341079'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add score_v3 to token_snapshots
    op.add_column('token_snapshots', sa.Column('score_v3', sa.Integer(), nullable=True))

    # 2. Add signal_id and close_reason to positions
    op.add_column('positions', sa.Column('signal_id', sa.Integer(), sa.ForeignKey('signals.id'), nullable=True))
    op.add_column('positions', sa.Column('close_reason', sa.String(30), nullable=True))

    # 3. Create wallet_clusters table (if_not_exists for idempotency)
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='wallet_clusters')"
    ))
    if not result.scalar():
        op.create_table(
            'wallet_clusters',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('cluster_id', sa.String(64), nullable=False),
            sa.Column('wallet_address', sa.String(64), nullable=False),
            sa.Column('confidence', sa.Numeric(), nullable=True),
            sa.Column('method', sa.String(30), nullable=False),
            sa.Column('detected_at', sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index('idx_wallet_clusters_cluster', 'wallet_clusters', ['cluster_id'])
        op.create_index('idx_wallet_clusters_wallet', 'wallet_clusters', ['wallet_address'])


def downgrade() -> None:
    op.drop_index('idx_wallet_clusters_wallet', 'wallet_clusters')
    op.drop_index('idx_wallet_clusters_cluster', 'wallet_clusters')
    op.drop_table('wallet_clusters')
    op.drop_column('positions', 'close_reason')
    op.drop_column('positions', 'signal_id')
    op.drop_column('token_snapshots', 'score_v3')
