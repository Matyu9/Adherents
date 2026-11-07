"""Tâches périodiques, lancées par `flask run-tasks` (service scheduler)."""
from ..extensions import db
from . import accounts, activity, logout, mailer, oidc, sessions, throttle


def run(now=None):
    """Lance les tâches et renvoie un résumé lisible."""
    logins = activity.purge_expired_logins(now)
    attempts = throttle.purge(now)
    links = accounts.purge_expired(now)
    old_sessions = sessions.purge_expired(now)
    expired = oidc.maintain(now)
    orphans = logout.orphans()  # filet de sécurité : une session fermée sans que ses applis aient été prévenues
    db.session.commit()
    sent, failed, dropped = logout.deliver_pending(now)
    emails = mailer.deliver_pending()
    return (f"{logins} connexion(s), {attempts} tentative(s) et {links} lien(s) expiré(s) effacé(s), "
            f"{old_sessions} session(s) trop ancienne(s) fermée(s), {expired} élément(s) OIDC expiré(s) effacé(s), "
            f"{orphans} session(s) d'appli rattrapée(s), avis de déconnexion : {sent} reçu(s), {failed} à réessayer, "
            f"{dropped} abandonné(s), {emails} email(s) envoyé(s).")
