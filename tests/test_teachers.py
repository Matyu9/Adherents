"""Professeurs : un écran à part, avec son droit. On ne devient professeur que d'ici, jamais depuis une fiche."""
from datetime import date

import pytest

from app.extensions import db
from app.models import Activity, HouseholdMember, Membership, Person, Role, Teacher
from app.services import directory

from .conftest import enroll, login, make_account
from .test_annuaire import family, person, season  # noqa: F401


def test_a_new_teacher_gets_a_record_and_a_contract(client, admin):
    client.post("/professeurs/nouveau", data={
        "civility": "Mme", "first_name": " Élodie ", "last_name": "Martin", "email": "elodie@perso.test",
        "phone": "06 12 34 56 78", "status": "CDI", "started_on": "2018-09-01", "work_email": "e.martin@ecole.test"})
    elodie = db.session.scalar(db.select(Person).filter_by(last_name="Martin"))
    assert elodie.first_name == "Élodie" and elodie.civility == "Mme" and elodie.email == "elodie@perso.test"
    assert [p.number for p in elodie.phones] == ["+33612345678"]
    assert elodie.teacher.status == "CDI" and elodie.teacher.started_on == date(2018, 9, 1)
    assert db.session.scalar(db.select(Activity).filter_by(action="teacher.create")).person_id == elodie.id
    page = client.get(f"/professeurs/{elodie.id}")
    assert page.headers["Cache-Control"] == "no-store" and "e.martin@ecole.test" in page.get_data(as_text=True)


def test_invalid_contract_or_phone_is_refused(client, admin):
    response = client.post("/professeurs/nouveau", data={"first_name": "A", "last_name": "B", "phone": "12",
                                                         "started_on": "2020-09-01", "left_on": "2019-01-01"})
    page = response.get_data(as_text=True)
    assert "ne peut pas précéder" in page and "Numéro invalide" in page
    assert db.session.scalar(db.select(db.func.count(Teacher.person_id))) == 0


def test_a_parent_becomes_a_teacher_without_a_second_record(client, admin, family):
    page = client.get("/professeurs/nouveau?q=anne").get_data(as_text=True)
    assert f"personne={family['anne'].id}" in page
    form = client.get(f"/professeurs/nouveau?personne={family['anne'].id}").get_data(as_text=True)
    assert "devient professeur" in form and 'name="first_name"' not in form
    client.post(f"/professeurs/nouveau?personne={family['anne'].id}", data={"status": "Vacataire"})
    assert db.session.get(Person, family["anne"].id).teacher.status == "Vacataire"
    assert db.session.scalar(db.select(db.func.count(Person.id))) == 3
    assert "aussi adhérent ou parent" in client.get("/professeurs").get_data(as_text=True)
    # Déjà professeur : on retombe sur sa page au lieu d'en créer un second contrat.
    assert client.get(f"/professeurs/nouveau?personne={family['anne'].id}").status_code == 302


def test_a_member_record_offers_no_contract(client, admin, family):
    page = client.get(f"/annuaire/personnes/{family['zoe'].id}").get_data(as_text=True)
    content = page[page.index('<main class="content">'):page.index("</main>")]  # sans le menu
    assert "professeur" not in content.lower()


def test_teacher_record_links_to_the_contract_only_with_the_right(client, app, family):
    family["anne"].teacher = Teacher(status="CDI", notes="salaire confidentiel")
    db.session.add(Role(key="sec", name="Secrétariat", permissions=["directory.view", "directory.edit"]))
    db.session.commit()
    login(client, enroll(make_account("sec", email="sec@asso.test")))
    page = client.get(f"/annuaire/personnes/{family['anne'].id}").get_data(as_text=True)
    assert "Professeur" in page and "/professeurs/" not in page and "CDI" not in page
    assert "confidentiel" not in page
    for url in ("/professeurs", f"/professeurs/{family['anne'].id}", "/professeurs/nouveau"):
        assert client.get(url).status_code == 403
    assert client.post(f"/professeurs/{family['anne'].id}/retirer").status_code == 403
    assert db.session.get(Teacher, family["anne"].id) is not None


def test_manager_has_no_teacher_right_by_default(client, accounts):
    login(client, accounts[Role.GESTIONNAIRE])
    assert client.get("/professeurs").status_code == 403
    assert "/professeurs" not in client.get("/").get_data(as_text=True)


def test_teachers_only_are_out_of_the_people_list(client, admin, family):
    only = person("Paul", "Prof", teacher=Teacher())
    both = family["marc"]
    both.teacher = Teacher()
    db.session.commit()
    people = client.get("/annuaire").get_data(as_text=True)
    assert "PROF Paul" not in people and "MARTIN Marc" in people
    teachers = client.get("/professeurs").get_data(as_text=True)
    assert "PROF Paul" in teachers and "MARTIN Marc" in teachers and "MARTIN Zoé" not in teachers
    # Il redevient visible dès qu'il adhère ou entre dans un foyer.
    db.session.add(Membership(person=only, season=season()))
    db.session.commit()
    assert "PROF Paul" in client.get("/annuaire").get_data(as_text=True)


def test_departure_and_list_of_former_teachers(client, admin, app):
    paul = person("Paul", "Prof", teacher=Teacher(status="CDD"))
    db.session.commit()
    client.post(f"/professeurs/{paul.id}/modifier", data={"status": "CDD", "left_on": "2024-06-30"})
    assert db.session.get(Teacher, paul.id).left_on == date(2024, 6, 30)
    assert db.session.scalar(db.select(Activity).filter_by(action="teacher.update")).changes == {
        "left_on": ["(masqué)", "(masqué)"]}  # le journal dit ce qui a changé dans le contrat, jamais les valeurs
    assert "PROF Paul" not in client.get("/professeurs").get_data(as_text=True)
    assert "PROF Paul" in client.get("/professeurs?partis=1").get_data(as_text=True)


def test_removing_a_mistaken_teacher_keeps_the_record(client, admin, family):
    family["zoe"].teacher = Teacher()
    db.session.commit()
    client.post(f"/professeurs/{family['zoe'].id}/retirer")
    assert db.session.get(Teacher, family["zoe"].id) is None
    assert db.session.get(Person, family["zoe"].id).household_links[0].role == HouseholdMember.DEPENDENT
    assert client.get(f"/professeurs/{family['zoe'].id}").status_code == 404


def test_merge_still_carries_the_contract(family):
    copy = person("Anne", "Martin", teacher=Teacher(status="CDI"))
    directory.merge_persons(family["anne"], copy)
    assert family["anne"].teacher.status == "CDI"


@pytest.mark.parametrize("url", ["/professeurs/999", "/professeurs/999/modifier"])
def test_unknown_teacher(client, admin, url):
    assert client.get(url).status_code == 404


def test_children_are_never_offered_as_teachers(client, admin, family):
    page = client.get("/professeurs/nouveau?q=martin").get_data(as_text=True)
    assert f"personne={family['anne'].id}" in page and f"personne={family['zoe'].id}" not in page
    response = client.get(f"/professeurs/nouveau?personne={family['zoe'].id}", follow_redirects=True)
    assert "mineur" in response.get_data(as_text=True)
    client.post(f"/professeurs/nouveau?personne={family['zoe'].id}", data={"status": "CDI"})
    assert db.session.get(Person, family["zoe"].id).teacher is None
