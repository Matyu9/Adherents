"""Tâches périodiques, lancées par `flask run-tasks` (service scheduler)."""
from ..extensions import db
from . import activity, mailer, throttle


def run(now=None):
    """Lance les tâches et renvoie un résumé lisible."""
    logins = activity.purge_expired_logins(now)
    attempts = throttle.purge(now)
    db.session.commit()
    emails = mailer.deliver_pending()
    return f"{logins} connexion(s) et {attempts} tentative(s) effacée(s), {emails} email(s) envoyé(s)."
