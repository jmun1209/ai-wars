"""One-time TikTok OAuth 2.0 + PKCE helper — manual callback flow, no server needed."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import urllib.parse
import webbrowser
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

load_dotenv()

_ENV_FILE = Path(__file__).parent / ".env"
_REDIRECT_URI = "https://jmun1209.github.io/ai-wars/docs/callback.html"
_SCOPES = "user.info.basic,video.upload"
_AUTH_BASE = "https://www.tiktok.com/v2/auth/authorize/"
_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"


def _generate_pkce() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256 method).

    Returns:
        Tuple of (code_verifier, code_challenge).
    """
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def _build_auth_url(client_key: str, state: str, code_challenge: str) -> str:
    """Build the TikTok OAuth authorization URL with PKCE."""
    params = {
        "client_key": client_key,
        "scope": _SCOPES,
        "response_type": "code",
        "redirect_uri": _REDIRECT_URI,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{_AUTH_BASE}?{urllib.parse.urlencode(params)}"


def _exchange_code(code: str, client_key: str, client_secret: str, code_verifier: str) -> dict:
    """Exchange an authorization code + PKCE verifier for an access token."""
    resp = requests.post(
        _TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": _REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _extract_code_from_url(pasted_url: str) -> tuple[str, str]:
    """Parse the code and state from the pasted callback URL.

    Args:
        pasted_url: The full URL from the browser address bar after redirect.

    Returns:
        Tuple of (code, state).

    Raises:
        ValueError: If code or state are missing from the URL.
    """
    parsed = urllib.parse.urlparse(pasted_url)
    params = dict(urllib.parse.parse_qsl(parsed.query))
    code = params.get("code", "")
    state = params.get("state", "")
    if not code:
        raise ValueError("No 'code' parameter found in the URL. Did you copy the full address bar URL?")
    return code, state


def main() -> None:
    """Run the one-time TikTok OAuth flow (manual callback — no local server needed)."""
    client_key = os.environ.get("TIKTOK_CLIENT_KEY", "")
    client_secret = os.environ.get("TIKTOK_CLIENT_SECRET", "")

    if not client_key or not client_secret:
        print("ERROR: TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET must be set in .env")
        return

    state = secrets.token_urlsafe(16)
    code_verifier, code_challenge = _generate_pkce()
    auth_url = _build_auth_url(client_key, state, code_challenge)

    print("\n" + "=" * 60)
    print("TIKTOK AUTH SETUP")
    print("=" * 60)
    print("\nStep 1 — Make sure your TikTok app has this redirect URI registered:")
    print(f"\n  {_REDIRECT_URI}\n")
    print("  (developers.tiktok.com → your app → Login Kit → Redirect URI)\n")
    print("Step 2 — Opening your browser to authorize…")
    print("  After you approve, you'll land on a page at jmun1209.github.io")
    print("  that shows your authorization code. Copy the 'code' value.\n")

    webbrowser.open(auth_url)

    print("-" * 60)
    pasted = input("Paste the full callback URL OR just the code value here:\n> ").strip()

    # Accept either a full URL or a bare code
    if pasted.startswith("http"):
        try:
            code, received_state = _extract_code_from_url(pasted)
        except ValueError as exc:
            print(f"\nERROR: {exc}")
            return
    else:
        # Bare code pasted directly
        code = pasted
        received_state = state  # trust it since we opened the URL ourselves

    if received_state != state:
        print("\nERROR: State mismatch — the URL may be from a different session. Run the script again.")
        return

    print("\nExchanging code for access token…")
    try:
        token_data = _exchange_code(code, client_key, client_secret, code_verifier)
    except requests.HTTPError as exc:
        print(f"\nERROR: Token exchange failed: {exc.response.text}")
        return

    access_token = token_data.get("access_token", "")
    open_id = token_data.get("open_id", "")

    if not access_token:
        print(f"\nERROR: No access_token in response: {token_data}")
        return

    set_key(str(_ENV_FILE), "TIKTOK_ACCESS_TOKEN", access_token)
    set_key(str(_ENV_FILE), "TIKTOK_OPEN_ID", open_id)

    print("\n✓ Success! Saved to .env:")
    print(f"  TIKTOK_ACCESS_TOKEN = {access_token[:12]}…")
    print(f"  TIKTOK_OPEN_ID      = {open_id}")
    print("\nYou can now run: python app.py")


if __name__ == "__main__":
    main()
