"""Conservation des données : rien tant que la durée n'est pas choisie, puis anonymisation décidée à l'écran."""
from datetime import date, datetime

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import Activity, BankAccount, Campaign, Consent, Dossier, ExternalRef, Membership, Person, Phone, Season
from app.services import bank, directory, registration, retention
from app.services.activity import log

from .test_annuaire import home, person

IBAN = "FR7630006000011234567890189"


def season(start):
    record = Season(label=f"{start}-{start + 1}", starts_on=date(start, 9, 1), ends_on=date(start + 1, 8, 31))
    db.session.add(record)
    db.session.flush()
    return record


@pytest.fixture
def history(app):
    """Six saisons. Les Anciens ne sont plus venus depuis 2019 ; les Récents sont là cette saison."""
    seasons = {year: season(year) for year in range(2019, 2025)}
    old_home = home("Famille Ancien", address_street="3 rue Vieille", postal_code="76000", city="Rouen")
    paul = person("Paul", "Ancien", email="paul@ancien.test", birth_date=date(1975, 4, 5), notes="à rappeler")
    lea = person("Léa", "Ancien", birth_date=date(2008, 2, 3))
    directory.add_member(old_home, paul, "guardian")
    directory.add_member(old_home, lea, "dependent")
    paul.phones.append(Phone(number="+33600000001"))
    lea.external_refs.append(ExternalRef(kind="student", value="42"))
    db.session.add(Membership(person=lea, season=seasons[2019]))
    account = BankAccount(household_id=old_home.id, holder_name="Paul Ancien")
    bank.set_iban(account, IBAN)
    db.session.add(account)
    db.session.add(Consent(person=lea, season=seasons[2019], label="Image", text="x", granted=True,
                           given_at=datetime(2019, 9, 1)))
    log("person.update", "Fiche de Léa Ancien modifiée", person=lea)
    recent_home = home("Famille Récent")
    marie, tom = person("Marie", "Récent"), person("Tom", "Récent")
    directory.add_member(recent_home, marie, "guardian")
    directory.add_member(recent_home, tom, "dependent")
    db.session.add_all([Membership(person=marie, season=seasons[2019]), Membership(person=tom, season=seasons[2024])])
    for record in (paul, lea, marie, tom):
        record.created_at = datetime(2019, 1, 1)
    db.session.commit()
    return {"seasons": seasons, "old_home": old_home, "paul": paul, "lea": lea, "marie": marie, "tom": tom}


def test_nothing_without_a_decided_duration(history):
    assert retention.seasons_kept() is None and retention.candidates() == [] and retention.old_dossiers() == []


def test_candidates(history):
    retention.set_seasons_kept(3)
    assert retention.oldest_kept_season().label == "2022-2023"
    # Marie n'a pas d'adhésion récente, mais son fils Tom si : elle reste.
    assert {p.full_name for p in retention.candidates()} == {"Paul Ancien", "Léa Ancien"}
    assert retention.candidates(count=6) == []  # 2019 fait encore partie des 6 dernières saisons
    assert retention.candidates(count=10) == []  # pas encore 10 saisons
    from .conftest import make_account
    history["paul"].account = make_account(email="paul@ancien.test")
    db.session.commit()
    assert {p.full_name for p in retention.candidates()} == {"Léa Ancien"}  # un compte : on garde


def test_anonymize(history):
    lea, paul, home_ = history["lea"], history["paul"], history["old_home"]
    retention.anonymize(lea)
    db.session.commit()
    assert (lea.first_name, lea.last_name, lea.birth_date, lea.external_refs, lea.consents) == (
        "Anonyme", f"n° {lea.id}", date(2008, 1, 1), [], [])
    assert lea.anonymized_at and lea.archived_at
    assert [m.season.label for m in lea.memberships] == ["2019-2020"]  # l'historique reste, sans nom
    assert db.session.scalar(sa.select(Activity.message).filter_by(person_id=lea.id)).startswith("(effacé")
    assert home_.name == "Famille Ancien"  # Paul reste : le foyer n'est pas encore vide
    retention.anonymize(paul)
    db.session.commit()
    assert paul.email is None and paul.notes is None and paul.phones == []
    assert home_.name.startswith("Foyer anonymisé") and home_.address_street is None and home_.archived_at
    assert db.session.scalar(sa.select(sa.func.count(BankAccount.id))) == 0
    assert retention.candidates(count=3) == []
    found = db.session.scalars(sa.select(Person).where(Person.last_name.ilike("%ancien%"))).all()
    assert found == []


def test_old_dossiers_are_purged(history):
    seasons = history["seasons"]
    campaign = Campaign(season=seasons[2019], title="Inscriptions 2019", opens_at=datetime(2019, 6, 1),
                        closes_at=datetime(2019, 7, 1))
    recent = Campaign(season=seasons[2024], title="Inscriptions 2024", opens_at=datetime(2024, 6, 1),
                      closes_at=datetime(2024, 7, 1))
    db.session.add_all([campaign, recent])
    db.session.flush()
    old = registration.dossier_for(campaign, history["old_home"], None)
    db.session.add(Dossier(campaign=recent, data={}))  # saison récente : gardé
    db.session.commit()
    retention.set_seasons_kept(3)
    assert retention.old_dossiers() == [old]
    assert retention.purge_dossiers() == 1
    db.session.commit()
    assert db.session.scalar(sa.select(sa.func.count(Dossier.id))) == 1


def test_retention_screen(client, admin, history):
    page = client.get("/annuaire/conservation").get_data(as_text=True)
    assert "pas encore décidée" in page
    client.post("/annuaire/conservation", data={"seasons": "3"})
    assert retention.seasons_kept() == 3
    page = client.get("/annuaire/conservation").get_data(as_text=True)
    assert "ANCIEN Paul" in page and "ANCIEN Léa" in page and "RÉCENT" not in page
    client.post("/annuaire/conservation/anonymiser", data={"personne": [history["lea"].id, history["tom"].id]})
    assert history["lea"].anonymized_at is not None
    assert history["tom"].anonymized_at is None  # pas candidat : ignoré
    assert db.session.scalar(sa.select(Activity).filter_by(action="retention.anonymize")).message == \
        "1 fiche(s) anonymisée(s)"
    client.post("/annuaire/conservation", data={"seasons": ""})
    assert retention.seasons_kept() is None


def test_managers_cannot_anonymize(client, accounts, history):
    from app.models import Role

    from .conftest import login
    login(client, accounts[Role.GESTIONNAIRE])
    assert client.get("/annuaire/conservation").status_code == 403
