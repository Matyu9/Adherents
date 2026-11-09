"""Professeurs : leurs fiches et leurs contrats, séparés de l'annuaire des adhérents.

Un professeur reste une personne de l'annuaire (une seule fiche s'il est aussi parent ou élève), mais on ne devient
professeur que depuis cet écran, et son contrat ne se voit qu'avec le droit teacher.manage.
"""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from wtforms import DateField, EmailField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from ..extensions import db
from ..models import Person, Teacher
from ..permissions import require
from ..services import directory
from ..services.activity import diff, log
from ..services.directory import DirectoryError
from .directory import EMAIL, _blank, _strip, _text

bp = Blueprint("teachers", __name__, url_prefix="/professeurs")

CONTRACT_FIELDS = ("status", "started_on", "left_on", "work_email", "notes")


class ContractForm(FlaskForm):
    status = SelectField("Contrat", default="", coerce=_text, validate_choice=False,
                         choices=[("", "—"), *((s, s) for s in Teacher.STATUSES)])
    started_on = DateField("Arrivée", validators=[Optional()])
    left_on = DateField("Départ", validators=[Optional()])
    work_email = EmailField("Email professionnel", validators=[Optional(), Length(max=255), EMAIL], filters=[_strip])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=5000)])

    def validate_left_on(self, field):
        if field.data and self.started_on.data and field.data < self.started_on.data:
            raise ValidationError("Le départ ne peut pas précéder l'arrivée.")

    def values(self):
        return {"status": self.status.data if self.status.data in Teacher.STATUSES else None,
                "started_on": self.started_on.data, "left_on": self.left_on.data,
                "work_email": _blank(self.work_email.data), "notes": _blank(self.notes.data)}


class NewTeacherForm(ContractForm):
    """Nouveau professeur qui n'est pas encore dans l'annuaire : sa fiche et son contrat en une fois."""
    civility = SelectField("Civilité", default="", coerce=_text, validate_choice=False,
                           choices=[("", "—"), *((c, c) for c in Person.CIVILITIES)])
    first_name = StringField("Prénom", validators=[DataRequired(), Length(max=80)])
    last_name = StringField("Nom", validators=[DataRequired(), Length(max=80)])
    email = EmailField("Email personnel", validators=[Optional(), Length(max=255), EMAIL], filters=[_strip])
    phone = StringField("Téléphone", validators=[Optional(), Length(max=30)])

    def validate_phone(self, field):
        if field.data:
            try:
                field.data = directory.normalize_phone(field.data)
            except DirectoryError as exc:
                raise ValidationError(str(exc)) from None


def _adult_born_before(today=None):
    """Né ce jour-là ou avant : majeur. Les enfants ne sont jamais proposés comme professeurs."""
    today = today or date.today()
    try:
        return today.replace(year=today.year - 18)
    except ValueError:  # 29 février
        return today.replace(year=today.year - 18, day=28)


def _teacher(person_id):
    person = db.session.get(Person, person_id) or abort(404)
    if person.teacher is None:
        abort(404)
    return person


@bp.before_request
def count_reads():
    from flask import g
    from flask_login import current_user

    from ..services import guard

    if request.method == "GET" and current_user.is_authenticated:
        g.write_lock = True
        guard.count_read()


@bp.after_request
def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("")
@require("teacher.manage")
def index():
    q = (request.args.get("q") or "").strip()
    gone = request.args.get("partis") == "1"
    query = directory.search(db.select(Person).join(Teacher), q)
    active = db.or_(Teacher.left_on.is_(None), Teacher.left_on > date.today())
    query = query.where(~active if gone else active)
    items = db.session.scalars(query.order_by(Person.last_name, Person.first_name)).all()
    return render_template("teachers/index.html", items=items, q=q, gone=gone)


@bp.route("/nouveau", methods=["GET", "POST"])
@require("teacher.manage")
def create():
    """Nouveau professeur. Avec ?personne=…, il est déjà dans l'annuaire (parent, élève) : seul le contrat s'ajoute."""
    existing = db.session.get(Person, request.args.get("personne", type=int) or 0)
    if existing is not None and existing.teacher is not None:
        return redirect(url_for(".detail", person_id=existing.id))
    if existing is not None and existing.is_minor:
        flash(f"{existing.full_name} est mineur(e) : ce ne peut pas être un professeur.", "error")
        return redirect(url_for(".create"))
    form = ContractForm() if existing else NewTeacherForm()
    q = (request.args.get("q") or "").strip()
    found = []
    if existing is None and q:
        query = directory.search(db.select(Person), q).where(~Person.teacher.has(), Person.archived_at.is_(None),
                                                              db.or_(Person.birth_date.is_(None),
                                                                     Person.birth_date <= _adult_born_before()))
        found = db.session.scalars(query.order_by(Person.last_name, Person.first_name).limit(20)).all()
    if form.validate_on_submit():
        person = existing
        if person is None:
            person = Person(civility=form.civility.data if form.civility.data in Person.CIVILITIES else None,
                            first_name=form.first_name.data.strip(), last_name=form.last_name.data.strip(),
                            email=_blank(form.email.data))
            db.session.add(person)
            if form.phone.data:
                directory.add_phone(person, form.phone.data, "portable")
        person.teacher = Teacher(**form.values())
        db.session.flush()
        log("teacher.create", f"Professeur ajouté : {person.full_name}" + (" (déjà dans l'annuaire)" if existing else ""),
            person=person)
        db.session.commit()
        flash(f"{person.full_name} est enregistré(e) comme professeur.", "success")
        return redirect(url_for(".detail", person_id=person.id))
    return render_template("teachers/form.html", form=form, existing=existing, person=None, q=q, found=found)


@bp.route("/<int:person_id>")
@require("teacher.manage")
def detail(person_id):
    person = _teacher(person_id)
    return render_template("teachers/detail.html", person=person, teacher=person.teacher)


@bp.route("/<int:person_id>/modifier", methods=["GET", "POST"])
@require("teacher.manage")
def edit(person_id):
    person = _teacher(person_id)
    form = ContractForm(obj=person.teacher)
    if form.validate_on_submit():
        changes = diff(person.teacher, CONTRACT_FIELDS, form.values(), mask=CONTRACT_FIELDS)
        if changes:
            log("teacher.update", f"Contrat de {person.full_name} modifié", changes=changes, person=person)
        db.session.commit()
        flash("Contrat enregistré.", "success")
        return redirect(url_for(".detail", person_id=person.id))
    return render_template("teachers/form.html", form=form, existing=person, person=person, q="", found=[])


@bp.route("/<int:person_id>/retirer", methods=["POST"])
@require("teacher.manage")
def remove(person_id):
    """Pour une erreur de saisie seulement : un départ s'indique par sa date, et l'historique reste."""
    person = _teacher(person_id)
    person.teacher = None
    log("teacher.delete", f"{person.full_name} n'est plus enregistré(e) comme professeur", person=person)
    db.session.commit()
    flash("Ce n'est plus un professeur. Sa fiche de l'annuaire est conservée.", "success")
    return redirect(url_for(".index"))
