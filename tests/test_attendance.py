"""Gestion des cours, étape 6 : présences. Voir docs/cours.md."""

from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import Activity, Attendance, Lesson, Membership
from app.services import attendance, courses, lessons, options, retention

from .conftest import enroll, login
from .test_annuaire import person
from .test_lessons import BEFORE, OCT7, OCT14, OCT21, generated, lesson_on, school  # noqa: F401 (fixtures)


def statuses_of(lesson):
    return {a.enrollment.membership.person.first_name: a.status for a in lesson.attendance}


def alice_enrollment(generated, course="flute"):
    return next(e for e in generated[course].enrollments if e.membership.person.first_name == "Alice")


# ---------------------------------------------------------------------------
# Qui fait l'appel, et quand
# ---------------------------------------------------------------------------


def test_le_professeur_fait_l_appel(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    alice = alice_enrollment(generated)
    assert attendance.sheet(lesson) == [(alice, None)]
    assert attendance.record(lesson, {alice.id: "absent"}, generated["paul_account"], today=OCT14) == 1
    assert statuses_of(lesson) == {"Alice": "absent"} and lesson.attendance[0].recorded_by == generated["paul_account"]
    assert attendance.sheet(lesson) == [(alice, "absent")]
    assert "Appel de Flûte, mercredi 14 octobre" in db.session.scalar(
        db.select(Activity.message).filter_by(action="attendance.record")
    )
    # Corriger : pas de doublon, et rien au journal si rien ne change.
    attendance.record(lesson, {alice.id: "excused"}, generated["paul_account"], today=OCT14)
    assert statuses_of(lesson) == {"Alice": "excused"} and len(lesson.attendance) == 1
    assert attendance.record(lesson, {alice.id: "excused"}, generated["paul_account"], today=OCT14) == 0
    assert attendance.summary(lesson) == (1, 1)


@pytest.mark.parametrize(
    "who, today, message",
    [
        ("lea_account", OCT14, "Seuls le professeur"),  # Léa ne remplace pas cette séance
        ("anne_account", OCT14, "Seuls le professeur"),
        ("paul_account", OCT7, "à partir du jour"),
        ("paul_account", OCT14 + timedelta(days=8), "après 7 jours"),
    ],
)
def test_appel_refuse(generated, who, today, message):
    lesson = lesson_on(generated["flute"], OCT14)
    allowed, reason = attendance.may_record(generated[who], lesson, today)
    assert not allowed and message in reason
    with pytest.raises(attendance.AttendanceError, match=message):
        attendance.record(lesson, {}, generated[who], today=today)


def test_sept_jours_pile_encore_permis(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    assert attendance.may_record(generated["paul_account"], lesson, OCT21)[0]


def test_le_secretariat_corrige_apres_le_delai(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    assert attendance.may_record(generated["manager"], lesson, OCT14 + timedelta(days=60))[0]


def test_le_remplacant_fait_l_appel(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    lessons.set_substitute(lesson, generated["lea"], generated["manager"])
    assert attendance.may_record(generated["lea_account"], lesson, OCT14)[0]


def test_seance_annulee_sans_appel(generated):
    lesson = lesson_on(generated["flute"], OCT14)
    lessons.cancel(lesson, "Grève", generated["manager"], BEFORE)
    assert attendance.may_record(generated["manager"], lesson, OCT14) == (False, "La séance est annulée.")


def test_eleve_hors_de_la_feuille_refuse(generated):
    lesson = lesson_on(generated["flute"], OCT7)
    late = Membership(person=person("Zoé", "Tardive"), season=generated["season"])
    db.session.add(late)
    db.session.flush()
    enrollment, _ = courses.enroll(late, generated["flute"], OCT14)
    assert [e for e, _ in attendance.sheet(lesson)] == [alice_enrollment(generated)]  # pas encore arrivée
    with pytest.raises(attendance.AttendanceError, match="ne suit pas"):
        attendance.record(lesson, {enrollment.id: "present"}, generated["manager"], today=OCT14)
    with pytest.raises(attendance.AttendanceError, match="inconnu"):
        attendance.record(lesson, {alice_enrollment(generated).id: "malade"}, generated["manager"], today=OCT14)


def test_seance_avec_appel_annulee_plutot_que_supprimee(generated):
    lesson = lesson_on(generated["flute"], OCT21)
    attendance.record(lesson, {alice_enrollment(generated).id: "present"}, generated["manager"], today=OCT21)
    lessons.add_break(generated["season"], "Pont", OCT21, OCT21)
    db.session.refresh(generated["season"])
    lessons.generate(generated["season"], today=OCT14, apply=True)
    kept = db.session.get(Lesson, lesson.id)
    assert kept is not None and kept.cancelled and kept.reason == "Plus de cours ce jour-là"


# ---------------------------------------------------------------------------
# Absences répétées, familles, conservation
# ---------------------------------------------------------------------------


def _absences(generated, days, status="absent"):
    alice = alice_enrollment(generated)
    for day in days:
        attendance.record(lesson_on(generated["flute"], day), {alice.id: status}, generated["manager"], today=day)


def test_absences_repetees_sans_seuil_rien(generated):
    _absences(generated, [OCT7, OCT14])
    assert attendance.threshold() is None and attendance.repeated_absences(generated["season"]) == []


def test_absences_repetees(generated):
    _absences(generated, [OCT7, OCT14])
    _absences(generated, [OCT21], "excused")
    options.put(options.ABSENCE_THRESHOLD, 2)
    ((membership, absent, excused, per_course),) = attendance.repeated_absences(generated["season"])
    assert (membership.person.first_name, absent, excused) == ("Alice", 2, 1)
    assert [(c.title, n) for c, n in per_course] == [("Flûte", 2)]
    options.put(options.ABSENCE_THRESHOLD, 3)  # les excusées ne comptent pas
    assert attendance.repeated_absences(generated["season"]) == []


def test_reglage_des_familles(generated):
    assert attendance.family_visible() is True  # actif par défaut
    options.put(options.FAMILY_ATTENDANCE, "0")
    assert attendance.family_visible() is False


def test_anonymisation_efface_les_presences(generated):
    _absences(generated, [OCT7])
    alice = alice_enrollment(generated).membership.person
    retention.anonymize(alice)
    db.session.flush()
    assert db.session.scalar(db.select(db.func.count(Attendance.id))) == 0


def test_historique_d_une_inscription(generated):
    _absences(generated, [OCT14])
    _absences(generated, [OCT7], "present")
    rows = attendance.history(alice_enrollment(generated))
    assert [(lesson.day, a.status) for lesson, a in rows] == [(OCT7, "present"), (OCT14, "absent")]


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------


def page(response):
    assert response.status_code == 200, response.status_code
    return response.get_data(as_text=True)


class _Today(date):
    @classmethod
    def today(cls):
        return OCT14


@pytest.fixture
def on_oct14(monkeypatch):
    from app.services import attendance as service

    monkeypatch.setattr(service, "date", _Today)


def test_ecran_de_l_appel(client, generated, on_oct14):
    login(client, generated["paul_account"])
    lesson = lesson_on(generated["flute"], OCT14)
    alice = alice_enrollment(generated)
    html = page(client.get(f"/seances/{lesson.id}/appel"))
    assert "MARTIN Alice" in html and f'name="e{alice.id}" value="present" checked' in html
    response = client.post(f"/seances/{lesson.id}/appel", data={f"e{alice.id}": "absent"})
    assert response.location.endswith("/seances/mes-seances")
    assert statuses_of(db.session.get(Lesson, lesson.id)) == {"Alice": "absent"}
    assert f'name="e{alice.id}" value="absent" checked' in page(client.get(f"/seances/{lesson.id}/appel"))


def test_appel_en_lecture_seule_trop_tard(client, generated, monkeypatch):
    from app.services import attendance as service

    class Later(date):
        @classmethod
        def today(cls):
            return OCT14 + timedelta(days=30)

    monkeypatch.setattr(service, "date", Later)
    login(client, generated["paul_account"])
    lesson = lesson_on(generated["flute"], OCT14)
    html = page(client.get(f"/seances/{lesson.id}/appel"))
    assert "après 7 jours" in html and "disabled" in html and "Enregistrer l&#39;appel" not in html
    html = page(client.post(f"/seances/{lesson.id}/appel", data={}, follow_redirects=True))
    assert "après 7 jours" in html


def test_appel_refuse_a_une_famille(client, generated):
    login(client, generated["anne_account"])
    lesson = lesson_on(generated["flute"], OCT14)
    assert client.get(f"/seances/{lesson.id}/appel").status_code == 403


def test_secretariat_absences_et_reglages(client, generated, on_oct14):
    login(client, enroll(generated["manager"]))
    s = generated["season"]
    html = page(client.get(f"/seances/absences?saison={s.id}"))
    assert "Aucun seuil n'est fixé" in html  # texte du gabarit, non échappé
    client.post("/seances/presences/reglages", data={"threshold": "1", "family": "1", "saison": s.id})
    assert attendance.threshold() == 1 and attendance.family_visible()
    _absences(generated, [OCT7])
    db.session.commit()
    html = page(client.get(f"/seances/absences?saison={s.id}"))
    assert "MARTIN Alice" in html and "Flûte : 1" in html
    html = page(client.get(f"/seances/cours/{generated['flute'].id}"))
    assert ">1 / 1</a>" in html
    client.post("/seances/presences/reglages", data={"threshold": "", "saison": s.id})
    assert attendance.threshold() is None and attendance.family_visible() is False
    html = page(
        client.post("/seances/presences/reglages", data={"threshold": "0", "saison": s.id}, follow_redirects=True)
    )
    assert "Entre 1 et 100" in html


def test_la_famille_voit_les_absences(client, generated):
    _absences(generated, [OCT14])
    _absences(generated, [OCT7], "present")
    db.session.commit()
    login(client, generated["anne_account"])
    html = page(client.get("/ma-famille"))
    assert "1 présence, 1 absence : 14 oct. 2026 (absent)" in html
    options.put(options.FAMILY_ATTENDANCE, "0")
    db.session.commit()
    assert "absence" not in page(client.get("/ma-famille"))


def test_mes_seances_propose_l_appel(client, generated, monkeypatch):
    from app.blueprints import lessons as blueprint

    class Today(date):
        @classmethod
        def today(cls):
            return OCT14

    monkeypatch.setattr(blueprint, "date", Today)
    from app.services import attendance as service

    monkeypatch.setattr(service, "date", Today)
    login(client, generated["paul_account"])
    html = page(client.get("/seances/mes-seances"))
    assert "Faire l&#39;appel" in html  # expression Jinja : échappée
