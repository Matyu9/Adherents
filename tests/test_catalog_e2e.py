"""Catalogue dans un vrai navigateur : CSP bloquante, CSRF actif, confirmation avant la copie d'une saison.

SCREENSHOT_DIR=… garde une capture de chaque page, en clair et en sombre, pour vérifier la mise en page.
"""
from datetime import date

import pytest

pytest.importorskip("playwright.sync_api")

from app.extensions import db  # noqa: E402
from app.models import Course, Person, Season, Teacher  # noqa: E402

from .test_annuaire_e2e import shot  # noqa: E402
from .test_e2e import _app, browser, server, session  # noqa: E402,F401

pytestmark = pytest.mark.e2e


def _seasons_and_teacher():
    with _app["app"].app_context():
        from app.services import directory
        if db.session.scalar(db.select(Season).filter_by(label="2030-2031")) is None:
            old = Season(label="2030-2031", starts_on=date(2030, 9, 1), ends_on=date(2031, 8, 31))
            new = Season(label="2031-2032", starts_on=date(2031, 9, 1), ends_on=date(2032, 8, 31))
            db.session.add_all([old, new, Person(first_name="Élodie", last_name="Flûtiste", teacher=Teacher(status="CDI"))])
            db.session.flush()
            directory.set_current_season(old)
            db.session.commit()
        return {s.label: s.id for s in db.session.scalars(db.select(Season))}


def test_a_catalogue_from_start_to_the_next_season(session):
    seasons = _seasons_and_teacher()
    browser_page = session(color_scheme="light")
    page = browser_page.login()

    page.goto("/catalogue/disciplines/nouveau")
    page.fill("#name", "Flûte traversière")
    page.select_option("#category", "Instrument")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url("**/catalogue/referentiels*")
    page.goto("/catalogue/niveaux/nouveau")
    page.fill("#name", "Cycle 1")
    page.click("main form[method=post] button[type=submit]")
    page.goto("/catalogue/sites/nouveau")
    page.fill("#name", "École de musique")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url("**/catalogue/referentiels*")
    page.click("text=+ Salle")
    page.fill("#name", "Salle Debussy")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url("**/catalogue/referentiels*")
    assert "Salle Debussy" in page.content()
    shot(page, "catalogue-referentiels")

    page.goto(f"/catalogue?saison={seasons['2030-2031']}")
    page.click("text=+ Nouveau cours")
    page.select_option("#discipline_id", label="Flûte traversière")
    page.select_option("#level_id", label="Cycle 1")
    page.select_option("#teacher_id", label="FLÛTISTE Élodie")
    page.select_option("#weekday", label="Mercredi")
    page.fill("#starts_at", "17:00")
    page.fill("#ends_at", "17:45")
    page.select_option("#room_id", label="Salle Debussy (École de musique)")
    page.fill("#capacity", "1")
    page.check("#marked_full")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url("**/catalogue?saison=*")
    content = page.content()
    assert "Mercredi 17:00-17:45" in content and "complet" in content
    shot(page, "catalogue-cours")

    page.goto("/catalogue/copier")
    assert "1 cours recopié sur 2031-2032" in page.content()
    shot(page, "catalogue-copie")
    page.once("dialog", lambda d: d.dismiss())  # « Annuler » : rien n'est copié
    page.click("text=Copier sur 2031-2032")
    with _app["app"].app_context():
        assert db.session.scalar(db.select(db.func.count(Course.id))) == 1
    page.once("dialog", lambda d: d.accept())
    page.click("text=Copier sur 2031-2032")
    page.wait_for_url(f"**/catalogue?saison={seasons['2031-2032']}")
    assert "Mercredi 17:00-17:45" in page.content() and "complet" not in page.content()

    page.goto(f"/catalogue/professeurs?saison={seasons['2031-2032']}")
    assert "Flûte traversière" in page.content()
    shot(page, "catalogue-professeurs")
    assert browser_page.problems == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_catalogue_pages_have_no_violation(session, scheme):
    seasons = _seasons_and_teacher()
    browser_page = session(color_scheme=scheme, viewport={"width": 390, "height": 844} if scheme == "dark" else None)
    page = browser_page.login()
    for path in ("/catalogue", "/catalogue/professeurs", "/catalogue/referentiels", "/catalogue/copier",
                 f"/catalogue/cours/nouveau?saison={seasons['2030-2031']}", "/catalogue/salles/nouveau"):
        page.goto(path)
        assert page.evaluate("window.htmx !== undefined"), path
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), path
        shot(page, f"{scheme}{path.split('?')[0].replace('/', '-')}")
    assert browser_page.problems == []
