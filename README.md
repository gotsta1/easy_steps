# EasySteps — Telegram Subscription Access Bot

Production-ready backend automating paid access to private Telegram channels.
Handles the full lifecycle: payment processing → subscription management → channel access → expiry enforcement.

**Live at:** [esyaaaaa.online](https://esyaaaaa.online)

---

## What it does

1. User clicks "Subscribe" in a BotHelp chatbot flow
2. Backend creates a Lava.top invoice and returns a payment URL
3. User pays → Lava sends a webhook → backend activates subscription in Postgres
4. Backend generates a one-time Telegram invite link and returns it to BotHelp
5. User clicks the invite link and joins the private channel instantly
6. When subscription expires → bot automatically kicks the user from the channel
7. Post-expiry notifications are sent via BotHelp at configurable intervals

---

## Architecture

```
BotHelp chatbot
    │
    ├─ POST /payments/create ──► Lava.top API (create invoice)
    │                                  │
    │                          payment.success webhook
    │                                  │
    ├─ POST /lava/webhook  ────────────►──► EntitlementService ──► Postgres
    │                                                                  │
    └─ POST /payments/check ──────────────────────────────────────────┘
         └─► TelegramAccessService (create one-time invite link)

Telegram
    └─ POST /tg/access/webhook ──► aiogram ──► approve_join_request()

Background jobs (every 15 min)
    ├─ Kick expired members (ban + unban)
    └─ Send post-expiry notifications via BotHelp API
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Web framework | FastAPI + uvicorn |
| Telegram bot | aiogram v3 (webhook mode) |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| Migrations | Alembic |
| Database | PostgreSQL 16 |
| Payments | Lava.top API |
| Chatbot | BotHelp |
| Analytics | Google Sheets API |
| Reverse proxy | Caddy (auto HTTPS) |
| Deploy | Docker Compose on VPS (Netherlands) |

---

## Key features

- **Idempotent webhook processing** — every Lava event stored by stable `event_id`, safe to retry
- **Subscription stacking** — renewals extend from the current `active_until`, not from today
- **One-time invite links** — Telegram `member_limit=1` + 2h TTL, spam-protected via in-memory cache
- **Automatic kick on expiry** — ban + instant unban so users can rejoin after renewal
- **9-threshold post-expiry notifications** — 10h, 3d, 1w, 10d, 15d, 20d, 25d, 30d, 35d after expiry
- **Google Sheets analytics** — successful payments written to spreadsheet with Moscow timezone
- **Daily database backups** — pg_dump sent to Telegram every night at 3:00 UTC
- **Configurable via env** — zero hardcoded business logic, everything in `.env`

---

## Project structure

```
app/
  main.py                      # FastAPI app factory + lifespan + background jobs
  core/
    config.py                  # Pydantic Settings (fail-fast, lru_cache)
    logging.py                 # structlog JSON setup
    security.py                # Lava Basic Auth verification
    time.py                    # utcnow(), utcnow_plus(), ensure_tz()
  db/
    models.py                  # User, Entitlement, LavaEvent, PendingInvoice
    repo.py                    # UserRepo, EntitlementRepo, LavaEventRepo, PendingInvoiceRepo
    session.py                 # AsyncEngine + get_db() dependency
  services/
    entitlements.py            # can_approve_join() (pure) + EntitlementService
    lava.py                    # classify_event, extract_* (pure parsing)
    lava_api.py                # Lava API client: create_invoice()
    telegram_access.py         # TelegramAccessService (invite, approve, kick)
    google_sheets.py           # Append payment rows to Google Sheets
    bothelp_api.py             # BotHelp OAuth2 client for sending notifications
  api/routes/
    health.py                  # GET /health
    payments.py                # POST /payments/create + POST /payments/check
    lava_webhook.py            # POST /lava/webhook
    invite.py                  # POST /invites/club
    pay_redirect.py            # GET /pay/{invoice_id} → redirect to Lava
    admin.py                   # Admin endpoints
  bots/access_bot/
    bot.py                     # Bot + Dispatcher singletons
    handlers.py                # chat_join_request handler
    webhook.py                 # Webhook registration
migrations/versions/           # 11 Alembic migrations
tests/
  test_entitlements.py         # Pure unit tests for can_approve_join()
```

---

## Local development

```bash
git clone https://github.com/gotsta1/easy_steps.git
cd easy_steps
cp .env.example .env
# Fill in ACCESS_BOT_TOKEN, TG_CHANNEL_ID, LAVA_* and other required vars

docker compose up --build
# App: http://localhost:8000
# Postgres: localhost:5432 (easysteps/easysteps/easysteps)
```

Run tests:
```bash
pip install -e ".[dev]"
pytest tests/ -v
```

---

## Production deployment

```bash
# On VPS (Ubuntu 22.04)
git clone https://github.com/gotsta1/easy_steps.git /opt/easy_steps
cd /opt/easy_steps
cp .env.example .env  # fill in all values
docker compose -f docker-compose.prod.yml up -d --build
```

Caddy handles SSL certificates automatically via Let's Encrypt.

---

## Environment variables

See [.env.example](.env.example) for the full list with descriptions.

Required:
- `ACCESS_BOT_TOKEN` — Telegram bot token from BotFather
- `TG_CHANNEL_ID` — numeric ID of the private channel
- `LAVA_API_KEY` — Lava.top API key
- `LAVA_WEBHOOK_LOGIN` / `LAVA_WEBHOOK_PASSWORD` — Basic Auth for Lava webhooks
- `LAVA_OFFER_CLUB_*` — offer IDs for each subscription plan
- `DATABASE_URL` — PostgreSQL connection string
- `APP_PUBLIC_BASE_URL` — public HTTPS URL for webhook registration
- `BOTHELP_STEP_SUBSCRIPTION_SYNC` — technical BotHelp step that refreshes
  the `club_subscription_status` subscriber field
- `BOTHELP_STEP_REVIEW_MAILING` — technical BotHelp step that enrolls club
  users into the review mailing 120 hours after their access expires

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/payments/create` | Create Lava invoice, return payment URL |
| POST | `/payments/check` | Check payment status, return invite link |
| GET | `/pay/{invoice_id}` | Redirect to Lava payment page |
| POST | `/lava/webhook` | Receive Lava payment events |
| POST | `/tg/access/webhook` | Receive Telegram bot updates |
| POST | `/bothelp/webhook` | Receive BotHelp subscriber events |
| POST | `/subscriptions/status` | Return club/menu and three-state subscription status |
| POST | `/invites/club` | Generate club invite link (admin) |

---

## License

MIT
