"""Collecte des rapports CSP : publique, sans CSRF, limitée en débit et en taille, journalisée sans rien stocker."""

import json

import pytest

from app.blueprints import csp
from app.extensions import db
from app.models import Activity, ThrottleEvent

VIOLATION = {
    "csp-report": {
        "document-uri": "https://adherents.test/mon-compte",
        "violated-directive": "script-src-attr",
        "effective-directive": "script-src-attr",
        "blocked-uri": "inline",
        "source-file": "https://adherents.test/mon-compte",
        "line-number": 12,
        "disposition": "enforce",
    }
}


def post(client, payload, mimetype="application/csp-report", **kwargs):
    data = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    return client.post("/csp-report", data=data, content_type=mimetype, **kwargs)


def logs(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Violation CSP")]


def test_report_uri_is_logged(client, caplog):
    with caplog.at_level("WARNING"):
        assert post(client, VIOLATION).status_code == 204
    assert "directive=script-src-attr" in logs(caplog)[0] and "ligne=12" in logs(caplog)[0]


def test_report_to_is_logged(client, caplog):
    batch = [
        {"type": "csp-violation", "body": {"effectiveDirective": "style-src", "blockedURL": "inline"}},
        {"type": "autre", "body": {}},
    ]
    with caplog.at_level("WARNING"):
        assert post(client, batch, "application/reports+json").status_code == 204
    assert len(logs(caplog)) == 1 and "directive=style-src" in logs(caplog)[0]


def test_nothing_is_kept_in_the_journal(client):
    post(client, VIOLATION)
    assert db.session.scalar(db.select(db.func.count(Activity.id))) == 0


def test_works_without_csrf_token(tmp_path):
    from app import create_app

    from .conftest import config_for, release, reset_database

    app = create_app(config_for(tmp_path, WTF_CSRF_ENABLED=True))
    with app.app_context():
        reset_database()
        assert post(app.test_client(), VIOLATION).status_code == 204
        release()


def test_log_injection_is_neutralised(client, caplog):
    report = {"csp-report": {"blocked-uri": "x\nFAUSSE LIGNE\r\n", "effective-directive": "a" * 5000}}
    with caplog.at_level("WARNING"):
        post(client, report)
    line = logs(caplog)[0]
    assert "\n" not in line and "\r" not in line and len(line) < csp.MAX_FIELD * 2 + 100


@pytest.mark.parametrize(
    "payload, mimetype, code",
    [
        ("pas du json", "application/csp-report", 400),
        (VIOLATION, "text/plain", 415),
        ([1, 2], "application/csp-report", 204),
        ({"csp-report": "texte"}, "application/csp-report", 204),
        ({"autre": {}}, "application/json", 204),
        ("[{}]", "application/reports+json", 204),
        ('{"a": 1}', "application/reports+json", 204),
    ],
)
def test_odd_bodies(client, payload, mimetype, code):
    assert post(client, payload, mimetype).status_code == code


def test_body_too_large(client):
    assert post(client, b"x" * (csp.MAX_BODY + 1)).status_code == 413


def test_batch_size_is_bounded(client, caplog):
    batch = [{"type": "csp-violation", "body": {"effectiveDirective": "x"}}] * (csp.MAX_REPORTS + 5)
    with caplog.at_level("WARNING"):
        post(client, batch, "application/reports+json")
    assert len(logs(caplog)) == csp.MAX_REPORTS


def test_rate_limit_is_per_address_and_committed(app, client):
    for _ in range(csp._by_ip.limit):
        assert post(client, VIOLATION).status_code == 204
    assert post(client, VIOLATION).status_code == 429
    assert post(client, VIOLATION, environ_overrides={"REMOTE_ADDR": "10.1.1.1"}).status_code == 204
    assert db.session.scalar(db.select(db.func.count(ThrottleEvent.id))) == csp._by_ip.limit + 1


def test_get_is_not_allowed(client):
    assert client.get("/csp-report").status_code == 405
