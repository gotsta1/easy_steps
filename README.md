# EasySteps Backend

Telegram access automation backend.
Lava.top payments → Postgres entitlements → Access Bot approves/declines channel join requests.

---

## Architecture overview

```
Lava.top  ──POST /lava/webhook──►  FastAPI  ──► EntitlementService ──► Postgres
                                                                          │
BotHelp   ──POST /payments/create──► FastAPI ──► Lava invoice             │
          ──POST /payments/check ──► FastAPI ──► TG Bot API (invite link) │
                                                                          │
Telegram  ──POST /tg/access/webhook──► aiogram dispatcher                │
                                         └─► can_approve_join() ◄────────┘
```

**Stack:** Python 3.12 · FastAPI · aiogram v3 · SQLAlchemy 2.0 async · asyncpg · Alembic · Postgres · Docker · Railway

---

## Local development

### 1. Clone & configure

```bash
git clone <repo>
cd easy_steps
cp .env.example .env
# Edit .env — at minimum set ACCESS_BOT_TOKEN, TG_CHANNEL_ID, LAVA_SECRET,
# ADMIN_TOKEN, ACCESS_BOT_SECRET_TOKEN, and APP_PUBLIC_BASE_URL.
```

### 2. Start services

```bash
docker compose up --build
```

The app starts on `http://localhost:8000`.
Postgres is available at `localhost:5432` (user/pass/db: `easysteps`).

### 3. Run migrations (first time & after schema changes)

```bash
# While docker compose is running:
docker compose exec app alembic upgrade head

# Or directly against the local DB:
DATABASE_URL=postgresql+asyncpg://easysteps:easysteps@localhost/easysteps \
  alembic upgrade head
```

### 4. Create a new migration after model changes

```bash
alembic revision --autogenerate -m "describe your change"
# Review the generated file in migrations/versions/, then:
alembic upgrade head
```

### 5. Run tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `APP_ENV` | no | `dev` | `dev` or `prod` |
| `APP_HOST` | no | `0.0.0.0` | Bind address |
| `APP_PORT` | no | `8000` | Bind port |
| `APP_PUBLIC_BASE_URL` | **yes** | — | Public HTTPS URL, e.g. `https://app.up.railway.app` |
| `DATABASE_URL` | **yes** | — | Postgres URL; `postgres://` is auto-rewritten |
| `ACCESS_BOT_TOKEN` | **yes** | — | BotFather token for the Access Bot |
| `ACCESS_BOT_WEBHOOK_PATH` | no | `/tg/access/webhook` | Path where Telegram sends updates |
| `ACCESS_BOT_SECRET_TOKEN` | **yes** | — | Random secret passed to `setWebhook`; validated on every update |
| `TG_CHANNEL_ID` | **yes** | — | Numeric channel ID, e.g. `-1001234567890` |
| `TG_MENU_CHANNEL_ID` | no | `0` | Menu channel ID; `0` disables menu flows |
| `KICK_ON_EXPIRE` | no | `false` | Kick expired members from the channel |
| `KICK_GRACE_SECONDS` | no | `0` | Extra seconds after `active_until` before kicking |
| `KICK_CRON_SECONDS` | no | `3600` | How often the kick job runs |
| `LAVA_WEBHOOK_PATH` | no | `/lava/webhook` | Path where Lava sends webhooks |
| `LAVA_OFFER_MENU` | no | `` | Lava offer ID for one-time menu product |
| `LAVA_SECRET` | **yes** | — | HMAC-SHA256 signing secret from Lava dashboard |
| `LAVA_PRODUCT_KEY_CLUB` | no | `club_monthly` | Internal key for the club subscription |
| `ADMIN_TOKEN` | **yes** | — | `X-Admin-Token` header value for admin endpoints |
| `LOG_LEVEL` | no | `INFO` | Python logging level |
| `SENTRY_DSN` | no | — | Optional Sentry DSN |

---

## Telegram webhook setup

### 1. Create the Access Bot

1. Talk to [@BotFather](https://t.me/BotFather) → `/newbot`.
2. Copy the token to `ACCESS_BOT_TOKEN`.

### 2. Add the bot to your private channel as Administrator

The bot needs these admin permissions:
- **Invite users via link** — to create invite links
- **Manage chat members** (approve/decline join requests, kick if enabled)

Steps:
1. Open channel settings → Administrators → Add Administrator.
2. Search for your bot by username.
3. Enable the required permissions above.

### 3. Register the webhook

The app calls `setWebhook` automatically at startup using:

```
{APP_PUBLIC_BASE_URL}{ACCESS_BOT_WEBHOOK_PATH}
# e.g. https://app.up.railway.app/tg/access/webhook
```

To verify:
```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

For local development, use [ngrok](https://ngrok.com/):
```bash
ngrok http 8000
# Copy the https URL to APP_PUBLIC_BASE_URL in .env, then restart the app.
```

### 4. Enable join requests on the channel

In channel settings → set **Join via link** to **By Request** (not direct join).
Alternatively, always use the invite links created by `/invites/club` — these already set `creates_join_request=True`.

---

## Lava.top webhook configuration

In your Lava dashboard → Webhooks:

1. Set the webhook URL to: `{APP_PUBLIC_BASE_URL}/lava/webhook`
   (or whatever `LAVA_WEBHOOK_PATH` is set to).

2. Set the signing secret to match `LAVA_SECRET`.

3. **User identification** — the webhook payload must include the buyer's Telegram user ID.
   The app looks for it in (in priority order):
   - `payload.metadata.telegram_user_id`
   - `payload.comment` / `payload.purpose` (first integer token ≥ 5 digits)
   - `payload.custom_fields.telegram_user_id`
   - `payload.buyer.telegram_user_id`

   Configure your Lava product to pass the Telegram user ID via one of these fields.
   If the ID is missing, the event is stored but not processed (logged as `lava_unmatched_user`).

4. **Signature scheme** — currently implemented as HMAC-SHA256 over the raw body,
   checked against the `X-Signature` header. See `app/core/security.py` to adjust
   once Lava's exact scheme is confirmed.

---

## API reference

### `GET /health`

```json
{"status": "ok", "env": "prod"}
```

### `POST /lava/webhook`

Called by Lava on payment events. No auth from your side — Lava sends a signature header.

### `POST /invites/club`

**Headers:** `X-Admin-Token: <ADMIN_TOKEN>`

**Body:**
```json
{"telegram_user_id": 123456789}
```

**Response:**
```json
{
  "invite_link": "https://t.me/+xxxxxxxxxxxx",
  "expires_at": "2024-06-01T12:10:00+00:00"
}
```

Returns 402 if the user has no active subscription.

### `POST /invites/menu`

**Headers:** `X-Admin-Token: <ADMIN_TOKEN>`

**Body:**
```json
{"telegram_user_id": 123456789}
```

Returns a permanent join-request invite for menu channel (`expires_at: null`).

### `POST /payments/create`

**Headers:** `X-Admin-Token: <ADMIN_TOKEN>`

**Body:**
```json
{"telegram_user_id": 123456789, "product": "club", "plan": "3m"}
```

`product`:
- `club` (requires `plan`)
- `menu` (ignores `plan`, creates invoice for menu offer)

For `product=club`, `plan` supports canonical values:
- `1w`
- `1m`
- `3m`
- `6m`
- `12m`

`1w` is a trial plan and can be purchased only once per user.
If trial is already used, endpoint still returns HTTP 200 with:
`ok=false`, `error_code="trial_already_used"`, and `detail`.

Also accepted for BotHelp convenience: `1н`, `1нед`, `1`, `3`, `6`, `12`,
plus Cyrillic variants like `3м` / `6мес`.

### `POST /payments/check`

**Headers:** `X-Admin-Token: <ADMIN_TOKEN>`

**Body:**
```json
{"telegram_user_id": 123456789, "product": "menu"}
```

`product` defaults to `club` for backward compatibility.

If paid, returns `paid="true"` + invite link; otherwise `paid="false"`.
- `club` => time-limited invite (`expires_at` is set)
- `menu` => permanent invite (`expires_at = null`)

### `GET /admin/ping`

**Headers:** `X-Admin-Token: <ADMIN_TOKEN>`

Quick sanity check that admin auth is working.

---

## Extending the system

### Adding a new product (e.g. `recipes_lifetime`)

1. Add a new env var (e.g. `LAVA_PRODUCT_KEY_RECIPES=recipes_lifetime`).
2. In `app/api/routes/lava_webhook.py`, replace the single `product_key` lookup
   with a mapping from Lava's product/offer ID → internal key.
3. Add a new invite endpoint in `app/api/routes/invite.py`.
4. The entitlement logic (`can_approve_join`) and DB schema are already product-agnostic.

### Migrating off BotHelp

When you build a Main Bot:
1. Create a new bot module in `app/bots/main_bot/`.
2. Register its webhook in `app/main.py`.
3. Move the invite-link generation into the Main Bot flow instead of calling
   `POST /invites/club` externally.

---

## Railway deployment

1. Create a Railway project and provision a Postgres database.
2. Set all required environment variables (Railway provides `DATABASE_URL` automatically).
3. Set `APP_PUBLIC_BASE_URL` to your Railway-assigned domain (e.g. `https://easy-steps.up.railway.app`).
4. Deploy — the `Dockerfile` CMD runs `alembic upgrade head` before starting uvicorn.

> **Note:** Railway injects `DATABASE_URL` as `postgres://...`. The app rewrites it
> to `postgresql+asyncpg://...` automatically via the `normalise_db_url` validator.
