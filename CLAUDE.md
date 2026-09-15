# EasySteps: project context for coding agents

This file is the operational and architectural handoff for future coding sessions.
Read it before changing code or production data. Prefer the current code and migrations
over this document if they disagree, then update this document as part of the change.

## Safety rules

- This is a production payment and access-control system. Assume real users and real money.
- Never print, commit, or repeat values from `.env`, service-account JSON files, API keys,
  Telegram bot tokens, webhook credentials, passwords, or `ADMIN_TOKEN`.
- Never commit `.env`, `.claude/settings.local.json`, or Google service-account credentials.
- Before any manual production database mutation, create a timestamped `pg_dump -Fc` in
  `/opt/easy_steps/backups/` and report its full path.
- Perform production data changes in a transaction with `ON_ERROR_STOP=1`, assert the
  expected row count, commit, and then verify through both SQL and the public API.
- Do not remove Docker volumes, recreate the database, run `docker compose down -v`, or
  remove orphan containers without proving they are disposable. An orphan PostgreSQL
  container has existed on the host; do not touch it as part of routine deployment.
- Never rewrite or reset production Git history. Deploy only committed code using a
  fast-forward pull. Migrations must run with `alembic upgrade head`.
- Do not alter unrelated local changes. Stop and ask if unexpected changes appear while
  working.
- Do not treat a successful HTTP response alone as proof of a completed payment or data
  mutation. Verify the persisted state.

## Product summary

EasySteps is a FastAPI backend connecting:

- BotHelp chatbot flows;
- Lava.top one-time payment invoices and payment webhooks;
- private Telegram channels for club workouts and lifetime menu access;
- PostgreSQL entitlement and invoice state;
- Google Sheets payment reporting for a specific referral source.

The backend creates invoices, records successful payments, grants or extends access,
approves Telegram join requests, removes expired club members, sends BotHelp lifecycle
messages, synchronizes subscription state to BotHelp, and cleans active users from mailings.

## Technology and layout

- Python 3.12
- FastAPI and Uvicorn
- SQLAlchemy 2 async with asyncpg
- Alembic migrations
- aiogram 3 Telegram webhook bot
- PostgreSQL 16
- httpx for Lava and BotHelp calls
- gspread for Google Sheets
- Docker Compose and Caddy in production

Important files:

- `app/main.py`: application factory, lifespan, route registration, background loops.
- `app/core/config.py`: all environment-backed settings and offer/notification maps.
- `app/db/models.py`: `User`, `Entitlement`, `PendingInvoice`, and `LavaEvent`.
- `app/db/repo.py`: database query and persistence methods.
- `app/services/entitlements.py`: access decisions and entitlement mutations.
- `app/services/lava_api.py`: Lava v3 invoice creation and payment-provider mapping.
- `app/api/routes/payments.py`: invoice creation and the BotHelp payment check.
- `app/api/routes/lava_webhook.py`: idempotent payment event processing.
- `app/api/routes/subscriptions.py`: BotHelp-friendly status response.
- `app/services/bothelp_status_sync.py`: durable three-state BotHelp synchronization.
- `app/services/bothelp_club_lifecycle.py`: active-user mailing cleanup and
  recurring retention-message delivery.
- `app/services/google_sheets.py`: worksheet format and invoice-ID deduplication.
- `app/services/gsheet_delivery.py`: durable Sheets retry worker.
- `app/bots/access_bot/handlers.py`: Telegram join-request decisions.
- `migrations/versions/`: authoritative database history.
- `tests/`: unit and route-level behavior tests.

## Domain model

### Users

`users.telegram_user_id` is the stable Telegram identity. A user may also have a
`bothelp_subscriber_id`, populated by the BotHelp webhook. BotHelp status-delivery fields
persist the last confirmed synchronization result and retry information.

### Entitlements

An entitlement represents access to one `product_key`:

- `club`: time-limited workout-channel access;
- `menu`: normally lifetime menu-channel access.

Relevant fields:

- `status`: `active`, `inactive`, `past_due`, or `canceled`;
- `active_until`: UTC expiry; `NULL` means lifetime;
- `kicked_at`: actual successful Telegram kick time for the current expired access cycle;
- `retention_message_sent_at`: last confirmed retention-step trigger;
- `retention_offers`: paid retention offers (`0` or `1`);
- `duration_days`: original/most recently applied plan length;
- notification delivery markers;
- review-mailing reconciliation state and retry metadata.

Access is valid only when `status == active` and `active_until` is either `NULL` or in the
future. Do not infer access from payment rows alone.

Club renewals stack using:

```text
max(existing active_until, now) + purchased duration
```

Configured club plans map to 7, 30, 90, 180, and 365 days. Menu purchases normally set
`status=active`, `active_until=NULL`, and `duration_days=NULL`.

### Pending invoices

`pending_invoices` maps a Lava invoice/contract ID to Telegram user, offer, plan, payment
URL, BotHelp metadata (`cuid`, name, referral), amount, payment time, and Google Sheets
delivery state. A paid row is payment history; current channel access still comes from
`entitlements`.

The one-week plan is allowed only once. The check is based on an existing paid `1w`
pending invoice for that Telegram user.

### Lava events

`lava_events` stores webhook payloads under a stable unique event ID. This is the webhook
idempotency boundary and prevents the same event from extending access twice.

## Main flows

### Invoice creation

BotHelp calls `POST /payments/create` with `X-Admin-Token` and JSON containing the Telegram
ID, product/plan, currency/payment method, and optional BotHelp/referral data.

1. Normalize product and plan.
2. Resolve the configured Lava offer ID.
3. Reject a repeated paid one-week trial.
4. Create a Lava v3 invoice.
5. Persist the invoice-to-user mapping.
6. Return payment URL, invoice ID, and display amount.

Lava itself uses `X-Api-Key`; `X-Admin-Token` protects this application's internal
BotHelp-facing endpoints. They are different credentials with different purposes.

Current provider mapping in `lava_api.py`:

- RUB + CARD: `SMART_GLOCAL`;
- USD/EUR + CARD: `UNLIMIT`;
- SBP: `PAY2ME` and only valid with RUB;
- PayPal: `PAYPAL`;
- Apple Pay: `UNLIMIT`;
- USD/EUR without an explicit method: `UNLIMIT`.

### Payment webhook

Lava calls the configured webhook using Basic Auth.

1. Verify webhook Basic Auth.
2. Parse and persist the event for idempotency.
3. Resolve the user primarily from `pending_invoices` by contract ID.
4. Resolve the offer to `club + days` or lifetime `menu`.
5. On success, mark the invoice paid and activate/extend the entitlement.
6. Commit club changes before triggering BotHelp status synchronization.
7. Stop an active user from inactive-user mailings after a successful club purchase.
8. If `pending.ref == "tanya"`, attempt Google Sheets delivery immediately; failures are
   persisted for retry.

The webhook intentionally returns HTTP 200-style JSON outcomes for many unmatched or
unhandled cases to avoid uncontrolled Lava retries. Therefore logs and database state are
required when investigating an incident.

### Payment check button

BotHelp calls `POST /payments/check`. The response is:

```json
{"paid": "true"}
```

or `"false"` as a string. Important: the current implementation checks whether the user
has any active entitlement for the requested product. Although `invoice_id` exists in the
request model, it is not currently used to verify that specific invoice. Consequently, a
previously active subscriber may receive `paid=true` after creating but not paying a new
invoice. Treat this as known behavior/technical debt, not strict invoice verification.

### Telegram access

The access bot receives only `chat_join_request` updates. It maps the configured club and
menu channel IDs to product keys, reads the corresponding entitlement, and approves only
valid active access. Unknown channels are ignored; invalid/expired access is declined.

The expiry worker applies only to the club channel. It finds active club entitlements past
the grace cutoff, performs Telegram ban + unban (kick while allowing future rejoin), and
marks the entitlement inactive. After Telegram confirms the operation, it stores the exact
UTC time in `kicked_at`. Activating access again resets `kicked_at` to `NULL`. Historical
rows created before migration `0017` remain `NULL`; no kick time is inferred. Menu is not
expired/kicked by this loop.

### Subscription status endpoint

`POST /subscriptions/status` requires `X-Admin-Token` and body:

```json
{"telegram_user_id": 123456789}
```

Response values are deliberately strings for BotHelp compatibility:

```json
{
  "club": "15",
  "menu": "True",
  "subscription_status": "active"
}
```

- `club`: remaining calendar-sized 24-hour periods rounded up, `"0"` if inactive, or
  `"Бессрочно"` for lifetime club access;
- `menu`: `"True"` or `"False"`;
- `subscription_status`: `never_paid`, `active`, or `expired`.

`expired` means a club entitlement row exists but is not currently valid. It does not by
itself prove a paid invoice exists; manual or historical access may also create the row.

### BotHelp subscriber mapping and status sync

The BotHelp webhook stores the Telegram ID to BotHelp subscriber-ID mapping. A changed
mapping invalidates the previously synced status and triggers an immediate refresh.

The technical BotHelp synchronization step calls `/subscriptions/status` and maps
`subscription_status` into the BotHelp subscriber field `club_subscription_status`.

Status reconciliation:

- immediately after successful/canceled club events and BotHelp ID mapping;
- periodically in batches (production defaults are represented in `.env.example`);
- shorter polling interval while a full batch indicates backlog;
- only users whose desired status differs from the last confirmed status cause external
  BotHelp requests.

### Expiry notifications

The current configured policy supports only four BotHelp message steps:

- 3 days before club expiry;
- 2 days before club expiry;
- 10 hours after the actual successful Telegram kick;
- 3 days (72 hours) after the actual successful Telegram kick.

Delivery markers in the entitlement prevent repeated threshold delivery. Successful
renewal/upsert resets the relevant markers. Notifications require a stored BotHelp
subscriber ID.

### Club lifecycle and retention

The club-lifecycle worker gives active-user mailing cleanup priority. Seven days (168
hours) after an actual successful Telegram kick, it starts retention messages while club
access remains inactive. Messages repeat every 72 hours. Users with `retention_offers=0`
receive `BOTHELP_STEP_RETENTION_OFFER`; users with `retention_offers=1` receive
`BOTHELP_STEP_RETENTION_USED`. `retention_message_sent_at` controls the repeat interval,
and an older timestamp does not suppress the first message after a later kick.

The BotHelp payment branch passes `plan=retention_1m` through the normal country,
currency, payment-method, and `/payments/create` flow. The backend verifies eligibility
and applies `LAVA_RETENTION_PROMO_CODE` to the normal one-month offer. A successful Lava
webhook grants 30 days and changes `retention_offers` from `0` to `1`; later discounted
invoice creation is rejected. Active users are removed from inactive-user mailings using
`BOTHELP_STEP_REVIEW_MAILING_STOP` after activation and by periodic reconciliation.
BotHelp can call `POST /subscriptions/retention-offer` before entering the shared payment
flow; it returns whether the lifetime `retention_offers` flag is still `0` as the string
`True` or `False`. Discounted invoice creation uses the same single check, regardless of
current club status, expiry, kick time, or retention-message history.

### Google Sheets

Only successfully paid pending invoices whose exact referral is `tanya` are eligible.
Rows are appended; deleted rows are not recreated after `gsheet_recorded_at` has been set.
The hidden column H contains `Invoice ID` and is used for deduplication. Payment date/time
uses Lava's payment timestamp when available and is formatted in UTC+3.

Delivery is attempted immediately on the webhook and retried durably by a periodic worker.
The worker processes at most the configured batch size and stops the current cycle after a
failure to avoid hammering Google during a shared outage.

## API surface

- `GET /health`: public application health and environment label.
- `GET /admin/ping`: validates `X-Admin-Token`; other admin endpoints are only TODOs.
- `POST /payments/create`: protected invoice creation.
- `POST /payments/check`: protected active-entitlement check.
- `GET /pay/{invoice_id}`: redirect to the stored Lava payment URL.
- `POST /subscriptions/status`: protected BotHelp-compatible access status.
- configured Lava webhook path: Basic Auth protected.
- configured Telegram webhook path: Telegram secret-token protected.
- configured BotHelp webhook path: stores BotHelp subscriber mapping.

Do not assume endpoints mentioned by old README sections exist. Confirm route registration
in `app/main.py`.

## Background loops

Up to four loops start in the single app process when configured:

1. Expiry notifications and club kicks (`KICK_ON_EXPIRE`).
2. Durable Google Sheets delivery.
3. BotHelp subscription-status reconciliation.
4. BotHelp review-mailing reconciliation.

They are asynchronous application tasks, not separate worker containers. Avoid running
multiple app replicas unless the jobs are first protected with database locking or moved to
a dedicated worker; otherwise duplicate concurrent processing is possible.

## Local workflow

```bash
cp .env.example .env
docker compose up --build
```

Run tests before committing:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

Useful read-only checks:

```bash
git status --short
git log --oneline -10
curl -sS https://esyaaaaa.online/health
```

To test a protected status call without placing the token directly in shell history:

```bash
read -s ADMIN_TOKEN && echo
curl -sS -X POST 'https://esyaaaaa.online/subscriptions/status' \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"telegram_user_id": 123456789}'
unset ADMIN_TOKEN
```

## Production

- Public URL: `https://esyaaaaa.online`
- VPS SSH target: `root@82.25.61.129`
- Authentication: SSH key, not password.
- Project directory: `/opt/easy_steps`
- Compose file: `/opt/easy_steps/docker-compose.prod.yml`
- Application container is normally `easy_steps-app-1`.
- Main database container is normally `easy_steps-db-1`.
- Database/user name: `easysteps`.
- Backups: `/opt/easy_steps/backups/`.

Names and deployment state can change. Before acting, verify with read-only commands:

```bash
ssh -o BatchMode=yes root@82.25.61.129 \
  'cd /opt/easy_steps && git status --short && git rev-parse HEAD && docker compose -f docker-compose.prod.yml ps'
```

Normal code deployment sequence after local tests, review, commit, and push:

```bash
ssh -o BatchMode=yes root@82.25.61.129 \
  'cd /opt/easy_steps && git pull --ff-only && docker compose -f docker-compose.prod.yml up -d --build app && docker compose -f docker-compose.prod.yml exec -T app alembic upgrade head && docker compose -f docker-compose.prod.yml ps'
```

After deployment, verify `/health`, container logs, migration head, and any changed business
flow. A deploy is not complete merely because the container is running.

## Manual production access changes

Prefer implementing and using an audited admin service/endpoint. The current admin router
does not provide entitlement mutation endpoints, so historical operations have sometimes
used SQL. If SQL is unavoidable:

1. Inspect the user, all entitlements, paid invoices, BotHelp mapping, and current Telegram
   membership.
2. Create a `pg_dump -Fc` backup.
3. Use a transaction and assert exactly the expected records.
4. For gifted club time, stack from `GREATEST(active_until, now())`; do not replace valid
   remaining time.
5. Reset expiry notification markers when extending access.
6. For lifetime menu, use active status with `active_until=NULL` and `duration_days=NULL`.
7. Reconcile BotHelp subscription status and stop inactive-user mailings for newly active club
   users.
8. Verify SQL state, `/subscriptions/status`, BotHelp delivery state, and Telegram behavior.

Do not fabricate payment rows for gifted/manual access unless the business explicitly wants
the grant represented as a payment. Entitlements and payment history are separate concepts.

## Known risks and technical debt

- `/payments/check` ignores its optional `invoice_id` and checks any active entitlement.
- Background jobs run inside the web process and are unsafe with uncoordinated replicas.
- The BotHelp webhook route does not currently show independent authentication in its
  handler; evaluate this before exposing additional mutation behavior through it.
- Payment request bodies are logged (truncated) by middleware. Avoid adding secrets or
  sensitive personal data to these bodies, and consider structured redaction.
- CORS currently allows all origins.
- The admin API is mostly a stub, which encourages risky direct SQL for support operations.
- Trust `Settings.notify_steps_map`, `Settings.notify_post_kick_hours_map`, and tests for
  the current notification policy.
- There is no explicit uniqueness constraint visible in the current ORM model for
  `(user_id, product_key)`; preserve the one-row-per-product invariant and check migrations
  before changing upsert behavior.

## Expected agent behavior

- Begin investigations with repository state, relevant tests, and current implementation.
- For incidents, correlate `pending_invoices`, `lava_events`, `entitlements`, app logs,
  BotHelp mapping, and Telegram membership rather than guessing from one table.
- Explain the intended mutation before touching production.
- Back up first, change the smallest possible scope, and verify end to end.
- Add or update tests for behavior changes.
- Keep configuration in `Settings`/`.env.example`; do not hardcode BotHelp refs, offer IDs,
  channel IDs, URLs, or credentials.
- Update this file whenever architecture, production topology, key workflows, or operational
  rules change.
