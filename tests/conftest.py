import os
import secrets
from contextlib import contextmanager
from datetime import datetime
from functools import partial

import pyotp
import pytest
from flask import g
from flask.testing import FlaskClient
from hypothesis import settings
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.pool import NullPool
from werkzeug.security import generate_password_hash

import app.models as models
from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import Account, Role
from app.services import totp

# HYPOTHESIS_PROFILE=deep pytest tests/test_properties.py : 1000 cas par propriété.
settings.register_profile("deep", max_examples=1000, deadline=None)
settings.register_profile("default", max_examples=60, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

PASSWORD = "motdepasse"
TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
_HEADS = []


class Client(FlaskClient):
    """Se comporte comme de vraies requêtes, alors que le test garde un contexte d'application ouvert.

    Dans ce cas Flask réutilise le même `g` et la même session SQLAlchemy d'une requête à l'autre. Sans ces deux
    remises à zéro, une session révoquée resterait connectée, et une route qui oublie son commit passerait inaperçue.
    """

    def open(self, *args, **kwargs):
        g.pop("_login_user", None)
        g.pop("branding", None)
        response = super().open(*args, **kwargs)
        db.session.rollback()
        return response


@pytest.fixture(autouse=True)
def fast_hashing(monkeypatch):
    """scrypt est volontairement lent : inutile en test."""
    monkeypatch.setattr(models, "generate_password_hash", partial(generate_password_hash, method="pbkdf2:sha256:1"))


def stamp_head():
    """create_all produit le schéma le plus récent : la base est donc « à jour » pour la page de santé."""
    if not _HEADS:
        from app.services.health import migration_heads

        _HEADS.extend(migration_heads()[1])
    db.session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
    db.session.execute(text("DELETE FROM alembic_version"))
    for revision in _HEADS:
        db.session.execute(text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision})
    db.session.commit()


# ---------------------------------------------------------------------------
# Base PostgreSQL de test (TEST_DATABASE_URL, par défaut « adherents_test » sur le socket local)
# ---------------------------------------------------------------------------


def _worker_database(url):
    """Avec pytest-xdist (`-n auto`), chaque processus a sa propre base, créée au besoin : `adherents_test_gw0`…
    Sans quoi les processus videraient les tables les uns des autres."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return url
    target = make_url(url)
    name = f"{target.database}_{worker}"
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as connection:
        if not connection.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}).scalar():
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    admin.dispose()
    return target.set(database=name).render_as_string(hide_password=False)


TEST_DATABASE_URL = TestConfig.SQLALCHEMY_DATABASE_URI = _worker_database(TestConfig.SQLALCHEMY_DATABASE_URI)
os.environ["TEST_DATABASE_URL"] = TEST_DATABASE_URL  # pour les commandes lancées dans un autre processus
_SCHEMA_READY = []


def _empty_tables(connection):
    tables = ", ".join(f'"{table.name}"' for table in db.metadata.sorted_tables)
    connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    connection.execute(text("DROP TABLE IF EXISTS alembic_version"))


@pytest.fixture(scope="session", autouse=True)
def blank_test_database():
    """Début de session : schéma vierge, avant qu'une appli ne démarre (elle vérifie les IBAN déjà en base)."""
    engine = create_engine(TEST_DATABASE_URL, poolclass=NullPool)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    engine.dispose()


def reset_database():
    """Schéma créé une fois par session, puis tables vidées avant chaque test : bien plus rapide que de tout
    recréer à chaque fois."""
    if not _SCHEMA_READY:
        db.create_all()  # crée aussi l'extension unaccent et fold() (app/extensions.py)
        _SCHEMA_READY.append(True)
    _empty_tables(db.session)
    db.session.commit()


def release():
    """Fin d'un test : vide les tables (l'appli suivante ne doit rien trouver en démarrant) et ferme les connexions,
    sinon PostgreSQL finit par les refuser."""
    db.session.rollback()
    if _SCHEMA_READY and db.engine.url.database == make_url(TEST_DATABASE_URL).database:
        _empty_tables(db.session)
        db.session.commit()
    db.session.remove()
    db.engine.dispose()


@contextmanager
def fresh_database():
    """Base vide et jetable, pour ce qui ne doit pas partager la base de test : serveurs lancés dans un thread,
    migrations, redémarrages de l'appli. Renvoie son adresse."""
    url = make_url(TEST_DATABASE_URL)
    name = f"{url.database}_{secrets.token_hex(4)}"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture(scope="module")
def module_database():
    """Base jetable pour les serveurs des tests de bout en bout (un par module)."""
    with fresh_database() as url:
        yield url


def config_for(tmp_path, **overrides):
    return type("Cfg", (TestConfig,), {"DATA_DIR": tmp_path, "UPLOAD_DIR": tmp_path / "uploads", **overrides})


@pytest.fixture
def app(tmp_path):
    app = create_app(config_for(tmp_path))
    app.test_client_class = Client
    with app.app_context():
        reset_database()
        stamp_head()
        yield app
        release()


@pytest.fixture
def client(app):
    return app.test_client()


def enroll_totp(account, secret=TOTP_SECRET):
    account.totp_secret = totp.encrypt(secret)
    account.totp_enabled_at = datetime.now()
    db.session.commit()


def enroll(account):
    """enroll_totp qui renvoie le compte : `login(client, enroll(make_account(...)))`."""
    enroll_totp(account)
    return account


def totp_code(secret=TOTP_SECRET):
    return pyotp.TOTP(secret).now()


def make_account(role=Role.ADHERENT, email=None, active=True, password=PASSWORD, first_name=None, **fields):
    account = Account(
        first_name=first_name or role.capitalize(),
        last_name="Test",
        email=email or f"{role}@asso.test",
        role=role,
        active=active,
        **fields,
    )
    account.set_password(password)
    db.session.add(account)
    db.session.commit()
    # Comme en vrai : l'administrateur et le gestionnaire (annuaire) n'entrent pas sans double authentification.
    # Les rôles sur mesure restent sans, pour pouvoir tester l'obligation de la configurer.
    if role in (Role.ADMIN, Role.GESTIONNAIRE):
        enroll_totp(account)
    return account


def login(client, account, password=PASSWORD, **kwargs):
    """Connexion complète, code de double authentification compris si le compte en a un."""
    follow = kwargs.pop("follow_redirects", False)
    response = client.post("/connexion", data={"email": account.email, "password": password}, **kwargs)
    if response.status_code == 302 and response.location.endswith("/connexion/verification"):
        account.totp_last_counter = None  # plusieurs connexions dans les mêmes 30 secondes : pas un rejeu à tester ici
        db.session.commit()
        response = client.post("/connexion/verification", data={"code": totp_code()})
    return client.get(response.location, follow_redirects=True) if follow and response.status_code == 302 else response


@pytest.fixture
def accounts(app):
    return {role: make_account(role) for role in Role.BUILTIN}


@pytest.fixture
def admin(accounts, client):
    login(client, accounts[Role.ADMIN])
    return accounts[Role.ADMIN]


@pytest.fixture
def member(accounts, client):
    login(client, accounts[Role.ADHERENT])
    return accounts[Role.ADHERENT]
