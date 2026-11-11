"""Écran de gestion des comptes : création, modification, coupure, sessions, et qui peut gérer qui."""

import re

import pytest

from app.extensions import db
from app.models import AccessGroup, Account, AccountToken, Activity, LoginSession, Role
from app.services import accounts as accounts_service
from app.services import mailer, totp

from .conftest import PASSWORD, enroll_totp, login, make_account, totp_code

DATA = {"first_name": "Inès", "last_name": "Lefèvre", "email": "ines@asso.test", "role": Role.ADHERENT}


def by_email(email):
    return db.session.scalar(db.select(Account).filter_by(email=email))


def actions():
    return db.session.scalars(db.select(Activity.action)).all()


def tokens():
    return db.session.scalars(db.select(AccountToken)).all()


@pytest.fixture
def target(app):
    return make_account(email="cible@asso.test", first_name="Cible")


@pytest.fixture
def manager(client):
    """Non-administrateur à qui on a donné la gestion des comptes (et donc le TOTP obligatoire)."""
    db.session.add(Role(key="resp", name="Responsable", permissions=["account.manage", "activity.view"]))
    account = make_account("resp", email="resp@asso.test")
    enroll_totp(account)
    login(client, account)
    return account


# ---------------------------------------------------------------------------
# Création
# ---------------------------------------------------------------------------


def test_create_account_invites_without_password(client, admin):
    html = client.post("/comptes/nouveau", data=DATA).get_data(as_text=True)
    account = by_email("ines@asso.test")
    assert account.active and account.password_hash is None and account.email_verified is False
    assert "/lien/" in html, "sans SMTP, le lien doit s'afficher pour être transmis"
    assert tokens()[0].purpose == AccountToken.INVITE and "account.create" in actions()
    assert len(account.sub) == 36 and account.sub != account.email


def test_create_normalizes_email(client, admin):
    client.post("/comptes/nouveau", data={**DATA, "email": "  INES@Asso.Test "})
    assert by_email("ines@asso.test") is not None


def test_invited_account_cannot_log_in_before_choosing(client, admin):
    client.post("/comptes/nouveau", data=DATA)
    client.post("/deconnexion")
    response = client.post("/connexion", data={"email": "ines@asso.test", "password": "n-importe-quoi"})
    assert response.status_code == 200 and "incorrect" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "field, value",
    [
        ("first_name", ""),
        ("last_name", ""),
        ("email", ""),
        ("email", "pas-email"),
        ("first_name", "x" * 81),
        ("email", "x" * 251 + "@a.fr"),
        ("role", "inexistant"),
    ],
)
def test_create_validation(client, admin, field, value):
    assert client.post("/comptes/nouveau", data={**DATA, field: value}).status_code == 200
    assert by_email("ines@asso.test") is None and tokens() == []


def test_create_refuses_a_taken_address_whatever_the_case(client, admin, target):
    html = client.post("/comptes/nouveau", data={**DATA, "email": "CIBLE@asso.test"}).get_data(as_text=True)
    assert "déjà utilisée" in html


def test_html_in_names_is_escaped(client, admin):
    html = client.post("/comptes/nouveau", data={**DATA, "first_name": "<script>x</script>"}).get_data(as_text=True)
    assert "<script>x" not in html
    assert "<script>x" not in client.get(f"/comptes/{by_email('ines@asso.test').id}").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Liste
# ---------------------------------------------------------------------------


def names(client, query=""):
    return re.findall(r'href="/comptes/\d+">([^<]+)<', client.get(f"/comptes{query}").get_data(as_text=True))


def test_search_ignores_accents_and_case(client, admin):
    make_account(email="a@asso.test", first_name="Éloïse")
    make_account(email="b@asso.test", first_name="Paul")
    assert names(client, "?q=eloise") == ["TEST Éloïse"]
    assert names(client, "?q=B@ASSO") == ["TEST Paul"]


def test_filter_by_role(client, admin, target):
    assert names(client, f"?role={Role.ADMIN}") == ["TEST Admin"]


def test_unknown_filters_are_ignored(client, admin, target):
    assert len(names(client, "?role=zzz&statut=zzz&groupe=999")) == len(names(client)) == 4


def test_filter_by_group(client, admin, target):
    db.session.add(AccessGroup(key="k", name="K", members=[target]))
    db.session.commit()
    assert names(client, "?groupe=1") == ["TEST Cible"]


def test_status_filter_matches_status_of(client, admin):
    """Le filtre SQL et l'étiquette affichée doivent dire la même chose."""
    client.post("/comptes/nouveau", data=DATA)
    make_account(email="jamais@asso.test", first_name="Jamais")
    make_account(email="coupe@asso.test", first_name="Coupe", active=False)
    for account in db.session.scalars(db.select(Account)):
        assert accounts_service.status_of(account) in accounts_service.STATUSES
        listed = db.session.scalars(
            db.select(Account).where(accounts_service.status_condition(accounts_service.status_of(account)))
        ).all()
        assert account in listed
    assert names(client, "?statut=invite") == ["LEFÈVRE Inès"]
    assert "TEST Jamais" in names(client, "?statut=jamais") and "LEFÈVRE Inès" not in names(client, "?statut=jamais")
    assert names(client, "?statut=coupe") == ["TEST Coupe"]
    assert names(client, "?statut=actif") == ["TEST Admin"]


def test_pagination(client, admin):
    db.session.add_all(
        Account(first_name="P", last_name=f"N{n:03}", email=f"p{n}@asso.test", role=Role.ADHERENT) for n in range(120)
    )
    db.session.commit()
    assert len(names(client)) == 100
    assert len(names(client, "?page=2")) == 23  # 120 + les 3 comptes de la fixture
    assert "Page 1 sur 2" in client.get("/comptes").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Modification
# ---------------------------------------------------------------------------


def test_edit_changes_names_and_role_with_journal(client, admin, target):
    client.post(
        f"/comptes/{target.id}/modifier",
        data={"first_name": "Nouveau", "last_name": "Nom", "email": target.email, "role": Role.GESTIONNAIRE},
    )
    target = db.session.get(Account, target.id)
    assert target.full_name == "Nouveau Nom" and target.role == Role.GESTIONNAIRE
    entry = db.session.scalar(db.select(Activity).filter_by(action="account.update"))
    assert entry.changes["role"] == [Role.ADHERENT, Role.GESTIONNAIRE] and entry.target_account_id == target.id


def test_edit_email_loses_verification_and_cancels_links(client, admin, target):
    target.email_verified = True
    raw = accounts_service.issue(target, AccountToken.RESET, proves_email=True)
    db.session.commit()
    client.post(
        f"/comptes/{target.id}/modifier",
        data={"first_name": "C", "last_name": "T", "email": "nouvelle@asso.test", "role": Role.ADHERENT},
    )
    target = db.session.get(Account, target.id)
    assert target.email == "nouvelle@asso.test" and target.email_verified is False
    assert accounts_service.find(raw) is None


def test_edit_warns_the_old_address(app, client, admin, target, monkeypatch):
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="asso@asso.test")
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, *a, **k: sent.append(to))
    client.post(
        f"/comptes/{target.id}/modifier",
        data={"first_name": "C", "last_name": "T", "email": "nouvelle@asso.test", "role": Role.ADHERENT},
    )
    assert sent == ["cible@asso.test"]


def test_edit_without_email_change_keeps_verification(client, admin, target):
    target.email_verified = True
    db.session.commit()
    client.post(
        f"/comptes/{target.id}/modifier",
        data={"first_name": "Autre", "last_name": "T", "email": "cible@asso.test", "role": Role.ADHERENT},
    )
    assert db.session.get(Account, target.id).email_verified is True


def test_edit_refuses_a_taken_address(client, admin, target):
    html = client.post(
        f"/comptes/{target.id}/modifier",
        data={"first_name": "C", "last_name": "T", "email": admin.email, "role": Role.ADHERENT},
    )
    assert "déjà utilisée" in html.get_data(as_text=True)


def test_nobody_changes_their_own_role_or_email_here(client, admin):
    client.post(
        f"/comptes/{admin.id}/modifier",
        data={"first_name": "A", "last_name": "T", "email": admin.email, "role": Role.ADHERENT},
    )
    assert db.session.get(Account, admin.id).is_admin
    client.post(
        f"/comptes/{admin.id}/modifier",
        data={"first_name": "A", "last_name": "T", "email": "x@asso.test", "role": Role.ADMIN},
    )
    assert db.session.get(Account, admin.id).email == admin.email


def test_own_names_can_change(client, admin):
    client.post(
        f"/comptes/{admin.id}/modifier",
        data={"first_name": "Zoé", "last_name": "T", "email": admin.email, "role": Role.ADMIN},
    )
    assert db.session.get(Account, admin.id).first_name == "Zoé"


def test_sub_never_changes(client, admin, target):
    sub = target.sub
    client.post(
        f"/comptes/{target.id}/modifier",
        data={"first_name": "C", "last_name": "T", "sub": "pirate", "email": "n@asso.test", "role": Role.ADHERENT},
    )
    assert db.session.get(Account, target.id).sub == sub


# ---------------------------------------------------------------------------
# Invitation renvoyée
# ---------------------------------------------------------------------------


def test_resend_replaces_the_previous_invitation(client, admin):
    first = client.post("/comptes/nouveau", data=DATA).get_data(as_text=True)
    old = re.search(r"/lien/([\w-]+)", first).group(1)
    account = by_email("ines@asso.test")
    client.post(f"/comptes/{account.id}/inviter")
    assert accounts_service.find(old) is None and len(tokens()) == 1


def test_account_with_password_gets_a_reset_link(client, admin, target):
    client.post(f"/comptes/{target.id}/inviter")
    assert tokens()[0].purpose == AccountToken.RESET and "account.reset_link" in actions()


def test_cut_account_cannot_be_invited(client, admin, target):
    target.active = False
    db.session.commit()
    client.post(f"/comptes/{target.id}/inviter")
    assert tokens() == []


# ---------------------------------------------------------------------------
# Couper, rétablir
# ---------------------------------------------------------------------------


def test_cut_is_immediate(app, client, admin, target):
    victim = app.test_client()
    login(victim, target)
    raw = accounts_service.issue(target, AccountToken.RESET)
    db.session.commit()
    assert victim.get("/mon-compte").status_code == 200
    client.post(f"/comptes/{target.id}/couper")
    assert victim.get("/mon-compte").status_code == 302
    assert accounts_service.find(raw) is None
    assert db.session.scalar(db.select(db.func.count(LoginSession.id)).filter_by(account_id=target.id)) == 0
    assert login(app.test_client(), target).status_code == 200, "le mot de passe ne rouvre plus rien"
    assert "account.cut" in actions()


def test_cut_stops_a_remembered_browser(app, client, admin, target):
    victim = app.test_client()
    victim.post("/connexion", data={"email": target.email, "password": PASSWORD, "remember": "y"})
    client.post(f"/comptes/{target.id}/couper")
    assert victim.get("/mon-compte").status_code == 302


def test_nobody_cuts_their_own_account(client, admin):
    client.post(f"/comptes/{admin.id}/couper")
    assert db.session.get(Account, admin.id).active and client.get("/comptes").status_code == 200


def test_restore_reopens_password_login(app, client, admin, target):
    client.post(f"/comptes/{target.id}/couper")
    client.post(f"/comptes/{target.id}/retablir")
    assert login(app.test_client(), target).status_code == 302
    assert "account.restore" in actions()


def test_at_least_one_admin_always_remains(client, admin):
    """Un admin ne peut agir sur lui-même ni être géré par un non-admin : le dernier ne peut donc pas disparaître."""
    client.post(f"/comptes/{admin.id}/couper")
    client.post(
        f"/comptes/{admin.id}/modifier",
        data={"first_name": "A", "last_name": "T", "email": admin.email, "role": Role.ADHERENT},
    )
    account = db.session.get(Account, admin.id)
    assert account.is_admin and account.active


# ---------------------------------------------------------------------------
# Sessions et double authentification d'un autre compte
# ---------------------------------------------------------------------------


def test_close_one_session_of_someone(app, client, admin, target):
    victim = app.test_client()
    login(victim, target)
    session_id = db.session.scalar(db.select(LoginSession.sid).filter_by(account_id=target.id))
    client.post(f"/comptes/{target.id}/sessions/{session_id}/fermer")
    assert victim.get("/mon-compte").status_code == 302


def test_close_session_of_another_account_via_wrong_url(app, client, admin, target):
    victim = app.test_client()
    login(victim, target)
    session_id = db.session.scalar(db.select(LoginSession.sid).filter_by(account_id=target.id))
    other = make_account(email="autre@asso.test")
    client.post(f"/comptes/{other.id}/sessions/{session_id}/fermer")
    assert victim.get("/mon-compte").status_code == 200


def test_close_all_sessions_keeps_mine(app, client, admin):
    other = app.test_client()
    login(other, admin)
    client.post(f"/comptes/{admin.id}/sessions/fermer")
    assert client.get("/comptes").status_code == 200 and other.get("/comptes").status_code == 302


def test_reset_totp_of_someone(app, client, admin, target):
    enroll_totp(target)
    totp.new_recovery_codes(target)
    db.session.commit()
    victim = app.test_client()
    login(victim, target)
    client.post(f"/comptes/{target.id}/double-authentification/reinitialiser")
    assert not db.session.get(Account, target.id).totp_enabled
    assert victim.get("/mon-compte").status_code == 302
    assert "account.totp_reset" in actions()


def test_cannot_reset_my_own_totp_here(client, admin):
    client.post(f"/comptes/{admin.id}/double-authentification/reinitialiser")
    assert db.session.get(Account, admin.id).totp_enabled


# ---------------------------------------------------------------------------
# Qui peut gérer qui
# ---------------------------------------------------------------------------


def post_routes(account_id):
    return [
        f"/comptes/{account_id}/inviter",
        f"/comptes/{account_id}/couper",
        f"/comptes/{account_id}/retablir",
        f"/comptes/{account_id}/double-authentification/reinitialiser",
        f"/comptes/{account_id}/sessions/fermer",
        f"/comptes/{account_id}/modifier",
        f"/comptes/{account_id}/groupes",
    ]


def test_manager_cannot_touch_an_admin(client, manager, accounts):
    target = accounts[Role.ADMIN]
    for path in post_routes(target.id):
        assert client.post(path).status_code == 403, path
    assert db.session.get(Account, target.id).active and tokens() == []


def test_manager_can_read_an_admin_but_sees_no_action(client, manager, accounts):
    html = client.get(f"/comptes/{accounts[Role.ADMIN].id}").get_data(as_text=True)
    assert "plus de droits" in html and "couper" not in html and "inviter" not in html


def test_manager_cannot_touch_someone_with_more_rights(client, manager):
    db.session.add(Role(key="plus", name="Plus", permissions=["settings.manage", "account.manage"]))
    richer = make_account("plus", email="plus@asso.test")
    for path in post_routes(richer.id):
        assert client.post(path).status_code == 403, path


def test_manager_cannot_touch_someone_in_a_group_they_lack(client, manager, target):
    """Sinon il prendrait le contrôle d'un compte qui ouvre une appli à laquelle il n'a pas droit."""
    db.session.add(AccessGroup(key="k", name="K", members=[target]))
    db.session.commit()
    assert client.post(f"/comptes/{target.id}/inviter").status_code == 403


def test_manager_can_manage_an_ordinary_account(client, manager, target):
    assert client.post(f"/comptes/{target.id}/couper").status_code == 302
    assert not db.session.get(Account, target.id).active


@pytest.mark.parametrize("role", [Role.ADMIN, "plus"])
def test_manager_cannot_grant_more_than_they_have(client, manager, target, role):
    db.session.add(Role(key="plus", name="Plus", permissions=["settings.manage"]))
    db.session.commit()
    client.post("/comptes/nouveau", data={**DATA, "role": role})
    assert by_email("ines@asso.test") is None
    client.post(
        f"/comptes/{target.id}/modifier",
        data={"first_name": "C", "last_name": "T", "email": target.email, "role": role},
    )
    assert db.session.get(Account, target.id).role == Role.ADHERENT


def test_manager_form_only_offers_allowed_roles(client, manager):
    db.session.add(Role(key="plus", name="Rôle-trop-riche", permissions=["settings.manage"]))
    db.session.commit()
    html = client.get("/comptes/nouveau").get_data(as_text=True)
    assert "Adhérent" in html and "Administrateur" not in html and "Rôle-trop-riche" not in html


def test_manager_without_group_right_cannot_change_groups(client, manager, target):
    db.session.add(AccessGroup(key="k", name="K"))
    db.session.commit()
    assert client.post(f"/comptes/{target.id}/groupes", data={"groups": "1"}).status_code == 403
    assert db.session.get(Account, target.id).groups == []


def test_journal_shows_these_actions_in_the_accounts_family(client, admin, target):
    client.post(f"/comptes/{target.id}/couper")
    assert "Compte coupé" in client.get("/journal?type=comptes").get_data(as_text=True)


def test_password_and_codes_never_reach_the_journal(client, admin, target):
    client.post(
        f"/comptes/{target.id}/modifier",
        data={
            "first_name": "C",
            "last_name": "T",
            "email": target.email,
            "role": Role.ADHERENT,
            "password": "secret-ne-pas-noter",
        },
    )
    login_code = totp_code()
    text = " ".join(f"{a.message} {a.changes}" for a in db.session.scalars(db.select(Activity)))
    assert "secret-ne-pas-noter" not in text and login_code not in text


def test_old_link_stays_dead_after_a_restore(client, admin, target):
    """Couper annule les liens pour de bon : rétablir le compte ne doit pas les réveiller."""
    raw = accounts_service.issue(target, AccountToken.RESET)
    db.session.commit()
    client.post(f"/comptes/{target.id}/couper")
    client.post(f"/comptes/{target.id}/retablir")
    assert accounts_service.find(raw) is None


def test_even_a_role_with_every_right_cannot_touch_an_admin(client):
    from app.permissions import PERMISSIONS

    db.session.add(Role(key="tout", name="Presque admin", permissions=sorted(PERMISSIONS)))
    account = make_account("tout", email="tout@asso.test")
    enroll_totp(account)
    login(client, account)
    chief = make_account(Role.ADMIN, email="chef@asso.test")
    assert client.post(f"/comptes/{chief.id}/couper").status_code == 403
    assert db.session.get(Account, chief.id).active
