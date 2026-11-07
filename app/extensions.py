import unicodedata

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()
migrate = Migrate(render_as_batch=True)
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Veuillez vous connecter pour accéder à cette page."
login_manager.login_message_category = "warning"


def fold(text):
    """Minuscules sans accents : « Flûte » → « flute ». Sert aux recherches et comparaisons de noms."""
    if text is None:
        return None
    decomposed = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def fold_contains(column, text):
    """Condition SQL « la colonne contient `text` », sans casse ni accents ; % et _ sont pris littéralement."""
    pattern = "%" + fold(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return db.func.fold(column).like(pattern, escape="\\")


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        # SQLite ne sait comparer sans casse que l'ASCII : on fournit fold() en SQL.
        dbapi_connection.create_function("fold", 1, fold, deterministic=True)
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()
        # pysqlite gère mal les transactions (un SAVEPOINT relâché hors BEGIN est validé) :
        # on désactive sa gestion implicite et SQLAlchemy émet BEGIN lui-même (recette officielle).
        dbapi_connection.isolation_level = None


@event.listens_for(Engine, "begin")
def _sqlite_begin(conn):
    if conn.dialect.name == "sqlite":
        # Une requête qui modifie des données prend le verrou d'écriture dès le début : deux
        # validations concurrentes (2 workers gunicorn) ne peuvent pas lire le même état puis
        # écrire chacune un résultat en conflit. Les lectures restent concurrentes (WAL).
        conn.exec_driver_sql("BEGIN IMMEDIATE" if _is_write_request() else "BEGIN")


def _is_write_request():
    from flask import has_request_context, request

    # Deux GET écrivent aussi : le code d'autorisation et le journal, la première clé de signature.
    return has_request_context() and (request.method in ("POST", "PUT", "PATCH", "DELETE")
                                      or request.endpoint in ("oidc.authorize", "oidc.jwks"))
