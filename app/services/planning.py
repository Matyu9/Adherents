"""Mise en page des plannings : une semaine en grille horaire, ou un mois en calendrier.

Les séances deviennent des « événements » placés par des classes CSS (static/css/planning-grid.css) : une ligne par
quart d'heure, une colonne par séance qui se chevauche avec une autre le même jour. Une séance passée reste affichée,
grisée. Rien n'est écrit en base.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

SLOT = 15  # minutes par ligne
MAX_LANES = 6
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
DAYS = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
SHORT_DAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")


@dataclass
class Event:
    title: str
    starts_at: time
    ends_at: time
    color: str
    day: date | None = None  # vide dans la semaine type
    weekday: int | None = None
    details: list = field(default_factory=list)  # lignes secondaires : salle, professeur…
    link: str | None = None
    past: bool = False
    cancelled: bool = False
    edited: bool = False
    note: str | None = None
    course_title: str | None = None  # pour la légende (le titre peut porter le prénom d'un enfant)
    # Placement dans la grille, calculé par week_layout
    row: int = 1
    span: int = 1
    lane: int = 1
    lanes: int = 1

    @property
    def tooltip(self):
        """Tout ce que la case n'a pas la place de montrer."""
        parts = [f"{self.starts_at:%H:%M}–{self.ends_at:%H:%M}", self.title, *self.details]
        if self.cancelled:
            parts.append("Annulée" + (f" : {self.note}" if self.note else ""))
        elif self.edited:
            parts.append("Modifiée" + (f" : {self.note}" if self.note else ""))
        return " · ".join(parts)

    @property
    def minutes(self):
        return _minutes(self.starts_at), _minutes(self.ends_at)

    @property
    def classes(self):
        return " ".join(
            c
            for c in (
                f"course-{self.color}",
                f"pl-r-{self.row}",
                f"pl-s-{self.span}",
                f"pl-l-{self.lane}",
                "is-past" if self.past else "",
                "is-cancelled" if self.cancelled else "",
                "is-short" if self.span <= 2 else "",
                "is-narrow" if self.lanes >= 3 else "",
            )
            if c
        )


def _minutes(moment):
    return moment.hour * 60 + moment.minute


def from_lesson(lesson, now, link=None, details=None, starts_at=None, ends_at=None, title=None):
    return Event(
        title=title or lesson.course.title,
        starts_at=starts_at or lesson.starts_at,
        ends_at=ends_at or lesson.ends_at,
        color=lesson.course.color_key,
        day=lesson.day,
        weekday=lesson.day.weekday(),
        details=[d for d in (details or []) if d],
        link=link,
        past=datetime.combine(lesson.day, ends_at or lesson.ends_at) <= now,
        cancelled=lesson.cancelled,
        edited=lesson.edited and not lesson.cancelled,
        note=lesson.reason,
        course_title=lesson.course.title,
    )


def from_course(course, link=None, details=None):
    return Event(
        title=course.title,
        starts_at=course.starts_at,
        ends_at=course.ends_at,
        color=course.color_key,
        weekday=course.weekday,
        details=[d for d in (details or []) if d],
        link=link,
        course_title=course.title,
    )


def legend(events):
    """[(cours, couleur)] des cours présents, dans l'ordre alphabétique."""
    return sorted({(e.course_title or e.title, e.color) for e in events})


def _assign_lanes(events):
    """Les séances qui se chevauchent sont décalées (une « voie » chacune) et se superposent, comme dans un agenda."""
    events = sorted(events, key=lambda e: (e.minutes[0], -e.minutes[1]))
    group, ends, group_end = [], [], -1
    for event in events + [None]:
        if event is None or event.minutes[0] >= group_end:
            for member in group:
                member.lanes = min(len(ends), MAX_LANES)
            if event is None:
                break
            group, ends, group_end = [], [], -1
        start, end = event.minutes
        lane = next((i for i, last in enumerate(ends) if last <= start), None)
        if lane is None:
            ends.append(end)
            lane = len(ends) - 1
        else:
            ends[lane] = end
        event.lane = min(lane + 1, MAX_LANES)
        group.append(event)
        group_end = max(group_end, end)


@dataclass
class Week:
    days: list  # [(date ou None, nom, événements, lanes, aujourd'hui, passé)]
    first_hour: int
    last_hour: int
    rows: int
    hours: list  # [(ligne, « 17:00 »)]
    now_row: int | None
    now_day: int | None


def week_layout(events, monday=None, now=None, typical=False):
    """Une semaine en grille. Les heures montrées vont de la première séance à la dernière (9 h - 18 h au moins).
    Le dimanche n'apparaît que s'il a des séances."""
    now = now or datetime.now()
    first = min((e.minutes[0] // 60 for e in events), default=9)
    last = max(((e.minutes[1] + 59) // 60 for e in events), default=18)
    first, last = min(first, 9), max(last, 18)
    rows = (last - first) * 60 // SLOT
    for event in events:
        start, end = event.minutes
        event.row = max(1, (start - first * 60) // SLOT + 1)
        event.span = max(1, min(rows - event.row + 1, -(-(end - start) // SLOT)))
    by_day = {d: [] for d in range(7)}
    for event in events:
        by_day[event.weekday if event.weekday is not None else event.day.weekday()].append(event)
    shown = range(7) if by_day[6] else range(6)
    days = []
    for index in shown:
        day = monday + timedelta(days=index) if monday and not typical else None
        _assign_lanes(by_day[index])
        lanes = max((e.lanes for e in by_day[index]), default=1)
        days.append(
            {
                "date": day,
                "name": DAYS[index],
                "short": SHORT_DAYS[index],
                "events": sorted(by_day[index], key=lambda e: (e.row, e.lane)),
                "lanes": lanes,
                "today": day == now.date(),
                "past": day is not None and day < now.date(),
            }
        )
    now_row = None
    if not typical and monday and monday <= now.date() <= monday + timedelta(days=6):
        minutes = now.hour * 60 + now.minute
        if first * 60 <= minutes < last * 60:
            now_row = (minutes - first * 60) // SLOT + 1
    return Week(
        days=days,
        first_hour=first,
        last_hour=last,
        rows=rows,
        hours=[((h - first) * 60 // SLOT + 1, f"{h:02d}:00") for h in range(first, last)],
        now_row=now_row,
        now_day=now.date() if now_row else None,
    )


def month_layout(year, month, events, now=None):
    """Un mois en calendrier : des semaines de lundi à dimanche, chaque jour avec ses séances dans l'ordre."""
    now = now or datetime.now()
    by_day = {}
    for event in sorted(events, key=lambda e: (e.day, e.starts_at)):
        by_day.setdefault(event.day, []).append(event)
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        weeks.append(
            [
                {
                    "date": day,
                    "events": by_day.get(day, []),
                    "outside": day.month != month,
                    "today": day == now.date(),
                    "past": day < now.date(),
                }
                for day in week
            ]
        )
    return weeks


def month_bounds(year, month):
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    return weeks[0][0], weeks[-1][-1]


def month_title(year, month):
    return f"{MONTHS[month - 1].capitalize()} {year}"


def week_title(monday):
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        return f"Semaine du {monday.day} au {sunday.day} {MONTHS[sunday.month - 1]} {sunday.year}"
    if monday.year == sunday.year:
        return f"Semaine du {monday.day} {MONTHS[monday.month - 1]} au {sunday.day} {MONTHS[sunday.month - 1]} {sunday.year}"
    return (
        f"Semaine du {monday.day} {MONTHS[monday.month - 1]} {monday.year} "
        f"au {sunday.day} {MONTHS[sunday.month - 1]} {sunday.year}"
    )


def shift_month(year, month, offset):
    index = year * 12 + month - 1 + offset
    return index // 12, index % 12 + 1
