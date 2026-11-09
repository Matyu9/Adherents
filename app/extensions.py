import unicodedata

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import DDL, event
from sqlalchemy.engine import Engine

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Veuillez vous connecter pour accéder à cette page."
login_manager.login_message_category = "warning"

# Ligatures que unaccent (PostgreSQL) développe et que la décomposition Unicode laisse telles quelles.
_LIGATURES = str.maketrans({"œ": "oe", "Œ": "oe", "æ": "ae", "Æ": "ae", "ß": "ss", "ø": "o", "Ø": "o",
                            "đ": "d", "Đ": "d", "ł": "l", "Ł": "l", "ı": "i"})

# fold() côté base : la même règle que fold() en Python, pour comparer une colonne à un texte saisi.
# Créée par la migration initiale, et par create_all (tests) grâce à l'écouteur ci-dessous.
FOLD_SQL = (
    "CREATE EXTENSION IF NOT EXISTS unaccent",
    "CREATE OR REPLACE FUNCTION fold(text) RETURNS text LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE "
    "AS $$ SELECT lower(public.unaccent('public.unaccent'::regdictionary, $1)) $$",
)
for _statement in FOLD_SQL:
    event.listen(db.metadata, "before_create", DDL(_statement))

# Verrou d'écriture : une requête qui modifie des données le prend dès le début de sa transaction, pour que deux
# requêtes concurrentes (plusieurs workers gunicorn) ne lisent pas le même état puis écrivent chacune un résultat en
# conflit (code OIDC consommé deux fois, limite de tentatives dépassée…). Les lectures restent concurrentes.
WRITE_LOCK_KEY = 0x41444845  # « ADHE »


def fold(text):
    """Minuscules sans accents : « Flûte » → « flute », « Cœur » → « coeur ». Sert aux recherches et comparaisons."""
    if text is None:
        return None
    folded = str(text)
    for _ in range(3):  # une passe peut produire un caractère à replier à son tour (« İ » → « i » + point)
        decomposed = unicodedata.normalize("NFKD", folded.lower().translate(_LIGATURES))
        again = "".join(c for c in decomposed if not unicodedata.combining(c))
        if again == folded:
            break
        folded = again
    return folded


def fold_contains(column, text):
    """Condition SQL « la colonne contient `text` », sans casse ni accents ; % et _ sont pris littéralement."""
    pattern = "%" + fold(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return db.func.fold(column).like(pattern, escape="\\")


@event.listens_for(Engine, "begin")
def _write_lock(conn):
    if conn.dialect.name == "postgresql" and _is_write_request():
        conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({WRITE_LOCK_KEY})")


def _is_write_request():
    from flask import g, has_app_context, has_request_context, request

    if has_app_context() and g.get("write_lock"):  # travail hors requête qui écrit (avis de déconnexion)
        return True
    # Trois GET écrivent aussi : le code d'autorisation et le journal, la première clé de signature, la déconnexion.
    return has_request_context() and (request.method in ("POST", "PUT", "PATCH", "DELETE")
                                      or request.endpoint in ("oidc.authorize", "oidc.jwks", "oidc.end_session",
                                                              "oidc.directory"))
