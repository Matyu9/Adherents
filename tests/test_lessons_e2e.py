"""Séances dans un vrai navigateur : calendrier, génération, séances d'un cours, déplacement, planning, agenda.

SCREENSHOT_DIR=… garde une capture de chaque page, en clair et en sombre, pour vérifier la mise en page.
"""

from datetime import date, time, timedelta

import pytest

pytest.importorskip("playwright.sync_api")

from app.extensions import db  # noqa: E402
from app.models import Course, Discipline, Lesson, Membership, Person, Room, Season, Site  # noqa: E402

from .test_annuaire_e2e import shot  # noqa: E402
from .test_e2e import _app, browser, server, session  # noqa: E402,F401

pytestmark = pytest.mark.e2e


def _course():
    """Une saison qui couvre les semaines à venir, un cours le jour de la semaine prochaine, deux salles."""
    with _app["app"].app_context():
        season = db.session.scalar(db.select(Season).filter_by(label="2050-2051"))
        if season is None:
            today = date.today()
            season = Season(label="2050-2051", starts_on=today - timedelta(days=10), ends_on=today + timedelta(days=60))
            site = Site(name="Maison e2e")
            rooms = [Room(site=site, name="Studio"), Room(site=site, name="Auditorium")]
            course = Course(
                season=season,
                discipline=Discipline(name="Danse e2e"),
                weekday=(today + timedelta(days=8)).weekday(),
                starts_at=time(17),
                ends_at=time(18),
                room=rooms[0],
            )
            db.session.add_all([season, site, *rooms, course])
            db.session.commit()
        course = db.session.scalar(db.select(Course).join(Discipline).filter(Discipline.name == "Danse e2e"))
        return season.id, course.id


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_calendar_lessons_and_planning(session, scheme):
    season_id, course_id = _course()
    browser_page = session(color_scheme=scheme)
    page = browser_page.login()

    page.goto(f"/seances/calendrier?saison={season_id}")
    shot(page, f"seances-calendrier-{scheme}")
    if page.locator("text=Générer les séances").count():
        page.click("text=Générer les séances")
        page.wait_for_selector("text=séance(s) créée(s)")
    assert "Les séances sont à jour." in page.content()

    page.goto(f"/seances/cours/{course_id}")
    shot(page, f"seances-cours-{scheme}")
    row = page.locator("tbody tr").filter(has_not=page.locator(".badge")).last
    row.locator(".row-menu summary").click()
    row.locator("text=Modifier la séance").click()
    page.wait_for_url("**/modifier")
    page.click("text=Voir les salles libres à ce créneau")
    page.select_option("#room_id", label="Auditorium (Maison e2e) — libre")
    page.fill("#reason", "Spectacle")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url(f"**/seances/cours/{course_id}")
    assert "Séance déplacée" in page.content() and "modifiée" in page.content()
    page.click("nav.tabs >> text=Calendrier")
    page.wait_for_url("**vue=calendrier*")
    assert page.locator(".pl-pill").count() >= 1
    shot(page, f"seances-cours-calendrier-{scheme}")

    page.goto(f"/seances/planning?mode=type&saison={season_id}")
    assert "Danse e2e" in page.content()
    shot(page, f"seances-planning-type-{scheme}")
    page.goto("/seances/planning")
    assert page.locator(".pl-event").count() >= 1 and page.locator(".pl-dayhead.is-today").count() == 1
    shot(page, f"seances-planning-semaine-{scheme}")
    page.goto("/seances/planning?mode=mois")
    assert page.locator(".pl-pill").count() >= 4
    shot(page, f"seances-planning-mois-{scheme}")
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("/seances/planning")
    # Sur un téléphone, la grille défile dans son cadre : la page elle-même ne défile pas de côté.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot(page, f"seances-planning-telephone-{scheme}")
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto("/seances/agenda")
    page.click("text=Créer mon adresse d'agenda") if page.locator(
        "text=Créer mon adresse d'agenda"
    ).count() else page.click("text=Créer une nouvelle adresse")
    assert ".ics" in page.locator("input[readonly]").input_value()
    assert not browser_page.problems, browser_page.problems


def test_roll_call_on_a_phone(session):
    """L'appel sur un écran de téléphone : un bouton par statut et par élève, sans JavaScript."""
    _, course_id = _course()
    with _app["app"].app_context():
        from app.services import courses, lessons

        course = db.session.get(Course, course_id)
        lessons.generate(course.season, apply=True)
        if not course.enrollments:
            for first in ("Nina", "Omar"):
                membership = Membership(person=Person(first_name=first, last_name="Appel"), season=course.season)
                db.session.add(membership)
                db.session.flush()
                courses.enroll(membership, course, course.season.starts_on)
        lesson = db.session.scalar(
            db.select(Lesson).where(Lesson.course_id == course_id, Lesson.day <= date.today()).order_by(Lesson.day)
        )
        db.session.commit()
        lesson_id = lesson.id
    browser_page = session(color_scheme="light", viewport={"width": 390, "height": 844})
    page = browser_page.login()
    page.goto(f"/seances/{lesson_id}/appel")
    shot(page, "seances-appel-telephone")
    page.locator("fieldset", has_text="APPEL Omar").locator("text=Absent").click()
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_selector("text=Appel enregistré")
    page.goto(f"/seances/{lesson_id}/appel")
    assert page.locator("fieldset", has_text="APPEL Omar").locator("input[value=absent]").is_checked()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")  # pas de défilement horizontal
    assert not browser_page.problems, browser_page.problems
