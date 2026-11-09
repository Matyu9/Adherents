"""schéma initial (PostgreSQL)

Remplace les migrations écrites pour SQLite (phases 0 à 5 et annuaire) : aucune base de production n'existait
quand l'appli est passée à PostgreSQL, le 9 novembre 2026.

Revision ID: a0000001initial
Revises: 
Create Date: 2026-11-09 15:54:29.022984

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0000001initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Recherche sans accents : fold(texte) = minuscules sans accents (voir app/extensions.py).
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE OR REPLACE FUNCTION fold(text) RETURNS text LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE "
               "AS $$ SELECT lower(public.unaccent('public.unaccent'::regdictionary, $1)) $$")
    op.create_table('access_group',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key'),
    sa.UniqueConstraint('name')
    )
    op.create_table('account',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sub', sa.String(length=36), nullable=False),
    sa.Column('first_name', sa.String(length=80), nullable=False),
    sa.Column('last_name', sa.String(length=80), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('email_verified', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('must_change_password', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('last_login_at', sa.DateTime(), nullable=True),
    sa.Column('totp_secret', sa.String(length=255), nullable=True),
    sa.Column('totp_enabled_at', sa.DateTime(), nullable=True),
    sa.Column('totp_last_counter', sa.Integer(), nullable=True),
    sa.Column('theme', sa.String(length=10), nullable=True),
    sa.Column('text_size', sa.String(length=10), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email'),
    sa.UniqueConstraint('sub')
    )
    op.create_table('app_setting',
    sa.Column('key', sa.String(length=50), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('household',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uuid', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('archived_at', sa.DateTime(), nullable=True),
    sa.Column('address_recipient', sa.String(length=120), nullable=True),
    sa.Column('address_street', sa.Text(), nullable=True),
    sa.Column('postal_code', sa.String(length=10), nullable=True),
    sa.Column('city', sa.String(length=80), nullable=True),
    sa.Column('country', sa.String(length=2), server_default='FR', nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('uuid')
    )
    with op.batch_alter_table('household', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_household_name'), ['name'], unique=False)

    op.create_table('oidc_client',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.String(length=64), nullable=False),
    sa.Column('secret_hash', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('redirect_uris', sa.JSON(), nullable=False),
    sa.Column('post_logout_redirect_uris', sa.JSON(), server_default='[]', nullable=False),
    sa.Column('backchannel_logout_uri', sa.Text(), nullable=True),
    sa.Column('first_party', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('active', sa.Boolean(), server_default='1', nullable=False),
    sa.Column('directory_access', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('client_id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('oidc_key',
    sa.Column('kid', sa.String(length=64), nullable=False),
    sa.Column('public_jwk', sa.JSON(), nullable=False),
    sa.Column('private_key', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('retired_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('kid')
    )
    op.create_table('role',
    sa.Column('key', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.Column('permissions', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('key'),
    sa.UniqueConstraint('name')
    )
    op.create_table('season',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=20), nullable=False),
    sa.Column('starts_on', sa.Date(), nullable=False),
    sa.Column('ends_on', sa.Date(), nullable=False),
    sa.CheckConstraint('ends_on > starts_on', name='ck_season_dates'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('label')
    )
    op.create_table('throttle_event',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('throttle_event', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_throttle_event_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_throttle_event_key_hash'), ['key_hash'], unique=False)

    op.create_table('account_access_group',
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['group_id'], ['access_group.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('account_id', 'group_id')
    )
    with op.batch_alter_table('account_access_group', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_account_access_group_group_id'), ['group_id'], unique=False)

    op.create_table('account_token',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('purpose', sa.String(length=20), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('proves_email', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('account_token', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_account_token_account_id'), ['account_id'], unique=False)

    op.create_table('login_session',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('sid', sa.String(length=36), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(), nullable=True),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sid')
    )
    with op.batch_alter_table('login_session', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_login_session_account_id'), ['account_id'], unique=False)

    op.create_table('notification',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('message', sa.String(length=500), nullable=False),
    sa.Column('link', sa.String(length=255), nullable=True),
    sa.Column('read', sa.Boolean(), nullable=False),
    sa.Column('dedup_key', sa.String(length=120), nullable=True),
    sa.Column('emailed', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedup_key')
    )
    with op.batch_alter_table('notification', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notification_account_id'), ['account_id'], unique=False)

    op.create_table('oidc_client_allowed_group',
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['oidc_client.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['group_id'], ['access_group.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('client_id', 'group_id')
    )
    with op.batch_alter_table('oidc_client_allowed_group', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oidc_client_allowed_group_group_id'), ['group_id'], unique=False)

    op.create_table('oidc_client_shared_group',
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['oidc_client.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['group_id'], ['access_group.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('client_id', 'group_id')
    )
    with op.batch_alter_table('oidc_client_shared_group', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oidc_client_shared_group_group_id'), ['group_id'], unique=False)

    op.create_table('oidc_code',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('redirect_uri', sa.Text(), nullable=False),
    sa.Column('scope', sa.String(length=255), nullable=False),
    sa.Column('nonce', sa.Text(), nullable=True),
    sa.Column('code_challenge', sa.String(length=128), nullable=False),
    sa.Column('code_challenge_method', sa.String(length=10), nullable=False),
    sa.Column('auth_time', sa.Integer(), nullable=False),
    sa.Column('sid', sa.String(length=36), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['client_id'], ['oidc_client.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('oidc_code', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oidc_code_account_id'), ['account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_oidc_code_client_id'), ['client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_oidc_code_expires_at'), ['expires_at'], unique=False)

    op.create_table('oidc_consent',
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('scope', sa.String(length=255), nullable=False),
    sa.Column('granted_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['client_id'], ['oidc_client.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('account_id', 'client_id')
    )
    with op.batch_alter_table('oidc_consent', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oidc_consent_client_id'), ['client_id'], unique=False)

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

    op.create_table('person',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('uuid', sa.String(length=36), nullable=False),
    sa.Column('civility', sa.String(length=10), nullable=True),
    sa.Column('first_name', sa.String(length=80), nullable=False),
    sa.Column('last_name', sa.String(length=80), nullable=False),
    sa.Column('birth_name', sa.String(length=80), nullable=True),
    sa.Column('sex', sa.String(length=1), nullable=True),
    sa.Column('birth_date', sa.Date(), nullable=True),
    sa.Column('birth_place', sa.String(length=120), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('emergency_contact', sa.String(length=255), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('archived_at', sa.DateTime(), nullable=True),
    sa.Column('account_id', sa.Integer(), nullable=True),
    sa.Column('address_recipient', sa.String(length=120), nullable=True),
    sa.Column('address_street', sa.Text(), nullable=True),
    sa.Column('postal_code', sa.String(length=10), nullable=True),
    sa.Column('city', sa.String(length=80), nullable=True),
    sa.Column('country', sa.String(length=2), server_default='FR', nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id'),
    sa.UniqueConstraint('uuid')
    )
    with op.batch_alter_table('person', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_person_email'), ['email'], unique=False)
        batch_op.create_index(batch_op.f('ix_person_last_name'), ['last_name'], unique=False)

    op.create_table('recovery_code',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('recovery_code', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_recovery_code_account_id'), ['account_id'], unique=False)

    op.create_table('role_access_group',
    sa.Column('role_key', sa.String(length=20), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['access_group.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_key'], ['role.key'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('role_key', 'group_id')
    )
    with op.batch_alter_table('role_access_group', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_role_access_group_group_id'), ['group_id'], unique=False)

    op.create_table('activity',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=50), nullable=False),
    sa.Column('message', sa.String(length=500), nullable=False),
    sa.Column('changes', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('target_account_id', sa.Integer(), nullable=True),
    sa.Column('person_id', sa.Integer(), nullable=True),
    sa.Column('household_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['household_id'], ['household.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['person_id'], ['person.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['target_account_id'], ['account.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('activity', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_activity_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_activity_household_id'), ['household_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_activity_person_id'), ['person_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_activity_target_account_id'), ['target_account_id'], unique=False)

    op.create_table('bank_account',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('household_id', sa.Integer(), nullable=False),
    sa.Column('holder_person_id', sa.Integer(), nullable=True),
    sa.Column('holder_name', sa.String(length=120), nullable=False),
    sa.Column('iban_ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('iban_last4', sa.String(length=4), nullable=False),
    sa.Column('iban_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('bic', sa.String(length=11), nullable=True),
    sa.Column('key_version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['holder_person_id'], ['person.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['household_id'], ['household.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('bank_account', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_bank_account_holder_person_id'), ['holder_person_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_bank_account_household_id'), ['household_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_bank_account_iban_fingerprint'), ['iban_fingerprint'], unique=False)

    op.create_table('external_ref',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('value', sa.String(length=40), nullable=False),
    sa.Column('person_id', sa.Integer(), nullable=True),
    sa.Column('household_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("kind IN ('student', 'household', 'teacher')", name='ck_external_ref_kind'),
    sa.CheckConstraint('(person_id IS NULL) <> (household_id IS NULL)', name='ck_external_ref_owner'),
    sa.ForeignKeyConstraint(['household_id'], ['household.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['person_id'], ['person.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kind', 'value')
    )
    with op.batch_alter_table('external_ref', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_external_ref_household_id'), ['household_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_external_ref_person_id'), ['person_id'], unique=False)

    op.create_table('household_member',
    sa.Column('household_id', sa.Integer(), nullable=False),
    sa.Column('person_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=10), nullable=False),
    sa.Column('guardian_rank', sa.Integer(), nullable=True),
    sa.Column('relationship', sa.String(length=40), nullable=True),
    sa.CheckConstraint("(role = 'guardian' AND guardian_rank IS NOT NULL AND guardian_rank IN (1, 2)) OR (role = 'dependent' AND guardian_rank IS NULL)", name='ck_household_member_role'),
    sa.ForeignKeyConstraint(['household_id'], ['household.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['person_id'], ['person.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('household_id', 'person_id'),
    sa.UniqueConstraint('household_id', 'guardian_rank')
    )
    with op.batch_alter_table('household_member', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_household_member_person_id'), ['person_id'], unique=False)

    op.create_table('membership',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('person_id', sa.Integer(), nullable=False),
    sa.Column('season_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('withdrawn_on', sa.Date(), nullable=True),
    sa.Column('category', sa.String(length=40), nullable=True),
    sa.Column('school_level', sa.String(length=40), nullable=True),
    sa.Column('school_class', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('active', 'withdrawn')", name='ck_membership_status'),
    sa.ForeignKeyConstraint(['person_id'], ['person.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['season_id'], ['season.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('person_id', 'season_id')
    )
    with op.batch_alter_table('membership', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_membership_season_id'), ['season_id'], unique=False)

    op.create_table('oidc_token',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('code_id', sa.String(length=64), nullable=True),
    sa.Column('scope', sa.String(length=255), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['client_id'], ['oidc_client.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['code_id'], ['oidc_code.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('oidc_token', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oidc_token_account_id'), ['account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_oidc_token_client_id'), ['client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_oidc_token_code_id'), ['code_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_oidc_token_expires_at'), ['expires_at'], unique=False)

    op.create_table('phone',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('person_id', sa.Integer(), nullable=True),
    sa.Column('household_id', sa.Integer(), nullable=True),
    sa.Column('number', sa.String(length=20), nullable=False),
    sa.Column('label', sa.String(length=40), nullable=True),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.CheckConstraint('(person_id IS NULL) <> (household_id IS NULL)', name='ck_phone_owner'),
    sa.ForeignKeyConstraint(['household_id'], ['household.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['person_id'], ['person.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('phone', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_phone_household_id'), ['household_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_phone_person_id'), ['person_id'], unique=False)

    op.create_table('teacher',
    sa.Column('person_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('started_on', sa.Date(), nullable=True),
    sa.Column('left_on', sa.Date(), nullable=True),
    sa.Column('work_email', sa.String(length=255), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['person_id'], ['person.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('person_id')
    )
    op.create_table('sepa_mandate',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('bank_account_id', sa.Integer(), nullable=False),
    sa.Column('rum', sa.String(length=35), nullable=False),
    sa.Column('signed_on', sa.Date(), nullable=False),
    sa.Column('first_debit_on', sa.Date(), nullable=True),
    sa.Column('last_debit_on', sa.Date(), nullable=True),
    sa.Column('revoked_on', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['bank_account_id'], ['bank_account.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('rum')
    )
    with op.batch_alter_table('sepa_mandate', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sepa_mandate_bank_account_id'), ['bank_account_id'], unique=False)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('sepa_mandate', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sepa_mandate_bank_account_id'))

    op.drop_table('sepa_mandate')
    op.drop_table('teacher')
    with op.batch_alter_table('phone', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_phone_person_id'))
        batch_op.drop_index(batch_op.f('ix_phone_household_id'))

    op.drop_table('phone')
    with op.batch_alter_table('oidc_token', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oidc_token_expires_at'))
        batch_op.drop_index(batch_op.f('ix_oidc_token_code_id'))
        batch_op.drop_index(batch_op.f('ix_oidc_token_client_id'))
        batch_op.drop_index(batch_op.f('ix_oidc_token_account_id'))

    op.drop_table('oidc_token')
    with op.batch_alter_table('membership', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_membership_season_id'))

    op.drop_table('membership')
    with op.batch_alter_table('household_member', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_household_member_person_id'))

    op.drop_table('household_member')
    with op.batch_alter_table('external_ref', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_external_ref_person_id'))
        batch_op.drop_index(batch_op.f('ix_external_ref_household_id'))

    op.drop_table('external_ref')
    with op.batch_alter_table('bank_account', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_bank_account_iban_fingerprint'))
        batch_op.drop_index(batch_op.f('ix_bank_account_household_id'))
        batch_op.drop_index(batch_op.f('ix_bank_account_holder_person_id'))

    op.drop_table('bank_account')
    with op.batch_alter_table('activity', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_activity_target_account_id'))
        batch_op.drop_index(batch_op.f('ix_activity_person_id'))
        batch_op.drop_index(batch_op.f('ix_activity_household_id'))
        batch_op.drop_index(batch_op.f('ix_activity_created_at'))

    op.drop_table('activity')
    with op.batch_alter_table('role_access_group', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_role_access_group_group_id'))

    op.drop_table('role_access_group')
    with op.batch_alter_table('recovery_code', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_recovery_code_account_id'))

    op.drop_table('recovery_code')
    with op.batch_alter_table('person', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_person_last_name'))
        batch_op.drop_index(batch_op.f('ix_person_email'))

    op.drop_table('person')
    with op.batch_alter_table('oidc_session', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oidc_session_sid'))
        batch_op.drop_index(batch_op.f('ix_oidc_session_client_id'))
        batch_op.drop_index(batch_op.f('ix_oidc_session_account_id'))

    op.drop_table('oidc_session')
    with op.batch_alter_table('oidc_logout', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oidc_logout_next_attempt_at'))
        batch_op.drop_index(batch_op.f('ix_oidc_logout_client_id'))

    op.drop_table('oidc_logout')
    with op.batch_alter_table('oidc_consent', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oidc_consent_client_id'))

    op.drop_table('oidc_consent')
    with op.batch_alter_table('oidc_code', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oidc_code_expires_at'))
        batch_op.drop_index(batch_op.f('ix_oidc_code_client_id'))
        batch_op.drop_index(batch_op.f('ix_oidc_code_account_id'))

    op.drop_table('oidc_code')
    with op.batch_alter_table('oidc_client_shared_group', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oidc_client_shared_group_group_id'))

    op.drop_table('oidc_client_shared_group')
    with op.batch_alter_table('oidc_client_allowed_group', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oidc_client_allowed_group_group_id'))

    op.drop_table('oidc_client_allowed_group')
    with op.batch_alter_table('notification', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notification_account_id'))

    op.drop_table('notification')
    with op.batch_alter_table('login_session', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_login_session_account_id'))

    op.drop_table('login_session')
    with op.batch_alter_table('account_token', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_account_token_account_id'))

    op.drop_table('account_token')
    with op.batch_alter_table('account_access_group', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_account_access_group_group_id'))

    op.drop_table('account_access_group')
    with op.batch_alter_table('throttle_event', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_throttle_event_key_hash'))
        batch_op.drop_index(batch_op.f('ix_throttle_event_created_at'))

    op.drop_table('throttle_event')
    op.drop_table('season')
    op.drop_table('role')
    op.drop_table('oidc_key')
    op.drop_table('oidc_client')
    with op.batch_alter_table('household', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_household_name'))

    op.drop_table('household')
    op.drop_table('app_setting')
    op.drop_table('account')
    op.drop_table('access_group')
    # ### end Alembic commands ###
    op.execute("DROP FUNCTION IF EXISTS fold(text)")
