"""Durée de conservation et anonymisation des anciens adhérents. Les services ne font pas de commit.

La durée n'a pas de valeur par défaut : tant qu'elle n'est pas choisie (question posée à la direction), rien n'est
proposé à l'anonymisation. Elle se compte en saisons : une personne sans adhésion active depuis N saisons, et qui n'a
plus de lien actif avec l'association (compte, professeur, foyer d'un adhérent récent), peut être anonymisée.

Ce qui reste après l'anonymisation : les adhésions et les inscriptions (sans nom), pour les statistiques des saisons
passées, et l'année de naissance. Tout le reste est effacé : nom, coordonnées, téléphones, numéros externes,
consentements, observations, et le texte du journal qui la concernait.
"""

from datetime import datetime

from ..extensions import db
from ..models import Activity, AppSetting, Campaign, Dossier, HouseholdMember, Membership, Person, Season

SETTING = "retention_seasons"
ANONYMOUS = "Anonyme"


def seasons_kept():
    """Nombre de saisons de conservation, ou None si la durée n'a pas encore été choisie."""
    setting = db.session.get(AppSetting, SETTING)
    return int(setting.value) if setting and setting.value.isdigit() and int(setting.value) > 0 else None


def set_seasons_kept(value):
    setting = db.session.get(AppSetting, SETTING)
    if value is None:
        if setting is not None:
            db.session.delete(setting)
        return
    if setting is None:
        setting = AppSetting(key=SETTING, value="")
        db.session.add(setting)
    setting.value = str(int(value))


def oldest_kept_season(count=None):
    """La plus ancienne des `count` dernières saisons commencées : avant elle, on ne garde plus."""
    count = count or seasons_kept()
    if not count:
        return None
    seasons = db.session.scalars(
        db.select(Season)
        .where(Season.starts_on <= datetime.now().date())
        .order_by(Season.starts_on.desc())
        .limit(count)
    ).all()
    return seasons[-1] if len(seasons) == count else None


def _recent(season):
    return Membership.season.has(Season.starts_on >= season.starts_on)


def candidates(count=None):
    """Personnes à anonymiser : ni adhésion récente, ni compte, ni profil de professeur, ni responsable ou membre
    d'un foyer où quelqu'un a une adhésion récente. Déjà anonymisées exclues."""
    limit = oldest_kept_season(count)
    if limit is None:
        return []
    recent_households = (
        db.select(HouseholdMember.household_id).join(Person).where(Person.memberships.any(_recent(limit)))
    )
    query = db.select(Person).where(
        Person.anonymized_at.is_(None),
        Person.account_id.is_(None),
        ~Person.teacher.has(),
        ~Person.memberships.any(_recent(limit)),
        Person.created_at < datetime.combine(limit.starts_on, datetime.min.time()),
        ~Person.household_links.any(HouseholdMember.household_id.in_(recent_households)),
    )
    return db.session.scalars(query.order_by(Person.last_name, Person.first_name)).all()


def anonymize(person, now=None):
    """Efface ce qui identifie la personne. Ses adhésions et inscriptions restent, sans nom."""
    now = now or datetime.now()
    birth_year = person.birth_date.year if person.birth_date else None
    for field in (
        "civility",
        "birth_name",
        "birth_place",
        "email",
        "emergency_contact",
        "notes",
        "address_recipient",
        "address_street",
        "postal_code",
        "city",
    ):
        setattr(person, field, None)
    person.first_name, person.last_name = ANONYMOUS, f"n° {person.id}"
    person.birth_date = datetime(birth_year, 1, 1).date() if birth_year else None  # l'année seule, au 1er janvier
    person.anonymized_at = now
    person.archived_at = person.archived_at or now
    person.phones.clear()
    person.external_refs.clear()
    person.consents.clear()
    from .attendance import forget

    forget(person)  # les présences ne survivent pas à la fiche
    db.session.execute(
        db.update(Activity)
        .where(Activity.person_id == person.id)
        .values(message="(effacé à l'anonymisation de la fiche)", changes=None)
    )
    for link in list(person.household_links):
        household = link.household
        if all(m.person is person or m.person.anonymized_at for m in household.members):
            anonymize_household(household, now)


def anonymize_household(household, now=None):
    now = now or datetime.now()
    household.name = f"Foyer anonymisé n° {household.id}"
    for field in ("address_recipient", "address_street", "postal_code", "city", "notes"):
        setattr(household, field, None)
    household.archived_at = household.archived_at or now
    household.phones.clear()
    household.external_refs.clear()
    household.bank_accounts.clear()  # IBAN et mandats
    household.consents.clear()
    db.session.execute(
        db.update(Activity)
        .where(Activity.household_id == household.id)
        .values(message="(effacé à l'anonymisation du foyer)", changes=None)
    )
    for dossier in db.session.scalars(db.select(Dossier).filter_by(household_id=household.id)):
        db.session.delete(dossier)


def old_dossiers(count=None):
    """Dossiers des campagnes de saisons plus anciennes que la durée de conservation : leur contenu (JSON, IBAN) a été
    recopié dans l'annuaire à la validation, ou n'a jamais abouti."""
    limit = oldest_kept_season(count)
    if limit is None:
        return []
    return db.session.scalars(
        db.select(Dossier)
        .join(Campaign)
        .join(Season)
        .where(Season.starts_on < limit.starts_on, Campaign.closes_at < datetime.now())
    ).all()


def purge_dossiers(count=None):
    dossiers = old_dossiers(count)
    for dossier in dossiers:
        db.session.delete(dossier)
    return len(dossiers)
