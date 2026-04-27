"""Backfill task source memory links.

Revision ID: f0a1b2c3d4e5
Revises: ea1b2c3d4f5a
Create Date: 2026-04-26 22:45:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "f0a1b2c3d4e5"
down_revision = "ea1b2c3d4f5a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        WITH candidates AS (
            SELECT
                tasks.id AS task_id,
                (regexp_match(
                    tasks.description,
                    'source_memory_id=([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})'
                ))[1]::uuid AS memory_id,
                row_number() OVER (
                    PARTITION BY (
                        regexp_match(
                            tasks.description,
                            'source_memory_id=([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})'
                        )
                    )[1]::uuid
                    ORDER BY
                        CASE
                            WHEN lower(tasks.title) LIKE 'decompose%' THEN 0
                            WHEN tasks.status IN ('inbox', 'review') THEN 1
                            ELSE 2
                        END,
                        tasks.created_at,
                        tasks.id
                ) AS rank
            FROM tasks
            WHERE tasks.source_memory_id IS NULL
              AND tasks.description IS NOT NULL
              AND tasks.description ~
                  'source_memory_id=[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
        ),
        eligible AS (
            SELECT candidates.task_id, candidates.memory_id
            FROM candidates
            JOIN board_memory ON board_memory.id = candidates.memory_id
            WHERE candidates.rank = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM tasks linked
                  WHERE linked.source_memory_id = candidates.memory_id
              )
        )
        UPDATE tasks
        SET source_memory_id = eligible.memory_id
        FROM eligible
        WHERE tasks.id = eligible.task_id
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE tasks
        SET source_memory_id = NULL
        WHERE description IS NOT NULL
          AND description ~
              'source_memory_id=[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
        """
    )
