"""Espace adhérent (phase 7) : chacun ne voit que son foyer ; matériel prêté lu dans l'Inventaire ; personnes de
l'annuaire transmises aux applis de l'association."""

import base64
import json
import uuid
from datetime import date, datetime

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import AccessGroup, BankAccount, Consent, Enrollment, Membership, Role
from app.services import bank, directory, inventory

from .conftest import login, make_account
from .test_annuaire import home, person
from .test_oidc import SECRET_OF, make_client
from .test_registration import IBAN, world  # noqa: F401


@pytest.fixture
def family(world, accounts):
    """Le foyer Martin de `world` (Zoé inscrite en flûte l'an dernier), Anne connectée ; et un autre foyer."""
    anne = accounts[Role.ADHERENT]
    world["anne"].account = anne
    account = BankAccount(household_id=world["household"].id, holder_name="Anne Martin")
    bank.set_iban(account, IBAN)
    db.session.add(account)
    db.session.add(
        Consent(
            person=world["zoe"],
            season=world["old"],
            label="Droit à l'image",
            text="x",
            granted=True,
            given_at=datetime(2026, 9, 1),
        )
    )
    other = home("Famille Durand", address_street="9 rue Secrète", postal_code="76000", city="Rouen")
    paul, lea = person("Paul", "Durand", email="paul@durand.test"), person("Léa", "Durand")
    directory.add_member(other, paul, "guardian")
    directory.add_member(other, lea, "dependent")
    db.session.commit()
    return {**world, "account": anne, "other": other, "paul": paul, "lea": lea}


def test_a_parent_sees_his_household(client, family):
    login(client, family["account"])
    page = client.get("/ma-famille").get_data(as_text=True)
    assert "Famille Martin" in page and "1 rue Haute" in page and "Zoé Martin" in page
    assert "Flûte · Cycle 1" in page and "2026-2027" in page
    assert "•••• •••• 0189" in page and IBAN not in page and "3000 6000" not in page
    assert "Droit à l&#39;image" in page
    assert "Durand" not in page and "9 rue Secrète" not in page  # l'autre foyer
    assert "Ma famille" in client.get("/").get_data(as_text=True)


def test_one_parent_two_households(client, family):
    directory.add_member(family["other"], family["anne"], "guardian")  # famille recomposée
    db.session.commit()
    login(client, family["account"])
    page = client.get("/ma-famille").get_data(as_text=True)
    assert "Famille Martin" in page and "Famille Durand" in page and "Léa Durand" in page


def test_a_dependent_with_an_account_sees_only_his_courses(client, family):
    teen = make_account(email="zoe@asso.test", first_name="Zoé")
    family["zoe"].account = teen
    db.session.commit()
    login(client, teen)
    page = client.get("/ma-famille").get_data(as_text=True)
    assert "Mes cours" in page and "Flûte · Cycle 1" in page
    assert "0189" not in page and "1 rue Haute" not in page and "Anne" not in page


def test_an_account_without_a_record(client, accounts):
    login(client, accounts[Role.ADHERENT])
    page = client.get("/ma-famille").get_data(as_text=True)
    assert "pas encore relié à votre fiche" in page
    assert "Ma famille" not in client.get("/").get_data(as_text=True)


def test_anonymous_is_sent_to_login(client):
    response = client.get("/ma-famille")
    assert response.status_code == 302 and "/connexion" in response.location


# ---------------------------------------------------------------------------
# Matériel prêté, lu dans l'Inventaire
# ---------------------------------------------------------------------------


class FakeOpener:
    """Remplace l'appel HTTP : garde la requête envoyée et renvoie `answer` (ou lève `error`)."""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.requests = answer, error, []

    def __call__(self, *handlers):
        return self

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        body = json.dumps(self.answer).encode()

        class Response:
            def __enter__(self_):
                return self_

            def __exit__(self_, *exc):
                return False

            def read(self_, size=-1):
                return body

        return Response()


@pytest.fixture
def inventaire(app, monkeypatch):
    app.config.update(INVENTAIRE_API_URL="https://inventaire.test/api/v1", INVENTAIRE_API_TOKEN="ohn_jeton")

    def install(**kwargs):
        opener = FakeOpener(**kwargs)
        monkeypatch.setattr(inventory.urllib.request, "build_opener", opener)
        return opener

    return install


def test_instruments_of_the_children(client, family, inventaire):
    zoe = family["zoe"].uuid
    opener = inventaire(
        answer={
            "persons": {
                zoe: [
                    {
                        "id": 7,
                        "status": "en_cours",
                        "status_label": "En cours",
                        "start": "2026-09-05",
                        "end": None,
                        "items": [{"name": "Flûte <b>Yamaha</b>", "quantity": 1}],
                    }
                ]
            }
        }
    )
    login(client, family["account"])
    page = client.get("/ma-famille").get_data(as_text=True)
    assert "Flûte &lt;b&gt;Yamaha&lt;/b&gt;" in page and "en cours depuis le 2026-09-05" in page
    (request,) = opener.requests
    assert request.full_url.startswith("https://inventaire.test/api/v1/annuaire/prets?personne=")
    assert f"personne={zoe}" in request.full_url and "Durand" not in request.full_url
    assert family["lea"].uuid not in request.full_url  # jamais les enfants d'un autre foyer
    assert request.get_header("Authorization") == "Bearer ohn_jeton"


def test_inventaire_down(client, family, inventaire):
    import urllib.error

    inventaire(error=urllib.error.URLError("refusé"))
    login(client, family["account"])
    page = client.get("/ma-famille").get_data(as_text=True)
    assert "indisponible pour le moment" in page and "Zoé Martin" in page


@pytest.mark.parametrize("answer", [[], {"persons": []}, {"autre": 1}])
def test_unexpected_answers(app, inventaire, answer):
    inventaire(answer=answer)
    assert inventory.loans([str(uuid.uuid4())]) is None


def test_inventaire_needs_https_and_a_token(app):
    app.config.update(INVENTAIRE_API_URL="http://inventaire.test/api/v1", INVENTAIRE_API_TOKEN="ohn_x")
    assert not inventory.enabled() and inventory.loans(["x"]) is None
    app.config.update(INVENTAIRE_API_URL="http://localhost:8000/api/v1")
    assert inventory.enabled()
    app.config.update(INVENTAIRE_API_TOKEN="")
    assert not inventory.enabled()


def test_no_instrument_section_without_inventaire(client, family):
    login(client, family["account"])
    assert "Matériel prêté" not in client.get("/ma-famille").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Personnes de l'annuaire, pour l'Inventaire
# ---------------------------------------------------------------------------


def basic(client_app):
    raw = f"{client_app.client_id}:{SECRET_OF[client_app.client_id]}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


@pytest.fixture
def inventaire_app(family):
    members = AccessGroup(key="membres", name="Membres")
    family["account"].groups = [members]
    application = make_client(allowed=[members], name="Inventaire")
    application.directory_access = True
    family["lea"].archived_at = datetime.now()
    family["zoe"].birth_date = date(2015, 5, 6)
    db.session.commit()
    return application


def test_persons_for_a_first_party_app(client, family, inventaire_app):
    data = client.get("/oidc/personnes", headers=basic(inventaire_app)).json
    rows = {(p["first_name"], p["last_name"]): p for p in data["persons"]}
    assert ("Léa", "Durand") not in rows  # archivée
    assert rows[("Zoé", "Martin")] == {
        "uuid": family["zoe"].uuid,
        "first_name": "Zoé",
        "last_name": "Martin",
        "birth_year": 2015,
        "sub": None,
    }
    assert rows[("Anne", "Martin")]["sub"] == family["account"].sub
    assert rows[("Paul", "Durand")]["sub"] is None  # pas de compte
    assert "email" not in json.dumps(data) and "@" not in json.dumps(data)


def test_persons_refusals(client, family, inventaire_app):
    assert client.get("/oidc/personnes").status_code == 401
    assert (
        client.get(
            "/oidc/personnes",
            headers={
                "Authorization": "Basic " + base64.b64encode(f"{inventaire_app.client_id}:faux".encode()).decode()
            },
        ).status_code
        == 401
    )
    inventaire_app.first_party = False  # une appli tierce ne voit pas les enfants sans compte
    db.session.commit()
    assert client.get("/oidc/personnes", headers=basic(inventaire_app)).status_code == 403
    inventaire_app.first_party, inventaire_app.directory_access = True, False
    db.session.commit()
    assert client.get("/oidc/personnes", headers=basic(inventaire_app)).status_code == 403


def test_a_teacher_with_an_account_sees_both_spaces(client, family):
    """Un professeur qui est aussi parent : son espace et ses élèves."""
    from app.models import Teacher

    family["anne"].teacher = Teacher(status="CDI")
    db.session.commit()
    login(client, family["account"])
    home_page = client.get("/").get_data(as_text=True)
    assert "Ma famille" in home_page and "Mes élèves" in home_page


def test_enrollment_withdrawn_is_not_listed(client, family):
    membership = next(m for m in family["zoe"].memberships if m.season == family["old"])
    membership.enrollments[0].status = Enrollment.WITHDRAWN
    db.session.commit()
    login(client, family["account"])
    assert "Flûte · Cycle 1" not in client.get("/ma-famille").get_data(as_text=True)
    assert db.session.scalar(sa.select(sa.func.count(Membership.id))) == 1


# ---------------------------------------------------------------------------
# Coordonnées modifiées par la famille
# ---------------------------------------------------------------------------


def _allow(*fields):
    from app.services import options

    options.set_member_editable(fields)
    db.session.commit()


def test_contacts_are_read_only_by_default(client, family):
    login(client, family["account"])
    household = family["household"]
    assert "Modifier nos coordonnées" not in client.get("/ma-famille").get_data(as_text=True)
    assert client.get(f"/ma-famille/coordonnees/{household.id}").status_code == 404


def test_a_parent_updates_address_phones_and_child_email(client, family, accounts):
    from app.models import Activity, Notification

    _allow("adresse", "telephones", "email")
    login(client, family["account"])
    household, anne, zoe = family["household"], family["anne"], family["zoe"]
    assert "Modifier nos coordonnées" in client.get("/ma-famille").get_data(as_text=True)
    page = client.get(f"/ma-famille/coordonnees/{household.id}").get_data(as_text=True)
    assert f'name="p{anne.id}-email"' in page and f'name="p{zoe.id}-email"' in page
    response = client.post(
        f"/ma-famille/coordonnees/{household.id}",
        data={
            "address_street": "3 rue Neuve",
            "postal_code": "14000",
            "city": "Caen",
            "foyer-label-0": "Maison",
            "foyer-phone-0": "02 31 00 00 00",
            f"p{anne.id}-email": anne.email or "",
            f"p{anne.id}-phone-0": "06 12 34 56 78",
            f"p{zoe.id}-email": "zoe@famille.test",
        },
    )
    assert response.status_code == 302
    db.session.expire_all()
    household = db.session.get(type(household), household.id)
    assert household.city == "Caen" and [p.number for p in household.phones] == ["+33231000000"]
    assert db.session.get(type(zoe), zoe.id).email == "zoe@famille.test"
    assert [p.number for p in db.session.get(type(anne), anne.id).phones] == ["+33612345678"]
    entry = db.session.scalar(db.select(Activity).filter_by(action="member.contacts"))
    assert entry.household_id == household.id and "adresse" in entry.message
    admin_messages = [
        n.message for n in db.session.scalars(db.select(Notification).filter_by(account_id=accounts[Role.ADMIN].id))
    ]
    assert any("coordonnées du foyer" in m for m in admin_messages)


def test_only_allowed_fields_change(client, family):
    _allow("telephones")
    login(client, family["account"])
    household, zoe = family["household"], family["zoe"]
    city = household.city
    client.post(
        f"/ma-famille/coordonnees/{household.id}",
        data={"city": "Ailleurs", f"p{zoe.id}-email": "pirate@x.test", "foyer-phone-0": "0612345678"},
    )
    db.session.expire_all()
    assert db.session.get(type(household), household.id).city == city
    assert db.session.get(type(zoe), zoe.id).email != "pirate@x.test"


def test_contacts_of_another_household_are_out_of_reach(client, family):
    _allow("adresse")
    login(client, family["account"])
    assert client.get(f"/ma-famille/coordonnees/{family['other'].id}").status_code == 404
    assert client.post(f"/ma-famille/coordonnees/{family['other'].id}", data={"city": "X"}).status_code == 404
    assert db.session.get(type(family["other"]), family["other"].id).city == "Rouen"


def test_bad_phone_is_refused_and_nothing_changes(client, family):
    _allow("adresse", "telephones")
    login(client, family["account"])
    household = family["household"]
    city = household.city
    page = client.post(
        f"/ma-famille/coordonnees/{household.id}", data={"city": "Caen", "foyer-phone-0": "12"}
    ).get_data(as_text=True)
    assert "invalide" in page
    db.session.expire_all()
    assert db.session.get(type(household), household.id).city == city


def test_contacts_go_through_an_open_dossier(client, family):
    from app.models import Dossier

    _allow("adresse")
    login(client, family["account"])
    household = family["household"]
    from app.services import registration

    dossier = registration.dossier_for(family["campaign"], household, None)
    db.session.commit()
    assert dossier.status == Dossier.DRAFT
    page = client.get(f"/ma-famille/coordonnees/{household.id}").get_data(as_text=True)
    assert "dans le dossier" in page and 'name="city"' not in page
    response = client.post(f"/ma-famille/coordonnees/{household.id}", data={"city": "Caen"})
    assert "dossier d&#39;inscription est en cours" in response.get_data(as_text=True)
    assert db.session.get(type(household), household.id).city == "Rouen"


def test_settings_tab_for_member_fields(client, admin):
    from app.services import options

    page = client.get("/parametres/adherents").get_data(as_text=True)
    assert "Adresse postale" in page
    client.post("/parametres/adherents", data={"fields": ["email", "inconnu"]})
    assert options.member_editable() == {"email"}
    client.post("/parametres/adherents", data={})
    assert options.member_editable() == set() and options.get(options.MEMBER_EDITABLE) is None
