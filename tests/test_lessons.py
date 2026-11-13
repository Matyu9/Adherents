"""Gestion des cours, étape 4 : calendrier de la saison, séances, déplacements par le professeur, plannings, agenda
à s'abonner. Voir docs/cours.md."""

from datetime import date, datetime, time

import pytest
from icalendar import Calendar

from app.extensions import db
from app.models import (
    Activity,
    Course,
    Discipline,
    HouseholdMember,
    Lesson,
    Membership,
    Notification,
    Person,
    Role,
    Room,
    Site,
    Teacher,
)
from app.services import agenda, courses, directory, lessons, options
from app.services.lessons import LessonError

from .conftest import enroll, login, make_account
from .test_annuaire import home, person, season

G, D = HouseholdMember.GUARDIAN, HouseholdMember.DEPENDENT
# Mercredis d'octobre 2026 : 7, 14, 21, 28. Changement d'heure le dimanche 25 octobre.
OCT7, OCT14, OCT21, OCT28 = date(2026, 10, 7), date(2026, 10, 14), date(2026, 10, 21), date(2026, 10, 28)
BEFORE = datetime(2026, 10, 1, 9)  # « maintenant » dans la plupart des tests


def at(text):
    return time.fromisoformat(text)


def messages(account):
    return [n.message for n in db.session.scalars(db.select(Notification).filter_by(account_id=account.id))]


@pytest.fixture
def school(app):
    """Saison 2026-2027, cours du 7 octobre au 28 octobre. Flûte le mercredi 17 h-18 h salle A avec Paul ;
    solfège le mercredi 18 h-19 h salle B avec Paul ; piano individuel le mercredi 17 h-19 h salle B avec Léa.
    Alice (flûte, piano à 17 h 30) a pour mère Anne, qui a un compte ; Basile (solfège) n'a pas de responsable avec
    compte, mais une adresse."""
    s = season("2026-2027", current=True)
    s.lessons_from, s.lessons_until = OCT7, OCT28
    site = Site(name="École")
    room_a, room_b, room_c = (
        Room(site=site, name="Salle A"),
        Room(site=site, name="Salle B"),
        Room(site=site, name="Salle C"),
    )
    flute, fm, piano = Discipline(name="Flûte"), Discipline(name="Solfège"), Discipline(name="Piano")
    db.session.add_all([site, room_a, room_b, room_c, flute, fm, piano])
    paul_p, lea_p = person("Paul", "Prof"), person("Léa", "Remplaçante")
    paul_p.teacher, lea_p.teacher = Teacher(status="CDI"), Teacher(status="CDD")
    db.session.flush()
    paul, lea = paul_p.teacher, lea_p.teacher
    c_flute = Course(
        season=s, discipline=flute, teacher=paul, weekday=2, starts_at=at("17:00"), ends_at=at("18:00"), room=room_a
    )
    c_fm = Course(
        season=s, discipline=fm, teacher=paul, weekday=2, starts_at=at("18:00"), ends_at=at("19:00"), room=room_b
    )
    c_piano = Course(
        season=s,
        discipline=piano,
        teacher=lea,
        weekday=2,
        starts_at=at("17:00"),
        ends_at=at("19:00"),
        room=room_b,
        individual=True,
    )
    db.session.add_all([c_flute, c_fm, c_piano])
    family = home("Famille Martin")
    anne = person("Anne", "Martin", email="anne@asso.test")
    directory.add_member(family, anne, G)
    alice, basile = person("Alice", "Martin"), person("Basile", "Durand")
    directory.add_member(family, alice, D)
    other = home("Famille Durand")
    marc = person("Marc", "Durand", email="marc@asso.test")
    directory.add_member(other, marc, G)
    directory.add_member(other, basile, D)
    m_alice, m_basile = Membership(person=alice, season=s), Membership(person=basile, season=s)
    db.session.add_all([m_alice, m_basile])
    db.session.flush()
    courses.enroll(m_alice, c_flute, date(2026, 9, 1))
    e_piano, _ = courses.enroll(m_alice, c_piano, date(2026, 9, 1))
    courses.set_slot(e_piano, at("17:30"), 30)
    courses.enroll(m_basile, c_fm, date(2026, 9, 1))
    paul_account = make_account(Role.ADHERENT, email="paul@asso.test")
    paul_p.account = paul_account
    lea_account = make_account(Role.ADHERENT, email="lea@asso.test")
    lea_p.account = lea_account
    anne_account = make_account(Role.ADHERENT, email="anne@asso.test")
    anne.account = anne_account
    manager = make_account(Role.GESTIONNAIRE, email="gestion@asso.test")
    db.session.commit()
    return {
        "season": s,
        "a": room_a,
        "b": room_b,
        "c": room_c,
        "flute": c_flute,
        "fm": c_fm,
        "piano": c_piano,
        "paul": paul,
        "lea": lea,
        "paul_account": paul_account,
        "lea_account": lea_account,
        "anne_account": anne_account,
        "manager": manager,
        "e_piano": e_piano,
        "m_alice": m_alice,
    }


@pytest.fixture
def generated(school):
    lessons.generate(school["season"], today=date(2026, 9, 1), apply=True)
    db.session.commit()
    return school


def lesson_on(course, day):
    return next(lesson for lesson in course.lessons if lesson.day == day)


# ---------------------------------------------------------------------------
# Calendrier et génération
# ---------------------------------------------------------------------------


def test_jours_de_cours_hors_vacances(school):
    assert lessons.scheduled_days(school["flute"]) == [OCT7, OCT14, OCT21, OCT28]
    lessons.add_break(school["season"], "Toussaint", date(2026, 10, 17), date(2026, 11, 1))
    db.session.refresh(school["season"])
    assert lessons.scheduled_days(school["flute"]) == [OCT7, OCT14]
    school["flute"].weekday = None
    assert lessons.scheduled_days(school["flute"]) == []


def test_fenetre_par_defaut_la_saison(school):
    s = school["season"]
    lessons.set_window(s, None, None)
    assert s.lessons_window == (s.starts_on, s.ends_on)
    with pytest.raises(LessonError, match="pendant la saison"):
        lessons.set_window(s, date(2026, 8, 1), None)
    with pytest.raises(LessonError, match="précède"):
        lessons.set_window(s, OCT28, OCT7)


@pytest.mark.parametrize(
    "label, start, end, message",
    [
        ("", OCT7, None, "Donnez un nom"),
        ("Pont", OCT14, OCT7, "finit avant"),
        ("Été", date(2028, 7, 1), None, "toucher la saison"),
    ],
)
def test_periode_refusee(school, label, start, end, message):
    with pytest.raises(LessonError, match=message):
        lessons.add_break(school["season"], label, start, end)


def test_un_jour_ferie(school):
    record = lessons.add_break(school["season"], "11 novembre", date(2026, 11, 11), None)
    assert record.starts_on == record.ends_on == date(2026, 11, 11)


def test_apercu_puis_generation(school):
    plan = lessons.generate(school["season"], today=date(2026, 9, 1))
    assert len(plan.created) == 12 and db.session.scalar(db.select(db.func.count(Lesson.id))) == 0
    lessons.generate(school["season"], today=date(2026, 9, 1), apply=True)
    flute = school["flute"]
    assert [(lesson.day, lesson.starts_at, lesson.room) for lesson in flute.lessons][0] == (
        OCT7,
        at("17:00"),
        school["a"],
    )
    assert actions("lesson.generate")[0].message == "Séances 2026-2027 : 12 créée(s), 0 recalée(s), 0 retirée(s)"
    # On peut la relancer : rien à faire.
    assert lessons.generate(school["season"], today=date(2026, 9, 1)).changes == 0


def actions(name):
    return db.session.scalars(db.select(Activity).filter_by(action=name)).all()


def test_nouvelle_generation_recale_l_avenir_seulement(generated):
    flute = generated["flute"]
    flute.starts_at, flute.ends_at, flute.room = at("16:00"), at("17:00"), generated["c"]
    moved = lesson_on(flute, OCT21)
    lessons.move(moved, OCT21, at("15:00"), at("16:00"), generated["a"], now=BEFORE)  # modifiée à la main
    plan = lessons.generate(generated["season"], today=OCT14, apply=True)
    assert [lesson.day for lesson in plan.updated] == [OCT14, OCT28]
    assert lesson_on(flute, OCT7).starts_at == at("17:00")  # passée : intacte
    assert lesson_on(flute, OCT21).starts_at == at("15:00")  # modifiée : intacte
    assert lesson_on(flute, OCT28).room == generated["c"] and lesson_on(flute, OCT28).revision == 1


def test_periode_ajoutee_retire_les_seances(generated):
    lessons.add_break(generated["season"], "Toussaint", date(2026, 10, 17), date(2026, 11, 1))
    db.session.refresh(generated["season"])
    plan = lessons.generate(generated["season"], today=date(2026, 9, 1), apply=True)
    assert len(plan.removed) == 6
    assert [lesson.day for lesson in generated["flute"].lessons] == [OCT7, OCT14]


def test_cours_sans_horaire_signale(school):
    school["fm"].starts_at = school["fm"].ends_at = None
    plan = lessons.generate(school["season"], today=date(2026, 9, 1))
    assert plan.skipped == [school["fm"]]


# ---------------------------------------------------------------------------
# Déplacer, annuler, ajouter, revenir en arrière
# ---------------------------------------------------------------------------


def test_le_professeur_deplace_sa_seance(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    paul = generated["paul_account"]
    warnings = lessons.move(
        lesson, date(2026, 10, 15), at("17:00"), at("18:00"), generated["c"], "Salle en travaux", paul, BEFORE
    )
    assert warnings == []
    assert (lesson.day, lesson.room, lesson.edited, lesson.revision, lesson.changed_by) == (
        date(2026, 10, 15),
        generated["c"],
        True,
        1,
        paul,
    )
    assert lesson.previous["day"] == "2026-10-14" and lesson.reason == "Salle en travaux"
    expected = "Séance de Flûte, mercredi 14 octobre 17:00-18:00, Salle A (École) déplacée : jeudi 15 octobre 17:00-18:00, Salle C (École)"
    assert messages(generated["anne_account"]) == [expected]
    assert messages(generated["manager"]) == [f"Paul Martin : {expected}"] or messages(generated["manager"])[
        0
    ].endswith(expected)
    assert messages(paul) == []


def test_changement_par_le_secretariat_sans_avis_au_secretariat(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    lessons.move(lesson, OCT14, at("16:00"), at("17:00"), generated["a"], None, generated["manager"], BEFORE)
    assert messages(generated["manager"]) == [] and len(messages(generated["anne_account"])) == 1


def test_famille_sans_compte_recoit_un_email(generated, monkeypatch):
    from app.services import mailer

    sent = []
    monkeypatch.setattr(mailer, "smtp_enabled", lambda: True)
    monkeypatch.setattr(mailer, "send_in_background", lambda to, subject, text, html=None: sent.append((to, subject)))
    lessons.cancel(lesson_on(generated["fm"], OCT14), "Professeur malade", generated["paul_account"], BEFORE)
    assert sent == [("marc@asso.test", "[Mon association] Solfège : changement de séance")]


@pytest.mark.parametrize(
    "case, message",
    [
        ("commencée", "déjà commencé"),
        ("délai", "moins de 48 heure"),
        ("vers le passé", "dans le passé"),
        ("à l'envers", "finit avant"),
        ("hors saison", "comprise dans la saison"),
        ("rien", "Rien n'a changé"),
        ("annulée", "annulée"),
    ],
)
def test_deplacement_refuse(generated, case, message):
    lesson = lesson_on(generated["flute"], OCT14)
    now, day, start, end, room = BEFORE, OCT21, at("17:00"), at("18:00"), generated["a"]
    if case == "commencée":
        now = datetime(2026, 10, 14, 17, 30)
    elif case == "délai":
        options.put(options.TEACHER_DELAY, 48)
        now = datetime(2026, 10, 13, 9)
    elif case == "vers le passé":
        now, day = datetime(2026, 10, 12, 9), date(2026, 10, 11)
    elif case == "à l'envers":
        end = at("16:00")
    elif case == "hors saison":
        day = date(2027, 9, 8)
    elif case == "rien":
        day = OCT14
    elif case == "annulée":
        lesson.status = Lesson.CANCELLED
    with pytest.raises(LessonError, match=message):
        lessons.move(lesson, day, start, end, room, user=generated["paul_account"], now=now)


def test_le_secretariat_n_a_pas_de_delai(generated):
    options.put(options.TEACHER_DELAY, 48)
    lesson = lesson_on(generated["flute"], OCT14)
    lessons.move(
        lesson,
        OCT14,
        at("16:00"),
        at("17:00"),
        generated["a"],
        user=generated["manager"],
        now=datetime(2026, 10, 14, 17, 30),
    )
    assert lesson.starts_at == at("16:00")


def test_avertissements_au_deplacement(generated):
    lessons.add_break(generated["season"], "Pont", date(2026, 10, 30), date(2026, 10, 30))
    db.session.refresh(generated["season"])
    lesson = lesson_on(generated["flute"], OCT14)
    # Salle B, 18 h : solfège y est (salle prise), Paul y enseigne (professeur pris).
    warnings = lessons.conflicts(lesson, OCT14, at("18:00"), at("19:00"), generated["b"])
    assert "Salle B (École) est prise par Solfège (18:00)." in warnings
    assert "Paul Prof a déjà Solfège à 18:00." in warnings
    # Alice a son passage de piano le même jour : 17 h 30 tombe dans le créneau du piano.
    warnings = lessons.conflicts(lesson, OCT14, at("17:00"), at("18:00"), generated["c"])
    assert "Alice Martin a Piano à 17:00." in warnings
    assert lessons.conflicts(lesson, date(2026, 10, 30), at("10:00"), at("11:00"), None) == [
        "Ce jour tombe pendant une période sans cours."
    ]


def test_salles_libres_d_abord(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    rooms = lessons.free_rooms(lesson, OCT14, at("18:00"), at("19:00"))
    assert rooms == [(generated["a"], None), (generated["c"], None), (generated["b"], "Piano, Solfège")]


def test_annuler(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    with pytest.raises(LessonError, match="motif"):
        lessons.cancel(lesson, " ", generated["paul_account"], BEFORE)
    lessons.cancel(lesson, "Professeur malade", generated["paul_account"], BEFORE)
    assert lesson.cancelled and lesson.reason == "Professeur malade" and lesson.previous["status"] == "planned"
    with pytest.raises(LessonError, match="déjà annulée"):
        lessons.cancel(lesson, "encore", generated["paul_account"], BEFORE)


def test_seance_ajoutee_puis_retiree(generated):
    lesson, warnings = lessons.add_extra(
        generated["flute"],
        date(2026, 10, 31),
        at("10:00"),
        at("11:00"),
        generated["a"],
        "Rattrapage",
        generated["paul_account"],
        BEFORE,
    )
    assert lesson.extra and lesson.previous == {"extra": True} and warnings == []
    assert messages(generated["anne_account"])[-1].startswith("Séance ajoutée : Flûte, samedi 31 octobre 10:00-11:00")
    with pytest.raises(LessonError, match="passé"):
        lessons.add_extra(
            generated["flute"], date(2026, 9, 2), at("10:00"), at("11:00"), user=generated["paul_account"], now=BEFORE
        )
    assert lessons.revert(lesson, generated["manager"]) is None
    assert db.session.get(Lesson, lesson.id) is None


def test_revenir_en_arriere(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    lessons.move(
        lesson, date(2026, 10, 15), at("16:00"), at("17:00"), generated["c"], None, generated["paul_account"], BEFORE
    )
    restored = lessons.revert(lesson, generated["manager"])
    assert (restored.day, restored.starts_at, restored.room, restored.edited, restored.previous) == (
        OCT14,
        at("17:00"),
        generated["a"],
        False,
        None,
    )
    assert restored.revision == 2
    assert messages(generated["anne_account"])[-1].startswith("Changement annulé par le secrétariat")
    with pytest.raises(LessonError, match="pas de modification"):
        lessons.revert(restored)


def test_revenir_sur_une_annulation(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    lessons.cancel(lesson, "Grève", generated["paul_account"], BEFORE)
    lessons.revert(lesson, generated["manager"])
    assert not lesson.cancelled and lesson.reason is None


def test_remplacant(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    lessons.set_substitute(lesson, generated["lea"], generated["manager"])
    assert lesson.teacher == generated["lea"]
    assert messages(generated["lea_account"]) == ["Vous remplacez Flûte le mercredi 14 octobre à 17:00."]
    assert lessons.can_view(generated["lea_account"], lesson) and not lessons.can_edit(generated["lea_account"], lesson)
    with pytest.raises(LessonError, match="déjà le professeur"):
        lessons.set_substitute(lesson, generated["paul"], generated["manager"])
    lessons.revert(lesson, generated["manager"])
    assert lesson.substitute is None


def test_droits_sur_les_seances(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    assert lessons.can_edit(generated["paul_account"], lesson) and lessons.can_edit(generated["manager"], lesson)
    assert not lessons.can_edit(generated["anne_account"], lesson) and not lessons.can_view(
        generated["anne_account"], lesson
    )
    assert not lessons.can_edit(generated["lea_account"], lesson)


def test_heure_de_passage_suit_la_seance(generated):
    lesson = lesson_on(generated["piano"], OCT14)
    assert lessons.student_times(generated["e_piano"], lesson) == (at("17:30"), at("18:00"))
    lessons.move(lesson, OCT14, at("16:00"), at("18:00"), generated["b"], now=BEFORE)
    assert lessons.student_times(generated["e_piano"], lesson) == (at("16:30"), at("17:00"))


def test_eleve_arrive_en_cours_de_saison(generated):
    lesson = lesson_on(generated["flute"], OCT7)
    late = Membership(person=person("Zoé", "Tardive"), season=generated["season"])
    db.session.add(late)
    db.session.flush()
    courses.enroll(late, generated["flute"], OCT14)
    assert [e.membership.person.first_name for e in lessons.students(lesson)] == ["Alice"]
    assert len(lessons.students(lesson_on(generated["flute"], OCT14))) == 2


# ---------------------------------------------------------------------------
# Agenda à s'abonner
# ---------------------------------------------------------------------------


def events_of(text):
    return [c for c in Calendar.from_ical(text).walk() if c.name == "VEVENT"]


def test_jeton_d_agenda(generated):
    account = generated["paul_account"]
    raw = agenda.issue_token(account)
    assert agenda.account_for(raw) == account and account.calendar_token != raw
    newer = agenda.issue_token(account)
    assert agenda.account_for(raw) is None and agenda.account_for(newer) == account
    agenda.revoke_token(account)
    assert agenda.account_for(newer) is None and agenda.account_for("x" * 200) is None
    account.active = False
    raw = agenda.issue_token(account)
    assert agenda.account_for(raw) is None


def test_agenda_du_professeur(generated):
    lessons.set_substitute(lesson_on(generated["piano"], OCT21), generated["paul"], generated["manager"])
    lessons.cancel(lesson_on(generated["flute"], OCT14), "Grève", generated["paul_account"], BEFORE)
    events = events_of(agenda.feed(generated["paul_account"], today=date(2026, 10, 1)))
    titles = [str(e["SUMMARY"]) for e in events]
    assert len(events) == 9 and "Piano (remplacement)" in titles and "Annulé : Flûte" in titles
    cancelled = next(e for e in events if str(e["SUMMARY"]) == "Annulé : Flûte")
    assert str(cancelled["STATUS"]) == "CANCELLED" and int(cancelled["SEQUENCE"]) == 1
    assert str(cancelled["DESCRIPTION"]) == "Grève" and str(cancelled["LOCATION"]) == "Salle A (École)"


def test_agenda_de_la_famille_et_changement_d_heure(generated):
    events = events_of(agenda.feed(generated["anne_account"], today=date(2026, 10, 1)))
    assert len(events) == 8  # flûte et piano d'Alice, 4 mercredis
    starts = sorted((e.decoded("DTSTART"), str(e["SUMMARY"])) for e in events)
    # Heure d'été jusqu'au 25 octobre (UTC+2), puis d'hiver (UTC+1).
    assert starts[0][0] == datetime(2026, 10, 7, 15, 0, tzinfo=starts[0][0].tzinfo) and starts[0][1] == "Flûte"
    assert starts[1][0].hour == 15 and starts[1][0].minute == 30  # passage de piano à 17 h 30
    assert starts[-1][0].hour == 16 and starts[-1][0].minute == 30
    text = agenda.feed(generated["anne_account"], today=date(2026, 10, 1))
    assert "Alice" not in text and "Martin" not in text  # aucun nom d'élève
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))


def test_agenda_vide_pour_qui_n_a_rien(generated):
    assert events_of(agenda.feed(generated["manager"])) == []


def test_lignes_longues_repliees_sans_couper_un_accent(generated):
    generated["flute"].group_label = "é" * 60
    lesson_on(generated["flute"], OCT14).reason = "à" * 100
    text = agenda.feed(generated["anne_account"], today=date(2026, 10, 1))
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    event = next(e for e in events_of(text) if e.get("DESCRIPTION"))
    assert str(event["DESCRIPTION"]) == "à" * 100


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------


def page(response):
    assert response.status_code == 200, response.status_code
    return response.get_data(as_text=True)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return BEFORE


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Les écrans prennent l'heure réelle : on la fixe au 1er octobre 2026, avant les séances des tests."""
    monkeypatch.setattr(lessons, "datetime", _FrozenDateTime)


@pytest.fixture
def manager_client(client, generated):
    login(client, enroll(generated["manager"]))
    return client


@pytest.fixture
def paul_client(client, generated):
    login(client, generated["paul_account"])
    return client


def test_ecran_du_calendrier(client, school):
    login(client, enroll(school["manager"]))
    s = school["season"]
    html = page(client.get(f"/seances/calendrier?saison={s.id}"))
    assert "12 séance(s) à créer" in html
    response = client.post(
        f"/seances/calendrier/{s.id}/periodes",
        data={"label": "Toussaint", "starts_on": "2026-10-17", "ends_on": "2026-11-01"},
    )
    assert response.status_code == 302
    html = page(client.get(f"/seances/calendrier?saison={s.id}"))
    assert "Toussaint" in html and "6 séance(s) à créer" in html
    client.post(f"/seances/calendrier/{s.id}/generer")
    assert db.session.scalar(db.select(db.func.count(Lesson.id))) == 6
    assert "Les séances sont à jour." in page(client.get(f"/seances/calendrier?saison={s.id}"))
    record = s.breaks[0]
    client.post(f"/seances/periodes/{record.id}/supprimer")
    assert "6 séance(s) à créer" in page(client.get(f"/seances/calendrier?saison={s.id}"))
    html = page(
        client.post(
            "/seances/calendrier", data={"saison": s.id, "lessons_from": "2026-10-28", "lessons_until": "2026-10-07"}
        )
    )
    assert "précède" in html
    client.post("/seances/reglages", data={"hours": "48", "saison": s.id})
    assert options.positive_int(options.TEACHER_DELAY) == 48
    client.post("/seances/reglages", data={"hours": "", "saison": s.id})
    assert options.get(options.TEACHER_DELAY) is None


def test_ecran_mes_seances(paul_client, generated, monkeypatch):
    from app.blueprints import lessons as lessons_blueprint

    monkeypatch.setattr(lessons_blueprint, "date", _FrozenDate)
    html = page(paul_client.get("/seances/mes-seances"))
    assert "Flûte" in html and "Solfège" in html and "Piano" not in html


class _FrozenDate(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 10)


def test_deplacer_depuis_l_ecran(paul_client, generated):
    lesson = lesson_on(generated["flute"], OCT28)
    html = page(paul_client.get(f"/seances/{lesson.id}/modifier?day=2026-10-28&starts_at=18:00&ends_at=19:00"))
    assert "Salle B (École) — occupée (Piano, Solfège)" in html and "Salle C (École) — libre" in html
    assert "Paul Prof a déjà Solfège à 18:00." in html
    response = paul_client.post(
        f"/seances/{lesson.id}/modifier",
        data={
            "day": "2026-10-29",
            "starts_at": "17:00",
            "ends_at": "18:00",
            "room_id": generated["c"].id,
            "reason": "Travaux",
        },
    )
    assert response.status_code == 302
    lesson = db.session.get(Lesson, lesson.id)
    assert (lesson.day, lesson.room_id) == (date(2026, 10, 29), generated["c"].id)


def test_autre_professeur_refuse(client, generated):
    login(client, generated["lea_account"])
    lesson = lesson_on(generated["flute"], OCT28)
    assert client.get(f"/seances/{lesson.id}/modifier").status_code == 403
    assert client.post(f"/seances/{lesson.id}/annuler", data={"reason": "x"}).status_code == 403
    assert client.get(f"/seances/cours/{generated['flute'].id}/ajouter").status_code == 403
    assert client.get(f"/seances/cours/{generated['flute'].id}").status_code == 403
    assert client.post(f"/seances/{lesson.id}/revenir").status_code == 403


def test_famille_refusee(client, generated):
    login(client, generated["anne_account"])
    lesson = lesson_on(generated["flute"], OCT28)
    assert client.get(f"/seances/{lesson.id}/modifier").status_code == 403
    assert client.get("/seances/mes-seances").status_code == 404


def test_annuler_et_ajouter_depuis_l_ecran(paul_client, generated):
    lesson = lesson_on(generated["flute"], OCT28)
    response = paul_client.post(f"/seances/{lesson.id}/annuler", data={"reason": "Malade", "retour": "mes-seances"})
    assert response.location.endswith("/seances/mes-seances")
    assert db.session.get(Lesson, lesson.id).cancelled
    html = page(paul_client.post(f"/seances/{lesson.id}/annuler", data={"reason": "Encore"}, follow_redirects=True))
    assert "déjà annulée" in html
    response = paul_client.post(
        f"/seances/cours/{generated['flute'].id}/ajouter",
        data={"day": "2026-11-04", "starts_at": "17:00", "ends_at": "18:00", "room_id": "", "reason": "Rattrapage"},
    )
    assert response.status_code == 302
    assert any(lesson.extra for lesson in db.session.get(Course, generated["flute"].id).lessons)


def test_secretariat_revient_et_nomme_un_remplacant(manager_client, generated):
    lesson = lesson_on(generated["flute"], OCT28)
    lessons.cancel(lesson, "Malade", generated["paul_account"], BEFORE)
    db.session.commit()
    html = page(manager_client.get(f"/seances/cours/{generated['flute'].id}"))
    assert "annulée" in html and "Revenir en arrière" in html
    assert f"par {generated['paul_account'].full_name}" in html
    manager_client.post(f"/seances/{lesson.id}/revenir")
    assert not db.session.get(Lesson, lesson.id).cancelled
    manager_client.post(f"/seances/{lesson.id}/remplacant", data={"teacher_id": generated["lea"].person_id})
    assert db.session.get(Lesson, lesson.id).substitute_id == generated["lea"].person_id
    assert manager_client.post(f"/seances/{lesson.id}/remplacant", data={"teacher_id": 99999}).status_code == 404


def test_plannings(manager_client, generated):
    html = page(manager_client.get("/seances/planning?date=2026-10-14&vue=salle&id=" + str(generated["b"].id)))
    assert "Semaine du 12 au 18 octobre 2026" in html and "Solfège" in html and "Flûte" not in html
    html = page(manager_client.get(f"/seances/planning?vue=prof&id={generated['paul'].person_id}&date=2026-10-14"))
    assert "Flûte" in html and "Piano" not in html
    html = page(manager_client.get(f"/seances/planning?vue=site&id={generated['a'].site_id}&date=2026-10-14"))
    assert "Flûte" in html and "Piano" in html
    html = page(manager_client.get(f"/seances/planning?mode=type&saison={generated['season'].id}"))
    assert "<h1>Semaine type</h1>" in html and "Flûte" in html
    html = page(manager_client.get("/seances/planning?mode=mois&date=2026-10-14"))
    assert "<h1>Octobre 2026</h1>" in html and html.count("pl-pill") == 12
    assert page(manager_client.get("/seances/planning?date=pas-une-date&mode=inconnu"))


def test_ecran_agenda_et_flux(client, generated):
    login(client, generated["anne_account"])
    html = page(client.post("/seances/agenda"))
    import re

    url = re.search(r'value="(http[^"]+\.ics)"', html).group(1)
    response = client.get(url.replace("http://localhost", ""))
    assert response.status_code == 200 and response.mimetype == "text/calendar"
    assert response.headers["Cache-Control"] == "private, max-age=300"
    assert "Copiez-la maintenant" in html
    assert "Copiez-la maintenant" not in page(client.get("/seances/agenda"))
    client.post("/seances/agenda", data={"action": "supprimer"})
    assert client.get(url.replace("http://localhost", "")).status_code == 404
    assert client.get("/seances/agenda/inconnu.ics").status_code == 404


def test_accueil_montre_les_prochaines_seances(app, client, generated, monkeypatch):
    """Accueil d'Anne le 10 octobre : les séances d'Alice du 14 (flûte, et piano à son heure de passage)."""
    from app.blueprints import dashboard

    monkeypatch.setattr(dashboard, "date", _FrozenDate)
    monkeypatch.setattr(dashboard, "datetime", _FrozenDateTime)
    login(client, generated["anne_account"])
    html = page(client.get("/"))
    assert "Prochaines séances" in html and "Alice · Flûte" in html
    assert "17:30-18:00" in html  # heure de passage du cours individuel
    assert "Solfège" not in html  # Basile n'est pas de sa famille
    teacher = app.test_client()
    login(teacher, generated["paul_account"])
    html = page(teacher.get("/"))
    assert "Flûte" in html and "Solfège" in html and "Alice" not in html


def test_une_saison_en_preparation_est_cachee_aux_professeurs(app, client, generated, monkeypatch):
    from app.blueprints import dashboard
    from app.blueprints import lessons as lessons_blueprint

    monkeypatch.setattr(lessons_blueprint, "date", _FrozenDate)
    monkeypatch.setattr(dashboard, "date", _FrozenDate)
    monkeypatch.setattr(dashboard, "datetime", _FrozenDateTime)
    generated["season"].hidden = True
    db.session.commit()
    login(client, generated["paul_account"])
    assert "Flûte" not in page(client.get("/seances/mes-seances"))
    assert "Flûte" not in page(client.get("/"))
    assert "Flûte" not in page(client.get("/inscription/mes-eleves"))
    staff = app.test_client()
    login(staff, enroll(generated["manager"]))
    assert "Flûte" in page(staff.get(f"/catalogue?saison={generated['season'].id}"))
    assert "en préparation" in page(staff.get("/annuaire/saisons"))


def test_fiche_eleve_pour_le_professeur(app, paul_client, generated):
    from app.models import Enrollment, Notification

    alice = generated["m_alice"].person
    s = generated["season"]
    html = page(paul_client.get(f"/inscription/mes-eleves?saison={s.id}"))
    assert f"/inscription/mes-eleves/{alice.id}" in html
    html = page(paul_client.get(f"/inscription/mes-eleves/{alice.id}"))
    assert "Alice Martin" in html and "Anne Martin" in html and "anne@asso.test" in html
    assert "Piano" in html and "avec vous" in html  # tous ses cours, dont celui de Léa
    assert "Présences à vos cours" in html
    # Ajouter Alice au solfège de Paul : le secrétariat est prévenu.
    response = paul_client.post(f"/inscription/mes-eleves/{alice.id}/cours", data={"course_id": generated["fm"].id})
    assert response.status_code == 302
    assert db.session.scalar(
        db.select(Enrollment).filter_by(membership_id=generated["m_alice"].id, course_id=generated["fm"].id)
    ).active
    assert any("a inscrit Alice Martin à Solfège" in m for m in messages(generated["manager"]))
    # Pas le cours d'un autre professeur, ni un élève qui n'est pas à lui.
    assert paul_client.post(
        f"/inscription/mes-eleves/{alice.id}/cours", data={"course_id": generated["piano"].id}
    ).status_code == 404
    lea_client = app.test_client()
    login(lea_client, generated["lea_account"])
    basile_id = db.session.scalar(db.select(Person.id).filter_by(first_name="Basile"))
    assert lea_client.get(f"/inscription/mes-eleves/{basile_id}").status_code == 404


def test_menu_d_un_professeur_sans_famille(app, paul_client, generated):
    html = page(paul_client.get("/"))
    assert 'href="/ma-famille"' not in html and 'href="/inscription"' not in html
    assert 'href="/inscription/mes-eleves"' in html and 'href="/seances/mes-seances"' in html
    parent = app.test_client()
    login(parent, generated["anne_account"])
    html = page(parent.get("/"))
    assert 'href="/ma-famille"' in html and 'href="/inscription"' in html


def test_page_des_seances_resume_liste_et_calendrier(manager_client, generated, monkeypatch):
    from app.blueprints import lessons as lessons_blueprint

    monkeypatch.setattr(lessons_blueprint, "date", _FrozenDate)  # le 10 octobre
    course = generated["flute"]
    html = page(manager_client.get(f"/seances/cours/{course.id}"))
    assert "À venir" in html and "Passées (1)" in html  # la séance du 7 octobre est passée, repliée
    assert 'class="dropdown row-menu"' in html and "Modifier la séance" in html
    assert "Mercredi 17:00-18:00" in html and "Présence" in html
    html = page(manager_client.get(f"/seances/cours/{course.id}?vue=calendrier"))
    assert "Octobre 2026" in html and html.count('class="pl-pill') == 4
    html = page(manager_client.get(f"/seances/cours/{course.id}?vue=calendrier&mois=2026-11"))
    assert "Novembre 2026" in html and html.count('class="pl-pill') == 1  # le 28 octobre, en tête de grille
    assert manager_client.get(f"/seances/cours/{course.id}?vue=calendrier&mois=2026-13").status_code == 200
