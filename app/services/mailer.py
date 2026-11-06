"""Envoi d'emails via SMTP (facultatif)."""
import logging
import re
import smtplib
import threading
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app, render_template

from ..extensions import db
from ..models import Account, Notification
from . import branding

logger = logging.getLogger(__name__)
# Une notification plus ancienne n'est plus envoyée (ex. SMTP configuré après coup).
MAX_AGE = timedelta(days=2)
DEFAULT_BUTTON = "Ouvrir l'espace adhérents"


def smtp_enabled():
    cfg = current_app.config
    return bool(cfg.get("SMTP_HOST") and (cfg.get("SMTP_FROM") or cfg.get("SMTP_USER")))


def send(to, subject, text, html=None):
    """Envoie un email ; `html`, s'il est fourni, s'ajoute en alternative à la version texte."""
    cfg = current_app.config
    message = EmailMessage()
    message["From"] = formataddr((branding.name(), cfg["SMTP_FROM"] or cfg["SMTP_USER"]))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as smtp:
        if cfg["SMTP_TLS"]:
            smtp.starttls()
        if cfg["SMTP_USER"]:
            smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        smtp.send_message(message)


def send_in_background(to, subject, text, html=None):
    """Envoie sans faire attendre la requête. Un échec est seulement noté dans les journaux."""
    app = current_app._get_current_object()

    def deliver():
        with app.app_context():
            try:
                send(to, subject, text, html)
            except (OSError, smtplib.SMTPException):
                logger.exception("Échec d'envoi d'un email de compte")

    if app.config["MAIL_IN_BACKGROUND"]:
        threading.Thread(target=deliver, daemon=True).start()
    else:
        deliver()


def compose(notification):
    """(objet, texte, html) de l'email correspondant à une notification."""
    url = f"{current_app.config['BASE_URL']}{notification.link}" if notification.link else None
    return render(notification.account, notification.message, notification.message, DEFAULT_BUTTON, url)


def compose_test(account):
    return render(account, "Email de test", "Si vous lisez ceci, l'envoi d'emails de l'appli fonctionne.",
                  DEFAULT_BUTTON, current_app.config["BASE_URL"], "ok")


def render(account, subject, intro, button, url, tone="brand"):
    subject = _shorten(" ".join(subject.split()))  # pas de retour à la ligne dans un en-tête
    # Paragraphes séparés par une ligne vide ; un simple retour à la ligne est conservé dans le paragraphe.
    paragraphs = [p.strip().splitlines() for p in re.split(r"\n\s*\n", intro.strip()) if p.strip()]
    context = {"user": account, "subject": subject, "intro": intro.strip(), "paragraphs": paragraphs,
               "button": button.strip() or DEFAULT_BUTTON, "url": url, "tone": tone}
    return (f"[{branding.name()}] {subject}", render_template("emails/notification.txt", **context),
            render_template("emails/notification.html", **context))


def _shorten(text, limit=100):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def deliver_pending(limit=200):
    """Envoie par email les notifications récentes pas encore envoyées. Renvoie le nombre d'emails envoyés."""
    if not smtp_enabled():
        return 0
    since = datetime.now() - MAX_AGE
    pending = db.session.scalars(
        db.select(Notification).join(Account).where(
            Notification.emailed.is_(False), Notification.created_at >= since, Account.active.is_(True))
        .order_by(Notification.id).limit(limit)).all()
    sent = 0
    for notification in pending:
        try:
            send(notification.account.email, *compose(notification))
        except (OSError, smtplib.SMTPException):
            logger.exception("Échec d'envoi de la notification %s", notification.id)
            break  # serveur indisponible : on réessaiera au prochain passage
        notification.emailed = True
        sent += 1
    db.session.commit()
    return sent


def deliver_quietly():
    """Après une action utilisateur : tente l'envoi sans jamais faire échouer la requête."""
    try:
        return deliver_pending(limit=20)
    except Exception:  # noqa: BLE001
        logger.exception("Envoi des emails impossible")
        db.session.rollback()
        return 0
