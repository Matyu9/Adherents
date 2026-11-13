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
from ..services import agenda, catalog, directory, options, planning
from ..services import attendance as attendance_service
from ..services import lessons as service
from ..services.lessons import LessonError

bp = Blueprint("lessons", __name__, url_prefix="/seances")


def _season():
    return directory.selected_season()


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
    kind = SelectField("Nature", choices=list(SeasonBreak.KINDS.items()), default=SeasonBreak.VACATION)
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
    presence = AttendanceSettingsForm(threshold=attendance_service.threshold())
    return render_template(
        "lessons/calendar.html",
        season=season,
        seasons=seasons,
        window=window,
        break_form=BreakForm(),
        delay=delay,
        presence=presence,
        family_attendance=attendance_service.family_visible(),
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
            service.add_break(season, form.label.data, form.starts_on.data, form.ends_on.data, form.kind.data)
        except LessonError as exc:
            flash(str(exc), "error")
        else:
            db.session.commit()
            flash("Période ajoutée. Relancez la génération pour retirer les séances concernées.", "success")
    else:
        _flash_all([e for errors in form.errors.values() for e in errors], "error")
    return redirect(url_for(".calendar", saison=season.id) + "#periodes")


@bp.route("/calendrier/<int:season_id>/feries", methods=["POST"])
@require("lesson.manage")
def add_holidays(season_id):
    """Ajoute les jours fériés de la saison qui manquent : chacun reste modifiable ou supprimable ensuite."""
    from ..services import holidays

    season = db.session.get(Season, season_id) or abort(404)
    added = holidays.apply(season, holidays.holidays_plan(season), SeasonBreak.HOLIDAY)
    db.session.commit()
    flash(
        f"{added} jour(s) férié(s) ajouté(s). Relancez la génération pour retirer les séances concernées."
        if added
        else "Les jours fériés de la saison sont déjà là.",
        "success",
    )
    return redirect(url_for(".calendar", saison=season.id) + "#periodes")


@bp.route("/calendrier/<int:season_id>/importer", methods=["GET", "POST"])
@require("lesson.manage")
def import_breaks(season_id):
    """Vacances et fermetures depuis un fichier .ics (calendrier scolaire, export d'agenda) ou CSV : aperçu, puis
    ajout des lignes cochées."""
    from ..services import holidays

    season = db.session.get(Season, season_id) or abort(404)
    rows, error = None, None
    if request.method == "POST" and request.form.get("action") == "confirmer":
        wanted = set(request.form.getlist("ligne"))
        kind = request.form.get("kind") if request.form.get("kind") in SeasonBreak.KINDS else SeasonBreak.VACATION
        picked = []
        for key in wanted:
            try:
                start, end, label = key.split("|", 2)
                picked.append(holidays.Row(label[:80], date.fromisoformat(start), date.fromisoformat(end)))
            except ValueError:
                abort(400)
        added = holidays.apply(season, holidays.plan(season, picked), kind)
        db.session.commit()
        flash(f"{added} période(s) ajoutée(s). Relancez la génération pour retirer les séances concernées.", "success")
        return redirect(url_for(".calendar", saison=season.id) + "#periodes")
    if request.method == "POST":
        upload = request.files.get("fichier")
        if not upload or not upload.filename:
            error = "Choisissez un fichier .ics ou .csv."
        else:
            try:
                rows = holidays.plan(season, holidays.parse(upload.filename, upload.read(holidays.MAX_FILE + 1)))
            except holidays.ImportError_ as exc:
                error = str(exc)
            else:
                if not rows:
                    error = "Aucune période trouvée dans ce fichier."
    return render_template("lessons/import_breaks.html", season=season, rows=rows, error=error, kinds=SeasonBreak.KINDS)


@bp.route("/periodes/<int:break_id>/maintenir", methods=["POST"])
@require("lesson.manage")
def keep_break(break_id):
    """« Cours maintenus » : la période reste affichée, mais ne retire plus de séance (ou l'inverse)."""
    record = db.session.get(SeasonBreak, break_id) or abort(404)
    record.keep_lessons = not record.keep_lessons
    db.session.commit()
    flash(
        f"« {record.label} » : "
        + ("cours maintenus." if record.keep_lessons else "plus de cours.")
        + " Relancez la génération pour mettre les séances à jour.",
        "success",
    )
    return redirect(url_for(".calendar", saison=record.season_id) + "#periodes")


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
    """Séances d'un cours : un résumé, puis la liste (à venir, passées repliées) ou le calendrier d'un mois."""
    from ..models import Attendance, Enrollment

    record = _course(course_id)
    _may_view_course(record)
    today = date.today()
    lessons = record.lessons
    view = "calendrier" if request.args.get("vue") == "calendrier" else "liste"
    marks = [a for lesson in lessons for a in lesson.attendance]
    stats = {
        "students": sum(1 for e in record.enrollments if e.status == Enrollment.ACTIVE),
        "done": sum(1 for lesson in lessons if not lesson.cancelled and lesson.day < today),
        "left": sum(1 for lesson in lessons if not lesson.cancelled and lesson.day >= today),
        "rate": round(100 * sum(1 for a in marks if a.status in Attendance.ATTENDED) / len(marks)) if marks else None,
    }
    calendar_data = None
    if view == "calendrier":
        try:
            year, month = (int(x) for x in request.args.get("mois", "").split("-"))
            date(year, month, 1)
        except ValueError:
            upcoming = next((lesson.day for lesson in lessons if lesson.day >= today), None)
            anchor = upcoming or (lessons[-1].day if lessons else today)
            year, month = anchor.year, anchor.month
        now = datetime.now()
        events = [
            planning.from_lesson(lesson, now, link=url_for(".course", course_id=record.id) + f"#s{lesson.id}")
            for lesson in lessons
        ]
        prev_month, next_month = planning.shift_month(year, month, -1), planning.shift_month(year, month, 1)
        start, end = planning.month_bounds(year, month)
        calendar_data = {
            "weeks": planning.month_layout(year, month, events, now, offs=service.breaks_between(start, end)),
            "title": planning.month_title(year, month),
            "prev": f"{prev_month[0]}-{prev_month[1]:02d}",
            "next": f"{next_month[0]}-{next_month[1]:02d}",
        }
    return render_template(
        "lessons/course.html",
        course=record,
        lessons=lessons,
        upcoming=[lesson for lesson in lessons if lesson.day >= today],
        past=[lesson for lesson in lessons if lesson.day < today],
        view=view,
        stats=stats,
        calendar=calendar_data,
        edit=service.can_edit(current_user, record),
        manager=service.is_manager(current_user),
        today=today,
        teachers=catalog.teachers_for(record.season) if service.is_manager(current_user) else [],
        summary=attendance_service.summary,
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
# Présences
# ---------------------------------------------------------------------------


@bp.route("/<int:lesson_id>/appel", methods=["GET", "POST"])
@login_required
def roll(lesson_id):
    lesson = _lesson(lesson_id)
    if not service.can_view(current_user, lesson):
        abort(403)
    allowed, reason = attendance_service.may_record(current_user, lesson)
    rows = attendance_service.sheet(lesson)
    if request.method == "POST":  # jeton CSRF vérifié par CSRFProtect, comme partout
        if not allowed:
            flash(reason, "error")
            return redirect(url_for(".roll", lesson_id=lesson.id))
        statuses = {e.id: request.form.get(f"e{e.id}") for e, _ in rows if request.form.get(f"e{e.id}")}
        try:
            attendance_service.record(lesson, statuses, current_user)
        except attendance_service.AttendanceError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        else:
            db.session.commit()
            missing = len(rows) - len(statuses)
            flash("Appel enregistré." + (f" {missing} élève(s) sans réponse." if missing else ""), "success")
            if service.is_manager(current_user):
                return redirect(url_for(".course", course_id=lesson.course_id))
            return redirect(url_for(".mine"))
    teacher = service.teacher_of(current_user)
    return render_template(
        "lessons/roll.html",
        lesson=lesson,
        rows=rows,
        notices=attendance_service.notices(lesson),
        allowed=allowed,
        reason=reason,
        statuses=attendance_service.Attendance.STATUSES,
        # Le nom mène à la fiche que la personne peut ouvrir : la fiche élève du professeur, ou l'annuaire.
        student_link=(
            (lambda person: url_for("directory.person", person_id=person.id))
            if can(current_user, "directory.view")
            else (lambda person: url_for("enrollment.my_student", person_id=person.id, saison=lesson.course.season_id))
            if teacher is not None and teacher == lesson.course.teacher
            else (lambda person: None)
        ),
    )


@bp.route("/absences")
@require("lesson.manage")
def absences():
    season, seasons = _season()
    return render_template(
        "lessons/absences.html",
        season=season,
        seasons=seasons,
        rows=attendance_service.repeated_absences(season) if season else [],
        threshold=attendance_service.threshold(),
    )


class AttendanceSettingsForm(FlaskForm):
    threshold = IntegerField(
        "Signaler un élève à partir de … absences non excusées",
        validators=[Optional(), NumberRange(min=1, max=100, message="Entre 1 et 100.")],
        description="Vide : aucun élève n'est signalé.",
    )


@bp.route("/presences/reglages", methods=["POST"])
@require("lesson.manage")
def save_attendance_settings():
    form = AttendanceSettingsForm()
    if form.validate_on_submit():
        options.put(options.ABSENCE_THRESHOLD, form.threshold.data)
        options.put(options.FAMILY_ATTENDANCE, "1" if request.form.get("family") == "1" else "0")
        db.session.commit()
        flash("Réglages des présences enregistrés.", "success")
    else:
        _flash_all([e for errors in form.errors.values() for e in errors], "error")
    return redirect(url_for(".calendar", saison=request.form.get("saison", type=int)) + "#presences")


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
    rows = service.upcoming_for(
        teacher,
        today - timedelta(days=7),
        today + timedelta(weeks=8),
        include_hidden=directory.sees_hidden_seasons(current_user),
    )
    return render_template(
        "lessons/mine.html",
        teacher=teacher,
        past=[lesson for lesson in rows if lesson.day < today],
        today_rows=[lesson for lesson in rows if lesson.day == today],
        upcoming=[lesson for lesson in rows if lesson.day > today],
        limit=service.teacher_limit(),
        now=datetime.now(),
        may_record=lambda lesson: attendance_service.may_record(current_user, lesson)[0],
        summary=attendance_service.summary,
    )


# ---------------------------------------------------------------------------
# Plannings
# ---------------------------------------------------------------------------


def _reference_day():
    try:
        return date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        return date.today()


def _mode():
    mode = request.args.get("mode")
    return mode if mode in ("semaine", "mois", "type") else "semaine"


def _range(mode, day):
    if mode == "mois":
        return planning.month_bounds(day.year, day.month)
    return service.week_of(day)


def _render_planning(events, mode, day, personal, **context):
    """Commun au planning de l'association et à « Mon planning »."""
    now = datetime.now()
    monday, _ = service.week_of(day)
    if mode == "mois":
        start, end = planning.month_bounds(day.year, day.month)
        layout = planning.month_layout(day.year, day.month, events, now, offs=service.breaks_between(start, end))
        title = planning.month_title(day.year, day.month)
        year, month = planning.shift_month(day.year, day.month, -1)
        previous = date(year, month, 1)
        year, month = planning.shift_month(day.year, day.month, 1)
        following = date(year, month, 1)
    else:
        layout = planning.week_layout(
            events,
            monday,
            now,
            typical=mode == "type",
            offs=service.breaks_between(monday, monday + timedelta(days=6)) if mode != "type" else None,
        )
        title = "Semaine type" if mode == "type" else planning.week_title(monday)
        previous, following = monday - timedelta(days=7), monday + timedelta(days=7)
    return render_template(
        "lessons/planning.html",
        mode=mode,
        layout=layout,
        title=title,
        day=day,
        previous=previous,
        following=following,
        today=date.today(),
        personal=personal,
        legend=planning.legend(events),
        endpoint="lessons.my_planning" if personal else "lessons.planning",
        **context,
    )


def _lesson_link(lesson):
    return (
        url_for(".course", course_id=lesson.course_id) + f"#s{lesson.id}"
        if service.can_view(current_user, lesson)
        else None
    )


@bp.route("/planning", endpoint="planning")
@require("catalog.view")
def planning_view():
    season, seasons = _season()
    view = request.args.get("vue") if request.args.get("vue") in ("salle", "prof", "site") else "salle"
    target_id = request.args.get("id", type=int)
    target = db.session.get({"salle": Room, "prof": Teacher, "site": Site}[view], target_id) if target_id else None
    filters = {
        "room": target if view == "salle" else None,
        "teacher": target if view == "prof" else None,
        "site": target if view == "site" else None,
    }
    mode, day = _mode(), _reference_day()
    now = datetime.now()

    def details(room, teacher, substitute=None):
        lines = []
        if room is not None and view != "salle":
            lines.append(room.label)
        if substitute is not None:
            lines.append(f"Remplacé par {substitute.person.full_name}")
        elif teacher is not None and view != "prof":
            lines.append(teacher.person.full_name)
        return lines

    if mode == "type":
        days = service.typical_week(season, **filters) if season else {}
        roster = can(current_user, "directory.view")
        events = [
            planning.from_course(
                course,
                link=url_for("catalog.course", course_id=course.id) if roster else None,
                details=details(course.room, course.teacher),
            )
            for courses in days.values()
            for course in courses
            if course.starts_at is not None
        ]
    else:
        start, end = _range(mode, day)
        events = [
            planning.from_lesson(
                lesson,
                now,
                link=_lesson_link(lesson),
                details=details(lesson.room, lesson.course.teacher, lesson.substitute),
            )
            for lesson in service.week_lessons(start, end, **filters)
        ]
    return _render_planning(
        events,
        mode,
        day,
        personal=False,
        season=season,
        seasons=seasons,
        view=view,
        target=target,
        rooms=catalog.rooms(),
        sites=catalog.sites(),
        teachers=catalog.teachers_for(season) if season else [],
    )


@bp.route("/mon-planning")
@login_required
def my_planning():
    """Les séances du compte : celles qu'il donne ou remplace, et celles des élèves dont il est responsable."""
    mode = _mode() if _mode() != "type" else "semaine"
    day = _reference_day()
    start, end = _range(mode, day)
    now = datetime.now()
    events = []
    for lesson, begin, finish, who in service.personal_lessons(current_user, start, end):
        if who is None or who == "remplacement":
            lines = [lesson.room.label if lesson.room else None, "Remplacement" if who else None]
            title = lesson.course.title
        else:
            lines = [
                lesson.room.label if lesson.room else None,
                lesson.teacher.person.full_name if lesson.teacher else None,
            ]
            title = f"{who.membership.person.first_name} · {lesson.course.title}"
        events.append(
            planning.from_lesson(
                lesson, now, link=_lesson_link(lesson), details=lines, starts_at=begin, ends_at=finish, title=title
            )
        )
    return _render_planning(events, mode, day, personal=True)


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


# ---------------------------------------------------------------------------
# Salles
# ---------------------------------------------------------------------------


@bp.route("/salles")
@require("catalog.view")
def rooms():
    """Les salles par site : places, agenda relié, heures occupées cette semaine et cours réguliers de la saison."""
    from collections import defaultdict

    season, _ = _season()
    monday, sunday = service.week_of(date.today())
    week = service.week_lessons(monday, sunday)
    hours, count = defaultdict(float), defaultdict(int)
    for lesson in week:
        if lesson.room_id and not lesson.cancelled:
            count[lesson.room_id] += 1
            hours[lesson.room_id] += (
                datetime.combine(lesson.day, lesson.ends_at) - datetime.combine(lesson.day, lesson.starts_at)
            ).seconds / 3600
    regular = defaultdict(int)
    if season is not None:
        for course in db.session.scalars(db.select(Course).where(Course.season_id == season.id)):
            if course.room_id:
                regular[course.room_id] += 1
    sites = db.session.scalars(db.select(Site).where(Site.archived_at.is_(None)).order_by(Site.name)).all()
    return render_template(
        "lessons/rooms.html",
        sites=sites,
        season=season,
        monday=monday,
        hours=hours,
        count=count,
        regular=regular,
    )
