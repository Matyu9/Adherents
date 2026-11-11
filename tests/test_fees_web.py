"""Écrans des tarifs (phase 9) : grille, règles, réglages, simulateur, cotisations, situation."""
import re

import pytest

from app.extensions import db
from app.models import Activity, Assessment, Course, FeeItem, FeePick, FeeRule, HouseholdSeason, Regie, Role, Season
from app.services import fees

from .conftest import login
from .test_annuaire import season
from .test_fees import bands, cond, enroll, priced, rule, school  # noqa: F401 (fixtures)


@pytest.fixture
def manager(client, accounts):
    login(client, accounts[Role.GESTIONNAIRE])
    return accounts[Role.GESTIONNAIRE]


@pytest.fixture
def admin_client(client, accounts):
    login(client, accounts[Role.ADMIN])
    return client


def page(response):
    assert response.status_code == 200, response.status_code
    return response.get_data(as_text=True)


def flashes(client):
    return page(client.get("/"))


# ---------------------------------------------------------------------------
# Éléments
# ---------------------------------------------------------------------------

def test_grille(client, manager, school):
    html = page(client.get(f"/tarifs?saison={school['season'].id}"))
    assert "Formule 30 min" in html and "420,00 €" in html and "EMHN" in html
    assert "1 cours de la saison ne facturent aucun élément" in html  # le cours « sans tarif »


def test_creer_un_element(client, manager, school):
    s = school["season"]
    response = client.post(f"/tarifs/elements/nouveau?saison={s.id}",
                           data={"label": "Location", "price": "120,50", "category": "Location",
                                 "regie": school["emhn"].id, "code": "Q1"})
    assert response.status_code == 302
    item = db.session.scalar(db.select(FeeItem).filter_by(label="Location"))
    assert (item.price, item.regie, item.code, item.season) == (12050, school["emhn"], "Q1", s)


@pytest.mark.parametrize("price, message", [("", "Indiquez le prix"), ("abc", "illisible"), ("-5", "positif"),
                                            ("1,234", "deux décimales")])
def test_element_refuse(client, manager, school, price, message):
    html = page(client.post(f"/tarifs/elements/nouveau?saison={school['season'].id}",
                            data={"label": "X", "price": price}))
    assert message in html
    assert db.session.scalar(db.select(FeeItem).filter_by(label="X")) is None


def test_modifier_un_element(client, manager, school):
    item = school["chorale"]
    html = page(client.get(f"/tarifs/elements/{item.id}/modifier"))
    assert 'value="90"' in html
    client.post(f"/tarifs/elements/{item.id}/modifier", data={"label": "Chorale enfants", "price": "95"})
    assert (item.label, item.price) == ("Chorale enfants", 9500)


def test_supprimer_un_element_utilise(client, manager, school):
    client.post(f"/tarifs/elements/{school['f30'].id}/supprimer")
    assert "Élément utilisé" in flashes(client)
    assert db.session.get(FeeItem, school["f30"].id) is not None


def test_element_facture_par_les_cours(client, manager, school):
    s = school["season"]
    html = page(client.get(f"/tarifs/cours?saison={s.id}"))
    assert f'name="course-{school["libre"].id}"' in html
    client.post("/tarifs/cours", data={"saison": s.id, f"course-{school['libre'].id}": school["chorale"].id,
                                       f"course-{school['flute30'].id}": ""})
    assert school["libre"].fee_item is school["chorale"] and school["flute30"].fee_item is None


def test_cours_avec_un_element_d_une_autre_saison(client, manager, school):
    other = season("2027-2028")
    foreign = fees.save_item(other, {"label": "Ailleurs", "price": 1})
    db.session.commit()
    client.post("/tarifs/cours", data={"saison": school["season"].id, f"course-{school['flute30'].id}": foreign.id})
    assert school["flute30"].fee_item is school["f30"]


# ---------------------------------------------------------------------------
# Formulaire des règles
# ---------------------------------------------------------------------------

def rule_data(**extra):
    data = {"name": "Dégressivité", "effect_kind": "percent", "effect_value": "-10", "effect_base": "current",
            "action": "save"}
    data.update(extra)
    return data


def test_formulaire_vide(client, manager, school):
    html = page(client.get(f"/tarifs/regles/nouvelle?saison={school['season'].id}"))
    assert 'name="c-0-0-field"' in html and "+ OU un autre bloc" in html
    # La touche Entrée enregistre : le premier bouton du formulaire est « Enregistrer ».
    first = re.search(r'<button type="submit" name="action" value="([^"]+)"', html)
    assert first.group(1) == "save"


def test_ajouter_et_retirer_des_conditions_sans_enregistrer(client, manager, school):
    url = f"/tarifs/regles/nouvelle?saison={school['season'].id}"
    html = page(client.post(url, data=rule_data(**{"c-0-0-field": "eleve.age", "action": "add-condition-0"})))
    assert 'name="c-0-1-field"' in html and 'value="eleve.age" selected' in html
    html = page(client.post(url, data=rule_data(**{"c-0-0-field": "eleve.age", "action": "add-group"})))
    assert 'name="c-1-0-field"' in html and "OU" in html
    html = page(client.post(url, data=rule_data(**{"c-0-0-field": "eleve.age", "c-0-1-field": "eleve.rang",
                                                    "action": "remove-0-0"})))
    assert 'value="eleve.rang" selected' in html and 'value="eleve.age" selected' not in html
    assert db.session.scalar(db.select(db.func.count(FeeRule.id))) == 0


def test_enregistrer_une_regle_complete(client, manager, school):
    s = school["season"]
    data = rule_data(**{
        "name": "Famille", "group": "famille", "notes": "2e enfant",
        "target_categories": ["Formules"], "target_exclude_items": [str(school["f45"].id)],
        "c-0-0-field": "eleve.rang", "c-0-0-op": "ge", "c-0-0-value": "2",
        "c-0-1-field": "eleve.age", "c-0-1-op": "between", "c-0-1-value": "6 et 17",
        "c-1-0-field": "ligne.element", "c-1-0-op": "in", "c-1-0-value": "formule 30 MIN, Chorale",
        "c-1-1-field": "eleve.nb_elements", "c-1-1-op": "ge", "c-1-1-value": "2", "c-1-1-param": "cat:Formules",
        "c-2-0-field": "foyer.resident", "c-2-0-op": "is", "c-2-0-value": "Non",
        "c-2-1-field": "", "activate": "1"})
    response = client.post(f"/tarifs/regles/nouvelle?saison={s.id}", data=data)
    assert response.status_code == 302
    saved = db.session.scalar(db.select(FeeRule))
    assert saved.active and saved.exclusive_group == "famille" and saved.notes == "2e enfant"
    assert saved.effect == {"kind": "percent", "value": "-10", "base": "current"}
    assert saved.target == {"categories": ["Formules"], "exclude_items": [school["f45"].id]}
    assert saved.conditions == [
        [{"field": "eleve.rang", "op": "ge", "value": 2}, {"field": "eleve.age", "op": "between", "value": [6, 17]}],
        [{"field": "ligne.element", "op": "in", "value": sorted([school["f30"].id, school["chorale"].id])},
         {"field": "eleve.nb_elements", "op": "ge", "value": 2, "param": {"category": "Formules"}}],
        [{"field": "foyer.resident", "op": "is", "value": False}]]
    html = page(client.get(f"/tarifs/regles?saison={s.id}"))
    assert "Rang de l&#39;élève dans le foyer ≥ 2 et Âge de l&#39;élève entre 6 et 17" in html
    assert "<strong>ou</strong>" in html and "−10 %" in html


def test_modifier_sans_rien_changer_garde_la_regle(client, manager, school):
    """Le formulaire prérempli renvoie exactement la règle : rien ne se perd à l'aller-retour."""
    s = school["season"]
    original = rule(s, "Complète", {"kind": "replace", "item": school["f45"].id},
                    [[cond("ligne.element", "in", [school["f30"].id]), cond("eleve.age", "between", [3, 10])],
                     [cond("foyer.tranche_qf", "in", ["A", "B"]), cond("eleve.adulte", "is", True),
                      cond("foyer.nb_elements", "ge", 1, {"item": school["chorale"].id})]],
                    {"items": [school["f30"].id], "exclude_categories": ["Adhésion"]}, "g")
    db.session.commit()
    before = (original.conditions, original.target, original.effect, original.exclusive_group)
    html = page(client.get(f"/tarifs/regles/{original.id}/modifier"))
    data = {"action": "save"}
    for name, value in re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html):
        data[name] = value.replace("&#39;", "'")
    for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        chosen = re.findall(r'<option value="([^"]*)" selected', body)
        if chosen:
            data[name] = chosen if len(chosen) > 1 or name.startswith("target_") else chosen[0]
    data.pop("csrf_token", None)
    assert client.post(f"/tarifs/regles/{original.id}/modifier", data=data).status_code == 302
    db.session.refresh(original)
    assert (original.conditions, original.target, original.effect, original.exclusive_group) == before


@pytest.mark.parametrize("extra, message", [
    ({"name": ""}, "nom"),
    ({"effect_value": ""}, "pourcentage"),
    ({"effect_kind": "amount", "effect_value": "beaucoup"}, "illisible"),
    ({"effect_kind": "replace"}, "Choisissez l&#39;élément"),
    ({"c-0-0-field": "eleve.age", "c-0-0-op": "in", "c-0-0-value": "3"}, "comparaison"),
    ({"c-0-0-field": "eleve.age", "c-0-0-op": "between", "c-0-0-value": "3"}, "deux nombres"),
    ({"c-0-0-field": "foyer.resident", "c-0-0-op": "is", "c-0-0-value": "peut-être"}, "oui ou non"),
    ({"c-0-0-field": "ligne.element", "c-0-0-op": "in", "c-0-0-value": "Harpe"}, "élément inconnu"),
    ({"c-0-0-field": "foyer.tranche_qf", "c-0-0-op": "in", "c-0-0-value": ""}, "au moins une valeur"),
    ({"c-0-0-field": "eleve.age", "c-0-0-op": "lt", "c-0-0-value": "18", "effect_kind": "total_amount",
      "effect_value": "-5"}, "un élève"),
    ({"c-0-0-field": "inconnu", "c-0-0-op": "eq", "c-0-0-value": "1"}, "Choisissez un critère"),
])
def test_regle_refusee_avec_message(client, manager, school, extra, message):
    html = page(client.post(f"/tarifs/regles/nouvelle?saison={school['season'].id}", data=rule_data(**extra)))
    assert message in html
    assert db.session.scalar(db.select(db.func.count(FeeRule.id))) == 0
    if "c-0-0-field" in extra and extra["c-0-0-field"] != "inconnu":
        assert f'value="{extra["c-0-0-field"]}" selected' in html  # la saisie est gardée


def test_effet_montant_en_euros(client, manager, school):
    client.post(f"/tarifs/regles/nouvelle?saison={school['season'].id}",
                data=rule_data(effect_kind="amount", effect_value="-12,50"))
    assert db.session.scalar(db.select(FeeRule)).effect == {"kind": "amount", "value": -1250}


def test_position_choisie_a_la_modification(client, manager, school):
    s = school["season"]
    a, b, c = (rule(s, n, {"kind": "free"}) for n in "ABC")
    db.session.commit()
    client.post(f"/tarifs/regles/{c.id}/modifier", data=rule_data(name="C", effect_kind="free", position="1"))
    assert [r.name for r in fees.season_rules(s)] == ["C", "A", "B"]


def test_monter_descendre_activer_supprimer(client, manager, school):
    s = school["season"]
    a, b = rule(s, "A", {"kind": "free"}), rule(s, "B", {"kind": "free"}, active=False)
    db.session.commit()
    client.post(f"/tarifs/regles/{b.id}/deplacer", data={"sens": "haut"})
    assert [r.name for r in fees.season_rules(s)] == ["B", "A"]
    client.post(f"/tarifs/regles/{b.id}/deplacer", data={"sens": "bas"})
    assert [r.name for r in fees.season_rules(s)] == ["A", "B"]
    client.post(f"/tarifs/regles/{b.id}/activer")
    assert b.active
    client.post(f"/tarifs/regles/{a.id}/supprimer")
    assert [(r.name, r.position) for r in fees.season_rules(s)] == [("B", 0)]


def test_brouillon_signale(client, manager, school):
    rule(school["season"], "Essai", {"kind": "free"}, active=False)
    db.session.commit()
    html = page(client.get(f"/tarifs/regles?saison={school['season'].id}"))
    assert "brouillon" in html and "Effet de l&#39;activation" in html


def test_effet_d_une_regle(client, manager, priced):
    draft = rule(priced["season"], "Tout offert", {"kind": "free"}, target={"categories": ["Formules"]}, active=False)
    db.session.commit()
    html = page(client.get(f"/tarifs/regles/{draft.id}/effet"))
    text = " ".join(html.split())
    assert "1 foyer sur 1 changeraient, pour un total de <strong>−918,00 €</strong>" in text  # 378 + 540
    assert "Famille Martin" in html


# ---------------------------------------------------------------------------
# Réglages et régies
# ---------------------------------------------------------------------------

def test_reglages(client, manager, school):
    s = school["season"]
    response = client.post(f"/tarifs/reglages?saison={s.id}", data={
        "ranking": "age_desc", "ranking_categories": ["Formules"], "rounding": "tenth", "rounding_scope": "total",
        "band-0-label": "A", "band-0-max": "600", "band-1-label": "B", "band-1-max": "1200", "band-2-label": "C",
        "default_band": "C", "resident_cities": "Caen\nHérouville\n\n"})
    assert response.status_code == 302
    settings = fees.season_settings(s)
    assert settings.engine == {"ranking": "age_desc", "ranking_categories": ["Formules"], "rounding": "tenth",
                               "rounding_scope": "total"}
    assert settings.qf_bands == [{"label": "A", "max": 600}, {"label": "B", "max": 1200}, {"label": "C", "max": None}]
    assert settings.resident_cities == ["Caen", "Hérouville"]
    html = page(client.get(f"/tarifs/reglages?saison={s.id}"))
    assert 'value="1200"' in html and '<option value="age_desc" selected' in html


@pytest.mark.parametrize("data, message", [
    ({"band-0-label": "A", "band-1-label": "B"}, "plafond"),
    ({"band-0-label": "A", "band-0-max": "mille", "band-1-label": "B"}, "entier positif"),
    ({"ranking": "hasard"}, "Valeur de réglage inconnue"),
])
def test_reglages_refuses(client, manager, school, data, message):
    html = page(client.post(f"/tarifs/reglages?saison={school['season'].id}", data=data))
    assert message in html


def test_regies(client, manager, school):
    client.post("/tarifs/regies", data={"name": "Harmonie", "saison": school["season"].id})
    regie = db.session.scalar(db.select(Regie).filter_by(name="Harmonie"))
    client.post(f"/tarifs/regies/{regie.id}/archiver")
    assert regie.archived
    client.post(f"/tarifs/regies/{regie.id}/archiver")
    assert not regie.archived
    client.post("/tarifs/regies", data={"name": "harmonie"})
    assert "existe déjà" in flashes(client)


def test_reglages_reserves_a_fee_edit(client, accounts, school):
    db.session.add(Role(key="tresorier", name="Trésorier", permissions=["fee.view", "fee.assess", "fee.situation"]))
    from .conftest import make_account
    login(client, make_account("tresorier", email="tresor@asso.test"))
    assert client.get("/tarifs/reglages").status_code == 403
    html = page(client.get(f"/tarifs?saison={school['season'].id}"))
    assert "Nouvel élément" not in html and "Réglages" not in html


# ---------------------------------------------------------------------------
# Simulateur et copie
# ---------------------------------------------------------------------------

def test_simulateur(client, manager, priced):
    s = priced["season"]
    draft = rule(s, "Remise brouillon", {"kind": "total_amount", "value": -1000}, active=False)
    db.session.commit()
    query = {"saison": s.id, "band": "A", "resident": "1", "s0-name": "Léo", "s0-age": "9",
             "s0-items": [priced["f30"].id, priced["chorale"].id]}
    html = page(client.get("/tarifs/simulateur", query_string=query))
    assert "Résultat : 314,00 €" in html  # 420 −30 % + chorale offerte + adhésion 20
    assert "QF A : 420,00 € → 294,00 €" in html and "ajouté par « Adhésion »" in html
    html = page(client.get("/tarifs/simulateur", query_string={**query, "drafts": draft.id}))
    assert "Résultat : 304,00 €" in html


def test_simulateur_vide(client, manager, school):
    html = page(client.get(f"/tarifs/simulateur?saison={school['season'].id}&n=50"))
    assert "Résultat" not in html and 'name="s11-name"' in html and 'name="s12-name"' not in html


def test_copie(client, manager, priced):
    target = season("2027-2028")
    db.session.commit()
    html = page(client.get(f"/tarifs/copier?depuis={priced['season'].id}&vers={target.id}"))
    assert "5 élément(s) et 5 règle(s)" in html
    client.post("/tarifs/copier", data={"depuis": priced["season"].id, "vers": target.id})
    assert len(fees.items(target)) == 5 and len(fees.season_rules(target)) == 5
    html = page(client.get(f"/tarifs/copier?depuis={priced['season'].id}&vers={target.id}"))
    assert "a déjà des tarifs" in html
    client.post("/tarifs/copier", data={"depuis": priced["season"].id, "vers": target.id})
    assert len(fees.items(target)) == 5


# ---------------------------------------------------------------------------
# Cotisations
# ---------------------------------------------------------------------------

def test_cotisation_d_un_foyer(client, manager, priced):
    s, household = priced["season"], priced["home"]
    url = f"/tarifs/foyers/{household.id}?saison={s.id}"
    html = page(client.get(url))
    assert "pas encore calculée" in html and "Calcul : 958,00 €" in html
    assert "réservée au trésorier" in html and "Tranche :" not in html
    client.post(f"/tarifs/foyers/{household.id}/calculer", data={"saison": s.id})
    assessment = fees.get_assessment(household, s)
    assert assessment.total == 95800
    html = page(client.get(url))
    assert "Cotisation enregistrée : 958,00 €" in html and "Famille 2 : 420,00 € → 378,00 €" in html


def test_correction_validation_reouverture(client, manager, priced):
    s, household = priced["season"], priced["home"]
    assessment = fees.assess(household, s)
    db.session.commit()
    line = assessment.lines[0]
    client.post(f"/tarifs/lignes/{line.id}/corriger", data={"amount": "300", "reason": ""})
    assert "motif" in flashes(client) and not line.corrected
    client.post(f"/tarifs/lignes/{line.id}/corriger", data={"amount": "300", "reason": "Arrangement"})
    assert line.amount == 30000 and line.corrected_by is manager
    html = page(client.get(f"/tarifs/foyers/{household.id}?saison={s.id}"))
    assert "corrigé" in html and "Arrangement" in html and "Le recalcul efface les corrections" in html
    client.post(f"/tarifs/lignes/{line.id}/annuler")
    assert not line.corrected
    client.post(f"/tarifs/cotisations/{assessment.id}/valider")
    assert assessment.status == Assessment.VALIDATED
    client.post(f"/tarifs/lignes/{line.id}/corriger", data={"amount": "1", "reason": "x"})
    assert "rouvrez" in flashes(client) and line.amount == line.computed
    client.post(f"/tarifs/cotisations/{assessment.id}/rouvrir", data={"reason": ""})
    assert assessment.status == Assessment.VALIDATED
    client.post(f"/tarifs/cotisations/{assessment.id}/rouvrir", data={"reason": "Erreur"})
    assert assessment.status == Assessment.PROPOSED
    client.post(f"/tarifs/cotisations/{assessment.id}/supprimer")
    assert fees.get_assessment(household, s) is None


def test_recalcul_efface_les_corrections_sur_demande(client, manager, priced):
    s, household = priced["season"], priced["home"]
    assessment = fees.assess(household, s)
    fees.correct_line(assessment.lines[0], 1, "x")
    db.session.commit()
    client.post(f"/tarifs/foyers/{household.id}/calculer", data={"saison": s.id})
    assert "corrections" in flashes(client) and assessment.lines[0].amount == 1
    client.post(f"/tarifs/foyers/{household.id}/calculer", data={"saison": s.id, "effacer": "1"})
    db.session.refresh(assessment)
    assert assessment.total == 95800


def test_ecart_affiche(client, manager, priced):
    s, household = priced["season"], priced["home"]
    fees.validate(fees.assess(household, s))
    rule(s, "Remise", {"kind": "total_amount", "value": -1000})
    db.session.commit()
    html = page(client.get(f"/tarifs/foyers/{household.id}?saison={s.id}"))
    assert "Un nouveau calcul donnerait 948,00 € au lieu de 958,00 €" in html and "Rouvrez" in html


def test_toutes_les_cotisations(client, manager, priced):
    s = priced["season"]
    html = page(client.get(f"/tarifs/cotisations?saison={s.id}"))
    assert "Famille Martin" in html and "à calculer" in html
    client.post("/tarifs/cotisations/calculer", data={"saison": s.id})
    assert "1 cotisation(s) calculée(s)" in flashes(client)
    html = page(client.get(f"/tarifs/cotisations?saison={s.id}&statut=validated"))
    assert "Famille Martin" not in html
    html = page(client.get(f"/tarifs/cotisations?saison={s.id}&statut=proposed"))
    assert "Famille Martin" in html and "958,00 €" in html


def test_elements_pris_a_part(client, manager, priced):
    s, household = priced["season"], priced["home"]
    client.post(f"/tarifs/foyers/{household.id}/elements",
                data={"saison": s.id, "person": priced["alice"].id, "item": priced["orchestre"].id, "note": "Tuba"})
    pick = db.session.scalar(db.select(FeePick))
    assert pick.note == "Tuba" and "Tuba" in page(client.get(f"/tarifs/foyers/{household.id}?saison={s.id}"))
    client.post(f"/tarifs/elements-pris/{pick.id}/retirer")
    assert db.session.scalar(db.select(FeePick)) is None
    other = season("2027-2028")
    foreign = fees.save_item(other, {"label": "Ailleurs", "price": 1})
    db.session.commit()
    client.post(f"/tarifs/foyers/{household.id}/elements",
                data={"saison": s.id, "person": priced["alice"].id, "item": foreign.id})
    assert db.session.scalar(db.select(FeePick)) is None


def test_lien_depuis_la_fiche_du_foyer(client, manager, school):
    html = page(client.get(f"/annuaire/foyers/{school['home'].id}"))
    assert f"/tarifs/foyers/{school['home'].id}" in html


# ---------------------------------------------------------------------------
# Situation du foyer : trésorier et direction
# ---------------------------------------------------------------------------

def test_situation_refusee_au_gestionnaire(client, manager, bands):
    assert client.get(f"/tarifs/foyers/{bands['home'].id}/situation").status_code == 403


def test_situation(admin_client, bands):
    s, household = bands["season"], bands["home"]
    url = f"/tarifs/foyers/{household.id}/situation?saison={s.id}"
    html = page(admin_client.get(url))
    assert "tranche retenue aujourd'hui : <strong>C</strong>" in html and "résident : <strong>oui</strong>" in html
    alice_m = bands["m"]["alice"]
    response = admin_client.post(url, data={"quotient": "450", "qf_band": "", "qf_proof": "Attestation CAF",
                                            "resident": "0", "tags": "boursier, ", f"tags-{alice_m.id}": "personnel"})
    assert response.status_code == 302
    record = db.session.get(HouseholdSeason, (household.id, s.id))
    assert (record.quotient, record.resident, record.tags, record.qf_proof) == (450, False, ["boursier"],
                                                                                 "Attestation CAF")
    assert alice_m.fee_tags == ["personnel"]
    html = page(admin_client.get(f"/tarifs/foyers/{household.id}?saison={s.id}"))
    assert "Tranche : <strong>A</strong> (quotient 450)" in html
    entry = db.session.scalar(db.select(Activity).filter_by(action="fee.situation"))
    assert "450" not in str(entry.changes)


@pytest.mark.parametrize("data, message", [({"quotient": "abc"}, "nombre entier"), ({"qf_band": "Z"}, "Tranche inconnue")])
def test_situation_refusee(admin_client, bands, data, message):
    html = page(admin_client.post(f"/tarifs/foyers/{bands['home'].id}/situation?saison={bands['season'].id}",
                                  data=data))
    assert message in html


def test_saison_introuvable(client, manager, app):
    assert client.get("/tarifs").status_code == 200
    assert client.get("/tarifs/simulateur").status_code == 302
    assert client.get("/tarifs/foyers/1").status_code == 404
