import os
from pathlib import Path

from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import UNSAFE_SECRETS, Config, startup_warnings
from .errors import register_error_handlers
from .extensions import csrf, db, login_manager, migrate
from .services import branding

__version__ = "0.1.0"

# Aucun script ni style en ligne : les comportements passent par app.js (data-confirm, data-autosubmit…).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'; report-uri /csp-report; report-to csp-endpoint")
# Pages qui affichent ou reçoivent des données de compte : jamais gardées par le navigateur ou un proxy.
NO_STORE = ("auth.login", "auth.password", "account.index")


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    if app.config["SECRET_KEY"] in ("", *UNSAFE_SECRETS) and not (app.debug or app.testing or os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")):
        raise RuntimeError("SECRET_KEY n'est pas définie (ou vaut encore la valeur d'exemple) : "
                           "renseignez-la dans le fichier .env avant de démarrer.")
    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
    for warning in startup_warnings(app.config):
        app.logger.warning(warning)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)

    from . import models  # noqa: F401  (enregistre les modèles pour Alembic)
    from .blueprints.account import bp as account_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.csp import bp as csp_bp
    from .blueprints.dashboard import bp as dashboard_bp
    from .blueprints.health import bp as health_bp
    from .blueprints.journal import bp as journal_bp
    from .blueprints.notifications import bp as notifications_bp
    from .blueprints.roles import bp as roles_bp
    from .blueprints.settings import bp as settings_bp
    from .cli import register_cli
    from .filters import register_filters
    from .permissions import can
    from .services.notify import unread_count

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(journal_bp)
    app.register_blueprint(roles_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(csp_bp)
    register_cli(app)
    register_filters(app)

    @app.url_defaults
    def static_version(endpoint, values):
        """?v=<date du fichier> sur les fichiers statiques : après une mise à jour, le navigateur charge la
        nouvelle version au lieu de celle de son cache."""
        if endpoint == "static" and "filename" in values and "v" not in values:
            try:
                values["v"] = int((Path(app.static_folder) / values["filename"]).stat().st_mtime)
            except OSError:
                pass

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
        # Une réponse qui pose sa propre CSP la garde telle quelle.
        if "Content-Security-Policy" not in response.headers:
            response.headers.setdefault("Content-Security-Policy-Report-Only" if app.config["CSP_REPORT_ONLY"]
                                        else "Content-Security-Policy", CSP)
            response.headers.setdefault("Reporting-Endpoints", 'csp-endpoint="/csp-report"')  # pour report-to
        if request.endpoint in NO_STORE:
            response.headers["Cache-Control"] = "no-store"
        return response

    register_error_handlers(app)

    return app
