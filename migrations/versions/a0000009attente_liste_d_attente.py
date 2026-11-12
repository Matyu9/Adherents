"""liste d'attente des cours

Revision ID: a0000009attente
Revises: a0000008individuel
Create Date: 2026-11-12 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000009attente'
down_revision = 'a0000008individuel'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('waitlist_entry',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('membership_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.Column('dossier_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("status IN ('waiting', 'enrolled', 'removed')", name='ck_waitlist_status'),
    sa.ForeignKeyConstraint(['course_id'], ['course.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dossier_id'], ['dossier.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['membership_id'], ['membership.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('course_id', 'membership_id')
    )
    with op.batch_alter_table('waitlist_entry', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_waitlist_entry_course_id'), ['course_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_waitlist_entry_dossier_id'), ['dossier_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_waitlist_entry_membership_id'), ['membership_id'], unique=False)


def downgrade():
    with op.batch_alter_table('waitlist_entry', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_waitlist_entry_membership_id'))
        batch_op.drop_index(batch_op.f('ix_waitlist_entry_dossier_id'))
        batch_op.drop_index(batch_op.f('ix_waitlist_entry_course_id'))

    op.drop_table('waitlist_entry')
