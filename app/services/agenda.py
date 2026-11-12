"""Agendas à s'abonner (iCalendar, .ics) : une adresse secrète par compte, lue par Google Agenda, Apple, Outlook…

- un professeur y trouve ses séances et ses remplacements ;
- une famille, les séances de ses enfants (et les siennes), avec l'heure de passage d'un cours individuel.

Aucun nom d'élève dans les événements : l'adresse peut circuler, et le calendrier qui la lit en garde une copie.
Le jeton n'est gardé que sous forme d'empreinte ; en créer un nouveau coupe l'ancienne adresse. Voir docs/cours.md.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import current_app

from ..extensions import db
from ..models import Account, Lesson
from . import lessons as lesson_service
from .activity import log

PAST_DAYS = 30  # les séances passées restent un mois dans l'agenda


def _digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(account):
    """Nouvelle adresse : renvoie le jeton en clair, à montrer une seule fois."""
    raw = secrets.token_urlsafe(32)
    account.calendar_token = _digest(raw)
    log("account.calendar", "Nouvelle adresse d'agenda (l'ancienne ne fonctionne plus)", user=account, target=account)
    db.session.flush()
    return raw


def revoke_token(account):
    account.calendar_token = None
    log("account.calendar", "Adresse d'agenda supprimée", user=account, target=account)
    db.session.flush()


def account_for(raw):
    if not raw or len(raw) > 100:
        return None
    account = db.session.scalar(db.select(Account).filter_by(calendar_token=_digest(raw)))
    return account if account is not None and account.active else None


# ---------------------------------------------------------------------------
# Événements
# ---------------------------------------------------------------------------


def events(account, today=None):
    """[(uid, séance, début, fin, titre)] : séances du professeur, puis celles des élèves suivis par le compte.
    Aucun nom d'élève : le titre est celui du cours."""
    today = today or datetime.now().date()
    start, end = today - timedelta(days=PAST_DAYS), today + timedelta(days=400)
    found = []
    for lesson, begin, finish, who in lesson_service.personal_lessons(account, start, end):
        if who is None or who == "remplacement":
            title = lesson.course.title + (" (remplacement)" if who else "")
            found.append((f"lesson-{lesson.id}", lesson, begin, finish, title))
        else:
            found.append((f"lesson-{lesson.id}-{who.id}", lesson, begin, finish, lesson.course.title))
    return sorted(found, key=lambda e: (e[1].day, e[2], e[0]))


def _escape(text):
    return text.replace("\\", "\\\\").replace(";", r"\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    """Lignes de 75 octets au plus, les suivantes commencent par une espace (RFC 5545)."""
    data, parts = line.encode(), []
    while len(data) > 75:
        cut = 75 if not parts else 74
        while (data[cut] & 0xC0) == 0x80:  # ne pas couper un caractère UTF-8 en deux
            cut -= 1
        parts.append(data[:cut])
        data = data[cut:]
    parts.append(data)
    return b"\r\n ".join(parts).decode()


def _utc(day, moment, zone):
    return datetime.combine(day, moment, zone).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def feed(account, today=None):
    zone = ZoneInfo(current_app.config["TIMEZONE"])
    host = urlsplit(current_app.config["BASE_URL"]).hostname or "adherents"
    name = current_app.config.get("ASSO_NAME", "Adhérents")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Adherents//Seances//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)} : cours",
        f"X-WR-TIMEZONE:{current_app.config['TIMEZONE']}",
    ]
    for uid, lesson, begin, finish, title in events(account, today):
        cancelled = lesson.status == Lesson.CANCELLED
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}@{host}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_utc(lesson.day, begin, zone)}",
            f"DTEND:{_utc(lesson.day, finish, zone)}",
            f"SEQUENCE:{lesson.revision}",
            f"SUMMARY:{_escape(('Annulé : ' if cancelled else '') + title)}",
            f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}",
        ]
        if lesson.room is not None:
            lines.append(f"LOCATION:{_escape(lesson.room.label)}")
        if lesson.reason:
            lines.append(f"DESCRIPTION:{_escape(lesson.reason)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
