"""Double authentification : configuration, connexion, anti-rejeu, codes de secours, obligation par rôle."""

import re
import time

import pyotp
import pytest

from app.blueprints.auth import MAX_FAILURES, PENDING_LIFETIME
from app.extensions import db
from app.models import Account, Activity, LoginSession, RecoveryCode, Role
from app.services import totp

from .conftest import PASSWORD, TOTP_SECRET, enroll_totp, login, make_account, totp_code

SETUP = "/mon-compte/double-authentification"
VERIFY = "/connexion/verification"


def code_at(offset=0, secret=TOTP_SECRET):
    return pyotp.TOTP(secret).generate_otp(int(time.time()) // 30 + offset)


def start_login(client, account, password=PASSWORD, **kwargs):
    return client.post("/connexion", data={"email": account.email, "password": password}, **kwargs)


def logged_in(client):
    return client.get("/mon-compte").status_code == 200


def count_sessions():
    return db.session.scalar(db.select(db.func.count(LoginSession.id)))


@pytest.fixture
def enrolled(app):
    account = make_account(email="totp@asso.test")
    enroll_totp(account)
    return account


# ---------------------------------------------------------------------------
# Connexion
# ---------------------------------------------------------------------------


def test_password_alone_opens_nothing(client, enrolled):
    response = start_login(client, enrolled)
    assert response.status_code == 302 and response.location == VERIFY
    assert not logged_in(client) and count_sessions() == 0


def test_valid_code_opens_the_session(client, enrolled):
    start_login(client, enrolled)
    response = client.post(VERIFY, data={"code": totp_code()})
    assert response.status_code == 302 and response.location == "/" and logged_in(client) and count_sessions() == 1


def test_wrong_password_never_reaches_the_code_page(client, enrolled):
    assert start_login(client, enrolled, password="mauvais").status_code == 200
    assert client.get(VERIFY).location == "/connexion"


def test_code_page_without_password_step(client, enrolled):
    assert client.post(VERIFY, data={"code": totp_code()}).location == "/connexion"
    assert not logged_in(client)


def test_wrong_code_is_refused_and_logged(client, enrolled):
    start_login(client, enrolled)
    wrong = "000000" if totp_code() != "000000" else "111111"
    assert "Code incorrect" in client.post(VERIFY, data={"code": wrong}).get_data(as_text=True)
    assert not logged_in(client)
    assert "auth.totp_failed" in db.session.scalars(db.select(Activity.action)).all()


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "<script>", "12 34"])
def test_malformed_codes(client, enrolled, code):
    start_login(client, enrolled)
    client.post(VERIFY, data={"code": code})
    assert not logged_in(client)


def test_code_with_spaces_is_accepted(client, enrolled):
    start_login(client, enrolled)
    code = totp_code()
    client.post(VERIFY, data={"code": f" {code[:3]} {code[3:]} "})
    assert logged_in(client)


def test_clock_tolerance_is_one_period(client, enrolled):
    start_login(client, enrolled)
    client.post(VERIFY, data={"code": code_at(-2)})
    assert not logged_in(client)
    client.post(VERIFY, data={"code": code_at(2)})
    assert not logged_in(client)
    client.post(VERIFY, data={"code": code_at(-1)})
    assert logged_in(client)


def test_code_cannot_be_replayed(app, client, enrolled):
    code = totp_code()
    start_login(client, enrolled)
    client.post(VERIFY, data={"code": code})
    assert logged_in(client)
    other = app.test_client()
    start_login(other, enrolled)
    other.post(VERIFY, data={"code": code})
    assert not logged_in(other), "un code déjà utilisé a été accepté"


def test_older_code_refused_after_a_newer_one(app, client, enrolled):
    start_login(client, enrolled)
    client.post(VERIFY, data={"code": code_at(0)})
    other = app.test_client()
    start_login(other, enrolled)
    other.post(VERIFY, data={"code": code_at(-1)})
    assert not logged_in(other)


def test_guesses_are_limited(client, enrolled):
    start_login(client, enrolled)
    for _ in range(5):
        client.post(VERIFY, data={"code": "abcdef"})
    response = client.post(VERIFY, data={"code": totp_code()})
    assert response.status_code == 429 and not logged_in(client)


def test_limit_survives_a_new_password_step(client, enrolled):
    """Repasser par le mot de passe ne redonne pas d'essais."""
    start_login(client, enrolled)
    for _ in range(5):
        client.post(VERIFY, data={"code": "abcdef"})
    start_login(client, enrolled)
    assert client.post(VERIFY, data={"code": totp_code()}).status_code == 429


def test_pending_step_expires(client, enrolled, monkeypatch):
    start_login(client, enrolled)
    now = time.time()
    monkeypatch.setattr("app.blueprints.auth.time.time", lambda: now + PENDING_LIFETIME + 1)
    assert client.post(VERIFY, data={"code": code_at()}).location == "/connexion"
    assert count_sessions() == 0


def test_account_cut_during_the_pending_step(client, enrolled):
    start_login(client, enrolled)
    enrolled.active = False
    db.session.commit()
    assert client.post(VERIFY, data={"code": totp_code()}).location == "/connexion"
    assert count_sessions() == 0


def test_next_is_kept_and_stays_local(app, client, enrolled):
    client.post("/connexion?next=/notifications", data={"email": enrolled.email, "password": PASSWORD})
    assert client.post(VERIFY, data={"code": totp_code()}).location == "/notifications"
    other = app.test_client()
    other.post("/connexion?next=//pirate.test", data={"email": enrolled.email, "password": PASSWORD})
    enrolled.totp_last_counter = None
    db.session.commit()
    assert other.post(VERIFY, data={"code": totp_code()}).location == "/"


def test_remember_is_kept(client, enrolled):
    client.post("/connexion", data={"email": enrolled.email, "password": PASSWORD, "remember": "y"})
    response = client.post(VERIFY, data={"code": totp_code()})
    assert "adherents_remember" in " ".join(response.headers.getlist("Set-Cookie"))


def test_password_failures_are_cleared_only_after_the_code(client, enrolled):
    for _ in range(MAX_FAILURES - 1):
        start_login(client, enrolled, password="mauvais")
    start_login(client, enrolled)
    client.post(VERIFY, data={"code": "abcdef"})
    start_login(client, enrolled, password="mauvais")
    assert start_login(client, enrolled, password="mauvais").status_code == 429


def test_code_page_is_not_cached(client, enrolled):
    start_login(client, enrolled)
    assert client.get(VERIFY).headers["Cache-Control"] == "no-store"


# ---------------------------------------------------------------------------
# Secret
# ---------------------------------------------------------------------------


def test_secret_is_encrypted_in_the_database(enrolled):
    assert TOTP_SECRET not in enrolled.totp_secret and totp.secret_of(enrolled) == TOTP_SECRET


def test_changing_secret_key_fails_cleanly(app, client, enrolled):
    start_login(client, enrolled)
    code = totp_code()
    old = app.config["SECRET_KEY"]
    app.config["SECRET_KEY"] = old + "-autre"
    assert totp.secret_of(enrolled) is None and totp.verify_code(enrolled, code) is False
    app.config["SECRET_KEY"] = old


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@pytest.fixture
def member_client(client):
    account = make_account(email="membre@asso.test")
    login(client, account)
    return client, account


def setup_secret(client):
    return re.search(r'class="totp-secret">([A-Z2-7]+)<', client.get(SETUP).get_data(as_text=True)).group(1)


def test_setup_page_shows_qr_and_secret(member_client):
    client, _ = member_client
    html = client.get(SETUP).get_data(as_text=True)
    assert 'src="data:image/png;base64,' in html and "totp-secret" in html
    assert client.get(SETUP).headers["Cache-Control"] == "no-store"


def test_reloading_keeps_the_same_secret(member_client):
    client, _ = member_client
    assert setup_secret(client) == setup_secret(client)


def test_uri_names_the_account_and_the_association(member_client):
    _, account = member_client
    uri = totp.uri(account, TOTP_SECRET)
    assert uri.startswith("otpauth://totp/") and "membre%40asso.test" in uri and f"secret={TOTP_SECRET}" in uri


def test_setup_not_active_until_confirmed(app, member_client):
    client, account = member_client
    setup_secret(client)
    account = db.session.get(Account, account.id)
    assert account.totp_secret and not account.totp_enabled
    other = app.test_client()
    assert start_login(other, account).location == "/", "un secret non confirmé ne doit rien exiger"


def test_setup_activates_and_shows_codes_once(member_client):
    client, account = member_client
    secret = setup_secret(client)
    html = client.post(SETUP, data={"password": PASSWORD, "code": totp_code(secret)}).get_data(as_text=True)
    codes = re.findall(r"<code>([0-9a-f]{10}-[0-9a-f]{10})</code>", html)
    assert len(codes) == 10 and len(set(codes)) == 10
    account = db.session.get(Account, account.id)
    assert account.totp_enabled
    assert not any(c in client.get(SETUP).get_data(as_text=True) for c in codes)
    stored = db.session.scalars(db.select(RecoveryCode.id)).all()
    assert len(stored) == 10 and not set(codes) & set(stored)


def test_setup_needs_the_password(member_client):
    """Une session volée ne doit pas pouvoir verrouiller le compte de son propriétaire."""
    client, account = member_client
    secret = setup_secret(client)
    html = client.post(SETUP, data={"password": "mauvais", "code": totp_code(secret)}).get_data(as_text=True)
    assert "Mot de passe incorrect" in html and not db.session.get(Account, account.id).totp_enabled


def test_setup_needs_a_valid_code(member_client):
    client, account = member_client
    setup_secret(client)
    html = client.post(SETUP, data={"password": PASSWORD, "code": "abcdef"}).get_data(as_text=True)
    assert "Code incorrect" in html and not db.session.get(Account, account.id).totp_enabled


def test_setup_closes_other_sessions(app, member_client):
    client, account = member_client
    other = app.test_client()
    login(other, account)
    secret = setup_secret(client)
    client.post(SETUP, data={"password": PASSWORD, "code": totp_code(secret)})
    assert logged_in(client) and not logged_in(other)


# ---------------------------------------------------------------------------
# Codes de secours
# ---------------------------------------------------------------------------


@pytest.fixture
def with_codes(enrolled):
    codes = totp.new_recovery_codes(enrolled)
    db.session.commit()
    return enrolled, codes


def test_recovery_code_logs_in_once(app, client, with_codes):
    account, codes = with_codes
    start_login(client, account)
    html = client.post(VERIFY, data={"code": codes[0]}, follow_redirects=True).get_data(as_text=True)
    assert logged_in(client) and "Il vous en reste 9" in html
    assert "auth.recovery_code" in db.session.scalars(db.select(Activity.action)).all()
    other = app.test_client()
    start_login(other, account)
    other.post(VERIFY, data={"code": codes[0]})
    assert not logged_in(other)


def test_recovery_code_is_tolerant_on_case_and_spaces(client, with_codes):
    account, codes = with_codes
    start_login(client, account)
    client.post(VERIFY, data={"code": f"  {codes[1].upper()} "})
    assert logged_in(client)


def test_someone_elses_recovery_code_is_refused(client, with_codes):
    _, codes = with_codes
    other = make_account(email="autre@asso.test")
    enroll_totp(other)
    start_login(client, other)
    client.post(VERIFY, data={"code": codes[0]})
    assert not logged_in(client)
    assert totp.remaining_recovery_codes(with_codes[0]) == 10


def test_regenerate_replaces_all_codes(member_client):
    client, account = member_client
    enroll_totp(account)
    old = totp.new_recovery_codes(account)
    db.session.commit()
    html = client.post(f"{SETUP}/codes", data={"password": PASSWORD, "code": totp_code()}).get_data(as_text=True)
    assert len(re.findall(r"<code>[0-9a-f-]{21}</code>", html)) == 10
    assert totp.use_recovery_code(account, old[0]) is False


@pytest.mark.parametrize("password, valid_code", [("mauvais", True), (PASSWORD, False)])
def test_regenerate_needs_password_and_code(member_client, password, valid_code):
    client, account = member_client
    enroll_totp(account)
    old = totp.new_recovery_codes(account)
    db.session.commit()
    client.post(f"{SETUP}/codes", data={"password": password, "code": totp_code() if valid_code else "abcdef"})
    assert totp.use_recovery_code(account, old[0]) is True


# ---------------------------------------------------------------------------
# Désactivation
# ---------------------------------------------------------------------------


def test_member_can_disable(member_client):
    client, account = member_client
    enroll_totp(account)
    totp.new_recovery_codes(account)
    db.session.commit()
    client.post(f"{SETUP}/desactiver", data={"password": PASSWORD, "code": totp_code()})
    account = db.session.get(Account, account.id)
    assert not account.totp_enabled and account.totp_secret is None
    assert db.session.scalar(db.select(db.func.count(RecoveryCode.id))) == 0


@pytest.mark.parametrize("password, valid_code", [("mauvais", True), (PASSWORD, False)])
def test_disable_needs_password_and_code(member_client, password, valid_code):
    client, account = member_client
    enroll_totp(account)
    client.post(f"{SETUP}/desactiver", data={"password": password, "code": totp_code() if valid_code else "abcdef"})
    assert db.session.get(Account, account.id).totp_enabled


def test_code_guessing_while_logged_in_is_limited(member_client):
    client, account = member_client
    enroll_totp(account)
    for _ in range(5):
        client.post(f"{SETUP}/desactiver", data={"password": PASSWORD, "code": "abcdef"})
    html = client.post(f"{SETUP}/desactiver", data={"password": PASSWORD, "code": totp_code()}).get_data(as_text=True)
    assert "Trop de tentatives" in html and db.session.get(Account, account.id).totp_enabled


def test_admin_cannot_disable(client, admin):
    html = client.post(
        f"{SETUP}/desactiver", data={"password": PASSWORD, "code": totp_code()}, follow_redirects=True
    ).get_data(as_text=True)
    assert "obligatoire" in html and db.session.get(Account, admin.id).totp_enabled
    assert "Désactiver" not in client.get(SETUP).get_data(as_text=True)


# ---------------------------------------------------------------------------
# Obligation
# ---------------------------------------------------------------------------


@pytest.fixture
def bare_admin(app):
    account = make_account(Role.ADMIN, email="neuf@asso.test")
    account.totp_secret = account.totp_enabled_at = None
    db.session.commit()
    return account


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/mon-compte",
        "/comptes",
        "/roles",
        "/parametres",
        "/journal",
        "/notifications",
        "/groupes",
        "/mot-de-passe",
    ],
)
def test_admin_without_totp_can_only_configure_it(client, bare_admin, path):
    start_login(client, bare_admin)
    assert client.get(path).location == SETUP


def test_admin_without_totp_cannot_post_either(client, bare_admin):
    start_login(client, bare_admin)
    response = client.post(
        "/comptes/nouveau", data={"first_name": "A", "last_name": "B", "email": "x@asso.test", "role": Role.ADMIN}
    )
    assert response.location == SETUP
    assert db.session.scalar(db.select(Account).filter_by(email="x@asso.test")) is None


def test_admin_without_totp_can_log_out_and_load_assets(client, bare_admin):
    start_login(client, bare_admin)
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get(SETUP).status_code == 200
    assert client.post("/deconnexion").location == "/connexion"


def test_admin_is_free_once_configured(client, bare_admin):
    start_login(client, bare_admin)
    secret = setup_secret(client)
    client.post(SETUP, data={"password": PASSWORD, "code": totp_code(secret)})
    assert client.get("/comptes").status_code == 200


@pytest.mark.parametrize("permission", ["account.manage", "group.manage"])
def test_role_that_manages_accounts_needs_totp_too(client, app, permission):
    db.session.add(Role(key="resp", name="Responsable", permissions=[permission]))
    account = make_account("resp", email="resp@asso.test")
    start_login(client, account)
    assert client.get("/").location == SETUP


def test_role_with_other_rights_does_not_need_it(client, accounts):
    login(client, accounts[Role.GESTIONNAIRE])
    assert client.get("/").status_code == 200


def test_promotion_applies_at_once(client):
    account = make_account(email="promu@asso.test")
    login(client, account)
    assert client.get("/").status_code == 200
    account.role = Role.ADMIN
    db.session.commit()
    assert client.get("/").location == SETUP


def test_forced_password_change_comes_first(client):
    account = make_account(Role.ADMIN, email="double@asso.test", must_change_password=True)
    account.totp_secret = account.totp_enabled_at = None
    db.session.commit()
    start_login(client, account)
    assert client.get("/").location == "/mot-de-passe"
    assert client.get("/mot-de-passe").status_code == 200


# ---------------------------------------------------------------------------
# Accès de secours
# ---------------------------------------------------------------------------


def test_create_admin_rescues_a_lost_phone(app, client):
    account = make_account(Role.ADMIN, email="chef@asso.test")
    totp.new_recovery_codes(account)
    db.session.commit()
    result = app.test_cli_runner().invoke(
        args=[
            "create-admin",
            "--email",
            "chef@asso.test",
            "--first-name",
            "É",
            "--last-name",
            "L",
            "--password",
            "secours-secours",
        ]
    )
    assert result.exit_code == 0
    account = db.session.get(Account, account.id)
    assert not account.totp_enabled and db.session.scalar(db.select(db.func.count(RecoveryCode.id))) == 0
    start_login(client, account, password="secours-secours")
    assert client.get("/").location == SETUP
