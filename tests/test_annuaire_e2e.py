"""Annuaire dans un vrai navigateur : CSP bloquante, CSRF actif, formulaires remplis comme une personne le ferait.

SCREENSHOT_DIR=… garde une capture de chaque page, en clair et en sombre, pour vérifier la mise en page.
"""
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from app.extensions import db  # noqa: E402
from app.models import Activity, BankAccount, Person  # noqa: E402

from .test_e2e import _app, browser, server, session  # noqa: E402,F401

pytestmark = pytest.mark.e2e
IBAN = "FR7630006000011234567890189"
SHOTS = os.environ.get("SCREENSHOT_DIR")


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


def test_a_household_from_start_to_iban(session):
    browser_page = session(color_scheme="light")
    page = browser_page.login()

    page.goto("/annuaire/saisons/nouvelle")
    page.fill("#label", "2026-2027")
    page.fill("#starts_on", "2026-09-01")
    page.fill("#ends_on", "2027-08-31")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url("**/annuaire/saisons")
    assert "en cours" in page.content()

    page.goto("/annuaire/personnes/nouvelle")
    page.select_option("#civility", "Mme")
    page.fill("#first_name", "Hélène")
    page.fill("#last_name", "Lefèvre")
    page.fill("#email", "helene@e2e.test")
    page.fill("#birth_date", "1982-03-04")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url("**/annuaire/personnes/*")
    page.click("text=Créer un foyer dont Hélène est responsable")
    page.wait_for_url("**/annuaire/foyers/nouveau*")
    assert page.input_value("#name") == "Famille Lefèvre"
    page.fill("#address_street", "3 place du Marché")
    page.fill("#postal_code", "76000")
    page.fill("#city", "Rouen")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url("**/annuaire/foyers/*")

    page.click("text=+ Nouvelle personne à charge")
    page.fill("#first_name", "Léon")
    page.fill("#birth_date", "2016-06-07")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url("**/annuaire/foyers/*")
    page.fill("#telephones input[name=number]", "06 12 34 56 78")
    page.fill("#telephones input[name=label]", "portable mère")
    page.click("#telephones button[type=submit]")
    page.wait_for_url("**/annuaire/foyers/*")
    content = page.content()
    assert "06 12 34 56 78" in content and "Léon Lefèvre" in content and "Responsable 1" in content

    page.click("text=+ Ajouter des coordonnées bancaires")
    page.fill("#iban", "FR76 3000 6000 0112 3456 7890 189")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url("**/annuaire/foyers/*")
    assert "•••• •••• 0189" in page.content() and IBAN not in page.content()
    shot(page, "foyer")

    page.click("text=Afficher l'IBAN")
    with _app["app"].app_context():
        from app.models import Account, Role
        db.session.scalar(db.select(Account).filter_by(role=Role.ADMIN)).totp_last_counter = None
        db.session.commit()
    from .conftest import TOTP_SECRET, totp_code
    page.fill("#code", totp_code(TOTP_SECRET))
    page.click("main form[method=post] button[type=submit]")
    assert "FR76 3000 6000 0112 3456 7890 189" in page.content()

    with _app["app"].app_context():
        leon = db.session.scalar(db.select(Person).filter_by(first_name="Léon"))
        assert leon.household_links[0].role == "dependent" and leon.email is None
        assert db.session.scalar(db.select(db.func.count(Activity.id)).filter_by(action="bank.view")) == 1
        assert db.session.scalar(db.select(BankAccount)).iban_last4 == "0189"
        person_id = leon.id

    page.goto(f"/annuaire/personnes/{person_id}")
    page.click("text=+ Ajouter une adhésion")
    page.fill("#category", "Scolaire")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url(f"**/annuaire/personnes/{person_id}*")
    shot(page, "personne")
    page.goto("/annuaire?statut=eleves")
    assert "LEFÈVRE Léon" in page.content() and "LEFÈVRE Hélène" not in page.content()
    page.goto("/annuaire?q=lefevre helene")
    assert "LEFÈVRE Hélène" in page.content()
    shot(page, "liste")
    assert browser_page.problems == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_directory_pages_have_no_violation(session, scheme):
    browser_page = session(color_scheme=scheme, viewport={"width": 390, "height": 844} if scheme == "dark" else None)
    page = browser_page.login()
    for path in ("/annuaire", "/annuaire/foyers", "/annuaire/saisons", "/annuaire/rattachement",
                 "/annuaire/personnes/nouvelle", "/annuaire/foyers/nouveau", "/annuaire/saisons/nouvelle"):
        page.goto(path)
        assert page.evaluate("window.htmx !== undefined"), path
        shot(page, f"{scheme}{path.replace('/', '-')}")
    with _app["app"].app_context():
        ids = db.session.scalars(db.select(Person.id)).all()
    for person_id in ids[:2]:
        page.goto(f"/annuaire/personnes/{person_id}")
        shot(page, f"{scheme}-personne-{person_id}")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert browser_page.problems == []
