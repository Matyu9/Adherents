"""Connexion, déconnexion, mot de passe, limitation des tentatives."""
from datetime import datetime, timedelta

import pytest

from app.blueprints.auth import MAX_FAILURES, safe_next
from app.extensions import db
from app.models import Account, Activity, LoginSession, Role, ThrottleEvent

from .conftest import PASSWORD, login, make_account


@pytest.fixture
def account(app):
    return make_account()


def actions():
    return [a.action for a in db.session.scalars(db.select(Activity).order_by(Activity.id))]


def test_login_page_is_public_and_translated(client):
    response = client.get("/connexion")
    assert response.status_code == 200 and "Connexion" in response.get_data(as_text=True)


def test_home_requires_login(client):
    response = client.get("/")
    assert response.status_code == 302 and "/connexion" in response.location


def test_login_success_opens_a_session_and_logs_it(client, account):
    response = login(client, account)
    assert response.status_code == 302 and response.location == "/"
    assert client.get("/").status_code == 200
    assert db.session.scalar(db.select(db.func.count(LoginSession.id))) == 1
    assert "auth.login" in actions()
    assert db.session.get(Account, account.id).last_login_at is not None


def test_email_is_not_case_sensitive(client, account):
    response = client.post("/connexion", data={"email": "  ADHERENT@Asso.Test ", "password": PASSWORD})
    assert response.status_code == 302


@pytest.mark.parametrize("email, password", [("adherent@asso.test", "mauvais"), ("inconnu@asso.test", PASSWORD)])
def test_failure_gives_the_same_message_whatever_the_cause(client, account, email, password):
    html = client.post("/connexion", data={"email": email, "password": password}).get_data(as_text=True)
    assert "Email ou mot de passe incorrect." in html
    assert client.get("/").status_code == 302


def test_inactive_account_cannot_log_in(client):
    account = make_account(active=False)
    assert "incorrect" in login(client, account).get_data(as_text=True)
    assert db.session.scalar(db.select(db.func.count(LoginSession.id))) == 0


def test_account_without_password_cannot_log_in(client):
    account = make_account()
    account.password_hash = None
    db.session.commit()
    assert "incorrect" in client.post("/connexion", data={"email": account.email, "password": "x"}).get_data(as_text=True)


def test_deactivating_an_account_ends_its_access_at_once(client, account):
    login(client, account)
    assert client.get("/").status_code == 200
    account.active = False
    db.session.commit()
    assert client.get("/").status_code == 302


@pytest.mark.parametrize("typed", ["mon-secret-a-moi", "mon-secret@a-moi.fr"])
def test_failed_login_journal_never_keeps_unknown_text(client, account, typed):
    """Le texte saisi peut être un mot de passe tapé au mauvais endroit."""
    client.post("/connexion", data={"email": typed, "password": "x"})
    entry = db.session.scalar(db.select(Activity).filter_by(action="auth.failed"))
    assert "mon-secret" not in entry.message and "adresse inconnue" in entry.message


def test_failed_login_on_known_account_targets_it(client, account):
    client.post("/connexion", data={"email": account.email, "password": "x"})
    entry = db.session.scalar(db.select(Activity).filter_by(action="auth.failed"))
    assert entry.target_account_id == account.id and account.email in entry.message


# --- Limitation des tentatives ------------------------------------------------------------------------------

def fail(client, email, times=1, ip="10.0.0.1"):
    for _ in range(times):
        response = client.post("/connexion", data={"email": email, "password": "mauvais"},
                               environ_overrides={"REMOTE_ADDR": ip})
    return response


def test_throttle_blocks_even_the_right_password(client, account):
    assert fail(client, account.email, MAX_FAILURES).status_code == 200
    response = client.post("/connexion", data={"email": account.email, "password": PASSWORD},
                           environ_overrides={"REMOTE_ADDR": "10.0.0.2"})
    assert response.status_code == 429 and "Trop de tentatives" in response.get_data(as_text=True)


def test_throttle_also_counts_per_address(client, account):
    for n in range(MAX_FAILURES):
        fail(client, f"inconnu{n}@asso.test")
    response = client.post("/connexion", data={"email": account.email, "password": PASSWORD},
                           environ_overrides={"REMOTE_ADDR": "10.0.0.1"})
    assert response.status_code == 429


def test_throttle_is_stored_in_database_so_every_worker_sees_it(app, account):
    """Chaque client représente ici un autre worker : le compte est commun, il est en base."""
    fail(app.test_client(), account.email, MAX_FAILURES - 1)
    assert fail(app.test_client(), account.email).status_code == 200
    assert fail(app.test_client(), account.email).status_code == 429


def test_throttle_never_stores_the_typed_text(client, account):
    fail(client, "mon-secret@a-moi.fr")
    hashes = db.session.scalars(db.select(ThrottleEvent.key_hash)).all()
    assert hashes and all("secret" not in h and "10.0.0.1" not in h and len(h) == 64 for h in hashes)


def test_success_resets_the_counter(client, account):
    fail(client, account.email, MAX_FAILURES - 1)
    assert client.post("/connexion", data={"email": account.email, "password": PASSWORD},
                       environ_overrides={"REMOTE_ADDR": "10.0.0.1"}).status_code == 302
    client.post("/deconnexion")
    assert fail(client, account.email, MAX_FAILURES - 1).status_code == 200


def test_old_failures_stop_counting(client, account):
    fail(client, account.email, MAX_FAILURES)
    db.session.execute(db.update(ThrottleEvent).values(created_at=datetime.now() - timedelta(minutes=16)))
    db.session.commit()
    assert login(client, account).status_code == 302


# --- Déconnexion et sessions --------------------------------------------------------------------------------

def test_logout_needs_post(client, account):
    login(client, account)
    assert client.get("/deconnexion").status_code == 405
    assert client.get("/").status_code == 200


def test_logout_invalidates_a_copied_cookie(app, client, account):
    login(client, account)
    cookie = client.get_cookie("adherents_session").value
    assert client.post("/deconnexion").status_code == 302
    thief = app.test_client()
    thief.set_cookie("adherents_session", cookie)
    assert thief.get("/").status_code == 302
    assert "auth.logout" in actions()


def test_remember_cookie_is_refused_once_session_is_revoked(app, account):
    client = app.test_client()
    client.post("/connexion", data={"email": account.email, "password": PASSWORD, "remember": "y"})
    remember = client.get_cookie("adherents_remember")
    assert remember is not None
    db.session.execute(db.delete(LoginSession))
    db.session.commit()
    thief = app.test_client()
    thief.set_cookie("adherents_remember", remember.value)
    assert thief.get("/").status_code == 302


def test_cookies_have_their_own_names(client, account):
    """Sur localhost, les cookies ne sont pas isolés par port : ceux de l'Inventaire écraseraient les nôtres."""
    login(client, account)
    assert client.get_cookie("adherents_session") and not client.get_cookie("session")


@pytest.mark.parametrize("target, expected", [
    ("/journal", "/journal"), ("/journal?page=2", "/journal?page=2"),
    ("//evil.test", "/"), ("///evil.test", "/"), ("/\\evil.test", "/"), ("https://evil.test", "/"), ("javascript:alert(1)", "/"),
    ("evil.test", "/"), ("", "/"), (None, "/"),
])
def test_safe_next(app, target, expected):
    with app.test_request_context():
        assert safe_next(target) == expected


def test_login_follows_next_only_when_local(client, account):
    url = "/connexion?next=//evil.test"
    assert client.post(url, data={"email": account.email, "password": PASSWORD}).location == "/"
    client.post("/deconnexion")
    url = "/connexion?next=/mon-compte"
    assert client.post(url, data={"email": account.email, "password": PASSWORD}).location == "/mon-compte"


def test_logged_in_user_is_sent_away_from_login_page(client, account):
    login(client, account)
    assert client.get("/connexion").status_code == 302


# --- Mot de passe -------------------------------------------------------------------------------------------

def change(client, current=PASSWORD, new="nouveau-mot-de-passe", confirm=None):
    return client.post("/mot-de-passe", data={"current": current, "new": new, "confirm": confirm or new})


def test_change_password(client, account):
    login(client, account)
    assert change(client).status_code == 302
    assert db.session.get(Account, account.id).check_password("nouveau-mot-de-passe")
    assert "account.password" in actions()


@pytest.mark.parametrize("kwargs, message", [
    ({"current": "faux"}, "actuel incorrect"), ({"new": PASSWORD}, "différent"), ({"new": "court"}, "8"),
    ({"confirm": "autre-chose"}, "ne correspondent pas"),
])
def test_change_password_errors(client, account, kwargs, message):
    login(client, account)
    response = change(client, **kwargs)
    assert response.status_code == 200 and message in response.get_data(as_text=True)
    assert db.session.get(Account, account.id).check_password(PASSWORD)


def test_change_password_closes_other_devices_only(app, account):
    mine, other = app.test_client(), app.test_client()
    login(mine, account)
    login(other, account)
    assert change(mine).status_code == 302
    assert mine.get("/").status_code == 200
    assert other.get("/").status_code == 302


def test_temporary_password_must_be_changed_first(client):
    account = make_account(must_change_password=True)
    login(client, account)
    assert client.get("/").location.endswith("/mot-de-passe")
    assert client.get("/mon-compte").location.endswith("/mot-de-passe")
    assert client.get("/static/css/app.css").status_code == 200
    assert client.post("/deconnexion").status_code == 302
    login(client, account)
    assert change(client).status_code == 302
    assert client.get("/").status_code == 200


def test_password_is_hashed(app):
    account = make_account()
    assert PASSWORD not in account.password_hash and account.check_password(PASSWORD)
    assert not Account(first_name="A", last_name="B", email="c@d.e").check_password("x")


def test_admin_role_constant_matches_builtin_list():
    assert Role.ADMIN in Role.BUILTIN and set(Role.DEFAULT_NAMES) == set(Role.BUILTIN)
