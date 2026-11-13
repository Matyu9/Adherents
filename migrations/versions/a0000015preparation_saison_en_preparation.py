"""saison en préparation, cachée à qui n'est pas de l'administration

Revision ID: a0000015preparation
Revises: a0000014notifications
Create Date: 2026-11-13 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000015preparation'
down_revision = 'a0000014notifications'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('season', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hidden', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('season', schema=None) as batch_op:
        batch_op.drop_column('hidden')
