import smtplib
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Notification, Role
from app.services import branding, mailer, tasks

from .conftest import make_account


class FakeSMTP:
    sent, fail = [], None

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.started_tls, self.credentials = False, None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self):
        FakeSMTP.tls = True

    def login(self, user, password):
        FakeSMTP.credentials = (user, password)

    def send_message(self, message):
        if FakeSMTP.fail:
            raise FakeSMTP.fail
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(app, monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail, FakeSMTP.tls, FakeSMTP.credentials = [], None, False, None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    app.config.update(
        SMTP_HOST="smtp.test", SMTP_FROM="asso@asso.test", SMTP_USER="u", SMTP_PASSWORD="p", SMTP_TLS=True
    )
    return FakeSMTP


def pending(account, message="Votre dossier est incomplet", **fields):
    notification = Notification(account_id=account.id, message=message, **fields)
    db.session.add(notification)
    db.session.commit()
    return notification


def test_nothing_is_sent_without_smtp(accounts):
    notification = pending(accounts[Role.ADHERENT])
    assert not mailer.smtp_enabled() and mailer.deliver_pending() == 0
    assert not db.session.get(Notification, notification.id).emailed


def test_delivery_marks_as_sent_and_is_not_repeated(accounts, smtp):
    notification = pending(accounts[Role.ADHERENT], link="/notifications")
    assert mailer.deliver_pending() == 1
    assert db.session.get(Notification, notification.id).emailed
    assert mailer.deliver_pending() == 0 and len(smtp.sent) == 1
    message = smtp.sent[0]
    assert message["To"] == "adherent@asso.test" and "Votre dossier" in message["Subject"]
    assert smtp.tls and smtp.credentials == ("u", "p")


def test_email_has_text_and_html_with_absolute_link(app, accounts, smtp):
    pending(accounts[Role.ADHERENT], link="/notifications")
    mailer.deliver_pending()
    message = smtp.sent[0]
    text = message.get_body(("plain",)).get_content()
    html = message.get_body(("html",)).get_content()
    url = app.config["BASE_URL"] + "/notifications"
    assert url in text and url in html and "Bonjour Adherent" in text and "Inventaire" not in html + text


def test_email_escapes_html_and_header_injection(accounts, smtp):
    pending(accounts[Role.ADHERENT], message="<b>Gras</b>\nBcc: pirate@evil.test")
    mailer.deliver_pending()
    message = smtp.sent[0]
    assert "<b>Gras" not in message.get_body(("html",)).get_content()
    assert message["Bcc"] is None and "\n" not in message["Subject"]


def test_email_uses_the_palette_and_name(accounts, smtp):
    branding.save("Harmonie", "", "vert")
    db.session.commit()
    pending(accounts[Role.ADHERENT])
    mailer.deliver_pending()
    assert "Harmonie" in smtp.sent[0]["Subject"]
    assert branding.PALETTES["vert"][1] in smtp.sent[0].get_body(("html",)).get_content()


def test_old_notifications_and_inactive_accounts_are_skipped(accounts, smtp):
    old = pending(accounts[Role.ADHERENT], created_at=datetime.now() - mailer.MAX_AGE - timedelta(hours=1))
    gone = make_account(email="parti@asso.test", active=False)
    pending(gone)
    assert mailer.deliver_pending() == 0 and not db.session.get(Notification, old.id).emailed


def test_failure_stops_and_retries_next_time(accounts, smtp):
    first = pending(accounts[Role.ADHERENT], message="un")
    second = pending(accounts[Role.ADHERENT], message="deux")
    smtp.fail = OSError("serveur indisponible")
    assert mailer.deliver_pending() == 0
    assert not db.session.get(Notification, first.id).emailed
    smtp.fail = None
    assert mailer.deliver_pending() == 2
    assert db.session.get(Notification, second.id).emailed


def test_deliver_quietly_never_raises(accounts, smtp, monkeypatch):
    monkeypatch.setattr(mailer, "deliver_pending", lambda limit: 1 / 0)
    assert mailer.deliver_quietly() == 0


def test_periodic_task_sends_emails_outside_of_a_request(app, accounts, smtp):
    pending(accounts[Role.ADHERENT])
    branding._set("logo", "logo/" + "a" * 32 + ".png")
    db.session.commit()
    assert "1 email(s)" in tasks.run()


def test_test_email_route(client, admin, smtp):
    assert "Email de test envoyé" in client.post("/parametres/email-test", follow_redirects=True).get_data(as_text=True)
    assert smtp.sent[0]["To"] == admin.email


def test_test_email_route_reports_failures(client, admin, smtp):
    smtp.fail = OSError("refusé")
    assert "Échec de l&#39;envoi" in client.post("/parametres/email-test", follow_redirects=True).get_data(as_text=True)


def test_long_subject_is_cut():
    assert len(mailer._shorten("x" * 300)) == 100
    assert mailer._shorten("court") == "court"
