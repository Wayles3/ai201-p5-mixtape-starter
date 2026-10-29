# Mixtape Bug Hunt — Submission

## AI Usage

I used Claude Code (an AI coding assistant in my editor/terminal) throughout. The specific things I asked it to do, and what I did with the answers:

1. **File-by-file summary for the codebase map.** I had it read `models.py`, every file in `routes/` and `services/`, and `seed_data.py`, and explain what each module is responsible for and how a rating request travels from `routes/songs.py::rate()` to `notification_service.rate_song()`. I wrote the map from those summaries after checking them against the files; for example, I confirmed in `models.py` that `playlist_entries` carries `position`, `added_by` and `added_at`, rather than trusting the summary.
2. **Side-by-side comparison for Issue #4.** I gave it `add_to_playlist()` and `rate_song()` and asked for the structural difference between the two. It pointed out that only the first ends with a `create_notification(...)` call. I then ran `git grep song_rated` on the original commit to confirm nothing else ever created that notification type, so the cause was a missing call, not a call that failed silently.
3. **Explaining a date condition for Issue #1.** Once the `today.weekday() != 6` clause was spotted, the AI explained that `weekday()` returns 6 for Sunday, which fits the report. I did not accept "that matches the report" on its own; I ran `update_listening_streak` for Sat→Sun, Sun→Mon and Tue→Wed with kenji's streak at 12, and only the Sunday case reset, which confirmed the diagnosis.
4. **Reproduction scripts.** It wrote throw-away scripts (outside the repo) that drive the seeded app through Flask's test client so each bug was reproduced via the same routes the issues use.

**Where I had to verify or course-correct:**
- My first reproduction of Issue #2 was inconclusive: the seed data already contained a fresh 10-minute-old listen for darius, so he appeared in the feed for the wrong reason. I cleared his events and inserted one at 23:00 the previous night, which gave a clean reproduction.
- While reproducing Issue #5, the "add a song" step from darius's report crashed with `IntegrityError: NOT NULL constraint failed: playlist_entries.position`. It would be easy to assume this crash was the cause of the missing song, but the seeded rows were all present in the table and the endpoint still returned 6 of 7, so it was a separate problem. I kept it out of the fix and recorded it in the Issue #5 entry.
- For Issue #3, I checked the join-per-tag explanation against the original code by searching for songs with 0, 1 and 3 tags (1, 1 and 3 results), rather than relying on the AI's description.
- The AI generated the fixes and regression tests; I reviewed each diff and, for the new tests, ran them with the fix reverted to confirm they fail on the buggy code.

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
| `services/search_service.py` | `search_songs` (case-insensitive match on title/artist; returns each song with its tag names) and `get_song`. |
| `services/notification_service.py` | Notification creation and retrieval, plus the two user actions that can produce notifications: `add_to_playlist` and `rate_song`. |
| `services/playlist_service.py` | Playlist creation and retrieval: `create_playlist`, `get_playlist_songs` (joins `playlist_entries`, ordered by `position`), `get_playlist`, `get_user_playlists`. |
| `tests/` | pytest suites for streaks, search, playlists, the listening-now feed and rating notifications, each using an in-memory SQLite app built through `create_app`. |
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


**Issue #2 — Friends Listening Now shows people from yesterday**

- **How you reproduced it.** On the seeded database I deleted darius's listening events and added one at 23:00 UTC the previous night (the start of today minus one hour; it was 21:36 UTC when I ran it). Then `GET /feed/<simone>/listening-now` (simone is darius's friend) returned darius in the feed even though his only listen was the night before. An earlier attempt was inconclusive because a freshly seeded 10-minute-old event for darius was also in the table; clearing it first made the result clean.
- **How you found the root cause.** `routes/feed.py::listening_now()` calls `get_friends_listening_now()` in `services/feed_service.py`. The function's recency filter is `ListeningEvent.listened_at >= cutoff`, where `cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD` and `RECENT_THRESHOLD = timedelta(hours=24)`. Comparing that with nova's wording ("until the same time the next day") made it fit: a listen at 11pm last night stays inside a rolling 24-hour window until 11pm tonight. I checked the other function in the file, `get_activity_feed`, whose docstring says it is "not filtered by recency", so the filter is specific to this feed.
- **The root cause.** The feed's meaning is "friends who have listened *today*" (the issue says "only friends who have listened today appear"), but the code implemented a *rolling* 24-hour window instead of a calendar-day boundary. Anything within the last 24 hours counted, so at 9am a listen from 11pm the previous evening (10 hours ago) was still included, and it only aged out at the same clock time the next day. The codebase's other date logic (the streak service) works on calendar days, so a rolling window was inconsistent with it too.
- **Your fix.** In `services/feed_service.py` the cutoff is now the start of the current UTC day: `datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)`. The query `listened_at >= cutoff` is unchanged, so only today's listens pass. I removed the now-unused `RECENT_THRESHOLD` constant (nothing else referenced it) and updated the docstring to say "today (UTC)". Day boundaries are UTC, the same as the rest of the app's stored timestamps.
- **Your side-effect check.** Re-ran the repro: darius no longer appears, while nova (listened minutes ago) still does. `get_activity_feed` is untouched and still returns recent events regardless of age. The per-friend de-duplication (most recent song only) is unchanged, and the new tests cover it. I added `tests/test_feed.py` with three tests: a listen at 23:00 yesterday is excluded (regression test), a listen one second after midnight is included (the boundary, on the "today" side), and a mixed case with a yesterday listener, a today listener with two listens, and one entry per friend. With the fix reverted, the two exclusion tests fail; with it, all pass (27 passed in the full suite). The feed tests freeze `feed_service.datetime` at 09:00 UTC, so a listen at 23:00 the night before is 10 hours old and sits inside the old rolling window; I confirmed they fail against a 24-hour cutoff regardless of the real time of day.

**Issue #3 — The same song keeps showing up twice in search**

- **How you reproduced it.** With the seeded database, `GET /songs/search?q=Anthem` returned `count: 3` and three identical "Crown Heights Anthem" entries. The seed gives that song three tags (`rap`, `hip-hop`, `boom bap`). I ran this against the original code: `Anthem` returned 3 results, `Collective` returned 4 ("Street Collective"'s one-tag song once, plus "Harlem Renaissance" three times), `Midnight` (0 tags) returned 1 and `Block Party` (1 tag) returned 1. That matches simone's "some songs once, others two or three times". The existing `tests/test_search.py::test_search_no_duplicates_multi_tag_song` failed too.
- **How you found the root cause.** `routes/songs.py::search()` just calls `search_songs()` in `services/search_service.py` and returns its list, so the duplication had to happen inside the query. The query does `select(Song).outerjoin(song_tags, ...)`. Reading that with the seed data in mind explained the inconsistency: the number of copies equals the number of `song_tags` rows a song has. I confirmed it with the counts above: 0 tags → 1 result, 1 tag → 1 result, 3 tags → 3 results. The seed script's own comment ("songs with 3+ tags — these are the ones that expose Issue #3") agreed with this.
- **The root cause.** `search_songs()` joined `Song` to the `song_tags` association table, so the SQL result had one row per (song, tag) pair. A song with three tags produced three rows; one tag produced one row; zero tags produced one row (the outer join keeps it with NULLs). With SQLAlchemy 2.0-style `select()` the `.scalars().all()` result is not de-duplicated, so every row became a separate list entry. The join was never needed: the `WHERE` clause only filters on `Song.title` and `Song.artist`, and tags are already loaded by the `Song.tags` relationship (`lazy="subquery"`), which `to_dict()` uses for the `tags` field. That is why the duplicates were conditional on tag count.
- **Your fix.** Removed the `.outerjoin(song_tags, ...)` from the query (and the `Tag` and `song_tags` imports it left unused). Each song is now one row, and the tags in each result still come from the relationship.
- **Your side-effect check.** Re-ran the same searches: `Anthem` (3 → 1 result, still with its three tags), `Collective` (4 → 2), `Midnight` (1, no tags, so a tagless song is not dropped) and `e` (12 results, no repeated titles). Ran `tests/test_search.py`: all five tests pass, including the existing multi-tag, single-tag, no-tag and no-match cases, which act as the regression tests for this fix. I did not need a new test because `test_search_no_duplicates_multi_tag_song` already covers the failure.

**Issue #4 — I got notified when a friend added my song to a playlist but not when they rated it**

- **How you reproduced it.** On the seeded database I had kenji rate darius's song "Block Party" (`POST /songs/<id>/rate` with `{"user_id": <kenji>, "score": 5}`). The response was `201` and the rating was stored, but `GET /users/<darius>/notifications` still returned `count: 0` before and after. The only notification in the seed data is the `song_added_to_playlist` one for nova, so nothing ever produced a `song_rated` type.
- **How you found the root cause.** Both actions live in `services/notification_service.py`, so I compared `add_to_playlist()` (works) with `rate_song()` (doesn't) side by side, as the hint suggests. `add_to_playlist()` ends with an `if song.shared_by != added_by_user_id:` block calling `create_notification(...)`. `rate_song()` validates, loads the song and rater, upserts the `Rating`, commits and returns, and has no call to `create_notification` at all. The `create_notification` docstring even lists `'song_rated'` as an example type, but `git grep song_rated` on the original commit finds that docstring as the only occurrence, so nothing ever passes that type.
- **The root cause.** The notification step was never part of `rate_song()`. Notifications in this app aren't produced by a central event system; each action function has to call `create_notification()` itself (the pattern in `add_to_playlist`). `rate_song()` saved the rating and returned, so no `Notification` row was ever inserted for a rating, hence no delay, no error, and an empty list in the endpoint, exactly as aaliya described. It is architectural (a missing call in one code path), not a typo.
- **Your fix.** After the rating is committed, `rate_song()` now follows the `add_to_playlist` pattern: if `song.shared_by != user_id` it calls `create_notification(user_id=song.shared_by, notification_type="song_rated", body=f"{rater.username} rated your song '{song.title}' {score}/5.")`. I kept the same self-action guard as the playlist path so people aren't notified about their own ratings. The notification is created after the rating commit so a rating is never lost if notification creation fails.
- **Your side-effect check.** Re-ran the repro: kenji's 5-star rating now gives darius one `song_rated` notification, and the rating is still saved and returned with `201`. An invalid score (9) is still rejected with the `400` "Score must be between 1 and 5" before any notification can be created. The playlist-add notification path is untouched. I added `tests/test_notifications.py` with three tests: rating notifies the sharer (this is the regression test; it fails with the fix reverted and passes with it), rating your own song doesn't notify, and the rating is still stored. Full suite: 24 passed.

**Issue #5 — The last song in a playlist never shows up**

- **How you reproduced it.** The seed script inserts 7 `playlist_entries` rows (positions 1–7) for "Friday Energy". `GET /playlists/<id>/songs` returned `count: 6`, and the song at position 7 was missing. The other two seeded playlists also showed 6 of 7 (checked on the original code: all three returned 6). `tests/test_playlists.py::test_playlist_returns_all_songs` and `test_playlist_returns_songs_in_order` failed before any change. (I could not reproduce darius's second step, "add a song and re-fetch", through the API; see the note below.)
- **How you found the root cause.** `routes/playlists.py::get_songs()` returns whatever `get_playlist_songs()` gives it. In `services/playlist_service.py` the query joins `playlist_entries`, filters by playlist and orders by `position`; since the seeded rows existed in the table, the query itself was returning all of them, so the loss was after the query. The return statement was the only thing left, and it iterated `songs[:-1]`.
- **The root cause.** `get_playlist_songs()` builds the full, correctly ordered list of songs and then returns `[song.to_dict() for song in songs[:-1]]`. The slice `[:-1]` means "everything except the last element", so the song with the highest `position` (the most recently added) was always dropped. That explains darius's observation: when simone added another song it took the highest position, so the previously-last song re-entered the slice and the new one became the hidden one. The function's own docstring says "This function returns all songs in the playlist."
- **Your fix.** Changed the return to iterate `songs` instead of `songs[:-1]` in `services/playlist_service.py`. The ordering by `position` is unchanged.
- **Your side-effect check.** All three seeded playlists now return 7 of 7, in position order, with the highest-position song last. `tests/test_playlists.py` passes, including the empty-playlist test (an empty list still returns `[]` without error) and the ordering test, so both ends of the list are right; the full suite (21 tests) is green. The two existing playlist tests that failed before are the regression tests for this fix.
- **Note on a separate problem I saw, not fixed here.** While trying darius's "add a song" step, `POST /playlists/<id>/songs` returned a 500: `add_to_playlist()` in `notification_service.py` appends to `playlist.songs`, and SQLAlchemy's insert into `playlist_entries` omits the NOT NULL `position` and `added_by` columns (`IntegrityError: NOT NULL constraint failed: playlist_entries.position`). That is a different defect from the five reported issues and is out of scope for this commit, so I left it alone and kept this fix to the one-line root cause.
