"""En-têtes, CSP bloquante, CSRF, secret de production, feuilles de style."""
import re
from pathlib import Path

import pytest

from app import CSP, create_app
from app.config import Config, startup_warnings
from app.extensions import db

from .conftest import TEST_DATABASE_URL, config_for, make_account, release, reset_database

TEMPLATES = Path("app/templates")
PAGES = [p for p in TEMPLATES.rglob("*.html") if "emails" not in p.parts]


def test_csp_blocks_by_default(client):
    headers = client.get("/connexion").headers
    assert headers["Content-Security-Policy"] == CSP and "Content-Security-Policy-Report-Only" not in headers
    assert headers["Reporting-Endpoints"] == 'csp-endpoint="/csp-report"'


def test_default_configuration_is_blocking():
    assert Config.CSP_REPORT_ONLY is False


def test_csp_allows_nothing_inline_or_external():
    assert "unsafe" not in CSP and "http" not in CSP and "*" not in CSP
    for directive in ("default-src 'self'", "script-src 'self'", "style-src 'self'", "object-src 'none'",
                      "base-uri 'none'", "frame-ancestors 'none'", "form-action 'self'"):
        assert directive in CSP


def test_report_only_mode_for_diagnosis(tmp_path):
    app = create_app(config_for(tmp_path, CSP_REPORT_ONLY=True))
    with app.app_context():
        reset_database()
        headers = app.test_client().get("/connexion").headers
        assert "Content-Security-Policy" not in headers and headers["Content-Security-Policy-Report-Only"] == CSP
        release()


def test_error_pages_have_the_headers_too(client):
    assert "Content-Security-Policy" in client.get("/nexiste-pas").headers


@pytest.mark.parametrize("path", [p.relative_to(TEMPLATES).as_posix() for p in PAGES])
def test_template_has_nothing_the_csp_would_block(path):
    html = (TEMPLATES / path).read_text()
    assert not re.search(r"\son[a-z]+\s*=\s*[\"']", html), "gestionnaire JavaScript en ligne"
    assert not re.search(r"\sstyle\s*=|<style\b", html), "style en ligne"
    assert not re.search(r"<script\b(?![^>]*\ssrc=)", html), "script en ligne"
    assert not re.search(r"https?://", re.sub(r"\{#.*?#\}", "", html, flags=re.S)), "ressource externe"


def test_scripts_have_nothing_the_csp_would_block():
    js = Path("app/static/js/app.js").read_text()
    assert not re.search(r"\beval\(|new Function|\.innerHTML\s*=|\.style\.|setAttribute\(\s*[\"']style", js)


def test_htmx_is_configured_for_the_csp(client):
    html = client.get("/connexion").get_data(as_text=True)
    assert '"includeIndicatorStyles": false' in html and '"allowEval": false' in html


def test_security_headers(client):
    headers = client.get("/connexion").headers
    assert headers["X-Content-Type-Options"] == "nosniff" and headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "same-origin" and "camera=()" in headers["Permissions-Policy"]


@pytest.mark.parametrize("path", ["/connexion", "/mon-compte", "/mot-de-passe"])
def test_account_pages_are_never_cached(client, admin, path):
    client.post("/deconnexion") if path == "/connexion" else None
    assert client.get(path).headers.get("Cache-Control") == "no-store"


def test_static_files_are_versioned(client):
    assert re.search(r"/static/css/app\.css\?v=\d+", client.get("/connexion").get_data(as_text=True))


def test_dark_theme_is_written_only_once():
    """La duplication du bloc sombre, source d'oublis dans l'Inventaire, ne doit pas revenir."""
    app_css = Path("app/static/css/app.css").read_text()
    dark = Path("app/static/css/dark.css").read_text()
    assert "prefers-color-scheme" not in app_css and 'data-theme="dark"' not in app_css
    assert "prefers-color-scheme" not in dark and dark.count("--brand:") == 7 + 1


def test_every_light_token_has_its_dark_value():
    light = Path("app/static/css/app.css").read_text().split("/* ---")[0]
    dark = Path("app/static/css/dark.css").read_text()
    colors = set(re.findall(r"(--[\w-]+):\s*#[0-9a-fA-F]+", light))
    missing = {token for token in colors if f"{token}:" not in dark}
    assert not missing


# --- CSRF ---------------------------------------------------------------------------------------------------

@pytest.fixture
def csrf_app(tmp_path):
    app = create_app(config_for(tmp_path, WTF_CSRF_ENABLED=True))
    with app.app_context():
        reset_database()
        yield app
        release()


def token_of(html):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)


def test_post_without_token_is_refused_with_a_clear_page(csrf_app):
    make_account(email="a@asso.test")
    response = csrf_app.test_client().post("/connexion", data={"email": "a@asso.test", "password": "motdepasse"})
    assert response.status_code == 400 and "Page expirée" in response.get_data(as_text=True)


def test_post_with_token_goes_through(csrf_app):
    make_account(email="a@asso.test")
    client = csrf_app.test_client()
    token = token_of(client.get("/connexion").get_data(as_text=True))
    response = client.post("/connexion", data={"email": "a@asso.test", "password": "motdepasse", "csrf_token": token})
    assert response.status_code == 302


def test_csrf_token_does_not_expire_during_a_long_form():
    assert Config.WTF_CSRF_TIME_LIMIT is None


# --- Configuration ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("secret", ["", "dev-secret-change-me", "change-moi"])
def test_production_refuses_a_known_secret(tmp_path, monkeypatch, secret):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    config = type("Prod", (Config,), {"SECRET_KEY": secret, "DATA_DIR": tmp_path, "UPLOAD_DIR": tmp_path / "u",
                                      "SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL})
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(config)


def test_production_starts_with_a_real_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    config = type("Prod", (Config,), {"SECRET_KEY": "x" * 64, "DATA_DIR": tmp_path, "UPLOAD_DIR": tmp_path / "u",
                                      "SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL})
    assert create_app(config).config["SECRET_KEY"] == "x" * 64


def test_warning_when_issuer_is_not_https():
    assert startup_warnings({"OIDC_ISSUER": "http://compte.asso.test", "TRUST_PROXY": True})
    assert not startup_warnings({"OIDC_ISSUER": "https://compte.asso.test", "BASE_URL": "http://x", "TRUST_PROXY": True})
    assert not startup_warnings({"OIDC_ISSUER": "http://localhost:8001", "BASE_URL": "http://x"})


def test_warning_when_https_without_proxy():
    assert startup_warnings({"BASE_URL": "https://a.test", "TRUST_PROXY": False})
    assert not startup_warnings({"BASE_URL": "https://a.test", "TRUST_PROXY": True})
    assert not startup_warnings({"BASE_URL": "http://localhost", "TRUST_PROXY": False})


def test_proxy_headers_are_ignored_by_default(client):
    client.post("/connexion", data={"email": "x@y.z", "password": "x"}, headers={"X-Forwarded-For": "1.2.3.4"})
    from app.models import Activity
    assert "1.2.3.4" not in db.session.scalar(db.select(Activity.message))


def test_proxy_headers_are_used_when_trusted(tmp_path):
    app = create_app(config_for(tmp_path, TRUST_PROXY=True))
    with app.app_context():
        reset_database()
        app.test_client().post("/connexion", data={"email": "x@y.z", "password": "x"},
                               headers={"X-Forwarded-For": "1.2.3.4"})
        from app.models import Activity
        assert "1.2.3.4" in db.session.scalar(db.select(Activity.message))
        release()


def test_docker_listens_locally_by_default():
    compose = Path("docker-compose.yml").read_text()
    assert '"${BIND_ADDR:-127.0.0.1}:8001:8000"' in compose and "flask run-tasks" in compose
    assert "pupitre" not in compose


def test_secrets_stay_out_of_git():
    ignored = Path(".gitignore").read_text().splitlines()
    assert {".env", "data/"} <= set(ignored)
