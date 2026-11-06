"""Sessions de connexion enregistrées en base, pour pouvoir les révoquer (déconnexion, mot de passe changé)."""
import re
import secrets
from datetime import datetime, timedelta

from flask import current_app, request
from flask_login import current_user, login_user, logout_user

from ..extensions import db
from ..models import LoginSession
from .activity import log


def start(user, remember=False, via=None):
    """Ouvre une session pour `user` sur cet appareil (à la place de login_user) et la note au journal."""
    lifetime = current_app.config.get("REMEMBER_COOKIE_DURATION", timedelta(days=365))
    db.session.execute(db.delete(LoginSession).where(LoginSession.created_at < datetime.now() - lifetime))
    user.session_token = secrets.token_urlsafe(32)
    now = datetime.now()
    db.session.add(LoginSession(id=user.session_token, account_id=user.id, created_at=now, last_seen_at=now,
                                user_agent=(request.user_agent.string or "")[:255] or None,
                                ip=request.remote_addr))
    user.last_login_at = now
    log("auth.login", f"Connexion de {user.full_name}" + (f" via {via}" if via else "") + f" ({request.remote_addr})",
        user=user)
    db.session.commit()
    login_user(user, remember=remember)


def end():
    """Déconnecte cet appareil : son cookie, même rejoué, ne donne plus accès."""
    if current_user.is_authenticated:
        log("auth.logout", f"Déconnexion de {current_user.full_name}")
        db.session.execute(db.delete(LoginSession).where(LoginSession.id == current_user.session_token))
        db.session.commit()
    logout_user()


def revoke(user, keep_current=False):
    """Déconnecte les appareils de `user` (tous, ou tous sauf celui de la requête en cours). Sans commit."""
    query = db.delete(LoginSession).where(LoginSession.account_id == user.id)
    if keep_current and current_user.is_authenticated and current_user.id == user.id:
        query = query.where(LoginSession.id != current_user.session_token)
    db.session.execute(query)


def revoke_one(user, session_id):
    """Ferme une session précise de `user`. Renvoie False si elle n'existe pas (ou est à quelqu'un d'autre). Sans commit."""
    return db.session.execute(db.delete(LoginSession).where(
        LoginSession.id == session_id, LoginSession.account_id == user.id)).rowcount > 0


def of(user):
    """Sessions ouvertes de `user`, la plus récemment active d'abord."""
    return db.session.scalars(db.select(LoginSession).where(LoginSession.account_id == user.id).order_by(
        db.func.coalesce(LoginSession.last_seen_at, LoginSession.created_at).desc())).all()


# Mise à jour de « vu pour la dernière fois » au plus toutes les 5 minutes : pas d'écriture à chaque requête.
SEEN_EVERY = timedelta(minutes=5)


def touch(login):
    """Note l'activité d'une session (appelé au chargement de l'utilisateur, en début de requête)."""
    now = datetime.now()
    if login.last_seen_at is None or now - login.last_seen_at >= SEEN_EVERY:
        login.last_seen_at = now
        db.session.commit()


_BROWSERS = (("Edg", "Edge"), ("OPR", "Opera"), ("Firefox", "Firefox"), ("Chrome", "Chrome"), ("Safari", "Safari"))
_SYSTEMS = (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
            ("Mac OS X", "macOS"), ("Macintosh", "macOS"), ("CrOS", "ChromeOS"), ("Linux", "Linux"))


def device_label(user_agent):
    """« Firefox · macOS », d'après l'en-tête User-Agent (approximatif, pour reconnaître ses appareils)."""
    ua = user_agent or ""
    browser = next((name for key, name in _BROWSERS if re.search(rf"\b{key}/", ua)), None)
    system = next((name for key, name in _SYSTEMS if key in ua), None)
    return " · ".join(x for x in (browser, system) if x) or "Appareil inconnu"


def revoke_all():
    """Déconnecte tout le monde sauf l'appareil de la requête en cours. Renvoie le nombre de sessions fermées. Sans commit."""
    query = db.delete(LoginSession)
    if current_user.is_authenticated:
        query = query.where(LoginSession.id != current_user.session_token)
    return db.session.execute(query).rowcount


def stats():
    """(sessions ouvertes, personnes connectées), pour la page Paramètres."""
    row = db.session.execute(db.select(db.func.count(LoginSession.id),
                                       db.func.count(db.distinct(LoginSession.account_id)))).one()
    return tuple(row)
