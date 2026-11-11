"""Instruments prêtés, lus dans l'API de l'Inventaire pour l'espace adhérent.

L'Inventaire ne sait pas qui est connecté ici : c'est Adhérents qui vérifie que la personne connectée est responsable
de celles qu'il demande, puis appelle l'API avec le jeton du compte de service (droit loan.by_person seulement).
Une panne de l'Inventaire ne casse pas l'espace adhérent : la section des instruments dit seulement qu'elle est
indisponible.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit

from flask import current_app

logger = logging.getLogger(__name__)
MAX_IDS = 20
MAX_BYTES = 1024 * 1024
TIMEOUT = 5


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Le jeton ne part qu'à l'adresse configurée."""

    def redirect_request(self, *args, **kwargs):
        return None


def enabled():
    config = current_app.config
    if not (config.get("INVENTAIRE_API_URL") and config.get("INVENTAIRE_API_TOKEN")):
        return False
    parts = urlsplit(config["INVENTAIRE_API_URL"])
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1"))


def loans(person_uuids):
    """{uuid: [prêt…]} pour ces personnes (20 au plus), ou None si l'Inventaire ne répond pas comme prévu."""
    ids = list(dict.fromkeys(person_uuids))[:MAX_IDS]
    if not ids or not enabled():
        return None
    config = current_app.config
    url = f"{config['INVENTAIRE_API_URL']}/annuaire/prets?" + urllib.parse.urlencode([("personne", i) for i in ids])
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {config['INVENTAIRE_API_TOKEN']}",
            "Accept": "application/json",
            "User-Agent": "Adherents",
        },
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read(MAX_BYTES + 1)[:MAX_BYTES])
    except (urllib.error.URLError, OSError, ValueError) as error:
        logger.warning("Inventaire injoignable pour l'espace adhérent : %s", error)
        return None
    persons = data.get("persons") if isinstance(data, dict) else None
    if not isinstance(persons, dict):
        return None
    return {i: [loan for loan in persons.get(i, []) if isinstance(loan, dict)] for i in ids}
