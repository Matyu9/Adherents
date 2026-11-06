"""Tests dans un vrai navigateur (Chromium via Playwright), CSP bloquante et CSRF actifs comme en production.

C'est la seule preuve que la CSP ne casse rien : pytest ne voit pas ce que le navigateur refuse d'exécuter.
Le serveur tourne dans un thread, sur 127.0.0.1 uniquement. Ignorés si Playwright n'est pas installé.
"""
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Account, Notification, Role  # noqa: E402

from .conftest import config_for, stamp_head  # noqa: E402

pytestmark = pytest.mark.e2e
PASSWORD = "motdepasse-e2e"
PAGES = ["/", "/mon-compte", "/mot-de-passe", "/notifications", "/journal", "/roles", "/roles/nouveau",
         f"/roles/{Role.ADHERENT}/modifier", "/parametres", "/sante", "/nexiste-pas"]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    app = create_app(config_for(tmp, SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp / 'e2e.db'}", WTF_CSRF_ENABLED=True,
                                TESTING=False, PROPAGATE_EXCEPTIONS=False, SECRET_KEY="cle-de-test-e2e-" * 4))
    with app.app_context():
        db.create_all()
        stamp_head()
        accounts = []
        for role, first in ((Role.ADMIN, "Alice"), (Role.ADHERENT, "Émile")):
            account = Account(first_name=first, last_name="Test", email=f"{role}@e2e.test", role=role, active=True)
            account.set_password(PASSWORD)
            accounts.append(account)
        db.session.add_all(accounts)
        db.session.flush()
        db.session.add(Notification(account_id=accounts[0].id, message="Une notification", link="/mon-compte"))
        db.session.commit()
    httpd = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except playwright.Error as exc:
            pytest.skip(f"Chromium indisponible : {exc}")
        yield browser
        browser.close()


class Page:
    """Page qui retient tout ce que le navigateur reproche à l'appli."""

    def __init__(self, browser, base, **options):
        self.context = browser.new_context(base_url=base, **options)
        self.page = self.context.new_page()
        self.problems = []
        self.page.on("console", lambda m: self.problems.append(m.text) if m.type == "error" and not self._expected(m.text) else None)
        self.page.on("pageerror", lambda e: self.problems.append(str(e)))
        self.page.add_init_script("document.addEventListener('securitypolicyviolation', e => "
                                  "console.error('CSP : ' + e.violatedDirective + ' ' + e.blockedURI))")

    @staticmethod
    def _expected(text):
        return "404 (" in text  # la page d'erreur testée exprès

    def login(self, role=Role.ADMIN):
        self.page.goto("/connexion")
        self.page.fill("#email", f"{role}@e2e.test")
        self.page.fill("#password", PASSWORD)
        self.page.click("button[type=submit]")
        self.page.wait_for_url("**/")
        return self.page


@pytest.fixture
def session(browser, server):
    pages = []

    def _new(**options):
        page = Page(browser, server, **options)
        pages.append(page)
        return page

    yield _new
    for page in pages:
        page.context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_no_page_breaks_under_the_blocking_csp(session, scheme):
    browser_page = session(color_scheme=scheme)
    page = browser_page.login()
    for path in PAGES:
        page.goto(path)
        assert page.evaluate("getComputedStyle(document.querySelector('.sidebar')).display") != ""
        assert page.evaluate("window.htmx !== undefined"), f"htmx non chargé sur {path}"
    assert browser_page.problems == []


def test_login_page_has_no_violation(session):
    browser_page = session()
    browser_page.page.goto("/connexion")
    assert browser_page.page.locator(".login-card").is_visible()
    assert browser_page.problems == []


def test_stylesheets_really_apply(session):
    page = session(color_scheme="light").login()
    light = page.evaluate("getComputedStyle(document.body).backgroundColor")
    dark_page = session(color_scheme="dark").login()
    dark = dark_page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert light != dark and light != "rgba(0, 0, 0, 0)"


def test_theme_choice_beats_the_device(session):
    browser_page = session(color_scheme="light")
    page = browser_page.login(Role.ADHERENT)
    light = page.evaluate("getComputedStyle(document.body).backgroundColor")
    page.goto("/mon-compte")
    page.check("input[name=theme][value=dark]")
    page.click("#affichage button[type=submit]")
    page.wait_for_url("**/mon-compte#affichage")
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") != light
    page.check("input[name=theme][value=auto]")
    page.click("#affichage button[type=submit]")
    page.wait_for_load_state()
    assert browser_page.problems == []


def test_confirmation_dialog_replaces_inline_onsubmit(session):
    browser_page = session()
    page = browser_page.login()
    page.goto("/roles/nouveau")
    page.fill("#name", "Rôle à supprimer")
    page.click("main button[type=submit]")
    page.wait_for_url("**/roles")
    row = page.locator("tr", has_text="Rôle à supprimer")

    questions = []
    page.once("dialog", lambda d: (questions.append(d.message), d.dismiss()))
    row.locator("button", has_text="Supprimer").click()
    page.wait_for_timeout(200)
    assert questions == ["Supprimer le rôle « Rôle à supprimer » ?"] and row.count() == 1

    page.once("dialog", lambda d: d.accept())
    row.locator("button", has_text="Supprimer").click()
    page.wait_for_url("**/roles")
    page.wait_for_selector(".flash-success")
    assert page.locator("tr", has_text="Rôle à supprimer").count() == 0
    assert browser_page.problems == []


def test_autosubmit_replaces_inline_onchange(session):
    browser_page = session()
    page = browser_page.login()
    page.goto("/journal")
    with page.expect_navigation():
        page.select_option("select[name=type]", "connexions")
    assert "type=connexions" in page.url and "Connexion de" in page.content()
    assert browser_page.problems == []


def test_mobile_menu_and_layout(session):
    browser_page = session(viewport={"width": 390, "height": 800}, has_touch=True, is_mobile=True)
    page = browser_page.login()
    for path in PAGES:
        page.goto(path)
        width = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        assert width[0] <= width[1], f"défilement horizontal sur {path} : {width}"
    page.goto("/")
    assert page.locator(".tabbar").is_visible()
    assert not page.locator(".scrim").is_visible()
    page.locator(".topbar [data-open-nav]").click()
    assert page.locator("body.nav-open").count() == 1 and page.locator(".scrim").is_visible()
    assert page.locator(".topbar [data-open-nav]").get_attribute("aria-expanded") == "true"
    page.keyboard.press("Escape")
    assert page.locator("body.nav-open").count() == 0
    assert browser_page.problems == []


def test_csrf_and_logout_in_a_real_browser(session):
    browser_page = session()
    page = browser_page.login(Role.ADHERENT)
    page.locator("details.user-menu summary").click()
    page.locator("button", has_text="Déconnexion").click()
    page.wait_for_url("**/connexion")
    page.goto("/mon-compte")
    assert "/connexion" in page.url
    assert browser_page.problems == []


def test_htmx_error_banner(session):
    browser_page = session()
    page = browser_page.login()
    page.evaluate("htmx.ajax('GET', '/nexiste-pas', {target: 'main'}).catch(() => {})")
    page.wait_for_selector(".htmx-error")
    assert "introuvable" in page.locator(".htmx-error").inner_text()
