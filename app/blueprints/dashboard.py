"""Accueil : ce qui compte maintenant pour la personne connectée (séances à venir, dossiers, notifications)."""

from datetime import date, datetime, timedelta

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Notification
from ..permissions import can
from ..services import sessions

bp = Blueprint("dashboard", __name__)

UPCOMING_DAYS = 7
RECENT_NOTIFICATIONS = 5


def _upcoming():
    """Séances des 7 prochains jours : celles que la personne donne, et celles des élèves qu'elle suit."""
    from ..services.lessons import personal_lessons

    today = date.today()
    now = datetime.now()
    rows = []
    for lesson, begin, finish, who in personal_lessons(current_user, today, today + timedelta(days=UPCOMING_DAYS)):
        if datetime.combine(lesson.day, finish) < now:
            continue
        student = who.membership.person.first_name if who is not None and who != "remplacement" else None
        rows.append({"lesson": lesson, "begin": begin, "end": finish, "student": student, "substitute": who == "remplacement"})
    return sorted(rows, key=lambda r: (r["lesson"].day, r["begin"]))


@bp.route("/")
@login_required
def index():
    stats = None
    if can(current_user, "settings.manage"):
        open_sessions, connected = sessions.stats()
        stats = {"sessions": open_sessions, "connected": connected}
    from .enrollment import my_dossiers

    notifications = db.session.scalars(
        db.select(Notification)
        .where(
            Notification.account_id == current_user.id,
            Notification.read.is_(False),
            Notification.dismissed_at.is_(None),
        )
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(RECENT_NOTIFICATIONS)
    ).all()
    return render_template(
        "dashboard/index.html",
        stats=stats,
        dossiers=my_dossiers(current_user),
        upcoming=_upcoming(),
        notifications=notifications,
        days=UPCOMING_DAYS,
    )


@bp.route("/mon-espace")
@login_required
def old_member_space():
    """Ancienne adresse de Ma famille, gardée pour les liens déjà donnés (l'Inventaire, des emails)."""
    from flask import redirect, url_for

    return redirect(url_for("member.index"), 301)
