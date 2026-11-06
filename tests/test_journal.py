from datetime import datetime, timedelta

import pytest

from app.blueprints.journal import PER_PAGE
from app.extensions import db
from app.models import Activity, Role
from app.services import activity, tasks
from app.services.activity import diff, log

from .conftest import login


@pytest.fixture
def journal(app, accounts):
    client = app.test_client()
    login(client, accounts[Role.ADMIN])
    return client


def test_filter_by_family(journal, accounts):
    log("role.update", "Rôle touché", user=accounts[Role.ADMIN])
    log("settings.logo", "Logo touché", user=accounts[Role.ADMIN])
    db.session.commit()
    html = journal.get("/journal?type=parametres").get_data(as_text=True)
    assert "Logo touché" in html and "Rôle touché" not in html
    html = journal.get("/journal?type=comptes").get_data(as_text=True)
    assert "Rôle touché" in html and "Logo touché" not in html
    assert "Rôle touché" in journal.get("/journal?type=inconnu").get_data(as_text=True)


def test_search_ignores_case_and_accents(journal, accounts):
    log("role.update", "Fiche de Lefèvre modifiée", user=accounts[Role.ADMIN])
    db.session.commit()
    assert "Lefèvre" in journal.get("/journal?q=LEFEVRE").get_data(as_text=True)
    assert "Lefèvre" not in journal.get("/journal?q=dupont").get_data(as_text=True)


def test_search_treats_wildcards_literally(journal, accounts):
    log("role.update", "Remise de 50 %", user=accounts[Role.ADMIN])
    log("role.update", "Autre ligne", user=accounts[Role.ADMIN])
    db.session.commit()
    html = journal.get("/journal?q=%25").get_data(as_text=True)
    assert "Remise" in html and "Autre ligne" not in html
    assert "Autre ligne" not in journal.get("/journal?q=_").get_data(as_text=True)


def test_filter_by_person_includes_author_and_target(journal, accounts):
    member, admin = accounts[Role.ADHERENT], accounts[Role.ADMIN]
    log("role.update", "Sur lui", user=admin, target=member)
    log("account.profile", "Par lui", user=member)
    log("settings.logo", "Rien à voir", user=admin)
    db.session.commit()
    html = journal.get(f"/journal?personne={member.id}").get_data(as_text=True)
    assert "Sur lui" in html and "Par lui" in html and "Rien à voir" not in html
    assert "voir tout le monde" in html


def test_pagination(journal, accounts):
    db.session.add_all(Activity(account_id=accounts[Role.ADMIN].id, action="role.update", message=f"Ligne {n:04}")
                       for n in range(PER_PAGE + 5))
    db.session.commit()
    first, second = journal.get("/journal").get_data(as_text=True), journal.get("/journal?page=2").get_data(as_text=True)
    assert "Page 1 / 2" in first and "Plus ancien" in first and "Page 2 / 2" in second and "Plus récent" in second


@pytest.mark.parametrize("url", ["/journal?page=abc", "/journal?page=-4", "/journal?personne=abc", "/journal?page=999"])
def test_odd_parameters_never_crash(journal, url):
    assert journal.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/journal?page=99999999999999999999", "/journal?personne=99999999999999999999"])
def test_numbers_too_large_are_a_clean_404(journal, url):
    assert journal.get(url).status_code == 404


def test_messages_are_escaped(journal, accounts):
    log("role.update", "<img src=x>", user=accounts[Role.ADMIN])
    db.session.commit()
    assert "<img src=x>" not in journal.get("/journal").get_data(as_text=True)


def test_diff_applies_and_reports_changes(app):
    class Thing:
        name, kept = "avant", "pareil"

    thing = Thing()
    assert diff(thing, ["name", "kept", "absent"], {"name": "après", "kept": "pareil"}) == {"name": ["avant", "après"]}
    assert thing.name == "après"


@pytest.mark.parametrize("field", ["iban", "bic", "password_hash"])
def test_diff_never_writes_sensitive_values(app, field):
    """L'IBAN sera saisi en phase 6 : il ne doit jamais atterrir en clair dans le journal."""
    thing = type("Thing", (), {field: "FR7630001007941234567890185"})()
    changes = diff(thing, [field], {field: "FR7612345678901234567890123"})
    assert changes == {field: ["(masqué)", "(masqué)"]}
    assert getattr(thing, field).endswith("0123")


def test_log_does_not_commit(accounts):
    log("role.update", "Non validé", user=accounts[Role.ADMIN])
    db.session.rollback()
    assert db.session.scalar(db.select(db.func.count(Activity.id))) == 0


def test_expired_logins_are_purged_but_not_other_actions(accounts):
    old = datetime.now() - timedelta(days=activity.LOGIN_RETENTION_DAYS + 1)
    db.session.add_all([Activity(action="auth.login", message="vieille connexion", created_at=old),
                        Activity(action="auth.failed", message="vieil échec", created_at=old),
                        Activity(action="role.update", message="vieux rôle", created_at=old),
                        Activity(action="auth.login", message="connexion récente")])
    db.session.commit()
    assert "2 connexion(s)" in tasks.run()
    db.session.rollback()
    assert set(db.session.scalars(db.select(Activity.message))) == {"vieux rôle", "connexion récente"}
