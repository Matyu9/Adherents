"""Groupes de comptes. La clé est fixée à la création : les applis clientes s'y fieront."""
from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from ..extensions import db, fold
from ..models import AccessGroup
from ..permissions import require
from ..services import groups, oidc
from ..services.activity import log

bp = Blueprint("groups", __name__, url_prefix="/groupes")


class GroupForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=80)])
    description = StringField("Description", validators=[Optional(), Length(max=255)])

    def __init__(self, *args, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = current

    def validate_name(self, field):
        name = fold(" ".join(field.data.split()))
        for group in groups.all_groups():
            if fold(group.name) == name and group is not self._current:
                raise ValidationError("Un groupe porte déjà ce nom.")


def _apply(form, group):
    group.name = " ".join(form.name.data.split())
    group.description = (form.description.data or "").strip() or None


@bp.route("")
@require("group.manage")
def index():
    return render_template("groups/index.html", groups=groups.all_groups(), counts=groups.member_counts(),
                           roles_of={g.id: groups.roles_using(g) for g in groups.all_groups()})


@bp.route("/nouveau", methods=["GET", "POST"])
@require("group.manage")
def create():
    form = GroupForm()
    if form.validate_on_submit():
        group = AccessGroup(key=groups.new_key(form.name.data))
        _apply(form, group)
        db.session.add(group)
        log("group.create", f"Groupe créé : {group.name}")
        db.session.commit()
        flash(f"Groupe « {group.name} » créé.", "success")
        return redirect(url_for(".index"))
    return render_template("groups/form.html", form=form, group=None)


@bp.route("/<int:group_id>/modifier", methods=["GET", "POST"])
@require("group.manage")
def edit(group_id):
    group = db.session.get(AccessGroup, group_id) or abort(404)
    form = GroupForm(obj=group, current=group)
    if form.validate_on_submit():
        _apply(form, group)
        log("group.update", f"Groupe modifié : {group.name}")
        db.session.commit()
        flash("Groupe enregistré.", "success")
        return redirect(url_for(".index"))
    return render_template("groups/form.html", form=form, group=group)


@bp.route("/<int:group_id>/supprimer", methods=["POST"])
@require("group.manage")
def delete(group_id):
    group = db.session.get(AccessGroup, group_id) or abort(404)
    used_by = oidc.clients_using(group)
    roles_using = groups.roles_using(group)
    if group.members:
        flash(f"Ce groupe contient {len(group.members)} compte(s) : retirez-les d'abord.", "error")
    elif used_by:
        flash(f"Ce groupe est utilisé par {', '.join(c.name for c in used_by)} : retirez-le d'abord.", "error")
    elif roles_using:
        flash(f"Ce groupe est donné par le rôle {', '.join(r.name for r in roles_using)} : retirez-le d'abord.",
              "error")
    else:
        db.session.delete(group)
        log("group.delete", f"Groupe supprimé : {group.name}")
        db.session.commit()
        flash(f"Groupe « {group.name} » supprimé.", "success")
    return redirect(url_for(".index"))
