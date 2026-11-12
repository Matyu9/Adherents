"""effectifs : date d'entrée et transfert d'une inscription, cotisation « à revoir », droit enrollment.edit

Revision ID: a0000007effectifs
Revises: a0000006types
Create Date: 2026-11-12 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000007effectifs'
down_revision = 'a0000006types'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('enrollment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('enrolled_on', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('replaces_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_enrollment_replaces_id'), ['replaces_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('enrollment_replaces_id_fkey'), 'enrollment', ['replaces_id'], ['id'], ondelete='SET NULL')

    # Les inscriptions existantes datent de leur enregistrement (jamais après leur abandon).
    op.execute("UPDATE enrollment SET enrolled_on = LEAST(created_at::date, COALESCE(withdrawn_on, created_at::date))")

    with op.batch_alter_table('enrollment', schema=None) as batch_op:
        batch_op.alter_column('enrolled_on', nullable=False, server_default=sa.text('CURRENT_DATE'))
        batch_op.create_check_constraint('ck_enrollment_dates', 'withdrawn_on >= enrolled_on')
        batch_op.create_check_constraint('ck_enrollment_replaces', 'replaces_id <> id')

    with op.batch_alter_table('assessment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('review_reasons', sa.JSON(), server_default='[]', nullable=False))

    op.execute("""UPDATE role SET permissions = (permissions::jsonb || '["enrollment.edit"]'::jsonb)::json
                  WHERE key = 'gestionnaire' AND NOT permissions::jsonb ? 'enrollment.edit'""")


def downgrade():
    op.execute("""UPDATE role SET permissions = COALESCE(
                    (SELECT json_agg(p) FROM json_array_elements_text(permissions) AS p WHERE p <> 'enrollment.edit'),
                    '[]'::json)""")
    with op.batch_alter_table('assessment', schema=None) as batch_op:
        batch_op.drop_column('review_reasons')

    with op.batch_alter_table('enrollment', schema=None) as batch_op:
        batch_op.drop_constraint('ck_enrollment_replaces', type_='check')
        batch_op.drop_constraint('ck_enrollment_dates', type_='check')
        batch_op.drop_constraint(batch_op.f('enrollment_replaces_id_fkey'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_enrollment_replaces_id'))
        batch_op.drop_column('replaces_id')
        batch_op.drop_column('enrolled_on')
