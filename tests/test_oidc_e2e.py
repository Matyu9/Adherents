"""Le vrai parcours : un navigateur Chromium, le serveur, et une appli cliente qui utilise Authlib comme l'Inventaire.

C'est la preuve que `form-action 'self'` ne bloque pas le retour vers l'appli après un formulaire : Chrome applique
cette règle aux redirections, pas pytest. Les deux serveurs tournent sur 127.0.0.1 uniquement.
"""
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")
pytest.importorskip("requests")
from authlib.integrations.flask_client import OAuth  # noqa: E402
from flask import Flask, jsonify  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import AccessGroup, Account, OidcClient, Role  # noqa: E402
from app.services import oidc  # noqa: E402

from .conftest import config_for, enroll_totp, stamp_head, totp_code  # noqa: E402

pytestmark = pytest.mark.e2e
PASSWORD = "motdepasse-e2e"


def serve(app):
    httpd = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def client_application(discovery, client_id, secret, callback_port_box, scope="openid profile email groups"):
    """Même configuration que l'Inventaire (app/services/oidc.py) : découverte, PKCE S256, lecture du jeton d'identité."""
    web = Flask("appli-cliente")
    web.secret_key = "appli-cliente-e2e"
    web.config.update(SESSION_COOKIE_NAME="client_session")
    oauth = OAuth(web)
    remote = oauth.register("idp", server_metadata_url=discovery, client_id=client_id, client_secret=secret,
                            client_kwargs={"scope": scope, "code_challenge_method": "S256"})

    @web.route("/entrer")
    def enter():
        return remote.authorize_redirect(f"http://127.0.0.1:{callback_port_box['port']}/retour")

    @web.route("/retour")
    def back():
        from flask import request
        if "error" in request.args:
            return jsonify(error=request.args["error"])
        token = remote.authorize_access_token()
        return jsonify(id_token=dict(token["userinfo"]), userinfo=dict(remote.userinfo(token=token)))

    return web


@pytest.fixture(scope="module")
def world(tmp_path_factory, module_database):
    tmp = tmp_path_factory.mktemp("oidc-e2e")
    box = {}
    # Le port de l'émetteur doit être connu avant de créer l'appli : il figure dans la configuration.
    probe = make_server("127.0.0.1", 0, Flask("x"))
    port = probe.server_port
    probe.server_close()
    issuer = f"http://127.0.0.1:{port}"
    idp = create_app(config_for(tmp, SQLALCHEMY_DATABASE_URI=module_database, WTF_CSRF_ENABLED=True,
                                TESTING=False, PROPAGATE_EXCEPTIONS=False, SECRET_KEY="cle-de-test-e2e-" * 4,
                                OIDC_ISSUER=issuer, BASE_URL=issuer))
    httpd = make_server("127.0.0.1", port, idp, threaded=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    with idp.app_context():
        db.create_all()
        stamp_head()
        group = AccessGroup(key="membres", name="Membres")
        person = Account(first_name="Élise", last_name="Test", email="elise@e2e.test", role=Role.ADHERENT, active=True,
                         email_verified=True, groups=[group])
        admin = Account(first_name="Alice", last_name="Test", email="alice@e2e.test", role=Role.ADMIN, active=True,
                        groups=[group])
        outsider = Account(first_name="Olaf", last_name="Test", email="olaf@e2e.test", role=Role.ADHERENT, active=True)
        for account in (person, admin, outsider):
            account.set_password(PASSWORD)
        db.session.add_all([person, admin, outsider])
        db.session.commit()
        enroll_totp(admin)
        box.update(idp=idp, issuer=issuer, sub=person.sub, group_id=group.id)

    apps = {}

    def add_client(name, first_party):
        with idp.app_context():
            group = db.session.get(AccessGroup, box["group_id"])
            probe = make_server("127.0.0.1", 0, Flask("x"))
            client_port = probe.server_port
            probe.server_close()
            client = OidcClient(client_id=oidc.new_client_id(), name=name, first_party=first_party,
                                redirect_uris=[f"http://127.0.0.1:{client_port}/retour"], allowed_groups=[group],
                                shared_groups=[group])
            secret = oidc.set_secret(client)
            db.session.add(client)
            db.session.commit()
            web = client_application(f"{issuer}/.well-known/openid-configuration", client.client_id, secret,
                                     {"port": client_port})
            apps[name] = make_server("127.0.0.1", client_port, web, threaded=True)
            threading.Thread(target=apps[name].serve_forever, daemon=True).start()
            return f"http://127.0.0.1:{client_port}"

    box["first_party"] = add_client("Inventaire", True)
    box["third_party"] = add_client("Appli tierce", False)
    yield box
    httpd.shutdown()
    for server in apps.values():
        server.shutdown()


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
def page(browser):
    context = browser.new_context(locale="fr-FR")
    page = context.new_page()
    page.blocked = []
    page.on("console", lambda m: page.blocked.append(m.text) if "Content Security Policy" in m.text else None)
    yield page
    context.close()


def sign_in(page, email):
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")


def json_of(page):
    import json
    page.wait_for_load_state("load")
    return json.loads(page.inner_text("body"))


def test_login_then_back_to_the_client(page, world):
    page.goto(world["first_party"] + "/entrer")
    assert "/connexion" in page.url
    sign_in(page, "elise@e2e.test")
    page.wait_for_url(world["first_party"] + "/retour*")
    data = json_of(page)
    assert data["id_token"]["sub"] == world["sub"] and data["id_token"]["iss"] == world["issuer"]
    assert data["userinfo"]["email"] == "elise@e2e.test" and data["userinfo"]["groups"] == ["membres"]
    assert page.blocked == [], page.blocked

    page.goto(world["first_party"] + "/entrer")  # déjà connecté : aucun écran
    page.wait_for_url(world["first_party"] + "/retour*")
    assert json_of(page)["id_token"]["sub"] == world["sub"]


def test_totp_login_then_back_to_the_client(page, world):
    page.goto(world["first_party"] + "/entrer")
    sign_in(page, "alice@e2e.test")
    page.wait_for_url("**/connexion/verification")
    page.fill("input[name=code]", totp_code())
    page.click("button[type=submit]")
    page.wait_for_url(world["first_party"] + "/retour*")
    assert json_of(page)["id_token"]["email"] == "alice@e2e.test"
    assert page.blocked == [], page.blocked


def test_consent_allow_then_remembered(page, world):
    page.goto(world["third_party"] + "/entrer")
    sign_in(page, "elise@e2e.test")
    page.wait_for_selector("button[value=allow]")
    assert "Appli tierce" in page.inner_text("h1")
    page.click("button[value=allow]")
    page.wait_for_url(world["third_party"] + "/retour*")
    assert json_of(page)["id_token"]["sub"] == world["sub"]
    assert page.blocked == [], page.blocked
    page.goto(world["third_party"] + "/entrer")
    page.wait_for_url(world["third_party"] + "/retour*")


def test_consent_refused_goes_back_with_an_error(page, world):
    page.goto(world["third_party"] + "/entrer")
    sign_in(page, "olaf@e2e.test")  # hors groupe : page de refus, sur le serveur
    page.wait_for_selector("h1")
    assert "Connexion impossible" in page.inner_text("h1") and page.url.startswith(world["issuer"])
    assert page.blocked == [], page.blocked


def test_deny_button_with_csp(browser, world):
    context = browser.new_context()
    page = context.new_page()
    with world["idp"].app_context():
        client = db.session.scalar(db.select(OidcClient).filter_by(name="Appli tierce"))
        account = db.session.scalar(db.select(Account).filter_by(email="alice@e2e.test"))
        oidc.revoke_consent(account, client.id)
        db.session.commit()
    page.goto(world["third_party"] + "/entrer")
    sign_in(page, "alice@e2e.test")
    page.wait_for_url("**/connexion/verification")
    with world["idp"].app_context():
        db.session.execute(db.update(Account).where(Account.email == "alice@e2e.test").values(totp_last_counter=None))
        db.session.commit()
    page.fill("input[name=code]", totp_code())
    page.click("button[type=submit]")
    page.wait_for_selector("button[value=deny]")
    page.click("button[value=deny]")
    page.wait_for_url(world["third_party"] + "/retour*")
    assert json_of(page) == {"error": "access_denied"}
    context.close()


def test_client_survives_a_key_rotation(page, world):
    """L'appli a gardé l'ancienne liste de clés en mémoire : le `kid` inconnu doit la faire recharger."""
    page.goto(world["first_party"] + "/entrer")
    sign_in(page, "elise@e2e.test")
    page.wait_for_url(world["first_party"] + "/retour*")
    with world["idp"].app_context():
        oidc.rotate_key()
        db.session.commit()
    page.goto(world["first_party"] + "/entrer")
    page.wait_for_url(world["first_party"] + "/retour*")
    assert json_of(page)["id_token"]["sub"] == world["sub"]


def test_nothing_overflows_on_a_phone(browser, world):
    """Une adresse de retour très longue et beaucoup de groupes ne doivent pas élargir la page."""
    long_uri = "https://inventaire.harmonie-municipale-de-saint-remy-les-chevreuse.example.org/auth/oidc/retour"
    with world["idp"].app_context():
        groups = [AccessGroup(key=f"g{i}", name=f"Professeurs d'instruments à vent {i}") for i in range(5)]
        client = OidcClient(client_id=oidc.new_client_id(), name="Appli au nom très long " * 3, redirect_uris=[long_uri],
                            allowed_groups=groups, shared_groups=groups)
        oidc.set_secret(client)
        db.session.add(client)
        db.session.execute(db.update(Account).where(Account.email == "alice@e2e.test").values(totp_last_counter=None))
        db.session.commit()
        pk = client.id
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(world["issuer"] + "/connexion")
    sign_in(page, "alice@e2e.test")
    page.wait_for_url("**/connexion/verification")
    page.fill("input[name=code]", totp_code())
    page.click("button[type=submit]")
    page.wait_for_load_state()
    for path in ("/applications", f"/applications/{pk}", f"/applications/{pk}/modifier"):
        page.goto(world["issuer"] + path)
        widths = page.evaluate("""() => [document.documentElement.scrollWidth, document.documentElement.clientWidth,
            ...[...document.querySelectorAll('.overflow-auto')].map(t => t.scrollWidth - t.clientWidth)]""")
        assert widths[0] <= widths[1] and all(extra <= 1 for extra in widths[2:]), (path, widths)
    context.close()
