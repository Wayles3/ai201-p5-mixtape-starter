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

**Issue #1 — My listening streak keeps resetting**

- **How you reproduced it.** Against the seeded database I set kenji's `listening_streak` to 12 and `last_listened_at` to Saturday 2026-10-24 22:00 UTC, then called `update_listening_streak(kenji, <Sunday 2026-10-25 09:00 UTC>)`. The streak came back as `1`. Running the same call for Sunday → Monday and Tuesday → Wednesday both gave `13`, so only the Sunday case failed, matching kenji's "both times it was a Sunday". The existing `tests/test_streaks.py::test_streak_increments_on_sunday` also failed (`assert 1 == 2`) before I changed anything.
- **How you found the root cause.** `GET /users/<id>/streak` (`routes/users.py`) only reads `User.listening_streak`, so the value had to be written elsewhere. `POST /songs/<id>/listen` (`routes/songs.py`) → `record_listening_event()` → `update_listening_streak()` in `services/streak_service.py` is the only writer. Reading its if/elif chain against its own docstring ("If the user listened yesterday: streak increments by 1"), the `elif` for the yesterday case had an extra clause, `and today.weekday() != 6`, that the docstring never mentions. Python's `weekday()` returns 6 for Sunday, which lined up exactly with the "Sunday only" symptom. I confirmed it by running the three date pairs above.
- **The root cause.** In `update_listening_streak()`, the increment branch was `elif days_since_last == 1 and today.weekday() != 6:`. On a Sunday, even when the user really did listen the day before (`days_since_last == 1`), the condition was false, so control fell through to the final `else`, which resets `listening_streak` to 1. The streak rules in the docstring say nothing about weekdays; Sunday was being treated as if a day had been skipped.
- **Your fix.** Removed `and today.weekday() != 6`, so the branch is `elif days_since_last == 1:`. Now "listened yesterday" increments on every day of the week and the `else` reset only fires when more than one day has passed, as the docstring describes.
- **Your side-effect check.** Ran all of `tests/test_streaks.py` (existing new-user, consecutive-day, same-day and skipped-day tests still pass). The skipped-day boundary matters most, so I added a regression test for it on the Sunday side: Friday → Sunday skips Saturday and still resets to 1. I also added `test_streak_increments_on_every_weekday`, parametrised over all seven weekdays; with the fix reverted it fails for exactly the Saturday → Sunday case and passes with the fix. Re-ran the reproduction script: Sat → Sun now gives 13.

**Issue #3 — The same song keeps showing up twice in search**

- **How you reproduced it.** With the seeded database, `GET /songs/search?q=Anthem` returned `count: 3` and three identical "Crown Heights Anthem" entries. The seed gives that song three tags (`rap`, `hip-hop`, `boom bap`). Searching `Collective` returned "Harlem Renaissance" (3 tags) once per tag, while songs with one or zero tags appeared once, which matches simone's "some songs once, others two or three times". The existing `tests/test_search.py::test_search_no_duplicates_multi_tag_song` failed too.
- **How you found the root cause.** `routes/songs.py::search()` just calls `search_songs()` in `services/search_service.py` and returns its list, so the duplication had to happen inside the query. The query does `select(Song).outerjoin(song_tags, ...)`. Reading that with the seed data in mind explained the inconsistency: the number of copies equals the number of `song_tags` rows a song has. I confirmed by counting results for songs with 0, 1 and 3 tags (1, 1, 3 results). The seed script's own comment ("songs with 3+ tags — these are the ones that expose Issue #3") agreed with this.
- **The root cause.** `search_songs()` joined `Song` to the `song_tags` association table, so the SQL result had one row per (song, tag) pair. A song with three tags produced three rows; one tag produced one row; zero tags produced one row (the outer join keeps it with NULLs). With SQLAlchemy 2.0-style `select()` the `.scalars().all()` result is not de-duplicated, so every row became a separate list entry. The join was never needed: the `WHERE` clause only filters on `Song.title` and `Song.artist`, and tags are already loaded by the `Song.tags` relationship (`lazy="subquery"`), which `to_dict()` uses for the `tags` field. That is why the duplicates were conditional on tag count.
- **Your fix.** Removed the `.outerjoin(song_tags, ...)` from the query (and the `Tag` and `song_tags` imports it left unused). Each song is now one row, and the tags in each result still come from the relationship.
- **Your side-effect check.** Re-ran the search for `Anthem` (1 result, still with its three tags), `Collective` (2), `Midnight` (1, no tags, so a tagless song is not dropped) and `e` (12 results, no repeated titles). Ran `tests/test_search.py`: all five tests pass, including the existing multi-tag, single-tag, no-tag and no-match cases, which act as the regression tests for this fix. I did not need a new test because `test_search_no_duplicates_multi_tag_song` already covers the failure.

