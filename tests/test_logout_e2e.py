"""Déconnexion dans un vrai navigateur, avec une appli cliente qui vérifie les avis comme le fera l'Inventaire.

L'appli cliente garde ses sessions côté serveur, indexées par `sid`. Son point d'accès Back-Channel Logout vérifie la
signature avec le JWKS du serveur, puis `iss`, `aud`, `iat`, `events`, l'absence de `nonce` et le `typ`. Tous les
serveurs tournent sur 127.0.0.1 uniquement, et les avis partent en tâche de fond comme en production.
"""
import secrets
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import pytest

playwright = pytest.importorskip("playwright.sync_api")
requests = pytest.importorskip("requests")
from authlib.integrations.flask_client import OAuth  # noqa: E402
from flask import Flask, jsonify, redirect, request, session  # noqa: E402
from joserfc import jwt  # noqa: E402
from joserfc.jwk import KeySet  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import AccessGroup, Account, OidcClient, OidcLogout, Role  # noqa: E402
from app.services import oidc, tasks  # noqa: E402

from .conftest import config_for, stamp_head  # noqa: E402

pytestmark = pytest.mark.e2e
PASSWORD = "motdepasse-e2e"
EVENT = "http://schemas.openid.net/event/backchannel-logout"


def free_port():
    probe = make_server("127.0.0.1", 0, Flask("x"))
    port = probe.server_port
    probe.server_close()
    return port


def serve(app, port):
    httpd = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class RelyingParty:
    """Appli cliente minimale, sessions côté serveur, déconnexion par avis et par end_session_endpoint."""

    def __init__(self, issuer, client_id, secret, port):
        self.base = f"http://127.0.0.1:{port}"
        self.sessions = {}  # identifiant de session locale -> {"sub", "sid", "id_token"}
        self.received = []  # avis acceptés
        self.rejected = []  # avis refusés, avec le motif
        self.down = False
        web = Flask(f"rp-{port}")
        web.secret_key = secrets.token_hex(16)
        web.config.update(SESSION_COOKIE_NAME=f"rp_{port}")
        oauth = OAuth(web)
        discovery = f"{issuer}/.well-known/openid-configuration"
        remote = oauth.register("idp", server_metadata_url=discovery, client_id=client_id, client_secret=secret,
                                client_kwargs={"scope": "openid profile email groups", "code_challenge_method": "S256"})
        rp = self

        @web.route("/entrer")
        def enter():
            return remote.authorize_redirect(f"{rp.base}/retour")

        @web.route("/retour")
        def back():
            token = remote.authorize_access_token()
            local = secrets.token_urlsafe(16)
            rp.sessions[local] = {"sub": token["userinfo"]["sub"], "sid": token["userinfo"].get("sid"),
                                  "id_token": token["id_token"]}
            session["local"] = local
            return redirect("/moi")

        @web.route("/moi")
        def me():
            current = rp.sessions.get(session.get("local"))
            return jsonify(connected=current is not None, sub=current and current["sub"])

        @web.route("/sortir")
        def leave():
            current = rp.sessions.pop(session.pop("local", None), None)
            params = {"post_logout_redirect_uri": f"{rp.base}/au-revoir", "state": "etat-sortie",
                      "client_id": client_id}
            if current and request.args.get("sans_jeton") is None:
                params["id_token_hint"] = current["id_token"]
            return redirect(remote.load_server_metadata()["end_session_endpoint"] + "?" + urlencode(params))

        @web.route("/au-revoir")
        def bye():
            return jsonify(bye=True, state=request.args.get("state"))

        @web.route("/backchannel", methods=["POST"])
        def backchannel():
            if rp.down:
                return "panne", 503
            try:
                raw = request.form["logout_token"]
                keys = KeySet.import_key_set(requests.get(remote.load_server_metadata()["jwks_uri"], timeout=5).json())
                token = jwt.decode(raw, keys, algorithms=["RS256"])
                claims = token.claims
                assert token.header.get("typ") == "logout+jwt"
                assert claims["iss"] == issuer and claims["aud"] == client_id
                assert abs(claims["iat"] - time.time()) < 60 and claims["exp"] > time.time()
                assert claims["events"] == {EVENT: {}} and "nonce" not in claims and claims.get("jti")
                assert claims.get("sub") or claims.get("sid")
            except Exception as exc:  # noqa: BLE001
                rp.rejected.append(repr(exc))
                return "invalide", 400
            rp.received.append(claims)
            for local, info in list(rp.sessions.items()):
                if info["sid"] == claims.get("sid") or ("sid" not in claims and info["sub"] == claims["sub"]):
                    del rp.sessions[local]
            return "", 200

        self.httpd = serve(web, port)

    def wait_for(self, count, timeout=10):
        deadline = time.time() + timeout
        while len(self.received) < count and time.time() < deadline:
            time.sleep(0.05)
        return len(self.received) >= count


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("logout-e2e")
    port = free_port()
    issuer = f"http://127.0.0.1:{port}"
    idp = create_app(config_for(tmp, SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp / 'e2e.db'}", WTF_CSRF_ENABLED=True,
                                TESTING=False, PROPAGATE_EXCEPTIONS=False, SECRET_KEY="cle-de-test-e2e-" * 4,
                                OIDC_ISSUER=issuer, BASE_URL=issuer, BACKCHANNEL_IN_BACKGROUND=True))
    httpd = serve(idp, port)
    rps = {}
    with idp.app_context():
        db.create_all()
        stamp_head()
        group = AccessGroup(key="membres", name="Membres")
        person = Account(first_name="Élise", last_name="Test", email="elise@e2e.test", role=Role.ADHERENT, active=True,
                         email_verified=True, groups=[group])
        person.set_password(PASSWORD)
        db.session.add(person)
        for name in ("Inventaire", "Autre appli"):
            rp_port = free_port()
            client = OidcClient(client_id=oidc.new_client_id(), name=name, first_party=True,
                                redirect_uris=[f"http://127.0.0.1:{rp_port}/retour"],
                                post_logout_redirect_uris=[f"http://127.0.0.1:{rp_port}/au-revoir"],
                                backchannel_logout_uri=f"http://127.0.0.1:{rp_port}/backchannel",
                                allowed_groups=[group], shared_groups=[group])
            secret = oidc.set_secret(client)
            db.session.add(client)
            db.session.commit()
            rps[name] = RelyingParty(issuer, client.client_id, secret, rp_port)
        sub = person.sub
    yield {"idp": idp, "issuer": issuer, "sub": sub, "inventaire": rps["Inventaire"], "autre": rps["Autre appli"]}
    httpd.shutdown()
    for rp in rps.values():
        rp.httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright.Error as exc:
            pytest.skip(f"Chromium indisponible : {exc}")
        yield browser
        browser.close()


@pytest.fixture
def page(browser, world):
    for rp in (world["inventaire"], world["autre"]):
        rp.received.clear()
        rp.rejected.clear()
        rp.down = False
    context = browser.new_context(locale="fr-FR")
    page = context.new_page()
    page.blocked = []
    page.on("console", lambda m: page.blocked.append(m.text) if "Content Security Policy" in m.text else None)
    yield page
    context.close()


def json_of(page):
    import json
    page.wait_for_load_state("load")
    return json.loads(page.inner_text("body"))


def enter(page, rp, sign_in=True):
    page.goto(rp.base + "/entrer")
    if sign_in:
        page.fill("input[name=email]", "elise@e2e.test")
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
    page.wait_for_url(rp.base + "/moi")
    assert json_of(page)["connected"] is True


def log_out_on_the_server(page, world):
    page.goto(world["issuer"] + "/")
    page.locator("details.user-menu summary").click()
    page.locator("button", has_text="Déconnexion").click()


def connected(page, rp):
    page.goto(rp.base + "/moi")
    return json_of(page)["connected"]


def test_logout_on_the_server_closes_both_apps(page, world):
    inventaire, autre = world["inventaire"], world["autre"]
    enter(page, inventaire)
    enter(page, autre, sign_in=False)  # même session : aucun écran
    log_out_on_the_server(page, world)
    page.wait_for_url("**/connexion")
    assert inventaire.wait_for(1) and autre.wait_for(1), (inventaire.rejected, autre.rejected)
    assert inventaire.rejected == [] and autre.rejected == []
    assert inventaire.received[0]["sub"] == world["sub"]
    assert inventaire.received[0]["sid"] == autre.received[0]["sid"]
    assert not connected(page, inventaire) and not connected(page, autre)
    assert page.blocked == [], page.blocked


def test_app_asks_to_log_out_with_its_id_token(page, world):
    inventaire, autre = world["inventaire"], world["autre"]
    enter(page, inventaire)
    enter(page, autre, sign_in=False)
    page.goto(inventaire.base + "/sortir")
    page.wait_for_url(inventaire.base + "/au-revoir*")
    assert json_of(page) == {"bye": True, "state": "etat-sortie"}
    assert autre.wait_for(1), autre.rejected
    assert not connected(page, autre)
    page.goto(world["issuer"] + "/")
    assert "/connexion" in page.url  # plus de session sur le serveur
    assert page.blocked == [], page.blocked


def test_confirmation_page_works_under_the_csp(page, world):
    """Sans jeton, la personne confirme : le formulaire puis le retour vers l'appli ne doivent rien bloquer."""
    inventaire = world["inventaire"]
    enter(page, inventaire)
    page.goto(inventaire.base + "/sortir?sans_jeton=1")
    page.wait_for_selector("button[value=logout]")
    assert "Inventaire" in page.inner_text("article")
    page.click("button[value=logout]")
    page.wait_for_url(inventaire.base + "/au-revoir*")
    assert json_of(page)["state"] == "etat-sortie"
    assert page.blocked == [], page.blocked
    page.goto(world["issuer"] + "/")
    assert "/connexion" in page.url


def test_staying_logged_in_keeps_the_server_session(page, world):
    inventaire = world["inventaire"]
    enter(page, inventaire)
    page.goto(inventaire.base + "/sortir?sans_jeton=1")
    page.wait_for_selector("button[value=stay]")
    page.click("button[value=stay]")
    page.wait_for_url(inventaire.base + "/au-revoir*")
    page.goto(world["issuer"] + "/")
    assert "/connexion" not in page.url
    assert inventaire.received == []


def test_app_down_then_back_receives_the_notice(page, world):
    inventaire = world["inventaire"]
    enter(page, inventaire)
    inventaire.down = True
    log_out_on_the_server(page, world)
    page.wait_for_url("**/connexion")
    idp = world["idp"]
    with idp.app_context():
        for _ in range(100):  # l'envoi en tâche de fond a échoué : l'avis attend son nouvel essai
            pending = db.session.scalars(db.select(OidcLogout)).all()
            if pending and pending[0].attempts == 1:
                break
            db.session.rollback()
            time.sleep(0.05)
        assert len(pending) == 1 and pending[0].attempts == 1 and pending[0].last_error == "réponse 503"
        db.session.rollback()
    assert connected(page, inventaire)  # toujours ouverte dans l'appli, faute d'avis
    inventaire.down = False
    with idp.app_context():
        assert "1 reçu(s)" in tasks.run(datetime.now() + timedelta(minutes=2))
        assert db.session.scalars(db.select(OidcLogout)).all() == []
    assert inventaire.received and not connected(page, inventaire)


def test_cut_account_closes_the_app_session(page, world):
    inventaire = world["inventaire"]
    enter(page, inventaire)
    idp = world["idp"]
    with idp.app_context():
        from app.services import sessions
        account = db.session.scalar(db.select(Account).filter_by(email="elise@e2e.test"))
        with idp.test_request_context():
            sessions.revoke(account)  # ce que fait « Couper » : ses sessions sont fermées
            db.session.commit()
        from app.services import logout
        logout.deliver_pending()
    assert inventaire.wait_for(1) and not connected(page, inventaire)
