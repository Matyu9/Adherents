"""notifications supprimées par leur destinataire

Revision ID: a0000014notifications
Revises: a0000013couleurs
Create Date: 2026-11-13 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000014notifications'
down_revision = 'a0000013couleurs'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('notification', schema=None) as batch_op:
        batch_op.add_column(sa.Column('dismissed_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('notification', schema=None) as batch_op:
        batch_op.drop_column('dismissed_at')
