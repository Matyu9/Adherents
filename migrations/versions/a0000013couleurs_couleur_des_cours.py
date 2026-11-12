"""couleur d'un cours dans les plannings

Revision ID: a0000013couleurs
Revises: a0000012presences
Create Date: 2026-11-12 22:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000013couleurs'
down_revision = 'a0000012presences'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('course', schema=None) as batch_op:
        batch_op.add_column(sa.Column('color', sa.String(length=12), nullable=True))


def downgrade():
    with op.batch_alter_table('course', schema=None) as batch_op:
        batch_op.drop_column('color')
