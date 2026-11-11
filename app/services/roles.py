"""Rôles : liste ordonnée, création d'une clé, rôle par défaut des rôles de base."""

import re

from ..extensions import db, fold
from ..models import Account, Role
from ..permissions import DEFAULT_PERMISSIONS


def default_role(key):
    """Rôle de base tel qu'à l'installation (pas encore enregistré en base)."""
    return Role(key=key, name=Role.DEFAULT_NAMES[key], permissions=sorted(DEFAULT_PERMISSIONS[key]))


def find(key):
    """Rôle enregistré, ou rôle de base par défaut, sinon None."""
    role = db.session.get(Role, key)
    if role is None and key in Role.BUILTIN:
        role = default_role(key)
    return role


def get(key):
    """Comme find(), pour le modifier : un rôle de base pas encore enregistré est ajouté à la session."""
    role = find(key)
    if role is not None and role not in db.session:
        db.session.add(role)
    return role


def all_roles():
    """Rôles de base (dans l'ordre administrateur, gestionnaire, adhérent), puis les autres par nom."""
    saved = {r.key: r for r in db.session.scalars(db.select(Role))}
    builtin = [saved.pop(key, None) or default_role(key) for key in Role.BUILTIN]
    return builtin + sorted(saved.values(), key=lambda r: fold(r.name))


def choices(include_admin=True):
    return [(r.key, r.name) for r in all_roles() if include_admin or r.key != Role.ADMIN]


def new_key(name):
    """Clé courte et unique tirée du nom : « Régisseur son » → « regisseur-son »."""
    base = re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-")[:16] or "role"
    key, n = base, 2
    while key in Role.BUILTIN or db.session.get(Role, key) is not None:
        key, n = f"{base}-{n}", n + 1
    return key


def member_counts():
    return dict(db.session.execute(db.select(Account.role, db.func.count(Account.id)).group_by(Account.role)).all())
