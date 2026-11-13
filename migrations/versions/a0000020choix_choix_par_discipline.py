"""réinscription par discipline : mode de choix d'une campagne, disciplines à placer

Revision ID: a0000020choix
Revises: a0000019disciplines
Create Date: 2026-11-13 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000020choix'
down_revision = 'a0000019disciplines'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('campaign', schema=None) as batch_op:
        batch_op.add_column(sa.Column('choice', sa.String(length=12), nullable=False, server_default='cours'))
    with op.batch_alter_table('membership', schema=None) as batch_op:
        batch_op.add_column(sa.Column('placement', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('membership', schema=None) as batch_op:
        batch_op.drop_column('placement')
    with op.batch_alter_table('campaign', schema=None) as batch_op:
        batch_op.drop_column('choice')
