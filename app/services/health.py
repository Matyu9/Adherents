"""État de l'installation (page /sante et /health) : base, migrations, disque, planificateur, emails."""
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask import current_app
from sqlalchemy import text

from ..extensions import db
from ..models import Account, Notification
from . import mailer

OK, WARN, ERROR = "ok", "warn", "error"
MIN_FREE_BYTES = 1024 ** 3
SCHEDULER_MAX_AGE = timedelta(hours=1)
HEARTBEAT_FILE = "scheduler.last"


def _check(name, label, status, message):
    return {"name": name, "label": label, "status": status, "message": message}


def _size(n):
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024


def _heartbeat_path():
    return Path(current_app.config["DATA_DIR"]) / HEARTBEAT_FILE


def record_scheduler_run(now=None):
    """Appelé par `flask run-tasks` : la page de santé vérifie que le planificateur tourne."""
    path = _heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((now or datetime.now()).isoformat(timespec="seconds"))


def last_scheduler_run():
    try:
        return datetime.fromisoformat(_heartbeat_path().read_text().strip())
    except (OSError, ValueError):
        return None


def check_database():
    try:
        version = db.session.execute(text("SHOW server_version")).scalar()
        size = db.session.execute(text("SELECT pg_database_size(current_database())")).scalar()
        unaccent = db.session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'unaccent'")).scalar()
    except Exception as exc:  # noqa: BLE001
        return _check("database", "Base de données", ERROR, f"Inaccessible : {exc}")
    if not unaccent:
        return _check("database", "Base de données", ERROR, "Extension unaccent absente : la recherche sans accents "
                      "ne marche pas (flask db upgrade).")
    return _check("database", "Base de données", OK, f"PostgreSQL {version}, {_size(size)}")


def migration_heads():
    """(révisions appliquées, dernières révisions disponibles)."""
    directory = Path(current_app.extensions["migrate"].directory)
    if not directory.is_absolute():  # « migrations », à côté du paquet app (pas du dossier courant)
        directory = Path(current_app.root_path).parent / directory
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(directory))
    heads = set(ScriptDirectory.from_config(cfg).get_heads())
    current = set(MigrationContext.configure(db.session.connection()).get_current_heads())
    return current, heads


def check_migrations():
    try:
        current, heads = migration_heads()
    except Exception as exc:  # noqa: BLE001
        return _check("migrations", "Migrations", ERROR, f"Impossible de lire la version de la base : {exc}")
    if current == heads:
        return _check("migrations", "Migrations", OK, f"À jour ({', '.join(sorted(heads))})")
    if not current:
        return _check("migrations", "Migrations", ERROR, "La base n'a jamais été migrée (flask db upgrade).")
    return _check("migrations", "Migrations", ERROR,
                  f"Base en version {', '.join(sorted(current))}, attendue {', '.join(sorted(heads))} "
                  "(flask db upgrade).")


def check_storage():
    folder = Path(current_app.config["UPLOAD_DIR"])
    probe = folder / ".sante"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return _check("storage", "Fichiers envoyés", ERROR, f"Dossier {folder} non accessible en écriture : {exc}")
    free = shutil.disk_usage(folder).free
    if free < MIN_FREE_BYTES:
        return _check("storage", "Fichiers envoyés", WARN, f"Espace disque faible : {_size(free)} libres")
    return _check("storage", "Fichiers envoyés", OK, f"Dossier accessible, {_size(free)} libres")


def check_permissions():
    """Le dossier des données (logo, fichiers envoyés, marqueur du planificateur) ne doit pas être lisible par les
    autres comptes du serveur. La base, elle, est protégée par PostgreSQL."""
    import os
    import stat

    if os.name != "posix":
        return _check("permissions", "Droits des fichiers", OK, "Non vérifiés sur ce système")
    paths = [Path(current_app.config["DATA_DIR"])]
    exposed = []
    for path in paths:
        try:
            mode = path.stat().st_mode
        except OSError:
            continue
        if mode & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
            exposed.append(f"{path} ({stat.filemode(mode)})")
    if exposed:
        return _check("permissions", "Droits des fichiers", WARN,
                      "Accessibles à tous les comptes du serveur : " + ", ".join(exposed)
                      + ". Restreignez-les (chmod 700).")
    return _check("permissions", "Droits des fichiers", OK, "Dossier des données réservé à l'appli")


def check_scheduler(now=None):
    last = last_scheduler_run()
    if last is None:
        return _check("scheduler", "Tâches périodiques", WARN,
                      "Le planificateur n'est jamais passé (service scheduler ou tâche cron absent).")
    age = (now or datetime.now()) - last
    when = f"{last:%d/%m/%Y à %H:%M}"
    if age > SCHEDULER_MAX_AGE:
        return _check("scheduler", "Tâches périodiques", WARN, f"Dernier passage le {when} : le planificateur "
                      "semble arrêté.")
    return _check("scheduler", "Tâches périodiques", OK, f"Dernier passage le {when}")


def check_email():
    if not mailer.smtp_enabled():
        return _check("email", "Emails", WARN, "SMTP non configuré : les notifications restent dans l'appli.")
    since = datetime.now() - mailer.MAX_AGE
    pending = db.session.scalar(
        db.select(db.func.count(Notification.id)).join(Account).where(
            Notification.emailed.is_(False), Notification.created_at >= since, Account.active.is_(True)))
    if pending > 20:
        return _check("email", "Emails", WARN, f"SMTP configuré, {pending} emails en attente d'envoi")
    return _check("email", "Emails", OK, f"SMTP configuré ({current_app.config['SMTP_HOST']}), "
                  f"{pending} email{'s' if pending > 1 else ''} en attente")


def report(now=None):
    from .. import __version__

    checks = [check_database(), check_migrations(), check_storage(), check_permissions(), check_scheduler(now),
              check_email()]
    statuses = {c["status"] for c in checks}
    status = ERROR if ERROR in statuses else WARN if WARN in statuses else OK
    return {"status": status, "version": __version__,
            "time": (now or datetime.now()).isoformat(timespec="seconds"), "checks": checks}
