"""fiche des professeurs : présentation, photo, diplômes, disponibilités

Revision ID: a0000016profs
Revises: a0000015preparation
Create Date: 2026-11-13 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000016profs'
down_revision = 'a0000015preparation'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('teacher', schema=None) as batch_op:
        batch_op.add_column(sa.Column('bio', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('photo', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('diplomas', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('availability', sa.JSON(), nullable=False, server_default='[]'))


def downgrade():
    with op.batch_alter_table('teacher', schema=None) as batch_op:
        batch_op.drop_column('availability')
        batch_op.drop_column('diplomas')
        batch_op.drop_column('photo')
        batch_op.drop_column('bio')
