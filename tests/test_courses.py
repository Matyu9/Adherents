"""Gestion des cours, étape 1 : élèves d'un cours, inscription, transfert et abandon en cours de saison, cotisation
« à revoir ». Voir docs/cours.md."""

import csv
import io
from datetime import date

import pytest

from app.extensions import db
from app.models import Activity, Assessment, Course, Enrollment, HouseholdMember, Membership, Role
from app.services import courses, directory, fees
from app.services.courses import MAX_REVIEW_REASONS, CourseError

from .conftest import enroll, login, make_account
from .test_annuaire import home, person, season

G, D = HouseholdMember.GUARDIAN, HouseholdMember.DEPENDENT
SEPT, NOV, DEC = date(2026, 9, 14), date(2026, 11, 2), date(2026, 12, 7)


def course(s, discipline, **fields):
    record = Course(season=s, discipline=discipline, **fields)
    db.session.add(record)
    db.session.flush()
    return record


def adherent(household, s, first, last="Martin", role=D, **fields):
    record = person(first, last, **fields)
    directory.add_member(household, record, role)
    membership = Membership(person=record, season=s)
    db.session.add(membership)
    db.session.flush()
    return membership


def actions(name):
    return db.session.scalars(db.select(Activity).filter_by(action=name)).all()


@pytest.fixture
def school(app):
    """Saison 2026-2027 : flûte (2 places), piano, chorale ; Alice et Basile Martin, enfants d'Anne ; Chloé, adulte
    seule ; un foyer recomposé où Alice a aussi son père."""
    from app.models import Discipline

    s, other = season("2026-2027", current=True), season("2027-2028")
    flute, piano, choeur = Discipline(name="Flûte"), Discipline(name="Piano"), Discipline(name="Chœur")
    db.session.add_all([flute, piano, choeur])
    db.session.flush()
    martin = home("Famille Martin")
    anne = person("Anne", "Martin", email="anne@asso.test")
    directory.add_member(martin, anne, G)
    alice = adherent(martin, s, "Alice", birth_date=date(2014, 3, 1))
    basile = adherent(martin, s, "Basile", birth_date=date(2017, 5, 1))
    pere = home("Famille Durand")
    marc = person("Marc", "Durand", email="ANNE@asso.test")  # même adresse, autre casse
    directory.add_member(pere, marc, G)
    directory.add_member(pere, alice.person, D)
    seule = home("Chloé Petit")
    chloe = adherent(seule, s, "Chloé", "Petit", role=G, email="chloe@asso.test")
    record = {
        "season": s,
        "other": other,
        "flute": course(s, flute, capacity=2),
        "piano": course(s, piano),
        "choeur": course(s, choeur),
        "flute_next": course(other, flute),
        "martin": martin,
        "pere": pere,
        "seule": seule,
        "alice": alice,
        "basile": basile,
        "chloe": chloe,
        "anne": anne,
        "marc": marc,
    }
    db.session.commit()
    return record


def assessed(household, s):
    """Une cotisation proposée, déjà calculée (sans élément : le total importe peu ici)."""
    assessment = fees.assess(household, s)
    db.session.flush()
    return assessment


# ---------------------------------------------------------------------------
# Inscrire
# ---------------------------------------------------------------------------


def test_inscrire_en_cours_de_saison(school):
    enrollment, warnings = courses.enroll(school["alice"], school["flute"], NOV)
    assert (enrollment.course, enrollment.enrolled_on, enrollment.active, warnings) == (school["flute"], NOV, True, [])
    assert enrollment.replaces is None
    assert [a.message for a in actions("enrollment.create")] == ["Alice Martin inscrit(e) à Flûte (2026-2027)"]
    assert actions("enrollment.create")[0].person_id == school["alice"].person_id


def test_date_par_defaut_aujourd_hui(school):
    enrollment, _ = courses.enroll(school["alice"], school["piano"])
    assert enrollment.enrolled_on == date.today()


@pytest.mark.parametrize(
    "case, message",
    [
        ("autre saison", "pas de la même saison"),
        ("adhésion abandonnée", "est abandonnée"),
        ("déjà inscrite", "déjà inscrit(e)"),
        ("hors saison", "comprise dans la saison 2026-2027"),
        ("sans date", None),
    ],
)
def test_inscription_refusee(school, case, message):
    alice, target, on = school["alice"], school["flute"], NOV
    if case == "autre saison":
        target = school["flute_next"]
    elif case == "adhésion abandonnée":
        alice.status, alice.withdrawn_on = Membership.WITHDRAWN, SEPT
    elif case == "déjà inscrite":
        courses.enroll(alice, target, SEPT)
    elif case == "hors saison":
        on = date(2027, 9, 1)
    if case == "sans date":  # la date par défaut est aujourd'hui, mais un formulaire vide ne passe pas par là
        with pytest.raises(CourseError, match="Indiquez la date"):
            courses._check_date(None, alice)
        return
    with pytest.raises(CourseError, match=message.replace("(", r"\(").replace(")", r"\)")):
        courses.enroll(alice, target, on)


def test_cours_complet_inscrit_quand_meme_avec_un_avertissement(school):
    courses.enroll(school["alice"], school["flute"], SEPT)
    courses.enroll(school["basile"], school["flute"], SEPT)
    _, warnings = courses.enroll(school["chloe"], school["flute"], SEPT)
    assert warnings == ["Flûte était complet : Chloé Petit y est inscrit(e) quand même."]
    school["piano"].marked_full = True
    _, warnings = courses.enroll(school["alice"], school["piano"], SEPT)
    assert len(warnings) == 1


def test_reinscription_reprend_l_inscription_abandonnee(school):
    first, _ = courses.enroll(school["alice"], school["flute"], SEPT)
    courses.withdraw(first, NOV)
    again, _ = courses.enroll(school["alice"], school["flute"], DEC)
    assert again is first
    assert (again.status, again.enrolled_on, again.withdrawn_on) == (Enrollment.ACTIVE, DEC, None)
    assert len(school["alice"].enrollments) == 1


# ---------------------------------------------------------------------------
# Abandonner un cours
# ---------------------------------------------------------------------------


def test_abandon_d_un_seul_cours(school):
    flute, _ = courses.enroll(school["alice"], school["flute"], SEPT)
    piano, _ = courses.enroll(school["alice"], school["piano"], SEPT)
    courses.withdraw(flute, NOV)
    assert (flute.status, flute.withdrawn_on) == (Enrollment.WITHDRAWN, NOV)
    assert piano.active and school["alice"].status == Membership.ACTIVE
    assert [a.message for a in actions("enrollment.withdraw")] == ["Alice Martin quitte Flûte (2026-2027)"]


def test_abandon_refuse(school):
    enrollment, _ = courses.enroll(school["alice"], school["flute"], NOV)
    with pytest.raises(CourseError, match="ne peut pas précéder"):
        courses.withdraw(enrollment, SEPT)
    courses.withdraw(enrollment, NOV)  # le jour même : l'élève n'a jamais suivi le cours
    with pytest.raises(CourseError, match="déjà terminée"):
        courses.withdraw(enrollment, DEC)


# ---------------------------------------------------------------------------
# Transférer
# ---------------------------------------------------------------------------


def test_transfert(school):
    old, _ = courses.enroll(school["alice"], school["flute"], SEPT)
    new, warnings = courses.transfer(old, school["piano"], NOV)
    assert (old.status, old.withdrawn_on) == (Enrollment.WITHDRAWN, NOV)
    assert (new.course, new.enrolled_on, new.replaces, warnings) == (school["piano"], NOV, old, [])
    # Une seule entrée au journal, pas une inscription et un abandon en plus
    assert [a.message for a in actions("enrollment.transfer")] == [
        "Alice Martin passe de Flûte (2026-2027) à Piano (2026-2027)"
    ]
    assert actions("enrollment.create") == [] or all("Piano" not in a.message for a in actions("enrollment.create"))
    assert not actions("enrollment.withdraw")


def test_transfert_vers_un_cours_quitte_reprend_l_inscription(school):
    flute, _ = courses.enroll(school["alice"], school["flute"], SEPT)
    piano, _ = courses.transfer(flute, school["piano"], NOV)
    back, _ = courses.transfer(piano, school["flute"], DEC)
    assert back is flute and back.replaces is piano and back.enrolled_on == DEC
    assert len(school["alice"].enrollments) == 2


@pytest.mark.parametrize("case", ["même cours", "terminée", "avant l'entrée", "autre saison", "déjà inscrite"])
def test_transfert_refuse_ne_change_rien(school, case):
    enrollment, _ = courses.enroll(school["alice"], school["flute"], NOV)
    target, on = school["piano"], DEC
    if case == "même cours":
        target = school["flute"]
    elif case == "terminée":
        courses.withdraw(enrollment, NOV)
    elif case == "avant l'entrée":
        on = SEPT
    elif case == "autre saison":
        target = school["flute_next"]
    elif case == "déjà inscrite":
        courses.enroll(school["alice"], target, NOV)
    db.session.commit()
    before = (enrollment.status, enrollment.withdrawn_on)
    with pytest.raises(CourseError):
        courses.transfer(enrollment, target, on)
    db.session.rollback()
    assert (enrollment.status, enrollment.withdrawn_on) == before


# ---------------------------------------------------------------------------
# Élèves d'un cours
# ---------------------------------------------------------------------------


def test_liste_des_eleves(school):
    basile, _ = courses.enroll(school["basile"], school["flute"], SEPT)
    alice, _ = courses.enroll(school["alice"], school["flute"], SEPT)
    chloe, _ = courses.enroll(school["chloe"], school["flute"], SEPT)
    courses.withdraw(chloe, NOV)
    assert courses.roster(school["flute"]) == ([alice, basile], [chloe])


def test_responsables_de_chaque_foyer_sans_doublon(school):
    assert courses.guardians_of(school["alice"].person) == [school["anne"], school["marc"]]
    assert courses.guardians_of(school["chloe"].person) == []


def test_adresses_des_familles(school):
    for key in ("alice", "basile", "chloe"):
        courses.enroll(school[key], school["piano"], SEPT)
    # Anne et Marc partagent l'adresse (casse différente) ; Chloé, adulte seule, reçoit à sa propre adresse.
    assert courses.recipients(school["piano"]) == ["anne@asso.test", "chloe@asso.test"]


def test_export_des_eleves(school):
    courses.enroll(school["alice"], school["piano"], NOV)
    courses.enroll(school["chloe"], school["piano"], SEPT)
    rows = courses.roster_rows(school["piano"])
    assert rows == [
        (
            "Martin",
            "Alice",
            "2014-03-01",
            "2026-11-02",
            "Anne Martin, Marc Durand",
            "",
            "anne@asso.test, ANNE@asso.test",
        ),
        ("Petit", "Chloé", "", "2026-09-14", "", "", "chloe@asso.test"),
    ]


def test_eleves_proposes_a_l_inscription(school):
    from datetime import datetime

    courses.enroll(school["alice"], school["flute"], SEPT)
    withdrawn, _ = courses.enroll(school["basile"], school["flute"], SEPT)
    courses.withdraw(withdrawn, NOV)
    school["chloe"].person.archived_at = datetime(2026, 10, 1)
    other = adherent(home("Autre"), school["other"], "Zoé")  # adhésion d'une autre saison
    proposed = courses.candidates(school["flute"])
    assert proposed == [school["basile"]]
    assert other not in proposed


def test_cours_proposes_a_un_eleve(school):
    courses.enroll(school["alice"], school["flute"], SEPT)
    proposed, counts = courses.open_courses(school["alice"])
    assert proposed == [school["choeur"], school["piano"]] and counts == {school["flute"].id: 1}
    proposed, _ = courses.open_courses(school["basile"], exclude=school["piano"])
    assert school["piano"] not in proposed and school["flute"] in proposed


# ---------------------------------------------------------------------------
# Cotisation « à revoir »
# ---------------------------------------------------------------------------


def test_chaque_changement_marque_la_cotisation_de_chaque_foyer(school):
    s = school["season"]
    martin, pere = assessed(school["martin"], s), assessed(school["pere"], s)
    enrollment, _ = courses.enroll(school["alice"], school["flute"], SEPT)
    assert martin.review_reasons == pere.review_reasons == ["Alice Martin inscrit(e) à Flûte le 14/09/2026"]
    courses.transfer(enrollment, school["piano"], NOV)
    assert martin.review_reasons[-1] == "Alice Martin est passé(e) de Flûte à Piano le 02/11/2026"
    assert len(martin.review_reasons) == 2  # le transfert compte pour un seul changement
    courses.withdraw(school["alice"].enrollments[-1], DEC)
    assert martin.review_reasons[-1] == "Alice Martin a quitté Piano le 07/12/2026"


def test_sans_cotisation_calculee_rien_a_marquer(school):
    courses.enroll(school["chloe"], school["flute"], SEPT)
    assert db.session.scalars(db.select(Assessment)).all() == []


def test_cotisation_validee_garde_son_statut(school):
    s = school["season"]
    assessment = assessed(school["seule"], s)
    fees.validate(assessment)
    courses.enroll(school["chloe"], school["flute"], SEPT)
    assert assessment.status == Assessment.VALIDATED and assessment.review_reasons


def test_motifs_sans_doublon_et_limites(school):
    assessment = assessed(school["seule"], school["season"])
    person = school["chloe"].person
    courses.flag_review(person, school["season"], "même motif")
    courses.flag_review(person, school["season"], "même motif")
    assert assessment.review_reasons == ["même motif"]
    for n in range(MAX_REVIEW_REASONS + 5):
        courses.flag_review(person, school["season"], f"motif {n}")
    assert len(assessment.review_reasons) == MAX_REVIEW_REASONS
    assert assessment.review_reasons[-1] == f"motif {MAX_REVIEW_REASONS + 4}"


def test_autre_saison_non_marquee(school):
    assessment = assessed(school["seule"], school["season"])
    courses.flag_review(school["chloe"].person, school["other"], "ailleurs")
    assert assessment.review_reasons == []


def test_recalcul_efface_le_signal(school):
    assessment = assessed(school["seule"], school["season"])
    courses.enroll(school["chloe"], school["flute"], SEPT)
    fees.assess(school["seule"], school["season"])
    assert assessment.review_reasons == []


def test_rien_a_changer(school):
    assessment = assessed(school["seule"], school["season"])
    with pytest.raises(fees.FeeError, match="pas à revoir"):
        fees.acknowledge_review(assessment)
    courses.enroll(school["chloe"], school["flute"], SEPT)
    fees.acknowledge_review(assessment)
    assert assessment.review_reasons == []
    assert "revue sans changement (1 changement(s) d'inscription)" in actions("fee.review")[0].message


# ---------------------------------------------------------------------------
# Écrans
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(client, accounts):
    login(client, accounts[Role.GESTIONNAIRE])
    return client


def page(response):
    assert response.status_code == 200, response.status_code
    return response.get_data(as_text=True)


def test_page_du_cours(manager, school):
    courses.enroll(school["alice"], school["flute"], SEPT)
    gone, _ = courses.enroll(school["chloe"], school["flute"], SEPT)
    courses.transfer(gone, school["piano"], NOV)
    db.session.commit()
    html = page(manager.get(f"/catalogue/cours/{school['flute'].id}"))
    assert "MARTIN Alice" in html and "Anne Martin" in html and "Marc Durand" in html
    assert "<strong>1</strong> élève sur 2" in html
    assert "Partis en cours de saison (1)" in html and "passé(e) à Piano" in html
    assert "mailto:?bcc=anne%40asso.test" in html
    assert "Changer de cours" in html and "Noter l'abandon" in html
    html = page(manager.get(f"/catalogue/cours/{school['piano'].id}"))
    assert "venu(e) de Flûte" in html


def test_page_du_cours_reservee_a_qui_voit_l_annuaire(client, school):
    from app.models import Role as RoleModel

    db.session.add(RoleModel(key="catalogue", name="Catalogue", permissions=["catalog.view"]))
    login(client, make_account("catalogue", email="cat@asso.test"))
    assert client.get(f"/catalogue/cours/{school['flute'].id}").status_code == 403
    assert client.get(f"/catalogue/cours/{school['flute'].id}/eleves.csv").status_code == 403
    html = page(client.get(f"/catalogue?saison={school['season'].id}"))
    assert f'/catalogue/cours/{school["flute"].id}"' not in html  # pas de lien vers une page refusée


def test_lecture_seule_sans_enrollment_edit(client, school):
    from app.models import Role as RoleModel

    db.session.add(RoleModel(key="lecture", name="Lecture", permissions=["catalog.view", "directory.view"]))
    login(client, enroll(make_account("lecture", email="lecture@asso.test")))  # l'annuaire demande le TOTP
    courses.enroll(school["alice"], school["flute"], SEPT)
    db.session.commit()
    html = page(client.get(f"/catalogue/cours/{school['flute'].id}"))
    assert "MARTIN Alice" in html and "Changer de cours" not in html and "Inscrire un élève" not in html
    assert client.get(f"/catalogue/cours/{school['flute'].id}/inscrire").status_code == 403


def test_export_csv(manager, school):
    courses.enroll(school["alice"], school["flute"], SEPT)
    db.session.commit()
    response = manager.get(f"/catalogue/cours/{school['flute'].id}/eleves.csv")
    assert response.status_code == 200 and response.mimetype == "text/csv"
    assert response.headers["Cache-Control"] == "no-store"
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip("﻿")), delimiter=";"))
    assert rows[0] == list(courses.ROSTER_COLUMNS) and rows[1][:2] == ["Martin", "Alice"]


def test_inscrire_depuis_le_cours(manager, school):
    html = page(manager.get(f"/catalogue/cours/{school['flute'].id}/inscrire"))
    assert "MARTIN Alice" in html and "PETIT Chloé" in html
    response = manager.post(
        f"/catalogue/cours/{school['flute'].id}/inscrire",
        data={"membership_id": school["alice"].id, "on": "2026-11-02"},
    )
    assert response.status_code == 302 and response.location.endswith(f"/catalogue/cours/{school['flute'].id}")
    enrollment = db.session.scalar(db.select(Enrollment))
    assert (enrollment.membership_id, enrollment.enrolled_on) == (school["alice"].id, NOV)


def test_inscription_hors_saison_affiche_l_erreur(manager, school):
    response = manager.post(
        f"/catalogue/cours/{school['flute'].id}/inscrire",
        data={"membership_id": school["alice"].id, "on": "2028-01-01"},
    )
    assert "comprise dans la saison" in page(response)
    assert db.session.scalar(db.select(Enrollment)) is None


def test_eleve_non_propose_refuse(manager, school):
    courses.enroll(school["alice"], school["flute"], SEPT)
    db.session.commit()
    response = manager.post(
        f"/catalogue/cours/{school['flute'].id}/inscrire",
        data={"membership_id": school["alice"].id, "on": "2026-11-02"},
    )
    assert "pas proposé" in page(response)


def test_inscrire_depuis_la_fiche(manager, school):
    alice = school["alice"]
    html = page(manager.get(f"/annuaire/personnes/{alice.person_id}"))
    assert f"/catalogue/adhesions/{alice.id}/inscrire" in html
    html = page(manager.get(f"/catalogue/adhesions/{alice.id}/inscrire"))
    assert "Flûte" in html and "2027-2028" not in html
    response = manager.post(
        f"/catalogue/adhesions/{alice.id}/inscrire", data={"course_id": school["piano"].id, "on": "2026-09-14"}
    )
    assert response.location.endswith(f"/annuaire/personnes/{alice.person_id}#adhesions")
    html = page(manager.get(f"/annuaire/personnes/{alice.person_id}"))
    assert "Piano" in html and "changer" in html


def test_cours_complet_signale_dans_la_liste(manager, school):
    school["flute"].marked_full = True
    db.session.commit()
    html = page(manager.get(f"/catalogue/adhesions/{school['alice'].id}/inscrire"))
    assert "Flûte (2026-2027) — complet" in html


def test_transferer_depuis_la_fiche(manager, school):
    enrollment, _ = courses.enroll(school["alice"], school["flute"], SEPT)
    db.session.commit()
    url = f"/catalogue/inscriptions/{enrollment.id}/transferer"
    html = page(manager.get(url + "?retour=fiche"))
    assert 'name="retour" value="fiche"' in html and "Piano" in html
    response = manager.post(url, data={"course_id": school["piano"].id, "on": "2026-11-02", "retour": "fiche"})
    assert response.location.endswith(f"/annuaire/personnes/{school['alice'].person_id}#adhesions")
    assert db.session.get(Enrollment, enrollment.id).status == Enrollment.WITHDRAWN


def test_transferer_depuis_le_cours_revient_au_nouveau_cours(manager, school):
    enrollment, _ = courses.enroll(school["alice"], school["flute"], SEPT)
    db.session.commit()
    response = manager.post(
        f"/catalogue/inscriptions/{enrollment.id}/transferer",
        data={"course_id": school["piano"].id, "on": "2026-11-02", "retour": "ailleurs"},
    )
    assert response.location.endswith(f"/catalogue/cours/{school['piano'].id}")


def test_transferer_une_inscription_terminee(manager, school):
    enrollment, _ = courses.enroll(school["alice"], school["flute"], SEPT)
    courses.withdraw(enrollment, NOV)
    db.session.commit()
    response = manager.get(f"/catalogue/inscriptions/{enrollment.id}/transferer", follow_redirects=True)
    assert "déjà terminée" in page(response)


def test_noter_un_abandon(manager, school):
    enrollment, _ = courses.enroll(school["alice"], school["flute"], SEPT)
    db.session.commit()
    response = manager.post(f"/catalogue/inscriptions/{enrollment.id}/abandon", data={"on": "2026-11-02"})
    assert response.location.endswith(f"/catalogue/cours/{school['flute'].id}")
    enrollment = db.session.get(Enrollment, enrollment.id)
    assert (enrollment.status, enrollment.withdrawn_on) == (Enrollment.WITHDRAWN, NOV)
    html = page(
        manager.post(
            f"/catalogue/inscriptions/{enrollment.id}/abandon", data={"on": "2026-12-07"}, follow_redirects=True
        )
    )
    assert "déjà terminée" in html


def test_abandon_sans_date(manager, school):
    enrollment, _ = courses.enroll(school["alice"], school["flute"], SEPT)
    db.session.commit()
    html = page(
        manager.post(f"/catalogue/inscriptions/{enrollment.id}/abandon", data={"on": ""}, follow_redirects=True)
    )
    assert "Indiquez une date valide" in html
    assert db.session.get(Enrollment, enrollment.id).active


def test_adhesion_abandonnee_ou_ajoutee_marque_la_cotisation(manager, school):
    s = school["season"]
    assessment = assessed(school["martin"], s)
    db.session.commit()
    basile = school["basile"]
    manager.post(
        f"/annuaire/adhesions/{basile.id}/modifier",
        data={"season_id": s.id, "status": "withdrawn", "withdrawn_on": "2026-11-02"},
    )
    assert db.session.get(Assessment, assessment.id).review_reasons == ["Adhésion 2026-2027 de Basile Martin : abandon"]
    anne = school["anne"]
    manager.post(f"/annuaire/personnes/{anne.id}/adhesions/nouvelle", data={"season_id": s.id, "status": "active"})
    assert db.session.get(Assessment, assessment.id).review_reasons[-1] == "Adhésion 2026-2027 de Anne Martin ajoutée"


def test_cotisation_a_revoir_a_l_ecran(manager, school):
    s = school["season"]
    assessment = assessed(school["martin"], s)
    courses.enroll(school["alice"], school["flute"], SEPT)
    other = assessed(school["seule"], s)
    db.session.commit()
    html = page(manager.get(f"/tarifs/cotisations?saison={s.id}&statut=review"))
    assert "Famille Martin" in html and "Chloé Petit" not in html and "à revoir" in html
    html = page(manager.get(f"/tarifs/foyers/{school['martin'].id}?saison={s.id}"))
    assert "Alice Martin inscrit(e) à Flûte le 14/09/2026" in html and "rien à changer" in html
    response = manager.post(f"/tarifs/cotisations/{assessment.id}/revue")
    assert response.status_code == 302
    assert db.session.get(Assessment, assessment.id).review_reasons == []
    html = page(manager.post(f"/tarifs/cotisations/{other.id}/revue", follow_redirects=True))
    assert "pas à revoir" in html


def test_validation_d_un_dossier_marque_la_cotisation(school):
    """Un second dossier validé pour un foyer déjà calculé : la cotisation est à revoir, et l'inscription datée."""
    from datetime import datetime

    from app.models import Campaign, Dossier
    from app.services import registration

    s = school["season"]
    assessment = assessed(school["martin"], s)
    campaign = Campaign(season=s, title="Inscriptions", opens_at=datetime(2026, 6, 1), closes_at=datetime(2026, 12, 1))
    db.session.add(campaign)
    dossier = registration.dossier_for(campaign, school["martin"], None)
    student = next(st for st in dossier.data["students"] if st.get("person_id") == school["basile"].person_id)
    student.update({"enrolled": True, "courses": [school["piano"].id]})
    dossier.data = {**dossier.data}
    dossier.status = Dossier.SUBMITTED
    db.session.flush()
    registration.validate(dossier, None, now=datetime(2026, 10, 5, 10))
    enrollment = next(e for e in school["basile"].enrollments if e.course_id == school["piano"].id)
    assert enrollment.enrolled_on == date(2026, 10, 5)
    assert "Basile Martin inscrit(e) à Piano par un dossier validé" in assessment.review_reasons


# ---------------------------------------------------------------------------
# Étape 2 : cours individuels
# ---------------------------------------------------------------------------


@pytest.fixture
def lessons(school):
    """Piano individuel le mercredi de 17 h à 19 h ; chorale collective le mercredi de 18 h à 19 h."""
    from datetime import time

    piano, choeur = school["piano"], school["choeur"]
    piano.individual, piano.weekday, piano.starts_at, piano.ends_at = True, 2, time(17), time(19)
    choeur.weekday, choeur.starts_at, choeur.ends_at = 2, time(18), time(19)
    alice, _ = courses.enroll(school["alice"], piano, SEPT)
    basile, _ = courses.enroll(school["basile"], piano, SEPT)
    db.session.commit()
    return {**school, "e_alice": alice, "e_basile": basile}


def t(text):
    from datetime import time

    return time.fromisoformat(text)


def test_passage_et_fin_calculee(lessons):
    alice = lessons["e_alice"]
    assert courses.set_slot(alice, t("17:30"), 30) == []
    assert (alice.slot_starts_at, alice.slot_ends_at) == (t("17:30"), t("18:00"))
    assert actions("enrollment.slot")[0].message == "Passage de Alice Martin en Piano : 17:30, 30 min"


def test_duree_sans_heure_puis_effacer(lessons):
    alice = lessons["e_alice"]
    courses.set_slot(alice, None, 45)
    assert (alice.slot_starts_at, alice.slot_minutes, alice.slot_ends_at) == (None, 45, None)
    courses.set_slot(alice, None, None)
    assert alice.slot_minutes is None
    assert actions("enrollment.slot")[-1].message.endswith(": effacé")


@pytest.mark.parametrize(
    "case, message",
    [
        ("cours collectif", "que les cours individuels"),
        ("heure sans durée", "Indiquez la durée"),
        ("durée nulle", "entre 1 et 240"),
        ("trop long", "entre 1 et 240"),
        ("après minuit", "avant minuit"),
        ("terminée", "terminée"),
    ],
)
def test_passage_refuse(lessons, case, message):
    enrollment, start, minutes = lessons["e_alice"], t("17:00"), 30
    if case == "cours collectif":
        enrollment, _ = courses.enroll(lessons["alice"], lessons["choeur"], SEPT)
    elif case == "heure sans durée":
        minutes = None
    elif case == "durée nulle":
        minutes = 0
    elif case == "trop long":
        minutes = 300
    elif case == "après minuit":
        start, minutes = t("23:45"), 30
    elif case == "terminée":
        courses.withdraw(enrollment, NOV)
    with pytest.raises(CourseError, match=message):
        courses.set_slot(enrollment, start, minutes)


def test_avertissements_de_passage(lessons):
    alice, basile = lessons["e_alice"], lessons["e_basile"]
    courses.set_slot(basile, t("17:00"), 30)
    # Bord à bord : 17:30 après un passage qui finit à 17:30, pas de chevauchement.
    assert courses.set_slot(alice, t("17:30"), 30) == []
    assert courses.set_slot(alice, t("17:15"), 30) == ["Chevauche le passage de Basile Martin (17:00-17:30)."]
    assert courses.set_slot(alice, t("18:45"), 30) == ["Hors du créneau du cours (17:00-19:00)."]
    assert courses.set_slot(alice, t("16:30"), 30) == ["Hors du créneau du cours (17:00-19:00)."]
    # Alice chante aussi à 18 h le même jour.
    courses.enroll(lessons["alice"], lessons["choeur"], SEPT)
    assert courses.set_slot(alice, t("18:00"), 30) == ["L'élève a aussi Chœur à 18:00."]


def test_planning_et_plages_libres(lessons):
    alice, basile = lessons["e_alice"], lessons["e_basile"]
    courses.set_slot(alice, t("18:00"), 30)
    chloe, _ = courses.enroll(lessons["chloe"], lessons["piano"], SEPT)  # sans heure : à la fin
    courses.set_slot(basile, t("17:00"), 45)
    rows, free = courses.planning(lessons["piano"])
    assert [e for e, _ in rows] == [basile, alice, chloe]
    assert free == [("17:45", "18:00"), ("18:30", "19:00")]


def test_planning_sans_horaire_de_cours(lessons):
    lessons["piano"].starts_at = lessons["piano"].ends_at = None
    courses.set_slot(lessons["e_alice"], t("17:00"), 30)
    rows, free = courses.planning(lessons["piano"])
    assert free == [] and rows[0][1] == []


def test_cours_individuel_recopie(school):
    from app.services import catalog

    school["piano"].individual = True
    db.session.delete(school["flute_next"])
    db.session.flush()
    catalog.copy_season(school["season"], school["other"])
    copied = db.session.scalar(db.select(Course).filter_by(season_id=school["other"].id, individual=True))
    assert copied.discipline.name == "Piano"


def test_page_d_un_cours_individuel(manager, lessons):
    courses.set_slot(lessons["e_basile"], t("17:00"), 30)
    db.session.commit()
    piano = lessons["piano"]
    html = page(manager.get(f"/catalogue/cours/{piano.id}"))
    assert "cours individuel" in html and "17:00-17:30" in html and "Plages libres : 17:30-19:00" in html
    assert html.index("MARTIN Basile") < html.index("MARTIN Alice")
    response = manager.post(
        f"/catalogue/inscriptions/{lessons['e_alice'].id}/passage", data={"starts_at": "17:15", "minutes": "30"}
    )
    assert response.location.endswith(f"/catalogue/cours/{piano.id}#e{lessons['e_alice'].id}")
    html = page(manager.get(f"/catalogue/cours/{piano.id}"))
    assert "Chevauche le passage de Basile Martin" in html
    html = page(
        manager.post(
            f"/catalogue/inscriptions/{lessons['e_alice'].id}/passage",
            data={"starts_at": "17:15", "minutes": ""},
            follow_redirects=True,
        )
    )
    assert "Indiquez la durée" in html
    html = page(
        manager.post(
            f"/catalogue/inscriptions/{lessons['e_alice'].id}/passage", data={"starts_at": "xx"}, follow_redirects=True
        )
    )
    assert "illisible" in html


def test_case_cours_individuel_au_formulaire(manager, school):
    piano = school["piano"]
    data = {"discipline_id": piano.discipline_id, "individual": "y"}
    assert manager.post(f"/catalogue/cours/{piano.id}/modifier", data=data).status_code == 302
    assert db.session.get(Course, piano.id).individual is True


def test_heure_de_passage_chez_le_professeur_et_la_famille(client, lessons):
    from app.models import Teacher
    from app.services import registration

    piano = lessons["piano"]
    prof = person("Paul", "Prof")
    prof.teacher = Teacher(status="CDI")
    db.session.flush()
    piano.teacher = prof.teacher
    courses.set_slot(lessons["e_basile"], t("18:00"), 30)
    courses.set_slot(lessons["e_alice"], t("17:00"), 30)
    db.session.commit()
    ((course_row, students),) = registration.students_of(prof.teacher, lessons["season"])
    assert [s.first_name for s, _, _ in students] == ["Alice", "Basile"]
    account = make_account(Role.ADHERENT, email="anne@asso.test")
    lessons["anne"].account = account
    db.session.commit()
    login(client, account)
    html = page(client.get("/ma-famille"))
    assert "Mercredi 17:00-17:30" in html and "Mercredi 18:00-18:30" in html


# ---------------------------------------------------------------------------
# Étape 3 : liste d'attente
# ---------------------------------------------------------------------------


@pytest.fixture
def full(school):
    """Flûte (2 places) complète avec Alice et Basile ; un gestionnaire pour recevoir les notifications."""
    courses.enroll(school["alice"], school["flute"], SEPT)
    basile, _ = courses.enroll(school["basile"], school["flute"], SEPT)
    manager_account = make_account(Role.GESTIONNAIRE, email="gestion@asso.test")
    db.session.commit()
    return {**school, "e_basile": basile, "manager": manager_account}


def notifications(account):
    from app.models import Notification

    return db.session.scalars(db.select(Notification).filter_by(account_id=account.id)).all()


def test_attente_dans_l_ordre(full):
    from app.models import WaitlistEntry

    chloe = courses.add_to_waitlist(full["chloe"], full["flute"])
    other = adherent(home("Autre"), full["season"], "Zoé")
    zoe = courses.add_to_waitlist(other, full["flute"])
    assert courses.waitlist(full["flute"]) == [chloe, zoe]
    assert (chloe.position, zoe.position, chloe.status) == (1, 2, WaitlistEntry.WAITING)
    courses.move_in_waitlist(zoe, -1)
    assert courses.waitlist(full["flute"]) == [zoe, chloe]
    courses.move_in_waitlist(zoe, -1)  # déjà premier : rien ne bouge
    assert courses.waitlist(full["flute"]) == [zoe, chloe]
    assert [a.message for a in actions("waitlist.add")][0] == "Chloé Petit en attente pour Flûte (2026-2027)"


@pytest.mark.parametrize(
    "case, message",
    [
        ("déjà en attente", "déjà en attente"),
        ("déjà inscrite", "déjà inscrit"),
        ("autre saison", "même saison"),
        ("abandon", "abandonnée"),
    ],
)
def test_attente_refusee(full, case, message):
    membership, target = full["chloe"], full["flute"]
    if case == "déjà en attente":
        courses.add_to_waitlist(membership, target)
    elif case == "déjà inscrite":
        membership = full["alice"]
    elif case == "autre saison":
        target = full["flute_next"]
    elif case == "abandon":
        membership.status = Membership.WITHDRAWN
    with pytest.raises(CourseError, match=message):
        courses.add_to_waitlist(membership, target)


def test_retirer_puis_revenir_en_fin_de_liste(full):
    from app.models import WaitlistEntry

    chloe = courses.add_to_waitlist(full["chloe"], full["flute"])
    zoe = courses.add_to_waitlist(adherent(home("Autre"), full["season"], "Zoé"), full["flute"])
    courses.remove_from_waitlist(chloe)
    assert chloe.status == WaitlistEntry.REMOVED and courses.waitlist(full["flute"]) == [zoe]
    with pytest.raises(CourseError, match="plus en attente"):
        courses.remove_from_waitlist(chloe)
    again = courses.add_to_waitlist(full["chloe"], full["flute"])
    assert again is chloe and courses.waitlist(full["flute"]) == [zoe, chloe]


def test_place_liberee_previent_le_secretariat_une_fois(full):
    courses.add_to_waitlist(full["chloe"], full["flute"])
    courses.withdraw(full["e_basile"], NOV)
    (note,) = notifications(full["manager"])
    assert (
        "Une place s'est libérée en Flûte (2026-2027) : 1 personne(s) en attente, à commencer par Chloé Petit"
        in note.message
    )
    assert notifications(make_account(Role.ADHERENT, email="x@asso.test")) == []
    # Rien n'est inscrit d'office, et le même effectif ne prévient pas deux fois le même jour.
    assert courses.waitlist(full["flute"])[0].waiting
    assert courses.announce_free_place(full["flute"]) is False


def test_pas_de_notification_sans_attente_ou_si_toujours_complet(full):
    full["flute"].marked_full = True
    courses.add_to_waitlist(full["chloe"], full["flute"])
    courses.withdraw(full["e_basile"], NOV)
    assert notifications(full["manager"]) == []
    full["flute"].marked_full = False
    full["flute"].capacity = 5
    db.session.flush()
    assert courses.announce_free_place(full["flute"]) is True


def test_inscrire_depuis_l_attente(full):
    from app.models import WaitlistEntry

    full["flute"].capacity = 3
    entry = courses.add_to_waitlist(full["chloe"], full["flute"])
    enrollment, warnings = courses.enroll_from_waitlist(entry, NOV)
    assert (enrollment.enrolled_on, warnings, entry.status) == (NOV, [], WaitlistEntry.ENROLLED)
    assert entry.closed_at is not None and courses.waitlist(full["flute"]) == []
    with pytest.raises(CourseError, match="plus en attente"):
        courses.enroll_from_waitlist(entry, NOV)


def test_inscription_directe_ferme_l_attente(full):
    from app.models import WaitlistEntry

    entry = courses.add_to_waitlist(full["chloe"], full["flute"])
    courses.enroll(full["chloe"], full["flute"], NOV)
    assert entry.status == WaitlistEntry.ENROLLED


def test_cours_avec_attente_non_supprimable(manager, full):
    courses.add_to_waitlist(full["chloe"], full["piano"])
    db.session.commit()
    html = page(manager.post(f"/catalogue/cours/{full['piano'].id}/supprimer", follow_redirects=True))
    assert "liste d&#39;attente" in html and db.session.get(Course, full["piano"].id) is not None


def test_ecrans_de_la_liste_d_attente(manager, full):
    flute = full["flute"]
    html = page(manager.get(f"/catalogue/cours/{flute.id}"))
    assert "Personne en attente." in html and "PETIT Chloé" in html  # proposée à l'ajout
    manager.post(f"/catalogue/cours/{flute.id}/attente", data={"membership_id": full["chloe"].id})
    other = adherent(home("Autre"), full["season"], "Zoé")
    db.session.commit()
    manager.post(f"/catalogue/cours/{flute.id}/attente", data={"membership_id": other.id})
    html = page(manager.get(f"/catalogue/cours/{flute.id}"))
    assert 'Liste d\'attente <small class="muted">(2)</small>' in html
    assert html.index("PETIT Chloé") < html.index("MARTIN Zoé")
    second = courses.waitlist(db.session.get(Course, flute.id))[1]
    manager.post(f"/catalogue/attente/{second.id}/monter")
    html = page(manager.get(f"/catalogue/cours/{flute.id}"))
    assert html.index("MARTIN Zoé") < html.index("PETIT Chloé")
    response = manager.post(f"/catalogue/attente/{second.id}/inscrire", data={"on": "2026-11-02"})
    assert response.location.endswith(f"/catalogue/cours/{flute.id}")
    assert any(e.course_id == flute.id and e.active for e in db.session.get(Membership, other.id).enrollments)
    first = courses.waitlist(db.session.get(Course, flute.id))[0]
    manager.post(f"/catalogue/attente/{first.id}/retirer")
    assert courses.waitlist(db.session.get(Course, flute.id)) == []
    html = page(manager.post(f"/catalogue/cours/{flute.id}/attente", data={}, follow_redirects=True))
    assert "Choisissez l&#39;élève" in html
    html = page(manager.post(f"/catalogue/attente/{first.id}/retirer", follow_redirects=True))
    assert "plus en attente" in html


def test_capacite_relevee_previent(manager, full):
    courses.add_to_waitlist(full["chloe"], full["flute"])
    db.session.commit()
    flute = full["flute"]
    manager.post(f"/catalogue/cours/{flute.id}/modifier", data={"discipline_id": flute.discipline_id, "capacity": "3"})
    (note,) = notifications(full["manager"])
    assert note.link.endswith(f"/catalogue/cours/{flute.id}#attente")


def test_attente_choisie_dans_le_dossier(full):
    """La famille coche la liste d'attente d'un cours complet ; la validation crée l'entrée, liée au dossier."""
    from datetime import datetime

    from app.models import Campaign, Dossier
    from app.services import registration

    s = full["season"]
    campaign = Campaign(season=s, title="Inscriptions", opens_at=datetime(2026, 6, 1), closes_at=datetime(2026, 12, 1))
    db.session.add(campaign)
    dossier = registration.dossier_for(campaign, full["seule"], None)
    student = next(st for st in dossier.data["students"] if st.get("person_id") == full["chloe"].person_id)
    student.update({"enrolled": True, "courses": [full["piano"].id], "waitlist": [full["flute"].id]})
    dossier.data = {**dossier.data}
    dossier.status = Dossier.SUBMITTED
    db.session.flush()
    registration.validate(dossier, None, now=datetime(2026, 10, 5, 10))
    (entry,) = courses.waitlist(full["flute"])
    assert entry.membership == full["chloe"] and entry.dossier == dossier


def test_formulaire_du_dossier_et_attente(client, full):
    """Écran de la famille : la case « liste d'attente » n'est acceptée que pour un cours complet."""
    from datetime import datetime, timedelta

    from app.models import Campaign

    s = full["season"]
    campaign = Campaign(
        season=s,
        title="Inscriptions",
        opens_at=datetime.now() - timedelta(days=1),
        closes_at=datetime.now() + timedelta(days=10),
    )
    db.session.add(campaign)
    account = make_account(Role.ADHERENT, email="chloe@asso.test")
    full["chloe"].person.account = account
    db.session.commit()
    login(client, account)
    from app.services import registration

    dossier = registration.dossier_for(campaign, full["seule"], account)
    db.session.commit()
    key = dossier.data["students"][0]["key"]
    url = f"/inscription/dossier/{dossier.uuid}/eleves/{key}"
    html = page(client.get(url))
    assert "M'inscrire sur la liste d'attente de ce cours" in html  # texte du gabarit, non échappé
    data = {"enrolled": "y", "category": "Adulte", "courses": [full["piano"].id], "waitlist": [full["piano"].id]}
    assert "ne concerne que les cours complets" in page(client.post(url, data=data))
    data["waitlist"] = [full["flute"].id]
    assert client.post(url, data=data).status_code == 302
    db.session.expire_all()
    from app.models import Dossier

    saved = db.session.get(Dossier, dossier.id).data["students"][0]
    assert saved["waitlist"] == [full["flute"].id]


def test_attente_visible_dans_mon_espace(client, full):
    courses.add_to_waitlist(full["chloe"], full["flute"])
    account = make_account(Role.ADHERENT, email="chloe@asso.test")
    full["chloe"].person.account = account
    db.session.commit()
    login(client, account)
    html = page(client.get("/ma-famille"))
    assert 'Flûte <span class="badge">liste d\'attente</span>' in html


def test_fusion_garde_attente_et_dates(full):
    from app.services import directory as directory_service

    double = person("Chloé", "Petit")
    dup = Membership(person=double, season=full["season"])
    db.session.add(dup)
    db.session.flush()
    enrollment, _ = courses.enroll(dup, full["piano"], NOV)
    courses.withdraw(enrollment, DEC)
    courses.add_to_waitlist(dup, full["flute"])
    db.session.flush()
    directory_service.merge_persons(full["chloe"].person, double)
    db.session.flush()
    kept = full["chloe"]
    (moved,) = kept.enrollments
    assert (moved.enrolled_on, moved.withdrawn_on) == (NOV, DEC)
    assert [w.course for w in kept.waitlist] == [full["flute"]]
