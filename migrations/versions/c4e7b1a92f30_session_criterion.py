"""session criterion: record the load type a manifest session ordered by

Revision ID: c4e7b1a92f30
Revises: a0b2d5f8c3e1
Create Date: 2026-08-31

Nullable by construction, and nullable forever: a diff session has no
criteria to choose among, so NULL is the honest value rather than a
placeholder. Existing rows are all diff sessions and backfill to NULL
correctly without a data migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e7b1a92f30"
down_revision: Union[str, Sequence[str], None] = "a0b2d5f8c3e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("criterion", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_column("criterion")
