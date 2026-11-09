"""Annuaire : personnes, foyers, téléphones, saisons, adhésions, professeurs, fusion, rattachement des comptes."""
import re
from datetime import date, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (Account, AccountToken, Activity, BankAccount, ExternalRef, Household, HouseholdMember,
                        Membership, Person, Phone, Role, Season, Teacher)
from app.services import bank, directory
from app.services.directory import DirectoryError

from .conftest import login, make_account

G, D = HouseholdMember.GUARDIAN, HouseholdMember.DEPENDENT
IBAN = "FR7630006000011234567890189"


def person(first="Anne", last="Martin", **fields):
    record = Person(first_name=first, last_name=last, **fields)
    db.session.add(record)
    db.session.flush()
    return record


def home(name="Famille Martin", **fields):
    record = Household(name=name, **fields)
    db.session.add(record)
    db.session.flush()
    return record


def season(label="2026-2027", current=False):
    start = int(label[:4])
    record = Season(label=label, starts_on=date(start, 9, 1), ends_on=date(start + 1, 8, 31))
    db.session.add(record)
    db.session.flush()
    if current:
        directory.set_current_season(record)
    return record


@pytest.fixture
def family(app):
    """Anne et Marc, responsables ; Zoé, 11 ans, à charge."""
    household = home(address_street="1 rue Haute", postal_code="76000", city="Rouen")
    anne = person(email="anne@asso.test", birth_date=date(1980, 1, 2))
    marc = person("Marc", email="marc@asso.test")
    zoe = person("Zoé", birth_date=date.today().replace(year=date.today().year - 11))
    directory.add_member(household, anne, G, "mère")
    directory.add_member(household, marc, G, "père")
    directory.add_member(household, zoe, D)
    db.session.commit()
    return {"home": household, "anne": anne, "marc": marc, "zoe": zoe}


@pytest.fixture
def manager(accounts, client):
    login(client, accounts[Role.GESTIONNAIRE])
    return accounts[Role.GESTIONNAIRE]


# ---------------------------------------------------------------------------
# Modèle et contraintes
# ---------------------------------------------------------------------------

def test_a_parent_can_belong_to_two_households(app):
    anne, first, second = person(), home(), home("Famille Durand")
    directory.add_member(first, anne, G)
    directory.add_member(second, anne, G)
    db.session.commit()
    assert {link.household.name for link in anne.household_links} == {"Famille Martin", "Famille Durand"}
    assert db.session.scalar(db.select(db.func.count(Person.id))) == 1


def test_only_one_guardian_per_rank(app):
    household, a, b = home(), person(), person("Bob")
    db.session.add_all([HouseholdMember(household=household, person=a, role=G, guardian_rank=1),
                        HouseholdMember(household=household, person=b, role=G, guardian_rank=1)])
    with pytest.raises(IntegrityError):
        db.session.flush()


@pytest.mark.parametrize("role, rank", [(G, None), (G, 3), (D, 1), ("autre", None)])
def test_role_and_rank_must_agree(app, role, rank):
    db.session.add(HouseholdMember(household=home(), person=person(), role=role, guardian_rank=rank))
    with pytest.raises(IntegrityError):
        db.session.flush()


@pytest.mark.parametrize("owners", ["both", "none"])
def test_a_phone_belongs_to_exactly_one_record(app, owners):
    p, h = person(), home()
    db.session.add(Phone(number="+33612345678", person_id=p.id if owners == "both" else None,
                         household_id=h.id if owners == "both" else None))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_external_number_is_unique_per_kind(app):
    a, b = person(), person("Bob")
    db.session.add_all([ExternalRef(kind="student", value="42", person=a), ExternalRef(kind="teacher", value="42", person=a)])
    db.session.flush()  # même numéro, autre type : c'est permis (élève et professeur)
    db.session.add(ExternalRef(kind="student", value="42", person=b))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_one_membership_per_season_and_valid_dates(app):
    p, s = person(), season()
    db.session.add_all([Membership(person=p, season=s), Membership(person=p, season=s)])
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()
    db.session.add(Season(label="x", starts_on=date(2026, 9, 1), ends_on=date(2026, 9, 1)))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_an_account_belongs_to_at_most_one_person(app):
    account = make_account(email="a@asso.test")
    person(account=account)
    with pytest.raises(IntegrityError):
        person("Bob", account_id=account.id)


def test_deleting_a_person_removes_its_links_but_not_its_household(family):
    zoe_id = family["zoe"].id
    db.session.add(Phone(number="+33612345678", person_id=zoe_id))
    db.session.commit()
    db.session.delete(family["zoe"])
    db.session.commit()
    assert db.session.scalar(db.select(db.func.count(Phone.id))) == 0
    assert [m.person.first_name for m in family["home"].members] == ["Anne", "Marc"]


def test_age_and_minority(app):
    today = date.today()
    p = person(birth_date=today.replace(year=today.year - 18) + timedelta(days=1))
    assert p.age() == 17 and p.is_minor
    assert person("Sans date").age() is None and not person("Sans date 2").is_minor


# ---------------------------------------------------------------------------
# Téléphones
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("06 12 34 56 78", "+33612345678"), ("06.12.34.56.78", "+33612345678"), ("+33 6 12 34 56 78", "+33612345678"),
    ("+33 (0)6 12 34 56 78", "+33612345678"), ("0033612345678", "+33612345678"), ("+41 79 123 45 67", "+41791234567"),
])
def test_phone_numbers_are_normalized(text, expected):
    assert directory.normalize_phone(text) == expected


@pytest.mark.parametrize("text", ["", "12", "06 12 34 56", "+33 6 12 34 56 78 9", "abc", "00 12"])
def test_unreadable_phone_numbers_are_refused(text):
    with pytest.raises(DirectoryError):
        directory.normalize_phone(text)


def test_phone_display():
    assert directory.display_phone("+33612345678") == "06 12 34 56 78"
    assert directory.display_phone("+41791234567") == "+41791234567"


def test_phone_is_added_once(client, admin, family):
    url = f"/annuaire/foyers/{family['home'].id}/telephones"
    client.post(url, data={"number": "02 35 00 00 00", "label": "domicile"})
    response = client.post(url, data={"number": "+33235000000"}, follow_redirects=True)
    assert "déjà sur la fiche" in response.get_data(as_text=True)
    (phone,) = family["home"].phones
    assert phone.number == "+33235000000" and phone.label == "domicile"
    client.post(f"/annuaire/telephones/{phone.id}/supprimer")
    assert db.session.scalar(db.select(db.func.count(Phone.id))) == 0


# ---------------------------------------------------------------------------
# Foyers et membres
# ---------------------------------------------------------------------------

def test_a_third_guardian_is_refused(family):
    with pytest.raises(DirectoryError, match="deux responsables"):
        directory.add_member(family["home"], person("Paul"), G)


def test_same_person_cannot_join_twice(family):
    with pytest.raises(DirectoryError, match="déjà partie"):
        directory.add_member(family["home"], family["zoe"], D)


def test_a_child_cannot_carry_a_parent_email(family):
    family["zoe"].email = "ANNE@asso.test "
    assert directory.guardian_email_conflict(family["zoe"], family["zoe"].email) is family["anne"]
    other = home("Famille Durand")
    with pytest.raises(DirectoryError, match="adresse email de Marc"):
        directory.add_member(family["home"], person("Léo", email="marc@asso.test"), D)
    directory.add_member(other, person("Léo2", email="marc@asso.test"), D)  # pas de responsable avec cette adresse


def test_person_form_refuses_a_parent_email_on_a_child(client, admin, family):
    url = f"/annuaire/personnes/{family['zoe'].id}/modifier"
    response = client.post(url, data={"first_name": "Zoé", "last_name": "Martin", "email": "anne@asso.test",
                                      "country": "FR"})
    assert "jamais recopiée" in response.get_data(as_text=True)
    assert db.session.get(Person, family["zoe"].id).email is None
    # Le parent lui-même garde son adresse, et deux adultes peuvent partager la leur.
    response = client.post(f"/annuaire/personnes/{family['marc'].id}/modifier",
                           data={"first_name": "Marc", "last_name": "Martin", "email": "anne@asso.test", "country": "FR"})
    assert response.status_code == 302


def test_a_lone_adult_is_guardian_of_their_own_household(client, admin, app):
    adult = person("Jean", "Seul")
    db.session.commit()
    form = client.get(f"/annuaire/foyers/nouveau?personne={adult.id}").get_data(as_text=True)
    assert "Famille Seul" in form
    client.post(f"/annuaire/foyers/nouveau?personne={adult.id}", data={"name": "Famille Seul", "country": "FR"})
    (link,) = db.session.get(Person, adult.id).household_links
    assert link.role == G and link.guardian_rank == 1


def test_member_roles_and_swap(client, admin, family):
    household = family["home"]
    client.post(f"/annuaire/foyers/{household.id}/responsables/echanger")
    ranks = {m.person.first_name: m.guardian_rank for m in db.session.get(Household, household.id).guardians}
    assert ranks == {"Anne": 2, "Marc": 1}
    client.post(f"/annuaire/foyers/{household.id}/membres/{family['marc'].id}/modifier",
                data={"role": D, "relationship": "beau-père"})
    marc = next(m for m in db.session.get(Household, household.id).members if m.person_id == family["marc"].id)
    assert marc.role == D and marc.guardian_rank is None and marc.relationship == "beau-père"
    client.post(f"/annuaire/foyers/{household.id}/responsables/echanger")
    (anne,) = db.session.get(Household, household.id).guardians
    assert anne.guardian_rank == 1
    response = client.post(f"/annuaire/foyers/{household.id}/membres/{family['zoe'].id}/modifier", data={"role": G})
    assert response.status_code == 302
    assert {m.guardian_rank for m in db.session.get(Household, household.id).guardians} == {1, 2}


def test_removing_a_member_keeps_the_person(client, admin, family):
    client.post(f"/annuaire/foyers/{family['home'].id}/membres/{family['zoe'].id}/retirer")
    assert db.session.get(Person, family["zoe"].id) is not None
    assert len(db.session.get(Household, family["home"].id).members) == 2


def test_adding_an_existing_person_from_the_household_page(client, admin, family):
    leo = person("Léo", "Martin")
    db.session.commit()
    page = client.get(f"/annuaire/foyers/{family['home'].id}?q=leo").get_data(as_text=True)
    assert "MARTIN Léo" in page and "MARTIN Zoé" not in page  # déjà membre : pas proposée
    client.post(f"/annuaire/foyers/{family['home'].id}/membres", data={"person_id": leo.id, "role": D})
    assert any(m.person_id == leo.id for m in db.session.get(Household, family["home"].id).dependents)
    response = client.post(f"/annuaire/foyers/{family['home'].id}/membres", data={"person_id": leo.id, "role": G},
                           follow_redirects=True)
    assert "fait déjà partie" in response.get_data(as_text=True)


def test_creating_a_child_from_the_household(client, admin, family):
    url = f"/annuaire/personnes/nouvelle?foyer={family['home'].id}&role=dependent"
    assert 'value="Martin"' in client.get(url).get_data(as_text=True)
    client.post(url, data={"first_name": "Léa", "last_name": "Martin", "country": "FR"})
    lea = db.session.scalar(db.select(Person).filter_by(first_name="Léa"))
    assert lea.household_links[0].role == D
    response = client.post(url, data={"first_name": "Tom", "last_name": "Martin", "email": "anne@asso.test",
                                      "country": "FR"})
    assert "jamais recopiée" in response.get_data(as_text=True)
    assert db.session.scalar(db.select(Person).filter_by(first_name="Tom")) is None


# ---------------------------------------------------------------------------
# Liste, recherche, filtres
# ---------------------------------------------------------------------------

def test_search_ignores_accents_and_case(client, admin, family):
    page = client.get("/annuaire?q=zoe").get_data(as_text=True)
    assert "MARTIN Zoé" in page and "MARTIN Anne" not in page
    page = client.get("/annuaire?q=MARTIN ZOÉ").get_data(as_text=True)
    assert "MARTIN Zoé" in page and "MARTIN Marc" not in page
    assert "MARTIN Marc" in client.get("/annuaire?q=marc@asso").get_data(as_text=True)
    assert "Aucune fiche" in client.get("/annuaire?q=%25").get_data(as_text=True)


def test_status_filters(client, admin, family):
    old, current = season("2025-2026"), season("2026-2027", current=True)
    db.session.add_all([Membership(person=family["zoe"], season=current), Membership(person=family["anne"], season=old),
                        Membership(person=family["marc"], season=current, status=Membership.WITHDRAWN)])
    prof = person("Paul", "Prof", teacher=Teacher())
    person("Parti", "Prof", teacher=Teacher(left_on=date(2020, 1, 1)))
    gone = person("Ancienne", "Fiche", archived_at=date(2026, 1, 1))
    db.session.commit()

    def names(query):
        page = client.get("/annuaire?" + query).get_data(as_text=True)
        return {n for n in ("Zoé", "Anne", "Marc", "Paul", "Parti", "Ancienne") if f" {n}</a>" in page}

    assert names("") == {"Zoé", "Anne", "Marc", "Paul", "Parti"}
    assert names("statut=eleves") == {"Zoé"}
    assert names(f"statut=eleves&saison={old.id}") == {"Anne"}
    assert names("statut=anciens") == {"Anne", "Marc"}  # Marc a abandonné cette saison
    assert names("statut=professeurs") == {"Paul"}
    assert names("statut=archives") == {"Ancienne"}
    assert prof and gone


def test_households_list_and_search(client, admin, family):
    home("Famille Durand", city="Évreux")
    db.session.commit()
    page = client.get("/annuaire/foyers?q=zoe").get_data(as_text=True)
    assert "Famille Martin" in page and "Famille Durand" not in page
    assert "Famille Durand" in client.get("/annuaire/foyers?q=evreux").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Fiches : création, modification, droits
# ---------------------------------------------------------------------------

def test_create_and_edit_a_person_is_logged(client, admin, app):
    client.post("/annuaire/personnes/nouvelle", data={"first_name": " Jeanne ", "last_name": "Dupré", "sex": "F",
                                                      "birth_date": "2010-04-05", "country": "fr"})
    jeanne = db.session.scalar(db.select(Person).filter_by(last_name="Dupré"))
    assert jeanne.first_name == "Jeanne" and jeanne.birth_date == date(2010, 4, 5) and jeanne.country == "FR"
    assert jeanne.email is None and jeanne.civility is None and len(jeanne.uuid) == 36
    client.post(f"/annuaire/personnes/{jeanne.id}/modifier",
                data={"first_name": "Jeanne", "last_name": "Dupré", "birth_place": "Caen", "country": "FR"})
    entry = db.session.scalar(db.select(Activity).filter_by(action="person.update"))
    assert entry.person_id == jeanne.id and entry.changes == {"birth_place": [None, "Caen"]}
    assert "Fiche modifiée" in client.get(f"/annuaire/personnes/{jeanne.id}").get_data(as_text=True)


def test_birth_place_is_only_for_editors(client, app, family):
    family["anne"].birth_place = "Lisieux"
    db.session.add(Role(key="prof", name="Professeur", permissions=["directory.view"]))
    db.session.commit()
    login(client, make_account("prof", email="prof@asso.test"))
    page = client.get(f"/annuaire/personnes/{family['anne'].id}")
    assert page.status_code == 200 and "Lisieux" not in page.get_data(as_text=True)
    assert "Modifier" not in page.get_data(as_text=True)
    assert client.post(f"/annuaire/personnes/{family['anne'].id}/archiver").status_code == 403


def test_manager_sees_birth_place(client, manager, family):
    family["anne"].birth_place = "Lisieux"
    db.session.commit()
    assert "Lisieux" in client.get(f"/annuaire/personnes/{family['anne'].id}").get_data(as_text=True)


def test_member_has_no_access(client, member, family):
    for url in ("/annuaire", "/annuaire/foyers", f"/annuaire/personnes/{family['anne'].id}",
                f"/annuaire/foyers/{family['home'].id}"):
        assert client.get(url).status_code == 403


def test_pages_are_never_cached(client, admin, family):
    for url in ("/annuaire", f"/annuaire/personnes/{family['anne'].id}", f"/annuaire/foyers/{family['home'].id}"):
        assert client.get(url).headers["Cache-Control"] == "no-store"


def test_archive_and_restore(client, admin, family):
    anne = family["anne"]
    client.post(f"/annuaire/personnes/{anne.id}/archiver")
    assert db.session.get(Person, anne.id).archived_at is not None
    assert "MARTIN Anne" not in client.get("/annuaire").get_data(as_text=True)
    client.post(f"/annuaire/personnes/{anne.id}/restaurer")
    assert db.session.get(Person, anne.id).archived_at is None
    client.post(f"/annuaire/foyers/{family['home'].id}/archiver")
    assert "Famille Martin" not in client.get("/annuaire/foyers").get_data(as_text=True)
    assert "Famille Martin" in client.get("/annuaire/foyers?archives=1").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Saisons, adhésions, professeurs
# ---------------------------------------------------------------------------

def test_seasons(client, admin, family):
    client.post("/annuaire/saisons/nouvelle", data={"label": "2026-2027", "starts_on": "2026-09-01",
                                                    "ends_on": "2027-08-31"})
    first = db.session.scalar(db.select(Season))
    assert directory.current_season() is first  # la première saison devient celle en cours
    assert "La fin doit suivre" in client.post("/annuaire/saisons/nouvelle", data={
        "label": "2027-2028", "starts_on": "2027-09-01", "ends_on": "2027-08-01"}).get_data(as_text=True)
    assert "existe déjà" in client.post("/annuaire/saisons/nouvelle", data={
        "label": "2026-2027", "starts_on": "2026-09-01", "ends_on": "2027-08-31"}).get_data(as_text=True)
    client.post("/annuaire/saisons/nouvelle", data={"label": "2027-2028", "starts_on": "2027-09-01",
                                                    "ends_on": "2028-08-31"})
    second = db.session.scalar(db.select(Season).filter_by(label="2027-2028"))
    assert directory.current_season() is first
    client.post(f"/annuaire/saisons/{second.id}/en-cours")
    assert directory.current_season().id == second.id
    db.session.add(Membership(person=family["zoe"], season_id=first.id))
    db.session.commit()
    client.post(f"/annuaire/saisons/{first.id}/supprimer")
    client.post(f"/annuaire/saisons/{second.id}/supprimer")
    assert db.session.scalar(db.select(db.func.count(Season.id))) == 2  # l'une a des adhésions, l'autre est en cours


def test_season_with_memberships_cannot_be_deleted_in_the_database(family):
    s = season()
    db.session.add(Membership(person=family["zoe"], season=s))
    db.session.commit()
    with pytest.raises(IntegrityError):
        db.session.execute(db.delete(Season))


def test_membership_form(client, admin, family):
    s = season(current=True)
    db.session.commit()
    zoe = family["zoe"]
    page = client.get(f"/annuaire/personnes/{zoe.id}/adhesions/nouvelle").get_data(as_text=True)
    assert re.search(rf'<option selected value="{s.id}">', page)
    response = client.post(f"/annuaire/personnes/{zoe.id}/adhesions/nouvelle",
                           data={"season_id": s.id, "status": "active", "withdrawn_on": "2026-10-01"})
    assert "pas de date d'abandon" in response.get_data(as_text=True).replace("&#39;", "'")
    client.post(f"/annuaire/personnes/{zoe.id}/adhesions/nouvelle",
                data={"season_id": s.id, "status": "active", "category": "Scolaire", "school_class": "6e"})
    (m,) = db.session.get(Person, zoe.id).memberships
    assert m.category == "Scolaire" and m.school_class == "6e" and m.school_level is None
    # Plus de saison libre : le formulaire renvoie vers la fiche.
    assert client.get(f"/annuaire/personnes/{zoe.id}/adhesions/nouvelle").status_code == 302
    client.post(f"/annuaire/adhesions/{m.id}/modifier", data={"season_id": s.id, "status": "withdrawn",
                                                              "withdrawn_on": "2027-01-10"})
    m = db.session.get(Membership, m.id)
    assert m.status == "withdrawn" and m.withdrawn_on == date(2027, 1, 10)
    assert db.session.scalar(db.select(Activity).filter_by(action="membership.update")).changes["status"] == [
        "active", "withdrawn"]


def test_teacher_profile(client, admin, family):
    anne = family["anne"]
    response = client.post(f"/annuaire/personnes/{anne.id}/professeur",
                           data={"status": "CDI", "started_on": "2020-09-01", "left_on": "2019-01-01"})
    assert "ne peut pas précéder" in response.get_data(as_text=True)
    client.post(f"/annuaire/personnes/{anne.id}/professeur", data={"status": "CDI", "started_on": "2020-09-01",
                                                                   "work_email": "prof@ecole.test"})
    teacher = db.session.get(Person, anne.id).teacher
    assert teacher.status == "CDI" and teacher.active and teacher.work_email == "prof@ecole.test"
    client.post(f"/annuaire/personnes/{anne.id}/professeur", data={"status": "CDI", "left_on": "2021-06-30"})
    assert not db.session.get(Person, anne.id).teacher.active
    client.post(f"/annuaire/personnes/{anne.id}/professeur/supprimer")
    assert db.session.get(Person, anne.id).teacher is None and db.session.get(Teacher, anne.id) is None


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

def test_merging_two_persons(client, manager, family):
    old, current = season("2025-2026"), season("2026-2027", current=True)
    zoe = family["zoe"]
    copy = person("Zoé", "MARTIN", email="zoe@asso.test", birth_place="Rouen", notes="doublon de l'import")
    other_home = home("Famille Martin-Bis")
    directory.add_member(other_home, copy, D)
    directory.add_member(family["home"], copy, D)  # déjà dans le même foyer que l'original
    db.session.add_all([Phone(number="+33612345678", person=zoe), Phone(number="+33612345678", person=copy),
                        Phone(number="+33699999999", person=copy), ExternalRef(kind="student", value="7", person=copy),
                        Membership(person=zoe, season=current, category="original"),
                        Membership(person=copy, season=current, category="doublon"),
                        Membership(person=copy, season=old), Teacher(person=copy, status="Bénévole")])
    db.session.flush()
    from app.services.activity import log
    log("person.update", "trace du doublon", person=copy)
    db.session.commit()
    copy_id = copy.id

    page = client.get(f"/annuaire/personnes/{zoe.id}/fusionner?q=zoe").get_data(as_text=True)
    assert f"avec={copy_id}" in page
    client.post(f"/annuaire/personnes/{zoe.id}/fusionner", data={"avec": copy_id})

    zoe = db.session.get(Person, zoe.id)
    assert db.session.get(Person, copy_id) is None
    assert zoe.email == "zoe@asso.test" and zoe.birth_place == "Rouen" and "doublon de l'import" in zoe.notes
    assert {link.household.name for link in zoe.household_links} == {"Famille Martin", "Famille Martin-Bis"}
    assert sorted(p.number for p in zoe.phones) == ["+33612345678", "+33699999999"]
    assert [ref.value for ref in zoe.external_refs] == ["7"]
    assert {(m.season.label, m.category) for m in zoe.memberships} == {("2026-2027", "original"), ("2025-2026", None)}
    assert zoe.teacher.status == "Bénévole"
    assert db.session.scalar(db.select(Activity).filter_by(message="trace du doublon")).person_id == zoe.id
    merge = db.session.scalar(db.select(Activity).filter_by(action="person.merge"))
    assert "en double" in merge.message and merge.person_id == zoe.id


def test_merge_keeps_the_single_account_and_refuses_two(family):
    anne, marc = family["anne"], family["marc"]
    copy = person("Anne", "Martin", account=make_account(email="anne@asso.test"))
    directory.merge_persons(anne, copy)
    db.session.commit()
    assert anne.account.email == "anne@asso.test"
    marc.account = make_account(email="marc@asso.test")
    db.session.commit()
    assert directory.merge_problems(anne, marc)
    with pytest.raises(DirectoryError, match="chacune un compte"):
        directory.merge_persons(anne, marc)
    with pytest.raises(DirectoryError):
        directory.merge_persons(anne, anne)


def test_merge_moves_guardian_rank_and_bank_holder(family):
    second = home("Famille Durand")
    copy = person("Anne", "Durand")
    directory.add_member(second, copy, G)
    account = BankAccount(household_id=second.id, holder_name="Anne", holder_person_id=copy.id)
    bank.set_iban(account, IBAN)
    db.session.add(account)
    db.session.commit()
    directory.merge_persons(family["anne"], copy)
    db.session.commit()
    (durand,) = [link for link in family["anne"].household_links if link.household is second]
    assert durand.role == G and durand.guardian_rank == 1
    assert db.session.get(BankAccount, account.id).holder_person_id == family["anne"].id


def test_merging_households(client, admin, family):
    copy = home("Famille MARTIN", city="Rouen", notes="import")
    leo = person("Léo")
    directory.add_member(copy, family["anne"], G)
    directory.add_member(copy, leo, D)
    copy.phones.append(Phone(number="+33235000000"))
    account = BankAccount(household_id=copy.id, holder_name="Anne")
    bank.set_iban(account, IBAN)
    db.session.add(account)
    db.session.commit()
    copy_id = copy.id
    client.post(f"/annuaire/foyers/{family['home'].id}/fusionner", data={"avec": copy_id})
    kept = db.session.get(Household, family["home"].id)
    assert db.session.get(Household, copy_id) is None
    assert {m.person.first_name for m in kept.members} == {"Anne", "Marc", "Zoé", "Léo"}
    assert [p.number for p in kept.phones] == ["+33235000000"] and kept.notes == "import"
    (moved,) = kept.bank_accounts
    assert bank.reveal(moved) == IBAN  # rechiffré avec l'identifiant du foyer gardé


def test_household_merge_refuses_three_guardians(family):
    other = home("Famille Durand")
    directory.add_member(other, person("Paul"), G)
    assert "plus de deux responsables" in " ".join(directory.household_merge_problems(family["home"], other))
    with pytest.raises(DirectoryError):
        directory.merge_households(family["home"], other)


def test_merge_requires_its_right(client, app, family):
    db.session.add(Role(key="sec", name="Secrétariat", permissions=["directory.view", "directory.edit"]))
    db.session.commit()
    login(client, make_account("sec", email="sec@asso.test"))
    copy = person("Zoé")
    db.session.commit()
    assert client.post(f"/annuaire/personnes/{family['zoe'].id}/fusionner", data={"avec": copy.id}).status_code == 403
    assert db.session.get(Person, copy.id) is not None


# ---------------------------------------------------------------------------
# Comptes : rattachement et invitation
# ---------------------------------------------------------------------------

def test_suggestions_show_why_and_never_decide(family):
    homonym = make_account(email="autre@asso.test", first_name="Zoe")
    homonym.last_name = "Martin"
    shared = make_account(email="anne@asso.test", first_name="Pierre")
    db.session.commit()
    assert [(p.first_name, reasons) for p, reasons in directory.suggestions(shared)] == [("Anne", ["même email"])]
    assert [(p.first_name, reasons) for p, reasons in directory.suggestions(homonym)] == [("Zoé", ["même nom"])]
    assert shared.person is None and homonym.person is None


def test_linking_screen(client, admin, family):
    anne_account = make_account(email="anne@asso.test", first_name="Anne")
    anne_account.last_name = "Martin"
    db.session.commit()
    page = client.get("/annuaire/rattachement").get_data(as_text=True)
    assert "anne@asso.test" in page and "même email" in page and "même nom" in page
    client.post(f"/annuaire/rattachement/{anne_account.id}", data={"person_id": family["anne"].id})
    assert db.session.get(Person, family["anne"].id).account_id == anne_account.id
    entry = db.session.scalar(db.select(Activity).filter_by(action="account.link"))
    assert entry.target_account_id == anne_account.id and entry.person_id == family["anne"].id
    other = make_account(email="x@asso.test", first_name="X")
    response = client.post(f"/annuaire/rattachement/{other.id}", data={"person_id": family["anne"].id},
                           follow_redirects=True)
    assert "a déjà un compte" in response.get_data(as_text=True)
    assert "anne@asso.test" not in client.get("/annuaire/rattachement").get_data(as_text=True)
    client.post(f"/annuaire/personnes/{family['anne'].id}/compte/detacher")
    assert db.session.get(Person, family["anne"].id).account is None
    assert db.session.get(Account, anne_account.id) is not None


def test_creating_a_person_from_an_account(client, admin):
    client.post(f"/annuaire/rattachement/{admin.id}/fiche")
    assert admin.person.email == admin.email and admin.person.full_name == admin.full_name
    response = client.post(f"/annuaire/rattachement/{admin.id}/fiche", follow_redirects=True)
    assert "déjà une fiche" in response.get_data(as_text=True)


def test_inviting_an_adult_from_their_record(client, admin, family):
    response = client.post(f"/annuaire/personnes/{family['marc'].id}/compte")
    assert response.status_code == 200 and "/lien/" in response.get_data(as_text=True)  # sans SMTP : lien affiché
    account = db.session.get(Person, family["marc"].id).account
    assert account.email == "marc@asso.test" and account.role == Role.ADHERENT and account.active
    assert db.session.scalar(db.select(AccountToken).filter_by(account_id=account.id, purpose="invite")) is not None


@pytest.mark.parametrize("case, message", [("minor", "mineur"), ("no_email", "pas d'adresse"),
                                           ("taken", "déjà celle d'un autre compte"), ("archived", "archivée")])
def test_no_account_when_it_would_be_wrong(family, case, message):
    target = family["anne"]
    if case == "minor":
        target = family["zoe"]
        target.email = "zoe@asso.test"
    elif case == "no_email":
        target.email = None
    elif case == "taken":
        make_account(email="anne@asso.test")
    else:
        directory.archive(target)
    with pytest.raises(DirectoryError, match=message):
        directory.create_account(target)


def test_account_page_links_to_the_record(client, admin, family):
    family["anne"].account = make_account(email="anne@asso.test")
    db.session.commit()
    page = client.get(f"/comptes/{family['anne'].account.id}").get_data(as_text=True)
    assert f"/annuaire/personnes/{family['anne'].id}" in page


def test_linking_needs_both_rights(client, manager, family):
    assert client.get("/annuaire/rattachement").status_code == 403
    assert client.post(f"/annuaire/personnes/{family['marc'].id}/compte").status_code == 403
    assert family["marc"].account is None
