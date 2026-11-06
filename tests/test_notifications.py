from app.extensions import db
from app.models import Account, Notification, Role
from app.services.notify import admins, notify

from .conftest import login, make_account


def test_only_my_notifications_are_shown(app, accounts):
    db.session.add(Notification(account_id=accounts[Role.ADMIN].id, message="Pour l'admin"))
    db.session.add(Notification(account_id=accounts[Role.ADHERENT].id, message="Pour l'adhérent"))
    db.session.commit()
    client = app.test_client()
    login(client, accounts[Role.ADHERENT])
    html = client.get("/notifications").get_data(as_text=True)
    assert "Pour l&#39;adhérent" in html and "Pour l&#39;admin" not in html


def test_badge_counts_my_unread_only(client, member, accounts):
    db.session.add_all([Notification(account_id=member.id, message="1"), Notification(account_id=member.id, message="2"),
                        Notification(account_id=member.id, message="lue", read=True),
                        Notification(account_id=accounts[Role.ADMIN].id, message="autre")])
    db.session.commit()
    assert '<span class="bell-count">2</span>' in client.get("/").get_data(as_text=True)


def test_mark_all_read_touches_only_mine(client, member, accounts):
    other = Notification(account_id=accounts[Role.ADMIN].id, message="autre")
    mine = Notification(account_id=member.id, message="moi")
    db.session.add_all([other, mine])
    db.session.commit()
    client.post("/notifications/tout-lu")
    assert db.session.get(Notification, mine.id).read and not db.session.get(Notification, other.id).read
    assert "bell-count" not in client.get("/").get_data(as_text=True)


def test_empty_state(client, member):
    html = client.get("/notifications").get_data(as_text=True)
    assert "Aucune notification" in html and "Tout marquer" not in html


def test_notify_is_idempotent_with_a_dedup_key(accounts):
    people = [accounts[Role.ADMIN], accounts[Role.ADHERENT]]
    assert len(notify(people, "Relance", dedup_key="relance:2026")) == 2
    db.session.commit()
    assert notify(people, "Relance", dedup_key="relance:2026") == []
    assert len(notify(people, "Relance", dedup_key="relance:2027")) == 2


def test_notify_skips_none_and_duplicates_and_cuts_long_messages(accounts):
    person = accounts[Role.ADHERENT]
    created = notify([person, None, person], "x" * 600)
    assert len(created) == 1 and len(created[0].message) == 500


def test_admins_are_the_active_ones(accounts):
    make_account(Role.ADMIN, email="ancien@asso.test", active=False)
    assert [a.email for a in admins()] == ["admin@asso.test"]
    assert isinstance(admins()[0], Account)
