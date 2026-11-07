"""déconnexion : sid des sessions, adresses de déconnexion des applis, file des avis de déconnexion

Revision ID: a0000004deconnexion
Revises: a0000003oidc
Create Date: 2026-11-07 10:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000004deconnexion'
down_revision = 'a0000003oidc'
branch_labels = None
depends_on = None

RANDOM_SID = sa.text("(lower(hex(randomblob(16))))")


def upgrade():
    # Une valeur par défaut calculée ne peut pas s'ajouter par ALTER TABLE sous SQLite : la table est recréée, et
    # chaque session déjà ouverte reçoit son propre sid. Aucune autre table ne référence login_session.
    with op.batch_alter_table('login_session', schema=None, recreate='always') as batch_op:
        batch_op.add_column(sa.Column('sid', sa.String(length=36), server_default=RANDOM_SID, nullable=False))
        batch_op.create_unique_constraint(batch_op.f('uq_login_session_sid'), ['sid'])

    with op.batch_alter_table('oidc_client', schema=None) as batch_op:
        batch_op.add_column(sa.Column('post_logout_redirect_uris', sa.JSON(), server_default='[]', nullable=False))
        batch_op.add_column(sa.Column('backchannel_logout_uri', sa.Text(), nullable=True))

    with op.batch_alter_table('oidc_code', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sid', sa.String(length=36), nullable=True))

    op.create_table('oidc_session',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sid', sa.String(length=36), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('groups', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['client_id'], ['oidc_client.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sid', 'client_id')
    )
    with op.batch_alter_table('oidc_session', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oidc_session_account_id'), ['account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_oidc_session_client_id'), ['client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_oidc_session_sid'), ['sid'], unique=False)

    op.create_table('oidc_logout',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('sub', sa.String(length=36), nullable=False),
    sa.Column('sid', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_error', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['oidc_client.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('oidc_logout', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oidc_logout_client_id'), ['client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_oidc_logout_next_attempt_at'), ['next_attempt_at'], unique=False)


def downgrade():
    with op.batch_alter_table('oidc_logout', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oidc_logout_next_attempt_at'))
        batch_op.drop_index(batch_op.f('ix_oidc_logout_client_id'))
    op.drop_table('oidc_logout')

    with op.batch_alter_table('oidc_session', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oidc_session_sid'))
        batch_op.drop_index(batch_op.f('ix_oidc_session_client_id'))
        batch_op.drop_index(batch_op.f('ix_oidc_session_account_id'))
    op.drop_table('oidc_session')

    with op.batch_alter_table('oidc_code', schema=None) as batch_op:
        batch_op.drop_column('sid')

    with op.batch_alter_table('oidc_client', schema=None) as batch_op:
        batch_op.drop_column('backchannel_logout_uri')
        batch_op.drop_column('post_logout_redirect_uris')

    with op.batch_alter_table('login_session', schema=None, recreate='always') as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_login_session_sid'), type_='unique')
        batch_op.drop_column('sid')
