"""Tarifs (phase 9) : éléments, règles, réglages, situation des foyers, calcul, corrections, copie de saison."""
from datetime import date

import pytest

from app.extensions import db
from app.models import (Activity, Assessment, AssessmentLine, Course, Discipline, Enrollment, FeePick, FeeRule,
                        HouseholdMember, HouseholdSeason, Level, Membership)
from app.services import directory, fees, pricing
from app.services.fees import FeeError

from .test_annuaire import home, person, season

G, D = HouseholdMember.GUARDIAN, HouseholdMember.DEPENDENT


def item(s, label, euros, category=None, regie=None):
    return fees.save_item(s, {"label": label, "price": euros * 100, "category": category, "regie": regie})


def rule(s, name, effect, conditions=None, target=None, group=None, active=True):
    record = fees.save_rule(s, {"name": name, "effect": effect, "conditions": conditions or [],
                                "target": target or {}, "group": group})
    if active:
        fees.set_rule_active(record, True)
    return record


def cond(field, op, value, param=None):
    c = {"field": field, "op": op, "value": value}
    if param is not None:
        c["param"] = param
    return c


def student(household, s, first, born=None, role=D, last="Martin"):
    p = person(first, last, birth_date=born)
    directory.add_member(household, p, role)
    membership = Membership(person=p, season=s)
    db.session.add(membership)
    db.session.flush()
    return p, membership


def enroll(membership, course):
    membership.enrollments.append(Enrollment(course=course))
    db.session.flush()


@pytest.fixture
def school(app):
    """Saison 2026-2027 : adhésion, formules 30 et 45 min, chorale ; trois enfants Martin et leur mère."""
    s = season("2026-2027", current=True)
    emhn, ohn = fees.create_regie("EMHN"), fees.create_regie("OHN")
    adh = item(s, "Adhésion", 20, "Adhésion", emhn)
    f30 = item(s, "Formule 30 min", 420, "Formules", emhn)
    f45 = item(s, "Formule 45 min", 540, "Formules", emhn)
    chorale = item(s, "Chorale", 90, "Pratiques collectives", emhn)
    orchestre = item(s, "Orchestre", 60, "Pratiques collectives", ohn)
    flute, piano, choeur = (Discipline(name="Flûte"), Discipline(name="Piano"), Discipline(name="Chœur"))
    c1 = Level(name="Cycle 1")
    db.session.add_all([flute, piano, choeur, c1])
    db.session.flush()
    courses = {
        "flute30": Course(season=s, discipline=flute, level=c1, fee_item=f30),
        "piano45": Course(season=s, discipline=piano, level=c1, fee_item=f45),
        "choeur": Course(season=s, discipline=choeur, fee_item=chorale),
        "libre": Course(season=s, discipline=choeur, group_label="sans tarif"),
    }
    db.session.add_all(courses.values())
    household = home(city="Hérouville-Saint-Clair")
    anne, anne_m = student(household, s, "Anne", date(1980, 1, 2), role=G)
    alice, alice_m = student(household, s, "Alice", date(2012, 3, 1))
    basile, basile_m = student(household, s, "Basile", date(2017, 5, 1))
    db.session.commit()
    return {"season": s, "emhn": emhn, "ohn": ohn, "adh": adh, "f30": f30, "f45": f45, "chorale": chorale,
            "orchestre": orchestre, "home": household, "anne": anne, "alice": alice, "basile": basile,
            "m": {"anne": anne_m, "alice": alice_m, "basile": basile_m}, **courses}


def totals(result):
    return [(line.student_id, line.item.label, line.amount) for line in result.lines]


# ---------------------------------------------------------------------------
# Réglages
# ---------------------------------------------------------------------------

def test_reglages_crees_vides(school):
    settings = fees.season_settings(school["season"])
    assert settings.engine == {} and settings.qf_bands == [] and settings.default_band is None


def test_reglages_enregistres_et_journalises(school):
    s = school["season"]
    fees.save_settings(s, {"rounding": "tenth"}, [{"label": "A", "max": 600}, {"label": "B", "max": 1200},
                                                  {"label": "C"}], "C", [" Caen ", "Caen", "Hérouville"])
    settings = fees.season_settings(s)
    assert settings.engine["rounding"] == "tenth" and settings.default_band == "C"
    assert settings.resident_cities == ["Caen", "Hérouville"]
    assert db.session.scalar(db.select(Activity).filter_by(action="fee.settings")) is not None


@pytest.mark.parametrize("bands, default, message", [
    ([{"label": "A"}, {"label": "B"}], None, "plafond"),
    ([{"label": "A", "max": 600}, {"label": "B", "max": 500}], None, "croissant"),
    ([{"label": "A", "max": 600}, {"label": "A"}], None, "double"),
    ([{"label": "", "max": 600}], None, "nom court"),
    ([{"label": "A", "max": -1}], None, "positif"),
    ([{"label": "A", "max": True}], None, "positif"),
    ([{"label": "A"}], "Z", "par défaut"),
])
def test_tranches_refusees(school, bands, default, message):
    with pytest.raises(FeeError, match=message):
        fees.save_settings(school["season"], {}, bands, default)


def test_reglages_moteur_refuses(school):
    with pytest.raises(FeeError):
        fees.save_settings(school["season"], {"ranking": "hasard"})


# ---------------------------------------------------------------------------
# Éléments
# ---------------------------------------------------------------------------

def test_element_en_double_refuse(school):
    with pytest.raises(FeeError, match="déjà"):
        item(school["season"], "adhésion", 10)


@pytest.mark.parametrize("price", [-1, 1.5, None, True, fees.MAX_PRICE + 1])
def test_prix_refuse(school, price):
    with pytest.raises(FeeError, match="prix"):
        fees.save_item(school["season"], {"label": "X", "price": price})


def test_meme_libelle_autre_saison(school):
    other = season("2027-2028")
    assert item(other, "Adhésion", 25).season_id == other.id


def test_regie_archivee_refusee(school):
    school["ohn"].archived_at = date(2026, 1, 1)
    with pytest.raises(FeeError, match="archivée"):
        item(school["season"], "Atelier", 50, regie=school["ohn"])
    # un élément qui y était déjà rattaché garde sa régie
    fees.save_item(school["season"], {"label": "Orchestre", "price": 7000, "regie": school["ohn"]},
                   school["orchestre"])
    assert school["orchestre"].price == 7000


def test_modification_journalisee(school):
    fees.save_item(school["season"], {"label": "Adhésion", "price": 2500, "regie": school["emhn"],
                                      "category": "Adhésion"}, school["adh"])
    entry = db.session.scalars(db.select(Activity).filter_by(action="fee.item").order_by(Activity.id.desc())).first()
    assert entry.changes == {"price": [2000, 2500]}


def test_regie_en_double(school):
    with pytest.raises(FeeError, match="existe déjà"):
        fees.create_regie("emhn")
    with pytest.raises(FeeError):
        fees.create_regie(" ")


def test_element_utilise_non_supprimable(school):
    s = school["season"]
    with pytest.raises(FeeError, match="cours"):
        fees.delete_item(school["f30"])
    rule(s, "Adhésion", {"kind": "add", "item": school["adh"].id})
    with pytest.raises(FeeError, match="Adhésion"):
        fees.delete_item(school["adh"])
    fees.add_pick(school["home"], school["alice"], school["orchestre"])
    with pytest.raises(FeeError, match="à part"):
        fees.delete_item(school["orchestre"])
    unused = item(s, "Inutile", 1)
    fees.delete_item(unused)
    assert unused not in fees.items(s)


def test_categories(school):
    assert fees.categories(school["season"]) == ["Adhésion", "Formules", "Pratiques collectives"]


# ---------------------------------------------------------------------------
# Règles et ordre
# ---------------------------------------------------------------------------

def test_nouvelle_regle_en_brouillon_et_en_dernier(school):
    s = school["season"]
    a = rule(s, "A", {"kind": "free"}, active=False)
    b = rule(s, "B", {"kind": "free"}, active=False)
    assert (a.position, b.position, a.active) == (0, 1, False)


def test_regle_refusee_par_le_moteur(school):
    with pytest.raises(FeeError, match="Critère"):
        rule(school["season"], "R", {"kind": "free"}, [[{"field": "os.system", "op": "eq", "value": 1}]])


def test_regle_qui_cite_un_element_d_une_autre_saison(school):
    other = season("2027-2028")
    with pytest.raises(FeeError, match="n'existe pas"):
        rule(other, "R", {"kind": "replace", "item": school["f30"].id})


def test_monter_descendre(school):
    s = school["season"]
    a, b, c = (rule(s, n, {"kind": "free"}) for n in "ABC")
    fees.move_rule(c, -1)
    assert [r.name for r in fees.season_rules(s)] == ["A", "C", "B"]
    fees.move_rule(a, -5)  # déjà en tête
    fees.move_rule(a, 10)
    assert [r.name for r in fees.season_rules(s)] == ["C", "B", "A"]
    assert [r.position for r in fees.season_rules(s)] == [0, 1, 2]


def test_reordonner(school):
    s = school["season"]
    a, b, c = (rule(s, n, {"kind": "free"}) for n in "ABC")
    fees.reorder_rules(s, [c.id, a.id, b.id])
    assert [r.name for r in fees.season_rules(s)] == ["C", "A", "B"]
    with pytest.raises(FeeError, match="rechargez"):
        fees.reorder_rules(s, [c.id, a.id])
    with pytest.raises(FeeError):
        fees.reorder_rules(s, [c.id, a.id, a.id])


def test_supprimer_renumerote(school):
    s = school["season"]
    a, b, c = (rule(s, n, {"kind": "free"}) for n in "ABC")
    fees.delete_rule(b)
    assert [(r.name, r.position) for r in fees.season_rules(s)] == [("A", 0), ("C", 1)]


def test_regle_modifiee(school):
    s = school["season"]
    r = rule(s, "A", {"kind": "free"})
    fees.save_rule(s, {"name": "A bis", "effect": {"kind": "percent", "value": "-10"}}, r)
    assert r.name == "A bis" and r.effect == {"kind": "percent", "value": "-10", "base": "current"}
    assert r.active  # modifier ne remet pas en brouillon


def test_regle_d_une_autre_saison(school):
    r = rule(school["season"], "A", {"kind": "free"})
    with pytest.raises(FeeError, match="autre saison"):
        fees.save_rule(season("2027-2028"), {"name": "A", "effect": {"kind": "free"}}, r)


# ---------------------------------------------------------------------------
# Situation du foyer
# ---------------------------------------------------------------------------

@pytest.fixture
def bands(school):
    fees.save_settings(school["season"], {}, [{"label": "A", "max": 600}, {"label": "B", "max": 1200},
                                              {"label": "C"}], "C", ["Hérouville-Saint-Clair"])
    db.session.commit()
    return school


@pytest.mark.parametrize("values, expected", [
    ({}, "C"),
    ({"quotient": 599}, "A"),
    ({"quotient": 600}, "B"),
    ({"quotient": 5000}, "C"),
    ({"qf_band": "A", "quotient": 5000}, "A"),
])
def test_tranche_retenue(bands, values, expected):
    record = fees.save_situation(bands["home"], bands["season"], values)
    assert fees.band_of(record, fees.season_settings(bands["season"])) == expected


def test_residence(bands):
    s, household = bands["season"], bands["home"]
    settings = fees.season_settings(s)
    assert fees.resident_of(fees.situation(household, s), household, settings) is True
    household.city = "HEROUVILLE-SAINT-CLAIR"
    assert fees.resident_of(fees.situation(household, s), household, settings) is True
    household.city = "Rouen"
    assert fees.resident_of(fees.situation(household, s), household, settings) is False
    record = fees.save_situation(household, s, {"resident": True})
    assert fees.resident_of(record, household, settings) is True
    settings.resident_cities = []
    household.city = "Rouen"
    assert fees.resident_of(fees.situation(other := home("Autre"), s), other, settings) is None


def test_situation_refusee(bands):
    for values, message in (({"qf_band": "Z"}, "Tranche"), ({"quotient": -3}, "quotient"),
                            ({"quotient": "800"}, "quotient"), ({"resident": "oui"}, "Résident"),
                            ({"tags": ["x" * 41]}, "40")):
        with pytest.raises(FeeError, match=message):
            fees.save_situation(bands["home"], bands["season"], values)


def test_quotient_masque_dans_le_journal(bands):
    fees.save_situation(bands["home"], bands["season"], {"quotient": 812, "tags": ["boursier", " boursier "]})
    entry = db.session.scalar(db.select(Activity).filter_by(action="fee.situation"))
    assert entry.changes["quotient"] == ["(masqué)", "(masqué)"]
    assert entry.changes["tags"] == [[], ["boursier"]]
    assert "812" not in str(entry.changes) and entry.household_id == bands["home"].id


def test_situation_vide_non_enregistree(school):
    fees.situation(school["home"], school["season"])
    assert db.session.scalar(db.select(db.func.count()).select_from(HouseholdSeason)) == 0


def test_etiquettes_eleve(school):
    fees.set_student_tags(school["m"]["alice"], ["personnel"])
    assert school["m"]["alice"].fee_tags == ["personnel"]


# ---------------------------------------------------------------------------
# Ce que le foyer prend
# ---------------------------------------------------------------------------

def test_preparation(school):
    m = school["m"]
    enroll(m["alice"], school["flute30"])
    enroll(m["alice"], school["choeur"])
    enroll(m["basile"], school["piano45"])
    prepared = fees.prepare(school["home"], school["season"])
    assert prepared.order.picks == ((school["alice"].id, school["f30"].id), (school["alice"].id, school["chorale"].id),
                                    (school["basile"].id, school["f45"].id))
    by_id = {st.id: st for st in prepared.order.students}
    assert by_id[school["alice"].id].age == 14  # âge au début de la saison
    assert by_id[school["alice"].id].disciplines == {"Flûte", "Chœur"}
    assert by_id[school["anne"].id].adult
    assert prepared.warnings == []


def test_adulte_sans_date_de_naissance(school):
    marc, _ = student(school["home"], school["season"], "Marc", role=G)
    enfant, _ = student(school["home"], school["season"], "Sans date")
    by_id = {st.id: st for st in fees.prepare(school["home"], school["season"]).order.students}
    assert by_id[marc.id].adult and not by_id[enfant.id].adult and by_id[enfant.id].age is None


def test_eleve_qui_abandonne_ou_non_inscrit(school):
    m = school["m"]
    m["basile"].status = Membership.WITHDRAWN
    enroll(m["alice"], school["flute30"])
    m["alice"].enrollments[0].status = Enrollment.WITHDRAWN
    prepared = fees.prepare(school["home"], school["season"])
    assert prepared.order.picks == ()
    assert school["basile"].id not in {st.id for st in prepared.order.students}


def test_anciennete(school):
    old = season("2025-2026")
    db.session.add(Membership(person=school["alice"], season=old))
    db.session.add(Membership(person=school["basile"], season=old, status=Membership.WITHDRAWN))
    by_id = {st.id: st for st in fees.prepare(school["home"], school["season"]).order.students}
    assert by_id[school["alice"].id].seniority == 1 and by_id[school["basile"].id].seniority == 0


def test_avertissements(school):
    enroll(school["m"]["alice"], school["libre"])
    other = home("Famille Durand")
    directory.add_member(other, school["basile"], D)
    intrus = person("Intrus")
    directory.add_member(school["home"], intrus, D)
    fees.add_pick(school["home"], intrus, school["orchestre"])
    warnings = fees.prepare(school["home"], school["season"]).warnings
    assert any("ne facture aucun" in w for w in warnings)
    assert any("plusieurs foyers" in w for w in warnings)
    assert any("pas élève" in w for w in warnings)


def test_element_pris_a_part(school):
    pick = fees.add_pick(school["home"], school["alice"], school["orchestre"], "Location")
    assert fees.prepare(school["home"], school["season"]).order.picks == ((school["alice"].id, school["orchestre"].id),)
    fees.remove_pick(pick)
    assert fees.prepare(school["home"], school["season"]).order.picks == ()


def test_element_pris_a_part_hors_du_foyer(school):
    with pytest.raises(FeeError, match="ne fait pas partie"):
        fees.add_pick(home("Autre"), school["alice"], school["orchestre"])


def test_element_d_une_autre_saison_ignore(school):
    other = season("2027-2028")
    fees.add_pick(school["home"], school["alice"], item(other, "Futur", 10))
    assert fees.prepare(school["home"], school["season"]).order.picks == ()


def test_tranche_inconnue_signalee(school):
    fees.save_settings(school["season"], {}, [{"label": "A", "max": 600}, {"label": "B"}])
    assert any("Tranche" in w for w in fees.prepare(school["home"], school["season"]).warnings)


def test_cours_relie_a_un_element(school):
    other = season("2027-2028")
    with pytest.raises(FeeError, match="même saison"):
        fees.set_course_item(school["libre"], item(other, "X", 1))
    fees.set_course_item(school["libre"], school["chorale"])
    assert school["libre"].fee_item is school["chorale"]
    fees.set_course_item(school["libre"], None)
    assert school["libre"].fee_item_id is None


# ---------------------------------------------------------------------------
# Calcul, corrections, validation
# ---------------------------------------------------------------------------

@pytest.fixture
def priced(bands):
    """Grille complète : adhésion par élève, QF A, dégressivité, chorale offerte avec une formule, hors commune."""
    s, b = bands["season"], bands
    rule(s, "Adhésion", {"kind": "add", "item": b["adh"].id, "per": "student"})
    rule(s, "QF A", {"kind": "percent", "value": "-30"}, [[cond("foyer.tranche_qf", "in", ["A"])]],
         {"categories": ["Formules"]}, "social")
    rule(s, "Famille 2", {"kind": "percent", "value": "-10"}, [[cond("eleve.rang", "ge", 2)]],
         {"categories": ["Formules"]}, "famille")
    rule(s, "Collectif offert", {"kind": "free"},
         [[cond("eleve.nb_elements", "ge", 1, {"category": "Formules"})]], {"categories": ["Pratiques collectives"]})
    rule(s, "Hors commune", {"kind": "percent", "value": "20"}, [[cond("foyer.resident", "is", False)]],
         {"exclude_categories": ["Adhésion"]})
    m = b["m"]
    enroll(m["alice"], b["flute30"])
    enroll(m["alice"], b["choeur"])
    enroll(m["basile"], b["piano45"])
    db.session.commit()
    return b


def test_calcul_complet(priced):
    _, result = fees.compute(priced["home"], priced["season"])
    a, b = priced["alice"].id, priced["basile"].id
    assert totals(result) == [(a, "Formule 30 min", 37800), (a, "Chorale", 0), (b, "Formule 45 min", 54000),
                              (a, "Adhésion", 2000), (b, "Adhésion", 2000)]
    assert result.total == 95800


def test_calcul_qf_a_hors_commune(priced):
    priced["home"].city = "Rouen"
    fees.save_situation(priced["home"], priced["season"], {"quotient": 450})
    _, result = fees.compute(priced["home"], priced["season"])
    f30 = result.lines[0]
    assert [s.rule for s in f30.steps] == ["QF A", "Famille 2", "Hors commune"]
    assert f30.amount == round(42000 * 0.7 * 0.9 * 1.2)


def test_regle_en_brouillon_ignoree(priced):
    rule(priced["season"], "Tout offert", {"kind": "free"}, active=False)
    assert fees.compute(priced["home"], priced["season"])[1].total == 95800


def test_cotisation_enregistree(priced):
    a = fees.assess(priced["home"], priced["season"])
    assert a.status == Assessment.PROPOSED and a.total == a.computed_total == 95800
    first = a.lines[0]
    assert (first.label, first.regie_name, first.base, first.person_name) == (
        "Formule 30 min", "EMHN", 42000, "Alice Martin")
    assert first.steps == [{"rule": "Famille 2", "before": 42000, "after": 37800, "note": ""}]


def test_lignes_sur_le_total_et_arrondi(priced):
    s = priced["season"]
    rule(s, "Remise foyer", {"kind": "total_percent", "value": "-3"})
    fees.save_settings(s, {"rounding": "euro", "rounding_scope": "total"}, fees.season_settings(s).qf_bands, "C",
                       ["Hérouville-Saint-Clair"])
    a = fees.assess(priced["home"], s)
    kinds = [(line.kind, line.label, line.amount) for line in a.lines if line.kind != AssessmentLine.ITEM]
    assert kinds == [("adjustment", "Remise foyer", -2874), ("rounding", "Arrondi", -26)]
    assert a.total == 92900


def test_recalcul_d_une_proposee(priced):
    a = fees.assess(priced["home"], priced["season"])
    enroll(priced["m"]["anne"], priced["flute30"])
    again = fees.assess(priced["home"], priced["season"])
    assert again.id == a.id and again.total > 95800
    assert db.session.scalar(db.select(db.func.count()).select_from(AssessmentLine)) == len(again.lines)


def test_correction_tracee(priced, accounts):
    a = fees.assess(priced["home"], priced["season"])
    line = a.lines[0]
    with pytest.raises(FeeError, match="motif"):
        fees.correct_line(line, 30000, " ")
    with pytest.raises(FeeError, match="négatif"):
        fees.correct_line(line, -1, "x")
    with pytest.raises(FeeError, match="illisible"):
        fees.correct_line(line, "300", "x")
    fees.correct_line(line, 30000, "Geste commercial", accounts["gestionnaire"])
    assert (line.amount, line.computed, line.corrected) == (30000, 37800, True)
    assert a.total == 95800 - 7800
    entry = db.session.scalar(db.select(Activity).filter_by(action="fee.correct"))
    assert entry.changes == {"amount": [37800, 30000]} and "Geste commercial" in entry.message
    # le recalcul effacerait la correction : refusé, sauf demande expresse
    with pytest.raises(FeeError, match="corrections"):
        fees.assess(priced["home"], priced["season"])
    fees.assess(priced["home"], priced["season"], discard_corrections=True)
    assert a.total == 95800


def test_ajustement_peut_etre_negatif(priced):
    rule(priced["season"], "Remise", {"kind": "total_amount", "value": -1000})
    a = fees.assess(priced["home"], priced["season"])
    fees.correct_line(a.lines[-1], -2000, "Remise doublée")
    assert a.total == 95800 - 2000


def test_annuler_une_correction(priced):
    a = fees.assess(priced["home"], priced["season"])
    line = a.lines[0]
    fees.correct_line(line, 1, "Essai")
    fees.undo_correction(line)
    assert line.amount == line.computed and not line.corrected and line.correction_reason is None
    db.session.flush()  # la contrainte « correction complète ou absente » tient


def test_validation_fige(priced, accounts):
    a = fees.assess(priced["home"], priced["season"])
    fees.validate(a, accounts["gestionnaire"])
    assert a.status == Assessment.VALIDATED and a.validated_by is accounts["gestionnaire"]
    with pytest.raises(FeeError, match="déjà validée"):
        fees.validate(a)
    with pytest.raises(FeeError, match="rouvrez"):
        fees.assess(priced["home"], priced["season"])
    with pytest.raises(FeeError, match="rouvrez"):
        fees.correct_line(a.lines[0], 1, "x")
    with pytest.raises(FeeError, match="rouvrez"):
        fees.undo_correction(a.lines[0])
    with pytest.raises(FeeError, match="rouvrez"):
        fees.delete_assessment(a)
    with pytest.raises(FeeError, match="pourquoi"):
        fees.reopen(a, "")
    fees.reopen(a, "Erreur de tranche")
    assert a.status == Assessment.PROPOSED and a.validated_at is None
    with pytest.raises(FeeError, match="n'est pas validée"):
        fees.reopen(a, "encore")


def test_validee_garde_ses_libelles(priced):
    a = fees.assess(priced["home"], priced["season"])
    fees.validate(a)
    priced["alice"].first_name = "Alicia"
    fees.save_item(priced["season"], {"label": "Flûte 30", "price": 1, "category": "Formules"}, priced["f30"])
    db.session.flush()
    db.session.refresh(a)
    assert (a.lines[0].label, a.lines[0].person_name, a.total) == ("Formule 30 min", "Alice Martin", 95800)


def test_ecart_signale_sans_modifier(priced):
    a = fees.assess(priced["home"], priced["season"])
    fees.validate(a)
    assert fees.drift(a) is None
    rule(priced["season"], "Remise", {"kind": "total_amount", "value": -1000})
    assert fees.drift(a) == 94800 and a.total == 95800


def test_supprimer_une_proposee(priced):
    a = fees.assess(priced["home"], priced["season"])
    fees.delete_assessment(a)
    assert fees.get_assessment(priced["home"], priced["season"]) is None


def test_calcul_de_la_saison(priced):
    other = home("Famille Durand")
    _, m = student(other, priced["season"], "Chloé", date(2015, 1, 1), last="Durand")
    enroll(m, priced["piano45"])
    vide = home("Sans élève")
    fees.validate(fees.assess(priced["home"], priced["season"]))
    done, skipped, reasons = fees.assess_season(priced["season"])
    assert (done, skipped) == (1, 1) and "validée" in reasons[0]
    assert fees.get_assessment(vide, priced["season"]) is None
    assert {h.name for h in fees.season_households(priced["season"])} == {"Famille Martin", "Famille Durand"}


# ---------------------------------------------------------------------------
# Effet d'une règle, simulation
# ---------------------------------------------------------------------------

def test_effet_d_une_regle_en_brouillon(priced):
    draft = rule(priced["season"], "Basile offert", {"kind": "free"},
                 [[cond("eleve.age", "lt", 10)]], {"categories": ["Formules"]}, active=False)
    impacts = fees.rule_impact(draft)
    assert [(i.household.name, i.before, i.after, i.delta) for i in impacts] == [
        ("Famille Martin", 95800, 41800, -54000)]


def test_effet_de_la_desactivation(priced):
    collectif = next(r for r in fees.season_rules(priced["season"]) if r.name == "Collectif offert")
    assert [(i.before, i.after) for i in fees.rule_impact(collectif)] == [(95800, 104800)]


def test_effet_respecte_la_position(priced):
    # Placée avant l'ajout de l'adhésion, une règle « tout offert » laisse l'adhésion payante.
    s = priced["season"]
    draft = rule(s, "Tout offert", {"kind": "free"}, active=False)
    fees.reorder_rules(s, [draft.id] + [r.id for r in fees.season_rules(s) if r is not draft])
    assert [(i.before, i.after) for i in fees.rule_impact(draft)] == [(95800, 4000)]


def test_simulation_d_un_foyer_fictif(priced):
    s = priced["season"]
    draft = rule(s, "Brouillon", {"kind": "total_amount", "value": -500}, active=False)
    order = pricing.Order(pricing.Household("A", True), (pricing.Student(1, "X", age=8),),
                          ((1, priced["f30"].id),))
    assert fees.simulate(s, order).total == 29400 + 2000
    assert fees.simulate(s, order, include_drafts=[draft]).total == 29400 + 2000 - 500


# ---------------------------------------------------------------------------
# Copie de saison
# ---------------------------------------------------------------------------

def test_copie_de_saison(priced):
    s = priced["season"]
    rule(s, "Variante", {"kind": "replace", "item": priced["f45"].id}, [[cond("ligne.element", "in", [priced["f30"].id])]],
         {"items": [priced["f30"].id]}, active=False)
    target = season("2027-2028")
    flute_copy = Course(season=target, discipline=priced["flute30"].discipline, level=priced["flute30"].level)
    unknown = Course(season=target, discipline=Discipline(name="Harpe"))
    db.session.add_all([flute_copy, unknown])
    summary = fees.copy_season(s, target)
    assert "5 élément(s), 6 règle(s)" in summary and "1 cours" in summary
    new_items = {i.label: i for i in fees.items(target)}
    assert new_items["Formule 30 min"].id != priced["f30"].id
    variante = fees.season_rules(target)[-1]
    assert (variante.name, variante.active) == ("Variante", False)
    assert variante.effect["item"] == new_items["Formule 45 min"].id
    assert variante.target == {"items": [new_items["Formule 30 min"].id]}
    assert variante.conditions[0][0]["value"] == [new_items["Formule 30 min"].id]
    adh = fees.season_rules(target)[0]
    assert adh.effect["item"] == new_items["Adhésion"].id
    assert flute_copy.fee_item is new_items["Formule 30 min"] and unknown.fee_item is None
    assert fees.season_settings(target).qf_bands == fees.season_settings(s).qf_bands
    assert fees.season_settings(target).resident_cities == ["Hérouville-Saint-Clair"]
    # la saison copiée calcule comme l'ancienne
    assert all(pricing.referenced_items(r.as_dict()) <= set(i.id for i in new_items.values())
               for r in fees.season_rules(target))


def test_copie_refusee(priced):
    with pytest.raises(FeeError, match="différentes"):
        fees.copy_season(priced["season"], priced["season"])
    target = season("2027-2028")
    item(target, "Déjà là", 1)
    with pytest.raises(FeeError, match="déjà des tarifs"):
        fees.copy_season(priced["season"], target)


# ---------------------------------------------------------------------------
# Fusions de l'annuaire
# ---------------------------------------------------------------------------

def test_fusion_de_foyers(priced):
    s = priced["season"]
    keep, drop = home("Gardé"), priced["home"]
    fees.save_situation(drop, s, {"quotient": 450, "tags": ["boursier"]})
    fees.add_pick(drop, priced["alice"], priced["orchestre"])
    a = fees.assess(drop, s)
    directory.merge_households(keep, drop)
    db.session.refresh(a)
    assert a.household is keep
    assert fees.situation(keep, s).quotient == 450 and fees.situation(keep, s).tags == ["boursier"]
    assert db.session.scalar(db.select(FeePick.household_id)) == keep.id


def test_fusion_de_foyers_avec_deux_cotisations(priced):
    s = priced["season"]
    other = home("Autre")
    _, m = student(other, s, "Chloé", date(2015, 1, 1))
    enroll(m, priced["piano45"])
    fees.assess(priced["home"], s)
    fees.assess(other, s)
    assert any("cotisation" in p for p in directory.household_merge_problems(other, priced["home"]))


def test_fusion_de_situations_en_double(priced):
    s = priced["season"]
    keep = home("Gardé")
    fees.save_situation(keep, s, {"quotient": 1500})
    fees.save_situation(priced["home"], s, {"quotient": 450})
    summary = directory.merge_households(keep, priced["home"])
    assert fees.situation(keep, s).quotient == 1500
    assert any("situation" in line for line in summary)


def test_fusion_de_personnes(priced):
    s = priced["season"]
    double = person("Alice", "Martin")
    directory.add_member(priced["home"], double, D)
    fees.add_pick(priced["home"], double, priced["orchestre"])
    a = fees.assess(priced["home"], s)
    directory.merge_persons(priced["alice"], double)
    assert db.session.scalar(db.select(FeePick.person_id)) == priced["alice"].id
    assert a.lines


def test_personne_supprimee_ligne_gardee(priced):
    a = fees.assess(priced["home"], priced["season"])
    line_ids = [line.id for line in a.lines if line.person_id == priced["basile"].id]
    db.session.delete(priced["basile"])
    db.session.flush()
    db.session.expire_all()
    line = db.session.get(AssessmentLine, line_ids[0])
    assert line.person_id is None and line.person_name == "Basile Martin"


def test_regle_stockee_relue_par_le_moteur(priced):
    for record in db.session.scalars(db.select(FeeRule)):
        assert pricing.validate_rule(record.as_dict()) == record.as_dict()
