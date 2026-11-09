"""Phase 5 : Adhérents et le vrai Inventaire, dans un vrai navigateur.

L'Inventaire tourne dans son propre processus, avec son propre environnement Python (`INVENTAIRE_DIR`, par défaut le
dossier voisin `../Inventaire`), sans lire son `.env`. Les deux serveurs écoutent sur 127.0.0.1 uniquement. Le test
est ignoré si l'Inventaire n'est pas là.

Parcours : export des comptes depuis Adhérents, rattachement dans l'Inventaire par un administrateur (accès de secours,
car OIDC_ONLY=1), connexion par Adhérents, refus d'une adresse partagée, puis les trois façons d'être déconnecté.
"""
import json
import os
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import AccessGroup, Account, OidcClient, Role  # noqa: E402
from app.services import logout, oidc  # noqa: E402

from .conftest import config_for, stamp_head  # noqa: E402

pytestmark = pytest.mark.e2e
INVENTAIRE = Path(os.environ.get("INVENTAIRE_DIR", Path(__file__).resolve().parents[2] / "Inventaire"))
FLASK = INVENTAIRE / ".venv" / "bin" / "flask"
PYTHON = INVENTAIRE / ".venv" / "bin" / "python"
PASSWORD = "motdepasse-e2e"
ADMIN_PASSWORD = "admin-inventaire-e2e"

if not FLASK.exists():
    pytest.skip(f"Inventaire introuvable dans {INVENTAIRE} (INVENTAIRE_DIR)", allow_module_level=True)

SEED = """
from app import create_app
from app.extensions import db
from app.models import Role, User
app = create_app()
with app.app_context():
    admin = User(first_name="Ada", last_name="Admin", email="ada@inventaire.test", role=Role.ADMIN, active=True)
    admin.set_password(%r)
    db.session.add_all([admin,
        User(first_name="Élise", last_name="Test", email="elise@e2e.test", role=Role.EMPRUNTEUR),
        User(first_name="Léo", last_name="Durand", email="famille@e2e.test", role=Role.EMPRUNTEUR)])
    db.session.commit()
""" % ADMIN_PASSWORD


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for_port(port, process, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"L'Inventaire s'est arrêté : {process.stdout.read()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("L'Inventaire ne répond pas")


@pytest.fixture(scope="module")
def world(tmp_path_factory, module_database):
    tmp = tmp_path_factory.mktemp("inventaire-e2e")
    idp_port, inv_port = free_port(), free_port()
    issuer, inventaire = f"http://127.0.0.1:{idp_port}", f"http://127.0.0.1:{inv_port}"
    idp = create_app(config_for(tmp, SQLALCHEMY_DATABASE_URI=module_database, WTF_CSRF_ENABLED=True,
                                TESTING=False, PROPAGATE_EXCEPTIONS=False, SECRET_KEY="cle-de-test-e2e-" * 4,
                                OIDC_ISSUER=issuer, BASE_URL=issuer, BACKCHANNEL_IN_BACKGROUND=True))
    httpd = make_server("127.0.0.1", idp_port, idp, threaded=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    with idp.app_context():
        db.create_all()
        stamp_head()
        group = AccessGroup(key="membres", name="Membres")
        accounts = [Account(first_name="Élise", last_name="Test", email="elise@e2e.test"),
                    Account(first_name="Sophie", last_name="Durand", email="famille@e2e.test")]
        for account in accounts:
            account.role, account.active, account.email_verified, account.groups = Role.ADHERENT, True, True, [group]
            account.set_password(PASSWORD)
        client = OidcClient(client_id=oidc.new_client_id(), name="Inventaire", first_party=True,
                            redirect_uris=[f"{inventaire}/connexion/oidc/retour"],
                            post_logout_redirect_uris=[f"{inventaire}/connexion"],
                            backchannel_logout_uri=f"{inventaire}/connexion/oidc/deconnexion",
                            allowed_groups=[group], shared_groups=[group], directory_access=True)
        secret = oidc.set_secret(client)
        db.session.add_all([*accounts, client])
        db.session.commit()
        client_id, group_id, elise_sub = client.client_id, group.id, accounts[0].sub

    data = tmp / "inventaire"
    data.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith(("FLASK_", "OIDC_", "SMTP_"))}
    env.update(FLASK_APP="app:create_app()", FLASK_SKIP_DOTENV="1", SECRET_KEY="inventaire-e2e-" * 4,
               DATA_DIR=str(data), BASE_URL=inventaire, ASSO_NAME="Harmonie e2e",
               OIDC_DISCOVERY_URL=f"{issuer}/.well-known/openid-configuration", OIDC_CLIENT_ID=client_id,
               OIDC_CLIENT_SECRET=secret, OIDC_PROVIDER_NAME="Adhérents", OIDC_SCOPES="openid email profile groups",
               OIDC_ONLY="1", OIDC_LINK_BY_EMAIL="0", PYTHONDONTWRITEBYTECODE="1",
               OIDC_ACCOUNTS_URL=f"{issuer}/oidc/comptes")
    for command in ([str(FLASK), "db", "upgrade"], [str(PYTHON), "-c", SEED]):
        result = subprocess.run(command, cwd=INVENTAIRE, env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
    server = subprocess.Popen([str(FLASK), "run", "--host", "127.0.0.1", "--port", str(inv_port), "--no-reload",
                               "--no-debugger"], cwd=INVENTAIRE, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True)
    try:
        wait_for_port(inv_port, server)
        yield {"idp": idp, "issuer": issuer, "inventaire": inventaire, "db": data / "inventaire.db",
               "group_id": group_id, "elise_sub": elise_sub, "tmp": tmp}
    finally:
        server.terminate()
        output, _ = server.communicate(timeout=10)
        httpd.shutdown()
        if os.environ.get("E2E_LOGS"):
            print(output)


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


def inventaire_rows(world, sql, *params):
    connection = sqlite3.connect(world["db"])
    try:
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def sign_in_at_adherents(page, email):
    page.wait_for_selector("input[name=email]")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")


def enter_inventaire(page, world, email="elise@e2e.test"):
    page.goto(world["inventaire"] + "/connexion")
    page.click("text=Se connecter avec Adhérents")
    # Page intermédiaire de l'Inventaire, puis Adhérents : son formulaire, ou directement le retour si déjà connecté.
    page.wait_for_url(lambda url: url.startswith(world["issuer"] + "/connexion")
                      or url.startswith(world["inventaire"]) and "/connexion" not in url)
    if page.url.startswith(world["issuer"]):
        sign_in_at_adherents(page, email)
    try:
        page.wait_for_url(lambda url: url.startswith(world["inventaire"]) and "/connexion" not in url, timeout=15000)
    except playwright.Error:
        flashes = page.locator(".flash, [role=alert]").all_inner_texts()
        raise AssertionError(f"Connexion à l'Inventaire refusée sur {page.url} : {flashes}") from None


def inventaire_logged_in(page, world):
    page.goto(world["inventaire"] + "/")
    return "/connexion" not in page.url


def test_1_fetch_then_link_by_an_admin(page, world):
    export = world["tmp"] / "comptes.json"
    result = world["idp"].test_cli_runner().invoke(args=["oidc-export-accounts", "--application", "Inventaire",
                                                        "--output", str(export)])
    assert result.exit_code == 0, result.output
    assert {a["email"] for a in json.loads(export.read_text())["accounts"]} == {"elise@e2e.test", "famille@e2e.test"}

    page.goto(world["inventaire"] + "/connexion")
    assert page.locator("input[name=password]").count() == 0  # OIDC_ONLY : pas de mot de passe
    page.goto(world["inventaire"] + "/connexion?local=1")  # accès de secours
    page.fill("input[name=email]", "ada@inventaire.test")
    page.fill("input[name=password]", ADMIN_PASSWORD)
    page.click("form[method=post] button[type=submit]")
    page.wait_for_url(lambda url: "/connexion" not in url)
    page.goto(world["inventaire"] + "/utilisateurs/rattachement-oidc")
    page.click("text=Récupérer les comptes depuis Adhérents")  # sans fichier : l'inventaire lit /oidc/comptes
    page.wait_for_selector("table")
    elise = page.locator("tr", has_text="elise@e2e.test").locator("input[type=checkbox]")
    sophie = page.locator("tr", has_text="Sophie").locator("input[type=checkbox]")
    assert elise.is_checked() and not sophie.is_checked()  # adresse partagée : laissée au responsable
    page.click("text=Rattacher les comptes cochés")
    page.wait_for_selector("text=1 compte(s) rattaché(s)")
    assert inventaire_rows(world, "SELECT oidc_sub FROM user WHERE email = 'elise@e2e.test'") == [(world["elise_sub"],)]
    assert inventaire_rows(world, "SELECT oidc_sub FROM user WHERE email = 'famille@e2e.test'") == [(None,)]


def test_2_login_through_adherents_finds_the_linked_fiche(page, world):
    enter_inventaire(page, world)
    assert "Élise" in page.content()
    rows = inventaire_rows(world, "SELECT s.oidc_sid, s.oidc_id_token IS NOT NULL FROM login_session s "
                                  "JOIN user u ON u.id = s.user_id WHERE u.email = 'elise@e2e.test'")
    assert rows and rows[-1][0] and rows[-1][1] == 1
    assert inventaire_rows(world, "SELECT count(*) FROM user WHERE email = 'elise@e2e.test'") == [(1,)]


def test_3_shared_address_is_refused_instead_of_taking_the_childs_fiche(page, world):
    page.goto(world["inventaire"] + "/connexion")
    page.click("text=Se connecter avec Adhérents")
    sign_in_at_adherents(page, "famille@e2e.test")
    page.wait_for_url(world["inventaire"] + "/connexion*")
    assert "pas encore rattaché" in page.content()
    assert inventaire_rows(world, "SELECT oidc_sub FROM user WHERE email = 'famille@e2e.test'") == [(None,)]


def test_4_logout_from_inventaire_also_logs_out_of_adherents(page, world):
    enter_inventaire(page, world)
    page.locator("details.user-menu summary").click()
    page.locator("button", has_text="Déconnexion").click()
    page.wait_for_url(world["inventaire"] + "/connexion*")
    assert "Vous êtes déconnecté" in page.content()
    assert page.blocked == [], page.blocked
    page.goto(world["issuer"] + "/")
    assert "/connexion" in page.url  # plus de session dans Adhérents non plus
    assert not inventaire_logged_in(page, world)


def test_5_logout_at_adherents_closes_the_inventaire_session(page, world):
    enter_inventaire(page, world)
    page.goto(world["issuer"] + "/")
    page.locator("details.user-menu summary").click()
    page.locator("button", has_text="Déconnexion").click()
    page.wait_for_url("**/connexion")
    deadline = time.time() + 10
    while inventaire_logged_in(page, world) and time.time() < deadline:
        time.sleep(0.1)
    assert not inventaire_logged_in(page, world)


def test_6_leaving_the_group_closes_the_inventaire_session(page, world):
    enter_inventaire(page, world)
    idp = world["idp"]
    with idp.app_context():
        account = db.session.scalar(db.select(Account).filter_by(email="elise@e2e.test"))
        account.groups = []
        logout.review_access(accounts=[account])
        db.session.commit()
        sent, failed, dropped = logout.deliver_pending()  # une par session d'Élise encore ouverte dans Adhérents
        assert sent >= 1 and (failed, dropped) == (0, 0)
        account.groups = [db.session.get(AccessGroup, world["group_id"])]
        db.session.commit()
    assert not inventaire_logged_in(page, world)


def test_7_inventaire_refuses_a_forged_notice(world):
    import urllib.error
    import urllib.request
    request = urllib.request.Request(world["inventaire"] + "/connexion/oidc/deconnexion",
                                     data=b"logout_token=eyJhbGciOiJub25lIn0.e30.", method="POST")
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 400
