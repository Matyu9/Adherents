import contextlib
import os
from pathlib import Path

from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import UNSAFE_SECRETS, Config, startup_warnings
from .errors import register_error_handlers
from .extensions import csrf, db, login_manager, migrate
from .services import branding

__version__ = "0.5.0"

# Aucun script ni style en ligne : les comportements passent par app.js (data-confirm, data-autosubmit…).
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'; report-uri /csp-report; report-to csp-endpoint"
)
# Pages qui affichent ou reçoivent des données de compte : jamais gardées par le navigateur ou un proxy.
NO_STORE = (
    "auth.login",
    "auth.password",
    "auth.two_factor",
    "auth.forgot",
    "auth.token_link",
    "account.index",
    "account.change_email",
    "account.totp",
    "account.totp_codes",
    "account.totp_disable",
    "accounts.create",
    "accounts.invite",
    "accounts.detail",
    "oidc.authorize",
    "oidc.resume",
    "oidc.consent",
    "oidc.end_session",
    "oidc.end_session_confirm",
    "applications.create",
    "applications.new_secret",
    "applications.export_accounts",
    "oidc.directory",
)


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    if app.config["SECRET_KEY"] in ("", *UNSAFE_SECRETS) and not (
        app.debug or app.testing or os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")
    ):
        raise RuntimeError(
            "SECRET_KEY n'est pas définie (ou vaut encore la valeur d'exemple) : "
            "renseignez-la dans le fichier .env avant de démarrer."
        )
    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
    for warning in startup_warnings(app.config):
        app.logger.warning(warning)

    db.init_app(app)
    migrate.init_app(app, db)
    from . import models  # noqa: F401  (enregistre les modèles pour Alembic)
    from .services import bank

    bank.check_startup(app)  # IBAN en base sans leur clé : on refuse de démarrer
    csrf.init_app(app)
    login_manager.init_app(app)

    from .blueprints.account import bp as account_bp
    from .blueprints.accounts import bp as accounts_bp
    from .blueprints.applications import bp as applications_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.campaigns import bp as campaigns_bp
    from .blueprints.catalog import bp as catalog_bp
    from .blueprints.csp import bp as csp_bp
    from .blueprints.dashboard import bp as dashboard_bp
    from .blueprints.directory import bp as directory_bp
    from .blueprints.enrollment import bp as enrollment_bp
    from .blueprints.fees import bp as fees_bp
    from .blueprints.groups import bp as groups_bp
    from .blueprints.health import bp as health_bp
    from .blueprints.journal import bp as journal_bp
    from .blueprints.member import bp as member_bp
    from .blueprints.notifications import bp as notifications_bp
    from .blueprints.oidc import bp as oidc_bp
    from .blueprints.retention import bp as retention_bp
    from .blueprints.roles import bp as roles_bp
    from .blueprints.settings import bp as settings_bp
    from .blueprints.teachers import bp as teachers_bp
    from .cli import register_cli
    from .filters import register_filters
    from .permissions import can
    from .services import logout, oidc_server
    from .services.notify import unread_count

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(directory_bp)
    app.register_blueprint(teachers_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(fees_bp)
    app.register_blueprint(campaigns_bp)
    app.register_blueprint(enrollment_bp)
    app.register_blueprint(retention_bp)
    app.register_blueprint(member_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(groups_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(journal_bp)
    app.register_blueprint(roles_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(csp_bp)
    app.register_blueprint(oidc_bp)
    app.register_blueprint(applications_bp)
    oidc_server.init_app(app)
    logout.init_app(app)
    register_cli(app)
    register_filters(app)

    @app.url_defaults
    def static_version(endpoint, values):
        """?v=<date du fichier> sur les fichiers statiques : après une mise à jour, le navigateur charge la
        nouvelle version au lieu de celle de son cache."""
        if endpoint == "static" and "filename" in values and "v" not in values:
            with contextlib.suppress(OSError):
                values["v"] = int((Path(app.static_folder) / values["filename"]).stat().st_mtime)

    @app.context_processor
    def inject_globals():
        brand = branding.current()
        return {"can": can, "branding": brand, "asso_name": brand.name, "unread_notifications": unread_count}

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        # Aucune autre origine ne peut garder une référence à nos fenêtres, ni charger nos réponses.
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        # Espace privé : rien à indexer, même si une adresse fuit.
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        if request.is_secure:  # en HTTPS (derrière le proxy : TRUST_PROXY=1), le navigateur n'essaiera plus HTTP
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        # Une réponse qui pose sa propre CSP la garde telle quelle.
        if "Content-Security-Policy" not in response.headers:
            response.headers.setdefault(
                "Content-Security-Policy-Report-Only" if app.config["CSP_REPORT_ONLY"] else "Content-Security-Policy",
                CSP,
            )
            response.headers.setdefault("Reporting-Endpoints", 'csp-endpoint="/csp-report"')  # pour report-to
        if request.endpoint in NO_STORE:
            response.headers["Cache-Control"] = "no-store"
        return response

    register_error_handlers(app)

    return app
