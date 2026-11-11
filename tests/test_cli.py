from app.extensions import db
from app.models import Account, LoginSession, Role
from app.services import health

from .conftest import login, make_account


def create_admin(app, email="Chef@Asso.Test", password="un-mot-de-passe"):
    return app.test_cli_runner().invoke(
        args=[
            "create-admin",
            "--email",
            email,
            "--first-name",
            "Élise",
            "--last-name",
            "Lefèvre",
            "--password",
            password,
        ]
    )


def test_create_admin(app):
    result = create_admin(app)
    assert result.exit_code == 0 and "Élise Lefèvre" in result.output
    account = db.session.scalar(db.select(Account))
    assert account.email == "chef@asso.test" and account.is_admin and account.active
    assert account.check_password("un-mot-de-passe") and account.sub


def test_create_admin_is_the_rescue_access(app, client):
    """Compte existant : il redevient admin, actif, avec le nouveau mot de passe ; ses anciennes sessions sont closes."""
    account = make_account(Role.ADHERENT, email="chef@asso.test")
    login(client, account)
    account.active = False
    db.session.commit()
    assert create_admin(app).exit_code == 0
    account = db.session.get(Account, account.id)
    assert account.is_admin and account.active and account.check_password("un-mot-de-passe")
    assert db.session.scalar(db.select(db.func.count(LoginSession.id))) == 0
    assert db.session.scalar(db.select(db.func.count(Account.id))) == 1


def test_run_tasks_records_its_passage(app):
    assert health.last_scheduler_run() is None
    result = app.test_cli_runner().invoke(args=["run-tasks"])
    assert result.exit_code == 0 and "effacé(s)" in result.output
    assert health.last_scheduler_run() is not None


# ---------------------------------------------------------------------------
# Export des comptes pour le rattachement (phase 5)
# ---------------------------------------------------------------------------


def make_app_with_members(name="Inventaire"):
    from app.models import AccessGroup, OidcClient
    from app.services import oidc

    members, others = AccessGroup(key="membres", name="Membres"), AccessGroup(key="autres", name="Autres")
    client = OidcClient(
        client_id=oidc.new_client_id(),
        name=name,
        redirect_uris=["https://i.test/retour"],
        allowed_groups=[members],
        shared_groups=[members],
        first_party=True,
    )
    oidc.set_secret(client)
    db.session.add(client)
    db.session.commit()
    return client, members, others


def test_export_lists_only_the_accounts_that_may_enter(app):
    import json

    _client, members, others = make_app_with_members()
    elise = make_account(email="elise@asso.test", first_name="Élise", email_verified=True)
    elise.groups = [members]
    outsider = make_account(email="olaf@asso.test", first_name="Olaf")
    outsider.groups = [others]
    cut = make_account(email="coupe@asso.test", first_name="Coupé", active=False)
    cut.groups = [members]
    db.session.commit()
    result = app.test_cli_runner().invoke(args=["oidc-export-accounts", "--application", "inventaire"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["application"] == "Inventaire" and data["issuer"] == "http://localhost:8001"
    assert data["accounts"] == [
        {
            "sub": elise.sub,
            "email": "elise@asso.test",
            "email_verified": True,
            "first_name": "Élise",
            "last_name": "Test",
            "groups": ["membres"],
        }
    ]
    assert "1 compte(s) exporté(s)" in result.stderr
    from app.models import Activity

    assert db.session.scalar(db.select(Activity).filter_by(action="oidc.export")) is not None


def test_export_to_a_file_keeps_accents(app, tmp_path):
    import json

    _client, members, _ = make_app_with_members()
    account = make_account(email="e@asso.test", first_name="Éloïse")
    account.groups = [members]
    db.session.commit()
    path = tmp_path / "comptes.json"
    assert (
        app.test_cli_runner()
        .invoke(args=["oidc-export-accounts", "--application", "Inventaire", "--output", str(path)])
        .exit_code
        == 0
    )
    text = path.read_text(encoding="utf-8")
    assert "Éloïse" in text and json.loads(text)["accounts"][0]["first_name"] == "Éloïse"


def test_export_of_an_unknown_application_fails_clearly(app):
    result = app.test_cli_runner().invoke(args=["oidc-export-accounts", "--application", "Inconnue"])
    assert result.exit_code != 0 and "Aucune application" in result.output


def test_export_never_contains_secrets(app):
    _client, members, _ = make_app_with_members()
    account = make_account(email="e@asso.test")
    account.groups = [members]
    db.session.commit()
    output = app.test_cli_runner().invoke(args=["oidc-export-accounts", "--application", "Inventaire"]).output
    assert "password" not in output and "totp" not in output and account.password_hash not in output


def test_create_admin_notifies_the_apps_of_closed_sessions(app, client, monkeypatch):
    from app.services import logout

    calls = []
    monkeypatch.setattr(logout, "deliver_pending", lambda now=None: calls.append(now) or (0, 0, 0))
    make_account(Role.ADHERENT, email="chef@asso.test")
    assert create_admin(app).exit_code == 0 and calls == [None]
