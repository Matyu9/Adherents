"""types de discipline : une liste que l'association compose, à la place d'une liste fixe

Revision ID: a0000006types
Revises: a0000005tarifs
Create Date: 2026-11-11 19:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000006types'
down_revision = 'a0000005tarifs'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('discipline_type',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('archived_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    with op.batch_alter_table('discipline', schema=None) as batch_op:
        batch_op.add_column(sa.Column('type_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_discipline_type_id'), ['type_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('discipline_type_id_fkey'), 'discipline_type', ['type_id'], ['id'], ondelete='RESTRICT')

    # Les types déjà saisis deviennent des lignes de la nouvelle liste, dans l'ordre alphabétique.
    op.execute(
        "INSERT INTO discipline_type (name, position) "
        "SELECT category, 10 * row_number() OVER (ORDER BY category) FROM "
        "(SELECT DISTINCT btrim(category) AS category FROM discipline WHERE btrim(category) <> '') AS used"
    )
    op.execute(
        "UPDATE discipline SET type_id = discipline_type.id FROM discipline_type "
        "WHERE btrim(discipline.category) = discipline_type.name"
    )

    with op.batch_alter_table('discipline', schema=None) as batch_op:
        batch_op.drop_column('category')


def downgrade():
    with op.batch_alter_table('discipline', schema=None) as batch_op:
        batch_op.add_column(sa.Column('category', sa.String(length=40), nullable=True))

    op.execute(
        "UPDATE discipline SET category = discipline_type.name FROM discipline_type "
        "WHERE discipline.type_id = discipline_type.id"
    )

    with op.batch_alter_table('discipline', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('discipline_type_id_fkey'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_discipline_type_id'))
        batch_op.drop_column('type_id')

    op.drop_table('discipline_type')
