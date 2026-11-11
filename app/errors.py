"""Pages d'erreur : une seule mise en page, un message en français adapté à chaque code."""

import contextlib
import uuid
from urllib.parse import urlsplit

from flask import current_app, jsonify, render_template, request
from flask_wtf.csrf import CSRFError
from sqlalchemy.exc import DataError
from werkzeug.exceptions import HTTPException, InternalServerError

from .extensions import db

# Codes SQLSTATE « hors limites » : valeur numérique (22003) ou date (22008).
OUT_OF_RANGE = {"22003", "22008"}

# code → (icône, titre, explication)
MESSAGES = {
    400: ("alert", "Requête invalide", "La demande envoyée n'a pas pu être comprise. Revenez en arrière et réessayez."),
    403: ("key", "Accès refusé", "Vous n'avez pas les droits nécessaires pour cette action."),
    404: ("search", "Page introuvable", "Cette page n'existe pas ou a été supprimée."),
    405: (
        "alert",
        "Action impossible",
        "Cette adresse ne s'utilise pas de cette façon : passez par les boutons de l'appli.",
    ),
    413: ("image", "Fichier trop volumineux", "Le fichier envoyé dépasse {max_mb} Mo. Réduisez-le puis réessayez."),
    429: ("clock", "Trop de tentatives", "Patientez quelques minutes avant de réessayer."),
}
DEFAULT = ("alert", "Demande impossible", "La demande n'a pas pu aboutir. Revenez en arrière et réessayez.")
CSRF_EXPIRED = (
    "clock",
    "Page expirée",
    "Par sécurité, le formulaire a expiré et rien n'a été enregistré. Rechargez la page puis recommencez.",
)


def _back_url():
    """Page précédente, si elle vient de l'appli et n'est pas celle en erreur."""
    ref = urlsplit(request.referrer or "")
    path = ref.path + ("?" + ref.query if ref.query else "")
    if ref.netloc == request.host and path.startswith("/") and ref.path != request.path:
        return path
    return None


def _page(code, icon, title, message):
    message = message.format(max_mb=(current_app.config.get("MAX_CONTENT_LENGTH") or 0) // (1024 * 1024))
    return render_template(
        "errors/error.html", code=code, icon_name=icon, title=title, message=message, back=_back_url()
    ), code


def _is_api():
    return request.path.startswith("/api/")


def _json(code, title, e=None):
    """Erreur de l'API : {"error": {"code", "message"}}. Le message précis passé à abort() prime sur le titre."""
    message = e.description if e is not None and e.description != type(e).description else title
    response = jsonify({"error": {"code": code, "message": message}})
    response.status_code = code
    for name, value in e.get_headers() if e is not None else ():
        if name.lower() != "content-type":  # WWW-Authenticate (401), Retry-After (429)
            response.headers[name] = value
    return response


def register_error_handlers(app):
    @app.errorhandler(HTTPException)
    def http_error(e):
        if e.code is None or e.code < 400:
            return e
        if _is_api() and e.code < 500:
            return _json(e.code, MESSAGES.get(e.code, DEFAULT)[1], e)
        if e.code >= 500:
            return server_error(e)
        if isinstance(e, CSRFError):
            return _page(400, *CSRF_EXPIRED)
        return _page(e.code, *MESSAGES.get(e.code, DEFAULT))

    @app.errorhandler(OverflowError)
    def overflow(_e):
        # Identifiant, page ou date hors limites (ex. /notifications/99999999999999999999) : rien ne peut correspondre.
        db.session.rollback()
        return _json(404, MESSAGES[404][1]) if _is_api() else _page(404, *MESSAGES[404])

    @app.errorhandler(DataError)
    def out_of_range(e):
        # La même chose vue par PostgreSQL : nombre ou date hors des limites de la colonne. Toute autre erreur de
        # donnée reste une vraie erreur, traitée comme les autres (page 500 avec sa référence).
        if getattr(e.orig, "sqlstate", None) in OUT_OF_RANGE:
            return overflow(e)
        return app.handle_exception(e)

    @app.errorhandler(InternalServerError)
    def server_error(_e):
        # La page 500 ne touche pas à la base (elle est peut-être la cause) : gabarit autonome, sans menu.
        with contextlib.suppress(Exception):
            db.session.rollback()
        ref = uuid.uuid4().hex[:8]
        current_app.logger.error("Erreur 500, référence %s : %s %s", ref, request.method, request.path)
        if _is_api():
            return _json(500, f"Erreur interne (référence {ref}).")
        try:
            return render_template("errors/500.html", ref=ref), 500
        except Exception:  # noqa: BLE001
            current_app.logger.exception("Page d'erreur 500 impossible à afficher")
            return f"Erreur interne (référence {ref}).", 500, {"Content-Type": "text/plain; charset=utf-8"}
