"""Catalogue : disciplines, niveaux, lieux et cours de chaque saison. Les services ne font pas de commit."""
from dataclasses import dataclass, field
from datetime import datetime

from ..extensions import db, fold
from ..models import Course, Discipline, Level, Person, Room, Site, Teacher, TeacherDiscipline


class CatalogError(ValueError):
    """Opération refusée, avec un message à afficher tel quel."""


# ---------------------------------------------------------------------------
# Listes de référence : disciplines, niveaux, sites, salles
# ---------------------------------------------------------------------------

def disciplines(include_archived=False):
    query = db.select(Discipline)
    if not include_archived:
        query = query.where(Discipline.archived_at.is_(None))
    return sorted(db.session.scalars(query), key=lambda d: (d.archived, fold(d.category or "~"), fold(d.name)))


def levels(include_archived=False):
    query = db.select(Level).order_by(Level.position, Level.name)
    if not include_archived:
        query = query.where(Level.archived_at.is_(None))
    return sorted(db.session.scalars(query), key=lambda lv: lv.archived)


def sites(include_archived=False):
    query = db.select(Site)
    if not include_archived:
        query = query.where(Site.archived_at.is_(None))
    return sorted(db.session.scalars(query), key=lambda s: (s.archived, fold(s.name)))


def rooms(include_archived=False):
    """Salles des sites ouverts (toutes, archivées comprises, si `include_archived`)."""
    query = db.select(Room).join(Site)
    if not include_archived:
        query = query.where(Room.archived_at.is_(None), Site.archived_at.is_(None))
    return sorted(db.session.scalars(query), key=lambda r: (fold(r.site.name), fold(r.name)))


def room_open(room):
    return room is not None and not room.archived and not room.site.archived


def usage(item):
    """Nombre d'utilisations d'une valeur de référence : on ne supprime que ce qui n'a jamais servi."""
    if isinstance(item, Discipline):
        return (_count(Course.id, Course.discipline_id == item.id)
                + _count(TeacherDiscipline.season_id, TeacherDiscipline.discipline_id == item.id))
    if isinstance(item, Level):
        return _count(Course.id, Course.level_id == item.id)
    if isinstance(item, Room):
        return _count(Course.id, Course.room_id == item.id)
    if isinstance(item, Site):
        return _count(Room.id, Room.site_id == item.id)
    raise TypeError(item)


def _count(column, condition):
    return db.session.scalar(db.select(db.func.count(column)).where(condition))


def archive(item):
    item.archived_at = datetime.now()


def restore(item):
    item.archived_at = None


# ---------------------------------------------------------------------------
# Professeurs d'une saison
# ---------------------------------------------------------------------------

def teachers_for(season, keep=None):
    """Professeurs présents pendant la saison, par nom. `keep` : celui déjà choisi, gardé même s'il est parti, pour
    qu'enregistrer un cours ne le retire pas sans qu'on l'ait décidé."""
    items = [t for t in db.session.scalars(db.select(Teacher).join(Person).where(Person.archived_at.is_(None)))
             if t.teaches_during(season)]
    if keep is not None and keep not in items:
        items.append(keep)
    return sorted(items, key=lambda t: (fold(t.person.last_name), fold(t.person.first_name)))


def disciplines_of(teacher, season):
    return sorted((td.discipline for td in teacher.taught if td.season == season), key=lambda d: fold(d.name))


def set_disciplines(teacher, season, wanted):
    """Remplace les disciplines enseignées pendant la saison. Renvoie (ajoutées, retirées).

    Une discipline dont le professeur a un cours cette saison-là ne se retire pas."""
    wanted = {d.id: d for d in wanted}
    current = {td.discipline.id: td for td in teacher.taught if td.season == season}
    in_use = {c.discipline.id for c in teacher.courses if c.season == season}
    if blocked := sorted(current[d].discipline.name for d in set(current) - set(wanted) if d in in_use):
        raise CatalogError("Ce professeur a des cours de " + ", ".join(blocked) + " cette saison : changez d'abord "
                           "le professeur de ces cours.")
    added = [wanted[d] for d in wanted if d not in current]
    removed = [current[d].discipline for d in current if d not in wanted]
    for discipline_id in set(current) - set(wanted):
        teacher.taught.remove(current[discipline_id])
    for discipline in added:
        teacher.taught.append(TeacherDiscipline(season=season, discipline=discipline))
    return added, removed


def note_discipline(course):
    """Le professeur d'un cours enseigne sa discipline cette saison : on l'ajoute à ses disciplines s'il le faut."""
    teacher = course.teacher
    if teacher is None:
        return False
    if any(td.season == course.season and td.discipline == course.discipline for td in teacher.taught):
        return False
    teacher.taught.append(TeacherDiscipline(season=course.season, discipline=course.discipline))
    return True


def move_teaching(drop, keep):
    """Fusion de deux fiches : les cours et disciplines du professeur `drop` passent à `keep`."""
    held = {(td.season, td.discipline) for td in keep.taught}
    for td in list(drop.taught):
        if (td.season, td.discipline) not in held:
            keep.taught.append(TeacherDiscipline(season=td.season, discipline=td.discipline))
    moved = db.session.execute(db.update(Course).where(Course.teacher_id == drop.person_id)
                               .values(teacher_id=keep.person_id)).rowcount
    db.session.expire(drop, ["courses"])  # sinon, en supprimant `drop`, l'ORM viderait le professeur de ces cours
    return moved


# ---------------------------------------------------------------------------
# Cours
# ---------------------------------------------------------------------------

def course_query(season, discipline_id=None, teacher_id=None, weekday=None, site_id=None):
    query = db.select(Course).where(Course.season_id == season.id)
    if discipline_id:
        query = query.where(Course.discipline_id == discipline_id)
    if teacher_id == "aucun":
        query = query.where(Course.teacher_id.is_(None))
    elif teacher_id:
        query = query.where(Course.teacher_id == teacher_id)
    if weekday is not None:
        query = query.where(Course.weekday == weekday)
    if site_id:
        query = query.where(Course.room.has(Room.site_id == site_id))
    return query


def sorted_courses(courses):
    """Par discipline, niveau, jour et heure : l'ordre dans lequel on cherche un cours."""
    def key(c):
        return (fold(c.discipline.name), c.level.position if c.level else 1_000_000, fold(c.level.name) if c.level else "",
                fold(c.group_label or ""), 7 if c.weekday is None else c.weekday, c.starts_at or datetime.min.time(), c.id or 0)
    return sorted(courses, key=key)


def conflicts(course, others):
    """Chevauchements avec `others` : même salle ou même professeur au même moment. Un avertissement, pas un refus
    (deux groupes peuvent partager une grande salle)."""
    found = []
    for other in others:
        if other is course or (other.id is not None and other.id == course.id) or not course.overlaps(other):
            continue
        if course.room_id is not None and other.room_id == course.room_id:
            found.append((other, "même salle"))
        if course.teacher_id is not None and other.teacher_id == course.teacher_id:
            found.append((other, "même professeur"))
    return found


def season_conflicts(courses):
    """{id du cours: [(autre cours, raison)…]} pour tous les cours d'une saison."""
    by_day = {}
    for course in courses:
        if course.weekday is not None and course.starts_at is not None:
            by_day.setdefault(course.weekday, []).append(course)
    result = {}
    for day in by_day.values():
        for course in day:
            if found := conflicts(course, day):
                result[course.id] = found
    return result


def describe(course):
    """Résumé d'un cours pour le journal et les messages."""
    when = ""
    if course.weekday is not None:
        when = f", {course.weekday_name.lower()}"
        if course.starts_at is not None:
            when += f" {course.starts_at:%H:%M}-{course.ends_at:%H:%M}"
    return f"{course.title} ({course.season.label}{when})"


# ---------------------------------------------------------------------------
# Copie d'une saison sur une autre
# ---------------------------------------------------------------------------

@dataclass
class CopyLine:
    course: Course  # cours de la saison d'origine
    skipped: str | None = None  # raison, si le cours n'est pas recopié
    notes: list = field(default_factory=list)  # ce qui change dans la copie


@dataclass
class CopyPlan:
    source: object
    target: object
    lines: list
    disciplines: list  # [(professeur, discipline)] recopiés
    problems: list  # ce qui empêche de copier

    @property
    def copied(self):
        return [line for line in self.lines if line.skipped is None]

    @property
    def skipped(self):
        return [line for line in self.lines if line.skipped is not None]


def target_has_catalog(season):
    return (_count(Course.id, Course.season_id == season.id)
            + _count(TeacherDiscipline.season_id, TeacherDiscipline.season_id == season.id)) > 0


def copy_plan(source, target):
    """Ce que la copie ferait, sans rien écrire : affiché avant de confirmer."""
    problems = []
    if source is None or target is None:
        problems.append("Choisissez la saison d'origine et la saison à remplir.")
    elif source.id == target.id:
        problems.append("La saison d'origine et la saison à remplir doivent être différentes.")
    elif target_has_catalog(target):
        problems.append(f"La saison {target.label} a déjà des cours ou des disciplines de professeurs : la copie "
                        "ne sert qu'à remplir une saison vide.")
    if problems:
        return CopyPlan(source, target, [], [], problems)

    lines = []
    courses = db.session.scalars(db.select(Course).where(Course.season_id == source.id))
    for course in sorted_courses(courses):
        line = CopyLine(course)
        if course.discipline.archived:
            line.skipped = "discipline archivée"
        else:
            if course.level is not None and course.level.archived:
                line.notes.append("niveau archivé : à choisir")
            if course.room is not None and not room_open(course.room):
                line.notes.append("salle fermée : à choisir")
            if course.teacher is not None and not _teaches(course.teacher, target):
                line.notes.append(f"{course.teacher.person.full_name} ne sera plus là : professeur à trouver")
            if course.marked_full:
                line.notes.append("rouvert aux inscriptions")
        lines.append(line)

    taught = db.session.scalars(db.select(TeacherDiscipline).where(TeacherDiscipline.season_id == source.id))
    kept = [(td.teacher, td.discipline) for td in taught if _teaches(td.teacher, target) and not td.discipline.archived]
    kept.sort(key=lambda pair: (fold(pair[0].person.last_name), fold(pair[0].person.first_name), fold(pair[1].name)))
    return CopyPlan(source, target, lines, kept, [])


def _teaches(teacher, season):
    return teacher.person.archived_at is None and teacher.teaches_during(season)


def copy_season(source, target):
    """Recopie le catalogue de `source` sur `target`, vide. Renvoie le plan suivi."""
    plan = copy_plan(source, target)
    if plan.problems:
        raise CatalogError(" ".join(plan.problems))
    for line in plan.copied:
        old = line.course
        teacher = old.teacher if old.teacher is not None and _teaches(old.teacher, target) else None
        db.session.add(Course(
            season=target, discipline=old.discipline,
            level=old.level if old.level is not None and not old.level.archived else None,
            room=old.room if room_open(old.room) else None,
            teacher=teacher, group_label=old.group_label, weekday=old.weekday, starts_at=old.starts_at,
            ends_at=old.ends_at, capacity=old.capacity, marked_full=False, notes=old.notes))
    for teacher, discipline in plan.disciplines:
        teacher.taught.append(TeacherDiscipline(season=target, discipline=discipline))
    db.session.flush()
    # Un professeur gardé enseigne ses cours recopiés, même si sa discipline n'était pas notée la saison d'avant.
    for course in db.session.scalars(db.select(Course).where(Course.season_id == target.id)):
        note_discipline(course)
    db.session.flush()
    return plan
