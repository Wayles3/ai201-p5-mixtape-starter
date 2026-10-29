"""
tests/test_feed.py — Mixtape

Tests for the "Friends Listening Now" feed.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
import services.feed_service as feed_service
from services.feed_service import get_friends_listening_now

# Fixed "now" (09:00 UTC) so a listen at 23:00 the previous night is only 10h old,
# i.e. inside a rolling 24h window, whatever time the suite actually runs.
FROZEN_NOW = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return FROZEN_NOW if tz else FROZEN_NOW.replace(tzinfo=None)

    monkeypatch.setattr(feed_service, "datetime", FrozenDatetime)


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def ids(app):
    """Return ids for a viewer, two of their friends and a song."""
    with app.app_context():
        viewer = User(username="nova", email="nova@example.com")
        darius = User(username="darius", email="darius@example.com")
        simone = User(username="simone", email="simone@example.com")
        db.session.add_all([viewer, darius, simone])
        db.session.flush()
        for friend in (darius, simone):
            db.session.execute(friendships.insert().values(user_id=viewer.id, friend_id=friend.id))
        song = Song(title="Block Party", artist="Street Collective", shared_by=viewer.id)
        db.session.add(song)
        db.session.commit()
        return {"viewer": viewer.id, "darius": darius.id, "simone": simone.id, "song": song.id}


def _listen(ids, who, when):
    db.session.add(ListeningEvent(user_id=ids[who], song_id=ids["song"], listened_at=when))
    db.session.commit()


def _start_of_today():
    return FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)


def test_listen_from_yesterday_evening_is_excluded(app, ids):
    """Regression (Issue #2): a listen at 23:00 yesterday is not 'listening now' today,
    even though it is less than 24 hours ago for most of the day."""
    with app.app_context():
        _listen(ids, "darius", _start_of_today() - timedelta(hours=1))
        assert get_friends_listening_now(ids["viewer"]) == []


def test_listen_from_earlier_today_is_included(app, ids):
    """A listen from today (even just after midnight) still appears."""
    with app.app_context():
        _listen(ids, "simone", _start_of_today() + timedelta(seconds=1))
        feed = get_friends_listening_now(ids["viewer"])
        assert [f["friend"]["username"] for f in feed] == ["simone"]


def test_only_todays_listeners_appear_and_latest_song_per_friend(app, ids):
    """Mixed case: yesterday's listener is dropped, today's stays, once per friend."""
    with app.app_context():
        _listen(ids, "darius", _start_of_today() - timedelta(minutes=30))
        _listen(ids, "simone", _start_of_today() + timedelta(seconds=1))
        _listen(ids, "simone", _start_of_today() + timedelta(seconds=2))
        feed = get_friends_listening_now(ids["viewer"])
        assert [f["friend"]["username"] for f in feed] == ["simone"]
