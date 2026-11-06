from datetime import datetime, timedelta

from app.extensions import db
from app.models import ThrottleEvent
from app.services import tasks, throttle


def count():
    return db.session.scalar(db.select(db.func.count(ThrottleEvent.id)))


def test_blocks_at_the_limit(app):
    limiter = throttle.Limiter("essai", 2, 60)
    assert not limiter.blocked(["a"])
    limiter.hit(["a"])
    assert not limiter.blocked(["a"])
    limiter.hit(["a"])
    assert limiter.blocked(["a"])
    assert not limiter.blocked(["b"])


def test_blocked_if_any_key_is(app):
    limiter = throttle.Limiter("essai", 1, 60)
    limiter.hit(["a"])
    assert limiter.blocked(["z", "a"])


def test_two_instances_share_the_count_like_two_workers(app):
    throttle.Limiter("essai", 2, 60).hit(["a"])
    throttle.Limiter("essai", 2, 60).hit(["a"])
    assert throttle.Limiter("essai", 2, 60).blocked(["a"])


def test_limiters_do_not_mix(app):
    throttle.Limiter("un", 1, 60).hit(["a"])
    assert not throttle.Limiter("deux", 1, 60).blocked(["a"])


def test_clear_only_removes_those_keys(app):
    limiter = throttle.Limiter("essai", 1, 60)
    limiter.hit(["a", "b"])
    limiter.clear(["a"])
    assert not limiter.blocked(["a"]) and limiter.blocked(["b"])


def test_window_expires(app):
    limiter = throttle.Limiter("essai", 1, 60)
    db.session.add(ThrottleEvent(key_hash=limiter._digest("a"), created_at=datetime.now() - timedelta(seconds=61)))
    assert not limiter.blocked(["a"])


def test_digest_depends_on_secret_key(app):
    limiter = throttle.Limiter("essai", 1, 60)
    first = limiter._digest("a@b.c")
    app.config["SECRET_KEY"] = "une-autre-cle"
    assert limiter._digest("a@b.c") != first and "a@b.c" not in first


def test_purge_keeps_recent_events(app):
    now = datetime.now()
    db.session.add_all([ThrottleEvent(key_hash="vieux", created_at=now - throttle.MAX_WINDOW - timedelta(minutes=1)),
                        ThrottleEvent(key_hash="recent", created_at=now - timedelta(minutes=1))])
    assert throttle.purge(now) == 1
    assert db.session.scalars(db.select(ThrottleEvent.key_hash)).all() == ["recent"]


def test_periodic_task_purges_and_commits(app):
    db.session.add(ThrottleEvent(key_hash="vieux", created_at=datetime.now() - timedelta(days=2)))
    db.session.commit()
    assert "1 tentative(s)" in tasks.run()
    db.session.rollback()
    assert count() == 0
