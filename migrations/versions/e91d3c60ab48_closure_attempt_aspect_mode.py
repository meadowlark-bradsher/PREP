"""closure attempts: criterion, aspect scope, structured missing_aspects

Revision ID: e91d3c60ab48
Revises: c4e7b1a92f30
Create Date: 2026-08-31

Two new nullable columns, plus a widening of missing_aspects from Text to
JSON so it can hold either shape the judge produces: a sentence of prose
for a region that declared no aspects, or a list of aspect ids for one
that did.

The widening is why this migration has a backfill. Existing rows hold raw
prose, which is not valid JSON, so every non-NULL value is re-encoded as a
JSON string. There are no rows today — the migration clock has not started
— but a Text column silently reinterpreted as JSON is the kind of thing
that reads fine until the first real row, so it is written correctly now.

`aspect_scope` is the mode discriminator: non-NULL exactly when the judge
ran against a declared aspect list, and holding the ids it was permitted
to name.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e91d3c60ab48"
down_revision: Union[str, Sequence[str], None] = "c4e7b1a92f30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Re-encode prose as JSON *before* the type changes, so the column is
    # never transiently holding values its declared type cannot parse.
    rows = conn.execute(
        sa.text(
            "SELECT id, missing_aspects FROM closure_attempts "
            "WHERE missing_aspects IS NOT NULL"
        )
    ).fetchall()
    for row_id, prose in rows:
        conn.execute(
            sa.text(
                "UPDATE closure_attempts SET missing_aspects = :encoded WHERE id = :id"
            ),
            {"encoded": json.dumps(prose), "id": row_id},
        )

    with op.batch_alter_table("closure_attempts", schema=None) as batch_op:
        batch_op.alter_column(
            "missing_aspects",
            existing_type=sa.Text(),
            type_=sa.JSON(),
            existing_nullable=True,
        )
        batch_op.add_column(
            sa.Column("criterion", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("aspect_scope", sa.JSON(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()

    with op.batch_alter_table("closure_attempts", schema=None) as batch_op:
        batch_op.drop_column("aspect_scope")
        batch_op.drop_column("criterion")
        batch_op.alter_column(
            "missing_aspects",
            existing_type=sa.JSON(),
            type_=sa.Text(),
            existing_nullable=True,
        )

    # Structured verdicts have no prose equivalent; a list degrades to a
    # comma-joined string rather than being silently dropped.
    rows = conn.execute(
        sa.text(
            "SELECT id, missing_aspects FROM closure_attempts "
            "WHERE missing_aspects IS NOT NULL"
        )
    ).fetchall()
    for row_id, encoded in rows:
        try:
            value = json.loads(encoded)
        except (TypeError, ValueError):
            continue  # already prose; leave it alone
        prose = ", ".join(value) if isinstance(value, list) else str(value)
        conn.execute(
            sa.text(
                "UPDATE closure_attempts SET missing_aspects = :prose WHERE id = :id"
            ),
            {"prose": prose, "id": row_id},
        )
