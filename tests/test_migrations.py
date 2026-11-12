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
    (tables,) = run_sql(
        migrated,
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' "
        "AND table_name <> 'alembic_version'",
    )
    assert tables == [(0,)]
    upgrade(MIGRATIONS)
    run_sql(migrated, *SEED)
    accounts, settings = run_sql(
        migrated, "SELECT id, sub, email FROM account ORDER BY id", "SELECT value FROM app_setting WHERE key = 'name'"
    )
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
    run_sql(
        migrated,
        *SEED,
        "INSERT INTO account_token (id, account_id, purpose, proves_email, created_at, expires_at) "
        "VALUES ('h', 2, 'reset', false, now(), now())",
        "INSERT INTO recovery_code (id, account_id) VALUES ('c', 2)",
        "INSERT INTO access_group (id, key, name) VALUES (1, 'regie', 'Régie')",
        "INSERT INTO account_access_group (account_id, group_id) VALUES (2, 1)",
    )
    run_sql(migrated, "DELETE FROM account WHERE id = 2")
    tokens, codes, members, groups, notifications, activity = run_sql(
        migrated,
        "SELECT count(*) FROM account_token",
        "SELECT count(*) FROM recovery_code",
        "SELECT count(*) FROM account_access_group",
        "SELECT count(*) FROM access_group",
        "SELECT count(*) FROM notification",
        "SELECT account_id, target_account_id FROM activity",
    )
    assert (tokens, codes, members, groups, notifications) == ([(0,)], [(0,)], [(0,)], [(1,)], [(0,)])
    assert activity == [(1, None)]
    run_sql(migrated, "DELETE FROM account WHERE id = 1")
    assert run_sql(migrated, "SELECT count(*) FROM login_session", "SELECT account_id FROM activity") == [
        [(0,)],
        [(None,)],
    ]


def test_oidc_tables_cascade_on_the_real_schema(migrated):
    """Une appli ou un compte supprimé ne laisse ni code, ni jeton, ni consentement, ni avis en attente."""
    upgrade(MIGRATIONS)
    run_sql(
        migrated,
        *SEED,
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
        "VALUES (1, 'sub-2', 's', now(), now(), 0)",
    )
    assert_fails(
        migrated, "INSERT INTO oidc_session (sid, client_id, account_id, created_at) VALUES ('s', 1, 2, now())"
    )
    run_sql(migrated, "DELETE FROM oidc_code WHERE id = 'code'")
    assert run_sql(migrated, "SELECT code_id FROM oidc_token") == [[(None,)]]  # le jeton survit à la purge du code
    run_sql(migrated, "DELETE FROM account WHERE id = 2")
    assert run_sql(migrated, "SELECT count(*) FROM oidc_session", "SELECT count(*) FROM oidc_logout") == [
        [(0,)],
        [(1,)],
    ]
    run_sql(migrated, "DELETE FROM oidc_client WHERE id = 1")
    assert run_sql(migrated, "SELECT count(*) FROM oidc_logout", "SELECT count(*) FROM oidc_token") == [[(0,)], [(0,)]]


def test_directory_on_the_real_schema(migrated):
    """Contraintes et cascades de l'annuaire, telles que la migration les crée."""
    upgrade(MIGRATIONS)
    run_sql(
        migrated,
        *SEED,
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
        "UPDATE activity SET person_id = 2, household_id = 1",
    )
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
    assert run_sql(
        migrated,
        "SELECT count(*) FROM phone",
        "SELECT count(*) FROM membership",
        "SELECT count(*) FROM household_member",
        "SELECT person_id FROM activity",
    ) == [[(0,)], [(0,)], [(1,)], [(None,)]]
    run_sql(migrated, "DELETE FROM household WHERE id = 1")
    assert run_sql(
        migrated,
        "SELECT count(*) FROM bank_account",
        "SELECT count(*) FROM sepa_mandate",
        "SELECT household_id FROM activity",
        "SELECT count(*) FROM person",
    ) == [[(0,)], [(0,)], [(None,)], [(1,)]]
    run_sql(migrated, "DELETE FROM person WHERE id = 1")
    assert run_sql(migrated, "SELECT count(*) FROM teacher") == [[(0,)]]


CATALOG = (
    "INSERT INTO person (id, uuid, first_name, last_name, country, created_at, updated_at) "
    "VALUES (1, 'u1', 'Élodie', 'Martin', 'FR', now(), now())",
    "INSERT INTO teacher (person_id, status) VALUES (1, 'CDI')",
    "INSERT INTO season (id, label, starts_on, ends_on) VALUES (1, '2026-2027', '2026-09-01', '2027-08-31')",
    "INSERT INTO discipline_type (id, name) VALUES (1, 'Instrument')",
    "INSERT INTO discipline (id, name, type_id) VALUES (1, 'Flûte', 1)",
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
        "INSERT INTO discipline_type (name) VALUES ('Instrument')",
        "INSERT INTO discipline (name, type_id) VALUES ('Piano', 99)",
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
        "DELETE FROM discipline_type WHERE id = 1",
        "DELETE FROM level WHERE id = 1",
        "DELETE FROM room WHERE id = 1",
        "DELETE FROM site WHERE id = 1",
    ):
        assert_fails(migrated, statement)
    # Un profil de professeur retiré libère ses cours et efface ses disciplines.
    run_sql(migrated, "DELETE FROM teacher WHERE person_id = 1")
    assert run_sql(migrated, "SELECT teacher_id FROM course", "SELECT count(*) FROM teacher_discipline") == [
        [(None,)],
        [(0,)],
    ]


def test_typed_categories_become_discipline_types(migrated):
    """Les types saisis du temps de la liste fixe deviennent des lignes de la nouvelle liste, et reviennent tels
    quels si on recule."""
    upgrade(MIGRATIONS, revision="a0000005tarifs")
    run_sql(
        migrated,
        "INSERT INTO discipline (id, name, category) VALUES (1, 'Flûte', 'Instrument'), (2, 'Piano', 'Instrument'), "
        "(3, 'Chorale', 'Pratique collective'), (4, 'Jardin', NULL), (5, 'Atelier', ' '), (6, 'Éveil 5 ans', 'Éveil ')",
    )
    upgrade(MIGRATIONS)
    types, disciplines = run_sql(
        migrated,
        "SELECT name, position FROM discipline_type ORDER BY position",
        "SELECT d.name, t.name FROM discipline d LEFT JOIN discipline_type t ON t.id = d.type_id ORDER BY d.id",
    )
    # L'ordre alphabétique dépend de la collation de la base : on vérifie les noms et les rangs séparément.
    assert sorted(name for name, _ in types) == ["Instrument", "Pratique collective", "Éveil"]
    assert [position for _, position in types] == [10, 20, 30]
    assert disciplines == [
        ("Flûte", "Instrument"),
        ("Piano", "Instrument"),
        ("Chorale", "Pratique collective"),
        ("Jardin", None),
        ("Atelier", None),
        ("Éveil 5 ans", "Éveil"),
    ]
    downgrade(MIGRATIONS, revision="a0000005tarifs")
    assert run_sql(migrated, "SELECT name, category FROM discipline ORDER BY id") == [
        [
            ("Flûte", "Instrument"),
            ("Piano", "Instrument"),
            ("Chorale", "Pratique collective"),
            ("Jardin", None),
            ("Atelier", None),
            ("Éveil 5 ans", "Éveil"),
        ]
    ]


def test_saved_manager_role_gets_the_catalogue_rights(migrated):
    upgrade(MIGRATIONS, revision="a0000001initial")
    run_sql(
        migrated,
        "INSERT INTO role (key, name, permissions) VALUES "
        "('gestionnaire', 'Gestionnaire', '[\"directory.view\"]'), ('regie', 'Régie', '[\"activity.view\"]')",
    )
    upgrade(MIGRATIONS, revision="a0000002catalogue")
    (rows,) = run_sql(migrated, "SELECT key, permissions::text FROM role ORDER BY key")
    assert rows == [
        ("gestionnaire", '["directory.view", "catalog.view", "catalog.edit"]'),
        ("regie", '["activity.view"]'),
    ]
    downgrade(MIGRATIONS, revision="a0000001initial")
    (rows,) = run_sql(migrated, "SELECT key, permissions::text FROM role ORDER BY key")
    assert rows == [("gestionnaire", '["directory.view"]'), ("regie", '["activity.view"]')]


def test_registration_on_the_real_schema(migrated):
    """Contraintes et cascades des inscriptions, telles que la migration les crée."""
    upgrade(MIGRATIONS)
    run_sql(
        migrated,
        *SEED,
        *CATALOG,
        "INSERT INTO household (id, uuid, name, country, created_at, updated_at) "
        "VALUES (1, 'h1', 'Famille Martin', 'FR', now(), now())",
        "INSERT INTO membership (id, person_id, season_id, status, created_at, updated_at) "
        "VALUES (1, 1, 1, 'active', now(), now())",
        "INSERT INTO campaign (id, season_id, title, opens_at, closes_at, allow_new, created_at, updated_at) "
        "VALUES (1, 1, 'Réinscriptions', '2027-06-01', '2027-07-01', false, now(), now())",
        "INSERT INTO campaign_consent (id, campaign_id, label, text, scope, required, position) "
        "VALUES (1, 1, 'Image', 'Photos', 'student', false, 0)",
        "INSERT INTO dossier (id, uuid, campaign_id, household_id, account_id, status, data, created_at, updated_at) "
        "VALUES (1, 'd1', 1, 1, 2, 'validated', '{}', now(), now())",
        "INSERT INTO enrollment (id, membership_id, course_id, status, dossier_id, created_at, updated_at) "
        "VALUES (1, 1, 1, 'active', 1, now(), now())",
        "INSERT INTO consent (person_id, season_id, label, text, granted, given_at, dossier_id) "
        "VALUES (1, 1, 'Image', 'Photos', true, now(), 1)",
    )
    assert run_sql(migrated, "SELECT permissions::text FROM role WHERE key = 'regie'") == [[('["activity.view"]',)]]
    for statement in (
        "INSERT INTO campaign (season_id, title, opens_at, closes_at, allow_new, created_at, updated_at) "
        "VALUES (1, 'x', '2027-07-01', '2027-06-01', false, now(), now())",
        "INSERT INTO campaign_consent (campaign_id, label, text, scope, required, position) "
        "VALUES (1, 'x', 'x', 'autre', false, 0)",
        "INSERT INTO dossier (uuid, campaign_id, household_id, status, data, created_at, updated_at) "
        "VALUES ('d2', 1, 1, 'draft', '{}', now(), now())",  # un dossier par foyer et par campagne
        "INSERT INTO dossier (uuid, campaign_id, status, data, created_at, updated_at) "
        "VALUES ('d3', 1, 'perdu', '{}', now(), now())",
        "INSERT INTO enrollment (membership_id, course_id, status, created_at, updated_at) "
        "VALUES (1, 1, 'active', now(), now())",
        "INSERT INTO consent (person_id, household_id, season_id, label, text, granted, given_at) "
        "VALUES (1, 1, 1, 'x', 'x', true, now())",
        "INSERT INTO consent (season_id, label, text, granted, given_at) VALUES (1, 'x', 'x', true, now())",
        "DELETE FROM course WHERE id = 1",  # il a des élèves
        "DELETE FROM campaign WHERE id = 1",  # elle a des dossiers
    ):
        assert_fails(migrated, statement)
    run_sql(
        migrated,
        "INSERT INTO dossier (uuid, campaign_id, status, data, created_at, updated_at) "
        "VALUES ('d4', 1, 'draft', '{}', now(), now()), ('d5', 1, 'draft', '{}', now(), now())",
    )
    run_sql(migrated, "DELETE FROM account WHERE id = 2", "DELETE FROM household WHERE id = 1")
    assert run_sql(migrated, "SELECT household_id, account_id FROM dossier WHERE id = 1") == [[(None, None)]]
    run_sql(migrated, "DELETE FROM person WHERE id = 1")
    assert run_sql(migrated, "SELECT count(*) FROM enrollment", "SELECT count(*) FROM consent") == [[(0,)], [(0,)]]


def test_saved_manager_role_gets_the_registration_rights(migrated):
    upgrade(MIGRATIONS, revision="a0000002catalogue")
    run_sql(migrated, "INSERT INTO role (key, name, permissions) VALUES ('gestionnaire', 'Gestionnaire', '[]')")
    upgrade(MIGRATIONS, revision="a0000003inscriptions")
    assert run_sql(migrated, "SELECT permissions::text FROM role") == [
        [('["registration.view", "registration.manage"]',)]
    ]
    downgrade(MIGRATIONS, revision="a0000002catalogue")
    assert run_sql(migrated, "SELECT permissions::text FROM role") == [[("[]",)]]


FEES = (
    "INSERT INTO household (id, uuid, name, country, created_at, updated_at) "
    "VALUES (1, 'h1', 'Famille Martin', 'FR', now(), now())",
    "INSERT INTO membership (id, person_id, season_id, status, created_at, updated_at) "
    "VALUES (1, 1, 1, 'active', now(), now())",
    "INSERT INTO regie (id, name) VALUES (1, 'EMHN')",
    "INSERT INTO fee_item (id, season_id, label, regie_id, price, created_at, updated_at) "
    "VALUES (1, 1, 'Formule 30 min', 1, 42000, now(), now())",
    "UPDATE course SET fee_item_id = 1",
    "INSERT INTO fee_rule (id, season_id, position, name, conditions, target, effect, created_at, updated_at) "
    "VALUES (1, 1, 0, 'Offert', '[]', '{}', '{\"kind\": \"free\"}', now(), now())",
    "INSERT INTO fee_season (season_id, engine, qf_bands, resident_cities) VALUES (1, '{}', '[]', '[]')",
    "INSERT INTO household_season (household_id, season_id, quotient, tags, updated_at) "
    "VALUES (1, 1, 450, '[]', now())",
    "INSERT INTO fee_pick (id, household_id, person_id, item_id, created_at) VALUES (1, 1, 1, 1, now())",
    "INSERT INTO assessment (id, household_id, season_id, status, computed_total, computed_at, warnings, "
    "created_at, updated_at) VALUES (1, 1, 1, 'proposed', 42000, now(), '[]', now(), now())",
    "INSERT INTO assessment_line (id, assessment_id, position, kind, person_id, person_name, item_id, label, base, "
    "computed, amount, steps) VALUES (1, 1, 0, 'item', 1, 'Élodie Martin', 1, 'Formule 30 min', 42000, 42000, "
    "42000, '[]')",
)


def test_fees_on_the_real_schema(migrated):
    """Contraintes et cascades des tarifs, telles que la migration les crée."""
    upgrade(MIGRATIONS)
    run_sql(migrated, *CATALOG, *FEES)
    assert run_sql(migrated, "SELECT fee_tags::text, (SELECT active FROM fee_rule) FROM membership") == [
        [("[]", False)]
    ]
    for statement in (
        "INSERT INTO regie (name) VALUES ('EMHN')",
        "INSERT INTO fee_item (season_id, label, price, created_at, updated_at) "
        "VALUES (1, 'Formule 30 min', 1, now(), now())",  # même libellé dans la saison
        "INSERT INTO fee_item (season_id, label, price, created_at, updated_at) VALUES (1, 'x', -1, now(), now())",
        "INSERT INTO fee_rule (season_id, position, name, conditions, target, effect, created_at, updated_at) "
        "VALUES (1, -1, 'x', '[]', '{}', '{}', now(), now())",
        "INSERT INTO assessment (household_id, season_id, status, computed_total, computed_at, warnings, created_at, "
        "updated_at) VALUES (1, 1, 'proposed', 0, now(), '[]', now(), now())",  # une par foyer et par saison
        "UPDATE assessment SET status = 'payee'",
        "UPDATE assessment_line SET kind = 'autre'",
        "UPDATE assessment_line SET corrected_at = now()",  # correction sans motif
        # Ce qui a servi ne se supprime pas.
        "DELETE FROM fee_item WHERE id = 1",
        "DELETE FROM regie WHERE id = 1",
        "DELETE FROM household WHERE id = 1",
        "DELETE FROM season WHERE id = 1",
    ):
        assert_fails(migrated, statement)
    # Une fiche supprimée : ses éléments pris à part partent, la ligne de cotisation reste avec son nom.
    run_sql(migrated, "DELETE FROM person WHERE id = 1")
    assert run_sql(migrated, "SELECT count(*) FROM fee_pick", "SELECT person_id, person_name FROM assessment_line") == [
        [(0,)],
        [(None, "Élodie Martin")],
    ]
    run_sql(migrated, "DELETE FROM assessment")
    assert run_sql(migrated, "SELECT count(*) FROM assessment_line") == [[(0,)]]


def test_saved_manager_role_gets_the_fee_rights(migrated):
    upgrade(MIGRATIONS, revision="a0000004conservation")
    run_sql(
        migrated,
        "INSERT INTO role (key, name, permissions) VALUES ('gestionnaire', 'Gestionnaire', "
        "'[\"catalog.view\"]'), ('regie', 'Régie', '[\"activity.view\"]')",
    )
    upgrade(MIGRATIONS, revision="a0000005tarifs")
    (rows,) = run_sql(migrated, "SELECT key, permissions::text FROM role ORDER BY key")
    assert rows == [
        ("gestionnaire", '["catalog.view", "fee.view", "fee.edit", "fee.assess"]'),
        ("regie", '["activity.view"]'),
    ]
    downgrade(MIGRATIONS, revision="a0000004conservation")
    (rows,) = run_sql(migrated, "SELECT key, permissions::text FROM role ORDER BY key")
    assert rows == [("gestionnaire", '["catalog.view"]'), ("regie", '["activity.view"]')]


def test_enrollment_dates_and_review_on_the_real_schema(migrated):
    """Les inscriptions existantes prennent la date de leur enregistrement ; dates et transferts sont contrôlés."""
    upgrade(MIGRATIONS, revision="a0000006types")
    run_sql(
        migrated,
        *SEED,
        *CATALOG,
        "INSERT INTO household (id, uuid, name, country, created_at, updated_at) "
        "VALUES (1, 'h1', 'Famille Martin', 'FR', now(), now())",
        "INSERT INTO membership (id, person_id, season_id, status, created_at, updated_at) "
        "VALUES (1, 1, 1, 'active', now(), now())",
        "INSERT INTO enrollment (id, membership_id, course_id, status, withdrawn_on, created_at, updated_at) "
        "VALUES (1, 1, 1, 'withdrawn', '2026-10-01', '2026-11-05 10:00', now())",
        "INSERT INTO assessment (id, household_id, season_id, status, computed_total, computed_at, warnings, "
        "created_at, updated_at) VALUES (1, 1, 1, 'proposed', 0, now(), '[]', now(), now())",
        "INSERT INTO role (key, name, permissions) VALUES ('gestionnaire', 'Gestionnaire', '[\"fee.view\"]')",
    )
    upgrade(MIGRATIONS, revision="a0000007effectifs")
    # Abandonnée avant d'avoir été enregistrée (saisie a posteriori) : la date d'entrée ne la dépasse pas.
    assert run_sql(
        migrated,
        "SELECT enrolled_on::text, replaces_id FROM enrollment",
        "SELECT review_reasons::text FROM assessment",
        "SELECT permissions::text FROM role WHERE key = 'gestionnaire'",
    ) == [[("2026-10-01", None)], [("[]",)], [('["fee.view", "enrollment.edit"]',)]]
    run_sql(
        migrated,
        "INSERT INTO course (id, season_id, discipline_id, marked_full, created_at, updated_at) "
        "VALUES (9, 1, 1, false, now(), now())",
        "INSERT INTO enrollment (id, membership_id, course_id, status, replaces_id, created_at, updated_at) "
        "VALUES (2, 1, 9, 'active', 1, now(), now())",
    )
    assert run_sql(migrated, "SELECT enrolled_on = CURRENT_DATE FROM enrollment WHERE id = 2") == [[(True,)]]
    for statement in (
        "UPDATE enrollment SET withdrawn_on = enrolled_on - 1 WHERE id = 2",
        "UPDATE enrollment SET replaces_id = 2 WHERE id = 2",
        "UPDATE enrollment SET enrolled_on = NULL WHERE id = 2",
    ):
        assert_fails(migrated, statement)
    run_sql(migrated, "DELETE FROM enrollment WHERE id = 1")
    assert run_sql(migrated, "SELECT replaces_id FROM enrollment WHERE id = 2") == [[(None,)]]
    downgrade(MIGRATIONS, revision="a0000006types")
    assert run_sql(migrated, "SELECT permissions::text FROM role WHERE key = 'gestionnaire'") == [[('["fee.view"]',)]]


def test_individual_courses_on_the_real_schema(migrated):
    """Aucun cours ne devient individuel d'office ; heure de passage et durée sont contrôlées."""
    upgrade(MIGRATIONS, revision="a0000007effectifs")
    run_sql(
        migrated,
        *SEED,
        *CATALOG,
        "UPDATE course SET capacity = NULL",
        "INSERT INTO membership (id, person_id, season_id, status, created_at, updated_at) "
        "VALUES (1, 1, 1, 'active', now(), now())",
        "INSERT INTO enrollment (id, membership_id, course_id, status, created_at, updated_at) "
        "VALUES (1, 1, 1, 'active', now(), now())",
    )
    upgrade(MIGRATIONS)
    assert run_sql(migrated, "SELECT individual FROM course") == [[(False,)]]
    run_sql(migrated, "UPDATE enrollment SET slot_minutes = 30", "UPDATE enrollment SET slot_starts_at = '17:00'")
    for statement in (
        "UPDATE enrollment SET slot_minutes = 0",
        "UPDATE enrollment SET slot_minutes = NULL",  # une heure sans durée
    ):
        assert_fails(migrated, statement)
    downgrade(MIGRATIONS, revision="a0000007effectifs")


def test_waitlist_on_the_real_schema(migrated):
    upgrade(MIGRATIONS)
    run_sql(
        migrated,
        *SEED,
        *CATALOG,
        "INSERT INTO membership (id, person_id, season_id, status, created_at, updated_at) "
        "VALUES (1, 1, 1, 'active', now(), now())",
        "INSERT INTO waitlist_entry (id, course_id, membership_id, position, status, created_at) "
        "VALUES (1, 1, 1, 1, 'waiting', now())",
    )
    for statement in (
        "INSERT INTO waitlist_entry (course_id, membership_id, position, status, created_at) "
        "VALUES (1, 1, 2, 'waiting', now())",  # une entrée par élève et par cours
        "UPDATE waitlist_entry SET status = 'perdu'",
        "DELETE FROM course WHERE id = 1",  # il a une liste d'attente
    ):
        assert_fails(migrated, statement)
    run_sql(migrated, "DELETE FROM membership WHERE id = 1")
    assert run_sql(migrated, "SELECT count(*) FROM waitlist_entry") == [[(0,)]]
    downgrade(MIGRATIONS, revision="a0000008individuel")
    assert run_sql(migrated, "SELECT to_regclass('waitlist_entry') IS NULL") == [[(True,)]]


def test_lessons_on_the_real_schema(migrated):
    upgrade(MIGRATIONS, revision="a0000009attente")
    run_sql(migrated, *SEED, *CATALOG, "INSERT INTO role (key, name, permissions) VALUES ('gestionnaire', 'G', '[]')")
    upgrade(MIGRATIONS)
    assert run_sql(migrated, "SELECT permissions::text FROM role WHERE key = 'gestionnaire'") == [
        [('["lesson.manage"]',)]
    ]
    run_sql(
        migrated,
        "INSERT INTO season_break (id, season_id, label, starts_on, ends_on) VALUES (1, 1, 'Toussaint', '2026-10-17', '2026-11-01')",
        "INSERT INTO lesson (id, course_id, scheduled_on, day, starts_at, ends_at, room_id, substitute_id, status, created_at) "
        "VALUES (1, 1, '2026-10-07', '2026-10-07', '17:00', '18:00', 1, 1, 'planned', now())",
        "UPDATE account SET calendar_token = 'abc' WHERE id = 1",
    )
    assert run_sql(migrated, "SELECT edited, revision FROM lesson") == [[(False, 0)]]
    for statement in (
        "INSERT INTO season_break (season_id, label, starts_on, ends_on) VALUES (1, 'x', '2026-10-02', '2026-10-01')",
        "INSERT INTO lesson (course_id, scheduled_on, day, starts_at, ends_at, status, created_at) "
        "VALUES (1, '2026-10-07', '2026-10-08', '17:00', '18:00', 'planned', now())",  # un créneau, une séance
        "UPDATE lesson SET ends_at = '16:00'",
        "UPDATE lesson SET status = 'perdue'",
        "UPDATE account SET calendar_token = 'abc' WHERE id = 2",
    ):
        assert_fails(migrated, statement)
    run_sql(
        migrated,
        "UPDATE course SET room_id = NULL",
        "DELETE FROM room WHERE id = 1",
        "DELETE FROM teacher_discipline",
        "DELETE FROM teacher WHERE person_id = 1",
    )
    assert run_sql(migrated, "SELECT room_id, substitute_id FROM lesson") == [[(None, None)]]
    run_sql(
        migrated, "DELETE FROM teacher_discipline", "DELETE FROM course WHERE id = 1", "DELETE FROM season WHERE id = 1"
    )
    assert run_sql(migrated, "SELECT count(*) FROM lesson", "SELECT count(*) FROM season_break") == [[(0,)], [(0,)]]
    downgrade(MIGRATIONS, revision="a0000009attente")
    assert run_sql(migrated, "SELECT permissions::text FROM role WHERE key = 'gestionnaire'") == [[("[]",)]]
