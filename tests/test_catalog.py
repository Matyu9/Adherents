"""Catalogue (phase 8) : listes de référence, cours d'une saison, disciplines des professeurs, copie de saison."""
from datetime import date, time

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import Activity, Course, Discipline, Level, Person, Role, Room, Site, Teacher, TeacherDiscipline
from app.services import catalog, directory
from app.services.catalog import CatalogError

from .conftest import enroll, login, make_account
from .test_annuaire import person, season


def teacher(first="Élodie", last="Martin", **contract):
    record = person(first, last)
    record.teacher = Teacher(**contract)
    db.session.flush()
    return record.teacher


@pytest.fixture
def school(app):
    """Deux saisons, une flûtiste qui reste, un pianiste qui part fin juin 2027, des disciplines, niveaux et salles."""
    old, new = season("2026-2027", current=True), season("2027-2028")
    flute, piano, fm = (Discipline(name="Flûte traversière", category="Instrument"),
                        Discipline(name="Piano", category="Instrument"),
                        Discipline(name="Formation musicale", category="Formation musicale"))
    c1, c2 = Level(name="Cycle 1", position=1), Level(name="Cycle 2", position=2)
    ecole = Site(name="École", address="2 place de la Mairie")
    salle_a, salle_b = Room(site=ecole, name="Salle A"), Room(site=ecole, name="Salle B")
    db.session.add_all([flute, piano, fm, c1, c2, ecole, salle_a, salle_b])
    elodie = teacher(status="CDI", started_on=date(2015, 9, 1))
    paul = teacher("Paul", "Durand", status="CDD", left_on=date(2027, 6, 30))
    db.session.commit()
    return {"old": old, "new": new, "flute": flute, "piano": piano, "fm": fm, "c1": c1, "c2": c2, "ecole": ecole,
            "a": salle_a, "b": salle_b, "elodie": elodie, "paul": paul}


def course(s, discipline, **fields):
    record = Course(season=s, discipline=discipline, **fields)
    db.session.add(record)
    db.session.flush()
    return record


def count(model):
    return db.session.scalar(sa.select(sa.func.count()).select_from(model))


@pytest.fixture
def manager(accounts, client):
    login(client, accounts[Role.GESTIONNAIRE])
    return accounts[Role.GESTIONNAIRE]


# ---------------------------------------------------------------------------
# Modèle
# ---------------------------------------------------------------------------

def test_course_constraints(school):
    for fields in ({"weekday": 7}, {"starts_at": time(17)}, {"weekday": 1, "starts_at": time(18), "ends_at": time(17)},
                   {"capacity": 0}):
        with pytest.raises(sa.exc.IntegrityError):
            course(school["old"], school["flute"], **fields)
        db.session.rollback()


def test_reference_names_are_unique(school):
    db.session.add(Room(site=school["ecole"], name="Salle A"))
    with pytest.raises(sa.exc.IntegrityError):
        db.session.flush()
    db.session.rollback()
    db.session.add(Room(site=Site(name="Collège"), name="Salle A"))  # même nom, autre site : permis
    db.session.flush()


def test_a_full_course(school):
    c = course(school["old"], school["fm"], capacity=12)
    assert not c.is_full() and not c.is_full(enrolled=11)
    assert c.is_full(enrolled=12)  # les inscriptions (phase 6) passeront leur nombre
    c.marked_full = True
    assert c.is_full()
    unlimited = course(school["old"], school["piano"])
    assert not unlimited.is_full(enrolled=40)


def test_teacher_presence_during_a_season(school):
    old, new = school["old"], school["new"]
    paul, elodie = school["paul"], school["elodie"]
    assert paul.teaches_during(old) and not paul.teaches_during(new)
    assert paul.leaves_during(old) and not elodie.leaves_during(old)
    late = teacher("Léa", "Neuve", started_on=date(2028, 1, 15))
    assert not late.teaches_during(old) and late.teaches_during(new)
    assert [t.person.last_name for t in catalog.teachers_for(new)] == ["Martin", "Neuve"]
    assert paul in catalog.teachers_for(new, keep=paul)


def test_overlaps_and_conflicts(school):
    old = school["old"]
    a = course(old, school["flute"], weekday=2, starts_at=time(17), ends_at=time(18), room=school["a"],
               teacher=school["elodie"])
    after = course(old, school["piano"], weekday=2, starts_at=time(18), ends_at=time(19), room=school["a"])
    same_room = course(old, school["fm"], weekday=2, starts_at=time(17, 30), ends_at=time(18, 30), room=school["a"])
    same_teacher = course(old, school["fm"], weekday=2, starts_at=time(17, 45), ends_at=time(18, 15), room=school["b"],
                          teacher=school["elodie"])
    other_day = course(old, school["fm"], weekday=3, starts_at=time(17), ends_at=time(18), room=school["a"])
    assert not a.overlaps(after) and a.overlaps(same_room) and not a.overlaps(other_day)
    found = catalog.season_conflicts([a, after, same_room, same_teacher, other_day])
    assert {(o.id, why) for o, why in found[a.id]} == {(same_room.id, "même salle"), (same_teacher.id, "même professeur")}
    assert other_day.id not in found


# ---------------------------------------------------------------------------
# Disciplines des professeurs
# ---------------------------------------------------------------------------

def test_a_course_adds_its_discipline_to_the_teacher(school):
    c = course(school["old"], school["flute"], teacher=school["elodie"])
    assert catalog.note_discipline(c) and not catalog.note_discipline(c)
    assert catalog.disciplines_of(school["elodie"], school["old"]) == [school["flute"]]
    assert catalog.disciplines_of(school["elodie"], school["new"]) == []  # par saison


def test_a_discipline_in_use_cannot_be_removed_from_a_teacher(school):
    elodie, old = school["elodie"], school["old"]
    catalog.set_disciplines(elodie, old, [school["flute"], school["fm"]])
    course(old, school["flute"], teacher=elodie)
    with pytest.raises(CatalogError, match="Flûte traversière"):
        catalog.set_disciplines(elodie, old, [school["fm"]])
    added, removed = catalog.set_disciplines(elodie, old, [school["flute"], school["piano"]])
    assert added == [school["piano"]] and removed == [school["fm"]]


# ---------------------------------------------------------------------------
# Copie d'une saison sur la suivante
# ---------------------------------------------------------------------------

def test_copy_a_season(school):
    old, new = school["old"], school["new"]
    flute = course(old, school["flute"], level=school["c1"], teacher=school["elodie"], room=school["a"], weekday=2,
                   starts_at=time(17), ends_at=time(17, 45), capacity=1, marked_full=True, group_label="A",
                   notes="à revoir")
    catalog.set_disciplines(school["elodie"], old, [school["flute"], school["fm"]])
    db.session.commit()

    plan = catalog.copy_season(old, new)
    db.session.commit()
    assert len(plan.copied) == 1 and not plan.skipped
    (copy,) = db.session.scalars(sa.select(Course).where(Course.season_id == new.id)).all()
    assert copy.id != flute.id
    assert (copy.discipline, copy.level, copy.teacher, copy.room, copy.weekday, copy.starts_at, copy.ends_at,
            copy.capacity, copy.group_label, copy.notes) == (
        school["flute"], school["c1"], school["elodie"], school["a"], 2, time(17), time(17, 45), 1, "A", "à revoir")
    assert copy.marked_full is False  # une nouvelle saison rouvre les inscriptions
    assert flute.marked_full is True and flute.season == old  # l'original ne bouge pas
    assert {d.name for d in catalog.disciplines_of(school["elodie"], new)} == {"Flûte traversière", "Formation musicale"}


def test_copy_with_a_teacher_who_left(school):
    old, new = school["old"], school["new"]
    course(old, school["piano"], teacher=school["paul"], weekday=1, starts_at=time(18), ends_at=time(19))
    catalog.set_disciplines(school["paul"], old, [school["piano"]])
    db.session.commit()
    plan = catalog.copy_plan(old, new)
    assert plan.lines[0].notes == ["Paul Durand ne sera plus là : professeur à trouver"]
    assert plan.disciplines == []
    catalog.copy_season(old, new)
    db.session.commit()
    (copy,) = db.session.scalars(sa.select(Course).where(Course.season_id == new.id)).all()
    assert copy.teacher is None and copy.discipline == school["piano"]
    assert catalog.disciplines_of(school["paul"], new) == []


def test_copy_leaves_out_what_was_archived(school):
    old, new = school["old"], school["new"]
    course(old, school["piano"])
    course(old, school["flute"], level=school["c2"], room=school["b"])
    course(old, school["fm"], room=school["a"])
    catalog.archive(school["piano"])
    catalog.archive(school["c2"])
    catalog.archive(school["b"])
    db.session.commit()
    plan = catalog.copy_plan(old, new)
    assert [(line.course.discipline.name, line.skipped, line.notes) for line in plan.lines] == [
        ("Flûte traversière", None, ["niveau archivé : à choisir", "salle fermée : à choisir"]),
        ("Formation musicale", None, []),
        ("Piano", "discipline archivée", []),
    ]
    catalog.archive(school["ecole"])  # un site fermé ferme toutes ses salles
    catalog.copy_season(old, new)
    copies = {c.discipline.name: c for c in db.session.scalars(sa.select(Course).where(Course.season_id == new.id))}
    assert set(copies) == {"Flûte traversière", "Formation musicale"}
    assert copies["Flûte traversière"].level is None and copies["Formation musicale"].room is None


def test_copy_only_fills_an_empty_season(school):
    old, new = school["old"], school["new"]
    course(old, school["flute"])
    course(new, school["fm"])
    db.session.commit()
    with pytest.raises(CatalogError, match="déjà des cours"):
        catalog.copy_season(old, new)
    assert catalog.copy_plan(old, old).problems
    assert catalog.copy_plan(None, new).problems


def test_copy_page_previews_then_copies(client, manager, school):
    old, new = school["old"], school["new"]
    course(old, school["flute"], teacher=school["elodie"])
    course(old, school["piano"], teacher=school["paul"])
    db.session.commit()
    page = client.get(f"/catalogue/copier?depuis={old.id}&vers={new.id}").get_data(as_text=True)
    assert "2 cours recopiés" in page and "Paul Durand ne sera plus là" in page
    assert count(Course) == 2  # l'aperçu n'écrit rien
    response = client.post("/catalogue/copier", data={"depuis": old.id, "vers": new.id})
    assert response.status_code == 302 and response.location.endswith(f"/catalogue?saison={new.id}")
    assert count(Course) == 4
    assert "recopié sur 2027-2028" in db.session.scalar(sa.select(Activity.message).filter_by(action="catalog.copy"))
    # Une seconde fois : refusé, rien de plus.
    client.post("/catalogue/copier", data={"depuis": old.id, "vers": new.id})
    assert count(Course) == 4


def test_copy_page_defaults_to_the_two_latest_seasons(client, manager, school):
    page = client.get("/catalogue/copier").get_data(as_text=True)
    assert f'<option value="{school["old"].id}" selected>De 2026-2027' in page
    assert f'<option value="{school["new"].id}" selected>Vers 2027-2028' in page


# ---------------------------------------------------------------------------
# Écrans des cours
# ---------------------------------------------------------------------------

def course_data(school, **overrides):
    data = {"discipline_id": school["flute"].id, "level_id": school["c1"].id, "teacher_id": school["elodie"].person_id,
            "weekday": "2", "starts_at": "17:00", "ends_at": "17:45", "room_id": school["a"].id, "capacity": "1",
            "group_label": " A ", "notes": ""}
    data.update(overrides)
    return data


def test_create_a_course(client, manager, school):
    response = client.post(f"/catalogue/cours/nouveau?saison={school['old'].id}", data=course_data(school))
    assert response.status_code == 302
    c = db.session.scalar(sa.select(Course))
    assert (c.season, c.teacher, c.weekday, c.starts_at, c.group_label, c.marked_full) == (
        school["old"], school["elodie"], 2, time(17), "A", False)
    assert catalog.disciplines_of(school["elodie"], school["old"]) == [school["flute"]]
    assert db.session.scalar(sa.select(Activity).filter_by(action="course.create")).message.startswith(
        "Cours ajouté : Flûte traversière · Cycle 1 · A (2026-2027, mercredi 17:00-17:45)")
    page = client.get(f"/catalogue?saison={school['old'].id}").get_data(as_text=True)
    assert "Flûte traversière" in page and "Mercredi 17:00-17:45" in page and "Salle A (École)" in page


@pytest.mark.parametrize("overrides, message", [
    ({"starts_at": "18:00", "ends_at": "17:00"}, "La fin doit suivre le début."),
    ({"ends_at": ""}, "Indiquez le début et la fin"),
    ({"weekday": ""}, "Un horaire demande un jour."),
    ({"capacity": "0"}, "Entre 1 et 500."),
    ({"discipline_id": ""}, "Choisissez la discipline."),
    ({"discipline_id": "999"}, "Ce choix n&#39;est pas proposé."),
])
def test_invalid_course_is_refused(client, manager, school, overrides, message):
    response = client.post(f"/catalogue/cours/nouveau?saison={school['old'].id}", data=course_data(school, **overrides))
    assert response.status_code == 200 and message in response.get_data(as_text=True)
    assert count(Course) == 0


def test_a_teacher_who_left_is_not_offered_but_stays_on_his_courses(client, manager, school):
    old, new = school["old"], school["new"]
    paul = school["paul"]
    form = client.get(f"/catalogue/cours/nouveau?saison={new.id}").get_data(as_text=True)
    assert "Durand" not in form  # parti avant la saison 2027-2028
    form = client.get(f"/catalogue/cours/nouveau?saison={old.id}").get_data(as_text=True)
    assert "DURAND Paul (parti le 30 juin 2027)" in form
    # Choisi de force sur la saison suivante : refusé.
    response = client.post(f"/catalogue/cours/nouveau?saison={new.id}", data=course_data(school, teacher_id=paul.person_id))
    assert "Ce choix n&#39;est pas proposé." in response.get_data(as_text=True)
    # Un cours existant le garde tant qu'on ne change pas de professeur.
    c = course(old, school["piano"], teacher=paul)
    db.session.commit()
    page = client.get(f"/catalogue/cours/{c.id}/modifier").get_data(as_text=True)
    assert "part le 30 juin 2027" in page
    client.post(f"/catalogue/cours/{c.id}/modifier", data=course_data(school, discipline_id=school["piano"].id,
                                                                      teacher_id=paul.person_id))
    assert db.session.get(Course, c.id).teacher == paul
    listing = client.get(f"/catalogue?saison={old.id}").get_data(as_text=True)
    assert "parti le 30 juin 2027" in listing


def test_an_archived_value_stays_on_existing_courses_only(client, manager, school):
    c = course(school["old"], school["flute"], room=school["b"])
    catalog.archive(school["b"])
    db.session.commit()
    assert "Salle B" not in client.get(f"/catalogue/cours/nouveau?saison={school['old'].id}").get_data(as_text=True)
    page = client.get(f"/catalogue/cours/{c.id}/modifier").get_data(as_text=True)
    assert "Salle B (École) (fermée)" in page
    client.post(f"/catalogue/cours/{c.id}/modifier", data=course_data(school, room_id=school["b"].id))
    assert db.session.get(Course, c.id).room == school["b"]


def test_overlap_is_a_warning_not_a_refusal(client, manager, school):
    course(school["old"], school["fm"], weekday=2, starts_at=time(17, 30), ends_at=time(18, 30), room=school["a"])
    db.session.commit()
    page = client.post(f"/catalogue/cours/nouveau?saison={school['old'].id}", data=course_data(school),
                       follow_redirects=True).get_data(as_text=True)
    assert count(Course) == 2 and "Chevauchement (même salle)" in page


def test_full_course_is_shown(client, manager, school):
    c = course(school["old"], school["fm"], capacity=10)
    db.session.commit()
    client.post(f"/catalogue/cours/{c.id}/modifier", data=course_data(school, discipline_id=school["fm"].id,
                                                                      capacity="10", marked_full="y"))
    assert db.session.get(Course, c.id).marked_full
    assert '<span class="badge">complet</span>' in client.get("/catalogue").get_data(as_text=True)
    changes = db.session.scalar(sa.select(Activity).filter_by(action="course.update")).changes
    assert changes["marked_full"] == [False, True]


def test_duplicate_and_delete_a_course(client, manager, school):
    c = course(school["old"], school["flute"], level=school["c1"], capacity=8, marked_full=True)
    db.session.commit()
    form = client.get(f"/catalogue/cours/nouveau?saison={school['old'].id}&depuis={c.id}").get_data(as_text=True)
    assert 'value="8"' in form and 'name="marked_full" type="checkbox" value="y" checked' not in form
    client.post(f"/catalogue/cours/{c.id}/supprimer")
    assert count(Course) == 0
    assert db.session.scalar(sa.select(Activity).filter_by(action="course.delete")) is not None


def test_course_list_filters(client, manager, school):
    old = school["old"]
    course(old, school["flute"], teacher=school["elodie"], weekday=0, room=school["a"])
    course(old, school["piano"], weekday=4)
    db.session.commit()

    def shown(query):
        page = client.get(f"/catalogue?saison={old.id}&{query}").get_data(as_text=True)
        return {name for name in ("Flûte traversière", "Piano") if f"<strong>{name}</strong>" in page}

    assert shown("") == {"Flûte traversière", "Piano"}
    assert shown(f"prof={school['elodie'].person_id}") == {"Flûte traversière"}
    assert shown("prof=aucun") == {"Piano"}
    assert shown("jour=4") == {"Piano"}
    assert shown("jour=9") == {"Flûte traversière", "Piano"}
    assert shown(f"site={school['ecole'].id}") == {"Flûte traversière"}
    assert shown(f"discipline={school['piano'].id}") == {"Piano"}


def test_catalogue_without_any_season(client, manager):
    page = client.get("/catalogue").get_data(as_text=True)
    assert "Aucune saison" in page
    assert client.get("/catalogue/cours/nouveau").status_code == 302


# ---------------------------------------------------------------------------
# Listes de référence
# ---------------------------------------------------------------------------

def test_reference_lists(client, manager, school):
    assert client.post("/catalogue/disciplines/nouveau", data={"name": "Violon", "category": "Instrument"}).status_code == 302
    response = client.post("/catalogue/disciplines/nouveau", data={"name": "  FLUTE Traversiere "})
    assert "Ce nom existe déjà." in response.get_data(as_text=True)
    assert client.post("/catalogue/niveaux/nouveau", data={"name": "Débutant", "position": "0"}).status_code == 302
    client.post("/catalogue/sites/nouveau", data={"name": "Collège"})
    college = db.session.scalar(sa.select(Site).filter_by(name="Collège"))
    assert client.post("/catalogue/salles/nouveau", data={"site_id": college.id, "name": "Salle A"}).status_code == 302
    response = client.post("/catalogue/salles/nouveau", data={"site_id": college.id, "name": "salle a"})
    assert "Ce nom existe déjà sur ce site." in response.get_data(as_text=True)
    client.post(f"/catalogue/disciplines/{school['piano'].id}/modifier", data={"name": "Piano classique", "category": ""})
    assert school["piano"].name == "Piano classique" and school["piano"].category is None
    actions = set(db.session.scalars(sa.select(Activity.action)))
    assert {"discipline.create", "level.create", "site.create", "room.create", "discipline.update"} <= actions
    page = client.get("/catalogue/referentiels").get_data(as_text=True)
    assert "Violon" in page and "Débutant" in page and "Collège" in page


def test_used_values_are_archived_not_deleted(client, manager, school):
    course(school["old"], school["flute"], level=school["c1"], room=school["a"])
    db.session.commit()
    for url in (f"/catalogue/disciplines/{school['flute'].id}", f"/catalogue/niveaux/{school['c1'].id}",
                f"/catalogue/salles/{school['a'].id}", f"/catalogue/sites/{school['ecole'].id}"):
        page = client.post(url + "/supprimer", follow_redirects=True).get_data(as_text=True)
        assert "a déjà servi" in page
    assert count(Discipline) == 3 and count(Room) == 2 and count(Site) == 1
    client.post(f"/catalogue/disciplines/{school['flute'].id}/archiver")
    assert school["flute"].archived
    form = client.get(f"/catalogue/cours/nouveau?saison={school['old'].id}").get_data(as_text=True)
    assert ">Flûte traversière<" not in form and ">Piano<" in form
    client.post(f"/catalogue/disciplines/{school['flute'].id}/restaurer")
    assert not school["flute"].archived
    client.post(f"/catalogue/disciplines/{school['piano'].id}/supprimer")  # n'a jamais servi
    assert db.session.get(Discipline, school["piano"].id) is None


def test_unknown_reference_kind_is_404(client, manager):
    assert client.get("/catalogue/instruments/nouveau").status_code == 404
    assert client.get("/catalogue/disciplines/999/modifier").status_code == 404


# ---------------------------------------------------------------------------
# Professeurs
# ---------------------------------------------------------------------------

def test_teacher_disciplines_screen(client, manager, school):
    old, elodie = school["old"], school["elodie"]
    url = f"/catalogue/professeurs/{elodie.person_id}?saison={old.id}"
    client.post(url, data={"disciplines": [school["flute"].id, school["fm"].id]})
    assert {d.name for d in catalog.disciplines_of(elodie, old)} == {"Flûte traversière", "Formation musicale"}
    entry = db.session.scalar(sa.select(Activity).filter_by(action="teacher.disciplines"))
    assert entry.person_id == elodie.person_id
    course(old, school["flute"], teacher=elodie)
    db.session.commit()
    page = client.post(url, data={"disciplines": [school["fm"].id]}, follow_redirects=True).get_data(as_text=True)
    assert "a des cours de Flûte traversière" in page
    assert len(catalog.disciplines_of(elodie, old)) == 2
    listing = client.get(f"/catalogue/professeurs?saison={old.id}").get_data(as_text=True)
    assert "MARTIN Élodie" in listing and "Flûte traversière, Formation musicale" in listing


def test_teacher_list_shows_a_departed_teacher_who_still_has_courses(client, manager, school):
    new = school["new"]
    school["paul"].left_on = date(2027, 6, 30)
    course(new, school["piano"], teacher=school["paul"])  # importé ou saisi avant son départ
    db.session.commit()
    page = client.get(f"/catalogue/professeurs?saison={new.id}").get_data(as_text=True)
    assert "DURAND Paul" in page and "parti le 30 juin 2027" in page


def test_teacher_page_shows_courses_of_the_current_season(client, admin, school):
    course(school["old"], school["flute"], teacher=school["elodie"], weekday=2, starts_at=time(17), ends_at=time(18))
    course(school["new"], school["piano"], teacher=school["elodie"])
    catalog.set_disciplines(school["elodie"], school["old"], [school["flute"]])
    db.session.commit()
    page = client.get(f"/professeurs/{school['elodie'].person_id}").get_data(as_text=True)
    assert "Enseignement en 2026-2027" in page and "Mercredi 17:00-18:00" in page and "Piano" not in page


def test_removing_a_teacher_frees_his_courses(client, admin, school):
    elodie = school["elodie"]
    c = course(school["old"], school["flute"], teacher=elodie)
    catalog.note_discipline(c)
    db.session.commit()
    page = client.post(f"/professeurs/{elodie.person_id}/retirer", follow_redirects=True).get_data(as_text=True)
    assert "1 cours sont maintenant sans professeur" in page
    assert db.session.get(Course, c.id).teacher_id is None and count(TeacherDiscipline) == 0


def test_merging_two_records_keeps_the_teaching(app, school):
    old = school["old"]
    keep = person("Élodie", "Martin", email="elodie@perso.test")
    drop = school["elodie"].person
    c = course(old, school["flute"], teacher=school["elodie"])
    catalog.note_discipline(c)
    db.session.commit()
    summary = directory.merge_persons(keep, drop)
    db.session.commit()
    assert "1 cours repris" in summary
    assert db.session.get(Course, c.id).teacher_id == keep.id
    assert catalog.disciplines_of(keep.teacher, old) == [school["flute"]]
    assert db.session.get(Person, drop.id) is None


def test_merging_two_teachers_adds_up_their_disciplines(app, school):
    old, elodie, paul = school["old"], school["elodie"], school["paul"]
    catalog.set_disciplines(elodie, old, [school["flute"]])
    catalog.set_disciplines(paul, old, [school["flute"], school["piano"]])
    c = course(old, school["piano"], teacher=paul)
    db.session.commit()
    directory.merge_persons(elodie.person, paul.person)
    db.session.commit()
    assert {d.name for d in catalog.disciplines_of(elodie, old)} == {"Flûte traversière", "Piano"}
    assert db.session.get(Course, c.id).teacher == elodie


def test_a_season_with_courses_cannot_be_deleted(client, manager, school):
    course(school["new"], school["flute"])
    db.session.commit()
    page = client.post(f"/annuaire/saisons/{school['new'].id}/supprimer", follow_redirects=True).get_data(as_text=True)
    assert "a des cours" in page and db.session.get(type(school["new"]), school["new"].id) is not None


# ---------------------------------------------------------------------------
# Droits
# ---------------------------------------------------------------------------

def test_read_only_catalogue(client, school):
    course(school["old"], school["flute"])
    db.session.add(Role(key="lecture", name="Lecture", permissions=["catalog.view"]))
    db.session.commit()
    login(client, enroll(make_account("lecture", email="lecture@asso.test")))
    page = client.get("/catalogue").get_data(as_text=True)
    assert "Flûte traversière" in page and "Modifier" not in page and "Nouveau cours" not in page
    assert client.get(f"/catalogue/cours/nouveau?saison={school['old'].id}").status_code == 403
    assert client.post("/catalogue/disciplines/nouveau", data={"name": "X"}).status_code == 403
    assert count(Discipline) == 3


def test_members_do_not_see_the_catalogue(client, member, school):
    assert client.get("/catalogue").status_code == 403
    assert "/catalogue" not in client.get("/").get_data(as_text=True)
