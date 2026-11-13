"""Notifications du compte connecté (la cloche)."""

from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Notification

bp = Blueprint("notifications", __name__, url_prefix="/notifications")

LIMIT = 100


@bp.route("")
@login_required
def index():
    items = db.session.scalars(
        db.select(Notification)
        .where(Notification.account_id == current_user.id, Notification.dismissed_at.is_(None))
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(LIMIT)
    ).all()
    return render_template("notifications/index.html", notifications=items)


@bp.route("/tout-lu", methods=["POST"])
@login_required
def mark_all_read():
    db.session.execute(
        db.update(Notification)
        .where(
            Notification.account_id == current_user.id,
            Notification.read.is_(False),
            Notification.dismissed_at.is_(None),
        )
        .values(read=True)
    )
    db.session.commit()
    flash("Notifications marquées comme lues.", "success")
    return redirect(url_for(".index"))


@bp.route("/<int:notification_id>/supprimer", methods=["POST"])
@login_required
def dismiss(notification_id):
    notification = db.session.get(Notification, notification_id)
    if notification is None or notification.account_id != current_user.id or notification.dismissed_at:
        abort(404)
    notification.dismissed_at = datetime.now()
    notification.read = True
    db.session.commit()
    flash("Notification supprimée.", "success")
    return redirect(url_for(".index"))


@bp.route("/supprimer-les-lues", methods=["POST"])
@login_required
def dismiss_read():
    count = db.session.execute(
        db.update(Notification)
        .where(
            Notification.account_id == current_user.id,
            Notification.read.is_(True),
            Notification.dismissed_at.is_(None),
        )
        .values(dismissed_at=datetime.now())
    ).rowcount
    db.session.commit()
    flash(f"{count} notification{'s' if count > 1 else ''} supprimée{'s' if count > 1 else ''}.", "success")
    return redirect(url_for(".index"))
