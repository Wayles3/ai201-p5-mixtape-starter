# Mixtape Bug Hunt — Submission

## AI Usage

I worked on this project with Claude Code (an AI coding assistant running in my editor/terminal). Specifically:

- **Orientation:** Claude read every file in the repo (`app.py`, `models.py`, all of `routes/` and `services/`, the seed script and the existing tests) and summarised each one. I used those summaries, plus my own reading of the files, to write the codebase map below.
- **Reproduction:** Claude wrote throw-away scripts (kept outside the repo) that drive the seeded app through Flask's test client, so each bug was reproduced through the same HTTP routes the issues describe, and through direct service calls for the date-dependent streak bug.
- **Investigation:** Claude traced each symptom from route to service function. The diagnosis for each bug is only recorded below after I checked it against the code and against the reproduction output (see each entry's "How you found the root cause").
- **Where verification mattered:** while reproducing Issue #5 the "add a song" step crashed with an `IntegrityError` from a different code path (see the note under Issue #5), which is not one of the five reported bugs and which I did not assume was related. I also had to redo my first Issue #2 reproduction, because a freshly seeded event made the first run inconclusive.
- **Code:** Claude proposed and applied the fixes and regression tests; I reviewed each diff and ran the test suite before each commit.

---

## Codebase Map

### Stack and entry point

Mixtape is a Flask + Flask-SQLAlchemy app backed by SQLite (`mixtape.db`). `app.py` holds the `create_app()` factory: it configures the database URI, initialises the shared `db` object, registers four blueprints (`/songs`, `/playlists`, `/users`, `/feed`) and calls `db.create_all()`. The app is started with `FLASK_APP=app:create_app flask run`; `seed_data.py` drops and recreates the schema and loads sample data.

### Files and their roles

| File | Responsibility |
|---|---|
| `app.py` | Application factory, `db` instance, blueprint registration, table creation. |
| `models.py` | Seven SQLAlchemy models — `User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, `Notification` — and three association tables: `friendships` (self-referential user↔user, stored in both directions), `song_tags` (song↔tag) and `playlist_entries` (playlist↔song, carrying `position`, `added_by` and `added_at`, so playlist order is explicit). Each model has a `to_dict()` used for JSON output. `User` stores `listening_streak` and `last_listened_at`; `Rating` has a unique (user, song) constraint. |
| `routes/songs.py` | `/songs/search`, `/songs/<id>`, `/songs/<id>/rate`, `/songs/<id>/listen`. |
| `routes/playlists.py` | Create a playlist, fetch playlist metadata, list a playlist's songs, add a song to a playlist. |
| `routes/users.py` | User profile, `/users/<id>/streak`, `/users/<id>/notifications`, mark-notification-read. |
| `routes/feed.py` | `/feed/<id>/listening-now` and `/feed/<id>/activity`. |
| `services/streak_service.py` | Records a `ListeningEvent` and maintains the user's consecutive-day streak (`record_listening_event`, `update_listening_streak`, `get_streak`). |
| `services/feed_service.py` | Builds the friends feeds from `ListeningEvent` rows: `get_friends_listening_now` (recency-filtered, one entry per friend) and `get_activity_feed` (latest N events, unfiltered). |
| `services/search_service.py` | `search_songs` (case-insensitive match on title/artist, joined to the tag table) and `get_song`. |
| `services/notification_service.py` | Notification creation and retrieval, plus the two user actions that can produce notifications: `add_to_playlist` and `rate_song`. |
| `services/playlist_service.py` | Playlist creation and retrieval: `create_playlist`, `get_playlist_songs` (joins `playlist_entries`, ordered by `position`), `get_playlist`, `get_user_playlists`. |
| `tests/` | pytest suites for streaks, search and playlists, each using an in-memory SQLite app built through `create_app`. |
| `seed_data.py` | 5 users with friendships, 13 songs with 0/1/3 tags, 3 playlists, listening events at various ages, streaks, one notification. |

### Data flow — a user rates a song

1. `POST /songs/<song_id>/rate` with JSON `{user_id, score}` reaches `rate()` in `routes/songs.py`.
2. The route parses the body, checks both fields are present, converts `score` to `int`, and calls `rate_song(user_id, song_id, score)` in `services/notification_service.py`.
3. `rate_song` validates the score is 1–5, loads the `Song` and the rating `User` (raising `ValueError` if either is missing), then looks for an existing `Rating` for that (user, song) pair. It updates the score if one exists, otherwise inserts a new `Rating`, and commits.
4. It returns the `Rating`; the route serialises it with `to_dict()` and returns `201`. A `ValueError` from the service becomes a `400` response.

### Data flow — a user adds a song to a playlist

1. `POST /playlists/<playlist_id>/songs` with `{song_id, added_by}` reaches `add_song()` in `routes/playlists.py`, which calls `add_to_playlist` in `services/notification_service.py`.
2. That function loads the `Song`, the adding `User` and the `Playlist`, appends the song to `playlist.songs` if it is not already there, and commits.
3. If the adder is not the user who originally shared the song, it calls `create_notification(...)`, which inserts a `Notification` row for the sharer (`notification_type="song_added_to_playlist"`) and commits.
4. The sharer reads it later via `GET /users/<id>/notifications` → `get_notifications` (newest first, optional `unread_only`).

### Patterns I noticed

- **Thin routes, logic in services.** Every route parses input, calls one service function and formats the response; business rules live in `services/`.
- **Errors as `ValueError`.** Services raise `ValueError` for missing records or invalid input; routes translate that to `400` or `404`.
- **Services own the session.** Service functions use the shared `db.session` directly and commit themselves.
- **UUID string primary keys** everywhere, and timestamps stored as UTC `datetime`s.
- **Serialisation lives on the models** (`to_dict()`), and services return dicts (or model instances for the routes that call `to_dict()` themselves).
- **Notification logic is co-located with the actions that trigger it** in `notification_service.py` rather than in a separate event/hook layer.

---

## Root Cause Analyses

