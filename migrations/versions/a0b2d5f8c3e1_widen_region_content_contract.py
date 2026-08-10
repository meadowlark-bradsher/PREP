"""widen hunk to region content (contract: enforce + drop hunk)

Makes `regions.content` NOT NULL and drops the legacy `regions.hunk` column.
Run only after the expand migration has backfilled every row and the code
that reads `content` is deployed.

The downgrade reconstructs `hunk` from content. This is faithful only for
code_hunk content; any non-diff kind (glossary_term, concept) added after
this migration cannot be represented as a diff hunk and is reconstructed
with zeroed geometry and body-as-text. Downgrade past this point is only
safe before such content exists.

Revision ID: a0b2d5f8c3e1
Revises: 9f1a7c4e2b10
Create Date: 2026-08-10 00:05:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a0b2d5f8c3e1'
down_revision: Union[str, Sequence[str], None] = '9f1a7c4e2b10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_regions = sa.table(
    "regions",
    sa.column("id", sa.String),
    sa.column("hunk", sa.JSON),
    sa.column("content", sa.JSON),
)


def _as_dict(value) -> dict:
    if isinstance(value, str):
        return json.loads(value)
    return value or {}


def upgrade() -> None:
    with op.batch_alter_table("regions", schema=None) as batch_op:
        batch_op.alter_column(
            "content", existing_type=sa.JSON(), nullable=False
        )
        batch_op.drop_column("hunk")


def downgrade() -> None:
    # Re-add hunk nullable, backfill from content, then enforce NOT NULL.
    with op.batch_alter_table("regions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("hunk", sa.JSON(), nullable=True))
        batch_op.alter_column(
            "content", existing_type=sa.JSON(), nullable=True
        )

    conn = op.get_bind()
    rows = conn.execute(sa.select(_regions.c.id, _regions.c.content)).fetchall()
    for row in rows:
        content = _as_dict(row.content)
        meta = content.get("metadata") or {}
        hunk = {
            "file_path": meta.get("file_path", ""),
            "old_start": meta.get("old_start", 0),
            "old_count": meta.get("old_count", 0),
            "new_start": meta.get("new_start", 0),
            "new_count": meta.get("new_count", 0),
            "text": content.get("body", ""),
        }
        conn.execute(
            sa.update(_regions)
            .where(_regions.c.id == row.id)
            .values(hunk=hunk)
        )

    with op.batch_alter_table("regions", schema=None) as batch_op:
        batch_op.alter_column(
            "hunk", existing_type=sa.JSON(), nullable=False
        )
