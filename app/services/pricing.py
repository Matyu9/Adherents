"""Moteur de tarifs : calcule la cotisation d'un foyer à partir d'éléments et de règles ordonnées.

Le moteur ne touche pas à la base : il reçoit des objets simples (éléments, foyer, élèves, règles, réglages) et rend
un calcul détaillé. Le simulateur et le calcul réel (phase 9) passent tous deux par `compute`.

Principes (voir docs/tarifs.md) :

- un **élément** a un prix de base, en centimes ;
- une **règle** a des conditions (groupes reliés par OU, conditions d'un groupe reliées par ET), une cible et un
  effet. Les règles s'appliquent **dans l'ordre de la liste**, chacune sur le résultat des précédentes ;
- une règle d'un **groupe exclusif** ne s'applique pas à une ligne déjà touchée par une règle du même groupe : la
  première dans l'ordre l'emporte ;
- chaque ligne garde la trace des règles appliquées ; un montant ne descend jamais sous zéro ;
- l'arrondi vient en dernier.

Les règles et réglages sont des dictionnaires (stockés en JSON) : `validate_rule` et `validate_settings` les
vérifient avant tout enregistrement. Rien n'est jamais évalué comme du code.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


class PricingError(ValueError):
    """Règle ou réglage refusé, avec un message à afficher tel quel."""


# ---------------------------------------------------------------------------
# Données d'entrée
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    """Élément de cotisation : adhésion, formule, pratique collective, location…"""
    id: int
    label: str
    price: int  # prix de base, en centimes
    category: str | None = None
    regie: str | None = None


@dataclass(frozen=True)
class Student:
    id: int
    name: str = ""
    age: int | None = None
    adult: bool = False
    tags: frozenset = frozenset()
    disciplines: frozenset = frozenset()
    seniority: int = 0  # nombre de saisons déjà suivies


@dataclass(frozen=True)
class Household:
    qf_band: str | None = None
    resident: bool | None = None
    tags: frozenset = frozenset()


@dataclass(frozen=True)
class Order:
    """Ce que le foyer prend : des couples (élève, élément), dans l'ordre des inscriptions."""
    household: Household
    students: tuple
    picks: tuple  # ((student_id, item_id), …)


# ---------------------------------------------------------------------------
# Réglages
# ---------------------------------------------------------------------------

RANKINGS = {
    "price_desc": "Du plus cher au moins cher",
    "price_asc": "Du moins cher au plus cher",
    "age_desc": "Du plus âgé au plus jeune",
    "age_asc": "Du plus jeune au plus âgé",
}
ROUNDINGS = {"none": "Au centime", "tenth": "Au décime le plus proche", "euro": "À l'euro le plus proche"}
ROUNDING_SCOPES = {"line": "Chaque ligne", "total": "Le total du foyer"}
DEFAULT_SETTINGS = {"ranking": "price_desc", "ranking_categories": [], "rounding": "none", "rounding_scope": "line"}


def validate_settings(settings):
    """Réglages complétés par les valeurs par défaut, ou PricingError."""
    if not isinstance(settings, dict):
        raise PricingError("Réglages illisibles.")
    unknown = set(settings) - set(DEFAULT_SETTINGS)
    if unknown:
        raise PricingError(f"Réglage inconnu : {', '.join(sorted(unknown))}.")
    result = {**DEFAULT_SETTINGS, **settings}
    for key, allowed in (("ranking", RANKINGS), ("rounding", ROUNDINGS), ("rounding_scope", ROUNDING_SCOPES)):
        if result[key] not in allowed:
            raise PricingError(f"Valeur de réglage inconnue : {result[key]!r}.")
    categories = result["ranking_categories"]
    if not isinstance(categories, list) or not all(isinstance(c, str) and c for c in categories):
        raise PricingError("Les catégories du classement doivent être une liste de noms.")
    return result


# ---------------------------------------------------------------------------
# Critères des conditions
# ---------------------------------------------------------------------------

HOUSEHOLD, STUDENT, LINE = "household", "student", "line"
NUMBER, CHOICE, BOOL, TAGS = "number", "choice", "bool", "tags"

# champ : (libellé, niveau, type, paramètre attendu)
FIELDS = {
    "foyer.tranche_qf": ("Tranche de quotient familial", HOUSEHOLD, CHOICE, None),
    "foyer.resident": ("Résident de la commune", HOUSEHOLD, BOOL, None),
    "foyer.etiquettes": ("Étiquettes du foyer", HOUSEHOLD, TAGS, None),
    "foyer.nb_eleves": ("Nombre d'élèves du foyer", HOUSEHOLD, NUMBER, None),
    "foyer.nb_elements": ("Nombre d'éléments du foyer", HOUSEHOLD, NUMBER, "selection"),
    "eleve.age": ("Âge de l'élève", STUDENT, NUMBER, None),
    "eleve.adulte": ("Élève adulte", STUDENT, BOOL, None),
    "eleve.etiquettes": ("Étiquettes de l'élève", STUDENT, TAGS, None),
    "eleve.disciplines": ("Disciplines de l'élève", STUDENT, TAGS, None),
    "eleve.anciennete": ("Saisons déjà suivies", STUDENT, NUMBER, None),
    "eleve.rang": ("Rang de l'élève dans le foyer", STUDENT, NUMBER, None),
    "eleve.nb_elements": ("Nombre d'éléments de l'élève", STUDENT, NUMBER, "selection"),
    "ligne.element": ("Élément", LINE, CHOICE, None),
    "ligne.categorie": ("Catégorie de l'élément", LINE, CHOICE, None),
    "ligne.rang_eleve": ("Rang de l'élément dans sa catégorie, chez l'élève", LINE, NUMBER, None),
    "ligne.rang_foyer": ("Rang de l'élément dans sa catégorie, dans le foyer", LINE, NUMBER, None),
}
OPERATORS = {
    NUMBER: {"eq": "=", "ne": "≠", "lt": "<", "le": "≤", "gt": ">", "ge": "≥", "between": "entre"},
    CHOICE: {"in": "parmi", "not_in": "hors de"},
    BOOL: {"is": "est"},
    TAGS: {"has_any": "a l'une de", "has_all": "a toutes", "has_none": "n'a aucune de"},
}
LEVELS = (HOUSEHOLD, STUDENT, LINE)

# ---------------------------------------------------------------------------
# Effets
# ---------------------------------------------------------------------------

EFFECTS = {
    "percent": "Pourcentage (négatif : réduction)",
    "amount": "Montant en centimes (négatif : réduction)",
    "fixed": "Prix fixe",
    "free": "Offert",
    "replace": "Remplacer par un autre élément",
    "add": "Ajouter un élément",
    "total_percent": "Pourcentage sur le total du foyer",
    "total_amount": "Montant sur le total du foyer",
}
LINE_EFFECTS = ("percent", "amount", "fixed", "free", "replace")
TOTAL_EFFECTS = ("total_percent", "total_amount")
BASES = {"current": "Le montant à ce stade", "base": "Le prix de base de l'élément"}


def rule_level(rule):
    """Niveau le plus fin que les conditions de la règle peuvent consulter."""
    effect = rule["effect"]
    if effect["kind"] in LINE_EFFECTS:
        return LINE
    if effect["kind"] == "add" and effect.get("per") == "student":
        return STUDENT
    return HOUSEHOLD


def validate_rule(rule, items=None):
    """Règle normalisée, ou PricingError. `items` (ids connus) permet de vérifier les éléments cités."""
    if not isinstance(rule, dict):
        raise PricingError("Règle illisible.")
    name = rule.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PricingError("Une règle doit avoir un nom.")
    effect = _validate_effect(rule.get("effect"), items)
    normalized = {
        "name": name.strip(),
        "effect": effect,
        "target": _validate_target(rule.get("target") or {}, effect["kind"], items),
        "group": (rule.get("group") or "").strip() or None,
    }
    if normalized["group"] is not None and len(normalized["group"]) > 60:
        raise PricingError("Nom de groupe trop long.")
    level = rule_level(normalized)
    normalized["conditions"] = _validate_conditions(rule.get("conditions") or [], level, items)
    return normalized


def _validate_effect(effect, items):
    if not isinstance(effect, dict) or effect.get("kind") not in EFFECTS:
        raise PricingError("Effet de la règle inconnu.")
    kind = effect["kind"]
    if kind in ("percent", "total_percent"):
        value = _decimal(effect.get("value"))
        if value < -100 or value > 1000:
            raise PricingError("Le pourcentage doit être compris entre -100 et 1000.")
        result = {"kind": kind, "value": str(value)}
        if kind == "percent":
            base = effect.get("base", "current")
            if base not in BASES:
                raise PricingError("Base de calcul inconnue.")
            result["base"] = base
        return result
    if kind in ("amount", "total_amount"):
        return {"kind": kind, "value": _int(effect.get("value"), "Le montant")}
    if kind == "fixed":
        value = _int(effect.get("value"), "Le prix")
        if value < 0:
            raise PricingError("Un prix fixe ne peut pas être négatif.")
        return {"kind": kind, "value": value}
    if kind == "free":
        return {"kind": kind}
    item = _item_id(effect.get("item"), items)
    if kind == "replace":
        return {"kind": kind, "item": item}
    per = effect.get("per", "student")
    if per not in ("student", "household"):
        raise PricingError("Un élément s'ajoute pour chaque élève ou une fois pour le foyer.")
    return {"kind": kind, "item": item, "per": per}


def _validate_target(target, kind, items):
    if not isinstance(target, dict):
        raise PricingError("Cible de la règle illisible.")
    if kind not in LINE_EFFECTS:
        if target.get("items") or target.get("categories") or target.get("exclude_items") \
                or target.get("exclude_categories"):
            raise PricingError("Cette règle ne vise pas d'éléments : elle n'a pas de cible.")
        return {}
    result = {}
    for key in ("items", "exclude_items"):
        values = target.get(key) or []
        if not isinstance(values, list):
            raise PricingError("Liste d'éléments illisible.")
        if values:
            result[key] = sorted({_item_id(v, items) for v in values})
    for key in ("categories", "exclude_categories"):
        values = target.get(key) or []
        if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
            raise PricingError("Liste de catégories illisible.")
        if values:
            result[key] = sorted({v.strip() for v in values})
    return result


def _validate_conditions(groups, level, items):
    if not isinstance(groups, list) or not all(isinstance(g, list) for g in groups):
        raise PricingError("Conditions illisibles.")
    result = []
    for group in groups:
        if not group:
            continue  # un groupe vide ne veut rien dire : on l'ignore plutôt que d'en faire « toujours vrai »
        result.append([_validate_condition(c, level, items) for c in group])
    return result


def _validate_condition(condition, level, items):
    if not isinstance(condition, dict) or condition.get("field") not in FIELDS:
        raise PricingError("Critère inconnu.")
    name = condition["field"]
    label, field_level, kind, param = FIELDS[name]
    if LEVELS.index(field_level) > LEVELS.index(level):
        raise PricingError(f"« {label} » ne peut pas servir dans cette règle : elle ne porte pas sur "
                           f"{'un élément' if field_level == LINE else 'un élève'}.")
    op = condition.get("op")
    if op not in OPERATORS[kind]:
        raise PricingError(f"Opérateur inconnu pour « {label} ».")
    value = condition.get("value")
    result = {"field": name, "op": op}
    if kind == NUMBER:
        if op == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise PricingError(f"« {label} » : « entre » attend deux nombres.")
            low, high = (_int(v, label) for v in value)
            if low > high:
                raise PricingError(f"« {label} » : bornes inversées.")
            result["value"] = [low, high]
        else:
            result["value"] = _int(value, label)
    elif kind == BOOL:
        if not isinstance(value, bool):
            raise PricingError(f"« {label} » attend oui ou non.")
        result["value"] = value
    else:
        if not isinstance(value, list) or not value:
            raise PricingError(f"« {label} » : choisissez au moins une valeur.")
        if name == "ligne.element":
            result["value"] = sorted({_item_id(v, items) for v in value})
        elif not all(isinstance(v, str) and v.strip() for v in value):
            raise PricingError(f"« {label} » : valeurs illisibles.")
        else:
            result["value"] = sorted({v.strip() for v in value})
    if param == "selection":
        result["param"] = _validate_selection(condition.get("param"), label, items)
    elif condition.get("param") is not None:
        raise PricingError(f"« {label} » ne prend pas de précision.")
    return result


def _validate_selection(param, label, items):
    """Ce qu'on compte : un élément ou une catégorie (rien : tous les éléments)."""
    if param is None:
        return {}
    if not isinstance(param, dict) or len(param) > 1 or (param and not {"item", "category"} & set(param)):
        raise PricingError(f"« {label} » : précisez un élément ou une catégorie.")
    if "item" in param:
        return {"item": _item_id(param["item"], items)}
    if "category" in param:
        if not isinstance(param["category"], str) or not param["category"].strip():
            raise PricingError(f"« {label} » : catégorie illisible.")
        return {"category": param["category"].strip()}
    return {}


def referenced_items(rule):
    """Identifiants des éléments cités par une règle validée : effet, cible, conditions."""
    return set(remap_items(rule, {}, collect=True))


def remap_items(rule, mapping, collect=False):
    """Règle validée dont les éléments cités sont renumérotés selon `mapping` (copie de saison).

    Avec `collect`, renvoie seulement la liste des identifiants rencontrés.
    """
    seen = []

    def swap(item_id):
        seen.append(item_id)
        if collect:
            return item_id
        if item_id not in mapping:
            raise PricingError(f"La règle « {rule['name']} » cite un élément qui n'a pas été recopié.")
        return mapping[item_id]

    effect = dict(rule["effect"])
    if "item" in effect:
        effect["item"] = swap(effect["item"])
    target = {key: sorted(swap(v) for v in values) if key.endswith("items") else values
              for key, values in rule["target"].items()}
    conditions = []
    for group in rule["conditions"]:
        new_group = []
        for condition in group:
            condition = dict(condition)
            if condition["field"] == "ligne.element":
                condition["value"] = sorted(swap(v) for v in condition["value"])
            if "item" in condition.get("param", {}):
                condition["param"] = {"item": swap(condition["param"]["item"])}
            new_group.append(condition)
        conditions.append(new_group)
    if collect:
        return seen
    return {**rule, "effect": effect, "target": target, "conditions": conditions}


def _decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise PricingError("Pourcentage illisible.") from None
    if not result.is_finite():
        raise PricingError("Pourcentage illisible.")
    return Decimal(format(result.normalize(), "f")) if result != 0 else Decimal(0)


def _int(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise PricingError(f"{label} doit être un nombre entier.")
    if abs(value) > 10**9:
        raise PricingError(f"{label} est trop grand.")
    return value


def _item_id(value, items):
    if isinstance(value, bool) or not isinstance(value, int):
        raise PricingError("Élément illisible.")
    if items is not None and value not in items:
        raise PricingError("Cette règle cite un élément qui n'existe pas dans la saison.")
    return value


# ---------------------------------------------------------------------------
# Calcul
# ---------------------------------------------------------------------------

@dataclass
class Step:
    """Trace d'une règle appliquée : montant avant et après."""
    rule: str
    before: int
    after: int
    note: str = ""


@dataclass
class Line:
    student_id: int | None
    item: Item
    base: int
    amount: int
    position: int  # ordre d'arrivée, pour départager les égalités
    added_by: str | None = None  # règle qui a ajouté la ligne
    steps: list = field(default_factory=list)
    groups: set = field(default_factory=set)


@dataclass
class Result:
    lines: list
    adjustments: list  # règles sur le total : Step(before=total, after=total)
    rounding: int  # écart d'arrondi sur le total (arrondi « total »)
    total: int

    def by_regie(self):
        """Total par régie, avant les règles sur le total (qui ne relèvent d'aucune régie)."""
        totals = {}
        for line in self.lines:
            totals[line.item.regie] = totals.get(line.item.regie, 0) + line.amount
        return totals


def compute(order, items, rules, settings=None):
    """Calcule la cotisation d'un foyer.

    `items` : {id: Item} ; `rules` : règles validées, dans l'ordre d'application ; `settings` : réglages.
    """
    settings = validate_settings(settings or {})
    students = {s.id: s for s in order.students}
    lines = []
    for student_id, item_id in order.picks:
        if student_id not in students:
            raise PricingError("Un élément est attribué à un élève absent du foyer.")
        item = items[item_id]
        lines.append(Line(student_id, item, item.price, item.price, len(lines)))
    adjustments = []
    total_delta = 0
    for rule in rules:
        kind = rule["effect"]["kind"]
        if kind == "add":
            _apply_add(rule, order, students, items, lines, settings)
        elif kind in TOTAL_EFFECTS:
            context = _Context(order, students, lines, settings)
            if _matches(rule, context, None, None):
                before = sum(line.amount for line in lines) + total_delta
                after = max(0, before + _total_change(rule["effect"], before))
                total_delta += after - before
                adjustments.append(Step(rule["name"], before, after))
        else:
            context = _Context(order, students, lines, settings)
            for line in lines:
                if rule["group"] in line.groups or not _targets(rule["target"], line.item):
                    continue
                student = students.get(line.student_id)
                if _matches(rule, context, student, line):
                    _apply_line(rule, line, items)
    rounding = 0
    if settings["rounding_scope"] == "line":
        for line in lines:
            rounded = _round(line.amount, settings["rounding"])
            if rounded != line.amount:
                line.steps.append(Step("Arrondi", line.amount, rounded))
                line.amount = rounded
    total = sum(line.amount for line in lines) + total_delta
    if settings["rounding_scope"] == "total":
        rounded = _round(total, settings["rounding"])
        rounding, total = rounded - total, rounded
    return Result(lines, adjustments, rounding, total)


def _apply_add(rule, order, students, items, lines, settings):
    item = items[rule["effect"]["item"]]
    if rule["effect"]["per"] == "household":
        targets = [None]
    else:
        targets = list(dict.fromkeys(sid for sid, _ in order.picks))  # élèves qui prennent au moins un élément
    context = _Context(order, students, lines, settings)
    for student_id in targets:
        if _matches(rule, context, students.get(student_id), None):
            lines.append(Line(student_id, item, item.price, item.price, len(lines), added_by=rule["name"]))


def _apply_line(rule, line, items):
    effect = rule["effect"]
    before = line.amount
    note = ""
    kind = effect["kind"]
    if kind == "percent":
        reference = line.base if effect["base"] == "base" else line.amount
        line.amount = _cents(line.amount + Decimal(reference) * Decimal(effect["value"]) / 100)
    elif kind == "amount":
        line.amount += effect["value"]
    elif kind == "fixed":
        line.amount = effect["value"]
    elif kind == "free":
        line.amount = 0
    elif kind == "replace":
        new = items[effect["item"]]
        note = f"{line.item.label} → {new.label}"
        line.item = new
        line.base = new.price
        line.amount = new.price
    line.amount = max(0, line.amount)
    if rule["group"]:
        line.groups.add(rule["group"])
    line.steps.append(Step(rule["name"], before, line.amount, note))


def _total_change(effect, total):
    if effect["kind"] == "total_percent":
        return _cents(total + Decimal(total) * Decimal(effect["value"]) / 100) - total
    return effect["value"]


def _cents(amount):
    """Montant obtenu, arrondi au centime le plus proche (un demi-centime monte)."""
    return int(Decimal(amount).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _round(amount, mode):
    step = {"none": 1, "tenth": 10, "euro": 100}[mode]
    return int((Decimal(amount) / step).quantize(Decimal(1), rounding=ROUND_HALF_UP)) * step


def _targets(target, item):
    if item.id in target.get("exclude_items", ()) or item.category in target.get("exclude_categories", ()):
        return False
    if not target.get("items") and not target.get("categories"):
        return True
    return item.id in target.get("items", ()) or item.category in target.get("categories", ())


class _Context:
    """Ce que le foyer prend à l'instant où une règle s'applique : les rangs suivent les règles précédentes."""

    def __init__(self, order, students, lines, settings):
        self.household = order.household
        self.students = students
        self.settings = settings
        # Photo prise avant la règle : ce qu'elle change ne modifie pas les rangs qu'elle consulte.
        self.lines = [Line(ln.student_id, ln.item, ln.base, ln.amount, ln.position) for ln in lines]
        self._ranks = self._rank_students()
        self._line_ranks = {}

    def student_rank(self, student_id):
        return self._ranks.get(student_id)

    def _rank_students(self):
        ranking = self.settings["ranking"]
        categories = set(self.settings["ranking_categories"])
        present = list(dict.fromkeys(line.student_id for line in self.lines if line.student_id is not None))
        totals = {sid: sum(line.base for line in self.lines if line.student_id == sid
                           and (not categories or line.item.category in categories)) for sid in present}

        def key(sid):
            first = present.index(sid)
            if ranking.startswith("price"):
                value = totals[sid]
                return (-value if ranking == "price_desc" else value, first)
            age = self.students[sid].age
            if age is None:
                return (1, 0, first)  # âge inconnu : en dernier
            return (0, -age if ranking == "age_desc" else age, first)

        return {sid: rank for rank, sid in enumerate(sorted(present, key=key), start=1)}

    def line_rank(self, line, scope):
        key = (line.position, scope)
        if key not in self._line_ranks:
            snapshot = next(ln for ln in self.lines if ln.position == line.position)
            same = [other for other in self.lines if other.item.category == snapshot.item.category
                    and (scope == "household" or other.student_id == snapshot.student_id)]
            same.sort(key=lambda other: (-other.base, self.student_rank(other.student_id) or 0, other.position))
            self._line_ranks[key] = same.index(snapshot) + 1
        return self._line_ranks[key]

    def count(self, selection, student_id=None):
        return sum(1 for line in self.lines
                   if (student_id is None or line.student_id == student_id) and _selected(selection, line.item))


def _selected(selection, item):
    if "item" in selection:
        return item.id == selection["item"]
    if "category" in selection:
        return item.category == selection["category"]
    return True


def _matches(rule, context, student, line):
    groups = rule["conditions"]
    return not groups or any(all(_check(c, context, student, line) for c in group) for group in groups)


def _check(condition, context, student, line):
    name = condition["field"]
    value = _fact(name, condition, context, student, line)
    op, expected = condition["op"], condition["value"]
    kind = FIELDS[name][2]
    if value is None:
        # fait inconnu (élève sans âge, tranche non renseignée…) : seules « hors de » et « n'a aucune » sont vraies
        return op in ("not_in", "has_none")
    if kind == NUMBER:
        if op == "between":
            return expected[0] <= value <= expected[1]
        return {"eq": value == expected, "ne": value != expected, "lt": value < expected,
                "le": value <= expected, "gt": value > expected, "ge": value >= expected}[op]
    if kind == BOOL:
        return value is expected
    if kind == CHOICE:
        return (value in expected) == (op == "in")
    expected = set(expected)
    return {"has_any": bool(value & expected), "has_all": expected <= value, "has_none": not value & expected}[op]


def _fact(name, condition, context, student, line):
    household = context.household
    if name == "foyer.tranche_qf":
        return household.qf_band
    if name == "foyer.resident":
        return household.resident
    if name == "foyer.etiquettes":
        return set(household.tags)
    if name == "foyer.nb_eleves":
        return len({ln.student_id for ln in context.lines if ln.student_id is not None})
    if name == "foyer.nb_elements":
        return context.count(condition["param"])
    if name.startswith("ligne."):
        if name == "ligne.element":
            return line.item.id
        if name == "ligne.categorie":
            return line.item.category
        return context.line_rank(line, "student" if name == "ligne.rang_eleve" else "household")
    if student is None:
        return None  # élément ajouté pour le foyer : aucun élève
    if name == "eleve.age":
        return student.age
    if name == "eleve.adulte":
        return student.adult
    if name == "eleve.etiquettes":
        return set(student.tags)
    if name == "eleve.disciplines":
        return set(student.disciplines)
    if name == "eleve.anciennete":
        return student.seniority
    if name == "eleve.rang":
        return context.student_rank(student.id)
    if name == "eleve.nb_elements":
        return context.count(condition["param"], student.id)
    raise PricingError(f"Critère inconnu : {name}.")  # pragma: no cover - validé avant
