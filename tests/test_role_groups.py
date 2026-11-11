"""Groupes donnés par un rôle : chaque compte du rôle en fait partie sans qu'on les coche sur sa fiche.

Ils comptent partout où comptent les groupes : accès aux applis, groupes transmis, qui peut gérer qui, liste des
comptes d'un groupe. Et ils ne doivent pas permettre à un gestionnaire de donner des groupes qu'il n'a pas.
"""

import pytest

from app.extensions import db
from app.models import AccessGroup, Account, Activity, OidcClient, Role
from app.services import accounts as accounts_service
from app.services import oidc

from .conftest import enroll_totp, login, make_account
from .test_logout import Sent, connect, with_backchannel
from .test_oidc import make_client


@pytest.fixture
def sent(monkeypatch):
    from app.services import logout

    recorder = Sent()
    monkeypatch.setattr(logout, "post", recorder)
    return recorder


@pytest.fixture
def groups(app):
    members, admins = (
        AccessGroup(key="membres", name="Membres"),
        AccessGroup(key="inventaire-admins", name="Admins de l'Inventaire"),
    )
    db.session.add_all([members, admins])
    db.session.commit()
    return members, admins


def give_role_groups(key, *groups):
    from app.services import roles

    role = roles.get(key)
    role.groups = list(groups)
    db.session.commit()
    return role


@pytest.fixture
def admin_http(app):
    http = app.test_client()
    login(http, make_account(Role.ADMIN, email="chef@asso.test"))
    return http


# ---------------------------------------------------------------------------
# Les groupes effectifs
# ---------------------------------------------------------------------------


def test_account_gets_the_groups_of_its_role(groups):
    members, admins = groups
    give_role_groups(Role.GESTIONNAIRE, admins)
    account = make_account(Role.GESTIONNAIRE)
    account.groups = [members]
    db.session.commit()
    assert account.groups == [members]
    assert account.role_groups == [admins] and set(account.all_groups) == {members, admins}


def test_role_group_opens_the_app_and_is_shared(app, groups, client):
    members, admins = groups
    give_role_groups(Role.ADHERENT, members)
    person = make_account(Role.ADHERENT, email="paul@asso.test")  # aucun groupe coché
    application = make_client(allowed=[members], shared=[members, admins])
    assert oidc.may_access(application, person)
    login(client, person)
    claims = connect(client, application)
    from .test_oidc import decode

    assert decode(claims["id_token"])["groups"] == ["membres"]


def test_unsaved_builtin_role_has_no_groups(app):
    account = make_account(Role.ADHERENT)
    assert db.session.get(Role, Role.ADHERENT) is None and account.role_groups == []


def test_group_explicit_and_by_role_counts_once(groups):
    members, _ = groups
    give_role_groups(Role.ADHERENT, members)
    account = make_account(Role.ADHERENT)
    account.groups = [members]
    db.session.commit()
    assert account.all_groups == [members]


# ---------------------------------------------------------------------------
# Écran Rôles
# ---------------------------------------------------------------------------


def test_role_form_saves_groups_and_journals_them(admin_http, groups):
    members, admins = groups
    response = admin_http.post(
        "/roles/gestionnaire/modifier", data={"name": "Gestionnaire", "groups": [admins.id, members.id]}
    )
    assert response.status_code == 302
    role = db.session.get(Role, Role.GESTIONNAIRE)
    assert [g.key for g in role.groups] == ["inventaire-admins", "membres"]
    entry = db.session.scalar(db.select(Activity).filter_by(action="role.update"))
    assert entry.changes == {"groupes": [[], ["inventaire-admins", "membres"]]}
    page = admin_http.get("/roles").get_data(as_text=True)
    assert "Admins de l&#39;Inventaire" in page


def test_role_form_shows_checked_groups(admin_http, groups):
    _, admins = groups
    give_role_groups(Role.GESTIONNAIRE, admins)
    page = admin_http.get("/roles/gestionnaire/modifier").get_data(as_text=True)
    assert f'value="{admins.id}" checked' in page


def test_new_role_with_groups(admin_http, groups):
    _, admins = groups
    admin_http.post("/roles/nouveau", data={"name": "Régie", "groups": [admins.id]})
    role = db.session.scalar(db.select(Role).filter_by(name="Régie"))
    assert role.groups == [admins]


def test_duplicated_role_starts_with_the_same_groups(admin_http, groups):
    _, admins = groups
    give_role_groups(Role.GESTIONNAIRE, admins)
    page = admin_http.get("/roles/nouveau?depuis=gestionnaire").get_data(as_text=True)
    assert f'value="{admins.id}" checked' in page


def test_unknown_group_ids_are_ignored(admin_http, groups):
    admin_http.post("/roles/gestionnaire/modifier", data={"name": "Gestionnaire", "groups": ["999", "abc"]})
    assert db.session.get(Role, Role.GESTIONNAIRE).groups == []


def test_only_admins_edit_role_groups(app, groups):
    _, admins = groups
    manager = make_account(Role.GESTIONNAIRE)
    enroll_totp(manager)
    http = app.test_client()
    login(http, manager)
    assert http.post("/roles/gestionnaire/modifier", data={"name": "G", "groups": [admins.id]}).status_code == 403
    assert db.session.get(Role, Role.GESTIONNAIRE) is None or db.session.get(Role, Role.GESTIONNAIRE).groups == []


def test_deleting_a_role_keeps_its_groups(admin_http, groups):
    _, admins = groups
    admin_http.post("/roles/nouveau", data={"name": "Régie", "groups": [admins.id]})
    key = db.session.scalar(db.select(Role.key).filter_by(name="Régie"))
    admin_http.post(f"/roles/{key}/supprimer")
    assert db.session.get(Role, key) is None and db.session.get(AccessGroup, admins.id) is not None


# ---------------------------------------------------------------------------
# Avis de déconnexion quand les groupes d'un rôle ou le rôle d'un compte changent
# ---------------------------------------------------------------------------


def test_role_groups_change_notifies_the_apps(app, admin_http, groups, sent):
    members, admins = groups
    give_role_groups(Role.ADHERENT, members)
    application = with_backchannel(make_client(allowed=[members], shared=[members, admins], name="Inventaire"))
    person = make_account(Role.ADHERENT, email="paul@asso.test")
    browser = app.test_client()
    login(browser, person)
    connect(browser, application)
    sent.clear()
    admin_http.post("/roles/adherent/modifier", data={"name": "Adhérent", "groups": [members.id, admins.id]})
    assert [claims["sub"] for claims in sent.claims()] == [person.sub]  # il devient admin de l'Inventaire


def test_role_groups_unchanged_notifies_nobody(app, admin_http, groups, sent):
    members, _ = groups
    give_role_groups(Role.ADHERENT, members)
    application = with_backchannel(make_client(allowed=[members], shared=[members]))
    person = make_account(Role.ADHERENT, email="paul@asso.test")
    browser = app.test_client()
    login(browser, person)
    connect(browser, application)
    sent.clear()
    admin_http.post("/roles/adherent/modifier", data={"name": "Adhérent renommé", "groups": [members.id]})
    assert len(sent) == 0


def test_changing_the_role_of_an_account_notifies_and_cuts_access(app, admin_http, groups, sent):
    members, _ = groups
    give_role_groups(Role.GESTIONNAIRE, members)
    application = with_backchannel(make_client(allowed=[members], shared=[members]))
    person = make_account(Role.GESTIONNAIRE, email="g@asso.test")
    enroll_totp(person)
    browser = app.test_client()
    login(browser, person)
    token = connect(browser, application)["access_token"]
    sent.clear()
    response = admin_http.post(
        f"/comptes/{person.id}/modifier",
        data={
            "first_name": person.first_name,
            "last_name": person.last_name,
            "email": person.email,
            "role": Role.ADHERENT,
        },
    )
    assert response.status_code == 302
    assert [claims["sub"] for claims in sent.claims()] == [person.sub]
    assert browser.get("/oidc/userinfo", headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ---------------------------------------------------------------------------
# Fiche d'un compte, liste des comptes, écran Groupes
# ---------------------------------------------------------------------------


def test_account_page_shows_role_groups_as_fixed(admin_http, groups):
    members, admins = groups
    give_role_groups(Role.ADHERENT, admins)
    person = make_account(Role.ADHERENT, email="paul@asso.test")
    page = admin_http.get(f"/comptes/{person.id}").get_data(as_text=True)
    assert "(par le rôle Adhérent)" in page
    assert f'name="groups" value="{admins.id}"' not in page and f'name="groups" value="{members.id}"' in page


def test_saving_groups_never_turns_role_groups_into_checked_ones(admin_http, groups):
    members, admins = groups
    give_role_groups(Role.ADHERENT, admins)
    person = make_account(Role.ADHERENT, email="paul@asso.test")
    admin_http.post(f"/comptes/{person.id}/groupes", data={"groups": [members.id, admins.id]})
    assert db.session.get(Account, person.id).groups == [members]


def test_saving_groups_keeps_an_older_explicit_membership(admin_http, groups):
    members, admins = groups
    person = make_account(Role.ADHERENT, email="paul@asso.test")
    person.groups = [admins]
    db.session.commit()
    give_role_groups(Role.ADHERENT, admins)
    admin_http.post(f"/comptes/{person.id}/groupes", data={"groups": [members.id]})
    assert set(db.session.get(Account, person.id).groups) == {members, admins}


def test_group_filter_includes_members_by_role(admin_http, groups):
    _, admins = groups
    give_role_groups(Role.GESTIONNAIRE, admins)
    by_role = make_account(Role.GESTIONNAIRE, email="g@asso.test", first_name="Gaston")
    direct = make_account(Role.ADHERENT, email="d@asso.test", first_name="Denise")
    direct.groups = [admins]
    outside = make_account(Role.ADHERENT, email="o@asso.test", first_name="Odile")
    db.session.commit()
    page = admin_http.get(f"/comptes?groupe={admins.id}").get_data(as_text=True)
    assert by_role.email in page and direct.email in page and outside.email not in page


def test_groups_page_counts_and_protects_role_groups(admin_http, groups):
    members, admins = groups
    give_role_groups(Role.GESTIONNAIRE, admins)
    make_account(Role.GESTIONNAIRE, email="g1@asso.test")
    make_account(Role.GESTIONNAIRE, email="g2@asso.test")
    page = admin_http.get("/groupes").get_data(as_text=True)
    assert "0 coché(s), le reste par le rôle Gestionnaire" in page
    response = admin_http.post(f"/groupes/{admins.id}/supprimer", follow_redirects=True)
    assert "donné par le rôle Gestionnaire" in response.get_data(as_text=True)
    assert db.session.get(AccessGroup, admins.id) is not None
    admin_http.post(f"/groupes/{members.id}/supprimer")
    assert db.session.get(AccessGroup, members.id) is None


# ---------------------------------------------------------------------------
# Qui peut donner quoi
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(app):
    """Gestionnaire qui gère les comptes, sans le groupe « Admins de l'Inventaire »."""
    from app.services import roles

    role = roles.get(Role.GESTIONNAIRE)
    role.permissions = sorted(set(role.permissions) | {"account.manage"})
    db.session.commit()
    account = make_account(Role.GESTIONNAIRE, email="gestion@asso.test")
    enroll_totp(account)
    return account


def test_a_role_carrying_a_group_the_manager_lacks_is_not_assignable(manager, groups):
    _, admins = groups
    db.session.add(Role(key="regie", name="Régie", permissions=[], groups=[admins]))
    db.session.commit()
    keys = [key for key, _ in accounts_service.assignable_roles(manager)]
    assert "regie" not in keys and Role.ADHERENT in keys


def test_the_same_role_is_assignable_once_the_manager_has_the_group(manager, groups):
    _, admins = groups
    db.session.add(Role(key="regie", name="Régie", permissions=[], groups=[admins]))
    manager.groups = [admins]
    db.session.commit()
    assert "regie" in [key for key, _ in accounts_service.assignable_roles(manager)]


def test_manager_cannot_create_an_account_with_that_role(app, manager, groups):
    _, admins = groups
    db.session.add(Role(key="regie", name="Régie", permissions=[], groups=[admins]))
    db.session.commit()
    http = app.test_client()
    login(http, manager)
    http.post(
        "/comptes/nouveau", data={"first_name": "Pirate", "last_name": "X", "email": "p@asso.test", "role": "regie"}
    )
    created = db.session.scalar(db.select(Account).filter_by(email="p@asso.test"))
    assert created is None or created.role != "regie"


def test_manager_cannot_manage_someone_whose_role_gives_more_groups(manager, groups):
    _, admins = groups
    db.session.add(Role(key="regie", name="Régie", permissions=[], groups=[admins]))
    db.session.commit()
    target = make_account("regie", email="r@asso.test")
    assert not accounts_service.can_manage(manager, target)
    manager.groups = [admins]
    db.session.commit()
    assert accounts_service.can_manage(manager, target)


def test_consent_page_lists_role_groups(app, groups, client):
    members, admins = groups
    give_role_groups(Role.ADHERENT, members, admins)
    person = make_account(Role.ADHERENT, email="paul@asso.test")
    third = make_client(allowed=[members], shared=[admins], first_party=False, name="Tierce")
    login(client, person)
    from .test_oidc import Flow, follow

    page = follow(client, client.get(Flow(third).url)).get_data(as_text=True)
    assert "Admins de l&#39;Inventaire" in page
    assert OidcClient  # import utilisé par make_client
