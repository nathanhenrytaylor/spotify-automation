"""
One-time repair: consolidate all "26 Junk Drawer" playlists into one canonical playlist.

Problem: junk_mover.py searches /me/playlists (followed playlists only) to find the
destination "26 Junk Drawer". If that playlist was ever unfollowed, junk_mover can't
find it and creates a new one. This happened at least 3 times, producing:

  - 1ImOXHT5NnzM4WZpk2RXtB  "26 Junk Drawer"  1 track   (oldest, unknown origin)
  - 6w4zHYpKICYALcKqPdAh1Y  "26 Junk Drawer"  27 tracks (rbp, pre-migration)
  - 6S2WoEPF7AEmZABbFyG00Q  "26 Junk Drawer"  11 tracks (clawslices, 2026-09-16)

Repair steps:
  1. Gather all unique tracks from all three playlists.
  2. Pick the canonical playlist (most tracks: 6w4zHYpKICYALcKqPdAh1Y).
  3. Add any missing tracks from the other two into the canonical one.
  4. Follow the canonical playlist so it appears in your Spotify library.
  5. Empty the other two playlists (can't delete via API, but empty + unfollowed = invisible).

Run from repo root:
  python3 Junk_Mover/repair_26_junk_drawer.py
  python3 Junk_Mover/repair_26_junk_drawer.py --dry-run
"""

import argparse
import base64
import json
import logging
import os
import sys
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[1]

# Known "26 Junk Drawer" playlist IDs (from investigation 2026-09-21)
DUPLICATE_IDS = [
    "1ImOXHT5NnzM4WZpk2RXtB",  # 1 track - oldest/unknown origin
    "6w4zHYpKICYALcKqPdAh1Y",  # 27 tracks - rbp pre-migration (CANONICAL)
    "6S2WoEPF7AEmZABbFyG00Q",  # 11 tracks - clawslices 2026-09-16
]
CANONICAL_ID = "6w4zHYpKICYALcKqPdAh1Y"  # Largest, oldest - becomes the one true drawer


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def fetch_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    data = parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode()
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = request.Request("https://accounts.spotify.com/api/token", data=data, method="POST")
    req.add_header("Authorization", f"Basic {creds}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with request.urlopen(req) as r:
        return json.load(r)["access_token"]


def api(method: str, url: str, token: str, data=None):
    payload = json.dumps(data).encode() if data is not None else None
    req = request.Request(url, data=payload, method=method.upper())
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req) as r:
            if r.status == 204:
                return {}
            body = r.read()
            return json.loads(body) if body else {}
    except error.HTTPError as e:
        detail = e.read().decode()
        raise RuntimeError(f"Spotify API {e.code}: {detail}") from e


def get_playlist_tracks(token: str, playlist_id: str) -> list:
    tracks = []
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks?limit=100"
    while url:
        page = api("GET", url, token)
        for item in page.get("items", []):
            track = item.get("track") or {}
            uri = track.get("uri")
            if uri and uri.startswith("spotify:track:"):
                tracks.append({
                    "uri": uri,
                    "name": track.get("name", "?"),
                    "artist": ", ".join(a["name"] for a in track.get("artists", [])),
                })
        url = page.get("next")
    return tracks


def add_tracks(token: str, playlist_id: str, uris: list, dry_run: bool) -> None:
    for i in range(0, len(uris), 100):
        chunk = uris[i:i + 100]
        if dry_run:
            logging.info("DRY RUN: would add %d tracks to %s", len(chunk), playlist_id)
        else:
            api("POST", f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
                token, data={"uris": chunk})
            logging.info("Added %d tracks to %s", len(chunk), playlist_id)


def remove_all_tracks(token: str, playlist_id: str, uris: list, dry_run: bool) -> None:
    for i in range(0, len(uris), 100):
        chunk = uris[i:i + 100]
        if dry_run:
            logging.info("DRY RUN: would remove %d tracks from %s", len(chunk), playlist_id)
        else:
            api("DELETE", f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
                token, data={"tracks": [{"uri": u} for u in chunk]})
            logging.info("Removed %d tracks from %s", len(chunk), playlist_id)


def follow_playlist(token: str, playlist_id: str, dry_run: bool) -> None:
    if dry_run:
        logging.info("DRY RUN: would follow playlist %s", playlist_id)
    else:
        api("PUT", f"https://api.spotify.com/v1/playlists/{playlist_id}/followers",
            token, data={"public": False})
        logging.info("Followed playlist %s (it now appears in your library)", playlist_id)


def main():
    parser = argparse.ArgumentParser(description="Consolidate duplicate 26 Junk Drawer playlists")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    load_env()
    token = fetch_access_token(
        os.environ["JUNK_MOVER_CLIENT_ID"],
        os.environ["JUNK_MOVER_CLIENT_SECRET"],
        os.environ["JUNK_MOVER_REFRESH_TOKEN"],
    )

    # Step 1: gather all tracks from all duplicates
    all_tracks_by_uri: dict = {}
    for pid in DUPLICATE_IDS:
        tracks = get_playlist_tracks(token, pid)
        label = "(CANONICAL)" if pid == CANONICAL_ID else "(duplicate)"
        logging.info("Playlist %s %s: %d tracks", pid, label, len(tracks))
        for t in tracks:
            all_tracks_by_uri[t["uri"]] = t

    logging.info("Total unique tracks across all duplicates: %d", len(all_tracks_by_uri))

    # Step 2: find what's already in canonical
    canonical_tracks = get_playlist_tracks(token, CANONICAL_ID)
    canonical_uris = {t["uri"] for t in canonical_tracks}
    logging.info("Canonical playlist already has: %d tracks", len(canonical_uris))

    # Step 3: determine what needs to be added to canonical
    missing_uris = [uri for uri in all_tracks_by_uri if uri not in canonical_uris]
    logging.info("Tracks to add to canonical: %d", len(missing_uris))
    for uri in missing_uris:
        t = all_tracks_by_uri[uri]
        logging.info("  + %s by %s", t["name"], t["artist"])

    # Step 4: add missing tracks to canonical
    if missing_uris:
        add_tracks(token, CANONICAL_ID, missing_uris, args.dry_run)

    # Step 5: follow the canonical playlist
    follow_playlist(token, CANONICAL_ID, args.dry_run)

    # Step 6: empty the other two playlists
    for pid in DUPLICATE_IDS:
        if pid == CANONICAL_ID:
            continue
        tracks = get_playlist_tracks(token, pid)
        if tracks:
            logging.info("Emptying duplicate %s (%d tracks)...", pid, len(tracks))
            remove_all_tracks(token, pid, [t["uri"] for t in tracks], args.dry_run)
        else:
            logging.info("Duplicate %s is already empty.", pid)

    if args.dry_run:
        logging.info("Dry run complete. Re-run without --dry-run to apply.")
    else:
        logging.info("Done! Canonical '26 Junk Drawer' (%s) now has %d tracks and is in your library.",
                     CANONICAL_ID, len(all_tracks_by_uri))
        logging.info("The 2 empty duplicate playlists remain (can't delete via Spotify API) "
                     "— you can remove them manually from your Spotify client if desired.")


if __name__ == "__main__":
    main()
