"""Limitation de débit en base : au-delà de `limit` événements en `window` secondes pour une même clé (email, IP…),
l'action est refusée temporairement. La limite vaut pour tous les workers, et résiste à un redémarrage.

Les services ne font pas de commit : c'est la route qui valide `hit` et `clear`.
"""
import hashlib
import hmac
from datetime import datetime, timedelta

from flask import current_app

from ..extensions import db
from ..models import ThrottleEvent

# Aucun limiteur n'a de fenêtre plus longue : ce qui est plus ancien ne sert plus (cf. purge).
MAX_WINDOW = timedelta(days=1)


class Limiter:
    def __init__(self, name, limit, window):
        self.name, self.limit, self.window = name, limit, window

    def _digest(self, key):
        """Empreinte de la clé : un mot de passe tapé par erreur dans le champ email ne reste pas lisible en base."""
        secret = current_app.config["SECRET_KEY"].encode()
        return hmac.new(secret, f"{self.name}:{key}".encode(), hashlib.sha256).hexdigest()

    def blocked(self, keys):
        since = datetime.now() - timedelta(seconds=self.window)
        counts = db.session.execute(
            db.select(ThrottleEvent.key_hash, db.func.count())
            .where(ThrottleEvent.key_hash.in_([self._digest(k) for k in keys]), ThrottleEvent.created_at >= since)
            .group_by(ThrottleEvent.key_hash)).all()
        return any(count >= self.limit for _, count in counts)

    def hit(self, keys):
        db.session.add_all(ThrottleEvent(key_hash=self._digest(k)) for k in keys)

    def clear(self, keys):
        db.session.execute(db.delete(ThrottleEvent).where(
            ThrottleEvent.key_hash.in_([self._digest(k) for k in keys])))


def purge(now=None):
    """Efface les événements qui ne comptent plus (lancé par les tâches périodiques). Renvoie leur nombre."""
    limit = (now or datetime.now()) - MAX_WINDOW
    return db.session.execute(db.delete(ThrottleEvent).where(ThrottleEvent.created_at < limit)).rowcount
