"""Notifications du compte connecté (la cloche)."""
from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Notification

bp = Blueprint("notifications", __name__, url_prefix="/notifications")

LIMIT = 100


@bp.route("")
@login_required
def index():
    items = db.session.scalars(db.select(Notification).where(Notification.account_id == current_user.id)
                               .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(LIMIT)).all()
    return render_template("notifications/index.html", notifications=items)


@bp.route("/tout-lu", methods=["POST"])
@login_required
def mark_all_read():
    db.session.execute(db.update(Notification).where(Notification.account_id == current_user.id,
                                                     Notification.read.is_(False)).values(read=True))
    db.session.commit()
    flash("Notifications marquées comme lues.", "success")
    return redirect(url_for(".index"))
