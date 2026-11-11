"""Phase 4 : déconnexion. Avis envoyés aux applis (Back-Channel Logout) et déconnexion demandée par une appli.

Chaque façon de fermer une session, de perdre l'accès ou de changer de groupes doit prévenir les bonnes applis, et
seulement elles. L'envoi réseau est remplacé par un enregistreur, sauf dans les tests qui parlent à un vrai serveur.
"""
import re
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

from app.extensions import db
from app.models import (
    AccessGroup,
    Account,
    Activity,
    LoginSession,
    OidcClient,
    OidcCode,
    OidcConsent,
    OidcKey,
    OidcLogout,
    OidcSession,
    OidcToken,
    Role,
)
from app.services import logout, oidc, sessions, tasks

from .conftest import PASSWORD, config_for, login, make_account, release, reset_database, stamp_head
from .test_oidc import (  # noqa: F401
    CB,
    SECRET_OF,
    Flow,
    base64_header,
    code_of,
    decode,
    exchange,
    follow,
    make_client,
    members,
    person,
    signed_in_code,
)

BACKCHANNEL = "https://appli.test/oidc/backchannel"
AFTER = "https://appli.test/au-revoir"


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

class Sent:
    """Avis « envoyés » : (adresse, jeton). `failing` : motif d'échec à renvoyer, None pour réussir."""

    def __init__(self):
        self.calls, self.failing = [], None

    def __call__(self, uri, token):
        self.calls.append((uri, token))
        return self.failing

    def __iter__(self):
        return iter(self.calls)

    def __len__(self):
        return len(self.calls)

    def clear(self):
        self.calls.clear()

    def claims(self):
        return [verify_logout_token(token) for _, token in self.calls]


@pytest.fixture
def sent(monkeypatch):
    recorder = Sent()
    monkeypatch.setattr(logout, "post", recorder)
    return recorder


def verify_logout_token(token):
    header = base64_header(token)
    keys = {key["kid"]: key for key in oidc.jwks()["keys"]}
    claims = jwt.decode(token, RSAKey.import_key(keys[header["kid"]]), algorithms=["RS256"]).claims
    return {"header": header, **claims}


def with_backchannel(client, uri=BACKCHANNEL, after=(AFTER,)):
    client.backchannel_logout_uri = uri
    client.post_logout_redirect_uris = list(after)
    db.session.commit()
    return client


@pytest.fixture
def app_a(members, person):
    return with_backchannel(make_client(allowed=[members], shared=[members], name="Appli A"))


@pytest.fixture
def app_b(members, person):
    return with_backchannel(make_client(allowed=[members], shared=[members], name="Appli B"),
                            uri="https://b.test/backchannel", after=())


@pytest.fixture
def browser(app, person, client):
    login(client, person)
    return client


def connect(http, client, scope="openid profile email groups"):
    """Connexion complète à `client` depuis le navigateur `http` : renvoie la réponse du point d'accès token."""
    flow = Flow(client, scope=scope)
    response = exchange(http, flow, signed_in_code(http, flow))
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def sid_of(http_account):
    return db.session.scalar(db.select(LoginSession.sid).filter_by(account_id=http_account.id)
                             .order_by(LoginSession.created_at.desc()))


def notices():
    return db.session.scalars(db.select(OidcLogout).order_by(OidcLogout.id)).all()


def app_sessions():
    return db.session.scalars(db.select(OidcSession).order_by(OidcSession.id)).all()


# ---------------------------------------------------------------------------
# sid dans le jeton d'identité, et sessions d'appli notées
# ---------------------------------------------------------------------------

def test_id_token_carries_the_public_sid_of_the_browser_session(browser, app_a, person):
    claims = decode(connect(browser, app_a)["id_token"])
    login_session = db.session.scalar(db.select(LoginSession).filter_by(account_id=person.id))
    assert claims["sid"] == login_session.sid and claims["sid"] != login_session.id


def test_two_browsers_get_two_sids(app, browser, app_a, person):
    other = app.test_client()
    login(other, person)
    first, second = decode(connect(browser, app_a)["id_token"]), decode(connect(other, app_a)["id_token"])
    assert first["sid"] != second["sid"] and first["sub"] == second["sub"]


def test_exchange_records_the_app_session_with_the_groups_sent(browser, app_a, person):
    connect(browser, app_a)
    (row,) = app_sessions()
    assert (row.sid, row.client_id, row.account_id, row.groups) == (sid_of(person), app_a.id, person.id, ["membres"])


def test_without_groups_scope_nothing_is_compared_later(browser, app_a):
    connect(browser, app_a, scope="openid email")
    assert app_sessions()[0].groups is None


def test_second_login_to_the_same_app_reuses_the_row(browser, app_a):
    connect(browser, app_a, scope="openid")
    connect(browser, app_a)
    (row,) = app_sessions()
    assert row.groups == ["membres"]


def test_failed_exchange_records_nothing(browser, app_a):
    flow = Flow(app_a)
    code = signed_in_code(browser, flow)
    assert exchange(browser, flow, code, code_verifier="faux" * 12).status_code == 400
    assert app_sessions() == []


def test_code_is_refused_once_its_session_is_closed(app, browser, app_a, person, sent):
    flow = Flow(app_a)
    code = signed_in_code(browser, flow)
    browser.post("/deconnexion")
    response = exchange(app.test_client(), flow, code)
    assert response.status_code == 400 and response.get_json()["error"] == "invalid_grant"
    assert app_sessions() == [] and db.session.scalar(db.select(db.func.count(OidcToken.id))) == 0


def test_code_issued_before_the_upgrade_still_works(browser, app_a):
    """Un code sans sid (émis avant la phase 4) s'échange encore, simplement sans session d'appli."""
    flow = Flow(app_a)
    code = signed_in_code(browser, flow)
    db.session.execute(db.update(OidcCode).values(sid=None))
    db.session.commit()
    response = exchange(browser, flow, code)
    assert response.status_code == 200 and "sid" not in decode(response.get_json()["id_token"])
    assert app_sessions() == []


# ---------------------------------------------------------------------------
# Le jeton de déconnexion
# ---------------------------------------------------------------------------

def test_logout_token_follows_the_specification(browser, app_a, person, sent):
    sid = decode(connect(browser, app_a)["id_token"])["sid"]
    browser.post("/deconnexion")
    ((uri, _),) = list(sent)
    (claims,) = sent.claims()
    assert uri == BACKCHANNEL
    assert claims["header"]["typ"] == "logout+jwt" and claims["header"]["alg"] == "RS256" and claims["header"]["kid"]
    assert claims["iss"] == "http://localhost:8001" and claims["aud"] == app_a.client_id
    assert claims["sub"] == person.sub and claims["sid"] == sid
    assert claims["events"] == {"http://schemas.openid.net/event/backchannel-logout": {}}
    assert "nonce" not in claims and len(claims["jti"]) >= 16
    assert claims["exp"] - claims["iat"] == logout.TOKEN_LIFETIME and abs(claims["iat"] - time.time()) < 5


def test_every_logout_token_has_its_own_jti(app_a, person):
    first = jwt.decode(logout.logout_token(app_a, person.sub, "s"), RSAKey.import_key(oidc.active_key()[1]))
    second = jwt.decode(logout.logout_token(app_a, person.sub, "s"), RSAKey.import_key(oidc.active_key()[1]))
    assert first.claims["jti"] != second.claims["jti"]


def test_logout_token_without_sid_has_only_sub(app_a, person):
    token = jwt.decode(logout.logout_token(app_a, person.sub, None), RSAKey.import_key(oidc.active_key()[1]))
    assert "sid" not in token.claims and token.claims["sub"] == person.sub


@pytest.mark.parametrize("header, extra", [({"typ": "logout+jwt"}, {}),
                                           ({}, {"events": {logout.EVENT: {}}})])
def test_either_mark_of_a_logout_token_is_enough_to_refuse_it(app_a, person, header, extra):
    kid, private = oidc.active_key()
    token = jwt.encode({"alg": "RS256", "kid": kid, **header},
                       {"iss": "http://localhost:8001", "aud": app_a.client_id, "sub": person.sub, **extra},
                       RSAKey.import_key(private))
    assert oidc.hint_claims(token) is None


def test_genuine_hint_shape_is_accepted(app_a, person):
    kid, private = oidc.active_key()
    token = jwt.encode({"alg": "RS256", "kid": kid, "typ": "JWT"},
                       {"iss": "http://localhost:8001", "aud": app_a.client_id, "sub": person.sub},
                       RSAKey.import_key(private))
    assert oidc.hint_claims(token)["sub"] == person.sub


@pytest.mark.parametrize("claims", [{"iss": "https://autre.test"}, {"sub": 42}, {"sub": None}])
def test_hint_from_another_issuer_or_without_a_string_sub_is_refused(app_a, person, claims):
    kid, private = oidc.active_key()
    token = jwt.encode({"alg": "RS256", "kid": kid},
                       {"iss": "http://localhost:8001", "aud": app_a.client_id, "sub": person.sub, **claims},
                       RSAKey.import_key(private))
    assert oidc.hint_claims(token) is None


def test_logout_token_is_never_accepted_as_an_id_token_hint(browser, app_a, person):
    token = logout.logout_token(app_a, person.sub, sid_of(person))
    assert oidc.hint_claims(token) is None and oidc.subject_of_hint(token) is None
    response = browser.get("/oidc/deconnexion?" + urlencode({"id_token_hint": token}))
    assert response.status_code == 200 and "Se déconnecter" in response.get_data(as_text=True)  # il faut confirmer
    assert browser.get("/").status_code == 200


# ---------------------------------------------------------------------------
# Toutes les façons de fermer une session préviennent les applis
# ---------------------------------------------------------------------------

def test_local_logout_notifies_every_app_used_in_this_session(browser, app_a, app_b, person, sent):
    connect(browser, app_a)
    connect(browser, app_b)
    browser.post("/deconnexion")
    assert sorted(uri for uri, _ in sent) == sorted([BACKCHANNEL, "https://b.test/backchannel"])
    assert notices() == [] and app_sessions() == []


def test_app_without_backchannel_address_is_not_notified(browser, members, person, sent):
    silent = make_client(allowed=[members], name="Muette")
    connect(browser, silent)
    browser.post("/deconnexion")
    assert len(sent) == 0 and notices() == [] and app_sessions() == []


def test_app_never_used_is_not_notified(browser, app_a, app_b, sent):
    connect(browser, app_a)
    browser.post("/deconnexion")
    assert [uri for uri, _ in sent] == [BACKCHANNEL]


def test_closing_one_device_notifies_only_its_sid(app, browser, app_a, person, sent):
    phone = app.test_client()
    login(phone, person)
    mine = decode(connect(browser, app_a)["id_token"])["sid"]
    phone_sid = decode(connect(phone, app_a)["id_token"])["sid"]
    browser.post(f"/mon-compte/sessions/{phone_sid}/fermer")
    assert [claims["sid"] for claims in sent.claims()] == [phone_sid]
    assert [row.sid for row in app_sessions()] == [mine]
    assert phone.get("/").status_code == 302 and browser.get("/").status_code == 200


def test_closing_other_devices_spares_this_one(app, browser, app_a, person, sent):
    phone = app.test_client()
    login(phone, person)
    mine = decode(connect(browser, app_a)["id_token"])["sid"]
    theirs = decode(connect(phone, app_a)["id_token"])["sid"]
    browser.post("/mon-compte/sessions/fermer")
    assert [claims["sid"] for claims in sent.claims()] == [theirs]
    assert [row.sid for row in app_sessions()] == [mine]


def test_password_change_notifies_the_other_devices(app, browser, app_a, person, sent):
    phone = app.test_client()
    login(phone, person)
    theirs = decode(connect(phone, app_a)["id_token"])["sid"]
    connect(browser, app_a)
    response = browser.post("/mot-de-passe", data={"current": PASSWORD, "new": "nouveau-mot-de-passe-1",
                                                   "confirm": "nouveau-mot-de-passe-1"})
    assert response.status_code == 302
    assert [claims["sid"] for claims in sent.claims()] == [theirs]


def test_cut_account_notifies_all_its_sessions_and_kills_its_tokens(app, app_a, app_b, person, sent):
    laptop, phone = app.test_client(), app.test_client()
    login(laptop, person)
    login(phone, person)
    token = connect(laptop, app_a)["access_token"]
    connect(phone, app_b)
    admin = app.test_client()
    login(admin, make_account(Role.ADMIN, email="chef@asso.test"))
    sent.clear()
    assert admin.post(f"/comptes/{person.id}/couper").status_code == 302
    assert sorted(claims["aud"] for claims in sent.claims()) == sorted([app_a.client_id, app_b.client_id])
    assert all(claims["sub"] == person.sub for claims in sent.claims())
    assert laptop.get("/oidc/userinfo", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_totp_reset_by_an_admin_notifies_the_apps(app, app_a, members, sent):
    target = make_account(Role.GESTIONNAIRE, email="g@asso.test")
    from .conftest import enroll_totp
    enroll_totp(target)
    target.groups = [members]
    db.session.commit()
    device = app.test_client()
    login(device, target)
    connect(device, app_a)
    admin = app.test_client()
    login(admin, make_account(Role.ADMIN, email="chef@asso.test"))
    sent.clear()
    admin.post(f"/comptes/{target.id}/double-authentification/reinitialiser")
    assert [claims["sub"] for claims in sent.claims()] == [target.sub]


def test_admin_closes_one_session_of_someone(app, app_a, person, sent):
    device = app.test_client()
    login(device, person)
    sid = decode(connect(device, app_a)["id_token"])["sid"]
    admin = app.test_client()
    login(admin, make_account(Role.ADMIN, email="chef@asso.test"))
    sent.clear()
    admin.post(f"/comptes/{person.id}/sessions/{sid}/fermer")
    assert [claims["sid"] for claims in sent.claims()] == [sid]


def test_logging_everyone_out_notifies_every_app(app, app_a, person, sent):
    device = app.test_client()
    login(device, person)
    connect(device, app_a)
    admin = app.test_client()
    login(admin, make_account(Role.ADMIN, email="chef@asso.test"))
    sent.clear()
    assert admin.post("/parametres/deconnecter-tout").status_code == 302
    assert [claims["sub"] for claims in sent.claims()] == [person.sub]


def test_new_login_on_the_same_browser_ends_the_previous_session(browser, app_a, person, sent):
    """prompt=login : la nouvelle saisie remplace la session, dont les applis sont prévenues (autre personne possible)."""
    old = decode(connect(browser, app_a)["id_token"])["sid"]
    flow = Flow(app_a, prompt="login")
    login_url = browser.get(flow.url).location
    browser.post(login_url, data={"email": person.email, "password": PASSWORD})
    assert [claims["sid"] for claims in sent.claims()] == [old]
    assert sid_of(person) != old


def test_expired_sessions_are_closed_by_the_scheduler(app, browser, app_a, person, sent):
    connect(browser, app_a)
    later = datetime.now() + timedelta(days=366)  # au-delà de « rester connecté » (365 jours)
    summary = tasks.run(later)
    assert "1 session(s) trop ancienne(s) fermée(s)" in summary
    assert [claims["sub"] for claims in sent.claims()] == [person.sub]


def test_scheduler_catches_sessions_closed_behind_its_back(browser, app_a, person, sent):
    sid = decode(connect(browser, app_a)["id_token"])["sid"]
    db.session.execute(db.delete(LoginSession))  # comme une suppression de compte en base, sans passer par sessions
    db.session.commit()
    assert "1 session(s) d'appli rattrapée(s)" in tasks.run()
    assert [claims["sid"] for claims in sent.claims()] == [sid]


# ---------------------------------------------------------------------------
# Accès perdu, groupes transmis changés
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_http(app):
    http = app.test_client()
    login(http, make_account(Role.ADMIN, email="chef@asso.test"))
    return http


def set_groups(admin_http, account, *groups):
    return admin_http.post(f"/comptes/{account.id}/groupes", data={"groups": [g.id for g in groups]})


def test_leaving_the_allowed_group_notifies_and_revokes(admin_http, browser, app_a, person, members, sent):
    token = connect(browser, app_a)["access_token"]
    sent.clear()
    set_groups(admin_http, person)
    assert [claims["sub"] for claims in sent.claims()] == [person.sub]
    assert browser.get("/oidc/userinfo", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert db.session.scalar(db.select(db.func.count(OidcToken.id))) == 0  # annulés, pas seulement refusés
    assert browser.get("/").status_code == 200  # la session sur ce serveur, elle, reste ouverte


def test_shared_group_change_notifies_but_keeps_access(admin_http, browser, app_a, person, members, sent):
    regie = AccessGroup(key="regie", name="Régie")
    db.session.add(regie)
    app_a.shared_groups = [members, regie]
    db.session.commit()
    token = connect(browser, app_a)["access_token"]
    sent.clear()
    set_groups(admin_http, person, members, regie)
    assert [claims["sub"] for claims in sent.claims()] == [person.sub]
    info = browser.get("/oidc/userinfo", headers={"Authorization": f"Bearer {token}"})
    assert info.status_code == 200 and info.get_json()["groups"] == ["membres", "regie"]


def test_unrelated_group_change_notifies_nobody(admin_http, browser, app_a, person, members, sent):
    other = AccessGroup(key="autre", name="Autre")
    db.session.add(other)
    db.session.commit()
    connect(browser, app_a)
    sent.clear()
    set_groups(admin_http, person, members, other)  # ni autorisé ni transmis pour Appli A
    assert len(sent) == 0 and len(app_sessions()) == 1


def test_group_change_is_ignored_when_groups_were_not_requested(admin_http, browser, app_a, person, members, sent):
    regie = AccessGroup(key="regie", name="Régie")
    db.session.add(regie)
    app_a.shared_groups = [members, regie]
    db.session.commit()
    connect(browser, app_a, scope="openid email")
    sent.clear()
    set_groups(admin_http, person, members, regie)
    assert len(sent) == 0


def test_only_the_changed_account_is_notified(app, admin_http, browser, app_a, person, members, sent):
    other = make_account(email="autre@asso.test")
    other.groups = [members]
    db.session.commit()
    device = app.test_client()
    login(device, other)
    connect(device, app_a)
    connect(browser, app_a)
    sent.clear()
    set_groups(admin_http, other)
    assert [claims["sub"] for claims in sent.claims()] == [other.sub]


def edit_app(admin_http, client, **fields):
    data = {"name": client.name, "redirect_uris": "\n".join(client.redirect_uris),
            "post_logout_redirect_uris": "\n".join(client.post_logout_redirect_uris),
            "backchannel_logout_uri": client.backchannel_logout_uri or "",
            "allowed_groups": [g.id for g in client.allowed_groups],
            "shared_groups": [g.id for g in client.shared_groups], **fields}
    if client.first_party:
        data.setdefault("first_party", "y")
    return admin_http.post(f"/applications/{client.id}/modifier", data=data)


def test_removing_an_allowed_group_from_the_app_notifies_its_members(admin_http, browser, app_a, person, sent):
    connect(browser, app_a)
    sent.clear()
    assert edit_app(admin_http, app_a, allowed_groups=[]).status_code == 302
    assert [claims["sub"] for claims in sent.claims()] == [person.sub]


def test_changing_the_groups_shared_by_the_app_notifies(admin_http, browser, app_a, person, sent):
    connect(browser, app_a)
    sent.clear()
    edit_app(admin_http, app_a, shared_groups=[])
    assert [claims["sub"] for claims in sent.claims()] == [person.sub]


def test_renaming_the_app_notifies_nobody(admin_http, browser, app_a, sent):
    connect(browser, app_a)
    sent.clear()
    edit_app(admin_http, app_a, name="Appli A renommée")
    assert len(sent) == 0 and len(app_sessions()) == 1


def test_closing_the_app_notifies_everyone_in_it(admin_http, browser, app_a, person, sent):
    connect(browser, app_a)
    sent.clear()
    admin_http.post(f"/applications/{app_a.id}/activer")
    assert [claims["sub"] for claims in sent.claims()] == [person.sub] and app_sessions() == []


def test_revoking_consent_notifies_the_app(browser, members, person, sent):
    third = with_backchannel(make_client(allowed=[members], first_party=False, name="Tierce"))
    flow = Flow(third)
    page = follow(browser, browser.get(flow.url))
    query = re.search(r'name="query" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    import html as html_lib
    location = follow(browser, browser.post("/oidc/consentement", data={"query": html_lib.unescape(query),
                                                                          "decision": "allow"}))
    assert exchange(browser, flow, code_of(location)).status_code == 200
    sent.clear()
    browser.post(f"/mon-compte/applications/{third.id}/retirer")
    assert [claims["aud"] for claims in sent.claims()] == [third.client_id]
    assert db.session.get(OidcConsent, (person.id, third.id)) is None


# ---------------------------------------------------------------------------
# File d'envoi : échecs, nouveaux essais, abandon
# ---------------------------------------------------------------------------

def test_failed_notice_waits_in_the_queue(browser, app_a, sent):
    connect(browser, app_a)
    sent.failing = "réponse 503"
    before = datetime.now()
    browser.post("/deconnexion")
    (notice,) = notices()
    assert notice.attempts == 1 and notice.last_error == "réponse 503"
    assert before + logout.RETRY_DELAYS[0] <= notice.next_attempt_at <= datetime.now() + logout.RETRY_DELAYS[0]


def test_app_down_then_back_receives_the_notice(browser, app_a, person, sent):
    connect(browser, app_a)
    sent.failing = "injoignable"
    browser.post("/deconnexion")
    assert len(notices()) == 1
    assert logout.deliver_pending() == (0, 0, 0)  # pas encore l'heure du nouvel essai
    sent.failing = None
    summary = tasks.run(datetime.now() + timedelta(minutes=2))
    assert "1 reçu(s)" in summary and notices() == []
    assert [claims["sub"] for claims in sent.claims()] == [person.sub, person.sub]


def test_retry_delays_grow_then_stay(app_a, person, sent):
    db.session.add(OidcLogout(client_id=app_a.id, sub=person.sub, sid="s"))
    db.session.commit()
    sent.failing = "réponse 500"
    now = datetime.now()
    seen = []
    for _ in range(len(logout.RETRY_DELAYS) + 2):
        logout.deliver_pending(now)
        notice = notices()[0]
        seen.append(notice.next_attempt_at - now)
        now = notice.next_attempt_at
    assert seen == list(logout.RETRY_DELAYS) + [logout.RETRY_DELAYS[-1]] * 2


def test_notice_is_abandoned_after_three_days_and_journaled(app_a, person, sent):
    created = datetime.now() - logout.GIVE_UP - timedelta(minutes=1)
    db.session.add(OidcLogout(client_id=app_a.id, sub=person.sub, sid="s", created_at=created,
                              next_attempt_at=created, attempts=12))
    db.session.commit()
    sent.failing = "réponse 500"
    assert logout.deliver_pending() == (0, 0, 1)
    assert notices() == []
    entry = db.session.scalar(db.select(Activity).filter_by(action="oidc.logout_abandoned"))
    assert entry is not None and "Appli A" in entry.message and entry.account_id is None


def test_abandon_is_not_blamed_on_whoever_triggered_the_delivery(browser, app_a, person, sent):
    old = datetime.now() - logout.GIVE_UP - timedelta(minutes=1)
    db.session.add(OidcLogout(client_id=app_a.id, sub="ancien", sid="s", created_at=old, next_attempt_at=old))
    db.session.commit()
    connect(browser, app_a)
    sent.failing = "réponse 500"
    browser.post("/deconnexion")
    entry = db.session.scalar(db.select(Activity).filter_by(action="oidc.logout_abandoned"))
    assert entry.account_id is None


def test_same_session_and_app_is_queued_once(app_a, person, sent):
    sid = "meme-session"
    for _ in range(2):
        db.session.add(OidcSession(sid=sid, client_id=app_a.id, account_id=person.id))
        db.session.flush()
        logout.sessions_ended([sid])
        db.session.flush()
    sent.failing = "réponse 500"
    db.session.commit()
    assert len(notices()) == 1


def test_new_logout_brings_a_waiting_notice_forward(browser, app_a, person, admin_http, members, sent):
    """Un avis en attente (appli injoignable) pour cette session : la déconnexion le fait repartir tout de suite."""
    regie = AccessGroup(key="regie", name="Régie")
    db.session.add(regie)
    app_a.shared_groups = [members, regie]
    db.session.commit()
    connect(browser, app_a)
    sent.failing = "injoignable"
    set_groups(admin_http, person, members, regie)  # avis pour groupes changés, non reçu
    (waiting,) = notices()
    assert waiting.next_attempt_at > datetime.now()
    connect(browser, app_a)  # reconnexion silencieuse, même session
    sent.failing = None
    sent.clear()
    browser.post("/deconnexion")
    assert notices() == [] and [claims["sub"] for claims in sent.claims()] == [person.sub]


def test_claimed_notice_is_not_sent_twice(app_a, person, sent):
    db.session.add(OidcLogout(client_id=app_a.id, sub=person.sub, sid="s"))
    db.session.commit()
    row = db.session.execute(db.select(OidcLogout.id, OidcLogout.next_attempt_at)).one()
    assert logout._claim(row, datetime.now()) is True
    assert logout._claim(row, datetime.now()) is False  # un autre worker arrive avec l'état d'avant
    assert logout.deliver_pending() == (0, 0, 0) and len(sent) == 0


def test_notice_for_an_app_that_lost_its_address_is_dropped(app_a, person, sent):
    db.session.add(OidcLogout(client_id=app_a.id, sub=person.sub, sid="s"))
    app_a.backchannel_logout_uri = None
    db.session.commit()
    assert logout.deliver_pending() == (0, 0, 0) and notices() == [] and len(sent) == 0


def test_deleting_the_app_drops_its_notices_and_sessions(admin_http, browser, app_a, sent):
    connect(browser, app_a)
    sent.failing = "injoignable"
    browser.post("/deconnexion")
    assert len(notices()) == 1
    admin_http.post(f"/applications/{app_a.id}/activer")
    admin_http.post(f"/applications/{app_a.id}/supprimer")
    assert db.session.get(OidcClient, app_a.id) is None
    assert notices() == [] and app_sessions() == []


def test_open_app_cannot_be_deleted_so_nobody_is_left_logged_in(admin_http, browser, app_a, person, sent):
    connect(browser, app_a)
    sent.clear()
    admin_http.post(f"/applications/{app_a.id}/supprimer")
    assert db.session.get(OidcClient, app_a.id) is not None and len(sent) == 0
    admin_http.post(f"/applications/{app_a.id}/activer")  # fermer : l'avis part
    admin_http.post(f"/applications/{app_a.id}/supprimer")
    assert [claims["sub"] for claims in sent.claims()] == [person.sub]


def test_notice_uses_the_current_address(app_a, person, sent):
    db.session.add(OidcLogout(client_id=app_a.id, sub=person.sub, sid="s"))
    app_a.backchannel_logout_uri = "https://nouvelle.test/bc"
    db.session.commit()
    logout.deliver_pending()
    assert [uri for uri, _ in sent] == ["https://nouvelle.test/bc"]


def test_notice_survives_the_deletion_of_the_account(app_a, person, sent):
    db.session.add(OidcLogout(client_id=app_a.id, sub=person.sub, sid="s"))
    db.session.commit()
    sub = person.sub
    db.session.delete(person)
    db.session.commit()
    logout.deliver_pending()
    assert [claims["sub"] for claims in sent.claims()] == [sub]


def test_detail_page_shows_pending_notices(admin_http, app_a, person):
    db.session.add(OidcLogout(client_id=app_a.id, sub=person.sub, sid="s", next_attempt_at=datetime.now() + timedelta(hours=1)))
    db.session.commit()
    html = admin_http.get(f"/applications/{app_a.id}").get_data(as_text=True)
    assert "1 avis de déconnexion en attente" in html and BACKCHANNEL in html and AFTER in html


def test_view_rollback_is_not_committed_by_the_delivery(app, person, sent):
    """Ce qu'une route n'a pas validé ne doit pas l'être par l'envoi qui suit la requête."""
    @app.route("/_essai")
    def essai():
        from flask import g
        person.first_name = "Modifié"
        g.oidc_logout_queued = True
        return "ok"

    app.test_client().get("/_essai")
    db.session.expire_all()
    assert db.session.get(Account, person.id).first_name == "Paul"


# ---------------------------------------------------------------------------
# Envoi réel, vers un petit serveur local
# ---------------------------------------------------------------------------

class Receiver:
    """Serveur HTTP sur 127.0.0.1 qui note les avis reçus et répond le code demandé."""

    def __init__(self, status=200, location=None):
        self.received, self.status, self.location = [], status, location
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                receiver.received.append((self.path, self.headers.get("Content-Type"), self.rfile.read(length).decode()))
                self.send_response(receiver.status)
                if receiver.location:
                    self.send_header("Location", receiver.location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/backchannel"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def receiver():
    server = Receiver()
    yield server
    server.close()


def test_real_post_is_form_encoded(receiver, app_a, person):
    token = logout.logout_token(app_a, person.sub, "s")
    assert logout.post(receiver.url, token) is None
    ((path, content_type, body),) = receiver.received
    assert path == "/backchannel" and content_type == "application/x-www-form-urlencoded"
    assert parse_qs(body) == {"logout_token": [token]}


@pytest.mark.parametrize("status, expected", [(204, None), (202, None), (400, "réponse 400"), (404, "réponse 404"),
                                              (500, "réponse 500"), (501, "réponse 501")])
def test_real_post_status_codes(receiver, status, expected):
    receiver.status = status
    assert logout.post(receiver.url, "jeton") == expected


def test_redirect_is_not_followed(receiver):
    other = Receiver()
    try:
        receiver.status, receiver.location = 302, other.url
        assert logout.post(receiver.url, "jeton") == "réponse 302"
        assert other.received == []
    finally:
        other.close()


def test_unreachable_app_is_a_failure_not_a_crash():
    probe = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = probe.server_port
    probe.server_close()
    problem = logout.post(f"http://127.0.0.1:{port}/bc", "jeton")
    assert problem.startswith("injoignable") and len(problem) <= 255


def test_background_delivery_reaches_the_app(tmp_path, receiver):
    from app import create_app

    from .conftest import Client
    app = create_app(config_for(tmp_path, BACKCHANNEL_IN_BACKGROUND=True))
    app.test_client_class = Client
    with app.app_context():
        reset_database()
        stamp_head()
        group = AccessGroup(key="membres", name="Membres")
        account = make_account(email="bg@asso.test")
        account.groups = [group]
        client = with_backchannel(make_client(allowed=[group], name="Fond"), uri=receiver.url)
        http = app.test_client()
        login(http, account)
        connect(http, client)
        http.post("/deconnexion")
        for _ in range(100):
            if receiver.received:
                break
            time.sleep(0.05)
        assert len(receiver.received) == 1
        for _ in range(100):
            db.session.expire_all()
            if not notices():
                break
            time.sleep(0.05)
        assert notices() == []
        release()


# ---------------------------------------------------------------------------
# Déconnexion demandée par l'appli (end_session_endpoint)
# ---------------------------------------------------------------------------

def end_session(http, **params):
    return http.get("/oidc/deconnexion?" + urlencode(params))


def test_valid_hint_logs_out_without_asking_and_returns_to_the_app(browser, app_a, person, sent):
    id_token = connect(browser, app_a)["id_token"]
    response = end_session(browser, id_token_hint=id_token, post_logout_redirect_uri=AFTER, state="xyz")
    assert response.status_code == 302 and response.location == AFTER + "?state=xyz"
    assert browser.get("/").status_code == 302
    assert [claims["aud"] for claims in sent.claims()] == [app_a.client_id]
    entry = db.session.scalar(db.select(Activity).filter_by(action="auth.logout"))
    assert entry.message == "Déconnexion de Paul Test depuis Appli A"


def test_without_state_the_address_is_unchanged(browser, app_a):
    id_token = connect(browser, app_a)["id_token"]
    assert end_session(browser, id_token_hint=id_token, post_logout_redirect_uri=AFTER).location == AFTER


def test_valid_hint_without_return_address_lands_on_the_login_page(browser, app_a):
    id_token = connect(browser, app_a)["id_token"]
    response = end_session(browser, id_token_hint=id_token)
    assert urlsplit(response.location).path == "/connexion"
    assert "Vous êtes déconnecté" in browser.get(response.location).get_data(as_text=True)


def test_expired_hint_is_still_accepted(browser, app_a, monkeypatch):
    id_token = connect(browser, app_a)["id_token"]
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 10 * 3600)
    assert end_session(browser, id_token_hint=id_token, post_logout_redirect_uri=AFTER).location == AFTER


def test_hint_signed_by_a_retired_key_is_still_accepted(browser, app_a):
    id_token = connect(browser, app_a)["id_token"]
    oidc.rotate_key()
    db.session.commit()
    assert end_session(browser, id_token_hint=id_token, post_logout_redirect_uri=AFTER).location == AFTER


def test_without_hint_the_person_confirms(browser, app_a, sent):
    connect(browser, app_a)
    page = end_session(browser, client_id=app_a.client_id, post_logout_redirect_uri=AFTER, state="s1")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Appli A</strong> demande à vous déconnecter" in html
    assert browser.get("/").status_code == 200 and len(sent) == 0  # rien n'est fait avant la confirmation
    query = re.search(r'name="query" value="([^"]+)"', html).group(1)
    import html as html_lib
    response = browser.post("/oidc/deconnexion/confirmer", data={"query": html_lib.unescape(query),
                                                                  "decision": "logout"})
    assert follow(browser, response) == AFTER + "?state=s1"
    assert browser.get("/").status_code == 302 and len(sent) == 1


def test_staying_logged_in_goes_back_without_logging_out(browser, app_a, sent):
    connect(browser, app_a)
    query = urlencode({"client_id": app_a.client_id, "post_logout_redirect_uri": AFTER, "state": "s2"})
    response = browser.post("/oidc/deconnexion/confirmer", data={"query": query, "decision": "stay"})
    assert follow(browser, response) == AFTER + "?state=s2"
    assert browser.get("/").status_code == 200 and len(sent) == 0


def test_staying_without_return_address_goes_home(browser):
    response = browser.post("/oidc/deconnexion/confirmer", data={"query": "", "decision": "stay"})
    assert urlsplit(response.location).path == "/" and browser.get("/").status_code == 200


def test_confirmation_without_anything_logs_out_to_the_login_page(browser):
    response = browser.post("/oidc/deconnexion/confirmer", data={"query": "", "decision": "logout"})
    assert urlsplit(response.location).path == "/connexion" and browser.get("/").status_code == 302


def test_hint_of_someone_else_asks_for_confirmation(app, browser, app_a, members, person):
    other = make_account(email="autre@asso.test")
    other.groups = [members]
    db.session.commit()
    device = app.test_client()
    login(device, other)
    their_token = connect(device, app_a)["id_token"]
    page = end_session(browser, id_token_hint=their_token, post_logout_redirect_uri=AFTER)
    assert page.status_code == 200 and "Se déconnecter" in page.get_data(as_text=True)
    assert browser.get("/").status_code == 200 and device.get("/").status_code == 200


def test_hint_of_an_older_session_asks_for_confirmation(app, browser, app_a, person):
    phone = app.test_client()
    login(phone, person)
    phone_token = connect(phone, app_a)["id_token"]
    page = end_session(browser, id_token_hint=phone_token)
    assert page.status_code == 200 and browser.get("/").status_code == 200


def test_forged_hint_is_ignored(browser, app_a, person):
    key = RSAKey.generate_key(2048)
    forged = jwt.encode({"alg": "RS256", "kid": oidc.active_key()[0]},
                        {"iss": "http://localhost:8001", "sub": person.sub, "aud": app_a.client_id}, key)
    page = end_session(browser, id_token_hint=forged)
    assert page.status_code == 200 and browser.get("/").status_code == 200  # confirmation demandée
    assert end_session(browser, id_token_hint=forged, post_logout_redirect_uri=AFTER).status_code == 400


@pytest.mark.parametrize("hint", ["", "pas-un-jeton", "a.b.c", "é.é.é", "eyJhbGciOiJub25lIn0.e30."])
def test_garbage_hint_is_ignored(browser, hint):
    response = end_session(browser, id_token_hint=hint)
    assert response.status_code == 200 and browser.get("/").status_code == 200


def test_unregistered_return_address_is_refused(browser, app_a):
    id_token = connect(browser, app_a)["id_token"]
    for uri in ("https://pirate.test/", AFTER + "/", AFTER + "?x=1", CB, AFTER.upper()):
        response = end_session(browser, id_token_hint=id_token, post_logout_redirect_uri=uri)
        assert response.status_code == 400 and "Déconnexion impossible" in response.get_data(as_text=True)
    assert browser.get("/").status_code == 200  # rien n'a été fait


def test_return_address_needs_to_know_the_app(browser):
    assert end_session(browser, post_logout_redirect_uri=AFTER).status_code == 400
    assert end_session(browser, client_id="inconnu", post_logout_redirect_uri=AFTER).status_code == 400


def test_client_id_must_match_the_hint(browser, app_a, app_b):
    id_token = connect(browser, app_a)["id_token"]
    response = end_session(browser, id_token_hint=id_token, client_id=app_b.client_id)
    assert response.status_code == 400 and browser.get("/").status_code == 200


def test_return_address_of_another_app_is_refused(browser, app_a, app_b):
    app_b.post_logout_redirect_uris = ["https://b.test/bye"]
    db.session.commit()
    id_token = connect(browser, app_a)["id_token"]
    assert end_session(browser, id_token_hint=id_token, post_logout_redirect_uri="https://b.test/bye").status_code == 400


def test_closed_app_cannot_redirect(admin_http, browser, app_a):
    id_token = connect(browser, app_a)["id_token"]
    admin_http.post(f"/applications/{app_a.id}/activer")
    assert end_session(browser, id_token_hint=id_token, post_logout_redirect_uri=AFTER).status_code == 400


@pytest.mark.parametrize("name", ["id_token_hint", "client_id", "post_logout_redirect_uri", "state"])
def test_repeated_parameter_is_refused(browser, app_a, name):
    query = urlencode([("client_id", app_a.client_id), (name, "a"), (name, "b")])
    assert browser.get("/oidc/deconnexion?" + query).status_code == 400


def test_anonymous_goes_straight_back(client, app_a):
    response = end_session(client, client_id=app_a.client_id, post_logout_redirect_uri=AFTER, state="z")
    assert response.location == AFTER + "?state=z"
    assert urlsplit(end_session(client).location).path == "/connexion"


def test_app_may_post_the_request(browser, app_a):
    id_token = connect(browser, app_a)["id_token"]
    response = browser.post("/oidc/deconnexion", data={"id_token_hint": id_token, "post_logout_redirect_uri": AFTER})
    assert response.location == AFTER and browser.get("/").status_code == 302


def test_logout_works_while_a_password_change_is_required(browser, app_a, person):
    id_token = connect(browser, app_a)["id_token"]
    person.must_change_password = True
    db.session.commit()
    assert end_session(browser, id_token_hint=id_token, post_logout_redirect_uri=AFTER).location == AFTER


def test_logout_pages_are_never_cached(browser, app_a):
    page = end_session(browser, client_id=app_a.client_id)
    assert "no-store" in page.headers["Cache-Control"]


def test_confirmation_needs_csrf(tmp_path):
    from app import create_app
    app = create_app(config_for(tmp_path, WTF_CSRF_ENABLED=True))
    with app.app_context():
        reset_database()
        account = make_account(email="csrf@asso.test")
        http = app.test_client()
        page = http.get("/connexion").get_data(as_text=True)
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
        http.post("/connexion", data={"email": account.email, "password": PASSWORD, "csrf_token": token})
        response = http.post("/oidc/deconnexion/confirmer", data={"query": "", "decision": "logout"})
        assert response.status_code == 400 and http.get("/").status_code == 200
        release()


def test_html_in_the_app_name_is_escaped(browser, members):
    evil = with_backchannel(make_client(allowed=[members], name="<script>x</script>"))
    html = end_session(browser, client_id=evil.client_id).get_data(as_text=True)
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# Écran Applications
# ---------------------------------------------------------------------------

def create_form(**fields):
    return {"name": "Inventaire", "redirect_uris": CB, **fields}


def test_logout_addresses_are_saved(admin_http):
    response = admin_http.post("/applications/nouvelle", data=create_form(
        post_logout_redirect_uris=f"{AFTER}\n{AFTER}\nhttp://localhost:5000/bye",
        backchannel_logout_uri=f"  {BACKCHANNEL}  "))
    assert response.status_code == 200
    client = db.session.scalar(db.select(OidcClient))
    assert client.post_logout_redirect_uris == [AFTER, "http://localhost:5000/bye"]
    assert client.backchannel_logout_uri == BACKCHANNEL


def test_logout_addresses_are_optional(admin_http):
    admin_http.post("/applications/nouvelle", data=create_form())
    client = db.session.scalar(db.select(OidcClient))
    assert client.post_logout_redirect_uris == [] and client.backchannel_logout_uri is None


@pytest.mark.parametrize("field, value", [
    ("post_logout_redirect_uris", "http://appli.test/bye"),
    ("post_logout_redirect_uris", "https://appli.test/bye#x"),
    ("post_logout_redirect_uris", "javascript:alert(1)"),
    ("post_logout_redirect_uris", "https://*.appli.test/"),
    ("backchannel_logout_uri", "http://appli.test/bc"),
    ("backchannel_logout_uri", "https://appli.test/bc#x"),
    ("backchannel_logout_uri", "https://user:pw@appli.test/bc"),
    ("backchannel_logout_uri", "https://a.test/bc https://b.test/bc"),
    ("backchannel_logout_uri", "ftp://appli.test/bc"),
    ("backchannel_logout_uri", "https://appli.test/" + "a" * 2100),
])
def test_bad_logout_addresses_are_refused(admin_http, field, value):
    response = admin_http.post("/applications/nouvelle", data=create_form(**{field: value}))
    assert response.status_code == 200 and db.session.scalar(db.select(OidcClient)) is None


def test_edit_keeps_and_journals_logout_addresses(admin_http, app_a):
    page = admin_http.get(f"/applications/{app_a.id}/modifier").get_data(as_text=True)
    assert BACKCHANNEL in page and AFTER in page
    edit_app(admin_http, app_a, backchannel_logout_uri="")
    assert db.session.get(OidcClient, app_a.id).backchannel_logout_uri is None
    entry = db.session.scalar(db.select(Activity).filter_by(action="client.update"))
    assert entry.changes["avis de déconnexion"] == [BACKCHANNEL, None]


# ---------------------------------------------------------------------------
# sid des sessions
# ---------------------------------------------------------------------------

def test_every_session_gets_a_distinct_uuid_sid(app, person):
    for _ in range(3):
        login(app.test_client(), person)
    sids = db.session.scalars(db.select(LoginSession.sid)).all()
    assert len(set(sids)) == 3 and all(re.fullmatch(r"[0-9a-f-]{36}", sid) for sid in sids)


def test_current_sid_is_none_when_anonymous(app):
    with app.test_request_context():
        assert sessions.current_sid() is None


def test_keys_table_untouched_by_logout(browser, app_a, sent):
    connect(browser, app_a)
    before = db.session.scalar(db.select(db.func.count(OidcKey.kid)))
    browser.post("/deconnexion")
    assert db.session.scalar(db.select(db.func.count(OidcKey.kid))) == before
