# Google OAuth setup

This app needs a Google Cloud OAuth client so each user can sign in and read/write the
shared Drive folder. This is a one-time project-level setup, plus a recurring
per-new-user step explained at the bottom.

## One-time project setup

1. Create (or reuse) a project at https://console.cloud.google.com/.
2. Enable the **Google Drive API** for that project (APIs & Services -> Library).
3. Configure the **OAuth consent screen**:
   - User type: **External**.
   - Keep publishing status **Testing**. `drive.readonly` and full `drive` are Google
     "restricted" scopes that require an annual CASA security assessment before they
     can be used in a verified production app - unrealistic for a small group. Testing
     status skips that, at the cost of a 100-test-user cap and the per-user step below.
4. Create an **OAuth Client ID**:
   - Application type: **Desktop app** (not "Web application"). This type supports the
     loopback redirect flow (`http://localhost:<port>/...`) this app uses, without
     needing to pre-register an exact redirect URI.
   - Download the resulting JSON and save it as `client_secret.json` (path is passed to
     the CLI via `--client-secrets`, defaults to `client_secret.json` in the current
     directory).
5. Add the scopes on the consent screen:
   - `https://www.googleapis.com/auth/drive.readonly`
   - `https://www.googleapis.com/auth/drive.file`

   (Two narrow scopes rather than the single broad `drive` scope: `drive.readonly`
   covers listing/downloading anything the signed-in user can already view - including
   peers' shared subfolders - while `drive.file` only covers creating/editing files
   this app itself creates.)

## Per-new-user step (ongoing, while the app stays in Testing)

Every person who wants to use the app must be added under **Audience -> Test users**
on the OAuth consent screen configuration, using the Google account they'll sign in
with. Without this, their sign-in attempt is rejected by Google before it reaches the
app. There's a 100-test-user cap while in this mode.

## First run

`client_secret.json` must be present (default: current working directory) before
running `white-soapstone sync-once`. The first sync opens a browser for the user to
sign in and grant access; the refresh token that results is stored in the OS
keychain (via `keyring`) so subsequent syncs don't prompt again unless the token is
revoked.
