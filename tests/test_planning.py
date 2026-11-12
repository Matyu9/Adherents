"""Plannings : mise en page de la semaine et du mois, couleurs des cours, « Mon planning »."""

import re
from datetime import date, datetime, time

import pytest

from app.extensions import db
from app.models import Course
from app.services import courses, lessons, planning

from .conftest import login
from .test_lessons import OCT7, OCT14, generated, lesson_on, school  # noqa: F401 (fixtures)

MONDAY = date(2026, 10, 12)


def event(start, end, day=OCT14, title="Cours", color="bleu"):
    return planning.Event(
        title=title,
        starts_at=time.fromisoformat(start),
        ends_at=time.fromisoformat(end),
        color=color,
        day=day,
        weekday=day.weekday(),
    )


# ---------------------------------------------------------------------------
# Semaine
# ---------------------------------------------------------------------------


def test_lignes_d_un_quart_d_heure():
    e = event("17:00", "18:30")
    week = planning.week_layout([e], MONDAY, datetime(2026, 10, 1))
    assert (week.first_hour, week.last_hour, week.rows) == (9, 19, 40)  # 18 h 30 : la grille va jusqu'à 19 h
    assert (e.row, e.span) == (33, 6)  # 17 h = 8 heures après 9 h = ligne 33 ; 1 h 30 = 6 quarts d'heure
    assert "pl-r-33" in e.classes and "pl-s-6" in e.classes and "course-bleu" in e.classes


def test_heures_elargies_aux_seances():
    early, late = event("07:30", "08:15"), event("20:00", "21:10")
    week = planning.week_layout([early, late], MONDAY, datetime(2026, 10, 1))
    assert (week.first_hour, week.last_hour) == (7, 22)
    assert week.hours[0] == (1, "07:00") and early.row == 3


def test_seances_qui_se_chevauchent_cote_a_cote():
    a, b, c, d = event("17:00", "18:00"), event("17:30", "18:30"), event("18:00", "19:00"), event("20:00", "21:00")
    week = planning.week_layout([a, b, c, d], MONDAY, datetime(2026, 10, 1))
    assert [(e.lane, e.lanes) for e in (a, b, c)] == [(1, 2), (2, 2), (1, 2)]  # c reprend la place libérée par a
    assert (d.lane, d.lanes) == (1, 1)
    wednesday = week.days[2]
    assert wednesday["lanes"] == 2 and wednesday["name"] == "Mercredi"


def test_dimanche_seulement_s_il_sert():
    week = planning.week_layout([event("10:00", "11:00")], MONDAY, datetime(2026, 10, 1))
    assert len(week.days) == 6
    week = planning.week_layout([event("10:00", "11:00", day=date(2026, 10, 18))], MONDAY, datetime(2026, 10, 1))
    assert len(week.days) == 7


def test_aujourd_hui_et_trait_de_l_heure():
    week = planning.week_layout([], MONDAY, datetime(2026, 10, 14, 17, 40))
    assert [d["today"] for d in week.days][2] and week.days[1]["past"] and not week.days[3]["past"]
    assert week.now_row == 35  # 17 h 40 → quart d'heure de 17 h 30, 8 h 30 après 9 h
    assert planning.week_layout([], MONDAY, datetime(2026, 10, 14, 23)).now_row is None


def test_semaine_type_sans_dates():
    week = planning.week_layout([event("10:00", "11:00")], typical=True)
    assert week.days[0]["date"] is None and week.now_row is None


def test_titres():
    assert planning.week_title(MONDAY) == "Semaine du 12 au 18 octobre 2026"
    assert planning.week_title(date(2026, 10, 26)) == "Semaine du 26 octobre au 1 novembre 2026"
    assert planning.week_title(date(2026, 12, 28)) == "Semaine du 28 décembre 2026 au 3 janvier 2027"
    assert planning.month_title(2026, 11) == "Novembre 2026"
    assert planning.shift_month(2026, 12, 1) == (2027, 1) and planning.shift_month(2026, 1, -1) == (2025, 12)


# ---------------------------------------------------------------------------
# Mois
# ---------------------------------------------------------------------------


def test_mois_du_lundi_au_dimanche():
    weeks = planning.month_layout(2026, 10, [event("17:00", "18:00", day=OCT14)], datetime(2026, 10, 14, 9))
    assert weeks[0][0]["date"] == date(2026, 9, 28) and weeks[0][0]["outside"]
    assert weeks[-1][-1]["date"] == date(2026, 11, 1)
    cell = next(c for w in weeks for c in w if c["date"] == OCT14)
    assert cell["today"] and len(cell["events"]) == 1
    assert next(c for w in weeks for c in w if c["date"] == OCT7)["past"]
    assert planning.month_bounds(2026, 10) == (date(2026, 9, 28), date(2026, 11, 1))


# ---------------------------------------------------------------------------
# Couleurs
# ---------------------------------------------------------------------------


def test_couleur_choisie_ou_tiree_de_la_discipline(generated):
    flute = generated["flute"]
    assert flute.color is None and flute.color_key in Course.COLORS
    same = Course(season=generated["season"], discipline=flute.discipline)
    assert same.color_key == flute.color_key  # même discipline, même couleur
    flute.color = "violet"
    assert flute.color_key == "violet"
    flute.color = "inconnue"
    assert flute.color_key in Course.COLORS


def test_couleur_au_formulaire_et_recopiee(client, generated):
    from app.services import catalog

    from .conftest import enroll

    login(client, enroll(generated["manager"]))
    flute = generated["flute"]
    data = {"discipline_id": flute.discipline_id, "color": "rose"}
    assert client.post(f"/catalogue/cours/{flute.id}/modifier", data=data).status_code == 302
    assert db.session.get(Course, flute.id).color == "rose"
    assert client.post(f"/catalogue/cours/{flute.id}/modifier", data={**data, "color": "fuchsia"}).status_code == 200
    from .test_annuaire import season

    following = season("2027-2028")
    catalog.copy_season(generated["season"], following)
    assert (
        db.session.scalar(db.select(Course).filter_by(season_id=following.id, discipline_id=flute.discipline_id)).color
        == "rose"
    )


# ---------------------------------------------------------------------------
# Mon planning
# ---------------------------------------------------------------------------


def page(response):
    assert response.status_code == 200, response.status_code
    return response.get_data(as_text=True)


def test_mon_planning_du_professeur(client, generated):
    lessons.set_substitute(lesson_on(generated["piano"], OCT14), generated["paul"], generated["manager"])
    db.session.commit()
    login(client, generated["paul_account"])
    html = page(client.get("/seances/mon-planning?date=2026-10-14"))
    assert "Mon planning" in html and "Flûte" in html and "Solfège" in html and "Remplacement" in html
    assert html.count('class="pl-event') == 3
    html = page(client.get("/seances/mon-planning?date=2026-10-14&mode=mois"))
    assert "<h1>Octobre 2026</h1>" in html and html.count('class="pl-pill') == 9  # 4 flûtes, 4 solfèges, 1 piano
    assert "Semaine type" not in html  # pas de semaine type dans son planning
    assert "mode=type" not in page(client.get("/seances/mon-planning?mode=type&date=2026-10-14"))


def test_mon_planning_d_un_parent_avec_le_prenom_et_l_heure_de_passage(client, generated):
    login(client, generated["anne_account"])
    html = page(client.get("/seances/mon-planning?date=2026-10-14"))
    assert "Alice · Flûte" in html and "Alice · Piano" in html
    # Son passage de piano (17 h 30, une demi-heure : affiché en une ligne), pas le créneau du professeur.
    assert '<span class="pl-time">17:30</span>' in html
    assert "Paul Prof" in html  # professeur de la flûte ; une séance courte ne montre pas ces détails


def test_seances_passees_grisees_et_annulees_barrees(client, generated, monkeypatch):
    from app.blueprints import lessons as blueprint

    class Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 14, 17, 30)

    monkeypatch.setattr(blueprint, "datetime", Now)
    lessons.cancel(
        lesson_on(generated["flute"], date(2026, 10, 21)), "Grève", generated["manager"], datetime(2026, 10, 1)
    )
    db.session.commit()
    login(client, generated["anne_account"])
    html = page(client.get("/seances/mon-planning?date=2026-10-14"))
    # Flûte de 17 h à 18 h : pas encore finie à 17 h 30 ; piano d'Alice (17 h 30 - 18 h) non plus.
    assert not re.search(r'class="pl-event[^"]*is-past', html)
    html = page(client.get("/seances/mon-planning?date=2026-10-07"))
    assert html.count("is-past") >= 2
    html = page(client.get("/seances/mon-planning?date=2026-10-21"))
    assert "is-cancelled" in html and "Annulée : Grève" in html


def test_compte_sans_fiche(client, accounts):
    from app.models import Role

    login(client, accounts[Role.ADHERENT])
    html = page(client.get("/seances/mon-planning"))
    assert "relié à aucune fiche" in html


def test_un_eleve_inscrit_en_cours_de_route_n_apparait_qu_a_partir_de_son_arrivee(client, generated):
    from app.models import Membership

    from .test_annuaire import person

    child = person("Zoé", "Martin")
    from app.services import directory

    family = generated["anne_account"].person.household_links[0].household
    directory.add_member(family, child, "dependent")
    membership = Membership(person=child, season=generated["season"])
    db.session.add(membership)
    db.session.flush()
    courses.enroll(membership, generated["fm"], OCT14)
    db.session.commit()
    login(client, generated["anne_account"])
    assert "Zoé · Solfège" not in page(client.get("/seances/mon-planning?date=2026-10-07"))
    assert "Zoé · Solfège" in page(client.get("/seances/mon-planning?date=2026-10-14"))


@pytest.mark.parametrize(
    "path", ["/seances/planning?mode=mois", "/seances/mon-planning?mode=mois", "/seances/planning?mode=type"]
)
def test_pas_de_style_en_ligne(client, generated, path):
    from .conftest import enroll

    login(client, enroll(generated["manager"]))
    html = page(client.get(path))
    assert " style=" not in html
