"""Groupes de comptes."""
import re

from ..extensions import db, fold
from ..models import AccessGroup


def all_groups():
    return db.session.scalars(db.select(AccessGroup).order_by(AccessGroup.name)).all()


def new_key(name):
    """Valeur stable transmise aux applis, tirée du nom : « Régie son » → « regie-son »."""
    base = re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-")[:36] or "groupe"
    key, n = base, 2
    while db.session.scalar(db.select(AccessGroup.id).filter_by(key=key)) is not None:
        key, n = f"{base}-{n}", n + 1
    return key


def set_members_of(account, wanted):
    """Remplace les groupes d'un compte. Renvoie (ajoutés, retirés), pour le journal. Sans commit.

    C'est le seul point de changement : la phase 4 s'y branchera pour déconnecter le compte des applis concernées.
    """
    current = set(account.groups)
    wanted = set(wanted)
    account.groups = sorted(wanted, key=lambda g: fold(g.name))
    return wanted - current, current - wanted
