import os
from functools import partial

import pytest
from flask import g
from flask.testing import FlaskClient
from hypothesis import settings
from sqlalchemy import text
from werkzeug.security import generate_password_hash

import app.models as models
from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import Account, Role

# HYPOTHESIS_PROFILE=deep pytest tests/test_properties.py : 1000 cas par propriété.
settings.register_profile("deep", max_examples=1000, deadline=None)
settings.register_profile("default", max_examples=60, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

PASSWORD = "motdepasse"
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


def config_for(tmp_path, **overrides):
    return type("Cfg", (TestConfig,), {"DATA_DIR": tmp_path, "UPLOAD_DIR": tmp_path / "uploads", **overrides})


@pytest.fixture
def app(tmp_path):
    app = create_app(config_for(tmp_path))
    app.test_client_class = Client
    with app.app_context():
        db.create_all()
        stamp_head()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def make_account(role=Role.ADHERENT, email=None, active=True, password=PASSWORD, first_name=None, **fields):
    account = Account(first_name=first_name or role.capitalize(), last_name="Test",
                      email=email or f"{role}@asso.test", role=role, active=active, **fields)
    account.set_password(password)
    db.session.add(account)
    db.session.commit()
    return account


def login(client, account, password=PASSWORD, **kwargs):
    return client.post("/connexion", data={"email": account.email, "password": password}, **kwargs)


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
