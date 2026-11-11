"""Garde-fous autour des données personnelles : mode sécurisé, limite de lecture, inactivité.

- **Mode sécurisé** : les opérations bancaires demandent un code TOTP récent. Un code ouvre une fenêtre de
  ELEVATION_MINUTES, liée à la session de connexion : une autre session (ou un cookie rejoué après déconnexion)
  ne l'hérite pas.
- **Limite de lecture** : au-delà de DIRECTORY_READ_LIMIT pages de l'annuaire en 15 minutes, un compte est arrêté et
  les administrateurs sont prévenus. Un secrétariat n'y arrive pas ; un compte volé qui aspire l'annuaire, si.
- **Inactivité** : une session qui peut lire des données personnelles se ferme après IDLE_TIMEOUT_HOURS sans activité,
  même avec « rester connecté ».
"""

from datetime import datetime, timedelta

from flask import abort, current_app, session
from flask_login import current_user

from ..extensions import db
from ..models import LoginSession
from . import notify, throttle
from .activity import log

READ_WINDOW = 15 * 60
# Droits qui ouvrent des données personnelles : leurs sessions se ferment après une inactivité.
SENSITIVE_RIGHTS = ("directory.view", "bank.view", "teacher.manage")


# ---------------------------------------------------------------------------
# Mode sécurisé
# ---------------------------------------------------------------------------


def _sid():
    login = db.session.get(LoginSession, current_user.session_token) if current_user.is_authenticated else None
    return login.sid if login else None


def elevated():
    """Vrai si la session a saisi un code TOTP il y a moins de ELEVATION_MINUTES."""
    state = session.get("elevated")
    if not state or state.get("sid") != _sid():
        return False
    try:
        return datetime.now() < datetime.fromisoformat(state["until"])
    except (KeyError, TypeError, ValueError):
        return False


def elevate():
    minutes = current_app.config.get("ELEVATION_MINUTES", 10)
    session["elevated"] = {"sid": _sid(), "until": (datetime.now() + timedelta(minutes=minutes)).isoformat()}


def drop_elevation():
    session.pop("elevated", None)


def remaining():
    """Minutes restantes du mode sécurisé (arrondies au-dessus), 0 s'il n'est pas actif."""
    if not elevated():
        return 0
    left = datetime.fromisoformat(session["elevated"]["until"]) - datetime.now()
    return max(1, -(-int(left.total_seconds()) // 60))


# ---------------------------------------------------------------------------
# Limite de lecture
# ---------------------------------------------------------------------------


def _reads():
    return throttle.Limiter("directory-read", current_app.config.get("DIRECTORY_READ_LIMIT", 300), READ_WINDOW)


def count_read():
    """Compte une page lue. Au-delà de la limite : 429, entrée au journal et alerte des administrateurs."""
    keys = (f"account:{current_user.id}",)
    limiter = _reads()
    if limiter.blocked(keys):
        hour = datetime.now().strftime("%Y%m%d%H")
        created = notify.notify(
            notify.admins(),
            f"Lecture massive de l'annuaire par {current_user.full_name} : "
            f"compte arrêté {READ_WINDOW // 60} minutes. Vérifiez qu'il s'agit bien de cette "
            "personne, sinon coupez son compte.",
            link=f"/comptes/{current_user.id}",
            dedup_key=f"directory-read:{current_user.id}:{hour}",
        )
        if created:  # une seule entrée au journal par heure et par compte
            log(
                "directory.throttle",
                f"Lecture de l'annuaire bloquée pour {current_user.full_name} "
                f"(plus de {limiter.limit} pages en {READ_WINDOW // 60} minutes)",
                target=current_user,
            )
        db.session.commit()
        abort(429)
    limiter.hit(keys)
    db.session.commit()


# ---------------------------------------------------------------------------
# Inactivité
# ---------------------------------------------------------------------------


def handles_personal_data(account):
    from ..permissions import can

    return any(can(account, right) for right in SENSITIVE_RIGHTS)


def idle_expired(login, now=None):
    """Vrai si cette session doit se fermer : compte qui voit des données personnelles, inactif trop longtemps."""
    hours = current_app.config.get("IDLE_TIMEOUT_HOURS", 2)
    last = login.last_seen_at or login.created_at
    return (
        bool(hours) and last < (now or datetime.now()) - timedelta(hours=hours) and handles_personal_data(login.account)
    )
