"""Inscriptions dans un vrai navigateur : CSP bloquante, CSRF actif. Une mère réinscrit sa fille, le secrétariat valide.

SCREENSHOT_DIR=… garde une capture de chaque page, en clair et en sombre, pour vérifier la mise en page.
"""

from datetime import date, datetime, time, timedelta

import pytest

pytest.importorskip("playwright.sync_api")

from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    Account,
    Campaign,
    CampaignConsent,
    Course,
    Discipline,
    Dossier,
    Enrollment,
    Household,
    Membership,
    Person,
    Role,
    Season,
)
from app.services import directory  # noqa: E402

from .test_annuaire_e2e import shot  # noqa: E402
from .test_e2e import _app, browser, server, session  # noqa: E402,F401

pytestmark = pytest.mark.e2e


def _family():
    """Saisons 2040-2041 et 2041-2042, la famille Lefèvre rattachée au compte adhérent, une campagne ouverte."""
    with _app["app"].app_context():
        campaign = db.session.scalar(db.select(Campaign).filter_by(title="Réinscriptions 2041-2042"))
        if campaign is not None:
            return campaign.id
        old = Season(label="2040-2041", starts_on=date(2040, 9, 1), ends_on=date(2041, 8, 31))
        new = Season(label="2041-2042", starts_on=date(2041, 9, 1), ends_on=date(2042, 8, 31))
        piano = Discipline(name="Piano e2e")
        db.session.add_all([old, new, piano])
        last_year = Course(season=old, discipline=piano)
        this_year = Course(season=new, discipline=piano, weekday=1, starts_at=time(17), ends_at=time(18), capacity=4)
        db.session.add_all([last_year, this_year])
        household = Household(
            name="Famille Lefèvre", address_street="3 place du Marché", postal_code="76000", city="Rouen"
        )
        mother = Person(
            first_name="Hélène",
            last_name="Lefèvre",
            email="adherent@e2e.test",
            account=db.session.scalar(db.select(Account).filter_by(role=Role.ADHERENT)),
        )
        child = Person(first_name="Léon", last_name="Lefèvre", birth_date=date(2032, 6, 7))
        db.session.add_all([household, mother, child])
        db.session.flush()
        directory.add_member(household, mother, "guardian", "mère")
        directory.add_member(household, child, "dependent")
        directory.add_phone(mother, "06 12 34 56 78")
        membership = Membership(person=child, season=old)
        db.session.add(membership)
        db.session.flush()
        membership.enrollments.append(Enrollment(course=last_year))
        campaign = Campaign(
            season=new,
            title="Réinscriptions 2041-2042",
            opens_at=datetime.now() - timedelta(days=1),
            closes_at=datetime.now() + timedelta(days=30),
            instructions="Pensez au certificat.",
        )
        campaign.consents = [
            CampaignConsent(label="Règlement intérieur", text="J'ai lu le règlement.", scope="household", required=True)
        ]
        db.session.add(campaign)
        db.session.commit()
        return campaign.id


def test_a_mother_registers_her_son_and_the_office_validates(session):
    campaign_id = _family()
    family = session(color_scheme="light")
    page = family.login(Role.ADHERENT)
    page.click("nav >> text=Inscriptions")
    page.click("text=Commencer le dossier")
    page.wait_for_url("**/inscription/dossier/*")
    content = page.content()
    assert "Léon Lefèvre" in content and "Piano e2e" in content and "Pensez au certificat." in content
    shot(page, "inscription-dossier")

    page.click("text=Modifier >> nth=0")  # le foyer
    page.fill("#g1_phone", "07 00 00 00 01")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url("**/inscription/dossier/*")

    page.click("text=Répondre")
    page.check("input[name^=consent-]")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url("**/inscription/dossier/*")

    page.click("text=Indiquer un IBAN")
    page.fill("#holder_name", "Hélène Lefèvre")
    page.fill("#iban", "FR76 3000 6000 0112 3456 7890 189")
    page.click("main form[method=post] button[type=submit]")
    page.wait_for_url("**/inscription/dossier/*")
    assert "•••• 0189" in page.content()

    send = page.locator("button:has-text('Envoyer le dossier')")
    assert send.is_disabled()  # grisé tant que l'exactitude n'est pas certifiée
    page.check("#certify")
    assert send.is_enabled()
    page.click("text=Envoyer le dossier")
    page.wait_for_url("**/inscription/dossier/*")
    assert "Dossier envoyé" in page.content()
    assert family.problems == []

    office = session(color_scheme="dark")
    page = office.login()
    page.goto(f"/campagnes/{campaign_id}/dossiers?statut=submitted")
    page.click("text=Famille Lefèvre")
    content = page.content()
    assert "07 00 00 00 01" in content  # le changement de téléphone, à valider
    shot(page, "inscription-revue")
    page.once("dialog", lambda d: d.accept())
    page.click("text=Valider et mettre à jour l'annuaire")
    page.wait_for_url("**/campagnes/dossiers/*")
    assert "Dossier validé" in page.content()
    with _app["app"].app_context():
        dossier = db.session.scalar(db.select(Dossier).filter_by(campaign_id=campaign_id))
        assert dossier.status == Dossier.VALIDATED
        leon = db.session.scalar(db.select(Person).filter_by(first_name="Léon"))
        seasons = sorted(e.course.season.label for m in leon.memberships for e in m.enrollments)
        assert seasons == ["2040-2041", "2041-2042"]
    assert office.problems == []
