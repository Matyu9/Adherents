"""Jours fériés calculés, import de vacances (.ics, CSV), cours maintenus, calendriers et salles."""

from datetime import date, time

import pytest

from app.extensions import db
from app.models import Lesson, Notification, SeasonBreak
from app.services import holidays, lessons

from .conftest import enroll, login
from .test_lessons import BEFORE, OCT14, generated, lesson_on, school  # noqa: F401 (fixtures)


def page(response):
    return response.get_data(as_text=True)


def test_paques_et_jours_feries():
    assert holidays.easter(2027) == date(2027, 3, 28)
    assert holidays.easter(2026) == date(2026, 4, 5)
    found = dict(holidays.public_holidays(date(2026, 9, 1), date(2027, 6, 30)))
    assert found[date(2026, 11, 11)] == "Armistice 1918"
    assert found[date(2027, 3, 29)] == "Lundi de Pâques"
    assert found[date(2027, 5, 6)] == "Ascension"
    assert found[date(2027, 5, 17)] == "Lundi de Pentecôte"
    assert date(2026, 7, 14) not in found and len(found) == 9


ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:20261017\r
DTEND;VALUE=DATE:20261102\r
SUMMARY:Vacances de la Toussaint - Zone B\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART:20261219T000000Z\r
DTEND:20270104T000000Z\r
SUMMARY:Vacances de No\r
 ël\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:20250101\r
SUMMARY:Ancienne\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_lecture_ics():
    rows = holidays.parse("zone-b.ics", ICS.encode())
    assert [(r.label, r.starts_on, r.ends_on) for r in rows] == [
        ("Vacances de la Toussaint - Zone B", date(2026, 10, 17), date(2026, 11, 1)),  # fin exclue en journée entière
        ("Vacances de Noël", date(2026, 12, 19), date(2027, 1, 4)),  # fin avec heure : gardée
        ("Ancienne", date(2025, 1, 1), date(2025, 1, 1)),
    ]


def test_lecture_csv():
    text = "Nom;Début;Fin\nPont de l'Ascension;14/05/2027;15/05/2027\nFermeture;2027-02-01\n"
    rows = holidays.parse("fermetures.csv", text.encode("cp1252"))
    assert [(r.label, r.starts_on, r.ends_on) for r in rows] == [
        ("Pont de l'Ascension", date(2027, 5, 14), date(2027, 5, 15)),
        ("Fermeture", date(2027, 2, 1), date(2027, 2, 1)),
    ]
    with pytest.raises(holidays.ImportError_, match="Ligne 2"):
        holidays.parse("x.csv", b"A;01/01/2027\nB;pas une date\n")
    with pytest.raises(holidays.ImportError_, match="finit avant"):
        holidays.parse("x.csv", b"A;02/01/2027;01/01/2027\n")


def test_ajouter_les_jours_feries(client, school):
    login(client, enroll(school["manager"]))
    s = school["season"]
    client.post(f"/seances/calendrier/{s.id}/feries")
    kinds = {b.kind for b in s.breaks}
    assert kinds == {SeasonBreak.HOLIDAY} and any(b.starts_on == date(2026, 11, 11) for b in s.breaks)
    count = len(s.breaks)
    response = client.post(f"/seances/calendrier/{s.id}/feries", follow_redirects=True)
    assert "déjà là" in page(response) and len(db.session.get(type(s), s.id).breaks) == count


def test_import_avec_apercu(client, school):
    login(client, enroll(school["manager"]))
    s = school["season"]
    html = page(
        client.post(
            f"/seances/calendrier/{s.id}/importer",
            data={"fichier": (__import__("io").BytesIO(ICS.encode()), "zone-b.ics")},
            content_type="multipart/form-data",
        )
    )
    assert "Vacances de la Toussaint - Zone B" in html and "hors saison" in html and "nouvelle" in html
    assert not s.breaks  # rien d'ajouté avant confirmation
    client.post(
        f"/seances/calendrier/{s.id}/importer",
        data={"action": "confirmer", "kind": "vacances", "ligne": ["2026-10-17|2026-11-01|Vacances de la Toussaint - Zone B"]},
    )
    (record,) = db.session.get(type(s), s.id).breaks
    assert record.kind == "vacances" and record.ends_on == date(2026, 11, 1)
    html = page(
        client.post(
            f"/seances/calendrier/{s.id}/importer",
            data={"fichier": (__import__("io").BytesIO(ICS.encode()), "zone-b.ics")},
            content_type="multipart/form-data",
        )
    )
    assert "déjà là" in html
    html = page(
        client.post(
            f"/seances/calendrier/{s.id}/importer",
            data={"fichier": (__import__("io").BytesIO(b"rien"), "x.txt")},
            content_type="multipart/form-data",
        )
    )
    assert "date illisible" in html or "Aucune période" in html or "au moins un nom" in html


def test_cours_maintenus(client, school):
    login(client, enroll(school["manager"]))
    s = school["season"]
    lessons.add_break(s, "Fête locale", OCT14, OCT14, SeasonBreak.HOLIDAY)
    db.session.commit()
    plan = lessons.generate(s, today=date(2026, 9, 1))
    assert all(day != OCT14 for _course, day in plan.created)
    record = s.breaks[0]
    client.post(f"/seances/periodes/{record.id}/maintenir")
    assert db.session.get(SeasonBreak, record.id).keep_lessons
    plan = lessons.generate(s, today=date(2026, 9, 1))
    assert any(day == OCT14 for _course, day in plan.created)
    assert "cours maintenus" in page(client.get(f"/seances/calendrier?saison={s.id}"))


def test_calendriers_montrent_les_periodes(client, generated):
    login(client, enroll(generated["manager"]))
    lessons.add_break(generated["season"], "Armistice 1918", date(2026, 11, 11), date(2026, 11, 11), "ferie")
    db.session.commit()
    html = page(client.get("/seances/planning?mode=mois&date=2026-11-01"))
    assert 'class="pl-off"' in html and "Armistice 1918" in html
    html = page(client.get("/seances/planning?mode=semaine&date=2026-11-11"))
    assert "is-off" in html and "Armistice 1918" in html
    html = page(client.get(f"/seances/cours/{generated['flute'].id}?vue=calendrier&mois=2026-11"))
    assert "Armistice 1918" in html


def test_professeur_sur_un_jour_ferie_previent_le_secretariat(generated):
    lessons.add_break(generated["season"], "Armistice 1918", date(2026, 10, 21), date(2026, 10, 21), "ferie")
    db.session.flush()
    lesson = lesson_on(generated["flute"], OCT14)
    lessons.move(lesson, date(2026, 10, 21), time(10), time(11), user=generated["paul_account"], now=BEFORE)
    db.session.commit()
    messages = [
        n.message
        for n in db.session.scalars(db.select(Notification).filter_by(account_id=generated["manager"].id))
    ]
    assert any("Attention : jour férié « Armistice 1918 »" in m for m in messages)


def test_page_des_salles(client, generated):
    login(client, enroll(generated["manager"]))
    html = page(client.get("/seances/salles"))
    assert "Salle A" in html and "Salle C" in html and "Cours réguliers" in html
    assert 'href="/seances/salles"' in html  # dans le menu
