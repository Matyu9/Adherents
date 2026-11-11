"""Mon compte : profil, affichage, appareils connectés."""

import uuid

import pytest

from app.extensions import db
from app.models import Account, LoginSession
from app.services import sessions

from .conftest import login, make_account


def test_profile_update_is_logged(client, member):
    client.post("/mon-compte", data={"first_name": " Élise ", "last_name": " Lefèvre "})
    account = db.session.get(Account, member.id)
    assert account.full_name == "Élise Lefèvre" and account.initials == "ÉL"


def test_profile_cannot_change_role_or_email(client, member):
    client.post(
        "/mon-compte",
        data={"first_name": "A", "last_name": "B", "role": "admin", "email": "x@y.z", "active": "0", "sub": "autre"},
    )
    account = db.session.get(Account, member.id)
    assert account.role == member.role and account.email == "adherent@asso.test" and account.active


@pytest.mark.parametrize("first, last", [("", "B"), ("A", ""), ("x" * 81, "B")])
def test_profile_validation(client, member, first, last):
    assert client.post("/mon-compte", data={"first_name": first, "last_name": last}).status_code == 200
    assert db.session.get(Account, member.id).first_name == "Adherent"


def test_display_preferences(client, member):
    client.post("/mon-compte/affichage", data={"theme": "dark", "text_size": "xlarge"})
    account = db.session.get(Account, member.id)
    assert account.theme == "dark" and account.text_size == "xlarge"
    html = client.get("/").get_data(as_text=True)
    assert 'data-theme="dark"' in html and 'data-text="xlarge"' in html and "css/dark.css" in html
    assert 'media="(prefers-color-scheme: dark)"' not in html


def test_automatic_theme_follows_the_device(client, member):
    client.post("/mon-compte/affichage", data={"theme": "auto", "text_size": "normal"})
    account = db.session.get(Account, member.id)
    assert account.theme is None and account.text_size is None
    assert 'media="(prefers-color-scheme: dark)"' in client.get("/").get_data(as_text=True)


def test_light_theme_does_not_load_the_dark_sheet(client, member):
    client.post("/mon-compte/affichage", data={"theme": "light", "text_size": "normal"})
    assert "css/dark.css" not in client.get("/").get_data(as_text=True)


@pytest.mark.parametrize("data", [{"theme": "rose", "text_size": "normal"}, {"theme": "dark", "text_size": "enorme"}])
def test_invalid_display_choice_is_refused(client, member, data):
    assert "invalide" in client.post("/mon-compte/affichage", data=data, follow_redirects=True).get_data(as_text=True)
    assert db.session.get(Account, member.id).theme is None


def test_sessions_are_listed_with_device(app, accounts):
    client = app.test_client()
    client.post(
        "/connexion",
        data={"email": "adherent@asso.test", "password": "motdepasse"},
        headers={"User-Agent": "Mozilla/5.0 (iPhone) Safari/604.1"},
    )
    html = client.get("/mon-compte", headers={"User-Agent": "x"}).get_data(as_text=True)
    assert "Safari · iPhone" in html and "cet appareil" in html


def test_close_another_device(app, accounts):
    mine, other = app.test_client(), app.test_client()
    login(mine, accounts["adherent"])
    login(other, accounts["adherent"])
    assert db.session.scalar(db.select(db.func.count(LoginSession.id))) == 2
    assert mine.post("/mon-compte/sessions/fermer").status_code == 302
    assert mine.get("/").status_code == 200 and other.get("/").status_code == 302


def test_current_session_cannot_be_closed_by_this_route(client, member):
    sid = db.session.scalar(db.select(LoginSession.sid))
    html = client.post(f"/mon-compte/sessions/{sid}/fermer", follow_redirects=True).get_data(as_text=True)
    assert "utilisez « Déconnexion »" in html and client.get("/").status_code == 200


def test_another_device_is_closed_by_its_public_sid(app, accounts):
    mine, other = app.test_client(), app.test_client()
    login(mine, accounts["adherent"])
    login(other, accounts["adherent"])
    theirs = db.session.scalars(db.select(LoginSession).order_by(LoginSession.created_at)).all()[-1]
    html = mine.get("/mon-compte").get_data(as_text=True)
    assert theirs.sid in html
    assert all(row.id not in html for row in db.session.scalars(db.select(LoginSession)))  # jamais le jeton secret
    mine.post(f"/mon-compte/sessions/{theirs.id}/fermer")  # l'ancienne forme, par le jeton, ne ferme plus rien
    assert other.get("/").status_code == 200
    mine.post(f"/mon-compte/sessions/{theirs.sid}/fermer")
    assert other.get("/").status_code == 302 and mine.get("/").status_code == 200


def test_session_of_someone_else_cannot_be_closed(app, accounts):
    other = app.test_client()
    login(other, accounts["admin"])
    theirs = db.session.scalar(db.select(LoginSession).filter_by(account_id=accounts["admin"].id))
    client = app.test_client()
    login(client, accounts["adherent"])
    assert client.post(f"/mon-compte/sessions/{theirs.sid}/fermer").status_code == 302
    assert db.session.get(LoginSession, theirs.id) is not None and other.get("/").status_code == 200


@pytest.mark.parametrize(
    "agent, label",
    [
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit Version/17 Safari/605", "Safari · macOS"),
        ("Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537 Edg/120.0", "Edge · Windows"),
        ("Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko Firefox/120.0", "Firefox · Linux"),
        ("Mozilla/5.0 (Linux; Android 14) Chrome/120.0 Mobile Safari/537", "Chrome · Android"),
        ("", "Appareil inconnu"),
        (None, "Appareil inconnu"),
    ],
)
def test_device_label(agent, label):
    assert sessions.device_label(agent) == label


def test_last_seen_is_only_written_every_few_minutes(client, member):
    from datetime import datetime, timedelta

    login_row = db.session.scalar(db.select(LoginSession))
    old = datetime.now() - sessions.SEEN_EVERY + timedelta(minutes=1)
    login_row.last_seen_at = old
    db.session.commit()
    client.get("/")
    assert db.session.scalar(db.select(LoginSession)).last_seen_at == old
    login_row.last_seen_at = old - timedelta(minutes=2)
    db.session.commit()
    client.get("/")
    assert db.session.scalar(db.select(LoginSession)).last_seen_at > old


def test_sub_is_an_opaque_stable_uuid(app):
    account = make_account()
    first = account.sub
    uuid.UUID(first)
    assert account.email not in first and first != str(account.id)
    account.email = "nouvelle@asso.test"
    db.session.commit()
    assert db.session.get(Account, account.id).sub == first


def test_each_account_has_its_own_sub(accounts):
    assert len({a.sub for a in accounts.values()}) == len(accounts)
