"""Notifications dans l'appli (et par email quand le SMTP est configuré, cf. mailer.py)."""
from flask_login import current_user

from ..extensions import db
from ..models import Account, Notification, Role


def notify(accounts, message, link=None, dedup_key=None):
    """Crée une notification par destinataire (sans commit). `dedup_key` évite les doublons des relances."""
    created = []
    for account in {a.id: a for a in accounts if a is not None}.values():
        key = f"{dedup_key}:{account.id}" if dedup_key else None
        if key and db.session.scalar(db.select(Notification.id).filter_by(dedup_key=key)):
            continue
        notification = Notification(account_id=account.id, message=message[:500], link=link, dedup_key=key)
        db.session.add(notification)
        created.append(notification)
    return created


def admins():
    return db.session.scalars(db.select(Account).where(Account.role == Role.ADMIN, Account.active.is_(True))).all()


def unread_count():
    """Pastille de la cloche, pour le compte connecté."""
    if not current_user.is_authenticated:
        return 0
    return db.session.scalar(db.select(db.func.count(Notification.id)).where(
        Notification.account_id == current_user.id, Notification.read.is_(False)))
