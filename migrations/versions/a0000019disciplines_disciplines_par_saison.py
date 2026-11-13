"""réglages d'une discipline propres à une saison

Revision ID: a0000019disciplines
Revises: a0000018feries
Create Date: 2026-11-13 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000019disciplines'
down_revision = 'a0000018feries'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('discipline_season',
    sa.Column('discipline_id', sa.Integer(), nullable=False),
    sa.Column('season_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=True),
    sa.Column('type_id', sa.Integer(), nullable=True),
    sa.Column('offered', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('capacity', sa.Integer(), nullable=True),
    sa.Column('min_age', sa.Integer(), nullable=True),
    sa.Column('max_age', sa.Integer(), nullable=True),
    sa.CheckConstraint('capacity IS NULL OR capacity > 0', name='ck_discipline_season_capacity'),
    sa.CheckConstraint('min_age IS NULL OR max_age IS NULL OR min_age <= max_age', name='ck_discipline_season_ages'),
    sa.ForeignKeyConstraint(['discipline_id'], ['discipline.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['season_id'], ['season.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['type_id'], ['discipline_type.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('discipline_id', 'season_id')
    )


def downgrade():
    op.drop_table('discipline_season')
