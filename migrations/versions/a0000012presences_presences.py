"""présences des élèves aux séances

Revision ID: a0000012presences
Revises: a0000011salles
Create Date: 2026-11-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000012presences'
down_revision = 'a0000011salles'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('attendance',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('lesson_id', sa.Integer(), nullable=False),
    sa.Column('enrollment_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('recorded_by_id', sa.Integer(), nullable=True),
    sa.Column('recorded_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("status IN ('present', 'absent', 'excused')", name='ck_attendance_status'),
    sa.ForeignKeyConstraint(['enrollment_id'], ['enrollment.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['lesson_id'], ['lesson.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['recorded_by_id'], ['account.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('lesson_id', 'enrollment_id')
    )
    with op.batch_alter_table('attendance', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_attendance_enrollment_id'), ['enrollment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_attendance_lesson_id'), ['lesson_id'], unique=False)


def downgrade():
    with op.batch_alter_table('attendance', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_attendance_lesson_id'))
        batch_op.drop_index(batch_op.f('ix_attendance_enrollment_id'))

    op.drop_table('attendance')
