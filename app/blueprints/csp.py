"""Rapports de violation de la Content-Security-Policy envoyés par les navigateurs : journalisés, rien n'est stocké.
Public et sans jeton CSRF (le navigateur les poste lui-même), donc limité en débit et en taille."""

import json

from flask import Blueprint, current_app, request

from ..extensions import csrf, db
from ..services import throttle

bp = Blueprint("csp", __name__)

MAX_BODY = 16 * 1024
MAX_FIELD = 500
MAX_REPORTS = 10  # rapports journalisés par requête (report-to les envoie par lots)
_by_ip = throttle.Limiter("csp", 60, 3600)

# Champs utiles : (nom journalisé, clé report-uri, clé report-to)
FIELDS = (
    ("directive", "effective-directive", "effectiveDirective"),
    ("violée", "violated-directive", None),
    ("bloqué", "blocked-uri", "blockedURL"),
    ("page", "document-uri", "documentURL"),
    ("source", "source-file", "sourceFile"),
    ("ligne", "line-number", "lineNumber"),
    ("mode", "disposition", "disposition"),
)


def _clean(value):
    """Une ligne de journal par rapport : caractères de contrôle (retours à la ligne…) remplacés, longueur bornée."""
    text = "".join(c if c.isprintable() else "?" for c in str(value)[:MAX_FIELD])
    return text or "-"


def _reports(data, mimetype):
    """Rapports au format commun {nom: valeur}, depuis report-uri ({"csp-report": {…}}) ou report-to ([{…}, …])."""
    if mimetype == "application/reports+json":
        bodies = (
            [e.get("body") for e in data if isinstance(e, dict) and e.get("type") == "csp-violation"]
            if isinstance(data, list)
            else []
        )
        column = 2
    else:
        bodies = [data.get("csp-report")] if isinstance(data, dict) else []
        column = 1
    return [
        {field[0]: body.get(field[column]) for field in FIELDS if field[column]}
        for body in bodies
        if isinstance(body, dict)
    ][:MAX_REPORTS]


@bp.route("/csp-report", methods=["POST"])
@csrf.exempt
def report():
    ip = [request.remote_addr or "?"]
    if _by_ip.blocked(ip):
        return "", 429
    _by_ip.hit(ip)
    db.session.commit()
    body = request.stream.read(MAX_BODY + 1)  # sans se fier à Content-Length (envoi par morceaux)
    if len(body) > MAX_BODY:
        return "", 413
    if request.mimetype not in ("application/csp-report", "application/reports+json", "application/json"):
        return "", 415
    try:
        data = json.loads(body)
    except ValueError:
        return "", 400
    for entry in _reports(data, request.mimetype):
        current_app.logger.warning(
            "Violation CSP : "
            + " ".join(f"{name}={_clean(value)}" for name, value in entry.items() if value not in (None, ""))
        )
    return "", 204
