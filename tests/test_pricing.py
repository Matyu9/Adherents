"""Moteur de tarifs (services/pricing.py) : calcul pur, sans base de données."""
import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.services.pricing import (
    FIELDS,
    Household,
    Item,
    Order,
    PricingError,
    Student,
    compute,
    validate_rule,
    validate_settings,
)

ADH, F30, F45, CHORALE, ORCH, LOC, F30_FAM, FRAIS = 1, 2, 3, 4, 5, 6, 7, 8
ITEMS = {
    ADH: Item(ADH, "Adhésion", 2000, "Adhésion", "EMHN"),
    F30: Item(F30, "Formule 30 min", 42000, "Formules", "EMHN"),
    F45: Item(F45, "Formule 45 min", 54000, "Formules", "EMHN"),
    CHORALE: Item(CHORALE, "Chorale", 9000, "Pratiques collectives", "EMHN"),
    ORCH: Item(ORCH, "Orchestre d'harmonie", 6000, "Pratiques collectives", "OHN"),
    LOC: Item(LOC, "Location d'instrument", 12000, "Location", "EMHN"),
    F30_FAM: Item(F30_FAM, "Formule 30 min famille", 37000, "Formules", "EMHN"),
    FRAIS: Item(FRAIS, "Frais de dossier", 1000, None, "EMHN"),
}

ALICE = Student(1, "Alice", age=14)
BASILE = Student(2, "Basile", age=9)
CHLOE = Student(3, "Chloé", age=11)
PAPA = Student(4, "Papa", age=45, adult=True)


def rule(name="Règle", effect=None, conditions=None, target=None, group=None):
    raw = {"name": name, "effect": effect or {"kind": "free"}, "conditions": conditions or [],
           "target": target or {}, "group": group}
    return validate_rule(raw, set(ITEMS))


def run(picks, rules=(), household=None, students=(ALICE, BASILE, CHLOE, PAPA), settings=None):
    order = Order(household or Household(), tuple(students), tuple(picks))
    return compute(order, ITEMS, list(rules), settings)


def amounts(result):
    return [(line.student_id, line.item.id, line.amount) for line in result.lines]


def cond(field, op, value, param=None):
    c = {"field": field, "op": op, "value": value}
    if param is not None:
        c["param"] = param
    return c


# ---------------------------------------------------------------------------
# Sans règle
# ---------------------------------------------------------------------------

def test_grille_vide_rien_a_payer():
    result = run([])
    assert result.total == 0 and result.lines == [] and result.adjustments == []


def test_sans_regle_prix_de_base():
    result = run([(1, F30), (1, CHORALE), (2, F45)])
    assert result.total == 42000 + 9000 + 54000
    assert all(line.steps == [] for line in result.lines)


def test_element_pour_un_eleve_absent_refuse():
    with pytest.raises(PricingError):
        run([(99, F30)])


def test_total_par_regie():
    result = run([(1, F30), (1, ORCH)])
    assert result.by_regie() == {"EMHN": 42000, "OHN": 6000}


# ---------------------------------------------------------------------------
# Effets
# ---------------------------------------------------------------------------

def test_pourcentage_de_reduction():
    result = run([(1, F30)], [rule(effect={"kind": "percent", "value": "-10"})])
    assert result.total == 37800
    step = result.lines[0].steps[0]
    assert (step.rule, step.before, step.after) == ("Règle", 42000, 37800)


def test_pourcentage_arrondi_au_centime_le_plus_proche():
    item = {9: Item(9, "Bizarre", 333, "X")}
    order = Order(Household(), (ALICE,), ((1, 9),))
    r = validate_rule({"name": "R", "effect": {"kind": "percent", "value": "-50"}})
    assert compute(order, item, [r]).total == 167  # 166,5 arrondi au-dessus


def test_majoration():
    result = run([(1, F30)], [rule(effect={"kind": "percent", "value": "20"})])
    assert result.total == 50400


def test_montant_fixe_retire():
    result = run([(1, F30)], [rule(effect={"kind": "amount", "value": -5000})])
    assert result.total == 37000


def test_montant_ne_descend_jamais_sous_zero():
    result = run([(1, ADH)], [rule(effect={"kind": "amount", "value": -5000})])
    assert result.total == 0


def test_prix_fixe():
    result = run([(1, F30)], [rule(effect={"kind": "fixed", "value": 30000})])
    assert result.total == 30000


def test_offert():
    result = run([(1, F30), (1, CHORALE)], [rule(effect={"kind": "free"}, target={"categories": ["Pratiques collectives"]})])
    assert amounts(result) == [(1, F30, 42000), (1, CHORALE, 0)]


def test_remplacer_par_un_autre_element():
    result = run([(1, F30)], [rule(effect={"kind": "replace", "item": F30_FAM})])
    line = result.lines[0]
    assert line.item.id == F30_FAM and line.amount == 37000 and line.base == 37000
    assert line.steps[0].note == "Formule 30 min → Formule 30 min famille"


def test_ajouter_un_element_par_eleve():
    result = run([(1, F30), (2, F45), (2, CHORALE)],
                 [rule("Adhésion", effect={"kind": "add", "item": ADH, "per": "student"})])
    adhesions = [line for line in result.lines if line.item.id == ADH]
    assert [line.student_id for line in adhesions] == [1, 2]  # une seule par élève
    assert adhesions[0].added_by == "Adhésion"


def test_ajouter_un_element_pour_le_foyer():
    result = run([(1, F30), (2, F45)], [rule(effect={"kind": "add", "item": FRAIS, "per": "household"})])
    frais = [line for line in result.lines if line.item.id == FRAIS]
    assert len(frais) == 1 and frais[0].student_id is None


def test_ajout_sous_condition_eleve():
    adh_mineurs = rule(effect={"kind": "add", "item": ADH, "per": "student"},
                       conditions=[[cond("eleve.adulte", "is", False)]])
    result = run([(1, F30), (4, F30)], [adh_mineurs])
    assert [line.student_id for line in result.lines if line.item.id == ADH] == [1]


def test_pourcentage_sur_le_total():
    result = run([(1, F30), (2, F45)], [rule("Remise", effect={"kind": "total_percent", "value": "-5"})])
    assert result.total == 96000 - 4800
    assert result.adjustments[0].rule == "Remise"
    assert result.lines[0].amount == 42000  # les lignes ne bougent pas


def test_montant_sur_le_total():
    result = run([(1, ADH)], [rule(effect={"kind": "total_amount", "value": -5000})])
    assert result.total == 0


# ---------------------------------------------------------------------------
# Cible
# ---------------------------------------------------------------------------

def test_cible_par_element():
    result = run([(1, F30), (1, F45)], [rule(target={"items": [F45]})])
    assert amounts(result) == [(1, F30, 42000), (1, F45, 0)]


def test_cible_avec_exclusion():
    r = rule(effect={"kind": "percent", "value": "20"}, target={"exclude_categories": ["Adhésion"]})
    result = run([(1, ADH), (1, F30)], [r])
    assert amounts(result) == [(1, ADH, 2000), (1, F30, 50400)]


def test_exclusion_l_emporte_sur_la_cible():
    r = rule(target={"categories": ["Formules"], "exclude_items": [F45]})
    result = run([(1, F30), (1, F45)], [r])
    assert amounts(result) == [(1, F30, 0), (1, F45, 54000)]


# ---------------------------------------------------------------------------
# Conditions : ET, OU, faits inconnus
# ---------------------------------------------------------------------------

def test_conditions_et():
    r = rule(conditions=[[cond("foyer.tranche_qf", "in", ["A"]), cond("foyer.resident", "is", True)]])
    assert run([(1, F30)], [r], Household("A", True)).total == 0
    assert run([(1, F30)], [r], Household("A", False)).total == 42000


def test_conditions_ou():
    r = rule(conditions=[[cond("foyer.tranche_qf", "in", ["A"])], [cond("foyer.etiquettes", "has_any", ["boursier"])]])
    assert run([(1, F30)], [r], Household("A")).total == 0
    assert run([(1, F30)], [r], Household("C", tags=frozenset({"boursier"}))).total == 0
    assert run([(1, F30)], [r], Household("C")).total == 42000


def test_sans_condition_toujours_vraie():
    assert run([(1, F30)], [rule()]).total == 0


def test_tranche_inconnue():
    hors_a = rule(conditions=[[cond("foyer.tranche_qf", "not_in", ["A"])]])
    en_a = rule(conditions=[[cond("foyer.tranche_qf", "in", ["A"])]])
    assert run([(1, F30)], [en_a], Household(None)).total == 42000
    assert run([(1, F30)], [hors_a], Household(None)).total == 0


def test_age_inconnu_ne_satisfait_pas_une_comparaison():
    r = rule(conditions=[[cond("eleve.age", "lt", 18)]])
    sans_age = Student(1, "X")
    assert run([(1, F30)], [r], students=[sans_age]).total == 42000


def test_age_entre():
    r = rule(conditions=[[cond("eleve.age", "between", [10, 14])]])
    result = run([(1, F30), (2, F30), (3, F30)], [r])
    assert [line.amount for line in result.lines] == [0, 42000, 0]


def test_etiquettes_de_l_eleve():
    prof = Student(1, "Prof", tags=frozenset({"personnel", "musicien"}))
    toutes = rule(conditions=[[cond("eleve.etiquettes", "has_all", ["personnel", "musicien"])]])
    aucune = rule(conditions=[[cond("eleve.etiquettes", "has_none", ["personnel"])]])
    assert run([(1, F30)], [toutes], students=[prof]).total == 0
    assert run([(1, F30)], [aucune], students=[prof]).total == 42000


def test_disciplines_et_anciennete():
    eleve = Student(1, "X", disciplines=frozenset({"Piano"}), seniority=3)
    r = rule(conditions=[[cond("eleve.disciplines", "has_any", ["Piano"]), cond("eleve.anciennete", "ge", 3)]])
    assert run([(1, F30)], [r], students=[eleve]).total == 0


def test_nombre_d_eleves_du_foyer():
    r = rule(effect={"kind": "total_percent", "value": "-10"}, conditions=[[cond("foyer.nb_eleves", "ge", 3)]])
    assert run([(1, ADH), (2, ADH)], [r]).total == 4000
    assert run([(1, ADH), (2, ADH), (3, ADH)], [r]).total == 5400


def test_nombre_d_elements_d_une_categorie_chez_l_eleve():
    r = rule(target={"categories": ["Pratiques collectives"]},
             conditions=[[cond("eleve.nb_elements", "ge", 1, {"category": "Formules"})]])
    result = run([(1, F30), (1, CHORALE), (2, CHORALE)], [r])
    assert amounts(result) == [(1, F30, 42000), (1, CHORALE, 0), (2, CHORALE, 9000)]


def test_nombre_d_elements_du_foyer_par_element():
    r = rule(effect={"kind": "total_amount", "value": -1000},
             conditions=[[cond("foyer.nb_elements", "ge", 2, {"item": LOC})]])
    assert run([(1, LOC), (2, LOC)], [r]).total == 23000


def test_element_et_categorie_de_la_ligne():
    r = rule(conditions=[[cond("ligne.element", "in", [CHORALE])], [cond("ligne.categorie", "in", ["Location"])]])
    result = run([(1, F30), (1, CHORALE), (1, LOC)], [r])
    assert [line.amount for line in result.lines] == [42000, 0, 0]


# ---------------------------------------------------------------------------
# Ordre des règles et groupes exclusifs
# ---------------------------------------------------------------------------

def test_ordre_des_regles_en_cascade():
    moins_10 = rule("−10 %", effect={"kind": "percent", "value": "-10"})
    moins_50e = rule("−50 €", effect={"kind": "amount", "value": -5000})
    assert run([(1, F30)], [moins_10, moins_50e]).total == 42000 * 9 // 10 - 5000  # 32 800
    assert run([(1, F30)], [moins_50e, moins_10]).total == (42000 - 5000) * 9 // 10  # 33 300


def test_pourcentage_sur_le_prix_de_base():
    moins_10 = rule(effect={"kind": "percent", "value": "-10", "base": "base"})
    moins_50e = rule(effect={"kind": "amount", "value": -5000})
    assert run([(1, F30)], [moins_50e, moins_10]).total == 42000 - 5000 - 4200


def test_trace_dans_l_ordre():
    r1 = rule("QF A", effect={"kind": "percent", "value": "-30"})
    r2 = rule("Famille", effect={"kind": "percent", "value": "-10"})
    steps = run([(1, F30)], [r1, r2]).lines[0].steps
    assert [(s.rule, s.before, s.after) for s in steps] == [("QF A", 42000, 29400), ("Famille", 29400, 26460)]


def test_groupe_exclusif_la_premiere_l_emporte():
    qf_a = rule("QF A", effect={"kind": "percent", "value": "-30"}, group="social",
                conditions=[[cond("foyer.tranche_qf", "in", ["A"])]])
    boursier = rule("Boursier", effect={"kind": "percent", "value": "-50"}, group="social",
                    conditions=[[cond("foyer.etiquettes", "has_any", ["boursier"])]])
    famille = rule("Famille", effect={"kind": "percent", "value": "-10"})
    both = Household("A", tags=frozenset({"boursier"}))
    result = run([(1, F30)], [qf_a, boursier, famille], both)
    assert [s.rule for s in result.lines[0].steps] == ["QF A", "Famille"]
    # inverser l'ordre change la règle retenue
    result = run([(1, F30)], [boursier, qf_a, famille], both)
    assert [s.rule for s in result.lines[0].steps] == ["Boursier", "Famille"]


def test_groupe_exclusif_par_ligne():
    a = rule("A", effect={"kind": "percent", "value": "-10"}, group="g", target={"items": [F30]})
    b = rule("B", effect={"kind": "percent", "value": "-20"}, group="g")
    result = run([(1, F30), (1, F45)], [a, b])
    assert [[s.rule for s in line.steps] for line in result.lines] == [["A"], ["B"]]


def test_ligne_ajoutee_soumise_aux_regles_suivantes_seulement():
    reduc = rule("Réduc", effect={"kind": "percent", "value": "-50"})
    ajout = rule("Adhésion", effect={"kind": "add", "item": ADH, "per": "student"})
    avant = run([(1, F30)], [reduc, ajout])
    apres = run([(1, F30)], [ajout, reduc])
    assert avant.total == 21000 + 2000
    assert apres.total == 21000 + 1000


def test_regle_sur_le_total_calculee_a_sa_place():
    total = rule("Remise", effect={"kind": "total_percent", "value": "-10"})
    offert = rule("Offert", target={"items": [CHORALE]})
    assert run([(1, F30), (1, CHORALE)], [total, offert]).total == 42000 - 5100
    assert run([(1, F30), (1, CHORALE)], [offert, total]).total == 42000 - 4200


# ---------------------------------------------------------------------------
# Rangs : dégressivité
# ---------------------------------------------------------------------------

def famille(rang, pct):
    return rule(f"Famille {rang}", effect={"kind": "percent", "value": str(pct)}, group="famille",
                target={"categories": ["Formules"]}, conditions=[[cond("eleve.rang", "ge", rang)]])


DEGRESSIVITE = [famille(3, -20), famille(2, -10)]


def test_degressivite_le_plus_cher_paie_plein_tarif():
    result = run([(1, F30), (2, F45), (3, F30)], DEGRESSIVITE)
    # Basile (F45) est le plus cher : rang 1. Alice et Chloé à égalité : l'ordre d'inscription départage.
    assert amounts(result) == [(1, F30, 37800), (2, F45, 54000), (3, F30, 33600)]


def test_degressivite_le_moins_cher_paie_plein_tarif():
    result = run([(1, F30), (2, F45)], DEGRESSIVITE, settings={"ranking": "price_asc"})
    assert amounts(result) == [(1, F30, 42000), (2, F45, 48600)]


def test_degressivite_par_age():
    result = run([(1, F30), (2, F30), (3, F30)], DEGRESSIVITE, settings={"ranking": "age_desc"})
    assert amounts(result) == [(1, F30, 42000), (2, F30, 33600), (3, F30, 37800)]
    result = run([(1, F30), (2, F30), (3, F30)], DEGRESSIVITE, settings={"ranking": "age_asc"})
    assert amounts(result) == [(1, F30, 33600), (2, F30, 42000), (3, F30, 37800)]


def test_age_inconnu_classe_en_dernier():
    sans_age = Student(9, "?")
    result = run([(9, F30), (1, F30)], DEGRESSIVITE, students=[sans_age, ALICE], settings={"ranking": "age_desc"})
    assert amounts(result) == [(9, F30, 37800), (1, F30, 42000)]


def test_classement_sur_certaines_categories():
    # Alice a une location chère, mais le classement ne compte que les formules : Basile (F45) passe devant.
    picks = [(1, F30), (1, LOC), (2, F45)]
    sans = run(picks, DEGRESSIVITE, settings={"ranking": "price_desc"})
    avec = run(picks, DEGRESSIVITE, settings={"ranking": "price_desc", "ranking_categories": ["Formules"]})
    assert sans.lines[2].amount == 48600 and sans.lines[0].amount == 42000
    assert avec.lines[2].amount == 54000 and avec.lines[0].amount == 37800


def test_rang_ignore_les_eleves_sans_element():
    result = run([(2, F30)], DEGRESSIVITE)
    assert result.total == 42000


def test_rang_fige_pendant_une_regle():
    # Remplacer la formule du premier élève par une moins chère ne fait pas passer le second premier
    # au cours de la même règle.
    remplace = rule("Famille", effect={"kind": "replace", "item": F30_FAM}, target={"items": [F30, F45]},
                    conditions=[[cond("eleve.rang", "ge", 2)]])
    result = run([(1, F45), (2, F30)], [remplace])
    assert [line.item.id for line in result.lines] == [F45, F30_FAM]


def test_rang_d_une_ligne_fige_pendant_une_regle():
    # La F45 (rang 1) devient une formule famille moins chère que la F30 : la F30 ne devient pas rang 1 pour autant.
    r = rule(effect={"kind": "replace", "item": F30_FAM}, target={"categories": ["Formules"]},
             conditions=[[cond("ligne.rang_eleve", "eq", 1)]])
    result = run([(1, F45), (1, F30)], [r])
    assert [line.item.id for line in result.lines] == [F30_FAM, F30]


def test_variante_famille_par_remplacement():
    remplace = rule("Famille", effect={"kind": "replace", "item": F30_FAM}, target={"items": [F30]},
                    conditions=[[cond("eleve.rang", "ge", 2)]])
    result = run([(1, F30), (2, F30)], [remplace])
    assert [line.item.id for line in result.lines] == [F30, F30_FAM]


def test_deuxieme_instrument_chez_l_eleve():
    second = rule("2e instrument", effect={"kind": "percent", "value": "-50"}, target={"categories": ["Formules"]},
                  conditions=[[cond("ligne.rang_eleve", "ge", 2)]])
    result = run([(1, F30), (1, F45), (2, F30)], [second])
    assert amounts(result) == [(1, F30, 21000), (1, F45, 54000), (2, F30, 42000)]


def test_troisieme_pratique_collective_du_foyer_offerte():
    r = rule(target={"categories": ["Pratiques collectives"]}, conditions=[[cond("ligne.rang_foyer", "ge", 3)]])
    result = run([(1, CHORALE), (2, ORCH), (3, CHORALE)], [r])
    # classement par prix : les deux chorales (90 €) puis l'orchestre (60 €), qui est offert
    assert amounts(result) == [(1, CHORALE, 9000), (2, ORCH, 0), (3, CHORALE, 9000)]


# ---------------------------------------------------------------------------
# Arrondi
# ---------------------------------------------------------------------------

def test_arrondi_au_decime_par_ligne():
    r = rule(effect={"kind": "percent", "value": "-7"})
    result = run([(1, F30), (2, CHORALE)], [r], settings={"rounding": "tenth"})
    assert [line.amount for line in result.lines] == [39060, 8370]
    assert result.rounding == 0


def test_arrondi_au_decime_le_plus_proche():
    r = rule(effect={"kind": "amount", "value": -5})
    assert run([(1, F30)], [r], settings={"rounding": "tenth"}).total == 42000  # 419,95 → 420,00
    r = rule(effect={"kind": "amount", "value": -6})
    assert run([(1, F30)], [r], settings={"rounding": "tenth"}).total == 41990  # 419,94 → 419,90


def test_arrondi_trace():
    r = rule(effect={"kind": "amount", "value": -6})
    steps = run([(1, F30)], [r], settings={"rounding": "euro"}).lines[0].steps
    assert steps[-1].rule == "Arrondi" and steps[-1].after == 42000


def test_arrondi_sur_le_total():
    r = rule(effect={"kind": "amount", "value": -4})
    result = run([(1, F30), (2, F30)], [r], settings={"rounding": "tenth", "rounding_scope": "total"})
    assert [line.amount for line in result.lines] == [41996, 41996]
    assert result.total == 83990 and result.rounding == -2


# ---------------------------------------------------------------------------
# Validation des règles et réglages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, message", [
    ({"effect": {"kind": "free"}}, "nom"),
    ({"name": " ", "effect": {"kind": "free"}}, "nom"),
    ({"name": "R"}, "Effet"),
    ({"name": "R", "effect": {"kind": "eval"}}, "Effet"),
    ({"name": "R", "effect": {"kind": "percent", "value": "abc"}}, "Pourcentage"),
    ({"name": "R", "effect": {"kind": "percent", "value": "NaN"}}, "Pourcentage"),
    ({"name": "R", "effect": {"kind": "percent", "value": "-150"}}, "entre"),
    ({"name": "R", "effect": {"kind": "percent", "value": "-10", "base": "x"}}, "Base"),
    ({"name": "R", "effect": {"kind": "amount", "value": 1.5}}, "entier"),
    ({"name": "R", "effect": {"kind": "amount", "value": True}}, "entier"),
    ({"name": "R", "effect": {"kind": "fixed", "value": -1}}, "négatif"),
    ({"name": "R", "effect": {"kind": "replace", "item": 999}}, "n'existe pas"),
    ({"name": "R", "effect": {"kind": "add", "item": 1, "per": "cours"}}, "ajoute"),
    ({"name": "R", "effect": {"kind": "total_percent", "value": "-5"}, "target": {"items": [1]}}, "cible"),
    ({"name": "R", "effect": {"kind": "free"}, "conditions": [[{"field": "__import__", "op": "eq", "value": 1}]]},
     "Critère"),
    ({"name": "R", "effect": {"kind": "free"}, "conditions": [[cond("eleve.age", "in", [1])]]}, "Opérateur"),
    ({"name": "R", "effect": {"kind": "free"}, "conditions": [[cond("eleve.age", "between", [10, 5])]]}, "inversées"),
    ({"name": "R", "effect": {"kind": "free"}, "conditions": [[cond("eleve.age", "between", [1])]]}, "deux"),
    ({"name": "R", "effect": {"kind": "free"}, "conditions": [[cond("foyer.resident", "is", "oui")]]}, "oui ou non"),
    ({"name": "R", "effect": {"kind": "free"}, "conditions": [[cond("foyer.tranche_qf", "in", [])]]}, "au moins"),
    ({"name": "R", "effect": {"kind": "free"}, "conditions": [[cond("ligne.element", "in", [999])]]}, "n'existe pas"),
    ({"name": "R", "effect": {"kind": "free"}, "conditions": [[cond("eleve.age", "eq", 3, {"category": "X"})]]},
     "précision"),
    ({"name": "R", "effect": {"kind": "free"},
      "conditions": [[cond("eleve.nb_elements", "ge", 1, {"item": 1, "category": "X"})]]}, "précisez"),
    ({"name": "R", "effect": {"kind": "free"}, "conditions": {"field": "eleve.age"}}, "illisibles"),
    # une règle sur le foyer ne peut pas consulter un élève ni une ligne
    ({"name": "R", "effect": {"kind": "total_amount", "value": -1}, "conditions": [[cond("eleve.age", "lt", 18)]]},
     "un élève"),
    ({"name": "R", "effect": {"kind": "add", "item": 1, "per": "household"},
      "conditions": [[cond("eleve.rang", "ge", 2)]]}, "un élève"),
    ({"name": "R", "effect": {"kind": "add", "item": 1, "per": "student"},
      "conditions": [[cond("ligne.rang_eleve", "ge", 2)]]}, "un élément"),
    ({"name": "R", "effect": {"kind": "free"}, "group": "x" * 61}, "groupe"),
])
def test_regle_refusee(raw, message):
    with pytest.raises(PricingError, match=message):
        validate_rule(raw, set(ITEMS))


def test_regle_normalisee():
    normalized = validate_rule({
        "name": "  QF A ", "group": "  ", "effect": {"kind": "percent", "value": "-10.0"},
        "target": {"categories": ["Formules", " Formules"], "items": []},
        "conditions": [[], [cond("foyer.tranche_qf", "in", ["B", "A", "A"])]],
    }, set(ITEMS))
    assert normalized == {
        "name": "QF A", "group": None, "effect": {"kind": "percent", "value": "-10", "base": "current"},
        "target": {"categories": ["Formules"]},
        "conditions": [[{"field": "foyer.tranche_qf", "op": "in", "value": ["A", "B"]}]],
    }


def test_regle_validee_deux_fois_identique():
    raw = {"name": "R", "effect": {"kind": "percent", "value": "-12.5"},
           "conditions": [[cond("eleve.nb_elements", "ge", 2, {"category": "Formules"})]]}
    once = validate_rule(raw, set(ITEMS))
    assert validate_rule(once, set(ITEMS)) == once


def test_regle_sans_liste_d_elements_connus():
    assert validate_rule({"name": "R", "effect": {"kind": "replace", "item": 12345}})["effect"]["item"] == 12345


def test_tous_les_criteres_ont_des_operateurs():
    from app.services.pricing import OPERATORS
    assert all(kind in OPERATORS for _, _, kind, _ in FIELDS.values())


@pytest.mark.parametrize("settings", [
    {"ranking": "hasard"}, {"rounding": "dizaine"}, {"rounding_scope": "regie"}, {"inconnu": 1},
    {"ranking_categories": "Formules"}, {"ranking_categories": [""]}, [],
])
def test_reglages_refuses(settings):
    with pytest.raises(PricingError):
        validate_settings(settings)


def test_reglages_par_defaut():
    assert validate_settings({}) == {"ranking": "price_desc", "ranking_categories": [], "rounding": "none",
                                     "rounding_scope": "line"}


# ---------------------------------------------------------------------------
# Propriétés
# ---------------------------------------------------------------------------

picks_strategy = st.lists(st.tuples(st.sampled_from([1, 2, 3, 4]), st.sampled_from(sorted(ITEMS))), max_size=8)
percent_strategy = st.integers(-100, 100).map(str)


@given(picks_strategy, st.lists(percent_strategy, max_size=4))
def test_jamais_negatif_et_total_coherent(picks, percents):
    rules = [rule(f"R{i}", effect={"kind": "percent", "value": p}) for i, p in enumerate(percents)]
    rules.append(rule("Moins", effect={"kind": "amount", "value": -30000}, target={"categories": ["Formules"]}))
    result = run(picks, rules)
    assert all(line.amount >= 0 for line in result.lines)
    assert result.total == sum(line.amount for line in result.lines)
    for line in result.lines:  # chaque étape part de la précédente
        previous = line.base
        for step in line.steps:
            assert step.before == previous
            previous = step.after
        assert previous == line.amount


@given(picks_strategy, st.sampled_from(["tenth", "euro"]), st.sampled_from(["line", "total"]))
def test_arrondi_respecte_le_pas(picks, rounding, scope):
    r = rule(effect={"kind": "percent", "value": "-13"})
    result = run(picks, [r], settings={"rounding": rounding, "rounding_scope": scope})
    step = 10 if rounding == "tenth" else 100
    assert result.total % step == 0


@given(picks_strategy)
def test_reduction_ne_fait_jamais_payer_plus(picks):
    without = run(picks, []).total
    with_reduction = run(picks, DEGRESSIVITE + [rule("2e", effect={"kind": "percent", "value": "-50"},
                                                    conditions=[[cond("ligne.rang_eleve", "ge", 2)]])]).total
    assert with_reduction <= without
