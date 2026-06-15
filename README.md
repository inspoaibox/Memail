<div align="center">

<img src="docs/banner.svg" alt="Memail" width="700">

**Lightweight self-hosted mail service — one-click deploy, ready to use**

SMTP Receiver &bull; REST API &bull; Web Viewer &bull; IMAP Bridge

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Flask](https://img.shields.io/badge/Flask-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![Node.js](https://img.shields.io/badge/Node.js-20-339933?logo=node.js&logoColor=white)](https://nodejs.org)
[![MongoDB](https://img.shields.io/badge/MongoDB-7-47A248?logo=mongodb&logoColor=white)](https://www.mongodb.com)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**[中文文档](README_CN.md)**

---

<img src="docs/screenshot.jpg" alt="Memail Screenshot" width="900">

<sub>*All emails shown in the screenshot are for testing purposes only and have no real-world significance.*</sub>

</div>

## Overview

Memail is a complete self-hosted mail solution with three core services:

| Service | Stack | Port | Description |
|:--------|:------|:-----|:------------|
| **mail-service** | FastAPI + aiosmtpd | `:25` `:8080` | SMTP receiver + DuckMail-compatible REST API |
| **mail-viewer** | Flask + bleach | `:5000` | Web mail viewer with search, reply & compose |
| **imap-bridge** | Node.js + imapflow | `:3939` | IMAP bridge for Gmail / Outlook / QQ etc. |

<br>

## Architecture

```
    Internet                              Your Server
    ────────                              ───────────
                      ┌──────────────────────────────────────────────┐
                      │                                              │
   Incoming     ──────┤►  mail-service        ┌───────────────┐     │
   Email              │   (FastAPI+aiosmtpd)  │   MongoDB 7   │     │
   (SMTP :25)         │   ┌──────────────┐    │   ┌─────────┐ │     │
                      │   │ SMTP Handler │────┤►  │ accounts│ │     │
                      │   │ REST API     │◄───┤   │ messages│ │     │
                      │   └──────┬───────┘    │   │ domains │ │     │
                      │          │ :8080      └───┴─────────┘─┘     │
                      │          │                                    │
                      │          ▼                                    │
   Browser    ────────┤►  mail-viewer          imap-bridge           │
   (HTTP :5000)       │   (Flask)              (Node.js)             │
                      │   ┌──────────────┐    ┌──────────────┐      │
                      │   │ Inbox View   │    │ Gmail        │      │
                      │   │ Search       │◄───┤ Outlook      │      │
                      │   │ Reply / Send │    │ QQ / 163     │      │
                      │   │ HTML Sanitize│    │ Yahoo / GMX  │      │
                      │   └──────────────┘    └──────────────┘      │
                      │                         :3939                 │
                      └──────────────────────────────────────────────┘
```

<br>

## Quick Start

### 1. Clone & Configure

```bash
git clone https://github.com/inspoaibox/Memail.git
cd Memail
cp .env.example .env
```

Edit only the required values first:

```env
APP_SECRET=your-long-random-bootstrap-secret
ADMIN_USERNAME=admin
ACCESS_PASSWORD=your-viewer-password

SMTP_HOSTNAME=mail.yourdomain.com
DOMAINS=yourdomain.com
```

Most other values in `.env.example` are optional. `APP_SECRET` is used as the default JWT/API/session/encryption secret unless you override the advanced keys. `ADMIN_USERNAME` is the Web UI admin login name; you can start with `admin` and change it later from Web UI Settings > System Settings. Runtime settings take precedence over `.env`. Gmail OAuth2, Resend, the unified mailbox password, and the admin login password are configured later from the Web UI gear icon.

For production, prefer a hashed admin password instead of relying only on plain `ACCESS_PASSWORD`:

```bash
python - <<'PY'
from werkzeug.security import generate_password_hash
print(generate_password_hash("replace-with-a-strong-password"))
PY
```

Then add it to `.env`:

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=the-generated-hash
SESSION_TIMEOUT_MINUTES=60
LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCK_MINUTES=15
```

When `ADMIN_PASSWORD_HASH` is configured, it takes precedence; the legacy `ACCESS_PASSWORD` is only a bootstrap fallback and is not accepted as a second login password. The admin username is required on the login page: if it has not been changed in Web UI settings, use `ADMIN_USERNAME`; if that is not set, the default is `admin`.

### 2. Start With Local Builds

> If `docker compose` prints `unknown command: docker compose`, your server does not have the Docker Compose v2 plugin. Use the `docker-compose` commands below instead.

```bash
docker-compose up -d --build
docker-compose ps
```

Use this mode when you are building directly from the source code on your server. Do not set `MAIL_SERVICE_IMAGE`, `MAIL_VIEWER_IMAGE`, `IMAP_MAIL_IMAGE`, or `IMAP_SERVER_IMAGE` in `.env`.

Memail persists MongoDB data, runtime settings, external mailbox accounts, and the external IMAP mail cache. For routine updates, keep volumes and do not use `docker-compose down -v`.

When updating from source, rebuild the images. Otherwise the running containers can still use old templates baked into the previous image:

```bash
git pull
docker-compose build mail-service mail-viewer imap-mail imap-server
docker-compose up -d --no-deps mail-service imap-mail imap-server mail-viewer
docker-compose ps
```

If you already ran `git pull` but the web page did not change, rebuild and recreate the containers:

```bash
docker-compose build --no-cache mail-viewer imap-mail imap-server mail-service
docker-compose stop mail-viewer imap-mail imap-server mail-service
docker-compose rm -f mail-viewer imap-mail imap-server mail-service
docker-compose up -d --no-deps mail-service imap-mail imap-server mail-viewer
docker-compose ps
```

Do not run a full `docker-compose up -d --force-recreate` for routine source updates. With old `docker-compose 1.29.x` and newer Docker engines, recreating existing containers can fail with `KeyError: 'ContainerConfig'`; MongoDB also does not need to be recreated for frontend or service code updates.

If you already hit `KeyError: 'ContainerConfig'`, remove the temporary old containers left by Compose and start the services again. Do not use `-v`; volumes hold MongoDB data, external IMAP accounts, and runtime settings:

```bash
docker ps -a --format '{{.Names}}' | grep -E '^[0-9a-f]+_mail-' | xargs -r docker rm -f
docker-compose up -d mongodb
docker-compose up -d --no-deps mail-service imap-mail imap-server mail-viewer
docker-compose ps
```

### 3. Start With GitHub-Built Images

Use this mode when you want to run the Docker images built by GitHub Actions for [inspoaibox/Memail](https://github.com/inspoaibox/Memail). Your server only pulls the GHCR images and runs them. The Docker workflow builds and publishes images to GitHub Container Registry on pushes to `main` / `master`, `v*.*.*` tags, or manual workflow runs. Pull requests build for validation only and do not publish images.

```text
ghcr.io/inspoaibox/manymail-mail-service
ghcr.io/inspoaibox/manymail-mail-viewer
ghcr.io/inspoaibox/manymail-imap-mail
ghcr.io/inspoaibox/manymail-imap-server
```

GitHub Actions only builds and publishes images. It does not automatically SSH into your server or restart Docker Compose unless you add a separate deploy workflow.

After pushing code to GitHub, open the repository's **Actions** tab and wait for the **Docker** workflow to finish successfully. Then set these image names in `.env` on your server. For the official repository, you can use the `inspoaibox` image names directly. For your own fork, replace `inspoaibox` with your lowercase GitHub username or organization. Use the branch tag you build from, usually `main` or `master`:

```env
MAIL_SERVICE_IMAGE=ghcr.io/inspoaibox/manymail-mail-service:main
MAIL_VIEWER_IMAGE=ghcr.io/inspoaibox/manymail-mail-viewer:main
IMAP_MAIL_IMAGE=ghcr.io/inspoaibox/manymail-imap-mail:main
IMAP_SERVER_IMAGE=ghcr.io/inspoaibox/manymail-imap-server:main
```

Available tag patterns:

- `main` / `master`: latest image for that branch.
- `v1.2.3`: image for a release tag.
- `sha-xxxxxxx`: exact commit image tag shown in GitHub Actions or Packages.

If the GitHub Container Registry package is public, the server can pull it without logging in. If it is private, log in first with a token that has at least `read:packages` permission:

```bash
echo <github-token> | docker login ghcr.io -u <github-username> --password-stdin
```

Start the services:

```bash
docker-compose pull
docker-compose up -d
docker-compose ps
```

If your server uses Docker Compose v2, you can use `docker compose` instead of `docker-compose`.

`docker-compose pull` only pulls GitHub-built images when the four `*_IMAGE` variables above are set in `.env`. If they are not set, Compose uses the local `manymail-*:local` images built from source; in that mode, use the local-build update commands from section 2.

To update after new code is pushed:

```bash
# If docker-compose.yml or docs changed on the server copy, update the repo first.
git pull

# Pull the newly built GHCR images and recreate changed containers.
docker-compose pull
docker-compose up -d
docker-compose ps
```

Your `.env` file and Docker volumes are kept. Do not run `docker-compose down -v` unless you intentionally want to delete persisted MongoDB data, external IMAP accounts, and runtime settings.

External IMAP accounts, folders, message lists, cached message bodies, and runtime settings are stored in Docker volumes / MongoDB. By default, external IMAP lists and opened message bodies are cached for 24 hours; the refresh button still forces a remote sync. Override `IMAP_CACHE_TTL_SECONDS` in `.env` if you need a different TTL. The default is `86400`.

The background sync scheduler is enabled by default: after startup it waits about 20 seconds, then checks external IMAP accounts every 15 minutes. Fresh accounts are skipped until their TTL expires. The small sync label beside an account name is only a status indicator and does not need to be clicked; refreshing “All Accounts” queues all external accounts for background sync and immediately shows existing cache, while refreshing inside one external account syncs the current folder immediately. Tune or disable it with `IMAP_SYNC_CHECK_INTERVAL_SECONDS`, `IMAP_SYNC_STARTUP_DELAY_SECONDS`, or `IMAP_SYNC_SCHEDULER_ENABLED=0`.

Drafts, failed-send records, and sent records are persisted with the viewer settings. The compose screen opens inline in the right reading pane and uses the currently selected mailbox as the sender. “Save Draft” stores the message in the selected account’s “App Drafts” view, failed sends go to “Failed Send”, and both local accounts and external SMTP accounts can retry from the failed-send record. Compose supports To, Cc, Bcc, and attachments; drafts and failed-send records preserve these fields. Remote IMAP Drafts/Sent folders still appear as normal provider folders when available.

### Security And Multi-Device Sync

The Web UI settings page includes security controls:

- TOTP / 2FA setup and enablement.
- Login session list with IP/User-Agent and revoke support.
- Device Tokens for API and future sync integrations. Tokens are shown once.
- Audit logs for logins, sensitive confirmations, settings changes, device-token actions, and send success/failure.

Sensitive actions require secondary confirmation: saving system settings, deleting local mailboxes, deleting external accounts, permanent deletion, creating/revoking device tokens, and revoking login sessions. Confirmation checks the admin password and, if enabled, the 6-digit TOTP code.

Multi-device sync endpoints are available for future integrations:

```http
GET  /api/sync/bootstrap   # Initial sync: mailbox config, drafts, failed-send records, protocol
GET  /api/sync/changes     # Incremental pull: ?since=<sync_seq>&limit=200
POST /api/sync/push        # Offline push: currently draft.upsert / draft.delete
```

Use a device token:

```http
Authorization: Bearer memail_dev_xxx
```

### Mail Data Extraction API

The Web UI “Keyword Rules” settings pane includes an “Extraction Rules” form and an “Extraction API Guide” with copyable examples. For third-party systems, create an `extraction:read` read-only token in “Security → Device Tokens”. Rule creation and manual scans should still use a logged-in admin session or a full client token.

Extraction rules are not hard-coded to one mail template. They can be adapted to different HTML, plain-text, or subject formats:

- Sender contains, subject contains, and body keywords pre-filter candidate mail.
- Order regexes support multiple lines, one template per line.
- Tracking regexes support multiple lines, one template per line.
- Carrier regex extracts the carrier near a tracking number.
- The background scanner stores a per-rule, per-account, per-folder UID cursor with a small lookback window, so large mailboxes are not rescanned from scratch every time.
- Extraction results are stored in the MongoDB `extraction_results` collection by default, with pagination and filters for rule, order number, tracking number, and account email. Legacy settings-based results are migrated automatically.

The built-in Aosom shipped-order template creates both Canada and US rules for `noreply@aosom.ca` and `noreply@aosom.com`. It extracts order numbers and supports multiple packages, carriers, and tracking numbers in one email, including UPS, FedEx, OnTrac, USPS, DHL, and similar shipment formats.

After logging in as admin, use `GET /api/extraction/storage` to verify the active result store. A normal Docker deployment should report `store: "mongo"`.

Create Aosom CA/US extraction rules and scan immediately:

```bash
curl -X POST "https://mail.yourdomain.com/api/extraction-rules/defaults/aosom-shipped" \
  -H "Authorization: Bearer <client-full-token>" \
  -H "Content-Type: application/json" \
  -d '{"account_emails":["nfksuk@gmail.com"],"scan_now":true}'
```

Read structured results:

```bash
curl "https://mail.yourdomain.com/api/extraction/results?limit=100" \
  -H "Authorization: Bearer <extraction-read-token>"
```

Common filters:

```http
GET /api/extraction/results?rule_id=<rule_id>
GET /api/extraction/results?order_number=2B129800035555
GET /api/extraction/results?tracking_number=1Z1B0W642008640330
GET /api/extraction/results?account_email=nfksuk@gmail.com&limit=100&offset=0
```

Manual scan:

```http
POST /api/extraction/scan
POST /api/extraction-rules/<rule_id>/scan
```

Example result:

```json
{
  "success": true,
  "total": 1,
  "results": [
    {
      "order_number": "2B129800035555",
      "shipments": [
        {
          "carrier": "UPS",
          "tracking_number": "1Z1B0W642008640330",
          "item_ref": "84D-264V00BN-1/1"
        },
        {
          "carrier": "UPS",
          "tracking_number": "1Z1B0W642014357126",
          "item_ref": "84D-264V00BN-1/1"
        }
      ]
    }
  ]
}
```

The current conflict policy is `server-wins`: offline draft edits must include the latest `version`; if the server has a newer version, the API returns a conflict and the client should ask whether to overwrite or save a copy. Message-body cache is treated as immutable snapshots; clients watch `/api/sync/changes` for invalidation events and then refetch as needed.

To roll back, change the image tag in `.env` to an older tag, such as a release tag or `sha-...` tag shown in GitHub Actions, then run `docker-compose pull && docker-compose up -d`.

### Runtime Settings In The Web UI

Memail no longer requires every operational setting to be baked into `.env`. After Docker starts, log in to the web UI and click the gear icon to configure:

- Resend API Key for outbound mail.
- Unified mailbox password used by the built-in inbox viewer.
- Admin login password for rotating the Web UI administrator password.
- Gmail / Outlook OAuth2 Public Base URL, Client ID, and Client Secret. Redirect URIs are generated by the app, shown read-only, and are not manually editable.

These settings are saved to Docker volumes and survive container restarts, image rebuilds, and GitHub-built image updates. Sensitive values are encrypted with `CONFIG_ENCRYPTION_KEY`; if it is omitted, `docker-compose.yml` falls back to `IMAP_ACCOUNTS_SECRET`, then `SECRET_KEY`, then `APP_SECRET`.

Keep `.env` for bootstrapping values that must exist before the UI can start, such as database connection, login password, API keys used between internal services, session/JWT secrets, and encryption keys.

### Gmail External IMAP: App Password Or OAuth2

The external IMAP aggregator persists account configuration and encrypts sensitive credentials with `IMAP_ACCOUNTS_SECRET`, falling back to `APP_SECRET` in the default Compose setup.

Gmail supports two connection modes:

- **App password**: for personal Gmail accounts, enable 2-Step Verification and create a 16-character Google app password. In Memail, add an external account with `Gmail / Google` or auto-detect and paste that app password. A normal Google account password will not work for IMAP/SMTP.
- **OAuth2 sign-in**: useful for long-term use, Workspace accounts, or avoiding stored mailbox passwords. OAuth2 uses the `https://mail.google.com/` scope, which is a sensitive/restricted Gmail scope; public multi-user apps may need Google review. For personal/internal use, configure the OAuth app and test users in Google Cloud.

To configure Gmail OAuth2:

1. Create a Web application OAuth Client in Google Cloud Console.
2. In Memail settings, enter the Public Base URL, for example:

```text
https://mail.yourdomain.com
```

3. Memail generates the read-only callback URL. Copy it to Google Cloud Console as an Authorized redirect URI:

```text
https://mail.yourdomain.com/imap/api/oauth/gmail/callback
```

4. Open the Memail web UI, click the gear icon, and fill in:

   - Public Base URL: `https://mail.yourdomain.com`
   - Google Client ID
   - Google Client Secret
   - Redirect URI: generated by Memail and shown read-only

For Google Workspace, IMAP, app passwords, and third-party clients can be restricted by administrator policy. If password login is rejected even with a correct app password, enable the relevant access as an admin or use OAuth2.

### Outlook / Hotmail External Mail

Outlook.com supports manual IMAP/SMTP server settings:

- IMAP: `outlook.office365.com:993`, SSL/TLS
- SMTP: `smtp-mail.outlook.com:587`, STARTTLS

Manual server settings do not always mean plain email+password Basic Auth is accepted. Microsoft lists OAuth2/Modern Auth as the authentication method. Memail still lets you try manual email+password/app-password login from Add External; if the server returns `basic authentication is disabled` or `535 5.7.139`, use Outlook OAuth2 sign-in or confirm that app passwords, IMAP, and SMTP AUTH are allowed for the Microsoft account or tenant.

For Microsoft OAuth2, the Azure / Microsoft Entra app Redirect URI must be the read-only value generated in Memail settings:

```text
https://mail.yourdomain.com/imap/api/oauth/outlook/callback
```

### 4. Verify

```bash
# Check all services
docker compose ps

# View logs
docker compose logs -f

# Health check
curl http://127.0.0.1:8080/health
```

<br>

## DNS Setup

Add these DNS records for your domain:

```dns
; MX record — tells other mail servers where to deliver
yourdomain.com.       IN  MX   10  mail.yourdomain.com.

; A record — points to your server IP
mail.yourdomain.com.  IN  A        <your-server-ip>

; SPF record — declares which IPs may send for your domain
yourdomain.com.       IN  TXT      "v=spf1 ip4:<your-server-ip> -all"

; DKIM record — email signature verification (generate key pair first)
default._domainkey.yourdomain.com.  IN  TXT  "v=DKIM1; k=rsa; p=<your-public-key>"

; DMARC record — policy for failed SPF/DKIM checks
_dmarc.yourdomain.com.  IN  TXT  "v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com"
```

> **STARTTLS**: Memail supports STARTTLS. Mount your TLS certificate and key via `docker-compose.yml` and set `SMTP_TLS_CERT` / `SMTP_TLS_KEY` in `.env`. See `.env.example` for details.

<br>

## API Reference

> Base URL: `http://127.0.0.1:8080`
>
> Auth: `Authorization: Bearer <token>` (except `/health`, `/token`)
>
> In production, `REQUIRE_API_KEY_FOR_ACCOUNTS=1` by default, so account creation also requires `Authorization: Bearer <API_KEY>` or `X-API-Key`.

### Account

```http
POST /accounts              # Create mailbox account
POST /token                 # Login, returns JWT
```

### Messages

```http
GET  /messages              # List inbox (paginated: ?offset=0&limit=30)
GET  /messages/{id}         # Message detail
GET  /messages/search?q=    # Full-text search
PATCH /messages/{id}        # Mark read / delete
GET  /sent                  # Sent messages
```

### System

```http
GET  /health                # Health check (no auth required)
GET  /domains               # Active domain list
```

<details>
<summary><strong>Example: Create account & read inbox</strong></summary>

```bash
# Create account
curl -X POST http://127.0.0.1:8080/accounts \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{"address": "user@yourdomain.com", "password": "secret123"}'

# Get token
TOKEN=$(curl -s -X POST http://127.0.0.1:8080/token \
  -H "Content-Type: application/json" \
  -d '{"address": "user@yourdomain.com", "password": "secret123"}' \
  | jq -r '.token')

# List messages
curl http://127.0.0.1:8080/messages \
  -H "Authorization: Bearer $TOKEN"
```

</details>

<br>

## Project Structure

```
Memail/
│
├── mail-service/                # SMTP + REST API
│   ├── app.py                   #   FastAPI main app
│   ├── Dockerfile               #   Python 3.11-slim
│   └── requirements.txt         #   fastapi, aiosmtpd, pymongo, jwt, bcrypt
│
├── mail-viewer/                 # Web Mail Viewer
│   ├── app.py                   #   Flask main app
│   ├── Dockerfile               #   Python 3.11-slim + gunicorn
│   ├── requirements.txt         #   flask, bleach, tinycss2
│   ├── templates/
│   │   ├── index.html           #   Inbox UI
│   │   └── login.html           #   Login page
│   └── imap-mail-app/           #   IMAP Bridge (Node.js)
│       ├── server.js            #     Express REST API
│       ├── client.js            #     ImapFlow wrapper
│       ├── config.js            #     Provider presets
│       └── package.json         #     imapflow, mailparser
│
├── docker-compose.yml           # All 4 services orchestration
├── .env.example                 # Environment variable template
├── deploy.sh                    # Deployment script
├── test_smtp.py                 # SMTP tests
└── test_external_smtp.py        # External SMTP tests
```

<br>

## Security

| Layer | Feature |
|:------|:--------|
| **Auth** | JWT tokens (24h expiry) + API Key for account creation and admin endpoints |
| **Password** | bcrypt hashing |
| **Rate Limit** | Per-IP throttling on both API and SMTP |
| **SMTP** | IP blacklist / greylist, recipient limits, size limits |
| **Email Render** | HTML sanitization (bleach + CSSSanitizer), iframe sandbox |
| **Network** | Server-side image proxy (prevents IP leakage) |
| **Storage** | Auto-cleanup via MongoDB TTL index (default 3 days) |
| **Web** | Login-protected viewer, hashed admin password support, failed-login lockout, session timeout, HttpOnly session cookies |
| **Request Protection** | CSRF token validation for write requests plus anti-framing, MIME-sniffing, and referrer security headers |
| **Credential Storage** | External IMAP accounts and runtime settings are saved encrypted with `IMAP_ACCOUNTS_SECRET` / `CONFIG_ENCRYPTION_KEY` / `APP_SECRET` fallback |

<br>

## Tech Stack

<table>
<tr>
<td align="center" width="150"><br><strong>Python 3.11</strong><br>FastAPI &bull; Flask<br><br></td>
<td align="center" width="150"><br><strong>Node.js 20</strong><br>Express &bull; ImapFlow<br><br></td>
<td align="center" width="150"><br><strong>MongoDB 7</strong><br>pymongo<br><br></td>
<td align="center" width="150"><br><strong>Docker</strong><br>Compose<br><br></td>
</tr>
</table>

| Component | Dependencies |
|:----------|:-------------|
| mail-service | `fastapi` `uvicorn` `aiosmtpd` `pymongo` `PyJWT` `bcrypt` |
| mail-viewer | `flask` `gunicorn` `requests` `bleach` `tinycss2` |
| imap-bridge | `express` `imapflow` `mailparser` `dotenv` |

<br>

## Community

Memail appreciates [linux.do](https://linux.do/) — a friendly Chinese tech community where people share ideas, projects, and practical self-hosting experience.

<br>

## License

[MIT](LICENSE)

---

<div align="center">
<sub>Built for self-hosting. Own your email infrastructure.</sub>
</div>
