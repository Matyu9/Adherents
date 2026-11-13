"""jours fériés et vacances : nature d'une période sans cours, cours maintenus

Revision ID: a0000018feries
Revises: a0000017retards
Create Date: 2026-11-13 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000018feries'
down_revision = 'a0000017retards'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('season_break', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=12), nullable=False, server_default='fermeture'))
        batch_op.add_column(sa.Column('keep_lessons', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('season_break', schema=None) as batch_op:
        batch_op.drop_column('keep_lessons')
        batch_op.drop_column('kind')
