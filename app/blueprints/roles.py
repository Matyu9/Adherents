"""Rôles et droits (administrateurs uniquement)."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from ..extensions import db, fold
from ..models import Role
from ..permissions import PERMISSION_GROUPS, PERMISSIONS
from ..services import roles
from ..services.activity import log

bp = Blueprint("roles", __name__)


@bp.before_request
@login_required
def admins_only():
    # Qui modifie les rôles peut se donner n'importe quel droit : c'est réservé aux administrateurs.
    if not current_user.is_admin:
        abort(403)


class RoleForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=80)])
    description = StringField("Description", validators=[Optional(), Length(max=255)])

    def __init__(self, *args, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = current

    def validate_name(self, field):
        name = fold(" ".join(field.data.split()))
        for role in roles.all_roles():
            if fold(role.name) == name and (self._current is None or role.key != self._current.key):
                raise ValidationError("Un rôle porte déjà ce nom.")


def _apply(form, role):
    role.name = " ".join(form.name.data.split())
    role.description = (form.description.data or "").strip() or None
    if role.key != Role.ADMIN:  # l'administrateur a toujours tous les droits
        role.permissions = [p for p in PERMISSIONS if p in request.form.getlist("permissions")]


def _render(form, role, checked):
    return render_template("roles/form.html", form=form, role=role, groups=PERMISSION_GROUPS, checked=checked)


@bp.route("/roles")
def index():
    return render_template("roles/index.html", roles=roles.all_roles(), counts=roles.member_counts(),
                           total=len(PERMISSIONS))


@bp.route("/roles/nouveau", methods=["GET", "POST"])
def create():
    form = RoleForm()
    source = roles.find(request.args.get("depuis", ""))
    if form.validate_on_submit():
        role = Role(key=roles.new_key(form.name.data))
        _apply(form, role)
        db.session.add(role)
        log("role.create", f"Rôle créé : {role.name} ({len(role.permissions)} droit(s))")
        db.session.commit()
        flash(f"Rôle « {role.name} » créé.", "success")
        return redirect(url_for(".index"))
    if request.method == "GET" and source is not None:
        form.description.data = source.description
    checked = request.form.getlist("permissions") if request.method == "POST" else (
        source.permissions if source is not None and source.key != Role.ADMIN else [])
    return _render(form, None, set(checked))


@bp.route("/roles/<key>/modifier", methods=["GET", "POST"])
def edit(key):
    role = roles.get(key) or abort(404)
    form = RoleForm(obj=role, current=role)
    if form.validate_on_submit():
        _apply(form, role)
        log("role.update", f"Rôle modifié : {role.name}")
        db.session.commit()
        flash("Rôle enregistré.", "success")
        return redirect(url_for(".index"))
    checked = request.form.getlist("permissions") if request.method == "POST" else role.permissions
    return _render(form, role, set(PERMISSIONS) if key == Role.ADMIN else set(checked))


@bp.route("/roles/<key>/supprimer", methods=["POST"])
def delete(key):
    role = db.session.get(Role, key) or abort(404)
    if role.builtin:
        flash("Les rôles de base ne peuvent pas être supprimés.", "error")
    elif members := roles.member_counts().get(key):
        flash(f"Ce rôle est attribué à {members} personne(s) : changez d'abord leur rôle.", "error")
    else:
        db.session.delete(role)
        log("role.delete", f"Rôle supprimé : {role.name}")
        db.session.commit()
        flash(f"Rôle « {role.name} » supprimé.", "success")
    return redirect(url_for(".index"))
