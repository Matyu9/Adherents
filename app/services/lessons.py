"""Séances des cours : calendrier de la saison, génération, déplacements, annulations, remplaçants, plannings.

Le professeur modifie ses séances lui-même ; le changement s'applique tout de suite, les familles et le secrétariat
sont prévenus, et le secrétariat peut revenir en arrière. Voir docs/cours.md. Les services ne font pas de commit.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from ..extensions import db
from ..models import Course, Enrollment, Lesson, Room, SeasonBreak
from . import options
from .activity import log

WEEKDAY_NAMES = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def _link(endpoint, **values):
    """Adresse d'un écran, ou None hors requête (commande, tâche)."""
    from flask import url_for

    try:
        return url_for(endpoint, **values)
    except RuntimeError:
        return None


class LessonError(ValueError):
    """Opération refusée, avec un message à afficher tel quel."""


def day_label(day):
    return f"{WEEKDAY_NAMES[day.weekday()]} {day.day} {MONTHS[day.month - 1]}"


def describe(lesson):
    text = f"{lesson.course.title}, {day_label(lesson.day)} {lesson.starts_at:%H:%M}-{lesson.ends_at:%H:%M}"
    return text + (f", {lesson.room.label}" if lesson.room else "")


# ---------------------------------------------------------------------------
# Calendrier de la saison
# ---------------------------------------------------------------------------


def set_window(season, lessons_from, lessons_until):
    first, last = lessons_from or season.starts_on, lessons_until or season.ends_on
    if first < season.starts_on or last > season.ends_on:
        raise LessonError(f"Les cours doivent avoir lieu pendant la saison {season.label}.")
    if last < first:
        raise LessonError("La fin des cours précède leur reprise.")
    season.lessons_from, season.lessons_until = lessons_from, lessons_until
    db.session.flush()


def add_break(season, label, starts_on, ends_on):
    label = (label or "").strip()
    if not label:
        raise LessonError("Donnez un nom à la période (« Vacances de la Toussaint », « 11 novembre »…).")
    ends_on = ends_on or starts_on
    if starts_on is None:
        raise LessonError("Indiquez le premier jour de la période.")
    if ends_on < starts_on:
        raise LessonError("La période finit avant de commencer.")
    if ends_on < season.starts_on or starts_on > season.ends_on:
        raise LessonError(f"La période doit toucher la saison {season.label}.")
    record = SeasonBreak(season=season, label=label[:80], starts_on=starts_on, ends_on=ends_on)
    db.session.add(record)
    db.session.flush()
    return record


def off_days(season):
    """Fonction qui dit si un jour tombe dans une période sans cours."""
    periods = [(b.starts_on, b.ends_on) for b in season.breaks]
    return lambda day: any(a <= day <= b for a, b in periods)


def scheduled_days(course):
    """Jours du créneau régulier du cours, entre reprise et fin des cours, hors périodes sans cours."""
    if course.weekday is None or course.starts_at is None:
        return []
    first, last = course.season.lessons_window
    is_off = off_days(course.season)
    day = first + timedelta(days=(course.weekday - first.weekday()) % 7)
    days = []
    while day <= last:
        if not is_off(day):
            days.append(day)
        day += timedelta(days=7)
    return days


@dataclass
class Generation:
    created: list = field(default_factory=list)  # (cours, jour)
    updated: list = field(default_factory=list)  # séances recalées sur le créneau du cours
    removed: list = field(default_factory=list)  # séances qui ne tombent plus sur un jour de cours
    kept: int = 0  # séances passées ou modifiées à la main, laissées telles quelles
    skipped: list = field(default_factory=list)  # cours sans jour ni horaire

    @property
    def changes(self):
        return len(self.created) + len(self.updated) + len(self.removed)


def generate(season, today=None, apply=False, user=None):
    """Crée les séances qui manquent et recale les séances à venir sur le créneau de leur cours. On peut la relancer :
    elle ne touche ni aux séances passées ni à celles modifiées à la main. Sans `apply`, c'est un aperçu."""
    today = today or date.today()
    plan = Generation()
    for course in db.session.scalars(db.select(Course).where(Course.season_id == season.id)):
        days = scheduled_days(course)
        if not days:
            plan.skipped.append(course)
            continue
        existing = {lesson.scheduled_on: lesson for lesson in course.lessons if lesson.scheduled_on}
        for day in days:
            if day not in existing:
                plan.created.append((course, day))
        wanted = set(days)
        for scheduled, lesson in existing.items():
            if lesson.day < today or lesson.edited:
                plan.kept += 1
            elif scheduled not in wanted:
                plan.removed.append(lesson)
            elif (lesson.day, lesson.starts_at, lesson.ends_at, lesson.room_id) != (
                scheduled,
                course.starts_at,
                course.ends_at,
                course.room_id,
            ):
                plan.updated.append(lesson)
    if not apply:
        return plan
    for course, day in plan.created:
        db.session.add(
            Lesson(
                course=course,
                scheduled_on=day,
                day=day,
                starts_at=course.starts_at,
                ends_at=course.ends_at,
                room_id=course.room_id,
            )
        )
    for lesson in plan.updated:
        old_room = lesson.room_id
        course = lesson.course
        lesson.day, lesson.starts_at, lesson.ends_at = lesson.scheduled_on, course.starts_at, course.ends_at
        lesson.room_id = course.room_id
        lesson.revision += 1
        _after_change(lesson, old_room)
    for lesson in plan.removed:
        if has_attendance(lesson):
            lesson.status, lesson.reason = Lesson.CANCELLED, "Plus de cours ce jour-là"
            lesson.revision += 1
            _after_change(lesson, lesson.room_id)
        else:
            _before_delete(lesson)
            db.session.delete(lesson)
    db.session.flush()
    for course in {lesson.course for lesson in plan.removed}:
        db.session.expire(course, ["lessons"])
    if plan.changes:
        log(
            "lesson.generate",
            f"Séances {season.label} : {len(plan.created)} créée(s), {len(plan.updated)} recalée(s), "
            f"{len(plan.removed)} retirée(s)",
            user=user,
        )
    return plan


def has_attendance(lesson):
    """Une séance dont l'appel est fait ne se supprime pas (étape des présences)."""
    return bool(getattr(lesson, "attendance", None))


# Points d'accroche des agendas de salles (Google Workspace) : remplacés par room_calendars s'il est configuré.
def _after_change(lesson, old_room_id):
    from . import room_calendars

    room_calendars.lesson_changed(lesson, old_room_id)


def _before_delete(lesson):
    from . import room_calendars

    room_calendars.lesson_deleted(lesson)


# ---------------------------------------------------------------------------
# Qui fait quoi
# ---------------------------------------------------------------------------


def teacher_of(account):
    person = getattr(account, "person", None)
    return person.teacher if person is not None else None


def is_manager(account):
    from ..permissions import can

    return can(account, "lesson.manage")


def can_view(account, lesson):
    """Le secrétariat, le professeur du cours et le remplaçant de la séance."""
    if is_manager(account):
        return True
    teacher = teacher_of(account)
    return teacher is not None and teacher in (lesson.course.teacher, lesson.substitute)


def can_edit(account, lesson_or_course):
    """Le secrétariat, et le professeur du cours (pas le remplaçant)."""
    if is_manager(account):
        return True
    course = lesson_or_course if isinstance(lesson_or_course, Course) else lesson_or_course.course
    teacher = teacher_of(account)
    return teacher is not None and course.teacher == teacher


def teacher_limit(now=None):
    """Jusqu'à quand un professeur peut modifier une séance qui commence à `moment` : None si pas de limite."""
    hours = options.positive_int(options.TEACHER_DELAY)
    return timedelta(hours=hours) if hours else None


def _check_teacher_may_change(lesson, now):
    if lesson.starts() <= now:
        raise LessonError("Cette séance a déjà commencé : seul le secrétariat peut la modifier.")
    if (delay := teacher_limit()) and lesson.starts() - now < delay:
        hours = int(delay.total_seconds() // 3600)
        raise LessonError(
            f"La séance commence dans moins de {hours} heure(s) : demandez au secrétariat de la modifier."
        )


# ---------------------------------------------------------------------------
# Élèves concernés, disponibilités
# ---------------------------------------------------------------------------


def students(lesson):
    """Inscriptions des élèves qui suivent le cours le jour de la séance."""
    return [e for e in lesson.course.enrollments if e.attends_on(lesson.day)]


def student_times(enrollment, lesson):
    """Heure de passage de l'élève pour cette séance : son passage dans un cours individuel, décalé comme la
    séance ; sinon l'horaire de la séance."""
    course = lesson.course
    if not course.individual or enrollment.slot_starts_at is None or course.starts_at is None:
        return lesson.starts_at, lesson.ends_at
    shift = datetime.combine(lesson.day, lesson.starts_at) - datetime.combine(lesson.day, course.starts_at)
    start = datetime.combine(lesson.day, enrollment.slot_starts_at) + shift
    return start.time(), (start + timedelta(minutes=enrollment.slot_minutes)).time()


def _overlap(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def lessons_on(day):
    return db.session.scalars(db.select(Lesson).where(Lesson.day == day, Lesson.status == Lesson.PLANNED)).all()


def conflicts(lesson, day, starts_at, ends_at, room):
    """Ce qui gêne la séance au nouveau créneau : salle prise, professeur ailleurs, élève à un autre cours,
    période sans cours. Des avertissements, pas des refus."""
    warnings = []
    others = [o for o in lessons_on(day) if o.id != lesson.id and _overlap(starts_at, ends_at, o.starts_at, o.ends_at)]
    if room is not None:
        for other in others:
            if other.room_id == room.id:
                warnings.append(f"{room.label} est prise par {other.course.title} ({other.starts_at:%H:%M}).")
        busy = room_busy(room, day, starts_at, ends_at)
        if busy:
            warnings.append(f"{room.label} est réservée dans son agenda ({busy}).")
    teacher = lesson.teacher
    if teacher is not None:
        for other in others:
            if other.teacher == teacher:
                warnings.append(f"{teacher.person.full_name} a déjà {other.course.title} à {other.starts_at:%H:%M}.")
    people = {e.membership_id for e in lesson.course.enrollments if e.attends_on(day)}
    for other in others:
        for enrollment in students(other):
            if enrollment.membership_id in people:
                warnings.append(
                    f"{enrollment.membership.person.full_name} a {other.course.title} à {other.starts_at:%H:%M}."
                )
    if off_days(lesson.course.season)(day):
        warnings.append("Ce jour tombe pendant une période sans cours.")
    return warnings


def room_busy(room, day, starts_at, ends_at):
    """Occupation de la salle dans son agenda externe (Google Workspace), ou None."""
    from . import room_calendars

    return room_calendars.busy(room, datetime.combine(day, starts_at), datetime.combine(day, ends_at))


def free_rooms(lesson, day, starts_at, ends_at):
    """[(salle, occupée par)] des salles ouvertes, les libres d'abord : `occupée par` est vide si la salle est libre
    (dans l'appli et dans son agenda externe)."""
    from .catalog import rooms

    taken = {}
    for other in lessons_on(day):
        if other.id != lesson.id and other.room_id and _overlap(starts_at, ends_at, other.starts_at, other.ends_at):
            taken.setdefault(other.room_id, set()).add(other.course.title)
    result = []
    for room in rooms():
        reason = ", ".join(sorted(taken.get(room.id, ()))) or room_busy(room, day, starts_at, ends_at)
        result.append((room, reason))
    return sorted(result, key=lambda r: (r[1] is not None, r[0].label))


# ---------------------------------------------------------------------------
# Changer une séance
# ---------------------------------------------------------------------------


def _snapshot(lesson):
    return {
        "day": lesson.day.isoformat(),
        "starts_at": lesson.starts_at.strftime("%H:%M"),
        "ends_at": lesson.ends_at.strftime("%H:%M"),
        "room_id": lesson.room_id,
        "status": lesson.status,
        "reason": lesson.reason,
        "substitute_id": lesson.substitute_id,
    }


def _record(lesson, user, now):
    lesson.edited = True
    lesson.revision += 1
    lesson.changed_by, lesson.changed_at = user, now


def move(lesson, day, starts_at, ends_at, room=None, reason=None, user=None, now=None):
    """Déplace la séance (date, horaire, salle). Renvoie les avertissements."""
    now = now or datetime.now()
    by_teacher = user is not None and not is_manager(user)
    if lesson.cancelled:
        raise LessonError("Cette séance est annulée : rétablissez-la d'abord.")
    if by_teacher:
        _check_teacher_may_change(lesson, now)
    if day is None or starts_at is None or ends_at is None:
        raise LessonError("Indiquez la date et l'horaire.")
    if ends_at <= starts_at:
        raise LessonError("La séance finit avant de commencer.")
    season = lesson.course.season
    if not season.starts_on <= day <= season.ends_on:
        raise LessonError(f"La date doit être comprise dans la saison {season.label}.")
    if by_teacher and datetime.combine(day, starts_at) <= now:
        raise LessonError("On ne déplace pas une séance dans le passé.")
    if (day, starts_at, ends_at, room.id if room else None) == (
        lesson.day,
        lesson.starts_at,
        lesson.ends_at,
        lesson.room_id,
    ):
        raise LessonError("Rien n'a changé.")
    warnings = conflicts(lesson, day, starts_at, ends_at, room)
    before, old_room = describe(lesson), lesson.room_id
    lesson.previous = _snapshot(lesson)
    lesson.day, lesson.starts_at, lesson.ends_at, lesson.room = day, starts_at, ends_at, room
    lesson.reason = (reason or "").strip()[:200] or None
    _record(lesson, user, now)
    db.session.flush()
    message = f"Séance de {before} déplacée : {day_label(day)} {starts_at:%H:%M}-{ends_at:%H:%M}" + (
        f", {room.label}" if room else ""
    )
    _announce(lesson, message, user, by_teacher)
    _after_change(lesson, old_room)
    return warnings


def cancel(lesson, reason, user=None, now=None):
    now = now or datetime.now()
    by_teacher = user is not None and not is_manager(user)
    if lesson.cancelled:
        raise LessonError("Cette séance est déjà annulée.")
    if by_teacher:
        _check_teacher_may_change(lesson, now)
    reason = (reason or "").strip()
    if not reason:
        raise LessonError("Indiquez le motif, il sera transmis aux familles.")
    lesson.previous = _snapshot(lesson)
    lesson.status, lesson.reason = Lesson.CANCELLED, reason[:200]
    _record(lesson, user, now)
    db.session.flush()
    _announce(lesson, f"Séance de {describe(lesson)} annulée : {lesson.reason}", user, by_teacher)
    _after_change(lesson, lesson.room_id)


def add_extra(course, day, starts_at, ends_at, room=None, reason=None, user=None, now=None):
    """Ajoute une séance (rattrapage). Renvoie (séance, avertissements)."""
    now = now or datetime.now()
    by_teacher = user is not None and not is_manager(user)
    if day is None or starts_at is None or ends_at is None:
        raise LessonError("Indiquez la date et l'horaire.")
    if ends_at <= starts_at:
        raise LessonError("La séance finit avant de commencer.")
    if not course.season.starts_on <= day <= course.season.ends_on:
        raise LessonError(f"La date doit être comprise dans la saison {course.season.label}.")
    if by_teacher and datetime.combine(day, starts_at) <= now:
        raise LessonError("On n'ajoute pas une séance dans le passé.")
    lesson = Lesson(course=course, day=day, starts_at=starts_at, ends_at=ends_at, room=room, revision=0)
    lesson.reason = (reason or "").strip()[:200] or None
    lesson.previous = {"extra": True}
    db.session.add(lesson)
    _record(lesson, user, now)
    db.session.flush()
    warnings = conflicts(lesson, day, starts_at, ends_at, room)
    _announce(lesson, f"Séance ajoutée : {describe(lesson)}", user, by_teacher)
    _after_change(lesson, None)
    return lesson, warnings


def set_substitute(lesson, teacher, user=None):
    """Le secrétariat confie la séance à un remplaçant (ou le retire)."""
    if teacher is not None and teacher == lesson.course.teacher:
        raise LessonError("C'est déjà le professeur du cours.")
    lesson.previous = _snapshot(lesson)
    lesson.substitute = teacher
    _record(lesson, user, datetime.now())
    db.session.flush()
    who = teacher.person.full_name if teacher else "personne"
    log("lesson.substitute", f"Remplaçant de {describe(lesson)} : {who}", user=user)
    _after_change(lesson, lesson.room_id)  # le nom du professeur figure dans l'agenda de la salle
    if teacher is not None and teacher.person.account is not None:
        from . import notify

        notify.notify(
            [teacher.person.account],
            f"Vous remplacez {lesson.course.title} le {day_label(lesson.day)} à {lesson.starts_at:%H:%M}.",
            _link("lessons.mine"),
        )


def revert(lesson, user=None):
    """Le secrétariat revient sur la dernière modification. Une séance ajoutée disparaît (ou est annulée si l'appel
    a été fait)."""
    previous = lesson.previous
    if not previous:
        raise LessonError("Il n'y a pas de modification à annuler.")
    old_room = lesson.room_id
    if previous.get("extra"):
        if has_attendance(lesson):
            lesson.status, lesson.reason, lesson.previous = Lesson.CANCELLED, "Séance retirée", None
            lesson.revision += 1
            _after_change(lesson, old_room)
        else:
            _announce(lesson, f"Séance retirée : {describe(lesson)}", user, False)
            _before_delete(lesson)
            course = lesson.course
            db.session.delete(lesson)
            db.session.flush()
            db.session.expire(course, ["lessons"])
            return None
    else:
        lesson.day = date.fromisoformat(previous["day"])
        lesson.starts_at = time.fromisoformat(previous["starts_at"])
        lesson.ends_at = time.fromisoformat(previous["ends_at"])
        lesson.room_id = previous["room_id"]
        lesson.status, lesson.reason = previous["status"], previous["reason"]
        lesson.substitute_id = previous.get("substitute_id")
        lesson.previous = None
        lesson.revision += 1
        lesson.changed_by, lesson.changed_at = user, datetime.now()
        lesson.edited = lesson.extra or (lesson.day, lesson.starts_at, lesson.ends_at, lesson.room_id) != (
            lesson.scheduled_on,
            lesson.course.starts_at,
            lesson.course.ends_at,
            lesson.course.room_id,
        )
    db.session.flush()
    db.session.refresh(lesson)
    state = "annulée" if lesson.cancelled else describe(lesson)
    _announce(lesson, f"Changement annulé par le secrétariat : séance de {lesson.course.title} {state}", user, False)
    _after_change(lesson, old_room)
    return lesson


# ---------------------------------------------------------------------------
# Prévenir
# ---------------------------------------------------------------------------


def family_contacts(lesson):
    """(comptes, adresses sans compte) des responsables des élèves de la séance (l'élève lui-même s'il n'en a pas)."""
    from .courses import guardians_of

    accounts, emails = {}, {}
    for enrollment in students(lesson):
        student = enrollment.membership.person
        for person in guardians_of(student) or [student]:
            if person.account is not None and person.account.active:
                accounts[person.account.id] = person.account
            elif person.email:
                emails.setdefault(person.email.lower(), person.email)
    return list(accounts.values()), list(emails.values())


def _announce(lesson, message, user, by_teacher):
    """Au journal ; aux familles (notification, et email pour celles sans compte) ; au secrétariat si c'est le
    professeur qui a fait le changement."""
    from . import mailer, notify

    log("lesson.change", message, user=user)
    accounts, emails = family_contacts(lesson)
    notify.notify(accounts, message, _link("member.index"))
    if emails and mailer.smtp_enabled():
        subject = f"{lesson.course.title} : changement de séance"
        for address in emails:
            mailer.send_in_background(address, *mailer.render(None, subject, message, "", None))
    if by_teacher:
        notify.notify(
            [a for a in notify.holders("lesson.manage") if a != user],
            f"{user.full_name} : {message}",
            _link("lessons.course", course_id=lesson.course_id),
        )


# ---------------------------------------------------------------------------
# Listes et plannings
# ---------------------------------------------------------------------------


def upcoming_for(teacher, start, end, include_hidden=True):
    """Séances du professeur (ses cours et ses remplacements) entre deux dates, dans l'ordre. `include_hidden` :
    aussi celles des saisons en préparation."""
    from ..models import Season

    query = (
        db.select(Lesson)
        .join(Course)
        .where(
            Lesson.day >= start,
            Lesson.day <= end,
            db.or_(Lesson.substitute_id == teacher.person_id, Course.teacher_id == teacher.person_id),
        )
        .order_by(Lesson.day, Lesson.starts_at)
    )
    if not include_hidden:
        query = query.where(Course.season.has(Season.hidden.is_(False)))
    return db.session.scalars(query).all()


def week_of(day):
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def week_lessons(start, end, room=None, teacher=None, site=None):
    query = db.select(Lesson).join(Course).where(Lesson.day >= start, Lesson.day <= end)
    if room is not None:
        query = query.where(Lesson.room_id == room.id)
    if site is not None:
        query = query.join(Room, Room.id == Lesson.room_id).where(Room.site_id == site.id)
    lessons = db.session.scalars(query.order_by(Lesson.day, Lesson.starts_at)).all()
    if teacher is not None:
        lessons = [lesson for lesson in lessons if lesson.teacher == teacher]
    return lessons


def typical_week(season, room=None, teacher=None, site=None):
    """Les créneaux des cours (semaine type), par jour."""
    query = db.select(Course).where(Course.season_id == season.id, Course.weekday.is_not(None))
    courses = db.session.scalars(query).all()
    if room is not None:
        courses = [c for c in courses if c.room_id == room.id]
    if teacher is not None:
        courses = [c for c in courses if c.teacher == teacher]
    if site is not None:
        courses = [c for c in courses if c.room is not None and c.room.site_id == site.id]
    days = {d: [] for d in range(7)}
    for course in sorted(courses, key=lambda c: (c.starts_at or time.min, c.title)):
        days[course.weekday].append(course)
    return days


def personal_lessons(account, start, end):
    """[(séance, début, fin, pour qui)] d'un compte entre deux dates : ses séances de professeur (pour qui :
    « remplacement » s'il remplace, sinon None), puis celles des élèves qu'il suit (pour qui : l'inscription), avec
    l'heure de passage d'un cours individuel."""
    from .directory import sees_hidden_seasons
    from .registration import guarded_households

    found = []
    person = account.person
    hidden_ok = sees_hidden_seasons(account)
    if person is not None and person.teacher is not None:
        for lesson in upcoming_for(person.teacher, start, end, include_hidden=hidden_ok):
            who = "remplacement" if lesson.substitute == person.teacher else None
            found.append((lesson, lesson.starts_at, lesson.ends_at, who))
    people = {person.id} if person is not None else set()
    for household in guarded_households(account):
        people |= {link.person_id for link in household.dependents}
    for enrollment in attending_enrollments(people):
        if enrollment.course.season.hidden and not hidden_ok:
            continue
        for lesson in enrollment.course.lessons:
            if start <= lesson.day <= end and enrollment.attends_on(lesson.day):
                begin, finish = student_times(enrollment, lesson)
                found.append((lesson, begin, finish, enrollment))
    return sorted(found, key=lambda r: (r[0].day, r[1], r[0].id))


def attending_enrollments(person_ids):
    """Inscriptions actives ou passées de ces personnes (pour leurs agendas)."""
    from ..models import Membership

    if not person_ids:
        return []
    return db.session.scalars(db.select(Enrollment).join(Membership).where(Membership.person_id.in_(person_ids))).all()
