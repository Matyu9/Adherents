from datetime import datetime, timedelta

from flask_login import current_user

from ..extensions import db
from ..models import Activity

# Durée de conservation des connexions au journal : elles contiennent des adresses IP.
LOGIN_RETENTION_DAYS = 365
# Champs jamais recopiés dans le journal, même sous forme d'ancienne et de nouvelle valeur.
SENSITIVE_FIELDS = frozenset({"password_hash", "iban", "bic"})


def log(action, message, *, changes=None, user=None, target=None):
    """Ajoute une entrée au journal d'activité (sans commit). `target` : le compte concerné, s'il y en a un."""
    if user is None and current_user and current_user.is_authenticated:
        user = current_user
    entry = Activity(account=user, action=action, message=message, changes=changes or None,
                     target_account_id=target.id if target else None)
    db.session.add(entry)
    return entry


def purge_expired_logins(now=None):
    """Efface les connexions, déconnexions et échecs trop anciens (lancé par les tâches périodiques)."""
    limit = (now or datetime.now()) - timedelta(days=LOGIN_RETENTION_DAYS)
    return db.session.execute(db.delete(Activity).where(Activity.action.startswith("auth."),
                                                        Activity.created_at < limit)).rowcount


def diff(obj, fields, new_values):
    """Applique `new_values` sur `obj` et renvoie {champ: [ancien, nouveau]} pour ce qui a changé.

    Un champ sensible est bien appliqué, mais le journal note seulement qu'il a changé, sans ses valeurs.
    """
    changes = {}
    for field in fields:
        if field not in new_values:
            continue
        old, new = getattr(obj, field), new_values[field]
        if old != new:
            changes[field] = ["(masqué)", "(masqué)"] if field in SENSITIVE_FIELDS else [_jsonable(old), _jsonable(new)]
            setattr(obj, field, new)
    return changes


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
