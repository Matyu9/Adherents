"""Inscriptions : campagnes et dossiers des familles. Voir docs/inscriptions.md. Les services ne font pas de commit.

Ce que la famille saisit reste dans `Dossier.data` jusqu'à la validation : l'annuaire n'est modifié que par
`validate()`, à la main d'un gestionnaire. Forme de `data` :

    {"household": {"name", "address_recipient", "address_street", "postal_code", "city", "country", "phone"},
     "guardians": [{"person_id", "civility", "first_name", "last_name", "email", "phone", "relationship"}],
     "students": [{"key", "person_id", "guardian", "enrolled", "first_name", "last_name", "birth_date", "sex",
                   "category", "school_level", "school_class", "courses": [id…], "wishes",
                   "consents": {"<id>": bool}}],
     "consents": {"<id>": bool},
     "bank": {"holder_name", "bic"},
     "answered_at", "answered_by", "comment"}

`guardian` (entier ou None) : l'élève est ce responsable lui-même (un adulte qui prend des cours).
"""
import hashlib
import secrets
import uuid
from datetime import date, datetime, timedelta

from ..extensions import db, fold
from ..models import (BankAccount, Campaign, CampaignConsent, Consent, Course, Dossier, Enrollment, Household,
                      HouseholdMember, Membership, Person, Season)
from . import bank, directory
from .directory import DirectoryError

ACCESS_TTL = timedelta(days=30)  # lien d'accès d'une nouvelle famille
HOUSEHOLD_FIELDS = ("name", "address_recipient", "address_street", "postal_code", "city", "country")
GUARDIAN_FIELDS = ("civility", "first_name", "last_name", "email")
STUDENT_FIELDS = ("first_name", "last_name", "birth_date", "sex")
MEMBERSHIP_FIELDS = ("category", "school_level", "school_class")
LABELS = {"name": "Nom du foyer", "address_recipient": "Destinataire", "address_street": "Adresse",
          "postal_code": "Code postal", "city": "Ville", "country": "Pays", "phone": "Téléphone",
          "civility": "Civilité", "first_name": "Prénom", "last_name": "Nom", "email": "Email",
          "birth_date": "Naissance", "sex": "Sexe", "category": "Catégorie", "school_level": "Niveau scolaire",
          "school_class": "Classe", "relationship": "Lien"}


class RegistrationError(ValueError):
    """Opération refusée, avec un message à afficher tel quel."""


# ---------------------------------------------------------------------------
# Campagnes
# ---------------------------------------------------------------------------

def open_campaigns(now=None):
    now = now or datetime.now()
    return db.session.scalars(db.select(Campaign).where(Campaign.opens_at <= now, Campaign.closes_at > now)
                              .order_by(Campaign.closes_at)).all()


def previous_season(season):
    return db.session.scalar(db.select(Season).where(Season.starts_on < season.starts_on)
                             .order_by(Season.starts_on.desc()).limit(1))


def enrolled_count(course):
    return db.session.scalar(db.select(db.func.count(Enrollment.id)).where(
        Enrollment.course_id == course.id, Enrollment.status == Enrollment.ACTIVE))


def enrolled_counts(season):
    return dict(db.session.execute(
        db.select(Enrollment.course_id, db.func.count(Enrollment.id)).join(Course)
        .where(Course.season_id == season.id, Enrollment.status == Enrollment.ACTIVE)
        .group_by(Enrollment.course_id)).all())


def offered_courses(campaign):
    """Cours proposés aux familles : ceux de la saison de la campagne, avec leur nombre d'inscrits."""
    from .catalog import sorted_courses
    courses = db.session.scalars(db.select(Course).where(Course.season_id == campaign.season_id)).all()
    return sorted_courses(courses), enrolled_counts(campaign.season)


# ---------------------------------------------------------------------------
# Qui accède à quel dossier
# ---------------------------------------------------------------------------

def guarded_households(account):
    """Foyers dont le compte est responsable (par sa fiche de l'annuaire)."""
    person = getattr(account, "person", None)
    if person is None:
        return []
    return [link.household for link in person.household_links
            if link.role == HouseholdMember.GUARDIAN and link.household.archived_at is None]


def can_open(account, dossier, session_access=()):
    """Un responsable du foyer, la personne qui a créé le dossier, ou une nouvelle famille avec son lien."""
    if dossier.uuid in session_access and dossier.access_expires_at and dossier.access_expires_at > datetime.now():
        return True
    if account is None or not account.is_authenticated:
        return False
    if dossier.account_id == account.id:
        return True
    return dossier.household is not None and dossier.household in guarded_households(account)


def dossier_for(campaign, household, account):
    """Dossier du foyer pour la campagne, créé et pré-rempli depuis l'annuaire au premier accès."""
    dossier = db.session.scalar(db.select(Dossier).filter_by(campaign_id=campaign.id, household_id=household.id))
    if dossier is None:
        dossier = Dossier(campaign=campaign, household=household, account=account, data=prefill(household, campaign))
        db.session.add(dossier)
        db.session.flush()
    return dossier


# ---------------------------------------------------------------------------
# Nouvelles familles : lien d'accès par email
# ---------------------------------------------------------------------------

def _digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def new_family_dossier(campaign, email):
    """Dossier vide pour une famille inconnue. Renvoie (dossier, jeton en clair à envoyer par email)."""
    if not campaign.allow_new:
        raise RegistrationError("Cette campagne n'accueille pas de nouvelles familles en ligne.")
    dossier = Dossier(campaign=campaign, email=email.strip().lower(), data=blank_data())
    db.session.add(dossier)
    db.session.flush()
    return dossier, renew_access(dossier)


def renew_access(dossier):
    raw = secrets.token_urlsafe(32)
    dossier.access_digest = _digest(raw)
    dossier.access_expires_at = datetime.now() + ACCESS_TTL
    return raw


def access_url(raw):
    from flask import current_app, url_for
    return current_app.config["BASE_URL"] + url_for("enrollment.access", token=raw)


def find_by_access(raw):
    if not raw or len(raw) > 100:
        return None
    dossier = db.session.scalar(db.select(Dossier).filter_by(access_digest=_digest(raw)))
    if dossier is None or dossier.access_expires_at is None or dossier.access_expires_at < datetime.now():
        return None
    return dossier


def pending_new_dossier(campaign, email):
    """Dossier déjà ouvert avec cette adresse pour la campagne (même envoyé, même validé) : on renvoie un lien vers
    lui plutôt que d'en créer un second."""
    return db.session.scalar(db.select(Dossier).where(
        Dossier.campaign_id == campaign.id, Dossier.email == email.strip().lower()).order_by(Dossier.id.desc()).limit(1))


# ---------------------------------------------------------------------------
# Pré-remplissage
# ---------------------------------------------------------------------------

def blank_data():
    return {"household": {"name": "", "country": "FR"}, "guardians": [], "students": [], "consents": {},
            "bank": {}}


def _iso(value):
    return value.isoformat() if isinstance(value, date) else value


def _phone(owner):
    return owner.phones[0].number if owner.phones else None


def prefill(household, campaign):
    """Dossier pré-rempli : le foyer, ses responsables, et ses élèves avec les cours équivalents à ceux de l'an dernier."""
    previous = previous_season(campaign.season)
    data = blank_data()
    data["household"] = {f: getattr(household, f) for f in HOUSEHOLD_FIELDS}
    data["household"]["phone"] = _phone(household)
    guardians = sorted(household.guardians, key=lambda m: m.guardian_rank)
    for link in guardians:
        p = link.person
        data["guardians"].append({"person_id": p.id, **{f: getattr(p, f) for f in GUARDIAN_FIELDS},
                                  "phone": _phone(p), "relationship": link.relationship})
    candidates = [(link.person, None) for link in household.dependents]
    candidates += [(link.person, index) for index, link in enumerate(guardians) if link.person.memberships]
    for person, guardian in candidates:
        if person.archived_at is not None:
            continue
        last = next((m for m in person.memberships if previous and m.season_id == previous.id), None)
        current = next((m for m in person.memberships if m.season_id == campaign.season_id), None)
        source = current or last
        data["students"].append({
            "key": uuid.uuid4().hex[:8], "person_id": person.id, "guardian": guardian,
            "enrolled": bool(current or (last and last.status == Membership.ACTIVE)),
            **{f: _iso(getattr(person, f)) for f in STUDENT_FIELDS},
            **{f: getattr(source, f) if source else None for f in MEMBERSHIP_FIELDS},
            "courses": _same_courses(last, campaign) if last and not current else
            [e.course_id for e in (current.enrollments if current else []) if e.status == Enrollment.ACTIVE],
            "wishes": None, "consents": {}})
    return data


def _same_courses(membership, campaign):
    """Cours de la nouvelle saison qui prolongent ceux de l'an dernier : même discipline, même niveau, même groupe
    si possible. La famille ou le secrétariat ajustent ensuite (passage au niveau supérieur…)."""
    offered = db.session.scalars(db.select(Course).where(Course.season_id == campaign.season_id)).all()
    chosen = []
    for enrollment in membership.enrollments:
        if enrollment.status != Enrollment.ACTIVE:
            continue
        old = enrollment.course
        same = [c for c in offered if c.discipline_id == old.discipline_id and c.level_id == old.level_id]
        match = next((c for c in same if c.group_label == old.group_label), same[0] if same else None)
        if match is not None and match.id not in chosen:
            chosen.append(match.id)
    return chosen


# ---------------------------------------------------------------------------
# Contrôles et envoi
# ---------------------------------------------------------------------------

def enrolled_students(dossier):
    return [s for s in dossier.data.get("students", []) if s.get("enrolled")]


def student_name(student, data):
    if student.get("guardian") is not None and student["guardian"] < len(data["guardians"]):
        g = data["guardians"][student["guardian"]]
        return f"{g.get('first_name') or ''} {g.get('last_name') or ''}".strip()
    return f"{student.get('first_name') or ''} {student.get('last_name') or ''}".strip()


def problems(dossier):
    """Ce qui manque avant l'envoi, dans des phrases lisibles par la famille."""
    data, found = dossier.data, []
    household = data.get("household", {})
    if not household.get("address_street") or not household.get("postal_code") or not household.get("city"):
        found.append("L'adresse du foyer est incomplète.")
    if not data.get("guardians"):
        found.append("Indiquez au moins un responsable.")
    for index, g in enumerate(data.get("guardians", []), start=1):
        if not g.get("first_name") or not g.get("last_name"):
            found.append(f"Le nom du responsable {index} manque.")
    if not any(g.get("email") for g in data.get("guardians", [])):
        found.append("Indiquez l'adresse email d'au moins un responsable.")
    if not any(g.get("phone") for g in data.get("guardians", [])) and not household.get("phone"):
        found.append("Indiquez au moins un numéro de téléphone.")
    students = enrolled_students(dossier)
    if not students:
        found.append("Inscrivez au moins un élève.")
    offered = {c.id: c for c in db.session.scalars(
        db.select(Course).where(Course.season_id == dossier.campaign.season_id))}
    for s in students:
        name = student_name(s, data) or "un élève"
        if s.get("guardian") is None and (not s.get("first_name") or not s.get("last_name") or not s.get("birth_date")):
            found.append(f"Le nom ou la date de naissance de {name} manque.")
        if not s.get("courses"):
            found.append(f"Choisissez au moins un cours pour {name}.")
        if any(c not in offered for c in s.get("courses", [])):
            found.append(f"Un cours choisi pour {name} n'est plus proposé : choisissez-en un autre.")
    for consent in dossier.campaign.consents:
        if not consent.required:
            continue
        if consent.scope == CampaignConsent.HOUSEHOLD and not data.get("consents", {}).get(str(consent.id)):
            found.append(f"« {consent.label} » doit être accepté.")
        if consent.scope == CampaignConsent.STUDENT:
            for s in students:
                if not s.get("consents", {}).get(str(consent.id)):
                    found.append(f"« {consent.label} » doit être accepté pour {student_name(s, data)}.")
    if dossier.iban_ciphertext is not None and not data.get("bank", {}).get("holder_name"):
        found.append("Indiquez le titulaire du compte bancaire.")
    return found


def submit(dossier, account=None, now=None):
    if not dossier.editable:
        raise RegistrationError("Ce dossier a déjà été envoyé.")
    if not dossier.campaign.is_open(now):
        raise RegistrationError("La campagne est fermée : le dossier ne peut plus être envoyé.")
    if found := problems(dossier):
        raise RegistrationError(" ".join(found))
    dossier.status = Dossier.SUBMITTED
    dossier.submitted_at = now or datetime.now()
    if account is not None and account.is_authenticated:
        dossier.account = dossier.account or account


def save(dossier, data):
    """Enregistre la saisie de la famille (réaffecte le JSON pour que SQLAlchemy voie le changement)."""
    if not dossier.editable:
        raise RegistrationError("Ce dossier a déjà été envoyé : il ne se modifie plus.")
    dossier.data = data


def return_for_fix(dossier, note):
    if dossier.status != Dossier.SUBMITTED:
        raise RegistrationError("Seul un dossier envoyé peut être renvoyé à la famille.")
    if not (note or "").strip():
        raise RegistrationError("Expliquez à la famille ce qu'elle doit corriger.")
    dossier.status = Dossier.TO_FIX
    dossier.review_note = note.strip()


def mark_transferred(dossier, now=None):
    if dossier.status != Dossier.VALIDATED:
        raise RegistrationError("Seul un dossier validé se reporte dans le logiciel de gestion.")
    dossier.status = Dossier.TRANSFERRED
    dossier.transferred_at = now or datetime.now()


def recipients(dossier):
    """Comptes à prévenir d'un changement de statut : les responsables du foyer qui en ont un, et l'auteur."""
    accounts = [dossier.account] if dossier.account else []
    if dossier.household is not None:
        accounts += [link.person.account for link in dossier.household.guardians if link.person.account]
    return [a for a in {a.id: a for a in accounts}.values() if a.active]


# ---------------------------------------------------------------------------
# Différences avec l'annuaire, pour la revue et le report
# ---------------------------------------------------------------------------

def _shown(value, field=None):
    if value in (None, ""):
        return None
    if field == "phone":
        return directory.display_phone(value)
    return value.isoformat() if isinstance(value, date) else str(value)


def changes(dossier):
    """[(fiche, champ, ancienne valeur, nouvelle valeur)] : ce que la validation changera dans l'annuaire."""
    data, found = dossier.data, []
    household = dossier.household
    title = data["household"].get("name") or "Foyer"
    for f in (*HOUSEHOLD_FIELDS, "phone"):
        new = _shown(data["household"].get(f), f)
        old = _shown(_phone(household) if f == "phone" else getattr(household, f), f) if household else None
        if new != old and (new or old):
            found.append((title, LABELS[f], old, new))
    for index, g in enumerate(data.get("guardians", []), start=1):
        person = db.session.get(Person, g["person_id"]) if g.get("person_id") else None
        name = f"Responsable {index} : {g.get('first_name') or ''} {g.get('last_name') or ''}".strip()
        if person is None:
            found.append((name, "Fiche", None, "nouvelle"))
        for f in (*GUARDIAN_FIELDS, "phone"):
            new = _shown(g.get(f), f)
            old = _shown(None if person is None else _phone(person) if f == "phone" else getattr(person, f), f)
            if new != old and (new or old) and (person is not None or new):
                found.append((name, LABELS[f], old, new))
    for s in enrolled_students(dossier):
        person = db.session.get(Person, s["person_id"]) if s.get("person_id") else None
        name = f"Élève : {student_name(s, data)}"
        if s.get("guardian") is None:
            if person is None:
                found.append((name, "Fiche", None, "nouvelle"))
            for f in STUDENT_FIELDS:
                new, old = _shown(s.get(f)), _shown(getattr(person, f) if person else None)
                if new != old and (new or old) and (person is not None or new):
                    found.append((name, LABELS[f], old, new))
        courses = [db.session.get(Course, c) for c in s.get("courses", [])]
        found.append((name, "Cours", None, ", ".join(c.title for c in courses if c)))
    if dossier.iban_ciphertext is not None:
        found.append((title, "IBAN", None, f"•••• {dossier.iban_last4} ({data.get('bank', {}).get('holder_name')})"))
    return found


# ---------------------------------------------------------------------------
# Validation : le dossier entre dans l'annuaire
# ---------------------------------------------------------------------------

def _apply(record, values):
    for field, value in values.items():
        if field == "birth_date" and isinstance(value, str):
            value = date.fromisoformat(value) if value else None
        setattr(record, field, value if value not in ("",) else None)


def _set_phone(owner, number, label=None):
    if not number:
        return
    try:
        directory.add_phone(owner, number, label)
    except DirectoryError:
        pass  # déjà sur la fiche


def _students_without_duplicates(dossier):
    data = dossier.data
    keys = [(fold(s.get("first_name") or ""), fold(s.get("last_name") or ""), s.get("birth_date"))
            for s in enrolled_students(dossier) if s.get("guardian") is None and not s.get("person_id")]
    if len(keys) != len(set(keys)):
        raise RegistrationError("Le dossier contient deux fois le même nouvel élève.")
    return data


def validate(dossier, actor, now=None):
    """Recopie le dossier dans l'annuaire : foyer, responsables, élèves, adhésions, inscriptions, consentements, IBAN.
    Renvoie la liste des avertissements (cours devenu complet…)."""
    if dossier.status != Dossier.SUBMITTED:
        raise RegistrationError("Seul un dossier envoyé peut être validé.")
    data = _students_without_duplicates(dossier)
    season, now, warnings = dossier.campaign.season, now or datetime.now(), []
    snapshot = [list(row) for row in changes(dossier)]  # pour le report : après la validation, plus de différence

    household = dossier.household
    if household is None:
        household = Household(name=data["household"].get("name") or "Foyer")
        db.session.add(household)
        dossier.household = household
    _apply(household, {f: data["household"].get(f) for f in HOUSEHOLD_FIELDS if f in data["household"]})
    household.country = household.country or "FR"
    db.session.flush()
    _set_phone(household, data["household"].get("phone"))

    guardians = []
    for g in data.get("guardians", []):
        person = db.session.get(Person, g["person_id"]) if g.get("person_id") else None
        if person is None:
            person = Person(first_name=g["first_name"], last_name=g["last_name"])
            db.session.add(person)
        _apply(person, {f: g.get(f) for f in GUARDIAN_FIELDS})
        db.session.flush()
        if not any(link.person is person for link in household.members):
            directory.add_member(household, person, HouseholdMember.GUARDIAN, g.get("relationship"))
        _set_phone(person, g.get("phone"), "portable")
        guardians.append(person)

    counts = enrolled_counts(season)
    for s in enrolled_students(dossier):
        if s.get("guardian") is not None:
            person = guardians[s["guardian"]]
        else:
            person = db.session.get(Person, s["person_id"]) if s.get("person_id") else None
            if person is None:
                person = Person(first_name=s["first_name"], last_name=s["last_name"])
                db.session.add(person)
            _apply(person, {f: s.get(f) for f in STUDENT_FIELDS})
            db.session.flush()
            if not any(link.person is person for link in household.members):
                directory.add_member(household, person, HouseholdMember.DEPENDENT)
        membership = next((m for m in person.memberships if m.season_id == season.id), None)
        if membership is None:
            membership = Membership(person=person, season=season)
            db.session.add(membership)
        membership.status, membership.withdrawn_on = Membership.ACTIVE, None
        _apply(membership, {f: s.get(f) for f in MEMBERSHIP_FIELDS})
        db.session.flush()
        held = {e.course_id: e for e in membership.enrollments}
        for course_id in s.get("courses", []):
            course = db.session.get(Course, course_id)
            if course is None or course.season_id != season.id:
                continue
            if course_id in held:
                held[course_id].status, held[course_id].withdrawn_on = Enrollment.ACTIVE, None
                continue
            if course.is_full(counts.get(course_id, 0)):
                warnings.append(f"{course.title} était complet : {person.full_name} y est inscrit(e) quand même.")
            membership.enrollments.append(Enrollment(course=course, dossier=dossier))
            counts[course_id] = counts.get(course_id, 0) + 1
        for consent in dossier.campaign.consents:
            if consent.scope == CampaignConsent.STUDENT and str(consent.id) in s.get("consents", {}):
                _record_consent(dossier, consent, s["consents"][str(consent.id)], season, person=person)
    for consent in dossier.campaign.consents:
        if consent.scope == CampaignConsent.HOUSEHOLD and str(consent.id) in data.get("consents", {}):
            _record_consent(dossier, consent, data["consents"][str(consent.id)], season, household=household)

    if dossier.iban_ciphertext is not None:
        iban = bank.reveal_dossier_iban(dossier)
        if any(a.household_id == household.id for a in bank.same_iban(iban)):
            warnings.append("Cet IBAN était déjà enregistré pour le foyer : il n'a pas été ajouté une seconde fois.")
        else:
            holder = data.get("bank", {}).get("holder_name") or household.name
            account = BankAccount(household_id=household.id, holder_name=holder, bic=data.get("bank", {}).get("bic"),
                                  holder=next((p for p in guardians if fold(p.full_name) == fold(holder)), None))
            bank.set_iban(account, iban)
            db.session.add(account)
            if others := [a for a in bank.same_iban(iban) if a.household_id != household.id]:
                warnings.append(f"Cet IBAN est aussi celui de {len(others)} autre(s) foyer(s).")
    dossier.data = {**data, "changes": snapshot}
    dossier.status = Dossier.VALIDATED
    dossier.validated_at = now
    dossier.validated_by = actor
    db.session.flush()
    return warnings


def _record_consent(dossier, consent, granted, season, person=None, household=None):
    answered_at = dossier.data.get("answered_at")
    db.session.add(Consent(
        person=person, household=household, season=season, label=consent.label, text=consent.text,
        granted=bool(granted), given_at=datetime.fromisoformat(answered_at) if answered_at else dossier.submitted_at,
        given_by_id=dossier.account_id, given_by_name=dossier.data.get("answered_by"), dossier=dossier))


# ---------------------------------------------------------------------------
# Fichier de report vers le logiciel de gestion
# ---------------------------------------------------------------------------

EXPORT_COLUMNS = ("dossier", "foyer", "fiche", "champ", "ancienne_valeur", "nouvelle_valeur")


def export_rows(dossiers, with_iban=False):
    """Une ligne par changement à reporter. L'IBAN en clair n'y figure qu'avec `with_iban` (droit bank.view)."""
    rows = []
    for dossier in dossiers:
        name = dossier.household.name if dossier.household else dossier.data["household"].get("name")
        for record, field, old, new in changes_at_validation(dossier):
            if field == "IBAN" and with_iban:
                new = bank.group(bank.reveal_dossier_iban(dossier))
            rows.append((dossier.uuid[:8], name, record, field, old or "", new or ""))
    return rows


def changes_at_validation(dossier):
    """Les changements tels qu'ils étaient au moment de la validation (gardés dans le dossier)."""
    return [tuple(row) for row in dossier.data.get("changes", [])] or changes(dossier)


# ---------------------------------------------------------------------------
# Relances
# ---------------------------------------------------------------------------

def _week(now):
    year, week, _ = now.isocalendar()
    return f"{year}-{week:02d}"


def remind(now=None):
    """Une relance par semaine aux responsables qui ont un compte, pour chaque campagne ouverte :
    - dossier commencé mais pas envoyé, ou renvoyé « à corriger » ;
    - foyer d'un élève de la saison précédente qui n'a pas encore ouvert son dossier.
    Renvoie le nombre de foyers relancés."""
    from . import notify
    now = now or datetime.now()
    reminded = 0
    for campaign in open_campaigns(now):
        previous = previous_season(campaign.season)
        dossiers = {d.household_id: d for d in db.session.scalars(
            db.select(Dossier).where(Dossier.campaign_id == campaign.id, Dossier.household_id.is_not(None)))}
        households = set()
        if previous is not None:
            households = set(db.session.scalars(
                db.select(Household).join(HouseholdMember).join(Person).join(Membership).where(
                    Membership.season_id == previous.id, Membership.status == Membership.ACTIVE,
                    Household.archived_at.is_(None))))
        households |= {d.household for d in dossiers.values() if d.status in Dossier.EDITABLE}
        for household in households:
            dossier = dossiers.get(household.id)
            if dossier is not None and not dossier.editable:
                continue
            accounts = [link.person.account for link in household.guardians
                        if link.person.account is not None and link.person.account.active]
            if not accounts:
                continue
            if dossier is None:
                message = f"Les inscriptions « {campaign.title} » sont ouvertes jusqu'au {campaign.closes_at:%d/%m/%Y}."
            elif dossier.status == Dossier.TO_FIX:
                message = f"Votre dossier « {campaign.title} » attend vos corrections."
            else:
                message = f"Votre dossier « {campaign.title} » n'est pas encore envoyé."
            # Chemins écrits à la main : les tâches périodiques tournent sans requête, donc sans url_for.
            link = f"/inscription/dossier/{dossier.uuid}" if dossier else "/inscription"
            if notify.notify(accounts, message, link=link,
                             dedup_key=f"relance:{campaign.id}:{household.id}:{_week(now)}"):
                reminded += 1
                if dossier is not None:
                    dossier.reminded_at = now
    return reminded


# ---------------------------------------------------------------------------
# Professeurs : leurs élèves
# ---------------------------------------------------------------------------

def students_of(teacher, season):
    """[(cours, [(élève, [responsables])])] : les élèves inscrits aux cours du professeur pendant la saison."""
    from .catalog import sorted_courses
    result = []
    for course in sorted_courses(c for c in teacher.courses if c.season_id == season.id):
        students = sorted((e.membership.person for e in course.enrollments if e.status == Enrollment.ACTIVE),
                          key=lambda p: (fold(p.last_name), fold(p.first_name)))
        rows = []
        for student in students:
            guardians = [link.person for h in student.household_links for link in h.household.guardians
                         if h.role == HouseholdMember.DEPENDENT and link.person is not student]
            rows.append((student, list({p.id: p for p in guardians}.values())))
        result.append((course, rows))
    return result
