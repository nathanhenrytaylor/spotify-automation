"""
Utility to guide you through obtaining a Spotify refresh token.

This flow is intentionally manual: Spotify requires the configured HTTPS redirect, so after
authorizing you must copy the `code` query param from the browser URL and paste it back here.
Local HTTPS capture would need certificates and is out of scope for this simple helper.

Steps when running:
1) It opens your browser to the authorize URL.
2) After redirect to the configured HTTPS URI, copy the entire redirected URL.
3) Paste that URL; the script extracts `code`, exchanges it, prints, and writes the refresh token to .env.
"""

import base64
import json
import os
import sys
from pathlib import Path
from typing import List
import webbrowser
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[1]


def load_env_from_root() -> None:
    # Centralize config by loading the repo-level .env so secrets stay out of code.
    """Load .env values from the project root into os.environ if present."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def require_env(keys: List[str]) -> None:
    # Fail fast with helpful messaging when required settings are absent.
    missing = [key for key in keys if not os.environ.get(key)]
    if missing:
        raise ValueError(
            "Missing environment variables: " + ", ".join(missing) + ". Fill them in .env."
        )


def build_authorize_url(client_id: str, redirect_uri: str, scope: str) -> str:
    # Construct the consent URL the user must open to grant offline access.
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
    }
    return "https://accounts.spotify.com/authorize?" + parse.urlencode(
        params, quote_via=parse.quote
    )


def exchange_code_for_tokens(
    client_id: str, client_secret: str, code: str, redirect_uri: str
) -> dict:
    # Exchange the short-lived code for the long-lived refresh token.
    data = parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
    ).encode()

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = request.Request("https://accounts.spotify.com/api/token", data=data, method="POST")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with request.urlopen(req) as resp:
            return json.load(resp)
    except error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"Failed to exchange code: {exc.status} {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Network error while exchanging code: {exc.reason}") from exc


def update_env_refresh_token(env_path: Path, refresh_token: str) -> None:
    # Persist the refresh token back into .env to avoid manual edits.
    if not env_path.exists():
        env_path.write_text(f"JUNK_MOVER_REFRESH_TOKEN={refresh_token}\n", encoding="utf-8")
        return

    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = False
    for idx, line in enumerate(lines):
        if line.startswith("JUNK_MOVER_REFRESH_TOKEN="):
            lines[idx] = f"JUNK_MOVER_REFRESH_TOKEN={refresh_token}"
            updated = True
            break
    if not updated:
        lines.append(f"JUNK_MOVER_REFRESH_TOKEN={refresh_token}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    load_env_from_root()

    # Use the configured redirect. Because it's HTTPS and not a local listener, this flow stays manual:
    # you copy the `code` from the redirected URL and paste it here.
    redirect_uri = os.environ.get(
        "JUNK_MOVER_REDIRECT_URI", "https://example.com/callback"
    )

    require_env(["JUNK_MOVER_CLIENT_ID", "JUNK_MOVER_CLIENT_SECRET"])

    client_id = os.environ["JUNK_MOVER_CLIENT_ID"]
    client_secret = os.environ["JUNK_MOVER_CLIENT_SECRET"]

    # Need playlist modification scopes to move tracks between playlists.
    scope = "user-read-email user-read-private playlist-modify-public playlist-modify-private"
    auth_url = build_authorize_url(client_id, redirect_uri, scope)

    print("Launching browser to authorize...")
    opened = webbrowser.open(auth_url)
    if not opened:
        print("If the browser did not open, manually visit this URL:")
        print(auth_url)

    print("After approving in the browser, copy the ENTIRE redirected URL and paste it here.")
    callback_url = input("Redirected URL: ").strip()

    parsed_callback = parse.urlparse(callback_url)
    code = (parse.parse_qs(parsed_callback.query).get("code") or [None])[0]

    if not code:
        print("No code found in the provided URL; exiting.")
        sys.exit(1)

    tokens = exchange_code_for_tokens(client_id, client_secret, code, redirect_uri)

    refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token")

    print("\nTokens received:")
    print(json.dumps(tokens, indent=2))

    if refresh_token:
        print("\nAdd this to your .env:")
        print(f"JUNK_MOVER_REFRESH_TOKEN={refresh_token}")
        update_env_refresh_token(ROOT / ".env", refresh_token)
        print("Persisted refresh token to .env.")
    else:
        print("\nNo refresh_token returned. Ensure you requested the correct scopes and that")
        print("the redirect URI matches exactly what is configured in your Spotify app.")

    if access_token:
        print("\nAccess token (short-lived) provided for convenience.")


if __name__ == "__main__":
    main()
