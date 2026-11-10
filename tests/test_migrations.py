"""Migrations Alembic jouées sur une vraie base PostgreSQL, vide et jetable.

Les autres tests créent le schéma avec create_all : ils ne verraient pas un modèle sans migration, ni une contrainte
ou une cascade que la migration aurait oubliée.
"""
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask_migrate import downgrade, upgrade
from sqlalchemy.pool import NullPool

from app import create_app
from app.extensions import db

from .conftest import config_for, fresh_database, release

MIGRATIONS = str(Path(__file__).resolve().parent.parent / "migrations")
SEED = (
    "INSERT INTO account (id, sub, first_name, last_name, email, email_verified, role, active, must_change_password, "
    "created_at, updated_at) VALUES "
    "(1, 'sub-1', 'Élise', 'Lefèvre', 'elise@asso.test', false, 'admin', true, false, now(), now()), "
    "(2, 'sub-2', 'Paul', 'Martin', 'paul@asso.test', false, 'adherent', true, false, now(), now())",
    "INSERT INTO role (key, name, permissions) VALUES ('regie', 'Régie', '[\"activity.view\"]')",
    "INSERT INTO login_session (id, sid, account_id, created_at) VALUES ('jeton', 's1', 1, '2026-09-01 10:00:00')",
    "INSERT INTO activity (account_id, target_account_id, action, message, created_at) "
    "VALUES (1, 2, 'role.update', 'Rôle modifié', now())",
    "INSERT INTO notification (account_id, message, read, emailed, created_at) VALUES (2, 'Bonjour', false, false, now())",
    "INSERT INTO app_setting (key, value) VALUES ('name', 'Harmonie')",
)


@pytest.fixture
def migrated(tmp_path):
    with fresh_database() as url:
        app = create_app(config_for(tmp_path, SQLALCHEMY_DATABASE_URI=url))
        with app.app_context():
            engine = sa.create_engine(url, poolclass=NullPool)
            yield engine
            engine.dispose()
            release()


def run_sql(engine, *statements):
    """Exécute chaque requête dans sa propre transaction, comme le ferait une autre appli. Renvoie les lignes lues."""
    rows = []
    for statement in statements:
        with engine.begin() as connection:
            result = connection.exec_driver_sql(statement)
            rows.append(result.fetchall() if result.returns_rows else None)
    return rows


def assert_fails(engine, statement):
    with pytest.raises(sa.exc.IntegrityError):
        run_sql(engine, statement)


def test_single_head():
    config = AlembicConfig()
    config.set_main_option("script_location", MIGRATIONS)
    assert len(ScriptDirectory.from_config(config).get_heads()) == 1


def test_models_match_migrations(migrated):
    """Un modèle modifié sans migration passerait tous les autres tests, et casserait en production."""
    upgrade(MIGRATIONS)
    context = MigrationContext.configure(db.session.connection(), opts={"compare_type": True})
    assert compare_metadata(context, db.metadata) == []


def test_upgrade_then_downgrade_then_upgrade_again(migrated):
    upgrade(MIGRATIONS)
    run_sql(migrated, *SEED)
    downgrade(MIGRATIONS, revision="base")
    (tables,) = run_sql(migrated, "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' "
                                  "AND table_name <> 'alembic_version'")
    assert tables == [(0,)]
    upgrade(MIGRATIONS)
    run_sql(migrated, *SEED)
    accounts, settings = run_sql(migrated, "SELECT id, sub, email FROM account ORDER BY id",
                                 "SELECT value FROM app_setting WHERE key = 'name'")
    assert accounts == [(1, "sub-1", "elise@asso.test"), (2, "sub-2", "paul@asso.test")]
    assert settings == [("Harmonie",)]


def test_search_without_accents_is_installed(migrated):
    upgrade(MIGRATIONS)
    (rows,) = run_sql(migrated, "SELECT fold('Cœur d''Élodie LEFÈVRE'), fold(NULL)")
    assert rows == [("coeur d'elodie lefevre", None)]


def test_constraints_of_the_real_schema(migrated):
    upgrade(MIGRATIONS)
    run_sql(migrated, *SEED)
    for statement in (
        "INSERT INTO account (sub, first_name, last_name, email, email_verified, role, active, must_change_password, "
        "created_at, updated_at) VALUES ('autre', 'A', 'B', 'elise@asso.test', false, 'adherent', true, false, now(), now())",
        "INSERT INTO account (sub, first_name, last_name, email, email_verified, role, active, must_change_password, "
        "created_at, updated_at) VALUES ('sub-1', 'A', 'B', 'n@asso.test', false, 'adherent', true, false, now(), now())",
        "INSERT INTO login_session (id, sid, account_id, created_at) VALUES ('x', 's2', 99, now())",
        "INSERT INTO login_session (id, sid, account_id, created_at) VALUES ('y', 's1', 1, now())",
    ):
        assert_fails(migrated, statement)


def test_group_key_and_name_are_unique(migrated):
    upgrade(MIGRATIONS)
    run_sql(migrated, "INSERT INTO access_group (key, name) VALUES ('a', 'A')")
    assert_fails(migrated, "INSERT INTO access_group (key, name) VALUES ('a', 'B')")
    assert_fails(migrated, "INSERT INTO access_group (key, name) VALUES ('b', 'A')")


def test_deleting_an_account_keeps_the_journal_and_removes_what_belongs_to_it(migrated):
    upgrade(MIGRATIONS)
    run_sql(migrated, *SEED,
            "INSERT INTO account_token (id, account_id, purpose, proves_email, created_at, expires_at) "
            "VALUES ('h', 2, 'reset', false, now(), now())",
            "INSERT INTO recovery_code (id, account_id) VALUES ('c', 2)",
            "INSERT INTO access_group (id, key, name) VALUES (1, 'regie', 'Régie')",
            "INSERT INTO account_access_group (account_id, group_id) VALUES (2, 1)")
    run_sql(migrated, "DELETE FROM account WHERE id = 2")
    tokens, codes, members, groups, notifications, activity = run_sql(
        migrated, "SELECT count(*) FROM account_token", "SELECT count(*) FROM recovery_code",
        "SELECT count(*) FROM account_access_group", "SELECT count(*) FROM access_group",
        "SELECT count(*) FROM notification", "SELECT account_id, target_account_id FROM activity")
    assert (tokens, codes, members, groups, notifications) == ([(0,)], [(0,)], [(0,)], [(1,)], [(0,)])
    assert activity == [(1, None)]
    run_sql(migrated, "DELETE FROM account WHERE id = 1")
    assert run_sql(migrated, "SELECT count(*) FROM login_session", "SELECT account_id FROM activity") == [
        [(0,)], [(None,)]]


def test_oidc_tables_cascade_on_the_real_schema(migrated):
    """Une appli ou un compte supprimé ne laisse ni code, ni jeton, ni consentement, ni avis en attente."""
    upgrade(MIGRATIONS)
    run_sql(migrated, *SEED,
            "INSERT INTO oidc_client (id, client_id, secret_hash, name, redirect_uris, post_logout_redirect_uris, "
            "first_party, active, directory_access, created_at, updated_at) "
            "VALUES (1, 'c', 'h', 'Appli', '[]', '[]', false, true, false, now(), now())",
            "INSERT INTO oidc_code (id, client_id, account_id, redirect_uri, scope, code_challenge, "
            "code_challenge_method, auth_time, expires_at) VALUES ('code', 1, 2, 'u', 'openid', 'x', 'S256', 1, now())",
            "INSERT INTO oidc_token (id, client_id, account_id, code_id, scope, expires_at) "
            "VALUES ('jeton', 1, 2, 'code', 'openid', now())",
            "INSERT INTO oidc_consent (account_id, client_id, scope, granted_at) VALUES (2, 1, 'email', now())",
            "INSERT INTO oidc_session (sid, client_id, account_id, created_at) VALUES ('s', 1, 2, now())",
            "INSERT INTO oidc_logout (client_id, sub, sid, created_at, next_attempt_at, attempts) "
            "VALUES (1, 'sub-2', 's', now(), now(), 0)")
    assert_fails(migrated, "INSERT INTO oidc_session (sid, client_id, account_id, created_at) VALUES ('s', 1, 2, now())")
    run_sql(migrated, "DELETE FROM oidc_code WHERE id = 'code'")
    assert run_sql(migrated, "SELECT code_id FROM oidc_token") == [[(None,)]]  # le jeton survit à la purge du code
    run_sql(migrated, "DELETE FROM account WHERE id = 2")
    assert run_sql(migrated, "SELECT count(*) FROM oidc_session", "SELECT count(*) FROM oidc_logout") == [[(0,)], [(1,)]]
    run_sql(migrated, "DELETE FROM oidc_client WHERE id = 1")
    assert run_sql(migrated, "SELECT count(*) FROM oidc_logout", "SELECT count(*) FROM oidc_token") == [[(0,)], [(0,)]]


def test_directory_on_the_real_schema(migrated):
    """Contraintes et cascades de l'annuaire, telles que la migration les crée."""
    upgrade(MIGRATIONS)
    run_sql(migrated, *SEED,
            "INSERT INTO person (id, uuid, first_name, last_name, country, account_id, created_at, updated_at) "
            "VALUES (1, 'u1', 'Paul', 'Martin', 'FR', 2, now(), now()), (2, 'u2', 'Zoé', 'Martin', 'FR', NULL, now(), now())",
            "INSERT INTO household (id, uuid, name, country, created_at, updated_at) "
            "VALUES (1, 'h1', 'Famille Martin', 'FR', now(), now())",
            "INSERT INTO household_member (household_id, person_id, role, guardian_rank) "
            "VALUES (1, 1, 'guardian', 1), (1, 2, 'dependent', NULL)",
            "INSERT INTO phone (person_id, number, position) VALUES (2, '+33612345678', 0)",
            "INSERT INTO season (id, label, starts_on, ends_on) VALUES (1, '2026-2027', '2026-09-01', '2027-08-31')",
            "INSERT INTO membership (person_id, season_id, status, created_at, updated_at) "
            "VALUES (2, 1, 'active', now(), now())",
            "INSERT INTO teacher (person_id, status) VALUES (1, 'CDI')",
            "INSERT INTO bank_account (id, household_id, holder_person_id, holder_name, iban_ciphertext, iban_last4, "
            "iban_fingerprint, key_version, created_at, updated_at) "
            "VALUES (1, 1, 1, 'Paul', '\\x00', '0189', 'f', 1, now(), now())",
            "INSERT INTO sepa_mandate (bank_account_id, rum, signed_on) VALUES (1, 'R1', '2026-09-01')",
            "UPDATE activity SET person_id = 2, household_id = 1")
    for statement in (
        "INSERT INTO person (uuid, first_name, last_name, country, account_id, created_at, updated_at) "
        "VALUES ('u3', 'A', 'B', 'FR', 2, now(), now())",  # un compte, une seule fiche
        "INSERT INTO household_member (household_id, person_id, role, guardian_rank) VALUES (1, 2, 'guardian', 1)",
        "UPDATE household_member SET guardian_rank = NULL WHERE person_id = 1",  # un responsable a un rang
        "UPDATE household_member SET guardian_rank = 1 WHERE person_id = 2",  # une personne à charge n'en a pas
        "INSERT INTO phone (person_id, household_id, number, position) VALUES (1, 1, '+33600000000', 0)",
        "INSERT INTO phone (number, position) VALUES ('+33600000000', 0)",
        "INSERT INTO membership (person_id, season_id, status, created_at, updated_at) VALUES (2, 1, 'active', now(), now())",
        "INSERT INTO membership (person_id, season_id, status, created_at, updated_at) VALUES (1, 1, 'autre', now(), now())",
        "INSERT INTO season (label, starts_on, ends_on) VALUES ('x', '2027-09-01', '2027-01-01')",
        "INSERT INTO external_ref (kind, value, person_id) VALUES ('autre', '1', 1)",
        "INSERT INTO sepa_mandate (bank_account_id, rum, signed_on) VALUES (1, 'R1', '2026-09-01')",
        "DELETE FROM season WHERE id = 1",  # elle a des adhésions
    ):
        assert_fails(migrated, statement)
    run_sql(migrated, "DELETE FROM account WHERE id = 2")
    assert run_sql(migrated, "SELECT account_id FROM person WHERE id = 1") == [[(None,)]]
    run_sql(migrated, "DELETE FROM person WHERE id = 2")
    assert run_sql(migrated, "SELECT count(*) FROM phone", "SELECT count(*) FROM membership",
                   "SELECT count(*) FROM household_member", "SELECT person_id FROM activity") == [
        [(0,)], [(0,)], [(1,)], [(None,)]]
    run_sql(migrated, "DELETE FROM household WHERE id = 1")
    assert run_sql(migrated, "SELECT count(*) FROM bank_account", "SELECT count(*) FROM sepa_mandate",
                   "SELECT household_id FROM activity", "SELECT count(*) FROM person") == [
        [(0,)], [(0,)], [(None,)], [(1,)]]
    run_sql(migrated, "DELETE FROM person WHERE id = 1")
    assert run_sql(migrated, "SELECT count(*) FROM teacher") == [[(0,)]]


CATALOG = (
    "INSERT INTO person (id, uuid, first_name, last_name, country, created_at, updated_at) "
    "VALUES (1, 'u1', 'Élodie', 'Martin', 'FR', now(), now())",
    "INSERT INTO teacher (person_id, status) VALUES (1, 'CDI')",
    "INSERT INTO season (id, label, starts_on, ends_on) VALUES (1, '2026-2027', '2026-09-01', '2027-08-31')",
    "INSERT INTO discipline (id, name) VALUES (1, 'Flûte')",
    "INSERT INTO level (id, name) VALUES (1, 'Cycle 1')",
    "INSERT INTO site (id, name) VALUES (1, 'École')",
    "INSERT INTO room (id, site_id, name) VALUES (1, 1, 'Salle A')",
    "INSERT INTO course (id, season_id, discipline_id, level_id, teacher_id, room_id, weekday, starts_at, ends_at, "
    "capacity) VALUES (1, 1, 1, 1, 1, 1, 2, '17:00', '18:00', 8)",
    "INSERT INTO teacher_discipline (season_id, teacher_id, discipline_id) VALUES (1, 1, 1)",
)


def test_catalog_on_the_real_schema(migrated):
    """Contraintes et cascades du catalogue, telles que la migration les crée."""
    upgrade(MIGRATIONS)
    run_sql(migrated, *CATALOG)
    assert run_sql(migrated, "SELECT marked_full FROM course") == [[(False,)]]
    for statement in (
        "INSERT INTO discipline (name) VALUES ('Flûte')",
        "INSERT INTO room (site_id, name) VALUES (1, 'Salle A')",
        "INSERT INTO course (season_id, discipline_id, weekday) VALUES (1, 1, 7)",
        "INSERT INTO course (season_id, discipline_id, starts_at) VALUES (1, 1, '17:00')",
        "INSERT INTO course (season_id, discipline_id, starts_at, ends_at) VALUES (1, 1, '18:00', '17:00')",
        "INSERT INTO course (season_id, discipline_id, capacity) VALUES (1, 1, 0)",
        "INSERT INTO teacher_discipline (season_id, teacher_id, discipline_id) VALUES (1, 1, 1)",
        "INSERT INTO teacher_discipline (season_id, teacher_id, discipline_id) VALUES (1, 99, 1)",
        # Ce qui a servi ne se supprime pas.
        "DELETE FROM season WHERE id = 1",
        "DELETE FROM discipline WHERE id = 1",
        "DELETE FROM level WHERE id = 1",
        "DELETE FROM room WHERE id = 1",
        "DELETE FROM site WHERE id = 1",
    ):
        assert_fails(migrated, statement)
    # Un profil de professeur retiré libère ses cours et efface ses disciplines.
    run_sql(migrated, "DELETE FROM teacher WHERE person_id = 1")
    assert run_sql(migrated, "SELECT teacher_id FROM course", "SELECT count(*) FROM teacher_discipline") == [
        [(None,)], [(0,)]]


def test_saved_manager_role_gets_the_catalogue_rights(migrated):
    upgrade(MIGRATIONS, revision="a0000001initial")
    run_sql(migrated, "INSERT INTO role (key, name, permissions) VALUES "
                      "('gestionnaire', 'Gestionnaire', '[\"directory.view\"]'), ('regie', 'Régie', '[\"activity.view\"]')")
    upgrade(MIGRATIONS)
    (rows,) = run_sql(migrated, "SELECT key, permissions::text FROM role ORDER BY key")
    assert rows == [("gestionnaire", '["directory.view", "catalog.view", "catalog.edit"]'),
                    ("regie", '["activity.view"]')]
    downgrade(MIGRATIONS, revision="a0000001initial")
    (rows,) = run_sql(migrated, "SELECT key, permissions::text FROM role ORDER BY key")
    assert rows == [("gestionnaire", '["directory.view"]'), ("regie", '["activity.view"]')]
