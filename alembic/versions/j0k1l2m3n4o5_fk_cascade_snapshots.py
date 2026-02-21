"""Add ON DELETE CASCADE/SET NULL to snapshot FK references.

Without these, cleanup_old_data() would fail when deleting old snapshots
that are still referenced by token_top_holders or token_outcomes.

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-02-20
"""

from alembic import op

# revision identifiers
revision = "j0k1l2m3n4o5"
down_revision = "i9j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # TokenTopHolder.snapshot_id → CASCADE (delete holders when snapshot deleted)
    op.drop_constraint(
        "token_top_holders_snapshot_id_fkey",
        "token_top_holders",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "token_top_holders_snapshot_id_fkey",
        "token_top_holders",
        "token_snapshots",
        ["snapshot_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # TokenOutcome.peak_snapshot_id → SET NULL (keep outcome, clear ref)
    op.drop_constraint(
        "token_outcomes_peak_snapshot_id_fkey",
        "token_outcomes",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "token_outcomes_peak_snapshot_id_fkey",
        "token_outcomes",
        "token_snapshots",
        ["peak_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Revert TokenTopHolder
    op.drop_constraint(
        "token_top_holders_snapshot_id_fkey",
        "token_top_holders",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "token_top_holders_snapshot_id_fkey",
        "token_top_holders",
        "token_snapshots",
        ["snapshot_id"],
        ["id"],
    )

    # Revert TokenOutcome
    op.drop_constraint(
        "token_outcomes_peak_snapshot_id_fkey",
        "token_outcomes",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "token_outcomes_peak_snapshot_id_fkey",
        "token_outcomes",
        "token_snapshots",
        ["peak_snapshot_id"],
        ["id"],
    )
