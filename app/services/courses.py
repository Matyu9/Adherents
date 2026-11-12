"""Gestion des cours pendant la saison : élèves d'un cours, inscription, transfert et abandon d'un cours.

Chaque changement d'inscription marque la cotisation des foyers de l'élève « à revoir », sans la recalculer.
Voir docs/cours.md. Les services ne font pas de commit.
"""

from datetime import date

from ..extensions import db, fold
from ..models import Assessment, Course, Enrollment, HouseholdMember, Membership, Person
from .activity import log
from .catalog import describe, sorted_courses
from .registration import enrolled_counts

# Au-delà, les motifs les plus anciens sont oubliés : la liste sert à prévenir, pas d'historique (le journal l'est).
MAX_REVIEW_REASONS = 20


class CourseError(ValueError):
    """Opération refusée, avec un message à afficher tel quel."""


# ---------------------------------------------------------------------------
# Élèves d'un cours
# ---------------------------------------------------------------------------


def guardians_of(person):
    """Responsables des foyers où la personne est enfant (sans doublon), dans l'ordre des foyers."""
    found = {}
    for link in person.household_links:
        if link.role != HouseholdMember.DEPENDENT:
            continue
        for guardian in link.household.guardians:
            if guardian.person is not person:
                found.setdefault(guardian.person.id, guardian.person)
    return list(found.values())


def _by_name(enrollment):
    person = enrollment.membership.person
    return fold(person.last_name), fold(person.first_name), enrollment.id


def roster(course):
    """(inscrits, partis) : les inscriptions actives, puis celles abandonnées ou transférées, par nom."""
    active = sorted((e for e in course.enrollments if e.active), key=_by_name)
    gone = sorted((e for e in course.enrollments if not e.active), key=_by_name)
    return active, gone


def recipients(course):
    """Adresses à qui écrire pour les élèves inscrits : leurs responsables, ou l'élève lui-même s'il n'en a pas."""
    emails = {}
    for enrollment in roster(course)[0]:
        student = enrollment.membership.person
        people = guardians_of(student) or [student]
        for person in people:
            if person.email:
                emails.setdefault(person.email.lower(), person.email)
    return sorted(emails.values(), key=str.lower)


ROSTER_COLUMNS = ("nom", "prenom", "naissance", "inscrit_le", "responsables", "telephones", "emails")


def roster_rows(course):
    rows = []
    for enrollment in roster(course)[0]:
        student = enrollment.membership.person
        contacts = guardians_of(student) or [student]
        rows.append(
            (
                student.last_name,
                student.first_name,
                student.birth_date.isoformat() if student.birth_date else "",
                enrollment.enrolled_on.isoformat(),
                ", ".join(p.full_name for p in contacts if p is not student),
                ", ".join(phone.number for p in contacts for phone in p.phones[:1]),
                ", ".join(p.email for p in contacts if p.email),
            )
        )
    return rows


def candidates(course):
    """Adhésions actives de la saison du cours qui n'y sont pas inscrites (inscription abandonnée comprise)."""
    taken = {e.membership_id for e in course.enrollments if e.active}
    query = (
        db.select(Membership)
        .join(Person)
        .where(
            Membership.season_id == course.season_id,
            Membership.status == Membership.ACTIVE,
            Person.archived_at.is_(None),
        )
    )
    memberships = [m for m in db.session.scalars(query) if m.id not in taken]
    return sorted(memberships, key=lambda m: (fold(m.person.last_name), fold(m.person.first_name), m.id))


def open_courses(membership, exclude=None):
    """Cours de la saison de l'adhésion où l'élève n'est pas déjà inscrit, avec leur nombre d'inscrits."""
    taken = {e.course_id for e in membership.enrollments if e.active}
    if exclude is not None:
        taken.add(exclude.id)
    courses = db.session.scalars(db.select(Course).where(Course.season_id == membership.season_id)).all()
    return sorted_courses(c for c in courses if c.id not in taken), enrolled_counts(membership.season)


# ---------------------------------------------------------------------------
# Inscrire, transférer, abandonner
# ---------------------------------------------------------------------------


def _check_date(on, membership, after=None):
    season = membership.season
    if on is None:
        raise CourseError("Indiquez la date.")
    if not season.starts_on <= on <= season.ends_on:
        raise CourseError(f"La date doit être comprise dans la saison {season.label}.")
    if after is not None and on < after:
        raise CourseError(f"La date ne peut pas précéder l'inscription au cours, le {after:%d/%m/%Y}.")


def enroll(membership, course, on=None, replaces=None, user=None):
    """Inscrit l'élève au cours à la date `on` (aujourd'hui par défaut). Une inscription abandonnée au même cours est
    reprise plutôt que doublée. Renvoie (inscription, avertissements)."""
    on = on or date.today()
    if course.season_id != membership.season_id:
        raise CourseError("Le cours et l'adhésion ne sont pas de la même saison.")
    if membership.status != Membership.ACTIVE:
        raise CourseError(f"L'adhésion {membership.season.label} de {membership.person.full_name} est abandonnée.")
    _check_date(on, membership)
    existing = next((e for e in membership.enrollments if e.course_id == course.id), None)
    if existing is not None and existing.active:
        raise CourseError(f"{membership.person.full_name} est déjà inscrit(e) à ce cours.")
    warnings = []
    if course.is_full(enrolled_counts(course.season).get(course.id, 0)):
        warnings.append(f"{course.title} était complet : {membership.person.full_name} y est inscrit(e) quand même.")
    if existing is not None:
        existing.status, existing.withdrawn_on, existing.enrolled_on = Enrollment.ACTIVE, None, on
        existing.replaces = replaces
        enrollment = existing
    else:
        enrollment = Enrollment(membership=membership, course=course, enrolled_on=on, replaces=replaces)
        db.session.add(enrollment)
    db.session.flush()
    if replaces is None:
        person = membership.person
        log("enrollment.create", f"{person.full_name} inscrit(e) à {describe(course)}", user=user, person=person)
        flag_review(person, membership.season, f"{person.full_name} inscrit(e) à {course.title} le {on:%d/%m/%Y}")
    return enrollment, warnings


def withdraw(enrollment, on=None, user=None, quiet=False):
    """Note l'abandon du cours à la date `on` (aujourd'hui par défaut). L'adhésion reste."""
    on = on or date.today()
    if not enrollment.active:
        raise CourseError("Cette inscription est déjà terminée.")
    _check_date(on, enrollment.membership, after=enrollment.enrolled_on)
    enrollment.status, enrollment.withdrawn_on = Enrollment.WITHDRAWN, on
    db.session.flush()
    if not quiet:
        person, course = enrollment.membership.person, enrollment.course
        log("enrollment.withdraw", f"{person.full_name} quitte {describe(course)}", user=user, person=person)
        flag_review(person, course.season, f"{person.full_name} a quitté {course.title} le {on:%d/%m/%Y}")
    return enrollment


def transfer(enrollment, course, on=None, user=None):
    """Passe l'élève dans un autre cours de la même saison à la date `on` : l'ancienne inscription se termine ce
    jour-là (`withdrawn_on` est le premier jour où l'élève n'y est plus), la nouvelle commence le même jour.
    Renvoie (nouvelle inscription, avertissements)."""
    on = on or date.today()
    if not enrollment.active:
        raise CourseError("Cette inscription est déjà terminée.")
    if course.id == enrollment.course_id:
        raise CourseError("Choisissez un autre cours.")
    membership = enrollment.membership
    _check_date(on, membership, after=enrollment.enrolled_on)
    old_course = enrollment.course
    withdraw(enrollment, on, user=user, quiet=True)
    new, warnings = enroll(membership, course, on, replaces=enrollment, user=user)
    person = membership.person
    log(
        "enrollment.transfer",
        f"{person.full_name} passe de {describe(old_course)} à {describe(course)}",
        user=user,
        person=person,
    )
    flag_review(
        person,
        membership.season,
        f"{person.full_name} est passé(e) de {old_course.title} à {course.title} le {on:%d/%m/%Y}",
    )
    return new, warnings


# ---------------------------------------------------------------------------
# Cotisation « à revoir »
# ---------------------------------------------------------------------------


def flag_review(person, season, reason):
    """Ajoute le motif aux cotisations déjà calculées de la saison, pour chaque foyer de l'élève. Ne recalcule rien.
    Renvoie les cotisations marquées."""
    household_ids = [link.household_id for link in person.household_links]
    if not household_ids:
        return []
    flagged = db.session.scalars(
        db.select(Assessment).where(Assessment.season_id == season.id, Assessment.household_id.in_(household_ids))
    ).all()
    for assessment in flagged:
        reasons = [r for r in (assessment.review_reasons or []) if r != reason] + [reason]
        assessment.review_reasons = reasons[-MAX_REVIEW_REASONS:]
    db.session.flush()
    return flagged


# ---------------------------------------------------------------------------
# Cours individuels : heure de passage et durée de chaque élève
# ---------------------------------------------------------------------------

MAX_SLOT_MINUTES = 240


def _minutes(moment):
    return moment.hour * 60 + moment.minute


def _span(enrollment):
    """(début, fin) du passage en minutes depuis minuit, ou None."""
    if enrollment.slot_starts_at is None or not enrollment.slot_minutes:
        return None
    start = _minutes(enrollment.slot_starts_at)
    return start, start + enrollment.slot_minutes


def _course_span(course):
    if course.starts_at is None:
        return None
    return _minutes(course.starts_at), _minutes(course.ends_at)


def _hhmm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def slot_warnings(enrollment, others=None):
    """Avertissements sur le passage d'un élève : hors du créneau du cours, chevauchement avec un autre élève du
    même cours, ou avec un autre cours de l'élève le même jour. Rien n'est refusé."""
    span = _span(enrollment)
    if span is None:
        return []
    course, warnings = enrollment.course, []
    if (frame := _course_span(course)) and (span[0] < frame[0] or span[1] > frame[1]):
        warnings.append(f"Hors du créneau du cours ({_hhmm(frame[0])}-{_hhmm(frame[1])}).")
    others = others if others is not None else [e for e in course.enrollments if e.active]
    for other in others:
        if other is enrollment or (o := _span(other)) is None:
            continue
        if span[0] < o[1] and o[0] < span[1]:
            warnings.append(
                f"Chevauche le passage de {other.membership.person.full_name} ({_hhmm(o[0])}-{_hhmm(o[1])})."
            )
    for mine in enrollment.membership.enrollments:
        other_course = mine.course
        if mine is enrollment or not mine.active or other_course.weekday != course.weekday or course.weekday is None:
            continue
        o = _span(mine) if other_course.individual else _course_span(other_course)
        if o and span[0] < o[1] and o[0] < span[1]:
            warnings.append(f"L'élève a aussi {other_course.title} à {_hhmm(o[0])}.")
    return warnings


def set_slot(enrollment, starts_at, minutes, user=None):
    """Fixe l'heure de passage et la durée (les deux vides : on efface). Renvoie les avertissements."""
    course = enrollment.course
    if not course.individual:
        raise CourseError("L'heure de passage ne concerne que les cours individuels.")
    if not enrollment.active:
        raise CourseError("Cette inscription est terminée.")
    if minutes is not None and not 0 < minutes <= MAX_SLOT_MINUTES:
        raise CourseError(f"La durée doit être comprise entre 1 et {MAX_SLOT_MINUTES} minutes.")
    if starts_at is not None and minutes is None:
        raise CourseError("Indiquez la durée du passage.")
    if starts_at is not None and _minutes(starts_at) + minutes > 24 * 60:
        raise CourseError("Le passage doit finir avant minuit.")
    before = (enrollment.slot_starts_at, enrollment.slot_minutes)
    enrollment.slot_starts_at, enrollment.slot_minutes = starts_at, minutes
    db.session.flush()
    if before != (starts_at, minutes):
        person = enrollment.membership.person
        label = f"{starts_at:%H:%M}, {minutes} min" if starts_at else (f"{minutes} min" if minutes else "effacé")
        log("enrollment.slot", f"Passage de {person.full_name} en {course.title} : {label}", user=user, person=person)
    return slot_warnings(enrollment)


def planning(course):
    """Planning d'un cours individuel : [(inscription, avertissements)] par heure de passage (sans heure à la fin),
    et les plages libres du créneau [(début, fin)] en « HH:MM »."""
    active = [e for e in course.enrollments if e.active]
    rows = sorted(
        active,
        key=lambda e: (e.slot_starts_at is None, e.slot_starts_at or e.enrolled_on, *_by_name(e)),
    )
    result = [(e, slot_warnings(e, active)) for e in rows]
    free = []
    if frame := _course_span(course):
        cursor = frame[0]
        for start, end in sorted(s for e in active if (s := _span(e))):
            if start > cursor:
                free.append((cursor, min(start, frame[1])))
            cursor = max(cursor, end)
        if cursor < frame[1]:
            free.append((cursor, frame[1]))
    return result, [(_hhmm(a), _hhmm(b)) for a, b in free if b > a]
