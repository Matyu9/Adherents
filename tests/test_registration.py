"""Inscriptions (phase 6) : pré-remplissage, contrôles, envoi, validation dans l'annuaire, IBAN des dossiers."""
from datetime import date, datetime, time, timedelta

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import (
    BankAccount,
    Campaign,
    CampaignConsent,
    Consent,
    Course,
    Discipline,
    Dossier,
    Enrollment,
    Level,
    Membership,
    Teacher,
)
from app.services import bank, directory, registration
from app.services.bank import BankKeyError
from app.services.registration import RegistrationError

from .test_annuaire import home, person, season

IBAN = "FR7630006000011234567890189"
NOW = datetime(2027, 6, 15, 12, 0)


@pytest.fixture
def world(app):
    """Saison 2026-2027 (en cours) et 2027-2028 ; la famille Martin : Anne (responsable) et Zoé, inscrite en flûte."""
    old, new = season("2026-2027", current=True), season("2027-2028")
    flute, fm = Discipline(name="Flûte"), Discipline(name="Formation musicale")
    c1, c2 = Level(name="Cycle 1", position=1), Level(name="Cycle 2", position=2)
    prof = person("Élodie", "Prof")
    prof.teacher = Teacher(status="CDI")
    db.session.add_all([flute, fm, c1, c2])
    db.session.flush()
    courses = {
        "flute_old": Course(season=old, discipline=flute, level=c1, teacher=prof.teacher, capacity=2),
        "flute_new": Course(season=new, discipline=flute, level=c1, teacher=prof.teacher, capacity=1,
                            weekday=2, starts_at=time(17), ends_at=time(18)),
        "flute_c2": Course(season=new, discipline=flute, level=c2),
        "fm_new": Course(season=new, discipline=fm, capacity=12),
    }
    db.session.add_all(courses.values())
    household = home(address_street="1 rue Haute", postal_code="76000", city="Rouen")
    anne = person(email="anne@asso.test", birth_date=date(1980, 1, 2))
    zoe = person("Zoé", birth_date=date(2015, 5, 6))
    directory.add_member(household, anne, "guardian", "mère")
    directory.add_member(household, zoe, "dependent")
    directory.add_phone(anne, "06 12 34 56 78")
    membership = Membership(person=zoe, season=old, category="Scolaire", school_class="CM2")
    db.session.add(membership)
    db.session.flush()
    membership.enrollments.append(Enrollment(course=courses["flute_old"]))
    campaign = Campaign(season=new, title="Réinscriptions 2027-2028", opens_at=datetime(2027, 6, 1),
                        closes_at=datetime(2027, 7, 1), allow_new=True)
    campaign.consents = [
        CampaignConsent(label="Droit à l'image", text="J'autorise les photos.", scope="student"),
        CampaignConsent(label="Règlement intérieur", text="J'ai lu le règlement.", scope="household", required=True),
    ]
    db.session.add(campaign)
    db.session.commit()
    return {"old": old, "new": new, "household": household, "anne": anne, "zoe": zoe, "campaign": campaign,
            "prof": prof, **courses}


def answer_consents(dossier, image=True):
    data = dict(dossier.data)
    image_id, rules_id = (str(c.id) for c in dossier.campaign.consents)
    data["consents"] = {rules_id: True}
    data["students"] = [dict(s, consents={image_id: image}) for s in data["students"]]
    data["answered_at"] = NOW.isoformat()
    data["answered_by"] = "Anne Martin"
    registration.save(dossier, data)


# ---------------------------------------------------------------------------
# Pré-remplissage
# ---------------------------------------------------------------------------

def test_prefill_from_the_directory(world):
    dossier = registration.dossier_for(world["campaign"], world["household"], None)
    data = dossier.data
    assert data["household"]["city"] == "Rouen" and data["household"]["name"] == "Famille Martin"
    assert data["guardians"] == [{"person_id": world["anne"].id, "civility": None, "first_name": "Anne",
                                  "last_name": "Martin", "email": "anne@asso.test", "phone": "+33612345678",
                                  "relationship": "mère"}]
    (zoe,) = data["students"]
    assert zoe["person_id"] == world["zoe"].id and zoe["enrolled"] and zoe["guardian"] is None
    assert zoe["birth_date"] == "2015-05-06" and zoe["school_class"] == "CM2"
    assert zoe["courses"] == [world["flute_new"].id]  # même discipline, même niveau sur la nouvelle saison
    # Le même foyer retrouve le même dossier.
    assert registration.dossier_for(world["campaign"], world["household"], None) is dossier


def test_an_adult_learner_is_his_own_student(world):
    alone = home("Famille Durand")
    paul = person("Paul", "Durand")
    directory.add_member(alone, paul, "guardian", "lui-même")
    db.session.add(Membership(person=paul, season=world["old"], status="withdrawn"))
    db.session.commit()
    data = registration.prefill(alone, world["campaign"])
    (student,) = data["students"]
    assert student["guardian"] == 0 and student["person_id"] == paul.id
    assert not student["enrolled"]  # il avait abandonné : à cocher s'il revient


# ---------------------------------------------------------------------------
# Contrôles, envoi, retour à la famille
# ---------------------------------------------------------------------------

def test_problems_before_submission(world):
    dossier = registration.dossier_for(world["campaign"], world["household"], None)
    assert registration.problems(dossier) == ["« Règlement intérieur » doit être accepté."]
    data = dict(dossier.data)
    data["household"] = dict(data["household"], city="")
    data["students"] = [dict(data["students"][0], courses=[], birth_date=None)]
    registration.save(dossier, data)
    found = registration.problems(dossier)
    assert "L'adresse du foyer est incomplète." in found
    assert "Le nom ou la date de naissance de Zoé Martin manque." in found
    assert "Choisissez au moins un cours pour Zoé Martin." in found
    data["students"] = [dict(data["students"][0], enrolled=False)]
    registration.save(dossier, data)
    assert "Inscrivez au moins un élève." in registration.problems(dossier)


def test_submit_return_and_resubmit(world):
    dossier = registration.dossier_for(world["campaign"], world["household"], None)
    with pytest.raises(RegistrationError, match="Règlement intérieur"):
        registration.submit(dossier, now=NOW)
    answer_consents(dossier)
    with pytest.raises(RegistrationError, match="fermée"):
        registration.submit(dossier, now=datetime(2027, 7, 2))
    registration.submit(dossier, now=NOW)
    assert dossier.status == Dossier.SUBMITTED and dossier.submitted_at == NOW
    with pytest.raises(RegistrationError, match="déjà été envoyé"):
        registration.save(dossier, dossier.data)
    with pytest.raises(RegistrationError, match="Expliquez"):
        registration.return_for_fix(dossier, " ")
    registration.return_for_fix(dossier, "La date de naissance de Zoé est fausse.")
    assert dossier.status == Dossier.TO_FIX and dossier.editable
    registration.save(dossier, dossier.data)
    registration.submit(dossier, now=NOW)
    assert dossier.status == Dossier.SUBMITTED


def test_a_course_that_disappeared_blocks_the_submission(world):
    dossier = registration.dossier_for(world["campaign"], world["household"], None)
    answer_consents(dossier)
    data = dict(dossier.data)
    data["students"] = [dict(data["students"][0], courses=[world["flute_old"].id])]  # cours de l'an dernier
    registration.save(dossier, data)
    assert any("n'est plus proposé" in p for p in registration.problems(dossier))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def submitted(world, **student):
    dossier = registration.dossier_for(world["campaign"], world["household"], None)
    answer_consents(dossier)
    data = dict(dossier.data)
    data["students"] = [dict(data["students"][0], **student)]
    data["guardians"] = [dict(data["guardians"][0], email="anne.martin@asso.test", phone="07 00 00 00 01")]
    data["bank"] = {"holder_name": "Anne Martin", "bic": "AGRIFRPP882"}
    registration.save(dossier, data)
    bank.set_dossier_iban(dossier, IBAN)
    registration.submit(dossier, now=NOW)
    db.session.commit()
    return dossier


def test_changes_shown_before_validation(world):
    dossier = submitted(world, school_class="6e")
    rows = registration.changes(dossier)
    assert ("Responsable 1 : Anne Martin", "Email", "anne@asso.test", "anne.martin@asso.test") in rows
    assert ("Responsable 1 : Anne Martin", "Téléphone", "06 12 34 56 78", "07 00 00 00 01") in rows
    assert ("Élève : Zoé Martin", "Cours", None, "Flûte · Cycle 1") in rows
    assert ("Famille Martin", "IBAN", None, "•••• 0189 (Anne Martin)") in rows


def test_validation_fills_the_directory(world, admin):
    dossier = submitted(world, school_class="6e", courses=[world["flute_new"].id, world["fm_new"].id])
    warnings = registration.validate(dossier, admin, now=NOW)
    db.session.commit()
    assert warnings == []
    anne, zoe = world["anne"], world["zoe"]
    assert anne.email == "anne.martin@asso.test"
    assert {p.number for p in anne.phones} == {"+33612345678", "+33700000001"}  # l'ancien numéro reste
    membership = next(m for m in zoe.memberships if m.season == world["new"])
    assert membership.status == "active" and membership.school_class == "6e" and membership.category == "Scolaire"
    assert {e.course.discipline.name for e in membership.enrollments} == {"Flûte", "Formation musicale"}
    assert all(e.dossier == dossier for e in membership.enrollments)
    consents = db.session.scalars(sa.select(Consent).order_by(Consent.label)).all()
    assert [(c.label, c.granted, c.person, c.household, c.given_at, c.given_by_name) for c in consents] == [
        ("Droit à l'image", True, zoe, None, NOW, "Anne Martin"),
        ("Règlement intérieur", True, None, world["household"], NOW, "Anne Martin")]
    assert consents[0].text == "J'autorise les photos."
    (account,) = world["household"].bank_accounts
    assert bank.reveal(account) == IBAN and account.holder == anne and account.bic == "AGRIFRPP882"
    assert dossier.status == Dossier.VALIDATED and dossier.validated_by == admin
    # Après validation, plus de différence, mais le report garde ce qui a changé.
    assert ("Responsable 1 : Anne Martin", "Email", "anne@asso.test", "anne.martin@asso.test") in \
        registration.changes_at_validation(dossier)
    with pytest.raises(RegistrationError):
        registration.validate(dossier, admin)


def test_consent_text_changed_later_keeps_what_was_accepted(world, admin):
    dossier = submitted(world)
    registration.validate(dossier, admin, now=NOW)
    world["campaign"].consents[0].text = "Nouveau texte"
    db.session.commit()
    consent = db.session.scalar(sa.select(Consent).filter_by(label="Droit à l'image"))
    assert consent.text == "J'autorise les photos."


def test_a_full_course_is_a_warning_at_validation(world, admin):
    other = person("Léo", "Autre")
    membership = Membership(person=other, season=world["new"])
    db.session.add(membership)
    db.session.flush()
    membership.enrollments.append(Enrollment(course=world["flute_new"]))  # effectif maximum : 1
    db.session.commit()
    assert world["flute_new"].is_full(registration.enrolled_count(world["flute_new"]))
    warnings = registration.validate(submitted(world), admin, now=NOW)
    assert warnings == ["Flûte · Cycle 1 était complet : Zoé Martin y est inscrit(e) quand même."]


def test_same_iban_already_on_the_household_is_not_added_twice(world, admin):
    account = BankAccount(household_id=world["household"].id, holder_name="Anne Martin")
    bank.set_iban(account, IBAN)
    db.session.add(account)
    db.session.commit()
    warnings = registration.validate(submitted(world), admin, now=NOW)
    assert "déjà enregistré" in warnings[0] and len(world["household"].bank_accounts) == 1


def test_a_new_family_enters_the_directory(world, admin):
    dossier, token = registration.new_family_dossier(world["campaign"], " Claire@Famille.TEST ")
    assert dossier.email == "claire@famille.test" and registration.find_by_access(token) is dossier
    data = registration.blank_data()
    data["household"] = {"name": "Famille Petit", "address_street": "4 rue Basse", "postal_code": "76100",
                         "city": "Rouen", "country": "FR"}
    data["guardians"] = [{"person_id": None, "first_name": "Claire", "last_name": "Petit", "email": "claire@famille.test",
                          "phone": "0611111111", "relationship": "mère"}]
    data["students"] = [
        {"key": "a", "person_id": None, "guardian": None, "enrolled": True, "first_name": "Hugo", "last_name": "Petit",
         "birth_date": "2018-03-04", "sex": "M", "courses": [world["fm_new"].id], "consents": {}},
        {"key": "b", "person_id": None, "guardian": 0, "enrolled": True, "courses": [world["flute_c2"].id],
         "consents": {}},
    ]
    data["consents"] = {str(world["campaign"].consents[1].id): True}
    registration.save(dossier, data)
    registration.submit(dossier, now=NOW)
    registration.validate(dossier, admin, now=NOW)
    db.session.commit()
    household = dossier.household
    assert household.name == "Famille Petit" and household.city == "Rouen"
    claire = household.guardians[0].person
    hugo = household.dependents[0].person
    assert claire.email == "claire@famille.test" and hugo.birth_date == date(2018, 3, 4) and hugo.email is None
    assert [e.course for m in claire.memberships for e in m.enrollments] == [world["flute_c2"]]
    assert [e.course for m in hugo.memberships for e in m.enrollments] == [world["fm_new"]]


def test_the_same_new_child_twice_is_refused(world, admin):
    dossier, _ = registration.new_family_dossier(world["campaign"], "x@famille.test")
    child = {"person_id": None, "guardian": None, "enrolled": True, "first_name": "Hugo", "last_name": "Petit",
             "birth_date": "2018-03-04", "courses": [world["fm_new"].id], "consents": {}}
    data = registration.blank_data()
    data["students"] = [dict(child, key="a"), dict(child, key="b", first_name="hugo")]
    dossier.data, dossier.status = data, Dossier.SUBMITTED
    with pytest.raises(RegistrationError, match="deux fois"):
        registration.validate(dossier, admin)


def test_new_families_only_when_the_campaign_allows_it(world):
    world["campaign"].allow_new = False
    with pytest.raises(RegistrationError):
        registration.new_family_dossier(world["campaign"], "x@famille.test")


def test_access_link_expires(world):
    dossier, token = registration.new_family_dossier(world["campaign"], "x@famille.test")
    dossier.access_expires_at = datetime.now() - timedelta(seconds=1)
    assert registration.find_by_access(token) is None
    assert registration.find_by_access("faux") is None and registration.find_by_access("x" * 200) is None


def test_who_can_open_a_dossier(world, accounts):
    from app.models import Role
    dossier = registration.dossier_for(world["campaign"], world["household"], None)
    parent = accounts[Role.ADHERENT]
    assert not registration.can_open(parent, dossier)
    world["anne"].account = parent
    db.session.commit()
    assert registration.can_open(parent, dossier)
    world["zoe"].account = accounts[Role.GESTIONNAIRE]  # une personne à charge n'ouvre pas le dossier
    assert not registration.can_open(accounts[Role.GESTIONNAIRE], dossier)
    new, _ = registration.new_family_dossier(world["campaign"], "x@famille.test")
    assert registration.can_open(None, new, session_access=[new.uuid])
    assert not registration.can_open(None, new, session_access=[dossier.uuid])


# ---------------------------------------------------------------------------
# IBAN des dossiers
# ---------------------------------------------------------------------------

def test_dossier_iban_is_bound_to_its_dossier(world):
    a = registration.dossier_for(world["campaign"], world["household"], None)
    b, _ = registration.new_family_dossier(world["campaign"], "x@famille.test")
    bank.set_dossier_iban(a, IBAN)
    db.session.commit()
    assert a.iban_last4 == "0189" and bank.reveal_dossier_iban(a) == IBAN
    b.iban_ciphertext, b.iban_key_version = a.iban_ciphertext, a.iban_key_version
    with pytest.raises(BankKeyError):
        bank.reveal_dossier_iban(b)
    raw = db.session.execute(sa.text("SELECT iban_ciphertext FROM dossier WHERE id = :i"), {"i": a.id}).scalar()
    assert IBAN.encode() not in bytes(raw)


def test_rotation_includes_dossiers(world, app):
    dossier = registration.dossier_for(world["campaign"], world["household"], None)
    bank.set_dossier_iban(dossier, IBAN)
    db.session.commit()
    from .test_bank import KEY_2
    assert dossier.iban_key_version == 1
    app.config["BANK_DATA_KEYS"] = f"{KEY_2},{app.config['BANK_DATA_KEYS']}"
    assert bank.rotate() == 1 and dossier.iban_key_version == 2
    app.config["BANK_DATA_KEYS"] = KEY_2
    assert bank.reveal_dossier_iban(dossier) == IBAN


# ---------------------------------------------------------------------------
# Fusions
# ---------------------------------------------------------------------------

def test_merging_two_records_keeps_enrollments_and_consents(world, admin):
    registration.validate(submitted(world), admin, now=NOW)
    db.session.commit()
    double = person("Zoé", "Martin", birth_date=date(2015, 5, 6))
    membership = Membership(person=double, season=world["new"])
    db.session.add(membership)
    db.session.flush()
    membership.enrollments.append(Enrollment(course=world["fm_new"]))
    db.session.commit()
    directory.merge_persons(double, world["zoe"])
    db.session.commit()
    kept = next(m for m in double.memberships if m.season == world["new"])
    assert {e.course.discipline.name for e in kept.enrollments} == {"Flûte", "Formation musicale"}
    assert [c.label for c in double.consents] == ["Droit à l'image"]


def test_merging_households_moves_dossiers_and_consents(world, admin):
    registration.validate(submitted(world), admin, now=NOW)
    db.session.commit()
    other = home("Famille Martin bis")
    directory.merge_households(other, world["household"])
    db.session.commit()
    dossier = db.session.scalar(sa.select(Dossier))
    assert dossier.household == other and [c.label for c in other.consents] == ["Règlement intérieur"]


def test_households_with_a_dossier_each_for_the_same_campaign_do_not_merge(world):
    other = home("Famille Martin bis")
    registration.dossier_for(world["campaign"], world["household"], None)
    registration.dossier_for(world["campaign"], other, None)
    db.session.commit()
    assert any("même campagne" in p for p in directory.household_merge_problems(other, world["household"]))


def test_a_course_with_students_cannot_be_deleted(client, admin, world):
    page = client.post(f"/catalogue/cours/{world['flute_old'].id}/supprimer", follow_redirects=True)
    assert "1 élève(s) sont inscrits" in page.get_data(as_text=True)
    assert db.session.get(Course, world["flute_old"].id) is not None


def test_a_season_with_a_campaign_cannot_be_deleted(client, admin, world):
    page = client.post(f"/annuaire/saisons/{world['new'].id}/supprimer", follow_redirects=True)
    assert "campagnes d&#39;inscription" in page.get_data(as_text=True)
