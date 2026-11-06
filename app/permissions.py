"""Permissions centralisées.

Toute vérification de droits passe par `can(user, action)`, utilisée à la fois par le décorateur `require`
des routes et par les templates (pour masquer les boutons).
Les droits de chaque rôle se cochent dans l'écran « Rôles » (table `role`) ; l'administrateur les a tous.
"""
from functools import wraps

from flask import abort, has_app_context
from flask_login import current_user, login_required

from .extensions import db
from .models import Role

A, G, H = Role.ADMIN, Role.GESTIONNAIRE, Role.ADHERENT

# Catalogue des droits, regroupés comme dans l'écran « Rôles ».
PERMISSION_GROUPS = [
    ("Administration", [
        ("settings.manage", "Paramètres de l'appli"),
        ("activity.view", "Voir le journal de toutes les actions (connexions comprises)"),
    ]),
]
PERMISSIONS = {key: label for _, perms in PERMISSION_GROUPS for key, label in perms}

# Droits des rôles de base tant qu'ils n'ont pas été modifiés (et à la création de la base).
DEFAULT_PERMISSIONS = {
    A: set(PERMISSIONS),
    G: {"activity.view"},
    H: set(),
}


def permissions_of(role_key):
    """Droits d'un rôle : tous pour l'administrateur, sinon ceux enregistrés (ou ceux par défaut)."""
    if role_key == A:
        return set(PERMISSIONS)
    role = db.session.get(Role, role_key) if has_app_context() else None
    if role is not None:
        return set(role.permissions or ())
    return DEFAULT_PERMISSIONS.get(role_key, set())


def can(user, action):
    if action not in PERMISSIONS:
        raise KeyError(f"Permission inconnue : {action}")
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return action in permissions_of(user.role)


def require(action):
    """Décorateur de route : connexion obligatoire + droit `action`."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not can(current_user, action):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
