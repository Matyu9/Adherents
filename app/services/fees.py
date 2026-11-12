"""Tarifs et cotisations : éléments, règles, réglages, situation des foyers, calcul et validation (voir docs/tarifs.md).

Le calcul lui-même est dans services/pricing.py ; ce module lit la base, prépare ce que le moteur attend et garde ses
résultats. Les services ne font pas de commit.
"""

from dataclasses import dataclass, field
from datetime import datetime

from ..extensions import db, fold
from ..models import (
    Assessment,
    AssessmentLine,
    Course,
    Enrollment,
    FeeItem,
    FeePick,
    FeeRule,
    FeeSeason,
    Household,
    HouseholdMember,
    HouseholdSeason,
    Membership,
    Regie,
    Season,
)
from . import pricing
from .activity import log
from .pricing import PricingError


class FeeError(ValueError):
    """Opération refusée, avec un message à afficher tel quel."""


MAX_PRICE = 100_000_00  # 100 000 € : au-delà, c'est une faute de frappe


# ---------------------------------------------------------------------------
# Régies
# ---------------------------------------------------------------------------


def regies(include_archived=False):
    query = db.select(Regie).order_by(Regie.name)
    if not include_archived:
        query = query.where(Regie.archived_at.is_(None))
    return list(db.session.scalars(query))


def create_regie(name):
    name = (name or "").strip()
    if not name:
        raise FeeError("Donnez un nom à la régie.")
    if db.session.scalar(db.select(Regie).where(db.func.lower(Regie.name) == name.lower())) is not None:
        raise FeeError(f"La régie « {name} » existe déjà.")
    regie = Regie(name=name)
    db.session.add(regie)
    db.session.flush()
    return regie


# ---------------------------------------------------------------------------
# Réglages de la saison
# ---------------------------------------------------------------------------


def season_settings(season):
    """Réglages des tarifs de la saison, créés vides au premier accès."""
    if season.fee_season is None:
        season.fee_season = FeeSeason(engine={}, qf_bands=[], resident_cities=[])
        db.session.flush()
    return season.fee_season


def save_settings(season, engine=None, qf_bands=None, default_band=None, resident_cities=None):
    settings = season_settings(season)
    try:
        engine = pricing.validate_settings(engine or {})
    except PricingError as error:
        raise FeeError(str(error)) from None
    bands = _validate_bands(qf_bands or [])
    labels = [band["label"] for band in bands]
    default_band = (default_band or "").strip() or None
    if default_band is not None and default_band not in labels:
        raise FeeError("La tranche par défaut doit être l'une des tranches définies.")
    cities = sorted({c.strip() for c in (resident_cities or []) if c and c.strip()}, key=fold)
    changes = {}
    for name, value in (
        ("engine", engine),
        ("qf_bands", bands),
        ("default_band", default_band),
        ("resident_cities", cities),
    ):
        if getattr(settings, name) != value:
            changes[name] = [getattr(settings, name), value]
            setattr(settings, name, value)
    if changes:
        log("fee.settings", f"Réglages des tarifs {season.label} modifiés", changes=changes)
    db.session.flush()
    return settings


def _validate_bands(bands):
    """Tranches de la plus basse à la plus haute ; seule la dernière peut n'avoir pas de plafond."""
    result, labels, previous = [], set(), None
    for index, band in enumerate(bands):
        label = (band.get("label") or "").strip()
        if not label or len(label) > 10:
            raise FeeError("Chaque tranche a un nom court (10 caractères au plus).")
        if label in labels:
            raise FeeError(f"La tranche « {label} » est en double.")
        labels.add(label)
        ceiling = band.get("max")
        last = index == len(bands) - 1
        if ceiling is None:
            if not last:
                raise FeeError(f"La tranche « {label} » doit avoir un plafond : seule la dernière n'en a pas.")
        elif isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling <= 0:
            raise FeeError(f"Le plafond de la tranche « {label} » doit être un nombre entier positif.")
        elif previous is not None and ceiling <= previous:
            raise FeeError("Les plafonds des tranches doivent aller en croissant.")
        previous = ceiling if ceiling is not None else previous
        result.append({"label": label, "max": ceiling})
    return result


# ---------------------------------------------------------------------------
# Éléments
# ---------------------------------------------------------------------------


def items(season):
    return list(
        db.session.scalars(
            db.select(FeeItem).where(FeeItem.season_id == season.id).order_by(FeeItem.position, FeeItem.label)
        )
    )


def categories(season):
    return sorted({item.category for item in items(season) if item.category}, key=fold)


def save_item(season, data, item=None):
    """Crée ou modifie un élément. `data` : label, price (centimes), category, code, regie, notes."""
    label = (data.get("label") or "").strip()
    if not label:
        raise FeeError("Donnez un libellé à l'élément.")
    price = data.get("price")
    if isinstance(price, bool) or not isinstance(price, int) or not 0 <= price <= MAX_PRICE:
        raise FeeError("Le prix doit être un montant positif.")
    same = db.session.scalar(
        db.select(FeeItem).where(FeeItem.season_id == season.id, db.func.lower(FeeItem.label) == label.lower())
    )
    if same is not None and same is not item:
        raise FeeError(f"La saison a déjà un élément « {label} ».")
    regie = data.get("regie")
    if regie is not None and regie.archived and (item is None or item.regie is not regie):
        raise FeeError("Cette régie est archivée.")
    values = {
        "label": label,
        "price": price,
        "category": (data.get("category") or "").strip() or None,
        "code": (data.get("code") or "").strip() or None,
        "regie": regie,
        "notes": (data.get("notes") or "").strip() or None,
    }
    if item is None:
        position = db.session.scalar(db.select(db.func.max(FeeItem.position)).where(FeeItem.season_id == season.id))
        item = FeeItem(season=season, position=(position or 0) + 1, **values)
        db.session.add(item)
        db.session.flush()
        log("fee.item", f"Élément « {label} » ajouté aux tarifs {season.label}")
        return item
    if item.season_id != season.id:
        raise FeeError("Cet élément appartient à une autre saison.")
    changes = {}
    for name, value in values.items():
        old = getattr(item, name)
        if old != value:
            changes[name] = [
                old.name if isinstance(old, Regie) else old,
                value.name if isinstance(value, Regie) else value,
            ]
            setattr(item, name, value)
    if changes:
        log("fee.item", f"Élément « {label} » modifié ({season.label})", changes=changes)
    db.session.flush()
    return item


def item_usage(item):
    """Ce qui empêche de supprimer un élément : cours, éléments pris à part, règles."""
    problems = []
    courses = db.session.scalar(db.select(db.func.count(Course.id)).where(Course.fee_item_id == item.id))
    if courses:
        problems.append(f"{courses} cours le facturent")
    picks = db.session.scalar(db.select(db.func.count(FeePick.id)).where(FeePick.item_id == item.id))
    if picks:
        problems.append(f"{picks} élève(s) l'ont pris à part")
    rules = [rule.name for rule in season_rules(item.season) if item.id in pricing.referenced_items(rule.as_dict())]
    if rules:
        problems.append("règles : " + ", ".join(rules))
    return problems


def delete_item(item):
    if problems := item_usage(item):
        raise FeeError("Élément utilisé : " + " ; ".join(problems) + ".")
    log("fee.item", f"Élément « {item.label} » retiré des tarifs {item.season.label}")
    db.session.delete(item)
    db.session.flush()


# ---------------------------------------------------------------------------
# Règles
# ---------------------------------------------------------------------------


def season_rules(season, active_only=False):
    query = db.select(FeeRule).where(FeeRule.season_id == season.id).order_by(FeeRule.position, FeeRule.id)
    if active_only:
        query = query.where(FeeRule.active.is_(True))
    return list(db.session.scalars(query))


def save_rule(season, raw, rule=None):
    """Crée ou modifie une règle après l'avoir fait vérifier par le moteur. Une nouvelle règle arrive en dernier,
    en brouillon : elle ne compte qu'une fois activée."""
    known = {item.id for item in items(season)}
    try:
        normalized = pricing.validate_rule(raw, known)
    except PricingError as error:
        raise FeeError(str(error)) from None
    values = {
        "name": normalized["name"],
        "conditions": normalized["conditions"],
        "target": normalized["target"],
        "effect": normalized["effect"],
        "exclusive_group": normalized["group"],
        "notes": (raw.get("notes") or "").strip() or None,
    }
    if rule is None:
        rules = season_rules(season)
        rule = FeeRule(season=season, position=len(rules), active=False, **values)
        db.session.add(rule)
        db.session.flush()
        log("fee.rule", f"Règle « {rule.name} » ajoutée aux tarifs {season.label} (brouillon)")
        return rule
    if rule.season_id != season.id:
        raise FeeError("Cette règle appartient à une autre saison.")
    changes = {}
    for name, value in values.items():
        if getattr(rule, name) != value:
            changes[name] = [getattr(rule, name), value]
            setattr(rule, name, value)
    if changes:
        log("fee.rule", f"Règle « {rule.name} » modifiée ({season.label})", changes=changes)
    db.session.flush()
    return rule


def set_rule_active(rule, active):
    if rule.active != active:
        rule.active = active
        log("fee.rule", f"Règle « {rule.name} » {'activée' if active else 'remise en brouillon'} ({rule.season.label})")
        db.session.flush()


def delete_rule(rule):
    season = rule.season
    log("fee.rule", f"Règle « {rule.name} » supprimée des tarifs {season.label}")
    db.session.delete(rule)
    db.session.flush()
    _renumber(season_rules(season))


def move_rule(rule, offset):
    """Monte (offset < 0) ou descend (offset > 0) une règle dans l'ordre d'application."""
    rules = season_rules(rule.season)
    index = rules.index(rule)
    target = max(0, min(len(rules) - 1, index + offset))
    if target != index:
        rules.insert(target, rules.pop(index))
        _renumber(rules)
        log("fee.rule", f"Règle « {rule.name} » déplacée en position {target + 1} ({rule.season.label})")


def move_rule_to(rule, position):
    """Place une règle à la position donnée (1 = appliquée en premier)."""
    rules = season_rules(rule.season)
    index = rules.index(rule)
    target = max(0, min(len(rules) - 1, position - 1))
    if target != index:
        move_rule(rule, target - index)


def reorder_rules(season, rule_ids):
    """Range les règles dans l'ordre donné : toutes les règles de la saison, chacune une fois."""
    rules = {rule.id: rule for rule in season_rules(season)}
    if sorted(rule_ids) != sorted(rules):
        raise FeeError("L'ordre envoyé ne correspond pas aux règles de la saison : rechargez la page.")
    _renumber([rules[rule_id] for rule_id in rule_ids])
    log("fee.rule", f"Règles des tarifs {season.label} réordonnées")


def _renumber(rules):
    for position, rule in enumerate(rules):
        rule.position = position
    db.session.flush()


# ---------------------------------------------------------------------------
# Situation des foyers
# ---------------------------------------------------------------------------

SITUATION_FIELDS = ("qf_band", "quotient", "qf_proof", "resident", "tags")


def situation(household, season):
    """Situation du foyer pour la saison, ou une situation vide (non enregistrée) s'il n'en a pas."""
    found = db.session.get(HouseholdSeason, (household.id, season.id))
    return found if found is not None else HouseholdSeason(household_id=household.id, season_id=season.id, tags=[])


def save_situation(household, season, values):
    settings = season_settings(season)
    labels = {band["label"] for band in settings.qf_bands}
    band = (values.get("qf_band") or "").strip() or None
    if band is not None and band not in labels:
        raise FeeError("Tranche inconnue : définissez d'abord les tranches dans les réglages de la saison.")
    quotient = values.get("quotient")
    if quotient is not None and (isinstance(quotient, bool) or not isinstance(quotient, int) or quotient < 0):
        raise FeeError("Le quotient familial doit être un nombre entier positif.")
    resident = values.get("resident")
    if resident not in (None, True, False):
        raise FeeError("Résident : oui, non ou selon la commune.")
    tags = _clean_tags(values.get("tags"))
    record = db.session.get(HouseholdSeason, (household.id, season.id))
    if record is None:
        record = HouseholdSeason(household=household, season=season, tags=[])
        db.session.add(record)
    new = {
        "qf_band": band,
        "quotient": quotient,
        "qf_proof": (values.get("qf_proof") or "").strip() or None,
        "resident": resident,
        "tags": tags,
    }
    changes = {}
    for name in SITUATION_FIELDS:
        if getattr(record, name) != new[name]:
            # Le quotient ne sort pas de l'écran réservé : le journal note seulement qu'il a changé.
            changes[name] = ["(masqué)", "(masqué)"] if name == "quotient" else [getattr(record, name), new[name]]
            setattr(record, name, new[name])
    if changes:
        log(
            "fee.situation",
            f"Situation du foyer {household.name} modifiée ({season.label})",
            changes=changes,
            household=household,
        )
    db.session.flush()
    return record


def set_student_tags(membership, tags):
    tags = _clean_tags(tags)
    if membership.fee_tags != tags:
        log(
            "fee.situation",
            f"Étiquettes de {membership.person.full_name} modifiées ({membership.season.label})",
            changes={"fee_tags": [membership.fee_tags, tags]},
            person=membership.person,
        )
        membership.fee_tags = tags
        db.session.flush()


def _clean_tags(tags):
    tags = sorted({t.strip() for t in (tags or []) if isinstance(t, str) and t.strip()}, key=fold)
    if any(len(t) > 40 for t in tags):
        raise FeeError("Une étiquette fait 40 caractères au plus.")
    return tags


def band_of(record, settings):
    """Tranche retenue : celle saisie, sinon celle du quotient, sinon la tranche par défaut."""
    if record.qf_band:
        return record.qf_band
    if record.quotient is not None and settings.qf_bands:
        for band in settings.qf_bands:
            if band["max"] is None or record.quotient < band["max"]:
                return band["label"]
    return settings.default_band


def resident_of(record, household, settings):
    """Résident : la valeur saisie, sinon la commune du foyer comparée à la liste de la saison (vide : inconnu)."""
    if record.resident is not None:
        return record.resident
    if not settings.resident_cities or not household.city:
        return None
    return fold(household.city) in {fold(city) for city in settings.resident_cities}


# ---------------------------------------------------------------------------
# Ce que le foyer prend
# ---------------------------------------------------------------------------


@dataclass
class Prepared:
    """Ce que le moteur reçoit pour un foyer, et ce qu'il faut savoir pour le relire."""

    order: pricing.Order
    items: dict
    persons: dict  # id → Person
    warnings: list = field(default_factory=list)


def season_households(season):
    """Foyers dont au moins un membre a une adhésion active dans la saison."""
    query = (
        db.select(Household)
        .join(HouseholdMember)
        .join(Membership, Membership.person_id == HouseholdMember.person_id)
        .where(Membership.season_id == season.id, Membership.status == Membership.ACTIVE)
        .distinct()
        .order_by(Household.name, Household.id)
    )
    return list(db.session.scalars(query))


def prepare(household, season):
    settings = season_settings(season)
    record = situation(household, season)
    known = {item.id: item for item in items(season)}
    engine_items = {item.id: _engine_item(item) for item in known.values()}
    warnings = []
    students, picks, persons = [], [], {}
    for member in household.members:
        person = member.person
        membership = next(
            (m for m in person.memberships if m.season_id == season.id and m.status == Membership.ACTIVE), None
        )
        if membership is None:
            continue
        persons[person.id] = person
        if len(person.household_links) > 1:
            warnings.append(
                f"{person.full_name} fait partie de plusieurs foyers : sa cotisation est comptée dans chacun."
            )
        age = person.age(season.starts_on)
        enrollments = sorted((e for e in membership.enrollments if e.status == Enrollment.ACTIVE), key=lambda e: e.id)
        seniority = sum(
            1 for m in person.memberships if m.status == Membership.ACTIVE and m.season.starts_on < season.starts_on
        )
        students.append(
            pricing.Student(
                id=person.id,
                name=person.full_name,
                age=age,
                adult=age >= 18 if age is not None else member.role == HouseholdMember.GUARDIAN,
                tags=frozenset(membership.fee_tags or ()),
                disciplines=frozenset(e.course.discipline.name for e in enrollments),
                seniority=seniority,
            )
        )
        for enrollment in enrollments:
            course = enrollment.course
            if course.fee_item_id is None:
                warnings.append(f"{person.full_name} : le cours « {course.title} » ne facture aucun élément.")
            elif course.fee_item_id not in known:
                warnings.append(
                    f"{person.full_name} : le cours « {course.title} » facture un élément d'une autre saison."
                )
            else:
                picks.append((person.id, course.fee_item_id))
    picked = db.session.scalars(
        db.select(FeePick)
        .join(FeeItem)
        .where(FeePick.household_id == household.id, FeeItem.season_id == season.id)
        .order_by(FeePick.id)
    )
    for pick in picked:
        if pick.person_id not in persons:
            warnings.append(
                f"« {pick.item.label} » est pris pour {pick.person.full_name}, qui n'est pas élève cette "
                "saison : il n'est pas compté."
            )
            continue
        picks.append((pick.person_id, pick.item_id))
    band = band_of(record, settings)
    if settings.qf_bands and band is None:
        warnings.append("Tranche de quotient familial inconnue, et pas de tranche par défaut.")
    order = pricing.Order(
        household=pricing.Household(
            qf_band=band, resident=resident_of(record, household, settings), tags=frozenset(record.tags or ())
        ),
        students=tuple(students),
        picks=tuple(picks),
    )
    return Prepared(order, engine_items, persons, warnings)


def _engine_item(item):
    return pricing.Item(item.id, item.label, item.price, item.category, item.regie.name if item.regie else None)


def add_pick(household, person, item, note=None):
    if not any(link.household_id == household.id for link in person.household_links):
        raise FeeError(f"{person.full_name} ne fait pas partie de ce foyer.")
    pick = FeePick(household=household, person=person, item=item, note=(note or "").strip() or None)
    db.session.add(pick)
    log(
        "fee.pick",
        f"« {item.label} » ajouté pour {person.full_name} ({item.season.label})",
        household=household,
        person=person,
    )
    db.session.flush()
    return pick


def remove_pick(pick):
    log(
        "fee.pick",
        f"« {pick.item.label} » retiré pour {pick.person.full_name} ({pick.item.season.label})",
        household=pick.household,
        person=pick.person,
    )
    db.session.delete(pick)
    db.session.flush()


# ---------------------------------------------------------------------------
# Calcul
# ---------------------------------------------------------------------------


def engine_rules(season, rules=None):
    """Règles actives de la saison, dans l'ordre, telles que le moteur les lit."""
    rules = season_rules(season, active_only=True) if rules is None else rules
    return [rule.as_dict() for rule in rules]


def compute(household, season, rules=None):
    """Calcule sans rien enregistrer. Renvoie (Prepared, Result)."""
    prepared = prepare(household, season)
    result = pricing.compute(
        prepared.order, prepared.items, engine_rules(season, rules), season_settings(season).engine
    )
    return prepared, result


def get_assessment(household, season):
    return db.session.scalar(db.select(Assessment).filter_by(household_id=household.id, season_id=season.id))


def assess(household, season, discard_corrections=False):
    """Calcule la cotisation du foyer et l'enregistre comme proposée. Une cotisation validée ne bouge pas."""
    assessment = get_assessment(household, season)
    if assessment is not None:
        if assessment.status == Assessment.VALIDATED:
            raise FeeError(f"La cotisation du foyer {household.name} est validée : rouvrez-la pour la recalculer.")
        if not discard_corrections and any(line.corrected for line in assessment.lines):
            raise FeeError(f"La cotisation du foyer {household.name} a des corrections : le recalcul les effacerait.")
    prepared, result = compute(household, season)
    if assessment is None:
        assessment = Assessment(household=household, season=season)
        db.session.add(assessment)
    assessment.lines = _lines(prepared, result)
    assessment.computed_total = result.total
    assessment.computed_at = datetime.now()
    assessment.warnings = prepared.warnings
    assessment.review_reasons = []
    db.session.flush()
    return assessment


def _lines(prepared, result):
    lines = []
    for line in result.lines:
        person = prepared.persons.get(line.student_id)
        lines.append(
            AssessmentLine(
                position=len(lines),
                kind=AssessmentLine.ITEM,
                person_id=line.student_id,
                person_name=person.full_name if person else None,
                item_id=line.item.id,
                label=line.item.label,
                regie_name=line.item.regie,
                base=line.base,
                computed=line.amount,
                amount=line.amount,
                steps=[{"rule": s.rule, "before": s.before, "after": s.after, "note": s.note} for s in line.steps],
            )
        )
    for step in result.adjustments:
        lines.append(
            AssessmentLine(
                position=len(lines),
                kind=AssessmentLine.ADJUSTMENT,
                label=step.rule,
                computed=step.after - step.before,
                amount=step.after - step.before,
            )
        )
    if result.rounding:
        lines.append(
            AssessmentLine(
                position=len(lines),
                kind=AssessmentLine.ROUNDING,
                label="Arrondi",
                computed=result.rounding,
                amount=result.rounding,
            )
        )
    return lines


def assess_season(season):
    """Calcule toutes les cotisations de la saison qui peuvent l'être. Renvoie (calculées, laissées, motifs)."""
    done, skipped, reasons = 0, 0, []
    for household in season_households(season):
        try:
            assess(household, season)
            done += 1
        except FeeError as error:
            skipped += 1
            reasons.append(str(error))
    if done:
        log("fee.assess", f"{done} cotisation(s) calculée(s) pour {season.label}")
    return done, skipped, reasons


def correct_line(line, amount, reason, user=None):
    """Corrige le montant d'une ligne. La correction est tracée : qui, quand, pourquoi."""
    assessment = line.assessment
    if assessment.status != Assessment.PROPOSED:
        raise FeeError("Une cotisation validée ne se corrige pas : rouvrez-la d'abord.")
    reason = (reason or "").strip()
    if not reason:
        raise FeeError("Indiquez le motif de la correction.")
    if isinstance(amount, bool) or not isinstance(amount, int) or abs(amount) > MAX_PRICE:
        raise FeeError("Montant illisible.")
    if line.kind == AssessmentLine.ITEM and amount < 0:
        raise FeeError("Le montant d'un élément ne peut pas être négatif.")
    old = line.amount
    line.amount = amount
    line.correction_reason = reason[:255]
    line.corrected_at = datetime.now()
    line.corrected_by = user
    log(
        "fee.correct",
        f"Cotisation {assessment.season.label} du foyer {assessment.household.name} : « {line.label} »"
        f" corrigée ({reason[:200]})",
        changes={"amount": [old, amount]},
        user=user,
        household=assessment.household,
    )
    db.session.flush()


def undo_correction(line, user=None):
    if line.assessment.status != Assessment.PROPOSED:
        raise FeeError("Une cotisation validée ne se corrige pas : rouvrez-la d'abord.")
    if not line.corrected:
        return
    log(
        "fee.correct",
        f"Correction de « {line.label} » annulée ({line.assessment.season.label})",
        changes={"amount": [line.amount, line.computed]},
        user=user,
        household=line.assessment.household,
    )
    line.amount = line.computed
    line.correction_reason = line.corrected_at = line.corrected_by = None
    db.session.flush()


def validate(assessment, user=None):
    if assessment.status == Assessment.VALIDATED:
        raise FeeError("Cette cotisation est déjà validée.")
    assessment.status = Assessment.VALIDATED
    assessment.validated_at = datetime.now()
    assessment.validated_by = user
    log(
        "fee.validate",
        f"Cotisation {assessment.season.label} du foyer {assessment.household.name} validée : "
        f"{assessment.total / 100:.2f} €",
        user=user,
        household=assessment.household,
    )
    db.session.flush()


def reopen(assessment, reason, user=None):
    if assessment.status != Assessment.VALIDATED:
        raise FeeError("Cette cotisation n'est pas validée.")
    reason = (reason or "").strip()
    if not reason:
        raise FeeError("Indiquez pourquoi la cotisation est rouverte.")
    assessment.status = Assessment.PROPOSED
    assessment.validated_at = assessment.validated_by = None
    log(
        "fee.reopen",
        f"Cotisation {assessment.season.label} du foyer {assessment.household.name} rouverte ({reason[:200]})",
        user=user,
        household=assessment.household,
    )
    db.session.flush()


def acknowledge_review(assessment, user=None):
    """Le gestionnaire a vu les changements d'inscription et il n'y a rien à changer : le signal « à revoir » tombe."""
    if not assessment.review_reasons:
        raise FeeError("Cette cotisation n'est pas à revoir.")
    log(
        "fee.review",
        f"Cotisation {assessment.season.label} du foyer {assessment.household.name} revue sans changement "
        f"({len(assessment.review_reasons)} changement(s) d'inscription)",
        user=user,
        household=assessment.household,
    )
    assessment.review_reasons = []
    db.session.flush()


def delete_assessment(assessment, user=None):
    """Supprime une cotisation proposée (foyer en double, calcul lancé par erreur). Une validée reste."""
    if assessment.status == Assessment.VALIDATED:
        raise FeeError("Une cotisation validée ne se supprime pas : rouvrez-la d'abord.")
    log(
        "fee.assess",
        f"Cotisation {assessment.season.label} du foyer {assessment.household.name} supprimée",
        user=user,
        household=assessment.household,
    )
    db.session.delete(assessment)
    db.session.flush()


def drift(assessment):
    """Total que donnerait un nouveau calcul, s'il diffère de celui enregistré ; sinon None. Ne modifie rien."""
    _, result = compute(assessment.household, assessment.season)
    return result.total if result.total != assessment.computed_total else None


# ---------------------------------------------------------------------------
# Effet d'une règle sur la saison
# ---------------------------------------------------------------------------


@dataclass
class Impact:
    household: Household
    before: int
    after: int

    @property
    def delta(self):
        return self.after - self.before


def rule_impact(rule):
    """Ce que changerait l'activation d'une règle en brouillon (ou la désactivation d'une règle active), foyer par
    foyer. Rien n'est enregistré."""
    season = rule.season
    active = season_rules(season, active_only=True)
    with_rule = sorted([r for r in active if r is not rule] + [rule], key=lambda r: (r.position, r.id))
    without_rule = [r for r in active if r is not rule]
    before_rules, after_rules = (with_rule, without_rule) if rule.active else (without_rule, with_rule)
    impacts = []
    for household in season_households(season):
        prepared = prepare(household, season)
        settings = season_settings(season).engine
        before = pricing.compute(prepared.order, prepared.items, engine_rules(season, before_rules), settings).total
        after = pricing.compute(prepared.order, prepared.items, engine_rules(season, after_rules), settings).total
        if before != after:
            impacts.append(Impact(household, before, after))
    return impacts


def simulate(season, order, include_drafts=()):
    """Calcul d'un foyer fictif avec les éléments et règles de la saison (et des règles en brouillon choisies)."""
    chosen = {rule.id for rule in include_drafts}
    rules = [rule for rule in season_rules(season) if rule.active or rule.id in chosen]
    engine_items = {item.id: _engine_item(item) for item in items(season)}
    return pricing.compute(order, engine_items, engine_rules(season, rules), season_settings(season).engine)


# ---------------------------------------------------------------------------
# Copie d'une saison à l'autre
# ---------------------------------------------------------------------------


def copy_season(source, target):
    """Recopie éléments, règles et réglages de `source` sur `target`, qui n'en a pas encore.

    Les cours de `target` qui ne facturent rien reprennent l'élément du cours équivalent de `source` (même discipline,
    même niveau, même groupe). Renvoie un résumé.
    """
    if source.id == target.id:
        raise FeeError("Choisissez deux saisons différentes.")
    if items(target) or season_rules(target):
        raise FeeError(f"La saison {target.label} a déjà des tarifs : la copie ne se fait que sur une saison vide.")
    mapping = {}
    for old in items(source):
        new = FeeItem(
            season=target,
            label=old.label,
            code=old.code,
            category=old.category,
            regie=old.regie,
            price=old.price,
            position=old.position,
            notes=old.notes,
        )
        db.session.add(new)
        db.session.flush()
        mapping[old.id] = new.id
    for old in season_rules(source):
        copied = pricing.remap_items(old.as_dict(), mapping)
        db.session.add(
            FeeRule(
                season=target,
                position=old.position,
                name=old.name,
                active=old.active,
                conditions=copied["conditions"],
                target=copied["target"],
                effect=copied["effect"],
                exclusive_group=old.exclusive_group,
                notes=old.notes,
            )
        )
    old_settings = season_settings(source)
    settings = season_settings(target)
    settings.engine = dict(old_settings.engine)
    settings.qf_bands = list(old_settings.qf_bands)
    settings.default_band = old_settings.default_band
    settings.resident_cities = list(old_settings.resident_cities)
    linked = 0
    previous = {}
    for course in db.session.scalars(
        db.select(Course).where(Course.season_id == source.id, Course.fee_item_id.is_not(None)).order_by(Course.id)
    ):
        previous.setdefault((course.discipline_id, course.level_id, course.group_label), course.fee_item_id)
    for course in db.session.scalars(
        db.select(Course).where(Course.season_id == target.id, Course.fee_item_id.is_(None))
    ):
        old_item = previous.get((course.discipline_id, course.level_id, course.group_label))
        if old_item is not None:
            course.fee_item_id = mapping[old_item]
            linked += 1
    db.session.flush()
    summary = f"{len(mapping)} élément(s), {len(season_rules(target))} règle(s) recopiés ; {linked} cours reliés"
    log("fee.copy", f"Tarifs de {source.label} recopiés sur {target.label} : {summary}")
    return summary


def set_course_item(course, item):
    if item is not None and item.season_id != course.season_id:
        raise FeeError("L'élément doit être de la même saison que le cours.")
    if course.fee_item_id != (item.id if item else None):
        log(
            "fee.course",
            f"Cours « {course.title} » ({course.season.label}) : élément facturé {item.label if item else 'aucun'}",
        )
        course.fee_item = item
        db.session.flush()


def seasons_with_fees():
    return list(db.session.scalars(db.select(Season).join(FeeItem).distinct().order_by(Season.starts_on.desc())))


# ---------------------------------------------------------------------------
# Règles lisibles par un humain
# ---------------------------------------------------------------------------


def euros(cents):
    """12345 → « 123,45 € » ; les centimes nuls sont gardés pour aligner les colonnes."""
    sign = "−" if cents < 0 else ""
    whole, rest = divmod(abs(cents), 100)
    return f"{sign}{whole:,}".replace(",", "\u202f") + f",{rest:02d} €"


def parse_euros(text):
    """« 12,5 », « 12.50 € », « -3 » → centimes ; None si vide ; FeeError si illisible."""
    text = (text or "").strip().replace("€", "").replace("\u202f", "").replace(" ", "").replace("−", "-")
    if not text:
        return None
    text = text.replace(",", ".")
    try:
        from decimal import Decimal, InvalidOperation

        value = Decimal(text)
    except InvalidOperation:
        raise FeeError(f"Montant illisible : « {text} ».") from None
    if not value.is_finite() or value != value.quantize(Decimal("0.01")):
        raise FeeError(f"Montant illisible : « {text} » (deux décimales au plus).")
    cents = int(value * 100)
    if abs(cents) > MAX_PRICE:
        raise FeeError("Montant trop grand.")
    return cents


def describe_effect(effect, labels):
    kind = effect["kind"]
    if kind in ("percent", "total_percent"):
        value = effect["value"].replace(".", ",")
        text = f"{'+' if not value.startswith('-') else ''}{value.replace('-', '−')} %"
        if kind == "total_percent":
            return text + " sur le total du foyer"
        return text + (" du prix de base" if effect.get("base") == "base" else "")
    if kind == "amount":
        return ("+" if effect["value"] >= 0 else "") + euros(effect["value"])
    if kind == "total_amount":
        return ("+" if effect["value"] >= 0 else "") + euros(effect["value"]) + " sur le total du foyer"
    if kind == "fixed":
        return f"prix fixe {euros(effect['value'])}"
    if kind == "free":
        return "offert"
    if kind == "replace":
        return f"remplacé par « {labels.get(effect['item'], '?')} »"
    per = "pour chaque élève" if effect["per"] == "student" else "une fois pour le foyer"
    return f"ajoute « {labels.get(effect['item'], '?')} » {per}"


def describe_target(target, labels):
    parts = []
    if target.get("items") or target.get("categories"):
        parts.append(
            ", ".join(
                [f"« {labels.get(i, '?')} »" for i in target.get("items", [])]
                + [f"catégorie {c}" for c in target.get("categories", [])]
            )
        )
    else:
        parts.append("tous les éléments")
    excluded = [f"« {labels.get(i, '?')} »" for i in target.get("exclude_items", [])] + [
        f"catégorie {c}" for c in target.get("exclude_categories", [])
    ]
    if excluded:
        parts.append("sauf " + ", ".join(excluded))
    return " ".join(parts)


def describe_condition(condition, labels):
    label, _, kind, _ = pricing.FIELDS[condition["field"]]
    op = pricing.OPERATORS[kind][condition["op"]]
    value = condition["value"]
    if condition["field"] == "ligne.element":
        value = ", ".join(labels.get(v, "?") for v in value)
    elif isinstance(value, bool):
        value = "oui" if value else "non"
    elif condition["op"] == "between":
        value = f"{value[0]} et {value[1]}"
    elif isinstance(value, list):
        value = ", ".join(value)
    param = condition.get("param")
    if param:
        label += f" ({labels.get(param['item'], '?')})" if "item" in param else f" ({param['category']})"
    return f"{label} {op} {value}"


def describe_conditions(groups, labels):
    """Liste de phrases : une par groupe (conditions reliées par ET), les groupes étant reliés par OU."""
    return [" et ".join(describe_condition(c, labels) for c in group) for group in groups]
