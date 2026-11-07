import os
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_SECRET = "dev-secret-change-me"
# Valeurs connues de tous (défaut du code, modèle .env.example) : refusées en production.
UNSAFE_SECRETS = (DEFAULT_SECRET, "change-moi")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or DEFAULT_SECRET
    DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'adherents.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_DIR = DATA_DIR / "uploads"
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001").rstrip("/")
    # Identité du serveur OIDC, inscrite dans les jetons : la changer déconnecte ce serveur de toutes les applis.
    OIDC_ISSUER = (os.environ.get("OIDC_ISSUER") or BASE_URL).rstrip("/")
    OAUTH2_TOKEN_EXPIRES_IN = {"authorization_code": 3600}
    # Derrière un reverse proxy (Caddy, Traefik, nginx) : faire confiance à X-Forwarded-*.
    TRUST_PROXY = os.environ.get("TRUST_PROXY", "0") == "1"
    # Noms propres à l'appli : sur localhost, les cookies ne sont pas isolés par port, et ceux de l'Inventaire
    # écraseraient ceux-ci (la connexion OIDC entre les deux applis échouerait).
    SESSION_COOKIE_NAME = "adherents_session"
    REMEMBER_COOKIE_NAME = "adherents_remember"
    SESSION_COOKIE_SAMESITE = "Lax"
    # Jeton CSRF valable toute la session (et non 1 h) : un dossier d'inscription reste ouvert longtemps sur un
    # téléphone, il ne doit pas échouer à l'envoi. Il reste lié à la session et protégé par SameSite.
    WTF_CSRF_TIME_LIMIT = None
    SESSION_COOKIE_SECURE = BASE_URL.startswith("https://")
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    ASSO_NAME = os.environ.get("ASSO_NAME", "Mon association")

    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "")
    SMTP_TLS = os.environ.get("SMTP_TLS", "true").lower() == "true"

    # Envoi hors de la requête, là où sa durée révélerait si un compte existe (mot de passe oublié).
    MAIL_IN_BACKGROUND = True
    # Avis de déconnexion aux applis : envoyés juste après la requête, sans la faire attendre.
    BACKCHANNEL_IN_BACKGROUND = True

    # Content-Security-Policy : appliquée par défaut. « 1 » = Report-Only, pour diagnostiquer sans rien bloquer.
    CSP_REPORT_ONLY = os.environ.get("CSP_REPORT_ONLY", "0") == "1"


def startup_warnings(config):
    """Réglages incohérents qui n'empêchent pas de démarrer mais méritent d'être signalés dans les journaux."""
    warnings = []
    public = (config.get("BASE_URL") or "", config.get("OIDC_ISSUER") or "")
    if any(url.startswith("https://") for url in public) and not config.get("TRUST_PROXY"):
        warnings.append("BASE_URL ou OIDC_ISSUER est en https:// mais TRUST_PROXY n'est pas à 1 : derrière un reverse proxy, "
                        "l'adresse des visiteurs et le protocole (cookies Secure, redirections) dépendent des "
                        "en-têtes X-Forwarded-*, ignorés sans TRUST_PROXY=1.")
    issuer = urlsplit(config.get("OIDC_ISSUER") or "https://non-renseigne")
    if issuer.scheme != "https" and issuer.hostname not in ("localhost", "127.0.0.1", "::1"):
        warnings.append("OIDC_ISSUER n'est pas en https:// : les connexions OIDC seront refusées, car les applis "
                        "clientes transmettraient leurs secrets et recevraient les jetons en clair.")
    return warnings


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    MAIL_IN_BACKGROUND = False
    BACKCHANNEL_IN_BACKGROUND = False
