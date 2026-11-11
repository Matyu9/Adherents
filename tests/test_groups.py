"""Groupes de comptes."""

import pytest

from app.extensions import db
from app.models import AccessGroup, Account, Activity, Role
from app.services import groups

from .conftest import enroll_totp, login, make_account


def group(name="Régie"):
    return db.session.scalar(db.select(AccessGroup).filter_by(name=name))


@pytest.fixture
def regie(app):
    item = AccessGroup(key="regie", name="Régie")
    db.session.add(item)
    db.session.commit()
    return item


def test_create_derives_a_stable_key(client, admin):
    client.post("/groupes/nouveau", data={"name": "  Régie   Son & Lumière ", "description": "Technique"})
    item = group("Régie Son & Lumière")
    assert item.key == "regie-son-lumiere" and item.description == "Technique"
    assert "group.create" in db.session.scalars(db.select(Activity.action)).all()


def test_renaming_keeps_the_key(client, admin, regie):
    client.post(f"/groupes/{regie.id}/modifier", data={"name": "Technique"})
    item = db.session.get(AccessGroup, regie.id)
    assert item.name == "Technique" and item.key == "regie"


def test_a_posted_key_is_ignored(client, admin, regie):
    client.post(f"/groupes/{regie.id}/modifier", data={"name": "Technique", "key": "pirate"})
    assert db.session.get(AccessGroup, regie.id).key == "regie"


def test_same_name_is_refused_without_accents_or_case(client, admin, regie):
    html = client.post("/groupes/nouveau", data={"name": "REGIE"}).get_data(as_text=True)
    assert "déjà ce nom" in html
    assert db.session.scalar(db.select(db.func.count(AccessGroup.id))) == 1


def test_a_group_can_keep_its_own_name(client, admin, regie):
    assert client.post(f"/groupes/{regie.id}/modifier", data={"name": "Régie", "description": "x"}).status_code == 302


@pytest.mark.parametrize("name", ["", "   ", "x" * 81])
def test_invalid_names(client, admin, name):
    assert client.post("/groupes/nouveau", data={"name": name}).status_code == 200
    assert db.session.scalar(db.select(db.func.count(AccessGroup.id))) == 0


def test_keys_stay_unique(app, regie):
    assert groups.new_key("Régie") == "regie-2"
    assert groups.new_key("!!!") == "groupe"


def test_delete_empty_group(client, admin, regie):
    client.post(f"/groupes/{regie.id}/supprimer")
    assert db.session.get(AccessGroup, regie.id) is None


def test_group_with_members_cannot_be_deleted(client, admin, regie):
    regie.members.append(make_account(email="m@asso.test"))
    db.session.commit()
    client.post(f"/groupes/{regie.id}/supprimer")
    assert db.session.get(AccessGroup, regie.id) is not None


@pytest.mark.parametrize("path", ["/groupes/999/modifier", "/groupes/999/supprimer"])
def test_unknown_group(client, admin, path):
    assert client.post(path).status_code == 404


def test_set_groups_of_an_account(client, admin, regie):
    other = AccessGroup(key="autre", name="Autre")
    db.session.add(other)
    target = make_account(email="m@asso.test")
    client.post(f"/comptes/{target.id}/groupes", data={"groups": [regie.id, other.id]})
    assert {g.key for g in db.session.get(Account, target.id).groups} == {"regie", "autre"}
    client.post(f"/comptes/{target.id}/groupes", data={"groups": [other.id]})
    assert [g.key for g in db.session.get(Account, target.id).groups] == ["autre"]
    entries = db.session.scalars(db.select(Activity).filter_by(action="account.groups")).all()
    assert entries[-1].changes["groupes"] == [["regie"], []]


def test_unknown_group_id_is_ignored(client, admin, regie):
    target = make_account(email="m@asso.test")
    assert client.post(f"/comptes/{target.id}/groupes", data={"groups": ["999", "abc"]}).status_code == 302
    assert db.session.get(Account, target.id).groups == []


def test_saving_the_same_groups_logs_nothing(client, admin, regie):
    target = make_account(email="m@asso.test")
    client.post(f"/comptes/{target.id}/groupes")
    assert db.session.scalar(db.select(Activity).filter_by(action="account.groups")) is None


def test_group_filter_links_to_accounts(client, admin, regie):
    assert f"/comptes?groupe={regie.id}" in client.get("/groupes").get_data(as_text=True)


@pytest.fixture
def group_manager(client):
    db.session.add(Role(key="resp", name="Responsable", permissions=["account.manage", "group.manage"]))
    account = make_account("resp", email="resp@asso.test")
    enroll_totp(account)
    login(client, account)
    return account


def test_non_admin_cannot_change_their_own_groups(client, group_manager, regie):
    """Sinon il s'ouvrirait lui-même n'importe quelle appli."""
    client.post(f"/comptes/{group_manager.id}/groupes", data={"groups": [regie.id]})
    assert db.session.get(Account, group_manager.id).groups == []


def test_admin_can_change_their_own_groups(client, admin, regie):
    client.post(f"/comptes/{admin.id}/groupes", data={"groups": [regie.id]})
    assert [g.key for g in db.session.get(Account, admin.id).groups] == ["regie"]


def test_deleting_account_rows_cascades_to_memberships(app, regie):
    target = make_account(email="m@asso.test")
    regie.members.append(target)
    db.session.commit()
    db.session.delete(target)
    db.session.commit()
    assert db.session.get(AccessGroup, regie.id).members == []
