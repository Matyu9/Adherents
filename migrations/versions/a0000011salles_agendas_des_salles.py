"""agendas des salles : agenda et capacité d'une salle, bâtiment d'un site, file d'envoi vers les agendas

Revision ID: a0000011salles
Revises: a0000010seances
Create Date: 2026-11-12 23:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000011salles'
down_revision = 'a0000010seances'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('site', schema=None) as batch_op:
        batch_op.add_column(sa.Column('external_id', sa.String(length=255), nullable=True))
        batch_op.create_unique_constraint(batch_op.f('site_external_id_key'), ['external_id'])

    with op.batch_alter_table('room', schema=None) as batch_op:
        batch_op.add_column(sa.Column('calendar_id', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('capacity', sa.Integer(), nullable=True))
        batch_op.create_unique_constraint(batch_op.f('room_calendar_id_key'), ['calendar_id'])

    op.create_table('room_calendar_job',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('calendar_id', sa.String(length=255), nullable=False),
    sa.Column('event_id', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=10), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('next_try_at', sa.DateTime(), nullable=False),
    sa.Column('last_error', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("action IN ('put', 'delete')", name='ck_room_calendar_job_action'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('room_calendar_job')
    with op.batch_alter_table('room', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('room_calendar_id_key'), type_='unique')
        batch_op.drop_column('capacity')
        batch_op.drop_column('calendar_id')

    with op.batch_alter_table('site', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('site_external_id_key'), type_='unique')
        batch_op.drop_column('external_id')
