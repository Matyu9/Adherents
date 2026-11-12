"""Catalogue : disciplines, niveaux, sites et salles communs à toutes les saisons, puis les cours de chaque saison et
les disciplines de chaque professeur. Voir docs/catalogue.md."""

import csv
import io
from datetime import date

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    IntegerField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
    TimeField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError
from wtforms.widgets import CheckboxInput, ListWidget

from ..extensions import db, fold
from ..filters import date_fr
from ..models import (
    Course,
    Discipline,
    DisciplineType,
    Enrollment,
    Level,
    Membership,
    Room,
    Season,
    Site,
    Teacher,
    WaitlistEntry,
)
from ..permissions import can, require
from ..services import catalog, courses, directory, registration
from ..services.activity import diff, log
from ..services.catalog import CatalogError
from ..services.courses import CourseError
from .directory import _blank, _strip

bp = Blueprint("catalog", __name__, url_prefix="/catalogue")

KINDS = "any(types, disciplines, niveaux, sites, salles)"
COURSE_FIELDS = (
    "discipline_id",
    "level_id",
    "group_label",
    "teacher_id",
    "weekday",
    "starts_at",
    "ends_at",
    "room_id",
    "capacity",
    "individual",
    "marked_full",
    "notes",
)


class Choice(SelectField):
    """Liste déroulante dont la valeur doit être l'une des options proposées (une valeur archivée ne l'est plus)."""

    def pre_validate(self, form):
        if not any(match for _, _, match, *_ in self.iter_choices()):
            raise ValidationError("Ce choix n'est pas proposé.")


def _optional_int(value):
    """Liste déroulante facultative : « — » (chaîne vide) → None."""
    if value in (None, ""):
        return None
    return int(value)


def _selected_season():
    """Saison demandée (?saison=), sinon la saison en cours, sinon la plus récente."""
    seasons = directory.seasons()
    wanted = request.args.get("saison", type=int)
    season = next((s for s in seasons if s.id == wanted), None) if wanted else None
    return season or directory.current_season() or (seasons[0] if seasons else None), seasons


# ---------------------------------------------------------------------------
# Formulaires
# ---------------------------------------------------------------------------


class CourseForm(FlaskForm):
    discipline_id = Choice("Discipline", coerce=_optional_int, validators=[DataRequired("Choisissez la discipline.")])
    level_id = Choice("Niveau", coerce=_optional_int)
    group_label = StringField(
        "Groupe",
        validators=[Optional(), Length(max=80)],
        filters=[_strip],
        description="Pour distinguer deux cours de même discipline et niveau : « groupe A », « ados »…",
    )
    teacher_id = Choice("Professeur", coerce=_optional_int)
    weekday = Choice(
        "Jour", coerce=_optional_int, choices=[("", "—"), *((str(i), name) for i, name in enumerate(Course.WEEKDAYS))]
    )
    starts_at = TimeField("Début", validators=[Optional()])
    ends_at = TimeField("Fin", validators=[Optional()])
    room_id = Choice("Salle", coerce=_optional_int)
    capacity = IntegerField(
        "Effectif maximum",
        validators=[Optional(), NumberRange(min=1, max=500, message="Entre 1 et 500.")],
        description="Vide : pas de limite.",
    )
    individual = BooleanField("Cours individuel : chaque élève a son heure de passage dans le créneau")
    marked_full = BooleanField("Complet : ne plus proposer ce cours aux inscriptions")
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=5000)])

    def __init__(self, *args, season, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._season = season
        keep_discipline = course.discipline if course else None
        self.discipline_id.choices = [("", "—")] + [
            (str(d.id), d.name + (" (archivée)" if d.archived else ""))
            for d in _with(catalog.disciplines(), keep_discipline)
        ]
        self.level_id.choices = [("", "—")] + [
            (str(lv.id), lv.name + (" (archivé)" if lv.archived else ""))
            for lv in _with(catalog.levels(), course.level if course else None)
        ]
        self.room_id.choices = [("", "—")] + [
            (str(r.id), r.label + ("" if catalog.room_open(r) else " (fermée)"))
            for r in _with(catalog.rooms(), course.room if course else None)
        ]
        self.teacher_id.choices = [("", "À trouver")] + [
            (
                str(t.person_id),
                t.person.sort_name + (f" (parti le {date_fr(t.left_on)})" if t.leaves_during(season) else ""),
            )
            for t in catalog.teachers_for(season, keep=course.teacher if course else None)
        ]

    def validate(self, extra_validators=None):
        """L'horaire se vérifie ici : un champ vide arrête ses propres validateurs (Optional)."""
        ok = super().validate(extra_validators)
        start, end = self.starts_at.data, self.ends_at.data
        if not self.starts_at.errors and not self.ends_at.errors:
            if (start is None) != (end is None):
                self.ends_at.errors.append("Indiquez le début et la fin, ou aucun des deux.")
                ok = False
            elif start is not None and end <= start:
                self.ends_at.errors.append("La fin doit suivre le début.")
                ok = False
        if start is not None and self.weekday.data is None and not self.weekday.errors:
            self.weekday.errors.append("Un horaire demande un jour.")
            ok = False
        return ok

    def values(self):
        return {
            "discipline_id": self.discipline_id.data,
            "level_id": self.level_id.data,
            "group_label": _blank(self.group_label.data),
            "teacher_id": self.teacher_id.data,
            "weekday": self.weekday.data,
            "starts_at": self.starts_at.data,
            "ends_at": self.ends_at.data,
            "room_id": self.room_id.data,
            "capacity": self.capacity.data,
            "individual": self.individual.data,
            "marked_full": self.marked_full.data,
            "notes": _blank(self.notes.data),
        }


def _with(items, keep):
    """La liste des valeurs ouvertes, plus celle déjà choisie si elle a été archivée depuis."""
    return items + [keep] if keep is not None and keep not in items else items


class DisciplineTypeForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=40)], filters=[_strip])
    position = IntegerField(
        "Ordre",
        default=0,
        validators=[Optional(), NumberRange(min=0, max=9999, message="Entre 0 et 9999.")],
        description="Les types, et leurs disciplines avec eux, sont classés par ordre croissant, puis par nom.",
    )

    def values(self):
        return {"name": self.name.data, "position": self.position.data or 0}


class DisciplineForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=80)], filters=[_strip])
    type_id = Choice(
        "Type",
        coerce=_optional_int,
        validators=[Optional()],
        description="Facultatif. Les types se créent dans la liste « Types de discipline ».",
    )

    def __init__(self, *args, obj=None, **kwargs):
        super().__init__(*args, obj=obj, **kwargs)
        types = _with(catalog.discipline_types(), obj.type if obj else None)
        self.type_id.choices = [("", "—")] + [(str(t.id), t.name) for t in types]

    def values(self):
        return {"name": self.name.data, "type_id": self.type_id.data}


class LevelForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=60)], filters=[_strip])
    position = IntegerField(
        "Ordre",
        default=0,
        validators=[Optional(), NumberRange(min=0, max=9999, message="Entre 0 et 9999.")],
        description="Les niveaux sont classés par ordre croissant, puis par nom.",
    )

    def values(self):
        return {"name": self.name.data, "position": self.position.data or 0}


class SiteForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=80)], filters=[_strip])
    address = StringField("Adresse", validators=[Optional(), Length(max=255)], filters=[_strip])

    def values(self):
        return {"name": self.name.data, "address": _blank(self.address.data)}


class RoomForm(FlaskForm):
    site_id = Choice("Site", coerce=_optional_int, validators=[DataRequired("Choisissez le site.")])
    name = StringField("Nom", validators=[DataRequired(), Length(max=80)], filters=[_strip])

    def __init__(self, *args, room=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.site_id.choices = [("", "—")] + [
            (str(s.id), s.name) for s in _with(catalog.sites(), room.site if room else None)
        ]

    def values(self):
        return {"site_id": self.site_id.data, "name": self.name.data}


# Chaque liste de référence : modèle, formulaire, libellés, et champ qui doit être unique (avec sa portée).
REFERENCES = {
    "types": {
        "model": DisciplineType,
        "form": DisciplineTypeForm,
        "one": "type de discipline",
        "title": "Types de discipline",
        "fields": ("name", "position"),
        "used_for": "les nouvelles disciplines",
    },
    "disciplines": {
        "model": Discipline,
        "form": DisciplineForm,
        "one": "discipline",
        "title": "Disciplines",
        "fields": ("name", "type_id"),
    },
    "niveaux": {"model": Level, "form": LevelForm, "one": "niveau", "title": "Niveaux", "fields": ("name", "position")},
    "sites": {"model": Site, "form": SiteForm, "one": "site", "title": "Sites", "fields": ("name", "address")},
    "salles": {"model": Room, "form": RoomForm, "one": "salle", "title": "Salles", "fields": ("site_id", "name")},
}


def _name_taken(kind, values, item):
    """Même nom, sans tenir compte des accents ni de la casse : « Flute » et « Flûte » sont la même discipline."""
    model = REFERENCES[kind]["model"]
    query = db.select(model)
    if model is Room:
        query = query.where(Room.site_id == values["site_id"])
    return any(other is not item and fold(other.name) == fold(values["name"]) for other in db.session.scalars(query))


def _item_label(item):
    return item.label if isinstance(item, Room) else item.name


# ---------------------------------------------------------------------------
# Cours d'une saison
# ---------------------------------------------------------------------------


@bp.route("")
@require("catalog.view")
def index():
    season, seasons = _selected_season()
    if season is None:
        return render_template(
            "catalog/index.html", season=None, seasons=[], items=[], conflicts={}, filters={}, enrolled={}
        )
    filters = {
        "discipline": request.args.get("discipline", type=int),
        "prof": request.args.get("prof") if request.args.get("prof") == "aucun" else request.args.get("prof", type=int),
        "jour": request.args.get("jour", type=int),
        "site": request.args.get("site", type=int),
    }
    if filters["jour"] is not None and not 0 <= filters["jour"] <= 6:
        filters["jour"] = None
    query = catalog.course_query(season, filters["discipline"], filters["prof"], filters["jour"], filters["site"])
    items = catalog.sorted_courses(db.session.scalars(query))
    every = db.session.scalars(catalog.course_query(season)).all()
    return render_template(
        "catalog/index.html",
        season=season,
        seasons=seasons,
        items=items,
        filters=filters,
        conflicts=catalog.season_conflicts(every),
        total=len(every),
        enrolled=registration.enrolled_counts(season),
        disciplines=catalog.disciplines(include_archived=True),
        teachers=catalog.teachers_for(season),
        sites=catalog.sites(include_archived=True),
        weekdays=Course.WEEKDAYS,
    )


def _save_course(course, form, season):
    """Enregistre le formulaire, note la discipline du professeur et prévient des chevauchements."""
    changes = diff(course, COURSE_FIELDS, form.values(), mask=("notes",))
    db.session.flush()
    db.session.refresh(course)
    if catalog.note_discipline(course):
        flash(
            f"{course.discipline.name} ajoutée aux disciplines de {course.teacher.person.full_name} "
            f"pour {season.label}.",
            "info",
        )
    others = db.session.scalars(catalog.course_query(season, weekday=course.weekday)).all()
    for other, reason in catalog.conflicts(course, others):
        flash(f"Chevauchement ({reason}) avec {catalog.describe(other)}.", "warning")
    return changes


@bp.route("/cours/nouveau", methods=["GET", "POST"])
@require("catalog.edit")
def create_course():
    season, _ = _selected_season()
    if season is None:
        flash("Créez d'abord une saison.", "error")
        return redirect(url_for(".index"))
    form = CourseForm(season=season)
    copy = db.session.get(Course, request.args.get("depuis", type=int) or 0) if request.method == "GET" else None
    if copy and copy.season_id == season.id:  # « dupliquer » : un autre groupe du même cours
        form = CourseForm(season=season, obj=copy, formdata=None)
        form.marked_full.data = False
    if form.validate_on_submit():
        course = Course(season=season, discipline_id=form.discipline_id.data)
        db.session.add(course)
        _save_course(course, form, season)
        log("course.create", f"Cours ajouté : {catalog.describe(course)}")
        db.session.commit()
        flash("Cours ajouté.", "success")
        return redirect(url_for(".index", saison=season.id))
    return render_template("catalog/course_form.html", form=form, season=season, course=None)


def _course(course_id):
    return db.session.get(Course, course_id) or abort(404)


@bp.route("/cours/<int:course_id>/modifier", methods=["GET", "POST"])
@require("catalog.edit")
def edit_course(course_id):
    course = _course(course_id)
    season = course.season
    form = CourseForm(obj=course, season=season, course=course)
    if form.validate_on_submit():
        if changes := _save_course(course, form, season):
            log("course.update", f"Cours modifié : {catalog.describe(course)}", changes=changes)
            if "capacity" in changes or "marked_full" in changes:
                courses.announce_free_place(course)
        db.session.commit()
        flash("Cours enregistré.", "success")
        return redirect(url_for(".index", saison=season.id))
    return render_template("catalog/course_form.html", form=form, season=season, course=course)


@bp.route("/cours/<int:course_id>/supprimer", methods=["POST"])
@require("catalog.edit")
def delete_course(course_id):
    course = _course(course_id)
    season_id = course.season_id
    if course.enrollments:
        flash(
            f"{len(course.enrollments)} élève(s) sont inscrits à ce cours : il ne peut pas être supprimé. "
            "Fermez-le aux inscriptions s'il ne doit plus être proposé.",
            "error",
        )
        return redirect(url_for(".index", saison=season_id))
    if course.waitlist:
        flash("Ce cours a une liste d'attente : il ne peut pas être supprimé.", "error")
        return redirect(url_for(".index", saison=season_id))
    log("course.delete", f"Cours supprimé : {catalog.describe(course)}")
    db.session.delete(course)
    db.session.commit()
    flash("Cours supprimé.", "success")
    return redirect(url_for(".index", saison=season_id))


# ---------------------------------------------------------------------------
# Élèves d'un cours : inscrire, transférer, noter un abandon (voir docs/cours.md)
# ---------------------------------------------------------------------------


class _DatedForm(FlaskForm):
    on = DateField("À partir du", validators=[DataRequired("Indiquez la date.")], default=date.today)


class EnrollStudentForm(_DatedForm):
    membership_id = Choice("Élève", coerce=_optional_int, validators=[DataRequired("Choisissez l'élève.")])


class EnrollCourseForm(_DatedForm):
    course_id = Choice("Cours", coerce=_optional_int, validators=[DataRequired("Choisissez le cours.")])


class WithdrawForm(_DatedForm):
    on = DateField("Date de l'abandon", validators=[DataRequired("Indiquez la date.")], default=date.today)


def _course_choices(form, course_list, counts):
    form.course_id.choices = [("", "—")] + [
        (str(c.id), catalog.describe(c) + (" — complet" if c.is_full(counts.get(c.id, 0)) else "")) for c in course_list
    ]


def _back_to(enrollment_or_course, person=None):
    """Retour à la fiche de l'élève (?retour=fiche) ou, par défaut, à la page du cours."""
    if request.values.get("retour") == "fiche" and person is not None:
        return redirect(url_for("directory.person", person_id=person.id) + "#adhesions")
    course = enrollment_or_course if isinstance(enrollment_or_course, Course) else enrollment_or_course.course
    return redirect(url_for(".course", course_id=course.id))


def _flash_all(messages, category="warning"):
    for message in messages:
        flash(message, category)


@bp.route("/cours/<int:course_id>")
@require("catalog.view")
def course(course_id):
    if not can(current_user, "directory.view"):  # la liste des élèves vient de l'annuaire
        abort(403)
    record = _course(course_id)
    active, gone = courses.roster(record)
    count = len(active)
    slots, free = courses.planning(record) if record.individual else ([], [])
    if record.individual:
        active = [e for e, _ in slots]
    return render_template(
        "catalog/course.html",
        course=record,
        season=record.season,
        active=active,
        gone=gone,
        count=count,
        full=record.is_full(count),
        guardians={e.id: courses.guardians_of(e.membership.person) for e in active},
        waiting_guardians={w.id: courses.guardians_of(w.membership.person) for w in courses.waitlist(record)},
        recipients=courses.recipients(record),
        withdraw_form=WithdrawForm(),
        slot_warnings={e.id: w for e, w in slots},
        waiting=courses.waitlist(record),
        waitlist_candidates=[
            m for m in courses.candidates(record) if not any(w.course_id == record.id and w.waiting for w in m.waitlist)
        ],
        free=free,
    )


@bp.route("/cours/<int:course_id>/eleves.csv")
@require("catalog.view")
def course_csv(course_id):
    if not can(current_user, "directory.view"):
        abort(403)
    record = _course(course_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(courses.ROSTER_COLUMNS)
    writer.writerows(courses.roster_rows(record))
    response = Response("\ufeff" + buffer.getvalue(), mimetype="text/csv")  # BOM : Excel lit les accents
    response.headers["Content-Disposition"] = f'attachment; filename="eleves-cours-{record.id}.csv"'
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/cours/<int:course_id>/inscrire", methods=["GET", "POST"])
@require("enrollment.edit")
def enroll_student(course_id):
    """Depuis la page du cours : choisir l'élève parmi les adhérents de la saison."""
    record = _course(course_id)
    form = EnrollStudentForm()
    form.membership_id.choices = [("", "—")] + [
        (
            str(m.id),
            m.person.sort_name + (f" ({m.person.age(record.season.starts_on)} ans)" if m.person.birth_date else ""),
        )
        for m in courses.candidates(record)
    ]
    if form.validate_on_submit():
        membership = db.session.get(Membership, form.membership_id.data) or abort(404)
        try:
            _, warnings = courses.enroll(membership, record, form.on.data)
        except CourseError as exc:
            form.on.errors.append(str(exc))
        else:
            db.session.commit()
            flash(f"{membership.person.full_name} est inscrit(e) au cours.", "success")
            _flash_all(warnings)
            return redirect(url_for(".course", course_id=record.id))
    return render_template("catalog/enroll_form.html", form=form, course=record, membership=None, enrollment=None)


@bp.route("/adhesions/<int:membership_id>/inscrire", methods=["GET", "POST"])
@require("enrollment.edit")
def enroll_in_course(membership_id):
    """Depuis la fiche de l'élève : choisir le cours de la saison."""
    membership = db.session.get(Membership, membership_id) or abort(404)
    form = EnrollCourseForm()
    _course_choices(form, *courses.open_courses(membership))
    if form.validate_on_submit():
        record = db.session.get(Course, form.course_id.data) or abort(404)
        try:
            _, warnings = courses.enroll(membership, record, form.on.data)
        except CourseError as exc:
            form.on.errors.append(str(exc))
        else:
            db.session.commit()
            flash(f"{membership.person.full_name} est inscrit(e) à {record.title}.", "success")
            _flash_all(warnings)
            return redirect(url_for("directory.person", person_id=membership.person_id) + "#adhesions")
    return render_template("catalog/enroll_form.html", form=form, course=None, membership=membership, enrollment=None)


def _enrollment(enrollment_id):
    return db.session.get(Enrollment, enrollment_id) or abort(404)


@bp.route("/inscriptions/<int:enrollment_id>/transferer", methods=["GET", "POST"])
@require("enrollment.edit")
def transfer(enrollment_id):
    enrollment = _enrollment(enrollment_id)
    membership = enrollment.membership
    if not enrollment.active:
        flash("Cette inscription est déjà terminée.", "error")
        return _back_to(enrollment, membership.person)
    form = EnrollCourseForm()
    _course_choices(form, *courses.open_courses(membership, exclude=enrollment.course))
    if form.validate_on_submit():
        record = db.session.get(Course, form.course_id.data) or abort(404)
        try:
            new, warnings = courses.transfer(enrollment, record, form.on.data)
        except CourseError as exc:
            form.on.errors.append(str(exc))
        else:
            db.session.commit()
            flash(f"{membership.person.full_name} passe à {record.title}.", "success")
            _flash_all(warnings)
            return _back_to(new, membership.person)
    return render_template(
        "catalog/enroll_form.html", form=form, course=enrollment.course, membership=membership, enrollment=enrollment
    )


class SlotForm(FlaskForm):
    starts_at = TimeField("Heure de passage", validators=[Optional()])
    minutes = IntegerField("Durée (min)", validators=[Optional()])


@bp.route("/inscriptions/<int:enrollment_id>/passage", methods=["POST"])
@require("enrollment.edit")
def set_slot(enrollment_id):
    enrollment = _enrollment(enrollment_id)
    form = SlotForm()
    if not form.validate_on_submit():
        flash("Heure ou durée illisible.", "error")
        return redirect(url_for(".course", course_id=enrollment.course_id))
    try:
        warnings = courses.set_slot(enrollment, form.starts_at.data, form.minutes.data)
    except CourseError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash(f"Passage de {enrollment.membership.person.full_name} enregistré.", "success")
        _flash_all(warnings)
    return redirect(url_for(".course", course_id=enrollment.course_id) + f"#e{enrollment.id}")


@bp.route("/inscriptions/<int:enrollment_id>/abandon", methods=["POST"])
@require("enrollment.edit")
def withdraw(enrollment_id):
    enrollment = _enrollment(enrollment_id)
    person = enrollment.membership.person
    form = WithdrawForm()
    if not form.validate_on_submit():
        flash("Indiquez une date valide.", "error")
        return _back_to(enrollment, person)
    try:
        courses.withdraw(enrollment, form.on.data)
    except CourseError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash(f"Abandon de {person.full_name} noté. Son adhésion reste active.", "success")
    return _back_to(enrollment, person)


def _entry(entry_id):
    return db.session.get(WaitlistEntry, entry_id) or abort(404)


def _to_waitlist(course):
    return redirect(url_for(".course", course_id=course.id) + "#attente")


@bp.route("/cours/<int:course_id>/attente", methods=["POST"])
@require("enrollment.edit")
def add_to_waitlist(course_id):
    record = _course(course_id)
    membership = db.session.get(Membership, request.form.get("membership_id", type=int) or 0)
    if membership is None:
        flash("Choisissez l'élève.", "error")
        return _to_waitlist(record)
    try:
        courses.add_to_waitlist(membership, record)
    except CourseError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash(f"{membership.person.full_name} est en liste d'attente.", "success")
    return _to_waitlist(record)


@bp.route("/attente/<int:entry_id>/<any(monter, descendre):direction>", methods=["POST"])
@require("enrollment.edit")
def move_waitlist(entry_id, direction):
    entry = _entry(entry_id)
    try:
        courses.move_in_waitlist(entry, -1 if direction == "monter" else 1)
    except CourseError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
    return _to_waitlist(entry.course)


@bp.route("/attente/<int:entry_id>/retirer", methods=["POST"])
@require("enrollment.edit")
def remove_from_waitlist(entry_id):
    entry = _entry(entry_id)
    try:
        courses.remove_from_waitlist(entry)
    except CourseError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash(f"{entry.membership.person.full_name} est retiré(e) de la liste d'attente.", "success")
    return _to_waitlist(entry.course)


@bp.route("/attente/<int:entry_id>/inscrire", methods=["POST"])
@require("enrollment.edit")
def enroll_from_waitlist(entry_id):
    entry = _entry(entry_id)
    form = _DatedForm()
    if not form.validate_on_submit():
        flash("Indiquez une date valide.", "error")
        return _to_waitlist(entry.course)
    try:
        _, warnings = courses.enroll_from_waitlist(entry, form.on.data)
    except CourseError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash(f"{entry.membership.person.full_name} est inscrit(e) au cours.", "success")
        _flash_all(warnings)
    return redirect(url_for(".course", course_id=entry.course_id))


# ---------------------------------------------------------------------------
# Copie d'une saison sur la suivante
# ---------------------------------------------------------------------------


@bp.route("/copier", methods=["GET", "POST"])
@require("catalog.edit")
def copy():
    """GET : choix des saisons et aperçu de la copie. POST : la copie, une fois l'aperçu vu."""
    seasons = directory.seasons()
    source = db.session.get(Season, request.values.get("depuis", type=int) or 0)
    target = db.session.get(Season, request.values.get("vers", type=int) or 0)
    if request.method == "GET" and source is None and target is None and len(seasons) >= 2:
        target, source = seasons[0], seasons[1]  # par défaut : de l'avant-dernière vers la plus récente
    plan = catalog.copy_plan(source, target) if source or target else None
    if request.method == "POST":
        try:
            plan = catalog.copy_season(source, target)
        except CatalogError as exc:
            flash(str(exc), "error")
            return redirect(url_for(".copy", depuis=source.id if source else None, vers=target.id if target else None))
        log(
            "catalog.copy",
            f"Catalogue {source.label} recopié sur {target.label} : {len(plan.copied)} cours, "
            f"{len(plan.skipped)} laissé(s) de côté, {len(plan.disciplines)} discipline(s) de professeurs",
        )
        db.session.commit()
        flash(
            f"{len(plan.copied)} cours recopiés sur {target.label}. Vérifiez les cours sans professeur ou sans salle.",
            "success",
        )
        return redirect(url_for(".index", saison=target.id))
    return render_template("catalog/copy.html", seasons=seasons, source=source, target=target, plan=plan)


# ---------------------------------------------------------------------------
# Disciplines des professeurs, par saison
# ---------------------------------------------------------------------------


@bp.route("/professeurs")
@require("catalog.view")
def teachers():
    season, seasons = _selected_season()
    rows = []
    if season is not None:
        counts = dict(
            db.session.execute(
                db.select(Course.teacher_id, db.func.count(Course.id))
                .where(Course.season_id == season.id)
                .group_by(Course.teacher_id)
            ).all()
        )
        rows = [
            (t, catalog.disciplines_of(t, season), counts.get(t.person_id, 0)) for t in catalog.teachers_for(season)
        ]
        # Partis entre-temps, mais avec des cours cette saison : à remplacer, donc à montrer.
        rows += [
            (t, catalog.disciplines_of(t, season), counts[t.person_id])
            for t in db.session.scalars(db.select(Teacher).where(Teacher.person_id.in_(counts.keys())))
            if t not in {r[0] for r in rows}
        ]
    return render_template("catalog/teachers.html", season=season, seasons=seasons, rows=rows)


class TeacherDisciplinesForm(FlaskForm):
    disciplines = SelectMultipleField(
        "Disciplines", coerce=int, widget=ListWidget(prefix_label=False), option_widget=CheckboxInput()
    )


@bp.route("/professeurs/<int:person_id>", methods=["GET", "POST"])
@require("catalog.edit")
def teacher_disciplines(person_id):
    teacher = db.session.get(Teacher, person_id) or abort(404)
    season, _ = _selected_season()
    if season is None:
        abort(404)
    current = catalog.disciplines_of(teacher, season)
    form = TeacherDisciplinesForm()
    form.disciplines.choices = [
        (d.id, d.name + (" (archivée)" if d.archived else ""))
        for d in catalog.disciplines() + [d for d in current if d.archived]
    ]
    if request.method == "GET":
        form.disciplines.data = [d.id for d in current]
    if form.validate_on_submit():
        wanted = [db.session.get(Discipline, i) for i in form.disciplines.data]
        try:
            added, removed = catalog.set_disciplines(teacher, season, wanted)
        except CatalogError as exc:
            flash(str(exc), "error")
            return redirect(url_for(".teacher_disciplines", person_id=person_id, saison=season.id))
        if added or removed:
            changes = {
                "disciplines": [
                    ", ".join(d.name for d in current) or None,
                    ", ".join(d.name for d in catalog.disciplines_of(teacher, season)) or None,
                ]
            }
            log(
                "teacher.disciplines",
                f"Disciplines de {teacher.person.full_name} en {season.label} modifiées",
                changes=changes,
                person=teacher.person,
            )
        db.session.commit()
        flash("Disciplines enregistrées.", "success")
        return redirect(url_for(".teachers", saison=season.id))
    courses = catalog.sorted_courses(c for c in teacher.courses if c.season_id == season.id)
    return render_template("catalog/teacher_form.html", form=form, teacher=teacher, season=season, courses=courses)


# ---------------------------------------------------------------------------
# Listes de référence : types de discipline, disciplines, niveaux, sites, salles
# ---------------------------------------------------------------------------


@bp.route("/referentiels")
@require("catalog.view")
def references():
    lists = {
        "types": catalog.discipline_types(include_archived=True),
        "disciplines": catalog.disciplines(include_archived=True),
        "niveaux": catalog.levels(include_archived=True),
        "sites": catalog.sites(include_archived=True),
    }
    return render_template("catalog/references.html", lists=lists, usage=catalog.usage)


def _reference(kind, item_id):
    return db.session.get(REFERENCES[kind]["model"], item_id) or abort(404)


@bp.route(f"/<{KINDS}:kind>/nouveau", methods=["GET", "POST"])
@require("catalog.edit")
def create_item(kind):
    spec = REFERENCES[kind]
    form = spec["form"]()
    if kind == "salles" and request.method == "GET" and (site_id := request.args.get("site", type=int)):
        form.site_id.data = site_id
    if form.validate_on_submit():
        values = form.values()
        if _name_taken(kind, values, None):
            form.name.errors.append("Ce nom existe déjà" + (" sur ce site." if kind == "salles" else "."))
        else:
            item = spec["model"](**values)
            db.session.add(item)
            db.session.flush()
            log(f"{spec['model'].__tablename__}.create", f"{spec['one'].capitalize()} ajouté(e) : {_item_label(item)}")
            db.session.commit()
            flash(f"{spec['one'].capitalize()} ajouté(e).", "success")
            return redirect(url_for(".references") + f"#{kind}")
    return render_template("catalog/item_form.html", form=form, spec=spec, kind=kind, item=None)


@bp.route(f"/<{KINDS}:kind>/<int:item_id>/modifier", methods=["GET", "POST"])
@require("catalog.edit")
def edit_item(kind, item_id):
    spec = REFERENCES[kind]
    item = _reference(kind, item_id)
    form = spec["form"](obj=item, **({"room": item} if kind == "salles" else {}))
    if form.validate_on_submit():
        values = form.values()
        if _name_taken(kind, values, item):
            form.name.errors.append("Ce nom existe déjà" + (" sur ce site." if kind == "salles" else "."))
        else:
            if changes := diff(item, spec["fields"], values):
                log(
                    f"{spec['model'].__tablename__}.update",
                    f"{spec['one'].capitalize()} modifié(e) : {_item_label(item)}",
                    changes=changes,
                )
            db.session.commit()
            flash("Enregistré.", "success")
            return redirect(url_for(".references") + f"#{kind}")
    return render_template("catalog/item_form.html", form=form, spec=spec, kind=kind, item=item)


@bp.route(f"/<{KINDS}:kind>/<int:item_id>/archiver", methods=["POST"])
@require("catalog.edit")
def archive_item(kind, item_id):
    spec = REFERENCES[kind]
    item = _reference(kind, item_id)
    catalog.archive(item)
    log(f"{spec['model'].__tablename__}.archive", f"{spec['one'].capitalize()} archivé(e) : {_item_label(item)}")
    db.session.commit()
    flash(
        f"{_item_label(item)} n'est plus proposé(e) pour {spec.get('used_for', 'les nouveaux cours')}. "
        "Ce qui existe déjà ne change pas.",
        "success",
    )
    return redirect(url_for(".references") + f"#{kind}")


@bp.route(f"/<{KINDS}:kind>/<int:item_id>/restaurer", methods=["POST"])
@require("catalog.edit")
def restore_item(kind, item_id):
    spec = REFERENCES[kind]
    item = _reference(kind, item_id)
    catalog.restore(item)
    log(f"{spec['model'].__tablename__}.restore", f"{spec['one'].capitalize()} rouvert(e) : {_item_label(item)}")
    db.session.commit()
    flash(f"{_item_label(item)} est de nouveau proposé(e).", "success")
    return redirect(url_for(".references") + f"#{kind}")


@bp.route(f"/<{KINDS}:kind>/<int:item_id>/supprimer", methods=["POST"])
@require("catalog.edit")
def delete_item(kind, item_id):
    spec = REFERENCES[kind]
    item = _reference(kind, item_id)
    if catalog.usage(item):
        flash(
            f"{_item_label(item)} a déjà servi : archivez-le (la) plutôt, l'historique des saisons passées en dépend.",
            "error",
        )
    else:
        log(f"{spec['model'].__tablename__}.delete", f"{spec['one'].capitalize()} supprimé(e) : {_item_label(item)}")
        db.session.delete(item)
        db.session.commit()
        flash("Supprimé.", "success")
    return redirect(url_for(".references") + f"#{kind}")
