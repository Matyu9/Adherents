"""Gestion des cours dans un vrai navigateur : CSP bloquante, CSRF actif, confirmation avant un abandon.

SCREENSHOT_DIR=… garde une capture de chaque page, en clair et en sombre, pour vérifier la mise en page.
"""

from datetime import date

import pytest

pytest.importorskip("playwright.sync_api")

from app.extensions import db  # noqa: E402
from app.models import Course, Discipline, Enrollment, Household, Membership, Person, Season  # noqa: E402

from .test_annuaire_e2e import shot  # noqa: E402
from .test_e2e import _app, browser, server, session  # noqa: E402,F401

pytestmark = pytest.mark.e2e


def _school():
    """Saison 2040-2041 : deux cours de chant, deux enfants d'un même foyer, l'un inscrit au premier cours."""
    with _app["app"].app_context():
        from app.services import courses, directory

        season = db.session.scalar(db.select(Season).filter_by(label="2040-2041"))
        if season is None:
            season = Season(label="2040-2041", starts_on=date(2040, 9, 1), ends_on=date(2041, 8, 31))
            chant = Discipline(name="Chant e2e")
            household = Household(name="Famille Rivière")
            mother = Person(first_name="Inès", last_name="Rivière", email="ines@e2e.test")
            kids = [Person(first_name=n, last_name="Rivière") for n in ("Jules", "Lina")]
            db.session.add_all([season, chant, household, mother, *kids])
            db.session.flush()
            directory.add_member(household, mother, "guardian")
            for kid in kids:
                directory.add_member(household, kid, "dependent")
                db.session.add(Membership(person=kid, season=season))
            db.session.add_all(
                [
                    Course(season=season, discipline=chant, group_label="petits", capacity=1),
                    Course(season=season, discipline=chant, group_label="grands"),
                ]
            )
            db.session.flush()
            jules = db.session.scalar(db.select(Membership).join(Person).filter(Person.first_name == "Jules"))
            small = db.session.scalar(db.select(Course).filter_by(group_label="petits"))
            courses.enroll(jules, small, date(2040, 9, 10))
            db.session.commit()
        small, big = (db.session.scalar(db.select(Course).filter_by(group_label=g)) for g in ("petits", "grands"))
        return small.id, big.id


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_course_page_transfer_and_withdraw(session, scheme):
    small, big = _school()
    browser_page = session(color_scheme=scheme)
    page = browser_page.login()

    page.goto(f"/catalogue/cours/{small}")
    content = page.content()
    assert "RIVIÈRE Jules" in content and "Inès Rivière" in content and "complet" in content
    shot(page, f"cours-eleves-{scheme}")

    # Inscrire Lina, malgré le cours complet : l'avertissement s'affiche.
    page.click("text=+ Inscrire un élève")
    page.select_option("#membership_id", label="RIVIÈRE Lina")
    page.fill("#on", "2040-10-01")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url(f"**/catalogue/cours/{small}")
    assert "était complet" in page.content() and "RIVIÈRE Lina" in page.content()

    # Jules passe chez les grands.
    page.locator("tr", has_text="RIVIÈRE Jules").locator("text=Changer de cours").click()
    page.select_option("#course_id", label="Chant e2e · grands (2040-2041)")
    page.fill("#on", "2040-11-05")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url(f"**/catalogue/cours/{big}")
    assert "venu(e) de Chant e2e · petits" in page.content()
    shot(page, f"cours-transfert-{scheme}")

    # Abandon de Lina : la confirmation refusée ne change rien, acceptée note l'abandon.
    page.goto(f"/catalogue/cours/{small}")
    row = page.locator("tr", has_text="RIVIÈRE Lina")
    row.locator("summary", has_text="Abandon").click()
    row.locator("input[name=on]").fill("2040-12-01")
    page.once("dialog", lambda d: d.dismiss())
    row.locator("text=Noter l'abandon").click()
    assert "RIVIÈRE Lina" in page.locator("table").inner_text()
    page.once("dialog", lambda d: d.accept())
    row.locator("text=Noter l'abandon").click()
    page.wait_for_selector("text=Abandon de Lina Rivière noté")
    assert "Partis en cours de saison (2)" in page.content()

    assert not browser_page.problems, browser_page.problems
    with _app["app"].app_context():
        lina = db.session.scalar(
            db.select(Enrollment).join(Membership).join(Person).filter(Person.first_name == "Lina")
        )
        assert lina.withdrawn_on == date(2040, 12, 1)
        # Remise à zéro pour l'autre jeu de couleurs.
        for enrollment in db.session.scalars(
            db.select(Enrollment).join(Course).filter(Course.season_id == lina.course.season_id)
        ):
            db.session.delete(enrollment)
        db.session.flush()
        jules = db.session.scalar(db.select(Membership).join(Person).filter(Person.first_name == "Jules"))
        from app.services import courses

        courses.enroll(jules, db.session.get(Course, small), date(2040, 9, 10))
        db.session.commit()
