from app.extensions import db
from app.models import Account, LoginSession, Role
from app.services import health

from .conftest import login, make_account


def create_admin(app, email="Chef@Asso.Test", password="un-mot-de-passe"):
    return app.test_cli_runner().invoke(args=["create-admin", "--email", email, "--first-name", "Élise", "--last-name",
                                              "Lefèvre", "--password", password])


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
    assert result.exit_code == 0 and "effacée(s)" in result.output
    assert health.last_scheduler_run() is not None
