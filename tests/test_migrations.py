"""Migrations Alembic jouées sur une base remplie.

Les autres tests créent le schéma avec create_all : ils ne verraient pas une migration qui, sous SQLite, recrée une
table référencée par des clés étrangères (refusé avec PRAGMA foreign_keys=ON), ni un modèle sans migration.
"""
import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask_migrate import downgrade, upgrade

from app import create_app
from app.extensions import db

from .conftest import config_for

MIGRATIONS = str(Path(__file__).resolve().parent.parent / "migrations")
SEED = (
    "INSERT INTO account (id, sub, first_name, last_name, email, role, active, must_change_password) VALUES "
    "(1, 'sub-1', 'Élise', 'Lefèvre', 'elise@asso.test', 'admin', 1, 0), "
    "(2, 'sub-2', 'Paul', 'Martin', 'paul@asso.test', 'adherent', 1, 0)",
    "INSERT INTO role (key, name, permissions) VALUES ('regie', 'Régie', '[\"activity.view\"]')",
    "INSERT INTO login_session (id, account_id, created_at) VALUES ('jeton', 1, '2026-09-01 10:00:00')",
    "INSERT INTO activity (account_id, target_account_id, action, message) VALUES (1, 2, 'role.update', 'Rôle modifié')",
    "INSERT INTO notification (account_id, message, read, emailed) VALUES (2, 'Bonjour', 0, 0)",
    "INSERT INTO app_setting (key, value) VALUES ('name', 'Harmonie')",
    "INSERT INTO throttle_event (key_hash, created_at) VALUES ('abc', '2026-09-01 10:00:00')",
)


@pytest.fixture
def migrated(tmp_path):
    path = tmp_path / "adherents.db"
    app = create_app(config_for(tmp_path, SQLALCHEMY_DATABASE_URI=f"sqlite:///{path}"))
    with app.app_context():
        yield path


def run_sql(path, *statements):
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        rows = [connection.execute(statement).fetchall() for statement in statements]
        connection.commit()
        return rows
    finally:
        connection.close()


def revisions():
    config = AlembicConfig()
    config.set_main_option("script_location", MIGRATIONS)
    return [script.revision for script in reversed(list(ScriptDirectory.from_config(config).walk_revisions()))]


def assert_data_intact(path):
    accounts, roles, fk_errors, settings = run_sql(
        path, "SELECT id, sub, email FROM account ORDER BY id", "SELECT key FROM role", "PRAGMA foreign_key_check",
        "SELECT value FROM app_setting WHERE key = 'name'")
    assert accounts == [(1, "sub-1", "elise@asso.test"), (2, "sub-2", "paul@asso.test")]
    assert roles == [("regie",)] and settings == [("Harmonie",)] and fk_errors == []


def test_single_head():
    config = AlembicConfig()
    config.set_main_option("script_location", MIGRATIONS)
    assert len(ScriptDirectory.from_config(config).get_heads()) == 1


def test_models_match_migrations(migrated):
    """Un modèle modifié sans migration passerait tous les autres tests, et casserait en production."""
    upgrade(MIGRATIONS)
    context = MigrationContext.configure(db.session.connection(), opts={"compare_type": True})
    assert compare_metadata(context, db.metadata) == []


def test_filled_database_survives_every_upgrade(migrated):
    """Remplit la base à la toute première révision, puis applique les suivantes une par une."""
    steps = revisions()
    upgrade(MIGRATIONS, revision=steps[0])
    run_sql(migrated, *SEED)
    for revision in steps[1:]:
        upgrade(MIGRATIONS, revision=revision)
        assert_data_intact(migrated)
    assert_data_intact(migrated)


def test_last_migration_can_be_undone_on_a_filled_database(migrated):
    upgrade(MIGRATIONS)
    run_sql(migrated, *SEED)
    steps = revisions()
    if len(steps) > 1:
        downgrade(MIGRATIONS, revision="-1")
        upgrade(MIGRATIONS)
        assert_data_intact(migrated)
    else:  # une seule révision : la défaire supprime les tables, et on doit pouvoir repartir de zéro
        downgrade(MIGRATIONS, revision="base")
        upgrade(MIGRATIONS)
        run_sql(migrated, *SEED)
        assert_data_intact(migrated)


def test_oidc_tables_cascade_on_the_real_schema(migrated):
    """Une appli ou un compte supprimé ne laisse ni code, ni jeton, ni consentement derrière lui."""
    upgrade(MIGRATIONS)
    run_sql(migrated, *SEED)
    run_sql(migrated,
            "INSERT INTO oidc_client (id, client_id, secret_hash, name, redirect_uris, first_party, active) "
            "VALUES (1, 'c', 'h', 'Appli', '[]', 0, 1)",
            "INSERT INTO oidc_code (id, client_id, account_id, redirect_uri, scope, code_challenge, "
            "code_challenge_method, auth_time, expires_at) VALUES ('code', 1, 2, 'u', 'openid', 'x', 'S256', 1, '2026-09-01')",
            "INSERT INTO oidc_token (id, client_id, account_id, code_id, scope, expires_at) "
            "VALUES ('jeton', 1, 2, 'code', 'openid', '2026-09-01')",
            "INSERT INTO oidc_consent (account_id, client_id, scope, granted_at) VALUES (2, 1, 'email', '2026-09-01')")
    run_sql(migrated, "DELETE FROM oidc_code WHERE id = 'code'")
    assert run_sql(migrated, "SELECT code_id FROM oidc_token") == [[(None,)]]  # le jeton survit à la purge de son code
    run_sql(migrated, "DELETE FROM oidc_client WHERE id = 1")
    assert run_sql(migrated, "SELECT COUNT(*) FROM oidc_token", "SELECT COUNT(*) FROM oidc_consent") == [[(0,)], [(0,)]]


def test_constraints_of_the_real_schema(migrated):
    upgrade(MIGRATIONS)
    run_sql(migrated, *SEED)
    for statement in (
        "INSERT INTO account (sub, first_name, last_name, email, role, active, must_change_password) "
        "VALUES ('autre', 'A', 'B', 'elise@asso.test', 'adherent', 1, 0)",
        "INSERT INTO account (sub, first_name, last_name, email, role, active, must_change_password) "
        "VALUES ('sub-1', 'A', 'B', 'nouveau@asso.test', 'adherent', 1, 0)",
        "INSERT INTO login_session (id, account_id, created_at) VALUES ('x', 99, '2026-09-01')",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            run_sql(migrated, statement)


def test_deleting_an_account_keeps_the_journal_but_not_its_sessions(migrated):
    upgrade(MIGRATIONS)
    run_sql(migrated, *SEED)
    run_sql(migrated, "DELETE FROM account WHERE id = 1")
    sessions, notifications, activity = run_sql(
        migrated, "SELECT count(*) FROM login_session", "SELECT count(*) FROM notification",
        "SELECT account_id, target_account_id FROM activity")
    assert sessions == [(0,)] and notifications == [(1,)]
    assert activity == [(None, 2)]


def test_new_columns_default_safely_on_existing_accounts(migrated):
    """Un compte d'avant la phase 2 n'a ni adresse vérifiée, ni double authentification."""
    steps = revisions()
    upgrade(MIGRATIONS, revision=steps[0])
    run_sql(migrated, *SEED)
    upgrade(MIGRATIONS)
    (rows,) = run_sql(migrated, "SELECT email_verified, totp_secret, totp_enabled_at FROM account")
    assert rows == [(0, None, None), (0, None, None)]


def test_deleting_an_account_removes_its_links_codes_and_memberships(migrated):
    upgrade(MIGRATIONS)
    run_sql(migrated, *SEED,
            "INSERT INTO account_token (id, account_id, purpose, proves_email, created_at, expires_at) "
            "VALUES ('h', 2, 'reset', 0, '2026-09-01', '2026-09-02')",
            "INSERT INTO recovery_code (id, account_id) VALUES ('c', 2)",
            "INSERT INTO access_group (id, key, name) VALUES (1, 'regie', 'Régie')",
            "INSERT INTO account_access_group (account_id, group_id) VALUES (2, 1)")
    run_sql(migrated, "DELETE FROM account WHERE id = 2")
    tokens, codes, members, kept = run_sql(
        migrated, "SELECT count(*) FROM account_token", "SELECT count(*) FROM recovery_code",
        "SELECT count(*) FROM account_access_group", "SELECT count(*) FROM access_group")
    assert (tokens, codes, members, kept) == ([(0,)], [(0,)], [(0,)], [(1,)])


def test_group_key_and_name_are_unique(migrated):
    upgrade(MIGRATIONS)
    run_sql(migrated, "INSERT INTO access_group (key, name) VALUES ('a', 'A')")
    for statement in ("INSERT INTO access_group (key, name) VALUES ('a', 'B')",
                      "INSERT INTO access_group (key, name) VALUES ('b', 'A')"):
        with pytest.raises(sqlite3.IntegrityError):
            run_sql(migrated, statement)


def test_open_sessions_get_their_own_sid_on_upgrade(migrated):
    """Phase 4 : chaque session déjà ouverte reçoit un sid distinct, et reste ouverte."""
    steps = revisions()
    upgrade(MIGRATIONS, revision="a0000003oidc")
    run_sql(migrated, *SEED, "INSERT INTO login_session (id, account_id, created_at) VALUES ('jeton-2', 2, '2026-09-02')")
    upgrade(MIGRATIONS)
    (rows,) = run_sql(migrated, "SELECT id, sid FROM login_session ORDER BY id")
    assert [row[0] for row in rows] == ["jeton", "jeton-2"]
    assert all(row[1] for row in rows) and rows[0][1] != rows[1][1]
    assert steps[-1] == "a0000004deconnexion"
    with pytest.raises(sqlite3.IntegrityError):
        run_sql(migrated, f"INSERT INTO login_session (id, sid, account_id, created_at) "
                          f"VALUES ('jeton-3', '{rows[0][1]}', 1, '2026-09-03')")
    (clients,) = run_sql(migrated, "SELECT count(*) FROM oidc_client")
    assert clients == [(0,)]


def test_logout_tables_cascade_on_the_real_schema(migrated):
    upgrade(MIGRATIONS, revision="a0000003oidc")
    run_sql(migrated, *SEED, "INSERT INTO oidc_client (id, client_id, secret_hash, name, redirect_uris, first_party, "
                             "active) VALUES (1, 'c', 'h', 'Appli', '[]', 0, 1)")
    upgrade(MIGRATIONS)
    (lists,) = run_sql(migrated, "SELECT post_logout_redirect_uris, backchannel_logout_uri FROM oidc_client")
    assert lists == [("[]", None)]  # une appli d'avant la phase 4 n'a aucune adresse de déconnexion
    run_sql(migrated,
            "INSERT INTO oidc_session (sid, client_id, account_id, created_at) VALUES ('s', 1, 2, '2026-09-01')",
            "INSERT INTO oidc_logout (client_id, sub, sid, created_at, next_attempt_at) "
            "VALUES (1, 'sub-2', 's', '2026-09-01', '2026-09-01')")
    with pytest.raises(sqlite3.IntegrityError):  # une seule ligne par session et par appli
        run_sql(migrated, "INSERT INTO oidc_session (sid, client_id, account_id, created_at) "
                          "VALUES ('s', 1, 2, '2026-09-01')")
    run_sql(migrated, "DELETE FROM account WHERE id = 2")
    assert run_sql(migrated, "SELECT count(*) FROM oidc_session", "SELECT count(*) FROM oidc_logout") == [[(0,)], [(1,)]]
    run_sql(migrated, "DELETE FROM oidc_client WHERE id = 1")
    assert run_sql(migrated, "SELECT count(*) FROM oidc_logout") == [[(0,)]]
