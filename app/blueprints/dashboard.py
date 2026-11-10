"""Page d'accueil après la connexion."""
from flask import Blueprint, render_template
from flask_login import current_user, login_required

from ..permissions import can
from ..services import sessions

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@login_required
def index():
    stats = None
    if can(current_user, "settings.manage"):
        open_sessions, connected = sessions.stats()
        stats = {"sessions": open_sessions, "connected": connected}
    from .enrollment import my_dossiers
    return render_template("dashboard/index.html", stats=stats, dossiers=my_dossiers(current_user))
