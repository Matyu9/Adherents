"""Annuaire : personnes, foyers, saisons. Les services ne font pas de commit : c'est la route qui valide."""
import re
from datetime import datetime

from ..extensions import db, fold, fold_contains
from ..models import (Account, Activity, AppSetting, BankAccount, Household, HouseholdMember, Membership, Person,
                      Phone, Role, Season, Teacher)
from . import bank

CURRENT_SEASON_KEY = "current_season"
ACCOUNT_MIN_AGE = 16  # en dessous, le responsable gère la fiche depuis son propre compte
PERSON_FIELDS = ("civility", "first_name", "last_name", "birth_name", "sex", "birth_date", "birth_place", "email",
                 "emergency_contact", "notes")
ADDRESS_FIELDS = ("address_recipient", "address_street", "postal_code", "city", "country")
# Filtres de la liste des personnes.
STATUSES = {"eleves": "Élèves de la saison", "anciens": "Anciens adhérents",
            "archives": "Archivées"}


class DirectoryError(ValueError):
    """Opération refusée, avec un message à afficher tel quel."""


# ---------------------------------------------------------------------------
# Téléphones
# ---------------------------------------------------------------------------

def normalize_phone(text):
    """Numéro au format international (+33612345678). Lève DirectoryError s'il est impossible à lire."""
    raw = re.sub(r"[\s.()/-]", "", text or "")
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    if re.fullmatch(r"0[1-9]\d{8}", raw):
        raw = "+33" + raw[1:]
    if raw.startswith("+330"):  # « +33 (0)6… »
        raw = "+33" + raw[4:]
    if raw.startswith("+33") and not re.fullmatch(r"\+33[1-9]\d{8}", raw):
        raise DirectoryError(f"Numéro français invalide : « {text} » (10 chiffres attendus).")
    if not re.fullmatch(r"\+[1-9]\d{6,14}", raw):
        raise DirectoryError(f"Numéro invalide : « {text} ». Pour l'étranger, commencez par l'indicatif (+41…).")
    return raw


def display_phone(number):
    """+33612345678 → 06 12 34 56 78 ; les numéros étrangers restent au format international."""
    if number and re.fullmatch(r"\+33\d{9}", number):
        national = "0" + number[3:]
        return " ".join(national[i:i + 2] for i in range(0, 10, 2))
    return number or ""


def add_phone(owner, number, label=None):
    number = normalize_phone(number)
    if any(phone.number == number for phone in owner.phones):
        raise DirectoryError("Ce numéro est déjà sur la fiche.")
    phone = Phone(number=number, label=(label or "").strip() or None,
                  position=max((p.position for p in owner.phones), default=-1) + 1)
    owner.phones.append(phone)
    return phone


# ---------------------------------------------------------------------------
# Saisons
# ---------------------------------------------------------------------------

def seasons():
    return db.session.scalars(db.select(Season).order_by(Season.starts_on.desc())).all()


def current_season():
    setting = db.session.get(AppSetting, CURRENT_SEASON_KEY)
    return db.session.get(Season, int(setting.value)) if setting and setting.value.isdigit() else None


def set_current_season(season):
    setting = db.session.get(AppSetting, CURRENT_SEASON_KEY)
    if setting is None:
        setting = AppSetting(key=CURRENT_SEASON_KEY, value="")
        db.session.add(setting)
    setting.value = str(season.id)


def active_in(season):
    """Condition « la personne a une adhésion active sur cette saison »."""
    return Person.memberships.any(db.and_(Membership.season_id == season.id, Membership.status == Membership.ACTIVE))


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------

def search(query, text):
    """Chaque mot cherché doit se trouver dans le nom, le prénom, le nom de naissance ou l'email, sans accents."""
    for word in (text or "").split():
        query = query.where(db.or_(*(fold_contains(column, word) for column in
                                     (Person.first_name, Person.last_name, Person.birth_name, Person.email))))
    return query


def staff_only():
    """Condition « la personne n'est là que comme professeur » : profil de professeur, sans adhésion ni foyer.
    Elle figure dans l'écran Professeurs, pas dans la liste des personnes."""
    return db.and_(Person.teacher.has(), ~Person.memberships.any(), ~Person.household_links.any())


def person_query(text=None, status=None, season=None):
    query = search(db.select(Person), text)
    if status == "archives":
        return query.where(Person.archived_at.is_not(None))
    query = query.where(Person.archived_at.is_(None), ~staff_only())
    if status == "eleves" and season is not None:
        query = query.where(active_in(season))
    elif status == "eleves":
        query = query.where(db.false())
    elif status == "anciens":
        query = query.where(Person.memberships.any())
        current = current_season()
        if current is not None:
            query = query.where(~active_in(current))
    return query


def household_query(text=None, archived=False):
    query = db.select(Household).where(Household.archived_at.is_not(None) if archived else Household.archived_at.is_(None))
    for word in (text or "").split():
        query = query.where(db.or_(fold_contains(Household.name, word), fold_contains(Household.city, word),
                                   Household.members.any(HouseholdMember.person.has(
                                       db.or_(fold_contains(Person.first_name, word),
                                              fold_contains(Person.last_name, word))))))
    return query


# ---------------------------------------------------------------------------
# Foyers et membres
# ---------------------------------------------------------------------------

def same_email(a, b):
    return bool(a and b) and a.strip().lower() == b.strip().lower()


def guardian_email_conflict(person, email, households=None):
    """Responsable dont `person` (à charge) recopierait l'adresse : elle doit rester celle du parent seul."""
    if not email:
        return None
    links = [(link.household, link.role) for link in person.household_links]
    links += [(household, HouseholdMember.DEPENDENT) for household in households or ()]
    for household, role in links:
        if role != HouseholdMember.DEPENDENT:
            continue
        for member in household.guardians:
            if member.person is not person and same_email(member.person.email, email):
                return member.person
    return None


def free_rank(household):
    taken = {m.guardian_rank for m in household.guardians}
    return next((rank for rank in (1, 2) if rank not in taken), None)


def add_member(household, person, role, relationship=None):
    if any(m.person is person for m in household.members):
        raise DirectoryError(f"{person.full_name} fait déjà partie de ce foyer.")
    rank = None
    if role == HouseholdMember.GUARDIAN:
        rank = free_rank(household)
        if rank is None:
            raise DirectoryError("Ce foyer a déjà deux responsables.")
    elif role == HouseholdMember.DEPENDENT:
        if parent := guardian_email_conflict(person, person.email, households=[household]):
            raise DirectoryError(f"{person.full_name} porte l'adresse email de {parent.full_name}, responsable du "
                                 "foyer : retirez-la d'abord de sa fiche.")
    else:
        raise DirectoryError("Rôle inconnu.")
    member = HouseholdMember(person=person, role=role, guardian_rank=rank,
                             relationship=(relationship or "").strip() or None)
    household.members.append(member)
    return member


def change_role(member, role):
    """Responsable ↔ à charge. Un responsable 2 seul ne devient pas responsable 1 : on garde les numéros."""
    if role == member.role:
        return
    household = member.household
    if role == HouseholdMember.GUARDIAN:
        rank = free_rank(household)
        if rank is None:
            raise DirectoryError("Ce foyer a déjà deux responsables.")
        member.role, member.guardian_rank = role, rank
    elif role == HouseholdMember.DEPENDENT:
        if parent := guardian_email_conflict(member.person, member.person.email, households=[household]):
            raise DirectoryError(f"{member.person.full_name} porte l'adresse email de {parent.full_name}.")
        member.role, member.guardian_rank = role, None
    else:
        raise DirectoryError("Rôle inconnu.")


def swap_guardians(household):
    """Le responsable 2 devient le 1 et inversement (le 1 paie, reçoit les courriers)."""
    guardians = sorted(household.guardians, key=lambda m: m.guardian_rank)
    if len(guardians) == 1:
        guardians[0].guardian_rank = 1 if guardians[0].guardian_rank == 2 else 2
        return
    if len(guardians) == 2:
        first, second = guardians
        first.role, first.guardian_rank = HouseholdMember.DEPENDENT, None  # libère le rang 1 le temps de l'échange
        db.session.flush()
        second.guardian_rank = 1
        db.session.flush()
        first.role, first.guardian_rank = HouseholdMember.GUARDIAN, 2


def default_household_name(person):
    return f"Famille {person.last_name}"


# ---------------------------------------------------------------------------
# Archivage
# ---------------------------------------------------------------------------

def archive(record):
    record.archived_at = datetime.now()


def restore(record):
    record.archived_at = None


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

# Champs comparés à la fusion, dans l'ordre de l'écran. « adresse » regroupe ADDRESS_FIELDS.
PERSON_MERGE_FIELDS = {"civility": "Civilité", "first_name": "Prénom", "last_name": "Nom",
                       "birth_name": "Nom de naissance", "sex": "Sexe", "birth_date": "Date de naissance",
                       "birth_place": "Lieu de naissance", "email": "Email", "adresse": "Adresse",
                       "emergency_contact": "Contact d'urgence"}
HOUSEHOLD_MERGE_FIELDS = {"name": "Nom du foyer", "adresse": "Adresse"}


def _value(record, field):
    if field == "adresse":
        return tuple(getattr(record, f) for f in ADDRESS_FIELDS) if record.has_address else None
    value = getattr(record, field)
    return None if value == "" else value


def _comparable(value):
    """« Léa » et « LEA », ou deux adresses à la casse près, ne sont pas un conflit : la fiche gardée l'emporte."""
    if isinstance(value, tuple):
        return tuple(_comparable(v) for v in value)
    return " ".join(fold(value).split()) if isinstance(value, str) else value


def merge_plan(keep, drop, fields):
    """Pour chaque champ : (champ, valeur gardée, valeur de l'autre, conflit). Il n'y a conflit que si les deux fiches
    ont une valeur différente : c'est alors à quelqu'un de choisir. Sinon la valeur remplie l'emporte."""
    plan = []
    for field in fields:
        kept, other = _value(keep, field), _value(drop, field)
        plan.append((field, kept, other, kept is not None and other is not None and _comparable(kept) != _comparable(other)))
    return plan


def _fill(keep, drop, fields, take=()):
    """Mélange les deux fiches dans `keep` : un champ vide d'un côté prend la valeur de l'autre, et en cas de conflit
    la valeur de `keep` reste, sauf pour les champs de `take`. Les notes s'additionnent. Renvoie les champs repris."""
    taken = []
    for field, kept, other, conflict in merge_plan(keep, drop, fields):
        if other is None or (kept is not None and not (conflict and field in take)):
            continue
        if field == "adresse":
            for name in ADDRESS_FIELDS:
                setattr(keep, name, getattr(drop, name))
        else:
            setattr(keep, field, other)
        taken.append(field)
    if drop.notes and drop.notes != keep.notes:
        keep.notes = f"{keep.notes}\n\n{drop.notes}" if keep.notes else drop.notes
        taken.append("notes")
    return taken


def merge_problems(keep, drop):
    """Ce qui empêche de fusionner deux personnes, avant d'y toucher."""
    if keep is drop:
        return ["Une fiche ne se fusionne pas avec elle-même."]
    problems = []
    if keep.account is not None and drop.account is not None:
        problems.append(f"Les deux fiches ont chacune un compte ({keep.account.email} et {drop.account.email}) : "
                        "détachez-en un d'abord.")
    return problems


def merge_persons(keep, drop, take=()):
    """Rattache tout ce qui concerne `drop` à `keep`, puis supprime `drop`. Renvoie un résumé lisible."""
    if problems := merge_problems(keep, drop):
        raise DirectoryError(" ".join(problems))
    summary = []
    if taken := _fill(keep, drop, PERSON_MERGE_FIELDS, take):
        summary.append("champs repris : " + ", ".join(taken))

    in_keep = {link.household_id for link in keep.household_links}
    for link in list(drop.household_links):
        data = (link.household, link.role, link.guardian_rank, link.relationship)
        drop.household_links.remove(link)
        db.session.flush()
        if link.household_id in in_keep:
            summary.append(f"déjà membre de {data[0].name}")
            continue
        db.session.add(HouseholdMember(household=data[0], person=keep, role=data[1], guardian_rank=data[2],
                                       relationship=data[3]))
        summary.append(f"foyer {data[0].name} repris")

    numbers = {phone.number for phone in keep.phones}
    for phone in list(drop.phones):
        if phone.number not in numbers:
            phone.person = keep
    for ref in list(drop.external_refs):
        ref.person = keep

    seasons_kept = {m.season_id for m in keep.memberships}
    for membership in list(drop.memberships):
        if membership.season_id in seasons_kept:
            summary.append(f"adhésion {membership.season.label} en double : celle de la fiche gardée reste")
        else:
            membership.person = keep

    if drop.teacher is not None:
        if keep.teacher is None:
            old = drop.teacher
            keep.teacher = Teacher(status=old.status, started_on=old.started_on, left_on=old.left_on,
                                   work_email=old.work_email, notes=old.notes)
            summary.append("profil de professeur repris")
        drop.teacher = None

    if drop.account is not None:
        account = drop.account
        drop.account = None
        db.session.flush()
        keep.account = account
        summary.append(f"compte {account.email} repris")

    db.session.execute(db.update(BankAccount).where(BankAccount.holder_person_id == drop.id)
                       .values(holder_person_id=keep.id))
    db.session.execute(db.update(Activity).where(Activity.person_id == drop.id).values(person_id=keep.id))
    db.session.flush()
    db.session.delete(drop)
    db.session.flush()
    return summary


def household_merge_problems(keep, drop):
    if keep is drop:
        return ["Un foyer ne se fusionne pas avec lui-même."]
    problems = []
    keep_people = {m.person_id for m in keep.members}
    guardians = {m.person_id for m in keep.guardians} | {m.person_id for m in drop.guardians}
    if len(guardians) > 2:
        problems.append("Les deux foyers ont ensemble plus de deux responsables : retirez-en un d'abord.")
    for member in drop.members:
        if member.person_id in keep_people:
            other = next(m for m in keep.members if m.person_id == member.person_id)
            if other.role != member.role:
                problems.append(f"{member.person.full_name} n'a pas le même rôle dans les deux foyers.")
    if drop.bank_accounts and not bank.enabled():
        problems.append("Le foyer à fusionner a des coordonnées bancaires et BANK_DATA_KEYS n'est pas définie.")
    return problems


def merge_households(keep, drop, take=()):
    if problems := household_merge_problems(keep, drop):
        raise DirectoryError(" ".join(problems))
    summary = []
    if taken := _fill(keep, drop, HOUSEHOLD_MERGE_FIELDS, take):
        summary.append("champs repris : " + ", ".join(taken))
    keep_people = {m.person_id for m in keep.members}
    for member in list(drop.members):
        data = (member.person, member.role, member.relationship)
        drop.members.remove(member)
        db.session.flush()
        if data[0].id in keep_people:
            continue
        db.session.flush()
        add_member(keep, data[0], data[1], data[2])
        summary.append(f"{data[0].full_name} repris")
    numbers = {phone.number for phone in keep.phones}
    for phone in list(drop.phones):
        if phone.number not in numbers:
            phone.household = keep
    for ref in list(drop.external_refs):
        ref.household = keep
    for account in list(drop.bank_accounts):
        iban = bank.reveal(account)
        account.household = keep
        db.session.flush()
        bank.move(account, keep.id, iban)
        summary.append(f"coordonnées bancaires {account.masked_iban} reprises")
    db.session.execute(db.update(Activity).where(Activity.household_id == drop.id).values(household_id=keep.id))
    db.session.flush()
    db.session.delete(drop)
    db.session.flush()
    return summary


# ---------------------------------------------------------------------------
# Comptes de connexion
# ---------------------------------------------------------------------------

def unlinked_accounts():
    return db.session.scalars(db.select(Account).where(~Account.person.has())
                             .order_by(Account.last_name, Account.first_name)).all()


def suggestions(account):
    """Fiches qui pourraient être celles du compte, avec la raison. L'email seul ne suffit pas : on montre aussi les
    homonymes, et c'est un humain qui choisit."""
    email = account.email.lower()
    candidates = db.session.scalars(db.select(Person).where(
        Person.account_id.is_(None), Person.archived_at.is_(None),
        db.or_(db.func.lower(Person.email) == email,
               db.and_(db.func.fold(Person.first_name) == fold(account.first_name),
                       db.func.fold(Person.last_name) == fold(account.last_name)))).order_by(Person.last_name)).all()
    result = []
    for person in candidates:
        reasons = []
        if same_email(person.email, account.email):
            reasons.append("même email")
        if fold(person.first_name) == fold(account.first_name) and fold(person.last_name) == fold(account.last_name):
            reasons.append("même nom")
        result.append((person, reasons))
    return sorted(result, key=lambda item: -len(item[1]))


def link(account, person):
    if person.account is not None:
        raise DirectoryError(f"La fiche de {person.full_name} a déjà un compte ({person.account.email}).")
    if account.person is not None:
        raise DirectoryError(f"Ce compte est déjà rattaché à la fiche de {account.person.full_name}.")
    person.account = account


def unlink(person):
    account = person.account
    person.account = None
    return account


def person_from_account(account):
    """Fiche créée à partir d'un compte sans fiche (un membre du bureau, par exemple), déjà rattachée."""
    if account.person is not None:
        raise DirectoryError("Ce compte a déjà une fiche.")
    person = Person(first_name=account.first_name, last_name=account.last_name, email=account.email, account=account)
    db.session.add(person)
    return person


def account_problems(person):
    """Ce qui empêche de créer un compte pour cette personne."""
    if person.account is not None:
        return f"{person.full_name} a déjà un compte."
    if person.archived_at is not None:
        return "La fiche est archivée."
    if not person.email:
        return "La fiche n'a pas d'adresse email."
    if person.age() is not None and person.age() < ACCOUNT_MIN_AGE:
        return f"Pas de compte avant {ACCOUNT_MIN_AGE} ans : son responsable gère sa fiche depuis le sien."
    if db.session.scalar(db.select(Account.id).filter_by(email=person.email.strip().lower())) is not None:
        return f"L'adresse {person.email} est déjà celle d'un autre compte : rattachez-le plutôt."
    return None


def needs_guardian_consent(person):
    """Un mineur de 16 ou 17 ans peut avoir son compte, avec l'accord d'un responsable légal (décidé le 9 novembre 2026)."""
    return person.is_minor


def create_account(person, role=Role.ADHERENT, guardian_consent=False):
    if problem := account_problems(person):
        raise DirectoryError(problem)
    if needs_guardian_consent(person) and not guardian_consent:
        raise DirectoryError(f"{person.full_name} est mineur(e) : confirmez qu'un responsable légal a donné son accord.")
    account = Account(first_name=person.first_name, last_name=person.last_name, email=person.email.strip().lower(),
                      role=role, active=True)
    db.session.add(account)
    person.account = account
    db.session.flush()
    return account
