"""Tâches périodiques, lancées par `flask run-tasks` (service scheduler)."""

from datetime import datetime

from ..extensions import db
from . import accounts, activity, bank, logout, mailer, oidc, registration, sessions, throttle


def run(now=None):
    """Lance les tâches et renvoie un résumé lisible."""
    logins = activity.purge_expired_logins(now)
    attempts = throttle.purge(now)
    links = accounts.purge_expired(now)
    old_sessions = sessions.purge_expired(now)
    expired = oidc.maintain(now)
    mandates, bank_accounts = bank.purge_expired_mandates((now or datetime.now()).date())
    reminded = registration.remind(now)
    orphans = logout.orphans()  # filet de sécurité : une session fermée sans que ses applis aient été prévenues
    db.session.commit()
    sent, failed, dropped = logout.deliver_pending(now)
    emails = mailer.deliver_pending()
    return (
        f"{logins} connexion(s), {attempts} tentative(s) et {links} lien(s) expiré(s) effacé(s), "
        f"{old_sessions} session(s) trop ancienne(s) fermée(s), {expired} élément(s) OIDC expiré(s) effacé(s), "
        f"{orphans} session(s) d'appli rattrapée(s), avis de déconnexion : {sent} reçu(s), {failed} à réessayer, "
        f"{dropped} abandonné(s), {emails} email(s) envoyé(s), {mandates} mandat(s) SEPA et "
        f"{bank_accounts} compte(s) bancaire(s) arrivés au bout de leur conservation supprimés, "
        f"{reminded} foyer(s) relancé(s) pour leur inscription."
    )
