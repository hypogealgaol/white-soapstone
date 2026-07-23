"""Google OAuth for Drive access.

Uses the "Desktop app" OAuth client type and the loopback flow
(InstalledAppFlow.run_local_server), which needs no pre-registered redirect URI.
Only the refresh token is persisted, via the OS keychain (through `keyring`) - access
tokens are always re-derived in memory. See docs/OAUTH_SETUP.md for the one-time
Google Cloud Console setup this depends on.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import keyring
import keyring.errors
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# drive.readonly covers listing/downloading anything the user can already view
# (including peers' shared subfolders); drive.file covers creating/editing only the
# files this app itself creates. Two narrow scopes instead of one broad `drive` scope.
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

_KEYRING_SERVICE = "white-soapstone"
_KEYRING_USERNAME = "google-oauth-token"


class AuthError(Exception):
    """Raised when Google credentials can't be obtained or refreshed."""


def _load_cached_credentials() -> Credentials | None:
    raw = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    if not raw:
        return None
    return Credentials.from_authorized_user_info(json.loads(raw), SCOPES)


def _store_credentials(creds: Credentials) -> None:
    keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, creds.to_json())


def sign_out() -> None:
    try:
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass


def get_credentials(
    client_secrets_path: str | Path,
    on_auth_required: Callable[[], None] | None = None,
) -> Credentials:
    """Returns valid Credentials, running the interactive browser sign-in only if
    there's no usable cached refresh token.

    `on_auth_required`, if given, fires right before blocking on the interactive
    sign-in (a browser window is opened, but it doesn't reliably grab focus when
    launched from a background thread of a windowed app) - callers use this to surface
    "waiting for you to sign in" somewhere a user will actually notice it, rather than
    a generic "syncing" status with no indication it's stuck on an external action.
    """
    creds = _load_cached_credentials()

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001 - refresh token revoked/invalid, re-auth
            raise AuthError(f"Failed to refresh Google credentials, re-auth required: {exc}") from exc
        _store_credentials(creds)
        return creds

    client_secrets_path = Path(client_secrets_path)
    if not client_secrets_path.exists():
        raise AuthError(
            f"No Google OAuth client secrets file found at '{client_secrets_path}'. "
            "See docs/OAUTH_SETUP.md."
        )

    if on_auth_required:
        on_auth_required()

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    _store_credentials(creds)
    return creds
