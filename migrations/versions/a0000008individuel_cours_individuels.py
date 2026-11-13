"""cours individuels : case sur le cours, heure de passage et durée de chaque élève

Revision ID: a0000008individuel
Revises: a0000007effectifs
Create Date: 2026-11-12 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000008individuel'
down_revision = 'a0000007effectifs'
branch_labels = None
depends_on = None


def upgrade():
    # Aucun cours ne devient individuel d'office : un cours sans limite d'effectif peut être collectif.
    with op.batch_alter_table('course', schema=None) as batch_op:
        batch_op.add_column(sa.Column('individual', sa.Boolean(), server_default=sa.false(), nullable=False))

    with op.batch_alter_table('enrollment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('slot_starts_at', sa.Time(), nullable=True))
        batch_op.add_column(sa.Column('slot_minutes', sa.SmallInteger(), nullable=True))
        batch_op.create_check_constraint('ck_enrollment_slot_minutes', 'slot_minutes > 0')
        batch_op.create_check_constraint('ck_enrollment_slot', 'slot_starts_at IS NULL OR slot_minutes IS NOT NULL')


def downgrade():
    with op.batch_alter_table('enrollment', schema=None) as batch_op:
        batch_op.drop_constraint('ck_enrollment_slot', type_='check')
        batch_op.drop_constraint('ck_enrollment_slot_minutes', type_='check')
        batch_op.drop_column('slot_minutes')
        batch_op.drop_column('slot_starts_at')

    with op.batch_alter_table('course', schema=None) as batch_op:
        batch_op.drop_column('individual')
