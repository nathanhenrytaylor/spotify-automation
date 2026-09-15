import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[1]


def load_env_from_root() -> None:
    # Keep configuration in one place by reading the repo-level .env at runtime.
    """Load .env values from the project root into os.environ if present."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        if not line or line.lstrip().startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())


def require_env(keys: List[str]) -> None:
    # Fail fast with a clear message if required secrets/config are missing.
    missing = [key for key in keys if not os.environ.get(key)]
    if missing:
        raise ValueError(
            "Missing environment variables: " + ", ".join(missing) + ". Fill them in .env."
        )


def fetch_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    # Use refresh token so the script can run unattended without user interaction.
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


def fetch_profile(access_token: str) -> Dict[str, Any]:
    # Pull the current user's profile to confirm authentication works.
    req = request.Request("https://api.spotify.com/v1/me")
    req.add_header("Authorization", f"Bearer {access_token}")

    try:
        with request.urlopen(req) as resp:
            return json.load(resp)
    except error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"Failed to fetch profile: {exc.status} {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Network error while fetching profile: {exc.reason}") from exc


def main() -> None:
    load_env_from_root()

    required_keys = [
        "JUNK_MOVER_CLIENT_ID",
        "JUNK_MOVER_CLIENT_SECRET",
        "JUNK_MOVER_REFRESH_TOKEN",
    ]
    require_env(required_keys)

    access_token = fetch_access_token(
        client_id=os.environ["JUNK_MOVER_CLIENT_ID"],
        client_secret=os.environ["JUNK_MOVER_CLIENT_SECRET"],
        refresh_token=os.environ["JUNK_MOVER_REFRESH_TOKEN"],
    )

    profile = fetch_profile(access_token)
    print(json.dumps(profile, indent=2))

    display_name = profile.get("display_name") or profile.get("id")
    if display_name:
        print(f"\nFetched profile for: {display_name}")


if __name__ == "__main__":
    main()
