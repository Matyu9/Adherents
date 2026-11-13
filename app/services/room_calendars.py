"""Agendas des salles (Google Workspace, facultatif). Voir docs/cours.md.

- **Lire** : avant d'enregistrer une séance, l'occupation de la salle dans son agenda (réunions de l'organisation).
  Les événements posés par l'appli elle-même ne comptent pas : ils sont déjà connus comme séances.
- **Réserver** : chaque séance prévue dans une salle reliée devient un événement de l'agenda de la salle, avec un
  identifiant tiré de la séance ; renvoyer ne crée jamais de doublon. Une séance annulée, déplacée ailleurs ou
  supprimée retire son événement. Les envois passent par une file réessayée (`flask run-tasks`).
- **Lister** les bâtiments et les salles de l'organisation, pour les relier à celles du catalogue.

Sans `GOOGLE_SERVICE_ACCOUNT_FILE`, rien ne se passe : les fonctions répondent « rien à signaler ». Google en panne
ne bloque jamais un enregistrement : la page le signale, et la file rattrape son retard.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app

from ..extensions import db, fold
from ..models import Lesson, Room, RoomCalendarJob, Site
from . import google_workspace as google
from .activity import log

logger = logging.getLogger(__name__)
EVENT_PREFIX = "adherents"  # identifiants d'événements : base32hex (0-9, a-v), au moins 5 caractères
MAX_ATTEMPTS = 12


def enabled():
    return google.configured()


def event_id(lesson_id):
    return f"{EVENT_PREFIX}{lesson_id}"


def _zone():
    return ZoneInfo(current_app.config["TIMEZONE"])


# ---------------------------------------------------------------------------
# Lire l'occupation
# ---------------------------------------------------------------------------


def busy(room, start, end):
    """Occupation de la salle dans son agenda entre `start` et `end` (texte court), ou None. Google injoignable ou
    agenda non partagé : « agenda illisible », pour qu'on vérifie à la main."""
    if not enabled() or room is None or not room.calendar_id:
        return None
    zone = _zone()
    try:
        items = google.events(
            room.calendar_id, start.replace(tzinfo=zone).isoformat(), end.replace(tzinfo=zone).isoformat()
        )
    except google.GoogleError as exc:
        logger.warning("Agenda de %s illisible : %s", room.label, exc)
        return "agenda illisible"
    taken = []
    for item in items:
        if item.get("id", "").startswith(EVENT_PREFIX) or item.get("status") == "cancelled":
            continue
        if item.get("transparency") == "transparent":  # « disponible » : ne bloque pas la salle
            continue
        begin = (item.get("start") or {}).get("dateTime")
        finish = (item.get("end") or {}).get("dateTime")
        if begin and finish:
            begin, finish = (
                datetime.fromisoformat(begin).astimezone(zone),
                datetime.fromisoformat(finish).astimezone(zone),
            )
            taken.append(f"{begin:%H:%M}-{finish:%H:%M}")
        else:
            taken.append("toute la journée")
    return "réservée " + ", ".join(taken) if taken else None


# ---------------------------------------------------------------------------
# Réserver : la file d'envoi
# ---------------------------------------------------------------------------


def _payload(lesson):
    zone = current_app.config["TIMEZONE"]
    teacher = lesson.teacher
    summary = lesson.course.title + (f" ({teacher.person.full_name})" if teacher else "")
    return {
        "summary": summary,
        "location": lesson.room.label if lesson.room else None,
        "description": "Séance réservée par l'espace adhérents. Pour la changer, passez par l'appli.",
        "start": {"dateTime": datetime.combine(lesson.day, lesson.starts_at).isoformat(), "timeZone": zone},
        "end": {"dateTime": datetime.combine(lesson.day, lesson.ends_at).isoformat(), "timeZone": zone},
        "transparency": "opaque",
        "extendedProperties": {"private": {"adherents_lesson": str(lesson.id)}},
    }


def _enqueue(calendar_id, lesson_id, action, payload=None):
    """Un seul envoi en attente par (agenda, événement) : le dernier état l'emporte."""
    eid = event_id(lesson_id)
    db.session.execute(
        db.delete(RoomCalendarJob).where(RoomCalendarJob.calendar_id == calendar_id, RoomCalendarJob.event_id == eid)
    )
    db.session.add(
        RoomCalendarJob(
            calendar_id=calendar_id, event_id=eid, action=action, payload=payload, next_try_at=datetime.now()
        )
    )


def lesson_changed(lesson, old_room_id):
    """La séance a changé : son événement suit (et quitte l'agenda de l'ancienne salle)."""
    if not enabled():
        return
    db.session.flush()
    if old_room_id and old_room_id != lesson.room_id:
        old = db.session.get(Room, old_room_id)
        if old is not None and old.calendar_id:
            _enqueue(old.calendar_id, lesson.id, RoomCalendarJob.DELETE)
    room = lesson.room
    if room is not None and room.calendar_id:
        if lesson.status == Lesson.PLANNED:
            _enqueue(room.calendar_id, lesson.id, RoomCalendarJob.PUT, _payload(lesson))
        else:
            _enqueue(room.calendar_id, lesson.id, RoomCalendarJob.DELETE)


def lesson_deleted(lesson):
    if not enabled():
        return
    if lesson.room is not None and lesson.room.calendar_id:
        _enqueue(lesson.room.calendar_id, lesson.id, RoomCalendarJob.DELETE)


def push_upcoming(today=None, user=None):
    """Met en file toutes les séances à venir des salles reliées (première mise en route, salle reliée après coup).
    Renvoie le nombre de séances."""
    today = today or datetime.now().date()
    query = (
        db.select(Lesson)
        .join(Room, Room.id == Lesson.room_id)
        .where(Lesson.day >= today, Room.calendar_id.is_not(None), Lesson.status == Lesson.PLANNED)
    )
    count = 0
    for lesson in db.session.scalars(query):
        _enqueue(lesson.room.calendar_id, lesson.id, RoomCalendarJob.PUT, _payload(lesson))
        count += 1
    if count:
        log("room_calendar.push", f"{count} séance(s) à envoyer aux agendas des salles", user=user)
    return count


def deliver_pending(now=None, limit=200):
    """Envoie ce qui attend. Renvoie (envoyés, à réessayer, abandonnés)."""
    if not enabled():
        return 0, 0, 0
    db.session.flush()  # les envois mis en file juste avant sont dus, eux aussi
    now = now or datetime.now()
    jobs = db.session.scalars(
        db.select(RoomCalendarJob).where(RoomCalendarJob.next_try_at <= now).order_by(RoomCalendarJob.id).limit(limit)
    ).all()
    sent = failed = dropped = 0
    for job in jobs:
        try:
            if job.action == RoomCalendarJob.PUT:
                google.put_event(job.calendar_id, job.event_id, job.payload)
            else:
                google.delete_event(job.calendar_id, job.event_id)
        except google.GoogleError as exc:
            job.attempts += 1
            job.last_error = str(exc)[:300]
            if job.attempts >= MAX_ATTEMPTS:
                log("room_calendar.drop", f"Envoi abandonné vers {job.calendar_id} ({job.event_id}) : {job.last_error}")
                db.session.delete(job)
                dropped += 1
            else:
                job.next_try_at = now + timedelta(minutes=min(2**job.attempts, 360))
                failed += 1
        else:
            db.session.delete(job)
            sent += 1
        db.session.commit()  # chaque envoi réussi est acquis, même si un suivant échoue
    return sent, failed, dropped


def pending_count():
    return db.session.scalar(db.select(db.func.count(RoomCalendarJob.id)))


# ---------------------------------------------------------------------------
# Lister et relier les salles de l'organisation
# ---------------------------------------------------------------------------


@dataclass
class SyncPlan:
    new: list = field(default_factory=list)  # (ressource, nom du bâtiment)
    renamed: list = field(default_factory=list)  # (salle, ressource)
    linkable: list = field(default_factory=list)  # (salle sans agenda, ressource proposée)
    gone: list = field(default_factory=list)  # salles reliées absentes de l'organisation
    unshared: list = field(default_factory=list)  # salles reliées dont l'agenda n'est pas lisible
    unlinked: list = field(default_factory=list)  # salles du catalogue sans agenda, sans proposition
    resources: dict = field(default_factory=dict)  # adresse → ressource


def _building_names():
    return {b.get("buildingId"): b.get("buildingName") or b.get("buildingId") for b in google.buildings()}


def sync_plan(check_access=True):
    """Compare les salles de l'organisation à celles du catalogue. N'écrit rien."""
    plan = SyncPlan()
    names = _building_names()
    resources = [
        r
        for r in google.resources()
        if r.get("resourceEmail") and r.get("resourceCategory", "CONFERENCE_ROOM") != "OTHER"
    ]
    plan.resources = {r["resourceEmail"]: r for r in resources}
    rooms = db.session.scalars(db.select(Room)).all()
    linked = {room.calendar_id: room for room in rooms if room.calendar_id}
    free = [room for room in rooms if not room.calendar_id and not room.archived]
    for email, resource in plan.resources.items():
        building = names.get(resource.get("buildingId")) if resource.get("buildingId") else None
        room = linked.get(email)
        if room is not None:
            if room.name != _room_name(resource):
                plan.renamed.append((room, resource))
            continue
        match = next((r for r in free if fold(r.name) == fold(_room_name(resource))), None)
        if match is not None:
            plan.linkable.append((match, resource))
            free.remove(match)
        else:
            plan.new.append((resource, building))
    plan.gone = [room for email, room in linked.items() if email not in plan.resources and not room.archived]
    plan.unlinked = free
    if check_access:
        start = datetime.now().replace(microsecond=0)
        for room in linked.values():
            if room.calendar_id in plan.resources and not room.archived and busy_check_fails(room, start):
                plan.unshared.append(room)
    return plan


def _room_name(resource):
    return (resource.get("resourceName") or resource["resourceEmail"])[:80]


def busy_check_fails(room, start):
    zone = _zone()
    try:
        google.events(
            room.calendar_id,
            start.replace(tzinfo=zone).isoformat(),
            (start + timedelta(hours=1)).replace(tzinfo=zone).isoformat(),
        )
    except google.GoogleError:
        return True
    return False


def apply_sync(create=(), rename=(), link=(), archive=(), user=None):
    """Applique les choix faits à l'écran : `create` et `link` sont des adresses de ressources (link : {salle: adresse}),
    `rename` et `archive` des identifiants de salles. Renvoie un résumé."""
    plan = sync_plan(check_access=False)
    done = {"créées": 0, "renommées": 0, "reliées": 0, "archivées": 0}
    for resource, building in plan.new:
        if resource["resourceEmail"] not in create:
            continue
        site = _site_for(resource.get("buildingId"), building)
        db.session.add(
            Room(
                site=site,
                name=_room_name(resource),
                calendar_id=resource["resourceEmail"],
                capacity=resource.get("capacity"),
            )
        )
        done["créées"] += 1
    for room, resource in plan.renamed:
        if room.id in rename:
            room.name, room.capacity = _room_name(resource), resource.get("capacity") or room.capacity
            done["renommées"] += 1
    for room_id, email in dict(link).items():
        room = db.session.get(Room, room_id)
        resource = plan.resources.get(email)
        if room is None or resource is None or room.calendar_id:
            continue
        if db.session.scalar(db.select(Room).filter_by(calendar_id=email)) is not None:
            continue
        room.calendar_id, room.capacity = email, resource.get("capacity") or room.capacity
        done["reliées"] += 1
    for room in plan.gone:
        if room.id in archive:
            room.archived_at = datetime.now()
            done["archivées"] += 1
    db.session.flush()
    summary = ", ".join(f"{count} {label}" for label, count in done.items() if count) or "rien"
    log("room_calendar.sync", f"Salles de l'organisation : {summary}", user=user)
    return done


def _site_for(building_id, building_name):
    if building_id:
        site = db.session.scalar(db.select(Site).filter_by(external_id=building_id))
        if site is not None:
            return site
    name = (building_name or "Organisation")[:80]
    site = next((s for s in db.session.scalars(db.select(Site)) if fold(s.name) == fold(name)), None)
    if site is None:
        site = Site(name=name)
        db.session.add(site)
    if building_id and site.external_id is None:
        site.external_id = building_id
    db.session.flush()
    return site
