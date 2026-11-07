"""Comptes : liens à usage unique, emails de compte, statut, et qui peut gérer quel compte."""
import hashlib
import logging
import secrets
import smtplib
from datetime import datetime, timedelta

from flask import current_app, url_for
from sqlalchemy import and_, exists

from ..extensions import db
from ..models import Account, AccountToken, Role
from ..permissions import can, permissions_of
from . import branding, mailer, roles

logger = logging.getLogger(__name__)

TTL = {
    AccountToken.INVITE: timedelta(days=7),
    AccountToken.RESET: timedelta(hours=1),
    AccountToken.EMAIL: timedelta(hours=24),
}
TTL_LABELS = {AccountToken.INVITE: "7 jours", AccountToken.RESET: "1 heure", AccountToken.EMAIL: "24 heures"}
STATUSES = {"actif": "Actif", "jamais": "Jamais connecté", "invite": "Invitation en attente", "coupe": "Coupé"}


# ---------------------------------------------------------------------------
# Qui peut gérer quoi
# ---------------------------------------------------------------------------

def can_manage(actor, target):
    """Gérer un compte permet d'en prendre le contrôle (un lien d'invitation s'affiche si l'email ne part pas).

    On ne peut donc gérer que des comptes qui n'ont ni plus de droits ni d'autres groupes que soi.
    """
    if actor.is_admin:
        return True
    if target.is_admin or not can(actor, "account.manage"):
        return False
    return (permissions_of(target.role) <= permissions_of(actor.role)
            and {g.id for g in target.all_groups} <= {g.id for g in actor.all_groups})


def assignable_roles(actor):
    """Rôles que `actor` peut attribuer : jamais plus de droits que les siens, ni des groupes qu'il n'a pas.

    Sans la règle des groupes, un rôle qui porte « Admins de l'Inventaire » ferait de n'importe qui un administrateur
    de l'Inventaire, attribué par quelqu'un qui ne l'est pas.
    """
    if actor.is_admin:
        return roles.choices()
    own, own_groups = permissions_of(actor.role), {g.id for g in actor.all_groups}
    return [(role.key, role.name) for role in roles.all_roles()
            if role.key != Role.ADMIN and permissions_of(role.key) <= own
            and {g.id for g in role.groups} <= own_groups]


# ---------------------------------------------------------------------------
# Statut
# ---------------------------------------------------------------------------

def _pending_invitation():
    return exists().where(and_(AccountToken.account_id == Account.id, AccountToken.purpose == AccountToken.INVITE,
                               AccountToken.expires_at >= datetime.now()))


def status_of(account):
    if not account.active:
        return "coupe"
    if account.last_login_at:
        return "actif"
    return "invite" if pending_invitation(account) else "jamais"


def status_condition(status):
    """Même règle que status_of(), en SQL, pour filtrer la liste."""
    return {
        "coupe": Account.active.is_(False),
        "actif": and_(Account.active.is_(True), Account.last_login_at.is_not(None)),
        "invite": and_(Account.active.is_(True), Account.last_login_at.is_(None), _pending_invitation()),
        "jamais": and_(Account.active.is_(True), Account.last_login_at.is_(None), ~_pending_invitation()),
    }[status]


# ---------------------------------------------------------------------------
# Liens à usage unique
# ---------------------------------------------------------------------------

def _digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def issue(account, purpose, email=None, proves_email=False):
    """Crée un lien (et annule les précédents du même type). Renvoie le jeton en clair. Sans commit."""
    db.session.execute(db.delete(AccountToken).where(AccountToken.account_id == account.id,
                                                     AccountToken.purpose == purpose))
    raw = secrets.token_urlsafe(32)
    now = datetime.now()
    db.session.add(AccountToken(id=_digest(raw), account_id=account.id, purpose=purpose, email=email,
                                proves_email=proves_email, created_at=now, expires_at=now + TTL[purpose]))
    return raw


def find(raw):
    """Lien valable correspondant au jeton, sinon None (inconnu, expiré, ou compte coupé)."""
    if not raw or len(raw) > 100:
        return None
    token = db.session.get(AccountToken, _digest(raw))
    if token is None or token.expires_at < datetime.now() or not token.account.active:
        return None
    return token


def mark_delivered(raw):
    """L'email est parti et le lien n'a pas été affiché : l'utiliser prouvera que l'adresse est la bonne. Sans commit."""
    token = db.session.get(AccountToken, _digest(raw))
    if token is not None:
        token.proves_email = True


def consume(token, *purposes):
    """Annule le lien utilisé et, pour le même compte, les autres liens des types donnés. Sans commit."""
    db.session.execute(db.delete(AccountToken).where(AccountToken.account_id == token.account_id,
                                                     AccountToken.purpose.in_(purposes or (token.purpose,))))


def revoke(account):
    """Annule tous les liens en attente (compte coupé, adresse changée…). Sans commit."""
    db.session.execute(db.delete(AccountToken).where(AccountToken.account_id == account.id))


def pending_invitation(account):
    return db.session.scalar(db.select(AccountToken).where(
        AccountToken.account_id == account.id, AccountToken.purpose == AccountToken.INVITE,
        AccountToken.expires_at >= datetime.now()))


def purge_expired(now=None):
    """Efface les liens expirés (lancé par les tâches périodiques). Renvoie leur nombre."""
    return db.session.execute(db.delete(AccountToken).where(AccountToken.expires_at < (now or datetime.now()))).rowcount


def link(raw):
    return current_app.config["BASE_URL"] + url_for("auth.token_link", token=raw)


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------

def _send(to, account, subject, intro, button, url):
    """Envoie un email de compte. Renvoie True s'il est parti, False sinon (SMTP absent ou en panne)."""
    return _deliver(to, mailer.render(account, subject, intro, button, url))


def _deliver(to, message):
    if not mailer.smtp_enabled():
        return False
    try:
        mailer.send(to, *message)
    except (OSError, smtplib.SMTPException):
        logger.exception("Échec d'envoi d'un email de compte")
        return False
    return True


def send_invitation(account, raw):
    asso = branding.name()
    return _send(account.email, account, f"Votre compte {asso}",
                 f"Un compte vous a été ouvert à {asso}.\n\nChoisissez votre mot de passe avec le bouton ci-dessous. "
                 f"Le lien est valable {TTL_LABELS[AccountToken.INVITE]} et ne sert qu'une fois.",
                 "Choisir mon mot de passe", link(raw))


def _reset_message(account, raw):
    return mailer.render(account, "Réinitialisation de votre mot de passe",
                         f"Une réinitialisation du mot de passe de votre compte a été demandée.\n\n"
                         f"Le lien est valable {TTL_LABELS[AccountToken.RESET]} et ne sert qu'une fois. Si vous n'êtes "
                         f"pas à l'origine de cette demande, ignorez cet email : votre mot de passe ne change pas.",
                         "Choisir un nouveau mot de passe", link(raw))


def send_reset(account, raw):
    """Lien envoyé à la demande d'un responsable : celui-ci doit savoir si l'email est parti."""
    return _deliver(account.email, _reset_message(account, raw))


def send_reset_in_background(account, raw):
    """Mot de passe oublié : la durée de la réponse ne doit pas révéler si le compte existe."""
    mailer.send_in_background(account.email, *_reset_message(account, raw))


def send_email_confirmation(account, email, raw):
    return _send(email, account, "Confirmez votre adresse email",
                 f"Confirmez que cette adresse est bien celle de votre compte {branding.name()} "
                 f"(lien valable {TTL_LABELS[AccountToken.EMAIL]}). Sans confirmation, rien ne change.",
                 "Confirmer l'adresse", link(raw))


def send_email_changed_notice(account, old_email):
    """Prévient l'ancienne adresse : un changement non souhaité se repère tout de suite."""
    return _send(old_email, account, "Adresse email modifiée",
                 f"L'adresse email de votre compte est désormais {account.email}.\n\n"
                 f"Si vous n'êtes pas à l'origine de ce changement, contactez vite un responsable de l'association.",
                 "Ouvrir mon compte", current_app.config["BASE_URL"])


def send_totp_reset_notice(account):
    return _send(account.email, account, "Double authentification désactivée",
                 "Un responsable a désactivé la double authentification de votre compte. Vos appareils ont été "
                 "déconnectés.\n\nSi vous n'avez rien demandé, contactez vite un responsable de l'association.",
                 "Ouvrir mon compte", current_app.config["BASE_URL"])
