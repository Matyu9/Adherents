"""retards à l'appel, absences prévenues à l'avance par les familles

Revision ID: a0000017retards
Revises: a0000016profs
Create Date: 2026-11-13 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000017retards'
down_revision = 'a0000016profs'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('ck_attendance_status', 'attendance', type_='check')
    op.create_check_constraint(
        'ck_attendance_status', 'attendance', "status IN ('present', 'absent', 'excused', 'late')"
    )
    op.create_table('absence_notice',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('lesson_id', sa.Integer(), nullable=False),
    sa.Column('enrollment_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['enrollment_id'], ['enrollment.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['lesson_id'], ['lesson.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('lesson_id', 'enrollment_id')
    )
    with op.batch_alter_table('absence_notice', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_absence_notice_enrollment_id'), ['enrollment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_absence_notice_lesson_id'), ['lesson_id'], unique=False)


def downgrade():
    with op.batch_alter_table('absence_notice', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_absence_notice_lesson_id'))
        batch_op.drop_index(batch_op.f('ix_absence_notice_enrollment_id'))
    op.drop_table('absence_notice')
    op.execute("UPDATE attendance SET status = 'present' WHERE status = 'late'")
    op.drop_constraint('ck_attendance_status', 'attendance', type_='check')
    op.create_check_constraint('ck_attendance_status', 'attendance', "status IN ('present', 'absent', 'excused')")
