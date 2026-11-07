"""Liste des comptes d'une appli : bouton d'export dans « Applications », et /oidc/comptes pour l'appli elle-même.

Elle ne doit rien dire de plus que ce que l'appli recevrait à la connexion de chacun.
"""
import base64
import json
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import AccessGroup, Activity, OidcClient, OidcConsent, Role
from app.services import activity, oidc

from .conftest import login, make_account
from .test_oidc import SECRET_OF, make_client


@pytest.fixture
def setup(app):
    members, regie, other = (AccessGroup(key="membres", name="Membres"), AccessGroup(key="regie", name="Régie"),
                             AccessGroup(key="autre", name="Autre"))
    db.session.add_all([members, regie, other])
    db.session.commit()
    elise = make_account(email="elise@asso.test", first_name="Élise", email_verified=True)
    elise.groups = [members, regie]
    paul = make_account(email="paul@asso.test", first_name="Paul")
    paul.groups = [members]
    olaf = make_account(email="olaf@asso.test", first_name="Olaf")
    olaf.groups = [other]
    coupe = make_account(email="coupe@asso.test", first_name="Coupé", active=False)
    coupe.groups = [members]
    db.session.commit()
    inventaire = make_client(allowed=[members], shared=[members, regie], name="Inventaire")
    inventaire.directory_access = True
    db.session.commit()
    return {"members": members, "elise": elise, "paul": paul, "app": inventaire}


def basic(client_id, secret):
    return {"Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()}


def fetch(http, application, secret=None):
    return http.get("/oidc/comptes", headers=basic(application.client_id,
                                                  secret if secret is not None else SECRET_OF[application.client_id]))


def test_first_party_app_gets_every_account_that_may_enter(client, setup):
    response = fetch(client, setup["app"])
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    data = response.get_json()
    assert data["issuer"] == "http://localhost:8001" and data["application"] == "Inventaire"
    assert [row["email"] for row in data["accounts"]] == ["elise@asso.test", "paul@asso.test"]
    assert data["accounts"][0] == {"sub": setup["elise"].sub, "email": "elise@asso.test", "email_verified": True,
                                   "first_name": "Élise", "last_name": "Test", "groups": ["membres", "regie"]}


def test_nothing_secret_is_ever_listed(client, setup):
    text = fetch(client, setup["app"]).get_data(as_text=True)
    for word in ("password", "totp", "session", "role", setup["elise"].password_hash):
        assert word not in text


def test_third_party_app_sees_only_consenting_people_and_what_they_accepted(client, setup):
    third = make_client(allowed=[setup["members"]], shared=[setup["members"]], first_party=False, name="Tierce")
    third.directory_access = True
    db.session.add(OidcConsent(account_id=setup["paul"].id, client_id=third.id, scope="email"))
    db.session.commit()
    data = fetch(client, third).get_json()
    assert data["accounts"] == [{"sub": setup["paul"].sub, "email": "paul@asso.test", "email_verified": False}]


def test_role_groups_count(client, setup):
    from app.services import roles
    role = roles.get(Role.GESTIONNAIRE)
    role.groups = [setup["members"]]
    db.session.commit()
    gestion = make_account(Role.GESTIONNAIRE, email="gestion@asso.test")
    assert gestion.sub in [row["sub"] for row in fetch(client, setup["app"]).get_json()["accounts"]]


def test_closed_by_default(client, setup):
    setup["app"].directory_access = False
    db.session.commit()
    response = fetch(client, setup["app"])
    assert response.status_code == 403 and response.get_json() == {"error": "access_denied"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer x"}, {"Authorization": "Basic !!!"},
                                     basic("inconnu", "x")])
def test_without_valid_credentials(client, setup, headers):
    response = client.get("/oidc/comptes", headers=headers)
    assert response.status_code == 401 and response.headers["WWW-Authenticate"].startswith("Basic")
    assert response.get_json() == {"error": "invalid_client"}


def test_wrong_secret(client, setup):
    assert fetch(client, setup["app"], secret="faux").status_code == 401


def test_closed_app_cannot_read_it(client, setup):
    setup["app"].active = False
    db.session.commit()
    assert fetch(client, setup["app"]).status_code == 401


def test_secret_guessing_is_throttled(client, setup):
    for _ in range(10):
        fetch(client, setup["app"], secret="faux")
    assert fetch(client, setup["app"]).status_code == 429  # même avec le bon secret, le temps de la pénalité


def test_each_read_is_journaled_and_purged_like_logins(client, setup):
    fetch(client, setup["app"])
    entry = db.session.scalar(db.select(Activity).filter_by(action="oidc.directory"))
    assert entry.message == "Inventaire a récupéré la liste de ses comptes (2)"
    assert activity.purge_expired_logins(datetime.now() + timedelta(days=400)) >= 1
    assert db.session.scalar(db.select(Activity).filter_by(action="oidc.directory")) is None


def test_post_is_not_allowed(client, setup):
    assert client.post("/oidc/comptes", headers=basic(setup["app"].client_id, "x")).status_code == 405


# ---------------------------------------------------------------------------
# Bouton « Exporter les comptes »
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_http(app):
    http = app.test_client()
    login(http, make_account(Role.ADMIN, email="chef@asso.test"))
    return http


def test_export_button_downloads_the_same_list(admin_http, client, setup):
    response = admin_http.post(f"/applications/{setup['app'].id}/comptes")
    assert response.status_code == 200 and response.mimetype == "application/json"
    assert response.headers["Content-Disposition"] == 'attachment; filename="comptes-inventaire.json"'
    assert response.headers["Cache-Control"] == "no-store"
    assert json.loads(response.get_data(as_text=True)) == fetch(client, setup["app"]).get_json()
    assert db.session.scalar(db.select(Activity).filter_by(action="oidc.export")) is not None


def test_export_works_even_when_the_app_cannot_read_it_itself(admin_http, setup):
    setup["app"].directory_access = False
    db.session.commit()
    assert admin_http.post(f"/applications/{setup['app'].id}/comptes").status_code == 200


def test_file_name_is_safe(admin_http, setup):
    setup["app"].name = 'Régie "son" / lumière'
    db.session.commit()
    disposition = admin_http.post(f"/applications/{setup['app'].id}/comptes").headers["Content-Disposition"]
    assert disposition == 'attachment; filename="comptes-regie-son-lumiere.json"'


def test_detail_page_offers_the_button_and_explains_the_endpoint(admin_http, setup):
    page = admin_http.get(f"/applications/{setup['app'].id}").get_data(as_text=True)
    assert "Exporter les comptes (JSON)" in page and "http://localhost:8001/oidc/comptes" in page


def test_form_saves_and_journals_directory_access(admin_http, setup):
    application = setup["app"]
    admin_http.post(f"/applications/{application.id}/modifier", data={
        "name": application.name, "redirect_uris": "\n".join(application.redirect_uris), "first_party": "y",
        "allowed_groups": [g.id for g in application.allowed_groups],
        "shared_groups": [g.id for g in application.shared_groups]})
    assert db.session.get(OidcClient, application.id).directory_access is False
    entry = db.session.scalar(db.select(Activity).filter_by(action="client.update"))
    assert entry.changes["liste des comptes"] == [True, False]
    assert oidc.directory(application)["accounts"]  # le bouton, lui, marche toujours
