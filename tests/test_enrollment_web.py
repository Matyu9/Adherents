"""Inscriptions dans l'appli : le dossier d'une famille, le formulaire public, et la revue par le secrétariat."""

import re
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import Activity, BankAccount, Campaign, Consent, Dossier, Enrollment, Household, Notification, Role
from app.services import bank, registration

from .conftest import login, make_account
from .test_links import smtp  # noqa: F401
from .test_registration import IBAN, world  # noqa: F401


@pytest.fixture
def opened(world):
    """La campagne de `world`, ouverte maintenant."""
    world["campaign"].opens_at = datetime.now() - timedelta(days=1)
    world["campaign"].closes_at = datetime.now() + timedelta(days=30)
    db.session.commit()
    return world


@pytest.fixture
def parent(client, opened, accounts):
    """Anne, responsable du foyer Martin, connectée avec son compte."""
    account = accounts[Role.ADHERENT]
    opened["anne"].account = account
    db.session.commit()
    login(client, account)
    return account


def start(client, world):
    client.post(f"/inscription/{world['campaign'].id}/foyer/{world['household'].id}")
    return db.session.scalar(sa.select(Dossier))


def fill_and_send(client, world, dossier):
    rules = world["campaign"].consents[1]
    client.post(f"/inscription/dossier/{dossier.uuid}/consentements", data={f"consent-{rules.id}": "y"})
    return client.post(f"/inscription/dossier/{dossier.uuid}/envoyer", data={"certify": "y", "comment": "Merci"})


# ---------------------------------------------------------------------------
# Côté famille
# ---------------------------------------------------------------------------


def test_a_parent_fills_and_sends_the_dossier(client, parent, opened):
    page = client.get("/inscription").get_data(as_text=True)
    assert "Réinscriptions 2027-2028" in page and "Commencer le dossier" in page
    dossier = start(client, opened)
    page = client.get(f"/inscription/dossier/{dossier.uuid}").get_data(as_text=True)
    assert "Zoé Martin" in page and "Flûte · Cycle 1" in page and "Règlement intérieur" in page
    assert "Envoyer le dossier" not in page  # le règlement n'est pas encore accepté
    response = fill_and_send(client, opened, dossier)
    assert response.status_code == 302
    db.session.refresh(dossier)
    assert dossier.status == Dossier.SUBMITTED and dossier.data["comment"] == "Merci"
    assert dossier.data["answered_by"] == parent.full_name and dossier.account == parent
    assert db.session.scalar(sa.select(Activity).filter_by(action="dossier.submit")) is not None
    # Envoyé : plus rien ne se modifie.
    response = client.post(f"/inscription/dossier/{dossier.uuid}/foyer", data={"name": "Autre"})
    assert response.status_code == 302 and dossier.data["household"]["name"] == "Famille Martin"


def test_household_form(client, parent, opened):
    dossier = start(client, opened)
    form = client.get(f"/inscription/dossier/{dossier.uuid}/foyer").get_data(as_text=True)
    assert 'value="06 12 34 56 78"' in form and 'value="anne@asso.test"' in form
    data = {
        "name": "Famille Martin",
        "address_street": "2 rue Neuve",
        "postal_code": "76000",
        "city": "Rouen",
        "country": "fr",
        "phone": "02 35 00 00 00",
        "g1_first_name": "Anne",
        "g1_last_name": "Martin",
        "g1_email": "Anne@Asso.test",
        "g1_phone": "06 12 34 56 78",
        "g1_relationship": "mère",
        "g2_first_name": "Marc",
        "g2_last_name": "Martin",
        "g2_phone": "12",
    }
    page = client.post(f"/inscription/dossier/{dossier.uuid}/foyer", data=data).get_data(as_text=True)
    assert "Numéro invalide" in page
    data["g2_phone"] = "07 11 22 33 44"
    client.post(f"/inscription/dossier/{dossier.uuid}/foyer", data=data)
    db.session.refresh(dossier)
    assert dossier.data["household"]["address_street"] == "2 rue Neuve" and dossier.data["household"]["country"] == "FR"
    anne, marc = dossier.data["guardians"]
    assert anne["person_id"] == opened["anne"].id and anne["email"] == "anne@asso.test"
    assert marc["person_id"] is None and marc["phone"] == "+33711223344"
    assert opened["household"].address_street == "1 rue Haute"  # l'annuaire attend la validation


def test_student_form_with_a_full_course(client, parent, opened):
    other = make_account(email="autre@asso.test")
    assert other  # un autre élève occupe déjà la seule place du cours de flûte
    from app.models import Membership

    from .test_annuaire import person

    leo = person("Léo", "Autre")
    membership = Membership(person=leo, season=opened["new"])
    db.session.add(membership)
    db.session.flush()
    membership.enrollments.append(Enrollment(course=opened["flute_c2"]))
    opened["flute_c2"].capacity = 1
    db.session.commit()
    dossier = start(client, opened)
    key = dossier.data["students"][0]["key"]
    image = opened["campaign"].consents[0]
    page = client.get(f"/inscription/dossier/{dossier.uuid}/eleves/{key}").get_data(as_text=True)
    assert f'value="{opened["flute_c2"].id}" disabled' in page and "complet" in page
    base = {"enrolled": "y", "first_name": "Zoé", "last_name": "Martin", "birth_date": "2015-05-06"}
    page = client.post(
        f"/inscription/dossier/{dossier.uuid}/eleves/{key}", data={**base, "courses": [opened["flute_c2"].id]}
    ).get_data(as_text=True)
    assert "complet" in page
    client.post(
        f"/inscription/dossier/{dossier.uuid}/eleves/{key}",
        data={
            **base,
            "courses": [opened["flute_new"].id, opened["fm_new"].id],
            f"consent-{image.id}": "y",
            "school_class": "6e",
        },
    )
    db.session.refresh(dossier)
    student = dossier.data["students"][0]
    assert student["courses"] == [opened["flute_new"].id, opened["fm_new"].id] and student["school_class"] == "6e"
    assert student["consents"] == {str(image.id): True}


def test_add_and_remove_students(client, parent, opened):
    dossier = start(client, opened)
    response = client.post(f"/inscription/dossier/{dossier.uuid}/eleves/nouveau", data={"qui": ""})
    new_key = response.location.rsplit("/", 1)[1]
    client.post(f"/inscription/dossier/{dossier.uuid}/eleves/nouveau", data={"qui": "0"})  # Anne prend des cours
    db.session.refresh(dossier)
    assert [s.get("guardian") for s in dossier.data["students"]] == [None, None, 0]
    assert dossier.data["students"][1]["last_name"] == "Martin"
    page = client.post(
        f"/inscription/dossier/{dossier.uuid}/eleves/nouveau", data={"qui": "0"}, follow_redirects=True
    ).get_data(as_text=True)
    assert "déjà dans la liste" in page
    zoe_key = dossier.data["students"][0]["key"]
    page = client.post(f"/inscription/dossier/{dossier.uuid}/eleves/{zoe_key}/retirer", follow_redirects=True).get_data(
        as_text=True
    )
    assert "décochez plutôt" in page
    client.post(f"/inscription/dossier/{dossier.uuid}/eleves/{new_key}/retirer")
    db.session.refresh(dossier)
    assert len(dossier.data["students"]) == 2


def test_bank_details_are_encrypted_at_once(client, parent, opened):
    dossier = start(client, opened)
    page = client.post(
        f"/inscription/dossier/{dossier.uuid}/banque",
        data={"holder_name": "Anne Martin", "iban": "FR76 3000 6000 0112 3456 7890 180"},
    )
    assert "clé de contrôle" in page.get_data(as_text=True)
    client.post(
        f"/inscription/dossier/{dossier.uuid}/banque",
        data={"holder_name": "Anne Martin", "iban": "fr76 3000 6000 0112 3456 7890 189"},
    )
    db.session.refresh(dossier)
    assert dossier.iban_last4 == "0189" and bank.reveal_dossier_iban(dossier) == IBAN
    page = client.get(f"/inscription/dossier/{dossier.uuid}").get_data(as_text=True)
    assert "•••• 0189" in page and "3000 6000" not in page and IBAN not in page
    assert IBAN not in str(db.session.scalars(sa.select(Activity.changes)).all())
    client.post(f"/inscription/dossier/{dossier.uuid}/banque/retirer")
    db.session.refresh(dossier)
    assert dossier.iban_ciphertext is None and dossier.data["bank"] == {}


def test_another_parent_cannot_open_the_dossier(client, opened, accounts):
    dossier = registration.dossier_for(opened["campaign"], opened["household"], None)
    db.session.commit()
    login(client, accounts[Role.ADHERENT])  # pas rattaché au foyer
    assert client.get(f"/inscription/dossier/{dossier.uuid}").status_code == 404
    assert client.post(f"/inscription/dossier/{dossier.uuid}/foyer", data={}).status_code == 404
    assert client.post(f"/inscription/{opened['campaign'].id}/foyer/{opened['household'].id}").status_code == 404
    assert db.session.scalar(sa.select(sa.func.count(Dossier.id))) == 1


def test_anonymous_visitor_is_sent_to_login(client, opened):
    dossier = registration.dossier_for(opened["campaign"], opened["household"], None)
    db.session.commit()
    response = client.get(f"/inscription/dossier/{dossier.uuid}")
    assert response.status_code == 302 and "/connexion" in response.location


def test_closed_campaign_is_read_only(client, parent, opened):
    dossier = start(client, opened)
    opened["campaign"].closes_at = datetime.now() - timedelta(minutes=1)
    db.session.commit()
    page = client.get(f"/inscription/dossier/{dossier.uuid}").get_data(as_text=True)
    assert "La campagne est fermée" in page and "Modifier" not in page
    page = client.post(
        f"/inscription/dossier/{dossier.uuid}/envoyer", data={"certify": "y"}, follow_redirects=True
    ).get_data(as_text=True)
    assert "La campagne est fermée." in page and dossier.status == Dossier.DRAFT


def test_sending_needs_the_certification(client, parent, opened):
    dossier = start(client, opened)
    rules = opened["campaign"].consents[1]
    client.post(f"/inscription/dossier/{dossier.uuid}/consentements", data={f"consent-{rules.id}": "y"})
    page = client.post(f"/inscription/dossier/{dossier.uuid}/envoyer", data={}, follow_redirects=True).get_data(
        as_text=True
    )
    assert "Cochez cette case" in page and dossier.status == Dossier.DRAFT


# ---------------------------------------------------------------------------
# Nouvelles familles
# ---------------------------------------------------------------------------


def test_a_new_family_receives_a_link(client, opened, smtp):  # noqa: F811
    url = f"/inscription/nouvelle/{opened['campaign'].id}"
    assert "Recevoir le lien" in client.get(url).get_data(as_text=True)
    page = client.post(url, data={"email": "Claire@Famille.test"}).get_data(as_text=True)
    assert "Vérifiez vos emails" in page
    ((to, subject, text),) = smtp
    assert to == "claire@famille.test" and "Réinscriptions 2027-2028" in subject
    link = re.search(r"http://\S*(/inscription/acces/[\w-]+)", text).group(1)
    response = client.get(link)
    dossier = db.session.scalar(sa.select(Dossier))
    assert response.location.endswith(f"/inscription/dossier/{dossier.uuid}") and dossier.email_verified_at
    page = client.get(f"/inscription/dossier/{dossier.uuid}").get_data(as_text=True)
    assert "Le foyer" in page and "guest-wide" in page
    form = client.get(f"/inscription/dossier/{dossier.uuid}/foyer").get_data(as_text=True)
    assert 'value="claire@famille.test"' in form
    # Une seconde demande avec la même adresse renvoie un lien vers le même dossier.
    client.post(url, data={"email": "claire@famille.test"})
    assert len(smtp) == 2 and db.session.scalar(sa.select(sa.func.count(Dossier.id))) == 1
    # L'ancien lien ne sert plus, le nouveau oui.
    other = client.application.test_client()
    assert other.get(link).location.endswith("/connexion")
    assert other.get(re.search(r"(/inscription/acces/[\w-]+)", smtp[1][2]).group(1)).status_code == 302


def test_new_family_without_email_sending(client, opened):
    page = client.post(f"/inscription/nouvelle/{opened['campaign'].id}", data={"email": "x@famille.test"})
    assert "indisponible" in page.get_data(as_text=True)
    assert db.session.scalar(sa.select(sa.func.count(Dossier.id))) == 0


def test_new_family_form_only_when_allowed(client, opened):
    opened["campaign"].allow_new = False
    db.session.commit()
    assert client.get(f"/inscription/nouvelle/{opened['campaign'].id}").status_code == 404


def test_new_family_requests_are_limited(client, opened, smtp):  # noqa: F811
    url = f"/inscription/nouvelle/{opened['campaign'].id}"
    for _ in range(5):
        client.post(url, data={"email": "x@famille.test"})
    assert client.post(url, data={"email": "x@famille.test"}).status_code == 429
    assert len(smtp) == 5


def test_a_bad_access_link(client, opened):
    page = client.get("/inscription/acces/faux", follow_redirects=True).get_data(as_text=True)
    assert "n&#39;est plus valable" in page


# ---------------------------------------------------------------------------
# Côté secrétariat
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(app, opened, accounts):
    gestion = app.test_client()
    login(gestion, accounts[Role.GESTIONNAIRE])
    return gestion


def test_campaign_screens(manager, opened):
    response = manager.post(
        "/campagnes/nouvelle",
        data={
            "season_id": opened["new"].id,
            "title": "Inscriptions d'été",
            "opens_at": "2027-06-01T09:00",
            "closes_at": "2027-05-01T09:00",
        },
    )
    assert "La clôture doit suivre" in response.get_data(as_text=True)
    response = manager.post(
        "/campagnes/nouvelle",
        data={
            "season_id": opened["new"].id,
            "title": "Inscriptions d'été",
            "opens_at": "2027-06-01T09:00",
            "closes_at": "2027-07-01T18:00",
            "allow_new": "y",
        },
    )
    campaign = db.session.scalar(sa.select(Campaign).filter_by(title="Inscriptions d'été"))
    assert response.location.endswith(f"/campagnes/{campaign.id}") and campaign.allow_new
    manager.post(
        f"/campagnes/{campaign.id}/consentements",
        data={"label": "Droit à l'image", "text": "J'autorise…", "scope": "student", "required": "y"},
    )
    assert [c.label for c in campaign.consents] == ["Droit à l'image"] and campaign.consents[0].required
    page = manager.get(f"/campagnes/{campaign.id}").get_data(as_text=True)
    assert "Droit à l&#39;image" in page and f"/inscription/nouvelle/{campaign.id}" in page
    manager.post(f"/campagnes/consentements/{campaign.consents[0].id}/supprimer")
    assert campaign.consents == []
    manager.post(f"/campagnes/{campaign.id}/supprimer")
    assert db.session.get(Campaign, campaign.id) is None


def test_a_campaign_with_dossiers_stays(manager, opened):
    registration.dossier_for(opened["campaign"], opened["household"], None)
    db.session.commit()
    page = manager.post(f"/campagnes/{opened['campaign'].id}/supprimer", follow_redirects=True).get_data(as_text=True)
    assert "ne se supprime plus" in page
    page = manager.post(
        f"/campagnes/{opened['campaign'].id}/modifier",
        data={
            "season_id": opened["old"].id,
            "title": "x",
            "opens_at": "2027-06-01T09:00",
            "closes_at": "2027-07-01T09:00",
        },
    )
    assert "la saison ne change plus" in page.get_data(as_text=True)


def test_review_return_then_validate(client, parent, manager, opened):
    dossier = start(client, opened)
    fill_and_send(client, opened, dossier)
    listing = manager.get(f"/campagnes/{opened['campaign'].id}/dossiers?statut=submitted").get_data(as_text=True)
    assert "Famille Martin" in listing and "Zoé Martin" in listing
    page = manager.get(f"/campagnes/dossiers/{dossier.id}").get_data(as_text=True)
    assert "Ce que la validation changera" in page and "Flûte · Cycle 1" in page and "Merci" in page

    manager.post(f"/campagnes/dossiers/{dossier.id}/renvoyer", data={"note": "Ajoutez la classe de Zoé."})
    db.session.refresh(dossier)
    assert dossier.status == Dossier.TO_FIX
    notice = db.session.scalar(sa.select(Notification).filter_by(account_id=parent.id))
    assert "Ajoutez la classe de Zoé." in notice.message and notice.link == f"/inscription/dossier/{dossier.uuid}"
    assert "À corriger : Ajoutez la classe" in client.get(f"/inscription/dossier/{dossier.uuid}").get_data(as_text=True)
    client.post(f"/inscription/dossier/{dossier.uuid}/envoyer", data={"certify": "y"})
    db.session.refresh(dossier)
    assert dossier.status == Dossier.SUBMITTED

    manager.post(f"/campagnes/dossiers/{dossier.id}/valider")
    db.session.refresh(dossier)
    assert dossier.status == Dossier.VALIDATED
    assert {e.course for e in db.session.scalars(sa.select(Enrollment).filter_by(dossier_id=dossier.id))} == {
        opened["flute_new"]
    }
    assert db.session.scalar(sa.select(Consent).filter_by(label="Règlement intérieur")).granted
    assert db.session.scalar(sa.select(sa.func.count(Notification.id)).filter_by(account_id=parent.id)) == 2
    page = manager.get(f"/campagnes/dossiers/{dossier.id}").get_data(as_text=True)
    assert "Ce que la validation a changé" in page and "Noter comme reporté" in page
    # Le catalogue compte maintenant Zoé dans le cours de flûte, complet (effectif maximum : 1).
    catalogue = manager.get(f"/catalogue?saison={opened['new'].id}").get_data(as_text=True)
    assert "1 / 1" in catalogue and "complet" in catalogue


def test_report_files(manager, opened, app, accounts):
    dossier = registration.dossier_for(opened["campaign"], opened["household"], None)
    from .test_registration import answer_consents

    answer_consents(dossier)
    data = dict(dossier.data, bank={"holder_name": "Anne Martin"})
    registration.save(dossier, data)
    bank.set_dossier_iban(dossier, IBAN)
    registration.submit(dossier)
    registration.validate(dossier, accounts[Role.GESTIONNAIRE])
    db.session.commit()

    page = manager.get(f"/campagnes/{opened['campaign'].id}/report").get_data(as_text=True)
    assert "Famille Martin" in page and "report-iban.csv" not in page  # le gestionnaire n'a pas bank.view
    csv = manager.get(f"/campagnes/{opened['campaign'].id}/report.csv").get_data(as_text=True)
    assert csv.startswith("﻿dossier;foyer;fiche;champ") and "Flûte · Cycle 1" in csv
    assert IBAN not in csv and "•••• 0189" in csv
    assert manager.get(f"/campagnes/{opened['campaign'].id}/report-iban.csv").status_code == 403

    treasurer = app.test_client()
    login(treasurer, accounts[Role.ADMIN])
    response = treasurer.get(f"/campagnes/{opened['campaign'].id}/report-iban.csv")
    assert response.status_code == 302 and "/annuaire/mode-securise" in response.location
    from .conftest import totp_code

    accounts[Role.ADMIN].totp_last_counter = None
    db.session.commit()
    treasurer.post("/annuaire/mode-securise", data={"code": totp_code()})
    csv = treasurer.get(f"/campagnes/{opened['campaign'].id}/report-iban.csv").get_data(as_text=True)
    assert "FR76 3000 6000 0112 3456 7890 189" in csv
    assert db.session.scalar(sa.select(Activity).filter_by(action="dossier.export_iban")) is not None

    manager.post(f"/campagnes/{opened['campaign'].id}/report/fait", data={"dossier": [dossier.id]})
    db.session.refresh(dossier)
    assert dossier.status == Dossier.TRANSFERRED and dossier.transferred_at


def test_a_new_family_dossier_is_validated_into_the_directory(manager, opened, accounts):
    dossier, _ = registration.new_family_dossier(opened["campaign"], "claire@famille.test")
    data = registration.blank_data()
    data["household"] = {
        "name": "Famille Petit",
        "address_street": "4 rue Basse",
        "postal_code": "76100",
        "city": "Rouen",
        "country": "FR",
    }
    data["guardians"] = [
        {
            "person_id": None,
            "first_name": "Claire",
            "last_name": "Petit",
            "email": "claire@famille.test",
            "phone": "+33611111111",
        }
    ]
    data["students"] = [
        {
            "key": "a",
            "person_id": None,
            "guardian": 0,
            "enrolled": True,
            "courses": [opened["fm_new"].id],
            "consents": {},
        }
    ]
    data["consents"] = {str(opened["campaign"].consents[1].id): True}
    registration.save(dossier, data)
    registration.submit(dossier)
    db.session.commit()
    page = manager.get(f"/campagnes/dossiers/{dossier.id}").get_data(as_text=True)
    assert "Nouvelle famille (claire@famille.test" in page and "nouvelle" in page
    manager.post(f"/campagnes/dossiers/{dossier.id}/valider")
    household = db.session.scalar(sa.select(Household).filter_by(name="Famille Petit"))
    assert household is not None and dossier.household == household
    assert not household.bank_accounts and db.session.scalar(sa.select(sa.func.count(BankAccount.id))) == 0


def test_members_do_not_see_the_campaigns(client, parent):
    assert client.get("/campagnes").status_code == 403


# ---------------------------------------------------------------------------
# Relances et professeurs
# ---------------------------------------------------------------------------


def test_weekly_reminders(opened, accounts):
    anne = accounts[Role.ADHERENT]
    opened["anne"].account = anne
    db.session.commit()
    now = datetime.now()
    assert registration.remind(now) == 1  # Zoé était inscrite l'an dernier, pas encore de dossier
    db.session.commit()
    notice = db.session.scalar(sa.select(Notification).filter_by(account_id=anne.id))
    assert "sont ouvertes jusqu'au" in notice.message and notice.link == "/inscription"
    assert registration.remind(now) == 0  # une fois par semaine
    dossier = registration.dossier_for(opened["campaign"], opened["household"], anne)
    db.session.commit()
    assert registration.remind(now + timedelta(days=7)) == 1
    db.session.commit()
    assert dossier.reminded_at is not None
    assert "pas encore envoyé" in db.session.scalars(sa.select(Notification.message)).all()[-1]
    dossier.status = Dossier.SUBMITTED
    db.session.commit()
    assert registration.remind(now + timedelta(days=14)) == 0  # envoyé : plus de relance
    from app.services import tasks

    assert "foyer(s) relancé(s)" in tasks.run()


def test_no_reminder_without_an_account_or_a_campaign(opened):
    assert registration.remind() == 0  # Anne n'a pas de compte
    opened["campaign"].closes_at = datetime.now() - timedelta(days=1)
    assert registration.remind() == 0


def test_a_teacher_sees_his_students_and_their_guardians(client, opened, accounts, admin):
    zoe_membership = next(m for m in opened["zoe"].memberships)
    zoe_membership.enrollments[0].status = "active"
    from app.models import Membership

    new = Membership(person=opened["zoe"], season=opened["new"])
    db.session.add(new)
    db.session.flush()
    new.enrollments.append(Enrollment(course=opened["flute_new"]))
    teacher_account = make_account(email="elodie@asso.test")
    opened["prof"].account = teacher_account
    db.session.commit()
    other = client.application.test_client()
    login(other, teacher_account)
    page = other.get(f"/inscription/mes-eleves?saison={opened['new'].id}").get_data(as_text=True)
    assert "MARTIN Zoé" in page and "Anne Martin" in page and "06 12 34 56 78" in page and "anne@asso.test" in page
    assert "Mes élèves" in other.get("/").get_data(as_text=True)
    page = other.get(f"/inscription/mes-eleves?saison={opened['old'].id}").get_data(as_text=True)
    assert "MARTIN Zoé" in page  # l'an dernier aussi
    assert other.get("/annuaire").status_code == 403  # et rien d'autre de l'annuaire
    # Un adhérent qui n'est pas professeur n'a pas cette page.
    assert client.get("/inscription/mes-eleves").status_code == 404
