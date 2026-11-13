"""Tarifs dans un vrai navigateur : CSP bloquante, champs de l'effet affichés selon son type, touche Entrée dans le
formulaire d'une règle, simulateur.

SCREENSHOT_DIR=… garde une capture de chaque page.
"""

from datetime import date

import pytest

pytest.importorskip("playwright.sync_api")

from app.extensions import db  # noqa: E402
from app.models import FeeItem, FeeRule, Season  # noqa: E402

from .test_annuaire_e2e import shot  # noqa: E402
from .test_e2e import _app, browser, server, session  # noqa: E402,F401

pytestmark = pytest.mark.e2e


def _season():
    with _app["app"].app_context():
        season = db.session.scalar(db.select(Season).filter_by(label="2034-2035"))
        if season is None:
            season = Season(label="2034-2035", starts_on=date(2034, 9, 1), ends_on=date(2035, 8, 31))
            db.session.add(season)
            db.session.commit()
        return season.id


def test_tarifs_de_bout_en_bout(session):
    season_id = _season()
    browser_page = session(color_scheme="light")
    page = browser_page.login()

    page.goto(f"/tarifs/reglages?saison={season_id}")
    page.fill("#regies input[name=name]", "EMHN e2e")
    page.click("#regies button:has-text('Ajouter')")
    page.wait_for_url("**/tarifs/reglages*")
    page.fill("input[name=band-0-label]", "A")
    page.fill("input[name=band-0-max]", "600")
    page.fill("input[name=band-1-label]", "C")
    rows = page.locator("[data-rows] tr").count()
    page.click("text=+ Ajouter une tranche")
    assert page.locator("[data-rows] tr").count() == rows + 1
    assert page.locator(f"input[name=band-{rows}-label]").is_visible()
    page.click("text=Enregistrer les réglages")
    page.wait_for_url("**/tarifs/reglages*")
    shot(page, "tarifs-reglages")

    for label, price in (("Formule e2e", "420"), ("Adhésion e2e", "20")):
        page.goto(f"/tarifs/elements/nouveau?saison={season_id}")
        page.fill("#label", label)
        page.fill("#price", price)
        page.fill("#category", "Formules" if label.startswith("Formule") else "Adhésion")
        page.select_option("#regie", label="EMHN e2e")
        page.click("main form[method=post] button[type=submit]")
        page.wait_for_url("**/tarifs?saison=*")
    assert "420,00 €" in page.content()
    shot(page, "tarifs-elements")

    # Formulaire d'une règle : seuls les champs utiles à l'effet choisi sont visibles.
    page.goto(f"/tarifs/regles/nouvelle?saison={season_id}")
    assert page.is_visible("#effect_value") and page.is_visible("#effect_base") and not page.is_visible("#effect_item")
    page.select_option("#effect_kind", "add")
    assert page.is_visible("#effect_item") and page.is_visible("#effect_per") and not page.is_visible("#effect_value")
    assert not page.is_visible("#target_items")
    page.select_option("#effect_kind", "percent")
    assert page.is_visible("#target_items")

    assert not page.is_visible("select[name=c-0-0-param]")
    page.select_option("select[name=c-0-0-field]", "eleve.nb_elements")
    assert page.is_visible("select[name=c-0-0-param]")
    page.fill("#name", "QF A e2e")
    page.select_option("select[name=c-0-0-field]", "foyer.tranche_qf")
    page.select_option("select[name=c-0-0-op]", "in")
    page.fill("input[name=c-0-0-value]", "A")
    page.click("text=+ OU un autre bloc")  # renvoie le formulaire avec un second bloc, sans rien perdre
    page.wait_for_selector("select[name=c-1-0-field]")
    assert page.input_value("#name") == "QF A e2e" and page.input_value("input[name=c-0-0-value]") == "A"
    page.select_option("select[name=c-1-0-field]", "foyer.etiquettes")
    page.select_option("select[name=c-1-0-op]", "has_any")
    page.fill("input[name=c-1-0-value]", "boursier")
    page.fill("#effect_value", "-30")
    page.select_option("#target_categories", "Formules")
    shot(page, "tarifs-regle")
    page.press("#name", "Enter")  # Entrée enregistre : elle ne retire pas la première condition
    page.wait_for_url("**/tarifs/regles?saison=*")
    with _app["app"].app_context():
        rule = db.session.scalar(db.select(FeeRule).filter_by(name="QF A e2e"))
        assert len(rule.conditions) == 2 and not rule.active
    content = page.content()
    assert "brouillon" in content and "Tranche de quotient familial parmi A" in content
    shot(page, "tarifs-regles")

    page.click("text=Effet de l'activation")
    page.wait_for_url("**/effet")
    page.click("button:has-text('Activer la règle')")
    page.wait_for_url("**/tarifs/regles*")
    with _app["app"].app_context():
        assert db.session.scalar(db.select(FeeRule).filter_by(name="QF A e2e")).active
        formule = db.session.scalar(db.select(FeeItem).filter_by(label="Formule e2e")).id

    page.goto(f"/tarifs/simulateur?saison={season_id}")
    page.select_option("#band", "A")
    page.fill("input[name=s0-name]", "Léo")
    page.fill("input[name=s0-age]", "9")
    page.select_option("select[name=s0-items]", str(formule))
    page.click("main form button:has-text('Calculer')")
    page.wait_for_selector("text=Résultat : 294,00 €")
    shot(page, "tarifs-simulateur")
    assert not browser_page.problems, browser_page.problems
