"""séances : calendrier de la saison, périodes sans cours, séances, jeton d'agenda, droit lesson.manage

Revision ID: a0000010seances
Revises: a0000009attente
Create Date: 2026-11-12 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000010seances'
down_revision = 'a0000009attente'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('season', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lessons_from', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('lessons_until', sa.Date(), nullable=True))

    op.create_table('season_break',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('season_id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('starts_on', sa.Date(), nullable=False),
    sa.Column('ends_on', sa.Date(), nullable=False),
    sa.CheckConstraint('ends_on >= starts_on', name='ck_season_break_dates'),
    sa.ForeignKeyConstraint(['season_id'], ['season.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('season_break', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_season_break_season_id'), ['season_id'], unique=False)

    op.create_table('lesson',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('scheduled_on', sa.Date(), nullable=True),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('starts_at', sa.Time(), nullable=False),
    sa.Column('ends_at', sa.Time(), nullable=False),
    sa.Column('room_id', sa.Integer(), nullable=True),
    sa.Column('substitute_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('reason', sa.String(length=200), nullable=True),
    sa.Column('edited', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('previous', sa.JSON(), nullable=True),
    sa.Column('changed_by_id', sa.Integer(), nullable=True),
    sa.Column('changed_at', sa.DateTime(), nullable=True),
    sa.Column('revision', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('ends_at > starts_at', name='ck_lesson_times'),
    sa.CheckConstraint("status IN ('planned', 'cancelled')", name='ck_lesson_status'),
    sa.ForeignKeyConstraint(['changed_by_id'], ['account.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['course_id'], ['course.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['room_id'], ['room.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['substitute_id'], ['teacher.person_id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('course_id', 'scheduled_on')
    )
    with op.batch_alter_table('lesson', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_lesson_course_id'), ['course_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_lesson_day'), ['day'], unique=False)
        batch_op.create_index(batch_op.f('ix_lesson_room_id'), ['room_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_lesson_substitute_id'), ['substitute_id'], unique=False)

    with op.batch_alter_table('account', schema=None) as batch_op:
        batch_op.add_column(sa.Column('calendar_token', sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint(batch_op.f('account_calendar_token_key'), ['calendar_token'])

    op.execute("""UPDATE role SET permissions = (permissions::jsonb || '["lesson.manage"]'::jsonb)::json
                  WHERE key = 'gestionnaire' AND NOT permissions::jsonb ? 'lesson.manage'""")


def downgrade():
    op.execute("""UPDATE role SET permissions = COALESCE(
                    (SELECT json_agg(p) FROM json_array_elements_text(permissions) AS p WHERE p <> 'lesson.manage'),
                    '[]'::json)""")
    with op.batch_alter_table('account', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('account_calendar_token_key'), type_='unique')
        batch_op.drop_column('calendar_token')

    with op.batch_alter_table('lesson', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_lesson_substitute_id'))
        batch_op.drop_index(batch_op.f('ix_lesson_room_id'))
        batch_op.drop_index(batch_op.f('ix_lesson_day'))
        batch_op.drop_index(batch_op.f('ix_lesson_course_id'))

    op.drop_table('lesson')
    with op.batch_alter_table('season_break', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_season_break_season_id'))

    op.drop_table('season_break')
    with op.batch_alter_table('season', schema=None) as batch_op:
        batch_op.drop_column('lessons_until')
        batch_op.drop_column('lessons_from')
