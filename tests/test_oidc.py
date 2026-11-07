"""Serveur OIDC : le parcours complet, puis tout ce qui doit être refusé. Chaque règle du plan a son test."""
import base64
import hashlib
import html
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import pyotp
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

from app import create_app
from app.extensions import db
from app.models import AccessGroup, Activity, OidcClient, OidcCode, OidcConsent, OidcKey, OidcToken, Role
from app.services import oidc, tasks
from app.services import totp as totp_service

from .conftest import config_for, login, make_account, stamp_head

CB = "https://appli.test/retour"
SECRET_OF = {}


def pkce():
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def make_client(allowed=(), shared=(), first_party=True, name="Appli", uris=(CB,), active=True):
    client = OidcClient(client_id=oidc.new_client_id(), name=name, redirect_uris=list(uris), first_party=first_party,
                        active=active, allowed_groups=list(allowed), shared_groups=list(shared))
    SECRET_OF[client.client_id] = oidc.set_secret(client)
    db.session.add(client)
    db.session.commit()
    return client


class Flow:
    """Demande d'autorisation, telle qu'une appli cliente l'envoie."""

    def __init__(self, client, scope="openid profile email groups", **params):
        self.client = client
        self.verifier, challenge = pkce()
        self.params = {"response_type": "code", "client_id": client.client_id, "redirect_uri": CB, "scope": scope,
                       "state": "etat-1", "nonce": secrets.token_urlsafe(8), "code_challenge": challenge,
                       "code_challenge_method": "S256", **params}
        self.params = {key: value for key, value in self.params.items() if value is not None}

    @property
    def url(self):
        return "/oidc/authorize?" + urlencode(self.params)


def follow(http, response):
    """Suit redirections et page intermédiaire, jusqu'à quitter le serveur ou tomber sur une page à remplir."""
    for _ in range(10):
        if response.status_code in (302, 303):
            location = response.location
            if urlsplit(location).netloc not in ("", "localhost"):
                return location
            response = http.get(location)
        elif response.status_code == 200 and (match := re.search(r'http-equiv="refresh" content="0;url=([^"]+)"',
                                                                 response.get_data(as_text=True))):
            target = html.unescape(match.group(1))
            if urlsplit(target).netloc:
                return target
            response = http.get(target)
        else:
            return response
    raise AssertionError("boucle de redirections")


def code_of(location, state="etat-1"):
    assert isinstance(location, str), f"pas de retour vers l'appli : {location.status_code}"
    assert location.startswith(CB + "?")
    query = parse_qs(urlsplit(location).query)
    assert query["state"] == [state]
    return query["code"][0]


def error_of(location):
    assert isinstance(location, str), f"pas de retour vers l'appli : {location.status_code}"
    assert location.startswith(CB + "?")
    return parse_qs(urlsplit(location).query)["error"][0]


def exchange(http, flow, code, **overrides):
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": CB, "code_verifier": flow.verifier,
            "client_id": flow.client.client_id, "client_secret": SECRET_OF[flow.client.client_id], **overrides}
    return http.post("/oidc/token", data={key: value for key, value in data.items() if value is not None})


def decode(id_token):
    keys = {key["kid"]: key for key in oidc.jwks()["keys"]}
    return jwt.decode(id_token, RSAKey.import_key(keys[base64_header(id_token)["kid"]]), algorithms=["RS256"]).claims


def base64_header(raw):
    import json
    part = raw.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


@pytest.fixture
def members(app):
    return AccessGroup(key="membres", name="Membres")


@pytest.fixture
def person(app, members):
    account = make_account(Role.ADHERENT, email="paul@asso.test", first_name="Paul", email_verified=True)
    account.groups = [members]
    db.session.commit()
    return account


@pytest.fixture
def client_app(members, person):
    return make_client(allowed=[members], shared=[members])


@pytest.fixture
def http(app, person, client):
    login(client, person)
    return client


def signed_in_code(http, flow):
    return code_of(follow(http, http.get(flow.url)))


# ---------------------------------------------------------------------------
# Le parcours normal
# ---------------------------------------------------------------------------

def test_discovery_announces_only_what_is_supported(client):
    meta = client.get("/.well-known/openid-configuration").get_json()
    assert meta["issuer"] == "http://localhost:8001"
    assert meta["authorization_endpoint"] == "http://localhost:8001/oidc/authorize"
    assert meta["response_types_supported"] == ["code"]
    assert meta["grant_types_supported"] == ["authorization_code"]
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["id_token_signing_alg_values_supported"] == ["RS256"]
    assert "none" not in meta["token_endpoint_auth_methods_supported"]
    assert meta["end_session_endpoint"] == "http://localhost:8001/oidc/deconnexion"
    assert meta["backchannel_logout_supported"] is True and meta["backchannel_logout_session_supported"] is True
    assert meta["frontchannel_logout_supported"] is False and "sid" in meta["claims_supported"]


def test_discovery_ignores_the_host_header(client):
    meta = client.get("/.well-known/openid-configuration", headers={"Host": "pirate.test"}).get_json()
    assert "pirate.test" not in str(meta)


def test_full_flow(http, client_app, person):
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    response = exchange(http, flow, code)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"
    token = response.get_json()
    assert token["token_type"] == "Bearer" and "refresh_token" not in token and token["expires_in"] == 3600
    claims = decode(token["id_token"])
    assert claims["iss"] == "http://localhost:8001" and claims["aud"] == client_app.client_id
    assert claims["sub"] == person.sub and claims["nonce"] == flow.params["nonce"]
    assert claims["exp"] > claims["iat"] and claims["auth_time"] <= claims["iat"] and claims["at_hash"]
    assert claims["email"] == "paul@asso.test" and claims["email_verified"] is True and claims["groups"] == ["membres"]
    info = http.get("/oidc/userinfo", headers={"Authorization": f"Bearer {token['access_token']}"})
    assert info.status_code == 200 and info.headers["Cache-Control"] == "no-store"
    assert info.get_json() == {"sub": person.sub, "name": "Paul Test", "given_name": "Paul", "family_name": "Test",
                               "updated_at": int(person.updated_at.timestamp()), "email": "paul@asso.test",
                               "email_verified": True, "groups": ["membres"]}


def test_secret_by_basic_auth_too(http, client_app):
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    credentials = base64.b64encode(f"{client_app.client_id}:{SECRET_OF[client_app.client_id]}".encode()).decode()
    response = http.post("/oidc/token", headers={"Authorization": f"Basic {credentials}"}, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": CB, "code_verifier": flow.verifier})
    assert response.status_code == 200


def test_anonymous_goes_through_login_and_comes_back(app, client, person, client_app):
    flow = Flow(client_app)
    page = follow(client, client.get(flow.url))
    assert "Connexion" in page.get_data(as_text=True)
    next_url = client.get(flow.url).location
    response = client.post(next_url, data={"email": person.email, "password": "motdepasse"})
    assert urlsplit(response.location).path == "/oidc/reprendre"  # et non directement l'appli : voir form-action
    exchange_ok = exchange(client, flow, code_of(follow(client, response)))
    assert exchange_ok.status_code == 200


def test_totp_step_keeps_the_flow(app, client, members):
    admin = make_account(Role.ADMIN, email="admin@asso.test")
    admin.groups = [members]
    db.session.commit()
    flow = Flow(make_client(allowed=[members]))
    response = client.post(client.get(flow.url).location, data={"email": admin.email, "password": "motdepasse"})
    assert response.location.endswith("/connexion/verification")
    from .conftest import totp_code
    response = client.post("/connexion/verification", data={"code": totp_code()})
    assert code_of(follow(client, response))


def test_post_to_authorize_works(http, client_app):
    flow = Flow(client_app)
    response = http.post("/oidc/authorize", data=flow.params)
    assert response.status_code == 303
    assert code_of(follow(http, response))


def test_post_to_authorize_and_token_work_with_csrf_on(tmp_path):
    """TestConfig coupe le CSRF : sans ce test, oublier l'exemption ne se verrait qu'en production."""
    app = create_app(config_for(tmp_path, WTF_CSRF_ENABLED=True))
    with app.app_context():
        db.create_all()
        stamp_head()
        http = app.test_client()
        assert http.post("/oidc/authorize").status_code == 303
        assert http.post("/oidc/token").status_code == 400
        assert http.post("/oidc/userinfo").status_code == 401
        db.session.remove()
        db.drop_all()


def test_consent_form_is_protected_by_csrf(tmp_path):
    app = create_app(config_for(tmp_path, WTF_CSRF_ENABLED=True))
    with app.app_context():
        db.create_all()
        stamp_head()
        account = make_account(email="c@asso.test")
        http = app.test_client()
        page = http.get("/connexion").get_data(as_text=True)
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
        http.post("/connexion", data={"email": account.email, "password": "motdepasse", "csrf_token": token})
        assert http.post("/oidc/consentement", data={"decision": "allow"}).status_code == 400
        db.session.remove()
        db.drop_all()


# ---------------------------------------------------------------------------
# Ce que l'appli reçoit
# ---------------------------------------------------------------------------

def test_scopes_limit_the_claims(http, client_app, person):
    flow = Flow(client_app, scope="openid")
    token = exchange(http, flow, signed_in_code(http, flow)).get_json()
    info = http.get("/oidc/userinfo", headers={"Authorization": f"Bearer {token['access_token']}"}).get_json()
    assert info == {"sub": person.sub}
    claims = decode(token["id_token"])
    assert not {"email", "name", "groups"} & set(claims)


def test_only_shared_groups_are_transmitted(http, members, person):
    other = AccessGroup(key="bureau", name="Bureau")
    person.groups = [members, other]
    db.session.commit()
    flow = Flow(make_client(allowed=[members], shared=[other]))
    token = exchange(http, flow, signed_in_code(http, flow)).get_json()
    assert decode(token["id_token"])["groups"] == ["bureau"]
    flow = Flow(make_client(allowed=[members], shared=[], name="Sans groupes"))
    token = exchange(http, flow, signed_in_code(http, flow)).get_json()
    assert decode(token["id_token"])["groups"] == []


def test_sub_is_opaque(http, client_app, person):
    flow = Flow(client_app)
    claims = decode(exchange(http, flow, signed_in_code(http, flow)).get_json()["id_token"])
    assert claims["sub"] == person.sub != str(person.id)
    assert person.email not in claims["sub"]


def test_unknown_scope_is_ignored(http, client_app):
    flow = Flow(client_app, scope="openid admin email")
    token = exchange(http, flow, signed_in_code(http, flow)).get_json()
    assert token["scope"] == "openid email"


def test_unverified_email_is_reported_as_such(http, client_app, person):
    person.email_verified = False
    db.session.commit()
    flow = Flow(client_app)
    assert decode(exchange(http, flow, signed_in_code(http, flow)).get_json()["id_token"])["email_verified"] is False


# ---------------------------------------------------------------------------
# Les codes
# ---------------------------------------------------------------------------

def test_code_is_stored_as_a_digest_and_nothing_in_clear(http, client_app):
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    assert db.session.get(OidcCode, code) is None and db.session.get(OidcCode, oidc.digest(code)) is not None
    token = exchange(http, flow, code).get_json()["access_token"]
    assert db.session.get(OidcToken, token) is None and db.session.get(OidcToken, oidc.digest(token)) is not None


def test_replayed_code_is_refused_and_cancels_its_tokens(http, client_app, person):
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    token = exchange(http, flow, code).get_json()["access_token"]
    replay = exchange(http, flow, code)
    assert replay.status_code == 400 and replay.get_json()["error"] == "invalid_grant"
    assert http.get("/oidc/userinfo", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert db.session.scalar(db.select(Activity).filter_by(action="oidc.replay")) is not None


def test_two_simultaneous_exchanges_cannot_both_succeed(http, client_app):
    """Le second a lu le code avant que le premier ne valide : c'est la mise à jour conditionnelle qui tranche."""
    from authlib.oauth2.rfc6749 import InvalidGrantError
    from app.services.oidc_server import CodeGrant
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    assert exchange(http, flow, code).status_code == 200
    row = db.session.get(OidcCode, oidc.digest(code))
    with pytest.raises(InvalidGrantError):
        CodeGrant.delete_authorization_code(None, row)


def test_code_expires_after_sixty_seconds(http, client_app):
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    row = db.session.get(OidcCode, oidc.digest(code))
    assert timedelta(seconds=55) < row.expires_at - oidc.utcnow() <= timedelta(seconds=60)
    row.expires_at = oidc.utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert exchange(http, flow, code).get_json()["error"] == "invalid_grant"


def test_code_of_another_client_is_refused(http, client_app, members):
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    other = make_client(allowed=[members], name="Autre")
    response = exchange(http, flow, code, client_id=other.client_id, client_secret=SECRET_OF[other.client_id])
    assert response.get_json()["error"] == "invalid_grant"


def test_wrong_secret_is_refused(http, client_app):
    flow = Flow(client_app)
    response = exchange(http, flow, signed_in_code(http, flow), client_secret="faux")
    assert response.status_code == 401 and response.get_json()["error"] == "invalid_client"


def test_no_secret_is_refused(http, client_app):
    flow = Flow(client_app)
    response = exchange(http, flow, signed_in_code(http, flow), client_secret=None)
    assert response.status_code == 401


def test_redirect_uri_must_match_at_token_time(http, members):
    second = "https://appli.test/autre"
    client_app = make_client(allowed=[members], uris=(CB, second))
    flow = Flow(client_app)
    response = exchange(http, flow, signed_in_code(http, flow), redirect_uri=second)
    assert response.get_json()["error"] == "invalid_grant"
    flow = Flow(client_app)
    response = exchange(http, flow, signed_in_code(http, flow), redirect_uri=None)
    assert response.get_json()["error"] == "invalid_grant"


@pytest.mark.parametrize("grant_type", ["password", "client_credentials", "refresh_token", "implicit"])
def test_other_grant_types_are_refused(http, client_app, grant_type):
    response = http.post("/oidc/token", data={"grant_type": grant_type, "client_id": client_app.client_id,
                                              "client_secret": SECRET_OF[client_app.client_id]})
    assert response.status_code == 400 and response.get_json()["error"] == "unsupported_grant_type"


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def test_pkce_missing_at_authorize(http, client_app):
    location = http.get(Flow(client_app, code_challenge=None, code_challenge_method=None).url).location
    assert error_of(location) == "invalid_request"


@pytest.mark.parametrize("method", ["plain", None, "S512", "s256"])
def test_pkce_method_must_be_s256(http, client_app, method):
    assert error_of(http.get(Flow(client_app, code_challenge_method=method).url).location) == "invalid_request"


def test_pkce_malformed_challenge(http, client_app):
    assert error_of(http.get(Flow(client_app, code_challenge="court").url).location) == "invalid_request"


def test_pkce_verifier_missing(http, client_app):
    flow = Flow(client_app)
    assert exchange(http, flow, signed_in_code(http, flow), code_verifier=None).get_json()["error"] == "invalid_request"


def test_pkce_verifier_wrong(http, client_app):
    flow = Flow(client_app)
    response = exchange(http, flow, signed_in_code(http, flow), code_verifier="x" * 64)
    assert response.get_json()["error"] == "invalid_grant"
    assert "access_token" not in response.get_json()


def test_pkce_verifier_of_the_challenge_itself_is_refused(http, client_app):
    """Le piège de `plain` : renvoyer le challenge comme verifier."""
    flow = Flow(client_app)
    response = exchange(http, flow, signed_in_code(http, flow), code_verifier=flow.params["code_challenge"])
    assert response.get_json()["error"] == "invalid_grant"


# ---------------------------------------------------------------------------
# Adresse de retour et demandes invalides
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("uri", ["https://pirate.test/retour", CB + "/", CB + "?x=1", CB.upper(), "https://appli.test",
                                 CB + "#frag", "https://appli.test/retour/../retour", None])
def test_bad_redirect_uri_never_redirects(http, client_app, uri):
    response = http.get(Flow(client_app, redirect_uri=uri).url)
    assert response.status_code == 400 and "Location" not in response.headers
    assert "pirate" not in response.get_data(as_text=True)


def test_unknown_client_never_redirects(http):
    response = http.get("/oidc/authorize?" + urlencode({"client_id": "inconnu", "redirect_uri": CB,
                                                        "response_type": "code", "scope": "openid"}))
    assert response.status_code == 400 and "Location" not in response.headers


def test_closed_client_is_like_unknown(http, members):
    client_app = make_client(allowed=[members], active=False)
    assert http.get(Flow(client_app).url).status_code == 400


def test_closing_a_client_stops_its_token_exchange_and_tokens(http, client_app):
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    flow2 = Flow(client_app)
    token = exchange(http, flow2, signed_in_code(http, flow2)).get_json()["access_token"]
    client_app.active = False
    db.session.commit()
    assert exchange(http, flow, code).get_json()["error"] == "invalid_client"
    assert http.get("/oidc/userinfo", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_without_openid_scope_is_refused(http, client_app):
    assert error_of(http.get(Flow(client_app, scope="email").url).location) == "invalid_scope"
    assert error_of(http.get(Flow(client_app, scope=None).url).location) == "invalid_scope"


@pytest.mark.parametrize("response_type", ["token", "id_token", "code id_token", "none"])
def test_other_response_types_are_refused(http, client_app, response_type):
    assert error_of(http.get(Flow(client_app, response_type=response_type).url).location) in (
        "unsupported_response_type", "unauthorized_client")


@pytest.mark.parametrize("param,error", [("request", "request_not_supported"), ("request_uri", "request_uri_not_supported"),
                                         ("registration", "registration_not_supported")])
def test_unsupported_parameters(http, client_app, param, error):
    assert error_of(http.get(Flow(client_app, **{param: "x"}).url).location) == error


@pytest.mark.parametrize("mode", ["fragment", "form_post"])
def test_other_response_modes_are_refused(http, client_app, mode):
    location = http.get(Flow(client_app, response_mode=mode).url).location
    assert error_of(location) == "invalid_request"


def test_repeated_parameter_is_refused(http, client_app):
    response = http.get(Flow(client_app).url + "&redirect_uri=https%3A%2F%2Fpirate.test")
    assert response.status_code == 400 and "Location" not in response.headers


def test_nonce_replay_is_refused(http, client_app):
    flow = Flow(client_app)
    signed_in_code(http, flow)
    assert error_of(http.get(Flow(client_app, nonce=flow.params["nonce"]).url).location) == "invalid_request"


@pytest.mark.parametrize("extra", [{"inconnu": "x"}, {"acr_values": "1 2"}, {"ui_locales": "fr"}, {"login_hint": "a@b.c"},
                                   {"claims_locales": "fr"}, {"display": "page"}, {"prompt": "select_account"},
                                   {"scope": "email groups openid"}, {"scope": "openid address phone"}])
def test_harmless_parameters_are_ignored(http, client_app, extra):
    """Ce que la suite de conformité vérifie : un paramètre inconnu ou facultatif ne fait pas échouer la demande."""
    assert code_of(follow(http, http.get(Flow(client_app, **extra).url)))


def test_response_type_missing(http, client_app):
    assert error_of(http.get(Flow(client_app, response_type=None).url).location) == "unsupported_response_type"


def test_state_is_returned_on_errors(http, client_app):
    location = http.get(Flow(client_app, scope="email", state="a b&c").url).location
    assert parse_qs(urlsplit(location).query)["state"] == ["a b&c"]


# ---------------------------------------------------------------------------
# Qui peut entrer
# ---------------------------------------------------------------------------

def test_outside_the_allowed_groups(http, person):
    other = AccessGroup(key="autres", name="Autres")
    client_app = make_client(allowed=[other])
    response = http.get(Flow(client_app).url)
    assert response.status_code == 403 and "Appli" in response.get_data(as_text=True)
    assert db.session.scalar(db.select(Activity).filter_by(action="oidc.denied")) is not None
    assert db.session.scalar(db.select(db.func.count(OidcCode.id))) == 0


def test_no_group_means_nobody(http):
    assert http.get(Flow(make_client(allowed=[])).url).status_code == 403


def test_an_admin_is_not_above_the_groups(app, client):
    admin = make_account(Role.ADMIN, email="admin@asso.test")
    login(client, admin)
    assert client.get(Flow(make_client(allowed=[])).url).status_code == 403


def test_silent_outside_groups_tells_the_client(http):
    assert error_of(http.get(Flow(make_client(allowed=[]), prompt="none").url).location) == "access_denied"


def test_cut_account_cannot_exchange_or_read(http, client_app, person):
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    flow2 = Flow(client_app)
    token = exchange(http, flow2, signed_in_code(http, flow2)).get_json()["access_token"]
    person.active = False
    db.session.commit()
    assert exchange(http, flow, code).get_json()["error"] == "invalid_grant"
    assert http.get("/oidc/userinfo", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_cut_screen_deletes_tokens(app, client, http, client_app, person):
    flow = Flow(client_app)
    exchange(http, flow, signed_in_code(http, flow))
    admin_http = app.test_client()
    login(admin_http, make_account(Role.ADMIN, email="admin@asso.test"))
    assert admin_http.post(f"/comptes/{person.id}/couper").status_code == 302
    assert db.session.scalar(db.select(db.func.count(OidcToken.id))) == 0


def test_cut_account_session_is_refused_at_authorize(http, client_app, person):
    person.active = False
    db.session.commit()
    response = http.get(Flow(client_app).url)
    assert urlsplit(response.location).path == "/connexion"  # un compte coupé n'est plus connecté


def test_removed_from_group_loses_access_at_once(http, client_app, person):
    flow = Flow(client_app)
    token = exchange(http, flow, signed_in_code(http, flow)).get_json()["access_token"]
    flow2 = Flow(client_app)
    code = signed_in_code(http, flow2)
    person.groups = []
    db.session.commit()
    assert http.get("/oidc/userinfo", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert exchange(http, flow2, code).get_json()["error"] == "invalid_grant"
    assert http.get(Flow(client_app).url).status_code == 403


def test_must_change_password_first_then_returns_to_the_client(http, client_app, person):
    person.must_change_password = True
    db.session.commit()
    flow = Flow(client_app)
    page = follow(http, http.get(flow.url))
    assert "mot de passe" in page.get_data(as_text=True).lower()
    assert db.session.scalar(db.select(db.func.count(OidcCode.id))) == 0
    response = http.post("/mot-de-passe", data={"current": "motdepasse", "new": "nouveau-passe-1", "confirm": "nouveau-passe-1"})
    assert code_of(follow(http, response))


def test_must_change_password_silent(http, client_app, person):
    person.must_change_password = True
    db.session.commit()
    assert error_of(http.get(Flow(client_app, prompt="none").url).location) == "interaction_required"


def test_totp_enrolment_required_first(app, client, members):
    """Un rôle à double authentification obligatoire qui ne l'a pas : pas de code tant qu'elle n'est pas en place."""
    db.session.add(Role(key="regie", name="Régie", permissions=["client.manage"]))
    account = make_account("regie", email="r@asso.test")
    account.groups = [members]
    db.session.commit()
    login(client, account)
    client_app = make_client(allowed=[members])
    page = follow(client, client.get(Flow(client_app).url))
    assert not isinstance(page, str) and "double authentification" in page.get_data(as_text=True).lower()
    assert error_of(client.get(Flow(client_app, prompt="none").url).location) == "interaction_required"


def test_totp_enrolment_then_returns_to_the_client(app, client, members):
    db.session.add(Role(key="regie", name="Régie", permissions=["client.manage"]))
    account = make_account("regie", email="r@asso.test")
    account.groups = [members]
    db.session.commit()
    login(client, account)
    follow(client, client.get(Flow(make_client(allowed=[members])).url))  # arrive sur la configuration
    secret = totp_service.secret_of(db.session.get(type(account), account.id))
    page = client.post("/mon-compte/double-authentification", data={"password": "motdepasse",
                                                                    "code": pyotp.TOTP(secret).now()})
    assert "Je les ai conservés" in page.get_data(as_text=True)
    assert code_of(follow(client, client.get("/mon-compte")))  # le bouton de cet écran


def test_resume_is_dropped_after_a_while(http, client_app, person):
    person.must_change_password = True
    db.session.commit()
    http.get(Flow(client_app).url)
    with http.session_transaction() as cookie:
        cookie["oidc_resume"]["at"] -= oidc.RESUME_LIFETIME + 1
        cookie.modified = True
    response = http.post("/mot-de-passe", data={"current": "motdepasse", "new": "nouveau-passe-1", "confirm": "nouveau-passe-1"})
    page = follow(http, response)  # la demande trop ancienne est oubliée : on reste sur le serveur
    assert not isinstance(page, str) and page.status_code == 200


def test_logout_forgets_a_pending_flow(http, client_app, person):
    person.must_change_password = True
    db.session.commit()
    http.get(Flow(client_app).url)
    http.post("/deconnexion")
    with http.session_transaction() as cookie:
        assert "oidc_resume" not in cookie


# ---------------------------------------------------------------------------
# prompt, max_age, id_token_hint
# ---------------------------------------------------------------------------

def test_prompt_none_anonymous(client, client_app):
    assert error_of(client.get(Flow(client_app, prompt="none").url).location) == "login_required"


def test_prompt_none_connected(http, client_app):
    assert code_of(http.get(Flow(client_app, prompt="none").url).location)


def test_prompt_none_with_another_value(http, client_app):
    assert error_of(http.get(Flow(client_app, prompt="none login").url).location) == "invalid_request"


def test_prompt_login_asks_again_without_looping(http, client_app, person):
    flow = Flow(client_app, prompt="login")
    response = http.get(flow.url)
    assert urlsplit(response.location).path == "/connexion"
    assert http.get("/mon-compte").status_code == 200  # un simple lien ne déconnecte personne
    response = http.post(response.location, data={"email": person.email, "password": "motdepasse"})
    assert code_of(follow(http, response))


def test_prompt_login_consent_asks_both(http, members, person):
    flow = Flow(make_client(allowed=[members], first_party=False), prompt="login consent")
    response = http.post(http.get(flow.url).location, data={"email": person.email, "password": "motdepasse"})
    assert "Autoriser" in follow(http, response).get_data(as_text=True)


def test_max_age_exceeded_asks_again(http, client_app, person):
    from app.models import LoginSession
    db.session.execute(db.update(LoginSession).values(created_at=datetime.now() - timedelta(hours=2)))
    db.session.commit()
    flow = Flow(client_app, max_age="3600")
    response = http.get(flow.url)
    assert urlsplit(response.location).path == "/connexion"
    response = http.post(response.location, data={"email": person.email, "password": "motdepasse"})
    claims = decode(exchange(http, flow, code_of(follow(http, response))).get_json()["id_token"])
    assert claims["auth_time"] > datetime.now().timestamp() - 60


def test_max_age_zero_does_not_loop(http, client_app, person):
    response = http.get(Flow(client_app, max_age="0").url)
    if urlsplit(response.location).path == "/connexion":
        response = http.post(response.location, data={"email": person.email, "password": "motdepasse"})
    assert code_of(follow(http, response))


def test_max_age_not_exceeded_keeps_session(http, client_app):
    assert code_of(http.get(Flow(client_app, max_age="3600").url).location)


def test_max_age_exceeded_silent(http, client_app):
    from app.models import LoginSession
    db.session.execute(db.update(LoginSession).values(created_at=datetime.now() - timedelta(hours=2)))
    db.session.commit()
    assert error_of(http.get(Flow(client_app, max_age="3600", prompt="none").url).location) == "login_required"


@pytest.mark.parametrize("value", ["abc", "-1", "1.5", "", "²", "9" * 5000])
def test_max_age_invalid(http, client_app, value):
    assert error_of(http.get(Flow(client_app, max_age=value).url).location) == "invalid_request"


def test_auth_time_is_the_real_login_not_the_request(http, client_app):
    from app.models import LoginSession
    old = datetime.now() - timedelta(hours=3)
    db.session.execute(db.update(LoginSession).values(created_at=old))
    db.session.commit()
    flow = Flow(client_app)
    claims = decode(exchange(http, flow, signed_in_code(http, flow)).get_json()["id_token"])
    assert claims["auth_time"] == int(old.timestamp())


def test_id_token_hint_same_person(http, client_app):
    flow = Flow(client_app)
    hint = exchange(http, flow, signed_in_code(http, flow)).get_json()["id_token"]
    assert code_of(http.get(Flow(client_app, prompt="none", id_token_hint=hint).url).location)


def test_id_token_hint_other_person_or_forged(http, client_app, members):
    flow = Flow(client_app)
    hint = exchange(http, flow, signed_in_code(http, flow)).get_json()["id_token"]
    head, body, signature = hint.split(".")
    forged = f"{head}.{body}.{signature[::-1]}"
    foreign = jwt.encode({"alg": "RS256", "kid": "autre"}, {"sub": "x", "iss": "y"}, RSAKey.generate_key(2048), ["RS256"])
    for bad in (forged, foreign, "n-importe-quoi", "a.b.c"):
        assert error_of(http.get(Flow(client_app, prompt="none", id_token_hint=bad).url).location) == "login_required"


# ---------------------------------------------------------------------------
# Consentement
# ---------------------------------------------------------------------------

def consent_page(http, flow):
    page = http.get(flow.url)
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    return text, html.unescape(re.search(r'name="query" value="([^"]+)"', text).group(1))


def test_third_party_asks_then_remembers(http, members):
    client_app = make_client(allowed=[members], shared=[members], first_party=False, name="Tierce <b>")
    flow = Flow(client_app)
    text, query = consent_page(http, flow)
    assert "Tierce &lt;b&gt;" in text and "adresse email" in text and ">Membres</span>" in text
    assert db.session.scalar(db.select(db.func.count(OidcCode.id))) == 0  # rien avant l'accord
    response = http.post("/oidc/consentement", data={"query": query, "decision": "allow"})
    assert code_of(follow(http, response))
    assert code_of(http.get(Flow(client_app).url).location)  # la fois suivante, sans rien demander


def test_refusing_tells_the_client_and_records_nothing(http, members):
    client_app = make_client(allowed=[members], first_party=False)
    _, query = consent_page(http, Flow(client_app))
    location = follow(http, http.post("/oidc/consentement", data={"query": query, "decision": "deny"}))
    assert error_of(location) == "access_denied"
    assert parse_qs(urlsplit(location).query)["state"] == ["etat-1"]
    assert db.session.scalar(db.select(db.func.count(OidcConsent.client_id))) == 0


def test_more_scopes_ask_again(http, members):
    client_app = make_client(allowed=[members], first_party=False)
    _, query = consent_page(http, Flow(client_app, scope="openid email"))
    http.post("/oidc/consentement", data={"query": query, "decision": "allow"})
    assert code_of(http.get(Flow(client_app, scope="openid email").url).location)
    assert http.get(Flow(client_app, scope="openid email profile").url).status_code == 200


def test_first_party_does_not_ask_except_with_prompt_consent(http, client_app):
    assert code_of(http.get(Flow(client_app).url).location)
    flow = Flow(client_app, prompt="consent")
    _, query = consent_page(http, flow)
    assert code_of(follow(http, http.post("/oidc/consentement", data={"query": query, "decision": "allow"})))


def test_silent_without_consent(http, members):
    client_app = make_client(allowed=[members], first_party=False)
    assert error_of(http.get(Flow(client_app, prompt="none").url).location) == "consent_required"


def test_consent_cannot_be_forged_for_a_bad_address(http, client_app):
    query = urlencode({"client_id": client_app.client_id, "redirect_uri": "https://pirate.test/", "scope": "openid"})
    response = http.post("/oidc/consentement", data={"query": query, "decision": "deny"})
    assert response.status_code == 400 and "pirate" not in response.get_data(as_text=True)


def test_consent_refused_outside_the_groups(http):
    client_app = make_client(allowed=[], first_party=False)
    query = urlencode({"client_id": client_app.client_id, "redirect_uri": CB, "scope": "openid"})
    assert http.post("/oidc/consentement", data={"query": query, "decision": "allow"}).status_code == 403
    assert db.session.scalar(db.select(db.func.count(OidcConsent.client_id))) == 0


def test_withdraw_consent_from_my_account(http, members):
    client_app = make_client(allowed=[members], first_party=False, name="Tierce")
    flow = Flow(client_app)
    _, query = consent_page(http, flow)
    code = code_of(follow(http, http.post("/oidc/consentement", data={"query": query, "decision": "allow"})))
    token = exchange(http, flow, code).get_json()["access_token"]
    assert "Tierce" in http.get("/mon-compte").get_data(as_text=True)
    http.post(f"/mon-compte/applications/{client_app.id}/retirer")
    assert http.get("/oidc/userinfo", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert http.get(Flow(client_app).url).status_code == 200  # redemandé


def test_withdrawing_someone_elses_consent_does_nothing(http, members, app):
    client_app = make_client(allowed=[members], first_party=False, name="Tierce")
    other = make_account(email="autre@asso.test")
    db.session.add(OidcConsent(account_id=other.id, client_id=client_app.id, scope="email"))
    db.session.commit()
    http.post(f"/mon-compte/applications/{client_app.id}/retirer")
    assert db.session.get(OidcConsent, (other.id, client_app.id)) is not None


def test_consent_page_does_not_leak_to_framing_or_cache(http, members):
    response = http.get(Flow(make_client(allowed=[members], first_party=False)).url)
    assert response.headers["X-Frame-Options"] == "DENY" and response.headers["Cache-Control"] == "no-store"


# ---------------------------------------------------------------------------
# userinfo
# ---------------------------------------------------------------------------

def test_userinfo_refusals(http, client_app):
    assert http.get("/oidc/userinfo").status_code == 401
    bad = http.get("/oidc/userinfo", headers={"Authorization": "Bearer faux"})
    assert bad.status_code == 401 and 'error="invalid_token"' in bad.headers["WWW-Authenticate"]
    flow = Flow(client_app)
    token = exchange(http, flow, signed_in_code(http, flow)).get_json()["access_token"]
    assert http.get(f"/oidc/userinfo?access_token={token}").status_code == 401  # jamais dans l'URL
    db.session.execute(db.update(OidcToken).values(expires_at=oidc.utcnow() - timedelta(seconds=1)))
    db.session.commit()
    assert http.get("/oidc/userinfo", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_userinfo_by_post(http, client_app, person):
    flow = Flow(client_app)
    token = exchange(http, flow, signed_in_code(http, flow)).get_json()["access_token"]
    assert http.post("/oidc/userinfo", headers={"Authorization": f"Bearer {token}"}).get_json()["sub"] == person.sub
    assert http.post("/oidc/userinfo", data={"access_token": token}).get_json()["sub"] == person.sub


def test_userinfo_session_cookie_is_not_enough(http):
    assert http.get("/oidc/userinfo").status_code == 401


# ---------------------------------------------------------------------------
# Clés
# ---------------------------------------------------------------------------

def test_jwks_has_no_private_part(client):
    keys = client.get("/oidc/jwks").get_json()["keys"]
    assert len(keys) == 1 and keys[0]["kty"] == "RSA" and keys[0]["alg"] == "RS256" and keys[0]["use"] == "sig"
    assert not {"d", "p", "q", "dp", "dq", "qi"} & set(keys[0])
    assert db.session.get(OidcKey, keys[0]["kid"]).private_key.startswith("gAAAA")  # chiffrée (Fernet)


def test_id_token_header(http, client_app):
    flow = Flow(client_app)
    header = base64_header(exchange(http, flow, signed_in_code(http, flow)).get_json()["id_token"])
    assert header["alg"] == "RS256" and header["kid"]


def test_rotation_old_token_still_verifies_new_key_signs(http, client_app):
    flow = Flow(client_app)
    old_token = exchange(http, flow, signed_in_code(http, flow)).get_json()["id_token"]
    old_kid = base64_header(old_token)["kid"]
    oidc.rotate_key()
    db.session.commit()
    assert db.session.get(OidcKey, old_kid).private_key is None
    assert decode(old_token)["sub"]
    flow = Flow(client_app)
    new_token = exchange(http, flow, signed_in_code(http, flow)).get_json()["id_token"]
    assert base64_header(new_token)["kid"] != old_kid
    assert {key["kid"] for key in http.get("/oidc/jwks").get_json()["keys"]} == {old_kid, base64_header(new_token)["kid"]}


def test_automatic_rotation_and_purge(app):
    first = oidc.active_key()[0]
    db.session.commit()
    tasks.run()
    assert oidc.active_key()[0] == first
    later = datetime.now() + oidc.KEY_ROTATION + timedelta(days=1)
    oidc.maintain(later)
    db.session.commit()
    assert db.session.get(OidcKey, first).retired_at is not None
    oidc.maintain(later + oidc.RETIRED_KEY_KEPT + timedelta(days=1))
    db.session.commit()
    assert db.session.get(OidcKey, first) is None


def test_two_active_keys_use_the_newest(app):
    first = oidc.active_key()[0]
    db.session.get(OidcKey, first).created_at = datetime.now() - timedelta(days=1)
    second = oidc._create_key().kid
    assert oidc.active_key()[0] == second


def test_changed_secret_key_replaces_the_key_instead_of_breaking(app):
    first = oidc.active_key()[0]
    db.session.commit()
    app.config["SECRET_KEY"] = "une-autre-cle-secrete"
    second = oidc.active_key()[0]
    assert second != first and db.session.get(OidcKey, first).retired_at is not None


def test_purge_keeps_recent_used_codes(http, client_app):
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    exchange(http, flow, code)
    row_id = oidc.digest(code)
    oidc.maintain(datetime.now() + timedelta(minutes=5))
    db.session.commit()
    assert db.session.get(OidcCode, row_id) is not None
    assert db.session.scalar(db.select(db.func.count(OidcToken.id))) == 1
    oidc.maintain(datetime.now() + oidc.USED_CODE_KEPT + timedelta(hours=2))
    db.session.commit()
    assert db.session.get(OidcCode, row_id) is None
    assert db.session.scalar(db.select(db.func.count(OidcToken.id))) == 0


# ---------------------------------------------------------------------------
# Écran Applications
# ---------------------------------------------------------------------------

@pytest.fixture
def manager(app):
    http = app.test_client()
    login(http, make_account(Role.ADMIN, email="admin@asso.test"))
    return http


def form_for(**fields):
    return {"name": "Inventaire", "redirect_uris": CB, **fields}


def test_create_shows_the_secret_once(manager, app):
    group = AccessGroup(key="g", name="G")
    db.session.add(group)
    db.session.commit()
    response = manager.post("/applications/nouvelle", data=form_for(allowed_groups=[group.id]))
    assert response.headers["Cache-Control"] == "no-store"
    text = response.get_data(as_text=True)
    secret = re.search(r'id="client-secret"[^>]*>([^<]+)</textarea>', text).group(1)
    created = db.session.scalar(db.select(OidcClient))
    assert created.secret_hash == oidc.digest(secret) and secret not in str(created.__dict__)
    assert created.allowed_groups == [group] and created.first_party is False
    assert secret not in manager.get(f"/applications/{created.id}").get_data(as_text=True)
    assert secret not in manager.get("/applications").get_data(as_text=True)
    assert secret not in str([a.message for a in db.session.scalars(db.select(Activity))])


@pytest.mark.parametrize("uri", ["http://appli.test/cb", "javascript:alert(1)", "ftp://a.test/", "https://a.test/#x",
                                 "https://a.test/*", "https://u:p@a.test/", "appli://retour", "//a.test/cb",
                                 "https://" + "a" * 2000 + ".test/"])
def test_bad_redirect_uris_are_refused(manager, uri):
    response = manager.post("/applications/nouvelle", data=form_for(redirect_uris=uri))
    assert response.status_code == 200 and db.session.scalar(db.select(OidcClient)) is None


@pytest.mark.parametrize("uri", ["https://a.test/cb", "https://a.test:8443/x?y=1", "http://localhost:8002/cb",
                                 "http://127.0.0.1/cb"])
def test_good_redirect_uris_are_accepted(manager, uri):
    manager.post("/applications/nouvelle", data=form_for(redirect_uris=uri))
    assert db.session.scalar(db.select(OidcClient)).redirect_uris == [uri]


def test_duplicate_name_is_refused(manager):
    manager.post("/applications/nouvelle", data=form_for())
    manager.post("/applications/nouvelle", data=form_for(name="INVENTAIRE"))
    assert db.session.scalar(db.select(db.func.count(OidcClient.id))) == 1


def test_edit_keeps_identifier_and_secret(manager, members):
    client_app = make_client()
    before = client_app.client_id, client_app.secret_hash
    assert "appli.test" in manager.get(f"/applications/{client_app.id}/modifier").get_data(as_text=True)
    db.session.add(members)
    db.session.commit()
    manager.post(f"/applications/{client_app.id}/modifier",
                 data=form_for(name="Renommée", first_party="y", allowed_groups=[members.id], shared_groups=[members.id]))
    client_app = db.session.get(OidcClient, client_app.id)
    assert (client_app.client_id, client_app.secret_hash) == before and client_app.name == "Renommée"
    assert client_app.first_party and client_app.allowed_groups == [members] and client_app.shared_groups == [members]
    assert db.session.scalar(db.select(Activity).filter_by(action="client.update")).changes


def test_edit_form_shows_one_address_per_line_and_saves_unchanged(manager):
    """Ouvrir le formulaire puis enregistrer sans rien toucher doit marcher."""
    second = "https://appli.test/autre"
    client_app = make_client(uris=(CB, second))
    page = manager.get(f"/applications/{client_app.id}/modifier").get_data(as_text=True)
    shown = html.unescape(re.search(r"<textarea[^>]*>(.*?)</textarea>", page, re.S).group(1)).strip()
    assert shown.split() == [CB, second] and "[" not in shown
    response = manager.post(f"/applications/{client_app.id}/modifier", data={"name": client_app.name, "redirect_uris": shown})
    assert response.status_code == 302
    assert db.session.get(OidcClient, client_app.id).redirect_uris == [CB, second]


def test_new_secret_kills_the_old(manager, http, client_app, app):
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    response = manager.post(f"/applications/{client_app.id}/secret")
    secret = re.search(r'id="client-secret"[^>]*>([^<]+)</textarea>', response.get_data(as_text=True)).group(1)
    assert exchange(http, flow, code).status_code == 401
    assert exchange(http, flow, code, client_secret=secret).status_code == 200


def test_toggle_and_delete(manager, http, client_app):
    pk = client_app.id
    flow = Flow(client_app)
    exchange(http, flow, signed_in_code(http, flow))
    manager.post(f"/applications/{pk}/activer")
    assert db.session.get(OidcClient, pk).active is False
    assert db.session.scalar(db.select(db.func.count(OidcToken.id))) == 0
    manager.post(f"/applications/{pk}/activer")
    assert db.session.get(OidcClient, pk).active is True
    flow = Flow(client_app)
    exchange(http, flow, signed_in_code(http, flow))
    response = manager.post(f"/applications/{pk}/supprimer", follow_redirects=True)
    assert "Fermez d'abord" in html.unescape(response.get_data(as_text=True)) and db.session.get(OidcClient, pk) is not None
    assert "/supprimer" not in manager.get(f"/applications/{pk}").get_data(as_text=True)
    manager.post(f"/applications/{pk}/activer")
    assert "/supprimer" in manager.get(f"/applications/{pk}").get_data(as_text=True)
    manager.post(f"/applications/{pk}/supprimer")
    assert db.session.get(OidcClient, pk) is None
    assert db.session.scalar(db.select(db.func.count(OidcToken.id))) == 0
    assert db.session.scalar(db.select(db.func.count(OidcCode.id))) == 0


def test_rotate_button(manager):
    first = oidc.active_key()[0]
    db.session.commit()
    manager.post("/applications/cles/renouveler")
    assert oidc.active_key()[0] != first
    assert "En service" in manager.get("/applications").get_data(as_text=True)


@pytest.mark.parametrize("field", ["allowed", "shared"])
def test_group_in_use_cannot_be_deleted(manager, members, field):
    db.session.add(members)
    make_client(**{field: [members]})
    response = manager.post(f"/groupes/{members.id}/supprimer", follow_redirects=True)
    assert db.session.get(AccessGroup, members.id) is not None and "utilisé par Appli" in response.get_data(as_text=True)


def test_client_manage_requires_totp(app, client):
    db.session.add(Role(key="regie", name="Régie", permissions=["client.manage"]))
    login(client, make_account("regie", email="r@asso.test"))
    assert client.get("/applications").location.endswith("/mon-compte/double-authentification")


def test_tasks_summary_and_journal_family(manager):
    assert "OIDC" in tasks.run()
    assert "Applications connectées" in manager.get("/journal").get_data(as_text=True)


def test_authorize_is_logged_without_any_secret(http, client_app):
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    exchange(http, flow, code)
    entries = db.session.scalars(db.select(Activity)).all()
    assert any(entry.action == "oidc.authorize" and "Appli" in entry.message for entry in entries)
    assert not any(code in entry.message or SECRET_OF[client_app.client_id] in entry.message for entry in entries)


def test_authorize_log_is_purged_like_logins(http, client_app):
    from app.services.activity import purge_expired_logins
    signed_in_code(http, Flow(client_app))
    assert purge_expired_logins(datetime.now() + timedelta(days=400)) >= 1
    assert db.session.scalar(db.select(Activity).filter_by(action="oidc.authorize")) is None


# ---------------------------------------------------------------------------
# Bugs trouvés en revue : chacun a échoué avant sa correction
# ---------------------------------------------------------------------------

def test_prompt_login_does_not_close_the_session_on_a_mere_link(http, client_app):
    """Un simple lien ne doit déconnecter personne : la session ne change qu'après la nouvelle connexion."""
    for extra in ({"prompt": "login"}, {"max_age": "0"}):
        response = http.get(Flow(client_app, **extra).url)
        if urlsplit(response.location).path == "/connexion":
            assert "Connexion" in http.get(response.location).get_data(as_text=True)  # le formulaire, même connecté
        assert http.get("/mon-compte").status_code == 200


def test_reauthentication_replaces_the_old_session(http, client_app, person):
    from app.models import LoginSession
    before = db.session.scalar(db.select(LoginSession.id))
    response = http.post(http.get(Flow(client_app, prompt="login").url).location,
                         data={"email": person.email, "password": "motdepasse"})
    assert code_of(follow(http, response))
    assert db.session.scalars(db.select(LoginSession.id)).all() != [before]
    assert db.session.scalar(db.select(db.func.count(LoginSession.id))) == 1


def test_reauthentication_with_totp(app, client, members):
    from .conftest import totp_code
    admin = make_account(Role.ADMIN, email="admin@asso.test")
    admin.groups = [members]
    db.session.commit()
    login(client, admin)
    admin.totp_last_counter = None
    db.session.commit()
    flow = Flow(make_client(allowed=[members]), prompt="login")
    response = client.post(client.get(flow.url).location, data={"email": admin.email, "password": "motdepasse"})
    assert response.location.endswith("/connexion/verification")
    response = client.post("/connexion/verification", data={"code": totp_code()})
    assert code_of(follow(client, response))


def test_hint_of_someone_else_is_refused_after_login(client, client_app, person, members):
    http_a = client
    login(http_a, person)
    flow = Flow(client_app)
    hint = exchange(http_a, flow, signed_in_code(http_a, flow)).get_json()["id_token"]
    http_a.post("/deconnexion")
    other = make_account(email="autre@asso.test")
    other.groups = [members]
    db.session.commit()
    response = http_a.get(Flow(client_app, id_token_hint=hint).url)
    response = http_a.post(response.location, data={"email": other.email, "password": "motdepasse"})
    assert error_of(follow(http_a, response)) == "login_required"


def test_hint_of_someone_else_when_connected_is_refused_without_logout(http, client_app, members):
    other_http = http.application.test_client()
    other = make_account(email="autre@asso.test")
    other.groups = [members]
    db.session.commit()
    login(other_http, other)
    flow = Flow(client_app)
    hint = exchange(other_http, flow, signed_in_code(other_http, flow)).get_json()["id_token"]
    assert error_of(http.get(Flow(client_app, id_token_hint=hint).url).location) == "login_required"
    assert http.get("/mon-compte").status_code == 200


def test_withdrawn_consent_cancels_pending_codes(http, members):
    client_app = make_client(allowed=[members], first_party=False)
    flow = Flow(client_app)
    _, query = consent_page(http, flow)
    code = code_of(follow(http, http.post("/oidc/consentement", data={"query": query, "decision": "allow"})))
    http.post(f"/mon-compte/applications/{client_app.id}/retirer")
    assert exchange(http, flow, code).get_json()["error"] == "invalid_grant"


@pytest.mark.parametrize("params", [{"redirect_uri": "https://pirate.test/é"}, {"redirect_uri": 'https://pirate.test/"'},
                                    {"redirect_uri": "https://pirate.test/\\"}, {"response_type": "é"}])
def test_odd_characters_get_the_refusal_page_not_a_crash(http, client_app, params):
    response = http.get(Flow(client_app, **params).url)
    assert response.status_code == 400 and "Location" not in response.headers


@pytest.mark.parametrize("data", [{"grant_type": "é"}, {"grant_type": '"'}])
def test_odd_grant_type_is_a_clean_error(client, data):
    response = client.post("/oidc/token", data=data)
    assert response.status_code == 400 and response.get_json()["error"]


@pytest.mark.parametrize("kid", [{"x": 1}, ["a", "b"], 5, None, True])
def test_hint_with_a_strange_kid_is_not_a_crash(http, client_app, kid):
    import json
    part = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    hint = f'{part({"alg": "RS256", "kid": kid})}.{part({"sub": "x"})}.{part("x")}'
    assert error_of(http.get(Flow(client_app, prompt="none", id_token_hint=hint).url).location) == "login_required"


def test_non_utf8_basic_credentials_are_refused_cleanly(client):
    raw = base64.b64encode(b"\xff:\xfe").decode()
    response = client.post("/oidc/token", headers={"Authorization": f"Basic {raw}"},
                           data={"grant_type": "authorization_code"})
    assert response.status_code == 401 and response.get_json()["error"] == "invalid_client"


@pytest.mark.parametrize("base", ["http://compte.asso.test", "http://192.168.1.10:8001"])
def test_plain_http_is_explained_not_reported_as_unknown_app(http, client_app, base):
    page = http.get(Flow(client_app).url, base_url=base)
    assert page.status_code == 400 and "https" in page.get_data(as_text=True)
    response = http.post("/oidc/token", base_url=base, data={"grant_type": "authorization_code"})
    assert response.status_code == 400 and response.get_json()["error"] == "invalid_request"


def test_issuer_https_without_proxy_warns():
    from app.config import startup_warnings
    assert startup_warnings({"OIDC_ISSUER": "https://compte.asso.test", "BASE_URL": "http://x", "TRUST_PROXY": False})


def test_blank_issuer_falls_back_to_base_url(monkeypatch):
    import importlib

    import app.config as config
    monkeypatch.setenv("OIDC_ISSUER", "")
    try:
        assert importlib.reload(config).Config.OIDC_ISSUER == config.Config.BASE_URL != ""
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_authorize_takes_the_write_lock_from_the_start(app, client_app):
    """Sous SQLite, passer de la lecture à l'écriture échoue si quelqu'un d'autre a écrit entre-temps."""
    from app.extensions import _is_write_request
    for path, expected in (("/oidc/authorize", True), ("/oidc/jwks", True), ("/.well-known/openid-configuration", False)):
        with app.test_request_context(path):
            assert _is_write_request() is expected


def test_pending_flow_does_not_pass_to_the_next_person_after_remote_revocation(app, http, client_app, person, members):
    person.must_change_password = True
    db.session.commit()
    http.get(Flow(client_app).url)
    from app.services import sessions
    sessions.revoke(person)
    person.must_change_password = False
    db.session.commit()
    other = make_account(email="autre@asso.test")
    login(http, other)
    response = http.get("/")
    assert urlsplit(response.location or "").path != "/oidc/reprendre"


def test_saving_my_account_is_not_lost_while_a_flow_is_pending(http, client_app, person):
    """Seul un affichage (GET) reprend la demande en attente : un formulaire envoyé doit aboutir."""
    oidc_resume = {"query": "client_id=x", "at": int(__import__("time").time())}
    with http.session_transaction() as cookie:
        cookie["oidc_resume"] = oidc_resume
    http.post("/mon-compte", data={"first_name": "Nouveau", "last_name": "Nom"})
    assert db.session.get(type(person), person.id).first_name == "Nouveau"


def test_session_lost_on_the_consent_page_comes_back_to_the_flow(http, members, person):
    client_app = make_client(allowed=[members], first_party=False)
    _, query = consent_page(http, Flow(client_app))
    http.post("/deconnexion")
    response = http.post("/oidc/consentement", data={"query": query, "decision": "allow"})
    assert urlsplit(response.location).path == "/connexion"
    response = http.post(response.location, data={"email": person.email, "password": "motdepasse"})
    assert "Autoriser" in follow(http, response).get_data(as_text=True)


def test_replay_entries_are_purged_like_logins(http, client_app):
    from app.services.activity import purge_expired_logins
    flow = Flow(client_app)
    code = signed_in_code(http, flow)
    exchange(http, flow, code)
    exchange(http, flow, code)
    purge_expired_logins(datetime.now() + timedelta(days=400))
    assert db.session.scalar(db.select(Activity).filter_by(action="oidc.replay")) is None


def test_rename_is_recorded_in_the_journal(manager):
    client_app = make_client()
    manager.post(f"/applications/{client_app.id}/modifier", data=form_for(name="Autre nom"))
    changes = db.session.scalar(db.select(Activity).filter_by(action="client.update")).changes
    assert changes["nom"] == ["Appli", "Autre nom"]


def test_lifetimes_do_not_depend_on_the_local_clock(http, client_app):
    """En UTC : au changement d'heure, un code ne doit ni vivre une heure de plus, ni expirer aussitôt."""
    flow = Flow(client_app)
    row = db.session.get(OidcCode, oidc.digest(signed_in_code(http, flow)))
    assert timedelta(seconds=55) < row.expires_at - oidc.utcnow() <= timedelta(seconds=60)


def test_token_lifetime_matches_what_is_announced(http, client_app):
    flow = Flow(client_app)
    token = exchange(http, flow, signed_in_code(http, flow)).get_json()
    row = db.session.get(OidcToken, oidc.digest(token["access_token"]))
    assert abs((row.expires_at - oidc.utcnow()).total_seconds() - token["expires_in"]) < 5


@pytest.mark.parametrize("prompt", ["none ", " none"])
def test_prompt_none_with_spaces_stays_silent(client, client_app, prompt):
    assert error_of(client.get(Flow(client_app, prompt=prompt).url).location) == "login_required"


def test_repeated_prompt_is_refused(http, client_app):
    assert error_of(http.get(Flow(client_app).url + "&prompt=none&prompt=login").location) == "invalid_request"


def test_joserfc_is_pinned():
    from pathlib import Path
    assert "joserfc==" in (Path(__file__).parent.parent / "requirements.txt").read_text()
