"""Coordonnées modifiées par les familles elles-mêmes, en cours d'année (Ma famille).

Ce qui est modifiable se règle dans Paramètres → Adhérents. Le changement s'applique tout de suite, il est noté au
journal, et le secrétariat (droit « Modifier l'annuaire ») reçoit une notification. Tant qu'un dossier d'inscription du
foyer est en cours, les coordonnées passent par le dossier : il les recopiera à sa validation.
"""

from flask import url_for

from ..extensions import db
from ..models import Dossier
from . import directory, notify, options
from .activity import log
from .directory import DirectoryError

OPEN_DOSSIER = (Dossier.DRAFT, Dossier.SUBMITTED, Dossier.TO_FIX)
MAX_PHONES = 4


def open_dossier(household):
    return db.session.scalar(
        db.select(Dossier).where(Dossier.household_id == household.id, Dossier.status.in_(OPEN_DOSSIER)).limit(1)
    )


def editable_people(account, household):
    """Fiches dont le compte modifie les coordonnées : la sienne et celles des enfants à charge du foyer. Celle de
    l'autre responsable reste à lui."""
    own = getattr(account, "person", None)
    people = [link.person for link in household.dependents]
    if own is not None and own in [link.person for link in household.guardians]:
        people.insert(0, own)
    return people


def _phones(form, prefix):
    """[(libellé, numéro)] saisis pour un propriétaire ; un numéro vide retire la ligne."""
    rows = []
    for k in range(MAX_PHONES):
        number = (form.get(f"{prefix}-phone-{k}") or "").strip()
        if number:
            rows.append(((form.get(f"{prefix}-label-{k}") or "").strip()[:40] or None, directory.normalize_phone(number)))
    return rows


def _replace_phones(owner, rows):
    before = [(p.label, p.number) for p in owner.phones]
    if before == rows:
        return False
    owner.phones.clear()
    db.session.flush()
    for label, number in rows:
        directory.add_phone(owner, number, label)
    return True


def save(account, household, form):
    """Applique les coordonnées saisies. Renvoie la liste des changements (pour le message) ; lève DirectoryError."""
    allowed = options.member_editable()
    if not allowed:
        raise DirectoryError("Les coordonnées ne se modifient pas ici : passez par le secrétariat.")
    if open_dossier(household) is not None:
        raise DirectoryError("Un dossier d'inscription est en cours : modifiez vos coordonnées dans le dossier.")
    changes = []
    if "adresse" in allowed:
        fields = {
            "address_street": (form.get("address_street") or "").strip() or None,
            "postal_code": (form.get("postal_code") or "").strip()[:10] or None,
            "city": (form.get("city") or "").strip()[:80] or None,
        }
        if any(getattr(household, key) != value for key, value in fields.items()):
            for key, value in fields.items():
                setattr(household, key, value)
            changes.append("adresse")
    people = editable_people(account, household)
    if "telephones" in allowed:
        if _replace_phones(household, _phones(form, "foyer")):
            changes.append("téléphone du foyer")
        for person in people:
            if _replace_phones(person, _phones(form, f"p{person.id}")):
                changes.append(f"téléphone de {person.first_name}")
    if "email" in allowed:
        for person in people:
            email = (form.get(f"p{person.id}-email") or "").strip() or None
            if email is not None and ("@" not in email or len(email) > 255):
                raise DirectoryError(f"Adresse email invalide pour {person.first_name}.")
            if email != person.email:
                if person.account is not None and email is None:
                    raise DirectoryError(f"{person.first_name} a un compte : son adresse email ne peut pas être vidée.")
                person.email = email
                changes.append(f"email de {person.first_name}")
    if changes:
        summary = ", ".join(changes)
        log("member.contacts", f"Coordonnées mises à jour par la famille : {summary}", household=household)
        notify.notify(
            [a for a in notify.holders("directory.edit") if a.id != account.id],
            f"{account.full_name} a mis à jour les coordonnées du foyer {household.name} : {summary}.",
            link=url_for("directory.household", household_id=household.id),
        )
    return changes
