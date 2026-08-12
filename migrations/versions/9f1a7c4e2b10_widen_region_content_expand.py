"""widen hunk to region content (expand: add + backfill content)

Adds the widened `regions.content` JSON column and backfills every existing
row as a code_hunk: body <- hunk.text, geometry -> content.metadata. The old
`hunk` column is retained so a rollback mid-deploy still has both columns; the
follow-up contract migration drops it.

Revision ID: 9f1a7c4e2b10
Revises: 511d7fffc85f
Create Date: 2026-08-10 00:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f1a7c4e2b10'
down_revision: Union[str, Sequence[str], None] = '511d7fffc85f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GEOMETRY_KEYS = ("file_path", "old_start", "old_count", "new_start", "new_count")

_regions = sa.table(
    "regions",
    sa.column("id", sa.String),
    sa.column("hunk", sa.JSON),
    sa.column("content", sa.JSON),
)


def _as_dict(value) -> dict:
    """SQLAlchemy's JSON type usually deserializes, but a raw text value can
    still come back as a string; handle both."""
    if isinstance(value, str):
        return json.loads(value)
    return value or {}


def _hunk_ref(h: dict) -> str:
    new_start = h.get("new_start", 0)
    new_count = h.get("new_count", 0)
    end = new_start + max(new_count, 1) - 1
    return f"{h.get('file_path', '')}@{new_start}-{end}"


def upgrade() -> None:
    with op.batch_alter_table("regions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("content", sa.JSON(), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(sa.select(_regions.c.id, _regions.c.hunk)).fetchall()
    for row in rows:
        h = _as_dict(row.hunk)
        metadata = {k: h[k] for k in _GEOMETRY_KEYS if k in h}
        metadata["ref"] = _hunk_ref(h)
        content = {
            "kind": "code_hunk",
            "body": h.get("text", ""),
            "metadata": metadata,
        }
        conn.execute(
            sa.update(_regions)
            .where(_regions.c.id == row.id)
            .values(content=content)
        )


def downgrade() -> None:
    with op.batch_alter_table("regions", schema=None) as batch_op:
        batch_op.drop_column("content")
