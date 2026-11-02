"""backfill: roadmap tasks finished through Today's Mission are done

Revision ID: 0055_mission_roadmap_backfill
Revises: 0054_business_plan_options
Create Date: 2026-10-19

Until the 2026-10-19 re-walk fix, completing a roadmap-linked mission task (or rejecting it as
"Already done") never completed the roadmap task, so missions kept re-offering finished work and
milestone progress never moved. This marks those roadmap tasks done and recomputes the progress
of every milestone that has tasks, with the same rule as `recompute_milestone_progress`.
Data-only and idempotent.
"""

from alembic import op

revision = "0055_mission_roadmap_backfill"
down_revision = "0054_business_plan_options"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE roadmap_tasks rt SET status = 'done', updated_at = now()
        WHERE rt.status <> 'done'
          AND EXISTS (
            SELECT 1 FROM mission_tasks mt
            WHERE mt.roadmap_task_id = rt.id
              AND (mt.status = 'done'
                   OR (mt.status = 'rejected' AND mt.reject_reason = 'Already done'))
          )
        """
    )
    op.execute(
        """
        UPDATE roadmap_milestones m SET progress = agg.pct
        FROM (
          SELECT milestone_id,
                 ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'done') / COUNT(*))::int AS pct
          FROM roadmap_tasks GROUP BY milestone_id
        ) agg
        WHERE agg.milestone_id = m.id AND m.progress IS DISTINCT FROM agg.pct
        """
    )


def downgrade() -> None:
    """Nothing to undo safely: which tasks were `todo` before the backfill isn't recorded, and
    re-opening finished work would be wrong. The schema is unchanged either way."""
