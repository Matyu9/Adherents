"""Présences : l'appel fait par le professeur (ou son remplaçant), corrigé par le secrétariat, vu par les familles.

- Le professeur du cours ou le remplaçant de la séance fait l'appel à partir du jour de la séance, et peut le
  corriger pendant `TEACHER_DAYS` jours ; ensuite, seul le secrétariat (`lesson.manage`) le peut.
- Pas de motif en texte libre : seulement présent, absent ou excusé.
- Les familles voient le statut des séances de leurs enfants, sauf si l'association coupe ce réglage.
- Les élèves qui cumulent les absences non excusées sont signalés à partir d'un seuil, sans valeur par défaut.

Voir docs/cours.md. Les services ne font pas de commit.
"""

from collections import Counter
from datetime import date, datetime, timedelta

from ..extensions import db, fold
from ..models import Attendance, Course, Enrollment, Lesson, Membership
from . import lessons as lesson_service
from . import options
from .activity import log

TEACHER_DAYS = 7


class AttendanceError(ValueError):
    """Opération refusée, avec un message à afficher tel quel."""


def may_record(account, lesson, today=None):
    """(autorisé, raison du refus) pour faire ou corriger l'appel de cette séance."""
    today = today or date.today()
    if lesson.cancelled:
        return False, "La séance est annulée."
    if lesson.day > today:
        return False, "L'appel se fait à partir du jour de la séance."
    if lesson_service.is_manager(account):
        return True, None
    teacher = lesson_service.teacher_of(account)
    if teacher is None or teacher not in (lesson.course.teacher, lesson.substitute):
        return False, "Seuls le professeur de la séance et le secrétariat font l'appel."
    if today - lesson.day > timedelta(days=TEACHER_DAYS):
        return False, f"L'appel ne se corrige plus après {TEACHER_DAYS} jours : demandez au secrétariat."
    return True, None


def sheet(lesson):
    """[(inscription, statut ou None)] : les élèves qui suivent le cours le jour de la séance, par nom."""
    recorded = {a.enrollment_id: a.status for a in lesson.attendance}
    students = sorted(
        lesson_service.students(lesson),
        key=lambda e: (fold(e.membership.person.last_name), fold(e.membership.person.first_name), e.id),
    )
    return [(e, recorded.get(e.id)) for e in students]


def record(lesson, statuses, user=None, today=None, now=None):
    """Enregistre l'appel : {id d'inscription: statut}. Les élèves absents de la feuille sont refusés."""
    allowed, reason = may_record(user, lesson, today) if user is not None else (True, None)
    if not allowed:
        raise AttendanceError(reason)
    expected = {e.id: e for e, _ in sheet(lesson)}
    unknown = set(statuses) - set(expected)
    if unknown:
        raise AttendanceError("Un élève de l'appel ne suit pas ce cours ce jour-là.")
    if any(status not in Attendance.STATUSES for status in statuses.values()):
        raise AttendanceError("Statut inconnu.")
    current = {a.enrollment_id: a for a in lesson.attendance}
    now = now or datetime.now()
    changed = 0
    for enrollment_id, status in statuses.items():
        row = current.get(enrollment_id)
        if row is None:
            db.session.add(
                Attendance(lesson=lesson, enrollment_id=enrollment_id, status=status, recorded_by=user, recorded_at=now)
            )
            changed += 1
        elif row.status != status:
            row.status, row.recorded_by, row.recorded_at = status, user, now
            changed += 1
    db.session.flush()
    if changed:
        counts = Counter(statuses.values())
        log(
            "attendance.record",
            f"Appel de {lesson_service.describe(lesson)} : {counts['present']} présent(s), {counts['absent']} absent(s), "
            f"{counts['excused']} excusé(s)",
            user=user,
        )
    return changed


def summary(lesson):
    """(appel fait, élèves attendus) pour une séance."""
    return len(lesson.attendance), len(lesson_service.students(lesson))


def family_visible():
    return options.flag(options.FAMILY_ATTENDANCE, True)


def threshold():
    return options.positive_int(options.ABSENCE_THRESHOLD)


def repeated_absences(season, limit=None):
    """[(adhésion, absences non excusées, excusées, [(cours, nombre)])] des élèves au seuil ou au-delà, le plus
    absent d'abord. Sans seuil fixé, rien n'est signalé."""
    limit = limit or threshold()
    if not limit:
        return []
    rows = db.session.execute(
        db.select(Membership, Course, Attendance.status, db.func.count(Attendance.id))
        .join(Enrollment, Enrollment.membership_id == Membership.id)
        .join(Attendance, Attendance.enrollment_id == Enrollment.id)
        .join(Lesson, Lesson.id == Attendance.lesson_id)
        .join(Course, Course.id == Lesson.course_id)
        .where(Membership.season_id == season.id, Attendance.status.in_((Attendance.ABSENT, Attendance.EXCUSED)))
        .group_by(Membership.id, Course.id, Attendance.status)
    ).all()
    found = {}
    for membership, course, status, count in rows:
        entry = found.setdefault(membership.id, [membership, 0, 0, Counter()])
        if status == Attendance.ABSENT:
            entry[1] += count
            entry[3][course] += count
        else:
            entry[2] += count
    result = [
        (m, absent, excused, sorted(per_course.items(), key=lambda c: -c[1]))
        for m, absent, excused, per_course in found.values()
        if absent >= limit
    ]
    return sorted(result, key=lambda r: (-r[1], fold(r[0].person.last_name)))


def history(enrollment):
    """[(séance, statut)] des séances de l'inscription dont l'appel est fait, dans l'ordre."""
    return sorted(((a.lesson, a) for a in enrollment.attendance), key=lambda r: (r[0].day, r[0].starts_at))


def forget(person):
    """Anonymisation : les présences de la personne sont effacées."""
    ids = [e.id for m in person.memberships for e in m.enrollments]
    if ids:
        db.session.execute(db.delete(Attendance).where(Attendance.enrollment_id.in_(ids)))
