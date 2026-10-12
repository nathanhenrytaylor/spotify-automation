"""
Unit tests for junk_mover.py

Covers the logic that has historically broken:
- ensure_junk_drawer_playlist cache behaviour (the root cause of duplicate playlists)
- State file load/save
- Track bucketing by year suffix
- iso_to_date parsing
- _normalize_playlist_name
- chunked helper
- find_playlists_by_name_owner filtering

Run with:
    python3 -m pytest Junk_Mover/test_junk_mover.py -v
"""

import datetime as dt
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# ---------------------------------------------------------------------------
# Bootstrap: make junk_mover importable without a real .env or state file
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

# Stub out .env loading so import doesn't fail in CI
os.environ.setdefault("JUNK_MOVER_CLIENT_ID", "test-id")
os.environ.setdefault("JUNK_MOVER_CLIENT_SECRET", "test-secret")
os.environ.setdefault("JUNK_MOVER_REFRESH_TOKEN", "test-token")
os.environ.setdefault("JUNK_MOVER_SOURCE_PLAYLIST", "The Big Junk Drawer")

import Junk_Mover.junk_mover as jm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_http_error(status: int, body: bytes = b"{}"):
    """Return a urllib.error.HTTPError-compatible object for patching."""
    from urllib import error as urllib_error
    import io
    err = urllib_error.HTTPError(url="https://api.spotify.com/", code=status,
                                  msg="", hdrs={}, fp=io.BytesIO(body))
    return err


# ---------------------------------------------------------------------------
# SpotifyHTTPError
# ---------------------------------------------------------------------------

class TestSpotifyHTTPError(unittest.TestCase):
    def test_carries_status(self):
        err = jm.SpotifyHTTPError(429, "rate limited")
        self.assertEqual(err.status, 429)

    def test_is_runtime_error(self):
        err = jm.SpotifyHTTPError(404, "not found")
        self.assertIsInstance(err, RuntimeError)

    def test_message_contains_status(self):
        err = jm.SpotifyHTTPError(500, "oops")
        self.assertIn("500", str(err))


# ---------------------------------------------------------------------------
# iso_to_date
# ---------------------------------------------------------------------------

class TestIsoToDate(unittest.TestCase):
    def test_standard_z_timestamp(self):
        result = jm.iso_to_date("2025-06-15T09:00:00Z")
        self.assertEqual(result, dt.date(2025, 6, 15))

    def test_without_trailing_z(self):
        result = jm.iso_to_date("2024-01-01T00:00:00")
        self.assertEqual(result, dt.date(2024, 1, 1))


# ---------------------------------------------------------------------------
# _normalize_playlist_name
# ---------------------------------------------------------------------------

class TestNormalizePlaylistName(unittest.TestCase):
    def test_case_insensitive(self):
        self.assertEqual(jm._normalize_playlist_name("26 Junk Drawer"),
                         jm._normalize_playlist_name("26 JUNK DRAWER"))

    def test_strips_whitespace(self):
        self.assertEqual(jm._normalize_playlist_name("  foo  "),
                         jm._normalize_playlist_name("foo"))

    def test_none_returns_empty_string(self):
        self.assertEqual(jm._normalize_playlist_name(None), "")


# ---------------------------------------------------------------------------
# chunked
# ---------------------------------------------------------------------------

class TestChunked(unittest.TestCase):
    def test_even_split(self):
        result = list(jm.chunked(list(range(6)), 2))
        self.assertEqual(result, [[0, 1], [2, 3], [4, 5]])

    def test_remainder(self):
        result = list(jm.chunked(list(range(5)), 2))
        self.assertEqual(result, [[0, 1], [2, 3], [4]])

    def test_empty(self):
        self.assertEqual(list(jm.chunked([], 10)), [])

    def test_chunk_larger_than_list(self):
        result = list(jm.chunked([1, 2], 100))
        self.assertEqual(result, [[1, 2]])


# ---------------------------------------------------------------------------
# group_tracks_by_year_suffix
# ---------------------------------------------------------------------------

class TestGroupTracksByYearSuffix(unittest.TestCase):
    def test_groups_by_two_digit_year(self):
        tracks = [
            ("spotify:track:A", dt.date(2024, 3, 1)),
            ("spotify:track:B", dt.date(2025, 7, 15)),
            ("spotify:track:C", dt.date(2024, 11, 30)),
        ]
        result = jm.group_tracks_by_year_suffix(tracks)
        self.assertIn("24", result)
        self.assertIn("25", result)
        self.assertEqual(len(result["24"]), 2)
        self.assertEqual(len(result["25"]), 1)

    def test_zero_padded(self):
        # Year 2000 should give "00", not "0"
        tracks = [("spotify:track:A", dt.date(2000, 1, 1))]
        result = jm.group_tracks_by_year_suffix(tracks)
        self.assertIn("00", result)

    def test_empty_input(self):
        self.assertEqual(jm.group_tracks_by_year_suffix([]), {})


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

class TestStateFile(unittest.TestCase):
    def setUp(self):
        self._orig_state_file = jm.STATE_FILE

    def tearDown(self):
        jm.STATE_FILE = self._orig_state_file

    def test_load_state_returns_empty_dict_when_missing(self, tmp_path=None):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            jm.STATE_FILE = pathlib.Path(d) / "nonexistent.json"
            self.assertEqual(jm.load_state(), {})

    def test_round_trip(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            jm.STATE_FILE = pathlib.Path(d) / "state.json"
            data = {"26 Junk Drawer": "abc123"}
            jm.save_state(data)
            loaded = jm.load_state()
            self.assertEqual(loaded, data)

    def test_load_state_returns_empty_on_corrupt_json(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "state.json"
            p.write_text("this is not json")
            jm.STATE_FILE = p
            self.assertEqual(jm.load_state(), {})


# ---------------------------------------------------------------------------
# find_playlists_by_name_owner
# ---------------------------------------------------------------------------

class TestFindPlaylistsByNameOwner(unittest.TestCase):
    def _make_playlists(self):
        return [
            {"id": "A", "name": "26 Junk Drawer", "owner": {"id": "user1"}},
            {"id": "B", "name": "26 Junk Drawer", "owner": {"id": "user2"}},   # wrong owner
            {"id": "C", "name": "25 Junk Drawer", "owner": {"id": "user1"}},   # wrong name
            {"id": "D", "name": "26 JUNK DRAWER", "owner": {"id": "user1"}},   # same name, different case
        ]

    def test_filters_by_owner_and_name(self):
        playlists = self._make_playlists()
        with patch.object(jm, "paginate_playlists", return_value=iter(playlists)):
            result = jm.find_playlists_by_name_owner("token", "user1", "26 Junk Drawer")
        ids = [p["id"] for p in result]
        self.assertIn("A", ids)
        self.assertIn("D", ids)
        self.assertNotIn("B", ids)
        self.assertNotIn("C", ids)

    def test_returns_empty_when_no_match(self):
        with patch.object(jm, "paginate_playlists", return_value=iter([])):
            result = jm.find_playlists_by_name_owner("token", "user1", "26 Junk Drawer")
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# ensure_junk_drawer_playlist — THE CRITICAL BUG AREA
# ---------------------------------------------------------------------------

class TestEnsureJunkDrawerPlaylist(unittest.TestCase):
    """
    Tests for the cache logic that caused repeated duplicate playlist creation.

    Policy:
      - Cache hit, playlist exists → return cached ID (no create)
      - Cache hit, 404 from Spotify → invalidate cache, fall through to search/create
      - Cache hit, non-404 HTTP error → raise, do NOT invalidate cache or create
      - Cache hit, network error → raise, do NOT invalidate cache or create
      - Cache miss, API finds one → cache it, return it (no create)
      - Cache miss, API finds none → create, cache, return new ID
    """

    CACHED_ID = "cached-playlist-id"
    NEW_ID = "brand-new-playlist-id"
    USER_ID = "test-user"
    NAME = "26 Junk Drawer"
    DESC = "test description"

    def _state_with_cache(self):
        return {self.NAME: self.CACHED_ID}

    def _patch(self, state, spotify_side_effect=None, find_result=None, create_id=None):
        """
        Returns a context manager bundle. spotify_side_effect is called for each
        spotify_request call in order. find_result controls find_playlists_by_name_owner.
        """
        patches = []

        p_load = patch.object(jm, "load_state", return_value=state)
        p_save = patch.object(jm, "save_state")
        p_follow = patch.object(jm, "follow_playlist")

        if spotify_side_effect is not None:
            p_spotify = patch.object(jm, "spotify_request", side_effect=spotify_side_effect)
        else:
            p_spotify = patch.object(jm, "spotify_request",
                                     return_value={"id": create_id or self.NEW_ID})

        if find_result is not None:
            p_find = patch.object(jm, "find_playlists_by_name_owner", return_value=find_result)
        else:
            p_find = patch.object(jm, "find_playlists_by_name_owner", return_value=[])

        return p_load, p_save, p_follow, p_spotify, p_find

    # --- Cache hit: playlist still exists ---

    def test_cache_hit_returns_cached_id_without_creating(self):
        state = self._state_with_cache()
        with patch.object(jm, "load_state", return_value=state), \
             patch.object(jm, "save_state") as mock_save, \
             patch.object(jm, "follow_playlist"), \
             patch.object(jm, "spotify_request", return_value={"id": self.CACHED_ID}) as mock_req, \
             patch.object(jm, "find_playlists_by_name_owner") as mock_find:

            result = jm.ensure_junk_drawer_playlist("token", self.USER_ID, self.NAME, self.DESC)

        self.assertEqual(result, self.CACHED_ID)
        # Must not have called find or POST (create)
        mock_find.assert_not_called()
        # Only the GET verification call should have happened, no POST
        for c in mock_req.call_args_list:
            self.assertNotEqual(c.args[0].upper(), "POST", "Should not POST (create) when cache hits")

    # --- Cache hit: 404 — invalidates cache, falls through to search ---

    def test_cache_hit_404_falls_through_to_search(self):
        state = self._state_with_cache()
        found_playlist = {"id": "found-id"}

        def spotify_side(method, url, token, **kwargs):
            if method == "GET" and self.CACHED_ID in url:
                raise jm.SpotifyHTTPError(404, "not found")
            return {"id": found_playlist["id"]}

        with patch.object(jm, "load_state", return_value=state), \
             patch.object(jm, "save_state"), \
             patch.object(jm, "follow_playlist"), \
             patch.object(jm, "spotify_request", side_effect=spotify_side), \
             patch.object(jm, "find_playlists_by_name_owner", return_value=[found_playlist]):

            result = jm.ensure_junk_drawer_playlist("token", self.USER_ID, self.NAME, self.DESC)

        self.assertEqual(result, "found-id")

    # --- THE KEY BUG FIX: non-404 HTTP errors must NOT create a duplicate ---

    def test_cache_hit_429_raises_and_does_not_create(self):
        """429 (rate-limit) during cache verification must raise, not silently create."""
        state = self._state_with_cache()

        def spotify_side(method, url, token, **kwargs):
            if method == "GET":
                raise jm.SpotifyHTTPError(429, "rate limited")
            return {"id": self.NEW_ID}

        with patch.object(jm, "load_state", return_value=state), \
             patch.object(jm, "save_state") as mock_save, \
             patch.object(jm, "follow_playlist"), \
             patch.object(jm, "spotify_request", side_effect=spotify_side) as mock_req, \
             patch.object(jm, "find_playlists_by_name_owner") as mock_find:

            with self.assertRaises(jm.SpotifyHTTPError) as ctx:
                jm.ensure_junk_drawer_playlist("token", self.USER_ID, self.NAME, self.DESC)

        self.assertEqual(ctx.exception.status, 429)
        mock_find.assert_not_called()
        # No POST create should have been attempted
        post_calls = [c for c in mock_req.call_args_list if c.args[0].upper() == "POST"]
        self.assertEqual(post_calls, [], "Must not create new playlist on 429")

    def test_cache_hit_500_raises_and_does_not_create(self):
        """Server error during cache verification must raise, not silently create."""
        state = self._state_with_cache()

        def spotify_side(method, url, token, **kwargs):
            if method == "GET":
                raise jm.SpotifyHTTPError(500, "internal server error")
            return {"id": self.NEW_ID}

        with patch.object(jm, "load_state", return_value=state), \
             patch.object(jm, "save_state"), \
             patch.object(jm, "follow_playlist"), \
             patch.object(jm, "spotify_request", side_effect=spotify_side), \
             patch.object(jm, "find_playlists_by_name_owner") as mock_find:

            with self.assertRaises(jm.SpotifyHTTPError):
                jm.ensure_junk_drawer_playlist("token", self.USER_ID, self.NAME, self.DESC)

        mock_find.assert_not_called()

    def test_cache_hit_network_error_raises_and_does_not_create(self):
        """Network error during cache verification must raise, not silently create."""
        state = self._state_with_cache()

        def spotify_side(method, url, token, **kwargs):
            if method == "GET":
                raise RuntimeError("Network error calling Spotify: timed out")
            return {"id": self.NEW_ID}

        with patch.object(jm, "load_state", return_value=state), \
             patch.object(jm, "save_state"), \
             patch.object(jm, "follow_playlist"), \
             patch.object(jm, "spotify_request", side_effect=spotify_side), \
             patch.object(jm, "find_playlists_by_name_owner") as mock_find:

            with self.assertRaises(RuntimeError):
                jm.ensure_junk_drawer_playlist("token", self.USER_ID, self.NAME, self.DESC)

        mock_find.assert_not_called()

    # --- Cache miss: API search finds existing playlist ---

    def test_no_cache_finds_existing_via_api(self):
        found = {"id": "api-found-id"}

        with patch.object(jm, "load_state", return_value={}), \
             patch.object(jm, "save_state") as mock_save, \
             patch.object(jm, "follow_playlist"), \
             patch.object(jm, "spotify_request", return_value={}) as mock_req, \
             patch.object(jm, "find_playlists_by_name_owner", return_value=[found]):

            result = jm.ensure_junk_drawer_playlist("token", self.USER_ID, self.NAME, self.DESC)

        self.assertEqual(result, "api-found-id")
        # Must not POST to create
        post_calls = [c for c in mock_req.call_args_list if c.args[0].upper() == "POST"]
        self.assertEqual(post_calls, [])
        # Must cache the found ID
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved.get(self.NAME), "api-found-id")

    # --- Cache miss: not found anywhere → create ---

    def test_no_cache_no_api_result_creates_new(self):
        with patch.object(jm, "load_state", return_value={}), \
             patch.object(jm, "save_state") as mock_save, \
             patch.object(jm, "follow_playlist"), \
             patch.object(jm, "spotify_request", return_value={"id": self.NEW_ID}), \
             patch.object(jm, "find_playlists_by_name_owner", return_value=[]):

            result = jm.ensure_junk_drawer_playlist("token", self.USER_ID, self.NAME, self.DESC)

        self.assertEqual(result, self.NEW_ID)
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved.get(self.NAME), self.NEW_ID)

    # --- Multiple API results: warn but use first, don't create ---

    def test_multiple_api_results_uses_first_without_creating(self):
        found = [{"id": "first-id"}, {"id": "second-id"}]

        with patch.object(jm, "load_state", return_value={}), \
             patch.object(jm, "save_state"), \
             patch.object(jm, "follow_playlist"), \
             patch.object(jm, "spotify_request", return_value={}) as mock_req, \
             patch.object(jm, "find_playlists_by_name_owner", return_value=found):

            result = jm.ensure_junk_drawer_playlist("token", self.USER_ID, self.NAME, self.DESC)

        self.assertEqual(result, "first-id")
        post_calls = [c for c in mock_req.call_args_list if c.args[0].upper() == "POST"]
        self.assertEqual(post_calls, [])


if __name__ == "__main__":
    unittest.main()
