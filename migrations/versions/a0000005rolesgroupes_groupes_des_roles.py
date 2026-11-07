"""groupes des rôles, accès des applis à la liste des comptes

Revision ID: a0000005rolesgroupes
Revises: a0000004deconnexion
Create Date: 2026-11-07 18:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000005rolesgroupes'
down_revision = 'a0000004deconnexion'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('role_access_group',
    sa.Column('role_key', sa.String(length=20), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['access_group.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_key'], ['role.key'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('role_key', 'group_id')
    )
    with op.batch_alter_table('role_access_group', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_role_access_group_group_id'), ['group_id'], unique=False)

    with op.batch_alter_table('oidc_client', schema=None) as batch_op:
        batch_op.add_column(sa.Column('directory_access', sa.Boolean(), server_default='0', nullable=False))


def downgrade():
    with op.batch_alter_table('oidc_client', schema=None) as batch_op:
        batch_op.drop_column('directory_access')

    with op.batch_alter_table('role_access_group', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_role_access_group_group_id'))
    op.drop_table('role_access_group')
