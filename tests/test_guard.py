"""Garde-fous des données personnelles : limite de lecture, inactivité, fusion d'un compte, journal, en-têtes, fichiers."""
import os
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Activity, LoginSession, Notification, Person, Role, Teacher
from app.services import health
from app.services.activity import diff

from .conftest import login, make_account
from .test_annuaire import family, person  # noqa: F401


# ---------------------------------------------------------------------------
# Limite de lecture
# ---------------------------------------------------------------------------

def test_mass_reading_is_stopped_and_admins_are_told(client, app, manager_client):
    app.config["DIRECTORY_READ_LIMIT"] = 5
    for _ in range(5):
        assert client.get("/annuaire").status_code == 200
    assert client.get("/annuaire").status_code == 429
    assert client.get("/annuaire/foyers").status_code == 429  # toutes les pages de l'annuaire
    assert client.get("/").status_code == 200  # le reste de l'appli reste ouvert
    alerts = db.session.scalars(db.select(Notification).where(Notification.message.contains("Lecture massive"))).all()
    assert len(alerts) == 1 and alerts[0].account.role == Role.ADMIN  # une seule alerte malgré deux refus
    entries = db.session.scalars(db.select(Activity).filter_by(action="directory.throttle")).all()
    assert len(entries) == 1 and entries[0].target_account_id == manager_client.id


def test_teacher_pages_count_too(client, app, admin):
    app.config["DIRECTORY_READ_LIMIT"] = 3
    for _ in range(3):
        client.get("/professeurs")
    assert client.get("/professeurs").status_code == 429


def test_reading_limit_is_per_account(app, accounts):
    app.config["DIRECTORY_READ_LIMIT"] = 2
    first, second = app.test_client(), app.test_client()
    login(first, accounts[Role.GESTIONNAIRE])
    login(second, accounts[Role.ADMIN])
    for _ in range(3):
        first.get("/annuaire")
    assert first.get("/annuaire").status_code == 429 and second.get("/annuaire").status_code == 200


def test_writes_are_not_counted(client, app, manager_client, family):
    app.config["DIRECTORY_READ_LIMIT"] = 1
    for _ in range(3):
        client.post(f"/annuaire/foyers/{family['home'].id}/telephones", data={"number": "0235000000"})
    assert client.get("/annuaire").status_code == 200


@pytest.fixture
def manager_client(client, accounts):
    login(client, accounts[Role.GESTIONNAIRE])
    return accounts[Role.GESTIONNAIRE]


# ---------------------------------------------------------------------------
# Inactivité
# ---------------------------------------------------------------------------

def _age_sessions(account, hours):
    for login_session in db.session.scalars(db.select(LoginSession).filter_by(account_id=account.id)):
        login_session.last_seen_at = datetime.now() - timedelta(hours=hours)
    db.session.commit()


def test_idle_session_with_personal_data_is_closed(client, manager_client):
    assert client.get("/annuaire").status_code == 200
    _age_sessions(manager_client, 3)
    response = client.get("/annuaire")
    assert response.status_code == 302 and "/connexion" in response.location
    assert db.session.scalar(db.select(db.func.count(LoginSession.id)).filter_by(account_id=manager_client.id)) == 0
    assert db.session.scalar(db.select(Activity).filter_by(action="auth.idle")) is not None
    assert "inactivité" in client.get("/connexion").get_data(as_text=True)


def test_recent_activity_keeps_the_session(client, manager_client):
    _age_sessions(manager_client, 1)
    assert client.get("/annuaire").status_code == 200


def test_member_sessions_are_not_cut(client, member):
    """Un adhérent sans accès aux données des autres garde « rester connecté »."""
    _age_sessions(member, 48)
    assert client.get("/mon-compte").status_code == 200


def test_idle_timeout_can_be_disabled(client, app, manager_client):
    app.config["IDLE_TIMEOUT_HOURS"] = 0
    _age_sessions(manager_client, 48)
    assert client.get("/annuaire").status_code == 200


# ---------------------------------------------------------------------------
# Fusion d'une fiche qui a un compte
# ---------------------------------------------------------------------------

def test_merging_a_record_with_an_account_needs_the_accounts_right(client, manager_client, family):
    copy = person("Anne", "Martin", account=make_account(email="anne@asso.test"))
    db.session.commit()
    page = client.get(f"/annuaire/personnes/{family['anne'].id}/fusionner?avec={copy.id}").get_data(as_text=True)
    assert "droit de gérer les comptes" in page and "<button type=\"submit\">Fusionner" not in page
    client.post(f"/annuaire/personnes/{family['anne'].id}/fusionner", data={"avec": copy.id})
    assert db.session.get(Person, copy.id) is not None  # refusé côté serveur aussi


def test_admin_can_merge_a_record_with_an_account(client, admin, family):
    copy = person("Anne", "Martin", account=make_account(email="anne@asso.test"))
    db.session.commit()
    client.post(f"/annuaire/personnes/{family['anne'].id}/fusionner", data={"avec": copy.id})
    assert db.session.get(Person, family["anne"].id).account.email == "anne@asso.test"


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def test_personal_fields_never_reach_the_journal(client, admin, family):
    anne = family["anne"]
    client.post(f"/annuaire/personnes/{anne.id}/modifier", data={
        "first_name": "Anne", "last_name": "Martin", "email": "anne@asso.test", "country": "FR",
        "birth_place": "Lisieux", "notes": "dossier social", "emergency_contact": "Sœur 0600000000"})
    entry = db.session.scalar(db.select(Activity).filter_by(action="person.update"))
    assert set(entry.changes) == {"birth_place", "notes", "emergency_contact"}
    assert all(values == ["(masqué)", "(masqué)"] for values in entry.changes.values())


def test_diff_can_mask_a_field_for_one_call():
    teacher = Teacher(status="CDD")
    assert diff(teacher, ("status",), {"status": "CDI"}, mask=("status",)) == {"status": ["(masqué)", "(masqué)"]}
    assert teacher.status == "CDI"


# ---------------------------------------------------------------------------
# En-têtes et fichiers
# ---------------------------------------------------------------------------

def test_isolation_headers_everywhere(client):
    headers = client.get("/connexion").headers
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert headers["X-Robots-Tag"] == "noindex, nofollow"
    assert "Strict-Transport-Security" not in headers  # en HTTP, HSTS n'a pas de sens


def test_hsts_over_https(client):
    headers = client.get("/connexion", base_url="https://localhost").headers
    assert headers["Strict-Transport-Security"] == "max-age=31536000"


@pytest.mark.skipif(os.name != "posix", reason="droits POSIX")
def test_health_warns_about_world_readable_data(app, tmp_path):
    os.chmod(tmp_path, 0o755)
    assert health.check_permissions()["status"] == health.WARN
    os.chmod(tmp_path, 0o700)
    assert health.check_permissions()["status"] == health.OK


# ---------------------------------------------------------------------------
# Double authentification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("right", ["directory.view", "bank.view", "teacher.manage"])
def test_reading_personal_data_requires_two_factor(client, app, right):
    """Un rôle qui lit les données des autres (annuaire, mineurs compris) doit protéger son compte."""
    db.session.add(Role(key="lecteur", name="Lecteur", permissions=[right]))
    db.session.commit()
    reader = make_account("lecteur", email="lecteur@asso.test")
    login(client, reader)
    response = client.get("/annuaire" if right != "teacher.manage" else "/professeurs")
    assert response.status_code == 302 and response.location.endswith("/mon-compte/double-authentification")


def test_a_member_needs_no_two_factor(client, member):
    assert client.get("/mon-compte").status_code == 200
