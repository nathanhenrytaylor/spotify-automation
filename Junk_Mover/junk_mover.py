"""
Move old songs from a source playlist into year-based "Junk Drawer" playlists.

Behavior:
- Logs in as the current user via refresh token.
- Finds a playlist owned by the user with name from JUNK_MOVER_SOURCE_PLAYLIST.
- Moves tracks whose added_at is older than JUNK_MOVER_DURATION_DAYS into a playlist
  named "<YY> Junk Drawer" where YY is the last two digits of the year the track
  was added to the source playlist. Playlists are created if missing.
"""

import argparse
import base64
import datetime as dt
import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "junk_mover.log"


def load_env_from_root() -> None:
    """Load .env values from the project root into os.environ if present."""
    # Keep secrets/config centralized in the repo-level .env for portability.
    env_path = ROOT / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def require_env(keys: List[str]) -> None:
    """Ensure all required environment variables are populated before execution."""
    # Surface missing configuration early with a clear error.
    missing = [key for key in keys if not os.environ.get(key)]
    if missing:
        raise ValueError(
            "Missing environment variables: " + ", ".join(missing) + ". Fill them in .env."
        )


def setup_logging() -> None:
    """Configure file + stdout logging with daily rotation and year-long retention."""
    # Rotate logs daily and retain a year for lightweight Pi-friendly observability.
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.TimedRotatingFileHandler(
        LOG_FILE, when="D", interval=1, backupCount=365
    )
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)

    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler(sys.stdout)])


def fetch_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Swap the long-lived refresh token for a short-lived bearer token for API calls."""
    # Use refresh token flow so this can run unattended on a schedule.
    data = parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    ).encode()

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = request.Request("https://accounts.spotify.com/api/token", data=data, method="POST")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with request.urlopen(req) as resp:
            payload = json.load(resp)
        return payload["access_token"]
    except error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"Failed to refresh token: {exc.status} {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Network error while refreshing token: {exc.reason}") from exc


def spotify_request(
    method: str,
    url: str,
    access_token: str,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Any] = None,
) -> Dict[str, Any]:
    """Perform a Spotify Web API request with consistent headers, encoding, and errors."""
    # Minimal helper around urllib with uniform error handling and JSON bodies.
    if params:
        url = f"{url}?{parse.urlencode(params)}"

    payload = None
    if data is not None:
        payload = json.dumps(data).encode()

    req = request.Request(url, data=payload, method=method.upper())
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")

    try:
        with request.urlopen(req) as resp:
            if resp.status == 204:
                return {}
            raw_body = resp.read()
            if not raw_body:
                return {}
            return json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Spotify API returned non-JSON body") from exc
    except error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"Spotify API error {exc.status}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Network error calling Spotify: {exc.reason}") from exc


def update_playlist_description(access_token: str, playlist_id: str, description: str) -> None:
    """Update a playlist's description string for user-facing audit context."""
    # Annotate playlists with the latest run details for quick auditing in the UI.
    spotify_request(
        "PUT",
        f"https://api.spotify.com/v1/playlists/{playlist_id}",
        access_token,
        data={"description": description},
    )
    logging.info("Updated description for playlist %s", playlist_id)


def get_current_user(access_token: str) -> Dict[str, Any]:
    """Return the authenticated user's profile, including ID and display name."""
    return spotify_request("GET", "https://api.spotify.com/v1/me", access_token)


def paginate_playlists(access_token: str) -> Iterable[Dict[str, Any]]:
    """Yield all playlists accessible to the user, paging through Spotify results."""
    # Iterate through all playlists without manual paging logic elsewhere.
    url = "https://api.spotify.com/v1/me/playlists"
    params = {"limit": 50, "offset": 0}
    while True:
        page = spotify_request("GET", url, access_token, params=params)
        for item in page.get("items", []):
            yield item
        if not page.get("next"):
            break
        params["offset"] += params["limit"]


def _normalize_playlist_name(name: Optional[str]) -> str:
    """Normalize playlist names for safe comparison."""
    return (name or "").strip().lower()


def find_playlists_by_name_owner(
    access_token: str, owner_id: str, target_name: str
) -> List[Dict[str, Any]]:
    """Return all playlists owned by the user with the matching name."""
    normalized_target = _normalize_playlist_name(target_name)
    matches: List[Dict[str, Any]] = []
    for playlist in paginate_playlists(access_token):
        if (
            _normalize_playlist_name(playlist.get("name")) == normalized_target
            and playlist.get("owner", {}).get("id") == owner_id
        ):
            matches.append(playlist)
    return matches


def find_playlist_by_name_owner(
    access_token: str, owner_id: str, target_name: str
) -> Optional[Dict[str, Any]]:
    """Locate a playlist by name that is owned by the given user ID."""
    matches = find_playlists_by_name_owner(access_token, owner_id, target_name)
    if len(matches) > 1:
        logging.warning(
            "Found %d playlists owned by %s with name %r; using the first match %s.",
            len(matches),
            owner_id,
            target_name,
            matches[0].get("id"),
        )
    return matches[0] if matches else None


def paginate_playlist_items(access_token: str, playlist_id: str) -> Iterable[Dict[str, Any]]:
    """Yield every track item from the specified playlist, handling pagination."""
    # Stream all tracks from a playlist, respecting API paging.
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
    params = {"limit": 100, "offset": 0}
    while True:
        page = spotify_request("GET", url, access_token, params=params)
        for item in page.get("items", []):
            yield item
        if not page.get("next"):
            break
        params["offset"] += params["limit"]


def ensure_junk_drawer_playlist(
    access_token: str, user_id: str, name: str, description: str
) -> str:
    """Return ID of the named Junk Drawer playlist, creating it if absent."""
    # Create or re-use the destination playlist on-demand to keep runs idempotent.
    existing = find_playlists_by_name_owner(access_token, user_id, name)
    if existing:
        if len(existing) > 1:
            logging.warning(
                "Multiple existing playlists were found for %r; using %s and ignoring %d duplicate(s).",
                name,
                existing[0].get("id"),
                len(existing) - 1,
            )
        return existing[0]["id"]

    body = {
        "name": name,
        "description": description,
        "public": False,
    }
    created = spotify_request(
        "POST", f"https://api.spotify.com/v1/users/{user_id}/playlists", access_token, data=body
    )
    logging.info("Created playlist %s (%s)", name, created.get("id"))
    return created["id"]


def add_tracks_to_playlist(access_token: str, playlist_id: str, uris: List[str]) -> None:
    """Add the given track URIs to a playlist in Spotify-compliant batches of 100."""
    # Add tracks in batches to respect API limits.
    for chunk in chunked(uris, 100):
        spotify_request(
            "POST",
            f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
            access_token,
            data={"uris": chunk},
        )
        logging.info("Added %d tracks to %s", len(chunk), playlist_id)


def remove_tracks_from_playlist(access_token: str, playlist_id: str, uris: List[str]) -> None:
    """Remove the given track URIs from a playlist in batches to honor API limits."""
    # Remove tracks in batches to respect API limits.
    for chunk in chunked(uris, 100):
        spotify_request(
            "DELETE",
            f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
            access_token,
            data={"tracks": [{"uri": uri} for uri in chunk]},
        )
        logging.info("Removed %d tracks from %s", len(chunk), playlist_id)


def chunked(seq: List[str], size: int) -> Iterable[List[str]]:
    """Yield sequential slices from a list with the requested maximum length."""
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def iso_to_date(iso_ts: str) -> dt.date:
    """Convert an ISO 8601 timestamp (with trailing Z) to a naive date for comparisons."""
    # Normalize Spotify's timestamp into a date for age comparisons.
    # Spotify returns ISO timestamps like "2025-11-29T12:34:56Z"
    clean = iso_ts.rstrip("Z")
    return dt.datetime.fromisoformat(clean).date()


def group_tracks_by_year_suffix(
    tracks: List[Tuple[str, dt.date]]
) -> Dict[str, List[Tuple[str, dt.date]]]:
    """Group (uri, added_date) pairs by two-digit year suffix for playlist routing."""
    # Bucket tracks by year suffix to route them into the correct Junk Drawer.
    buckets: Dict[str, List[Tuple[str, dt.date]]] = {}
    for uri, added_date in tracks:
        year_suffix = f"{added_date.year % 100:02d}"
        buckets.setdefault(year_suffix, []).append((uri, added_date))
    return buckets


def parse_args() -> argparse.Namespace:
    """Parse CLI flags for dry runs and duration selection."""
    parser = argparse.ArgumentParser(
        description="Move old songs from a source Spotify playlist into year-based Junk Drawer playlists."
    )
    parser.add_argument(
        "--duration-days",
        type=int,
        default=None,
        help=(
            "Age threshold in days for moving tracks. "
            "If omitted, reads JUNK_MOVER_DURATION_DAYS from environment."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be moved without changing Spotify playlists.",
    )
    parser.add_argument(
        "--source-playlist",
        type=str,
        default=None,
        help="Override JUNK_MOVER_SOURCE_PLAYLIST from environment.",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point: authenticate, find source playlist, move aged tracks, and annotate playlists."""
    # Load configuration from .env before anything else so secrets are available.
    load_env_from_root()
    # Set up file + stdout logging so runs on a Pi leave history for 1 year.
    setup_logging()

    args = parse_args()

    # Ensure all required inputs are present before making API calls.
    required_keys = [
        "JUNK_MOVER_CLIENT_ID",
        "JUNK_MOVER_CLIENT_SECRET",
        "JUNK_MOVER_REFRESH_TOKEN",
    ]
    if args.source_playlist is None:
        required_keys.append("JUNK_MOVER_SOURCE_PLAYLIST")
    if args.duration_days is None:
        required_keys.append("JUNK_MOVER_DURATION_DAYS")
    require_env(required_keys)

    # Parse the age threshold (in days) used to decide what gets moved.
    if args.duration_days is not None:
        duration_days = args.duration_days
    else:
        try:
            duration_days = int(os.environ["JUNK_MOVER_DURATION_DAYS"])
        except ValueError as exc:
            raise ValueError("JUNK_MOVER_DURATION_DAYS must be an integer") from exc
    if duration_days < 0:
        raise ValueError("Duration days cannot be negative; use 0 for 'today'.")

    # Read credentials and source playlist name from environment or CLI.
    client_id = os.environ["JUNK_MOVER_CLIENT_ID"]
    client_secret = os.environ["JUNK_MOVER_CLIENT_SECRET"]
    refresh_token = os.environ["JUNK_MOVER_REFRESH_TOKEN"]
    source_playlist_name = args.source_playlist or os.environ["JUNK_MOVER_SOURCE_PLAYLIST"]

    # Authenticate as the current user using the refresh token.
    access_token = fetch_access_token(client_id, client_secret, refresh_token)
    me = get_current_user(access_token)
    user_id = me.get("id")
    logging.info("Authenticated as %s", me.get("display_name") or user_id)

    # Locate the source playlist owned by this user; abort if not found.
    source_playlist = find_playlist_by_name_owner(access_token, user_id, source_playlist_name)
    if not source_playlist:
        raise RuntimeError(f"Could not find playlist '{source_playlist_name}' owned by this user.")
    source_playlist_id = source_playlist["id"]
    logging.info("Using source playlist '%s' (%s)", source_playlist_name, source_playlist_id)

    # Determine the cutoff date; only tracks added on/before this are eligible.
    cutoff_date = dt.date.today() - dt.timedelta(days=duration_days)
    # Collect candidate tracks with their added dates for later bucketing.
    candidates: List[Tuple[str, dt.date]] = []
    for item in paginate_playlist_items(access_token, source_playlist_id):
        added_at = item.get("added_at")
        track = item.get("track") or {}
        uri = track.get("uri")
        if not (added_at and uri):
            continue
        added_date = iso_to_date(added_at)
        if added_date <= cutoff_date:
            candidates.append((uri, added_date))

    logging.info(
        "Found %d tracks added on or before %s to move", len(candidates), cutoff_date.isoformat()
    )
    if not candidates:
        return

    run_timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    total_moved = 0
    dry_run_targets: List[str] = []
    if args.dry_run:
        logging.info("Dry run enabled; no Spotify write operations will be performed.")

    # Group tracks by year suffix so each batch goes to the right Junk Drawer.
    buckets = group_tracks_by_year_suffix(candidates)
    for year_suffix, tracks in buckets.items():
        playlist_name = f"{year_suffix} Junk Drawer"
        base_description = f"Junk drawer of tracks added in {year_suffix} from {source_playlist_name}"
        uris = [uri for uri, _ in tracks]

        if args.dry_run:
            dry_run_targets.append(playlist_name)
            existing = find_playlist_by_name_owner(access_token, user_id, playlist_name)
            if existing:
                logging.info(
                    "Dry run: would move %d tracks from '%s' to existing target playlist '%s' (%s)",
                    len(uris),
                    source_playlist_name,
                    playlist_name,
                    existing["id"],
                )
            else:
                logging.info(
                    "Dry run: would create target playlist '%s' and move %d tracks from '%s' into it",
                    playlist_name,
                    len(uris),
                    source_playlist_name,
                )
            logging.info(
                "Dry run: would remove %d tracks from source playlist '%s' (%s)",
                len(uris),
                source_playlist_name,
                source_playlist_id,
            )
            total_moved += len(uris)
            continue

        # Create/find the destination playlist for this year bucket.
        target_playlist_id = ensure_junk_drawer_playlist(
            access_token, user_id, playlist_name, base_description
        )
        # Move the tracks: add to the destination, then remove from the source.
        add_tracks_to_playlist(access_token, target_playlist_id, uris)
        remove_tracks_from_playlist(access_token, source_playlist_id, uris)
        total_moved += len(uris)

        description = f"{base_description}. Last run {run_timestamp} moved {len(uris)} tracks."
        update_playlist_description(access_token, target_playlist_id, description)
        logging.info(
            "Moved %d tracks to '%s' from '%s'",
            len(uris),
            playlist_name,
            source_playlist_name,
        )

    if args.dry_run:
        target_list = ", ".join(sorted(set(dry_run_targets)))
        logging.info(
            "Dry run complete; would have moved %d tracks from '%s' into junk drawers: %s.",
            total_moved,
            source_playlist_name,
            target_list,
        )
        return

    source_description = (
        f"{source_playlist_name} (managed by Junk Mover). "
        f"Last run {run_timestamp} moved {total_moved} tracks to junk drawers."
    )
    update_playlist_description(access_token, source_playlist_id, source_description)


if __name__ == "__main__":
    main()
