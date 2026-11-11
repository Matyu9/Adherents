import pytest

from app.extensions import db
from app.models import Activity, Role
from app.services import roles

from .conftest import make_account


def test_index_lists_builtin_roles(client, admin):
    html = client.get("/roles").get_data(as_text=True)
    assert all(name in html for name in Role.DEFAULT_NAMES.values())


def test_create_role(client, admin):
    response = client.post(
        "/roles/nouveau",
        data={
            "name": "  Régisseur   son ",
            "description": "Sono",
            "permissions": ["activity.view", "droit.inexistant"],
        },
    )
    assert response.status_code == 302
    role = db.session.get(Role, "regisseur-son")
    assert role.name == "Régisseur son" and role.permissions == ["activity.view"]
    assert db.session.scalar(db.select(Activity).filter_by(action="role.create"))


@pytest.mark.parametrize("name", ["Administrateur", "ADMINISTRATEUR", "administrâteur", " adhérent "])
def test_name_must_be_unique_without_case_or_accents(client, admin, name):
    response = client.post("/roles/nouveau", data={"name": name})
    assert response.status_code == 200 and "déjà ce nom" in response.get_data(as_text=True)


def test_name_is_required(client, admin):
    assert "regie" not in str(client.post("/roles/nouveau", data={"name": ""}).location)
    assert db.session.scalar(db.select(db.func.count(Role.key))) == 0


def test_duplicate_prefills_rights(client, admin):
    db.session.add(Role(key="regie", name="Régie", description="Texte", permissions=["activity.view"]))
    db.session.commit()
    html = client.get("/roles/nouveau?depuis=regie").get_data(as_text=True)
    assert "Texte" in html and 'value="activity.view" checked' in html


def test_edit_builtin_role_saves_it(client, admin):
    response = client.post(
        f"/roles/{Role.ADHERENT}/modifier", data={"name": "Membre", "permissions": ["activity.view"]}
    )
    assert response.status_code == 302
    role = db.session.get(Role, Role.ADHERENT)
    assert role.name == "Membre" and role.permissions == ["activity.view"]


def test_edit_can_keep_its_own_name(client, admin):
    assert client.post(f"/roles/{Role.ADHERENT}/modifier", data={"name": "Adhérent"}).status_code == 302


def test_admin_keeps_every_right_whatever_is_posted(client, admin):
    client.post(f"/roles/{Role.ADMIN}/modifier", data={"name": "Chef", "permissions": []})
    assert client.get("/parametres").status_code == 200
    assert db.session.get(Role, Role.ADMIN).name == "Chef"


def test_edit_unknown_role_is_404(client, admin):
    assert client.get("/roles/inconnu/modifier").status_code == 404
    assert client.post("/roles/inconnu/supprimer").status_code == 404


def test_builtin_role_cannot_be_deleted(client, admin):
    db.session.add(Role(key=Role.ADHERENT, name="Adhérent", permissions=[]))
    db.session.commit()
    html = client.post(f"/roles/{Role.ADHERENT}/supprimer", follow_redirects=True).get_data(as_text=True)
    assert "ne peuvent pas être supprimés" in html and db.session.get(Role, Role.ADHERENT)


def test_role_in_use_cannot_be_deleted(client, admin):
    db.session.add(Role(key="regie", name="Régie", permissions=[]))
    make_account("regie")
    html = client.post("/roles/regie/supprimer", follow_redirects=True).get_data(as_text=True)
    assert "attribué à 1 personne" in html and db.session.get(Role, "regie")


def test_delete_unused_role(client, admin):
    db.session.add(Role(key="regie", name="Régie", permissions=[]))
    db.session.commit()
    client.post("/roles/regie/supprimer")
    assert db.session.get(Role, "regie") is None
    assert db.session.scalar(db.select(Activity).filter_by(action="role.delete"))


def test_new_key(app):
    assert roles.new_key("Régisseur son") == "regisseur-son"
    assert roles.new_key("Administrateur très long nom") == "administrateur-t"
    assert roles.new_key("!!!") == "role"
    assert roles.new_key("admin") == "admin-2"
    db.session.add(Role(key="regie", name="R", permissions=[]))
    db.session.commit()
    assert roles.new_key("Régie") == "regie-2"


def test_all_roles_order_and_choices(app):
    db.session.add_all([Role(key="z", name="Zèbre", permissions=[]), Role(key="e", name="Éclair", permissions=[])])
    db.session.commit()
    assert [r.key for r in roles.all_roles()] == [Role.ADMIN, Role.GESTIONNAIRE, Role.ADHERENT, "e", "z"]
    assert Role.ADMIN not in dict(roles.choices(include_admin=False))


def test_member_counts(accounts):
    assert roles.member_counts() == {Role.ADMIN: 1, Role.GESTIONNAIRE: 1, Role.ADHERENT: 1}
