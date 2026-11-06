"""Liens à usage unique : invitation, mot de passe oublié, changement et vérification d'email."""
import re
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Account, AccountToken, Activity, LoginSession, Role
from app.services import accounts, mailer

from .conftest import PASSWORD, enroll_totp, login, make_account, totp_code

NEW = "un-nouveau-mot-de-passe"


@pytest.fixture
def smtp(app, monkeypatch):
    """SMTP actif, sans réseau : renvoie la liste des emails « envoyés » (destinataire, objet, texte)."""
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="asso@asso.test")
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None: sent.append((to, subject, text)))
    return sent


@pytest.fixture
def smtp_down(app, monkeypatch):
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="asso@asso.test")

    def fail(*args, **kwargs):
        raise OSError("serveur indisponible")
    monkeypatch.setattr(mailer, "send", fail)


def link_in(text):
    return re.search(r"http://\S*(/lien/[\w-]+)", text).group(1)


def tokens():
    return db.session.scalars(db.select(AccountToken)).all()


def new_password(client, path, password=NEW):
    return client.post(path, data={"new": password, "confirm": password})


def forgot(client, email):
    return client.post("/mot-de-passe-oublie", data={"email": email})


# ---------------------------------------------------------------------------
# Mot de passe oublié
# ---------------------------------------------------------------------------

def test_reset_changes_the_password_and_closes_every_session(app, client, smtp):
    account = make_account()
    other = app.test_client()
    login(other, account)
    forgot(client, account.email)
    (to, subject, text), = smtp
    assert to == account.email and "Réinitialisation" in subject
    response = new_password(client, link_in(text))
    assert response.status_code == 302 and response.location == "/"
    account = db.session.get(Account, account.id)
    assert account.check_password(NEW) and not account.check_password(PASSWORD)
    assert other.get("/mon-compte").status_code == 302, "un appareil volé ne survit pas à la réinitialisation"
    assert client.get("/mon-compte").status_code == 200
    assert db.session.scalar(db.select(db.func.count(LoginSession.id))) == 1


def test_reset_does_not_reveal_who_has_an_account(client, smtp):
    make_account(email="connu@asso.test")
    known = forgot(client, "connu@asso.test")
    unknown = forgot(client, "inconnu@asso.test")
    assert known.status_code == unknown.status_code == 302 and known.location == unknown.location
    known_html = client.get(known.location).get_data(as_text=True)
    assert "Si un compte actif" in known_html
    assert [to for to, *_ in smtp] == ["connu@asso.test"]


def test_reset_for_a_cut_account_sends_nothing(client, smtp):
    make_account(email="coupe@asso.test", active=False)
    assert forgot(client, "coupe@asso.test").status_code == 302
    assert smtp == [] and tokens() == []


def test_reset_is_case_insensitive(client, smtp):
    make_account(email="marie@asso.test")
    forgot(client, "  MARIE@Asso.Test ")
    assert len(smtp) == 1


def test_reset_requests_are_limited(client, smtp):
    make_account(email="marie@asso.test")
    for _ in range(5):
        assert forgot(client, "marie@asso.test").status_code == 302
    assert forgot(client, "marie@asso.test").status_code == 429
    assert len(smtp) == 5


def test_reset_requests_are_limited_by_address_too(client, smtp):
    for n in range(5):
        forgot(client, f"inconnu{n}@asso.test")
    assert forgot(client, "autre@asso.test").status_code == 429


def test_only_the_latest_reset_link_works(client, smtp):
    account = make_account()
    forgot(client, account.email)
    forgot(client, account.email)
    first, second = link_in(smtp[0][2]), link_in(smtp[1][2])
    assert new_password(client, first).location == "/connexion"
    assert db.session.get(Account, account.id).check_password(PASSWORD)
    assert new_password(client, second).location == "/"


def test_without_smtp_the_page_says_so(client):
    html = client.get("/mot-de-passe-oublie").get_data(as_text=True)
    assert "pas configuré" in html and "<form" not in html
    make_account(email="marie@asso.test")
    forgot(client, "marie@asso.test")
    assert tokens() == []


def test_background_sending_does_not_wait_for_the_mail_server(app, client, monkeypatch):
    """Sinon la durée de la réponse révélerait si le compte existe."""
    import threading
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="asso@asso.test", MAIL_IN_BACKGROUND=True)
    release, done, sent = threading.Event(), threading.Event(), []

    def slow(to, *args, **kwargs):
        release.wait(5)
        sent.append(to)
        done.set()
    monkeypatch.setattr(mailer, "send", slow)
    make_account(email="marie@asso.test")
    assert forgot(client, "marie@asso.test").status_code == 302
    assert sent == [], "la requête a attendu le serveur d'emails"
    release.set()
    assert done.wait(5) and sent == ["marie@asso.test"]


def test_reset_never_bypasses_the_second_factor(client, smtp):
    """Une boîte mail piratée ne doit pas suffire : le lien change le mot de passe, sans ouvrir de session."""
    account = make_account()
    enroll_totp(account)
    forgot(client, account.email)
    response = new_password(client, link_in(smtp[0][2]))
    assert response.location == "/connexion"
    assert client.get("/mon-compte").status_code == 302
    assert db.session.scalar(db.select(db.func.count(LoginSession.id))) == 0
    account = db.session.get(Account, account.id)
    assert account.totp_enabled and account.check_password(NEW)
    assert client.post("/connexion", data={"email": account.email, "password": NEW}).location.endswith("/verification")


# ---------------------------------------------------------------------------
# Comportement commun des liens
# ---------------------------------------------------------------------------

def test_link_is_single_use(client, smtp):
    account = make_account()
    forgot(client, account.email)
    path = link_in(smtp[0][2])
    new_password(client, path)
    client.post("/deconnexion")
    assert client.get(path).location == "/connexion"
    assert new_password(client, path, "encore-un-autre-mdp").location == "/connexion"
    assert db.session.get(Account, account.id).check_password(NEW)


def test_opening_the_link_consumes_nothing(client, smtp):
    """Les antivirus de messagerie suivent les liens : seul l'envoi du formulaire compte."""
    account = make_account()
    forgot(client, account.email)
    path = link_in(smtp[0][2])
    for _ in range(3):
        assert client.get(path).status_code == 200
    assert len(tokens()) == 1 and not db.session.get(Account, account.id).email_verified


def test_expired_link_is_refused(client, smtp):
    account = make_account()
    forgot(client, account.email)
    tokens()[0].expires_at = datetime.now() - timedelta(seconds=1)
    db.session.commit()
    assert new_password(client, link_in(smtp[0][2])).location == "/connexion"
    assert db.session.get(Account, account.id).check_password(PASSWORD)


@pytest.mark.parametrize("purpose, hours", [(AccountToken.INVITE, 7 * 24), (AccountToken.RESET, 1),
                                            (AccountToken.EMAIL, 24)])
def test_link_lifetimes(app, purpose, hours):
    accounts.issue(make_account(), purpose)
    token, = tokens()
    assert abs((token.expires_at - datetime.now()).total_seconds() - hours * 3600) < 5


def test_only_a_hash_is_stored(client, smtp):
    forgot(client, make_account().email)
    raw = link_in(smtp[0][2]).rsplit("/", 1)[1]
    assert raw not in tokens()[0].id and len(tokens()[0].id) == 64


def test_cut_account_link_stops_working(client, smtp):
    account = make_account()
    forgot(client, account.email)
    account.active = False
    db.session.commit()
    assert client.get(link_in(smtp[0][2])).location == "/connexion"


@pytest.mark.parametrize("token", ["inconnu", "x" * 500])
def test_unknown_or_absurd_link(client, token):
    assert client.get(f"/lien/{token}").location == "/connexion"


@pytest.mark.parametrize("new, confirm", [("court", "court"), (NEW, "différent"), ("", ""), ("x" * 201, "x" * 201)])
def test_invalid_new_password_keeps_the_link(client, smtp, new, confirm):
    account = make_account()
    forgot(client, account.email)
    response = client.post(link_in(smtp[0][2]), data={"new": new, "confirm": confirm})
    assert response.status_code == 200 and len(tokens()) == 1
    assert db.session.get(Account, account.id).check_password(PASSWORD)


def test_link_used_while_logged_in_as_someone_else(client, smtp):
    someone = make_account(email="autre@asso.test")
    login(client, someone)
    target = make_account(email="cible@asso.test")
    raw = accounts.issue(target, AccountToken.RESET, proves_email=True)
    db.session.commit()
    assert new_password(client, f"/lien/{raw}").location == "/"
    assert "cible@asso.test" in client.get("/mon-compte").get_data(as_text=True)


def test_token_pages_are_not_cached(client):
    account = make_account()
    raw = accounts.issue(account, AccountToken.RESET)
    db.session.commit()
    assert client.get(f"/lien/{raw}").headers["Cache-Control"] == "no-store"


def test_expired_links_are_purged(app):
    account = make_account()
    accounts.issue(account, AccountToken.RESET)
    db.session.commit()
    assert accounts.purge_expired(datetime.now()) == 0
    assert accounts.purge_expired(datetime.now() + timedelta(hours=2)) == 1


# ---------------------------------------------------------------------------
# email_verified : vrai uniquement après un clic sur un lien reçu par email
# ---------------------------------------------------------------------------

def test_new_account_is_not_verified(app):
    assert make_account().email_verified is False


def test_reset_received_by_email_verifies_the_address(client, smtp):
    account = make_account()
    forgot(client, account.email)
    new_password(client, link_in(smtp[0][2]))
    assert db.session.get(Account, account.id).email_verified is True


def test_requesting_a_link_does_not_verify(client, smtp):
    account = make_account()
    forgot(client, account.email)
    assert db.session.get(Account, account.id).email_verified is False


def test_invitation_received_by_email_verifies(client, admin, smtp):
    client.post("/comptes/nouveau", data={"first_name": "A", "last_name": "B", "email": "a@asso.test",
                                          "role": Role.ADHERENT})
    client.post("/deconnexion")
    (_, _, text), = smtp
    new_password(client, link_in(text))
    assert db.session.scalar(db.select(Account).filter_by(email="a@asso.test")).email_verified is True


def test_link_handed_over_manually_does_not_verify(client, admin):
    """Sans SMTP le lien passe par un responsable : il ne prouve rien sur la boîte mail."""
    html = client.post("/comptes/nouveau", data={"first_name": "A", "last_name": "B", "email": "a@asso.test",
                                                 "role": Role.ADHERENT}).get_data(as_text=True)
    path = re.search(r"http://\S*(/lien/[\w-]+)", html).group(1)
    client.post("/deconnexion")
    new_password(client, path)
    account = db.session.scalar(db.select(Account).filter_by(email="a@asso.test"))
    assert account.check_password(NEW) and account.email_verified is False


def test_link_shown_after_a_mail_failure_does_not_verify(client, admin, smtp_down):
    html = client.post("/comptes/nouveau", data={"first_name": "A", "last_name": "B", "email": "a@asso.test",
                                                 "role": Role.ADHERENT}).get_data(as_text=True)
    assert "pas pu partir" in html
    client.post("/deconnexion")
    new_password(client, re.search(r"http://\S*(/lien/[\w-]+)", html).group(1))
    assert db.session.scalar(db.select(Account).filter_by(email="a@asso.test")).email_verified is False


def test_link_is_hidden_once_the_mail_has_left(client, admin, smtp):
    html = client.post("/comptes/nouveau", data={"first_name": "A", "last_name": "B", "email": "a@asso.test",
                                                 "role": Role.ADHERENT}).get_data(as_text=True)
    assert "/lien/" not in html and "être envoyée" in html
    assert tokens()[0].proves_email is True


def test_verify_my_address(client, smtp):
    account = make_account()
    login(client, account)
    client.post("/mon-compte/email/verifier")
    (to, _, text), = smtp
    assert to == account.email
    assert db.session.get(Account, account.id).email_verified is False
    assert client.get(link_in(text)).status_code == 200
    assert db.session.get(Account, account.id).email_verified is False, "ouvrir le lien ne suffit pas à confirmer"
    client.post(link_in(text))
    assert db.session.get(Account, account.id).email_verified is True
    actions = db.session.scalars(db.select(Activity.action)).all()
    assert "account.email" in actions and len(smtp) == 1, "aucun avis : l'adresse n'a pas changé"


def test_verify_when_already_verified_sends_nothing(client, smtp):
    login(client, make_account(email_verified=True))
    assert "déjà vérifiée" in client.post("/mon-compte/email/verifier", follow_redirects=True).get_data(as_text=True)
    assert smtp == []


# ---------------------------------------------------------------------------
# Changer d'adresse depuis Mon compte
# ---------------------------------------------------------------------------

def change(client, email="nouvelle@asso.test", password=PASSWORD):
    return client.post("/mon-compte/email", data={"email": email, "password": password})


@pytest.fixture
def member_with_smtp(client, smtp):
    account = make_account(email="ancienne@asso.test", email_verified=True)
    login(client, account)
    return account


def test_change_needs_the_click_on_the_link(client, member_with_smtp, smtp):
    assert change(client).status_code == 302
    account = db.session.get(Account, member_with_smtp.id)
    assert account.email == "ancienne@asso.test" and account.email_verified is True
    (to, _, text), = smtp
    assert to == "nouvelle@asso.test"
    client.post(link_in(text))
    account = db.session.get(Account, member_with_smtp.id)
    assert account.email == "nouvelle@asso.test" and account.email_verified is True
    assert tokens() == []


def test_change_warns_the_old_address(client, member_with_smtp, smtp):
    change(client)
    client.post(link_in(smtp[0][2]))
    to, subject, text = smtp[1]
    assert to == "ancienne@asso.test" and "modifiée" in subject and "nouvelle@asso.test" in text


def test_change_requires_the_password(client, member_with_smtp, smtp):
    html = change(client, password="mauvais").get_data(as_text=True)
    assert "Mot de passe incorrect" in html and smtp == [] and tokens() == []


def test_password_guessing_through_the_change_form_is_limited(client, member_with_smtp, smtp):
    for _ in range(8):
        change(client, password="mauvais")
    assert "Trop de tentatives" in change(client).get_data(as_text=True)
    assert smtp == []


def test_change_to_a_taken_address_reveals_nothing(client, member_with_smtp, smtp):
    make_account(email="prise@asso.test")
    taken = client.post("/mon-compte/email", data={"email": "prise@asso.test", "password": PASSWORD},
                        follow_redirects=True).get_data(as_text=True)
    assert "Si prise@asso.test est disponible" in taken and "déjà" not in taken
    assert smtp == [] and tokens() == []


def test_address_taken_between_request_and_click(client, member_with_smtp, smtp):
    change(client)
    make_account(email="nouvelle@asso.test")
    html = client.post(link_in(smtp[0][2])).get_data(as_text=True)
    assert "déjà utilisée" in html
    assert db.session.get(Account, member_with_smtp.id).email == "ancienne@asso.test"


def test_change_cancels_links_sent_to_the_old_address(client, member_with_smtp, smtp):
    raw = accounts.issue(member_with_smtp, AccountToken.RESET, proves_email=True)
    db.session.commit()
    change(client)
    client.post(link_in(smtp[0][2]))
    client.post("/deconnexion")
    assert client.get(f"/lien/{raw}").location == "/connexion"


@pytest.mark.parametrize("email", ["pas-une-adresse", "a@b", "a b@asso.test", ""])
def test_change_refuses_invalid_addresses(client, member_with_smtp, smtp, email):
    assert change(client, email=email).status_code == 200
    assert smtp == []


def test_change_to_my_own_address(client, member_with_smtp, smtp):
    assert "déjà votre adresse" in change(client, email="ancienne@asso.test").get_data(as_text=True)
    assert smtp == []


def test_change_requests_are_limited(client, member_with_smtp, smtp):
    for n in range(5):
        change(client, email=f"n{n}@asso.test")
    assert "Trop de demandes" in client.post("/mon-compte/email", data={"email": "n9@asso.test", "password": PASSWORD},
                                             follow_redirects=True).get_data(as_text=True)
    assert len(smtp) == 5


def test_change_without_smtp_is_refused(client):
    login(client, make_account())
    assert "pas configuré" in client.post("/mon-compte/email", data={"email": "n@asso.test", "password": PASSWORD},
                                          follow_redirects=True).get_data(as_text=True)
    assert tokens() == []


def test_login_with_the_new_address_only(app, client, member_with_smtp, smtp):
    change(client)
    client.post(link_in(smtp[0][2]))
    fresh = app.test_client()
    assert fresh.post("/connexion", data={"email": "ancienne@asso.test", "password": PASSWORD}).status_code == 200
    assert fresh.post("/connexion", data={"email": "nouvelle@asso.test", "password": PASSWORD}).status_code == 302


def test_totp_code_helper_is_valid():
    assert re.fullmatch(r"\d{6}", totp_code())
