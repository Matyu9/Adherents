from datetime import datetime, timedelta

from sqlalchemy import text

from app.extensions import db
from app.models import Notification, Role
from app.services import health

from .conftest import login


def by_name(report):
    return {check["name"]: check for check in report["checks"]}


def test_page_and_json_for_admin(client, admin):
    assert "État de l'installation" in client.get("/sante").get_data(as_text=True)
    data = client.get("/health").get_json()
    assert set(by_name(data)) == {"database", "migrations", "storage", "permissions", "scheduler", "email"}
    assert data["version"]


def test_hidden_from_everyone_else(app, accounts):
    client = app.test_client()
    assert client.get("/sante").status_code == 404 and client.get("/health").status_code == 404
    login(client, accounts[Role.ADHERENT])
    assert client.get("/sante").status_code == 404


def test_stamped_database_is_up_to_date(app):
    assert by_name(health.report())["migrations"]["status"] == health.OK


def test_never_migrated_database_is_an_error_and_503(client, admin):
    db.session.execute(text("DELETE FROM alembic_version"))
    db.session.commit()
    response = client.get("/health")
    assert response.status_code == 503 and by_name(response.get_json())["migrations"]["status"] == health.ERROR


def test_old_revision_is_an_error(app):
    db.session.execute(text("UPDATE alembic_version SET version_num = 'ancienne'"))
    db.session.commit()
    assert "ancienne" in by_name(health.report())["migrations"]["message"]


def test_scheduler_states(app):
    assert health.check_scheduler()["status"] == health.WARN
    now = datetime.now()
    health.record_scheduler_run(now)
    assert health.check_scheduler(now)["status"] == health.OK
    assert health.check_scheduler(now + health.SCHEDULER_MAX_AGE + timedelta(minutes=1))["status"] == health.WARN


def test_corrupted_heartbeat_counts_as_never(app):
    health._heartbeat_path().write_text("n'importe quoi")
    assert health.last_scheduler_run() is None


def test_email_states(app, accounts):
    assert health.check_email()["status"] == health.WARN
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="a@b.c")
    assert health.check_email()["status"] == health.OK
    db.session.add_all(Notification(account_id=accounts[Role.ADHERENT].id, message=str(n)) for n in range(21))
    db.session.commit()
    assert "21 emails" in health.check_email()["message"]


def test_storage_error_is_reported(app, tmp_path):
    blocked = tmp_path / "fichier"
    blocked.write_text("pas un dossier")
    app.config["UPLOAD_DIR"] = blocked / "sous-dossier"
    assert health.check_storage()["status"] == health.ERROR


def test_overall_status_is_the_worst(app):
    report = health.report()
    assert report["status"] == health.WARN  # planificateur jamais passé, pas de SMTP
    db.session.execute(text("DELETE FROM alembic_version"))
    assert health.report()["status"] == health.ERROR
