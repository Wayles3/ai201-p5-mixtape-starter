"""
tests/test_notifications.py — Mixtape

Tests for notification creation when friends interact with shared songs.
"""

import pytest
from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def ids(app):
    """Return ids for a sharer, a rater and a song shared by the sharer."""
    with app.app_context():
        sharer = User(username="aaliya", email="aaliya@example.com")
        rater = User(username="kenji", email="kenji@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()
        song = Song(title="Golden Hour", artist="Solange K", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()
        return {"sharer": sharer.id, "rater": rater.id, "song": song.id}


def test_rating_notifies_song_sharer(app, ids):
    """Regression (Issue #4): rating someone's song creates a notification for them."""
    with app.app_context():
        rate_song(ids["rater"], ids["song"], 5)
        notifs = get_notifications(ids["sharer"])
        assert len(notifs) == 1
        assert notifs[0]["type"] == "song_rated"
        assert "kenji" in notifs[0]["body"]
        assert "Golden Hour" in notifs[0]["body"]


def test_rating_own_song_does_not_notify(app, ids):
    """Rating your own song does not notify yourself."""
    with app.app_context():
        rate_song(ids["sharer"], ids["song"], 4)
        assert get_notifications(ids["sharer"]) == []


def test_rating_is_still_saved(app, ids):
    """The rating itself is still stored alongside the notification."""
    with app.app_context():
        rating = rate_song(ids["rater"], ids["song"], 3)
        assert rating.score == 3
        assert db.session.get(Song, ids["song"]).ratings[0].score == 3
