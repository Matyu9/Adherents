"""Séances : calendrier de la saison, séances d'un cours, déplacements par le professeur, plannings, agenda à
s'abonner. Voir docs/cours.md."""

from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import DateField, IntegerField, SelectField, StringField, TimeField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from ..extensions import db
from ..models import Course, Lesson, Room, Season, SeasonBreak, Site, Teacher
from ..permissions import can, require
from ..services import agenda, catalog, directory, options
from ..services import lessons as service
from ..services.lessons import LessonError

bp = Blueprint("lessons", __name__, url_prefix="/seances")


def _season():
    seasons = directory.seasons()
    wanted = request.values.get("saison", type=int)
    season = next((s for s in seasons if s.id == wanted), None) if wanted else None
    return season or directory.current_season() or (seasons[0] if seasons else None), seasons


def _flash_all(messages, category="warning"):
    for message in messages:
        flash(message, category)


# ---------------------------------------------------------------------------
# Calendrier de la saison (secrétariat)
# ---------------------------------------------------------------------------


class WindowForm(FlaskForm):
    lessons_from = DateField("Reprise des cours", validators=[Optional()])
    lessons_until = DateField("Fin des cours", validators=[Optional()])


class BreakForm(FlaskForm):
    label = StringField("Nom", validators=[DataRequired("Donnez un nom à la période."), Length(max=80)])
    starts_on = DateField("Du", validators=[DataRequired("Indiquez le premier jour.")])
    ends_on = DateField("Au (inclus)", validators=[Optional()])


class DelayForm(FlaskForm):
    hours = IntegerField(
        "Délai avant une séance (heures)",
        validators=[Optional(), NumberRange(min=1, max=24 * 30, message="Entre 1 et 720 heures.")],
        description="En deçà, seul le secrétariat modifie la séance. Vide : pas de limite.",
    )


@bp.route("/calendrier", methods=["GET", "POST"])
@require("lesson.manage")
def calendar():
    season, seasons = _season()
    if season is None:
        flash("Créez d'abord une saison.", "error")
        return redirect(url_for("directory.seasons"))
    window = WindowForm(obj=season)
    if window.validate_on_submit():
        try:
            service.set_window(season, window.lessons_from.data, window.lessons_until.data)
        except LessonError as exc:
            window.lessons_until.errors.append(str(exc))
        else:
            db.session.commit()
            flash("Dates des cours enregistrées.", "success")
            return redirect(url_for(".calendar", saison=season.id))
    delay = DelayForm(hours=options.positive_int(options.TEACHER_DELAY))
    return render_template(
        "lessons/calendar.html",
        season=season,
        seasons=seasons,
        window=window,
        break_form=BreakForm(),
        delay=delay,
        plan=service.generate(season),
        lesson_count=db.session.scalar(
            db.select(db.func.count(Lesson.id)).join(Course).where(Course.season_id == season.id)
        ),
    )


@bp.route("/calendrier/<int:season_id>/periodes", methods=["POST"])
@require("lesson.manage")
def add_break(season_id):
    season = db.session.get(Season, season_id) or abort(404)
    form = BreakForm()
    if form.validate_on_submit():
        try:
            service.add_break(season, form.label.data, form.starts_on.data, form.ends_on.data)
        except LessonError as exc:
            flash(str(exc), "error")
        else:
            db.session.commit()
            flash("Période ajoutée. Relancez la génération pour retirer les séances concernées.", "success")
    else:
        _flash_all([e for errors in form.errors.values() for e in errors], "error")
    return redirect(url_for(".calendar", saison=season.id) + "#periodes")


@bp.route("/periodes/<int:break_id>/supprimer", methods=["POST"])
@require("lesson.manage")
def delete_break(break_id):
    record = db.session.get(SeasonBreak, break_id) or abort(404)
    season_id = record.season_id
    db.session.delete(record)
    db.session.commit()
    flash("Période supprimée. Relancez la génération pour créer les séances qui manquent.", "success")
    return redirect(url_for(".calendar", saison=season_id) + "#periodes")


@bp.route("/calendrier/<int:season_id>/generer", methods=["POST"])
@require("lesson.manage")
def generate(season_id):
    season = db.session.get(Season, season_id) or abort(404)
    plan = service.generate(season, apply=True, user=current_user)
    db.session.commit()
    flash(
        f"{len(plan.created)} séance(s) créée(s), {len(plan.updated)} recalée(s), {len(plan.removed)} retirée(s).",
        "success",
    )
    return redirect(url_for(".calendar", saison=season.id))


@bp.route("/reglages", methods=["POST"])
@require("lesson.manage")
def save_delay():
    form = DelayForm()
    if form.validate_on_submit():
        options.put(options.TEACHER_DELAY, form.hours.data)
        db.session.commit()
        flash("Délai enregistré.", "success")
    else:
        _flash_all([e for errors in form.errors.values() for e in errors], "error")
    return redirect(url_for(".calendar", saison=request.form.get("saison", type=int)) + "#reglages")


# ---------------------------------------------------------------------------
# Séances d'un cours
# ---------------------------------------------------------------------------


def _course(course_id):
    return db.session.get(Course, course_id) or abort(404)


def _lesson(lesson_id):
    return db.session.get(Lesson, lesson_id) or abort(404)


def _may_view_course(course):
    teacher = service.teacher_of(current_user)
    if not (
        service.is_manager(current_user)
        or can(current_user, "catalog.view")
        or (teacher is not None and course.teacher == teacher)
    ):
        abort(403)


@bp.route("/cours/<int:course_id>")
@login_required
def course(course_id):
    record = _course(course_id)
    _may_view_course(record)
    return render_template(
        "lessons/course.html",
        course=record,
        lessons=record.lessons,
        edit=service.can_edit(current_user, record),
        manager=service.is_manager(current_user),
        today=date.today(),
        teachers=catalog.teachers_for(record.season) if service.is_manager(current_user) else [],
    )


class LessonForm(FlaskForm):
    day = DateField("Date", validators=[DataRequired("Indiquez la date.")])
    starts_at = TimeField("Début", validators=[DataRequired("Indiquez l'heure de début.")])
    ends_at = TimeField("Fin", validators=[DataRequired("Indiquez l'heure de fin.")])
    room_id = SelectField("Salle", coerce=lambda v: int(v) if v not in (None, "") else None, validate_choice=False)
    reason = StringField("Message aux familles", validators=[Optional(), Length(max=200)])


def _room_choices(form, lesson, day, starts_at, ends_at):
    rooms = service.free_rooms(lesson, day, starts_at, ends_at) if day and starts_at and ends_at else []
    form.room_id.choices = [("", "— sans salle")] + [
        (str(room.id), room.label + (f" — occupée ({reason})" if reason else " — libre")) for room, reason in rooms
    ]
    return rooms


def _back_to_course(course_id):
    if request.values.get("retour") == "mes-seances":
        return redirect(url_for(".mine"))
    return redirect(url_for(".course", course_id=course_id))


@bp.route("/<int:lesson_id>/modifier", methods=["GET", "POST"])
@login_required
def edit(lesson_id):
    """Déplacer une séance. GET avec des valeurs : voir les salles libres à ce créneau, sans rien enregistrer."""
    lesson = _lesson(lesson_id)
    if not service.can_edit(current_user, lesson):
        abort(403)
    if request.method == "POST":
        form = LessonForm()
    else:
        form = LessonForm(formdata=request.args if "day" in request.args else None, obj=lesson)
        if "day" not in request.args:
            form.room_id.data = lesson.room_id
    rooms = _room_choices(form, lesson, form.day.data, form.starts_at.data, form.ends_at.data)
    if request.method == "POST" and form.validate():
        room = db.session.get(Room, form.room_id.data) if form.room_id.data else None
        try:
            warnings = service.move(
                lesson, form.day.data, form.starts_at.data, form.ends_at.data, room, form.reason.data, current_user
            )
        except LessonError as exc:
            db.session.rollback()
            form.day.errors.append(str(exc))
        else:
            db.session.commit()
            flash("Séance déplacée. Les familles sont prévenues.", "success")
            _flash_all(warnings)
            return _back_to_course(lesson.course_id)
    warnings = (
        service.conflicts(
            lesson,
            form.day.data,
            form.starts_at.data,
            form.ends_at.data,
            db.session.get(Room, form.room_id.data) if form.room_id.data else None,
        )
        if form.day.data and form.starts_at.data and form.ends_at.data and form.ends_at.data > form.starts_at.data
        else []
    )
    return render_template(
        "lessons/edit.html", form=form, lesson=lesson, course=lesson.course, rooms=rooms, warnings=warnings
    )


@bp.route("/cours/<int:course_id>/ajouter", methods=["GET", "POST"])
@login_required
def add(course_id):
    record = _course(course_id)
    if not service.can_edit(current_user, record):
        abort(403)
    form = LessonForm() if request.method == "POST" else LessonForm(formdata=request.args or None)
    if request.method == "GET" and not request.args:
        form.starts_at.data, form.ends_at.data, form.room_id.data = record.starts_at, record.ends_at, record.room_id
    probe = Lesson(
        id=0,
        course_id=record.id,
        day=form.day.data or date.today(),
        starts_at=datetime.min.time(),
        ends_at=datetime.min.time(),
    )
    rooms = _room_choices(form, probe, form.day.data, form.starts_at.data, form.ends_at.data)
    if request.method == "POST" and form.validate():
        room = db.session.get(Room, form.room_id.data) if form.room_id.data else None
        try:
            _, warnings = service.add_extra(
                record, form.day.data, form.starts_at.data, form.ends_at.data, room, form.reason.data, current_user
            )
        except LessonError as exc:
            db.session.rollback()
            form.day.errors.append(str(exc))
        else:
            db.session.commit()
            flash("Séance ajoutée. Les familles sont prévenues.", "success")
            _flash_all(warnings)
            return _back_to_course(record.id)
    return render_template("lessons/edit.html", form=form, lesson=None, course=record, rooms=rooms, warnings=[])


@bp.route("/<int:lesson_id>/annuler", methods=["POST"])
@login_required
def cancel(lesson_id):
    lesson = _lesson(lesson_id)
    if not service.can_edit(current_user, lesson):
        abort(403)
    try:
        service.cancel(lesson, request.form.get("reason"), current_user)
    except LessonError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash("Séance annulée. Les familles sont prévenues.", "success")
    return _back_to_course(lesson.course_id)


@bp.route("/<int:lesson_id>/revenir", methods=["POST"])
@require("lesson.manage")
def revert(lesson_id):
    lesson = _lesson(lesson_id)
    course_id = lesson.course_id
    try:
        service.revert(lesson, current_user)
    except LessonError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash("Dernier changement annulé. Les familles sont prévenues.", "success")
    return redirect(url_for(".course", course_id=course_id))


@bp.route("/<int:lesson_id>/remplacant", methods=["POST"])
@require("lesson.manage")
def substitute(lesson_id):
    lesson = _lesson(lesson_id)
    teacher_id = request.form.get("teacher_id", type=int)
    teacher = db.session.get(Teacher, teacher_id) if teacher_id else None
    if teacher_id and teacher is None:
        abort(404)
    try:
        service.set_substitute(lesson, teacher, current_user)
    except LessonError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        db.session.commit()
        flash("Remplaçant enregistré." if teacher else "Remplaçant retiré.", "success")
    return redirect(url_for(".course", course_id=lesson.course_id))


# ---------------------------------------------------------------------------
# Mes séances (professeurs)
# ---------------------------------------------------------------------------


@bp.route("/mes-seances")
@login_required
def mine():
    teacher = service.teacher_of(current_user)
    if teacher is None:
        abort(404)
    today = date.today()
    rows = service.upcoming_for(teacher, today - timedelta(days=7), today + timedelta(weeks=8))
    return render_template(
        "lessons/mine.html",
        teacher=teacher,
        past=[lesson for lesson in rows if lesson.day < today],
        today_rows=[lesson for lesson in rows if lesson.day == today],
        upcoming=[lesson for lesson in rows if lesson.day > today],
        limit=service.teacher_limit(),
        now=datetime.now(),
    )


# ---------------------------------------------------------------------------
# Plannings
# ---------------------------------------------------------------------------


@bp.route("/planning")
@require("catalog.view")
def planning():
    season, seasons = _season()
    view = request.args.get("vue") if request.args.get("vue") in ("salle", "prof", "site") else "salle"
    target_id = request.args.get("id", type=int)
    target = None
    if target_id:
        target = db.session.get({"salle": Room, "prof": Teacher, "site": Site}[view], target_id)
    try:
        week = date.fromisoformat(request.args.get("semaine", ""))
    except ValueError:
        week = date.today()
    monday, sunday = service.week_of(week)
    kind = "type" if request.args.get("type") == "type" else "reelle"
    filters = {
        "room": target if view == "salle" else None,
        "teacher": target if view == "prof" else None,
        "site": target if view == "site" else None,
    }
    if kind == "type":
        days = service.typical_week(season, **filters) if season else {d: [] for d in range(7)}
        rows = None
    else:
        days = None
        rows = {monday + timedelta(days=d): [] for d in range(7)}
        for lesson in service.week_lessons(monday, sunday, **filters):
            rows[lesson.day].append(lesson)
    return render_template(
        "lessons/planning.html",
        season=season,
        seasons=seasons,
        view=view,
        target=target,
        kind=kind,
        days=days,
        rows=rows,
        monday=monday,
        previous=monday - timedelta(days=7),
        following=monday + timedelta(days=7),
        rooms=catalog.rooms(),
        sites=catalog.sites(),
        teachers=catalog.teachers_for(season) if season else [],
        weekdays=Course.WEEKDAYS,
        day_label=service.day_label,
    )


# ---------------------------------------------------------------------------
# Agenda à s'abonner
# ---------------------------------------------------------------------------


@bp.route("/agenda", methods=["GET", "POST"])
@login_required
def agenda_page():
    url = None
    if request.method == "POST":
        if request.form.get("action") == "supprimer":
            agenda.revoke_token(current_user)
            db.session.commit()
            flash("Adresse supprimée : les agendas abonnés ne reçoivent plus rien.", "success")
            return redirect(url_for(".agenda_page"))
        raw = agenda.issue_token(current_user)
        db.session.commit()
        url = url_for(".feed", token=raw, _external=True)
    return render_template("lessons/agenda.html", url=url, has_token=bool(current_user.calendar_token))


@bp.route("/agenda/<token>.ics")
def feed(token):
    account = agenda.account_for(token)
    if account is None:
        abort(404)
    response = Response(agenda.feed(account), mimetype="text/calendar")
    response.headers["Cache-Control"] = "private, max-age=300"
    response.headers["Content-Disposition"] = 'inline; filename="cours.ics"'
    return response
