"""État de l'installation, réservé à qui gère les paramètres (404 pour les autres : la route ne se dévoile pas)."""
from functools import wraps

from flask import Blueprint, abort, jsonify, render_template
from flask_login import current_user

from ..permissions import can
from ..services import health

bp = Blueprint("health", __name__)


def _admin_only(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not can(current_user, "settings.manage"):
            abort(404)
        return view(*args, **kwargs)

    return wrapped


def _code(result):
    return 503 if result["status"] == health.ERROR else 200


@bp.route("/health")
@_admin_only
def as_json():
    result = health.report()
    return jsonify(result), _code(result)


@bp.route("/sante")
@_admin_only
def page():
    result = health.report()
    return render_template("settings/health.html", result=result), _code(result)
