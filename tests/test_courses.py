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
