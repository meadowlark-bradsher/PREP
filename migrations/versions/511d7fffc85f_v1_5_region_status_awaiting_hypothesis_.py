"""v1.5 region status: AWAITING_HYPOTHESIS and AWAITING_REVEAL_CHOICE

Revision ID: 511d7fffc85f
Revises: b308b508a8f2
Create Date: 2026-05-29 17:57:43.156725

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '511d7fffc85f'
down_revision: Union[str, Sequence[str], None] = 'b308b508a8f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Widen the CHECK constraint on regions.status to accept the two new
    v1.5 states. Alembic autogenerate misses this because the enum type
    name is unchanged; same pattern as the closure_mode widening in the
    previous v1.5 migration.
    """
    with op.batch_alter_table('regions', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum(
                'AWAITING_RECONCILIATION',
                'IN_DIALOGUE',
                'AWAITING_DISPOSITION',
                'CLOSED',
                name='regionstatus',
            ),
            type_=sa.Enum(
                'AWAITING_HYPOTHESIS',
                'AWAITING_REVEAL_CHOICE',
                'AWAITING_RECONCILIATION',
                'IN_DIALOGUE',
                'AWAITING_DISPOSITION',
                'CLOSED',
                name='regionstatus',
            ),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('regions', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum(
                'AWAITING_HYPOTHESIS',
                'AWAITING_REVEAL_CHOICE',
                'AWAITING_RECONCILIATION',
                'IN_DIALOGUE',
                'AWAITING_DISPOSITION',
                'CLOSED',
                name='regionstatus',
            ),
            type_=sa.Enum(
                'AWAITING_RECONCILIATION',
                'IN_DIALOGUE',
                'AWAITING_DISPOSITION',
                'CLOSED',
                name='regionstatus',
            ),
            existing_nullable=False,
        )
