import pytest
from flask_login import AnonymousUserMixin

from app.extensions import db
from app.models import Role
from app.permissions import DEFAULT_PERMISSIONS, PERMISSION_GROUPS, PERMISSIONS, can, permissions_of

from .conftest import make_account


def test_unknown_permission_raises_instead_of_opening_access(accounts):
    """Une faute de frappe dans un droit doit se voir tout de suite, pas ouvrir ni fermer un accès en silence."""
    with pytest.raises(KeyError):
        can(accounts[Role.ADMIN], "setings.manage")


def test_anonymous_and_none_have_no_right(app):
    assert not can(None, "settings.manage")
    assert not can(AnonymousUserMixin(), "settings.manage")


def test_admin_has_every_right_whatever_is_saved(accounts):
    db.session.add(Role(key=Role.ADMIN, name="Administrateur", permissions=[]))
    db.session.commit()
    assert all(can(accounts[Role.ADMIN], key) for key in PERMISSIONS)


def test_default_rights(accounts):
    assert can(accounts[Role.GESTIONNAIRE], "activity.view") and not can(accounts[Role.GESTIONNAIRE], "settings.manage")
    assert not any(can(accounts[Role.ADHERENT], key) for key in PERMISSIONS)


def test_saved_role_replaces_the_defaults(accounts):
    db.session.add(Role(key=Role.ADHERENT, name="Adhérent", permissions=["settings.manage"]))
    db.session.commit()
    assert can(accounts[Role.ADHERENT], "settings.manage")


def test_unknown_role_has_no_right(app):
    assert permissions_of("role-disparu") == set()
    assert not can(make_account("role-disparu"), "activity.view")


def test_catalogue_is_consistent():
    assert set(DEFAULT_PERMISSIONS) == set(Role.BUILTIN)
    assert all(rights <= set(PERMISSIONS) for rights in DEFAULT_PERMISSIONS.values())
    keys = [key for _, perms in PERMISSION_GROUPS for key, _ in perms]
    assert len(keys) == len(set(keys))


def test_member_view_really_reduces_rights(client, admin):
    """Vue adhérent : les pages d'administration sont refusées, pas seulement cachées du menu."""
    assert client.get("/comptes").status_code == 200
    client.post("/mon-compte/vue-adherent")
    page = client.get("/").get_data(as_text=True)
    assert "Vue adhérent" in page and "Revenir à mes droits" in page and 'href="/comptes"' not in page
    assert client.get("/comptes").status_code == 403
    assert client.get("/roles").status_code in (403, 404)
    client.post("/mon-compte/vue-adherent")
    assert client.get("/comptes").status_code == 200
    assert "Revenir à mes droits" not in client.get("/").get_data(as_text=True)


def test_member_view_is_not_offered_to_members(client, member):
    assert "Passer en vue adhérent" not in client.get("/").get_data(as_text=True)
    client.post("/mon-compte/vue-adherent")
    assert "Revenir à mes droits" not in client.get("/").get_data(as_text=True)
