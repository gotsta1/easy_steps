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
  remove orphan containers without proving they are disposable. The retired shared
  PostgreSQL volume must not be mounted into another running server.
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
- `retention_next_message_index`: zero-based position of the next message in the
  current three-message retention cycle;
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
8. If `pending.ref == settings.GSHEET_REF`, attempt Google Sheets delivery immediately; failures are
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

The BotHelp webhook verifies the subscriber against the official export API before
storing a mapping: both the Telegram ID and `BOTHELP_CHANNEL_ID` must match. Names,
email addresses and nearby numeric IDs are never used as identity evidence. A changed
mapping invalidates cached status and active-user mailing cleanup. Migration `0022`
enforces unique non-null subscriber IDs; clean historical duplicate mappings before
running that migration. Concurrent Telegram user creation uses INSERT ON CONFLICT.

Status sync writes `BOTHELP_SUBSCRIPTION_STATUS_FIELD` (default `club_subscription_status`)
through PATCH `/v1/subscribers/{id}/customFields`. It requires a positive `success=true`
response, not merely HTTP 200 or acceptance of a flow launch. It does not launch the
old technical synchronization step or interrupt the subscriber's current bot flow.
The public API does not expose a field read-back method: distinguish a confirmed write
response from an independently reread value.

Status reconciliation:

- immediately after successful/canceled club events and BotHelp ID mapping;
- periodically in batches (production defaults are represented in `.env.example`);
- shorter polling interval while a full batch indicates backlog;
- changed states are prioritized, with active club users first;
- club users also receive a direct field refresh every 24 hours to repair external drift;
- identity directory reconciliation runs in the existing status loop every six hours,
  using a complete, channel-scoped export. Empty, failed, ambiguous, non-advancing or
  suspiciously truncated snapshots must not overwrite working mappings;
- missing/recreated profiles are rebound only after exact identity verification;
- `bothelp_mapping_verified_at` stores identity validation time and
  `bothelp_status_next_retry_at` persists failure backoff; 404s retry no earlier than
  one hour. Confirmed field writes clear consecutive failure counts;
- a shared OAuth/HTTP client rate-limits requests and retries only idempotent GET/PATCH;
  message-triggering POSTs are never automatically repeated after an uncertain failure.

Production BotHelp channel identity is `8373474846`; it is not the private Telegram
workout channel ID. Account `1590698404` had active access but no matching BotHelp profile
in the 2026-11-08 directory; never fabricate a subscriber mapping for it.

### Expiry notifications

The current configured policy supports five BotHelp notification steps:

- 3 days before club expiry;
- 2 days before club expiry;
- 1 day before club expiry (`BOTHELP_STEP_NOTIFY_1D`);
- 10 hours after the actual successful Telegram kick;
- 3 days (72 hours) after the actual successful Telegram kick.

Delivery markers in the entitlement prevent repeated threshold delivery. Successful
renewal/upsert resets the relevant markers. Notifications require a stored BotHelp
subscriber ID.

Pre-expiry reminders use non-overlapping remaining-time windows: (48h, 72h],
(24h, 48h], and (0h, 24h]. They are anchored to `active_until`, not the later
Telegram kick or its grace period. Missed older reminders are not sent together
with the current threshold. Delivery follows the expiry worker polling interval.

### Club lifecycle and retention

The club-lifecycle worker gives active-user mailing cleanup priority. Twelve days (288
hours) after an actual successful Telegram kick, it starts retention messages while club
access remains inactive. Repeats are persisted at a random time 3–4 calendar days later,
between 06:00 and 24:00 Moscow time. Users with `retention_offers=0`
receive the three `BOTHELP_STEP_RETENTION_OFFER` steps in order; users with
`retention_offers=1` receive the three `BOTHELP_STEP_RETENTION_USED` steps in order.
After the third message the sequence starts again. `retention_next_message_at` stores
the randomized delivery time; `retention_next_message_index` advances only after a
successful BotHelp step trigger. Existing inactive users who already received a
retention message start at the second message after migration `0020`. A new activation
and kick resets the index so the next cycle starts at the first message of the current
offer branch. An older `retention_message_sent_at` does not suppress the first message
after a later kick. Configure all six BotHelp refs before enabling the new retention
sequence on production.

When processing a due repeat saved under the old two-day policy, the worker postpones
it if the new minimum Moscow calendar date/time has not arrived. This preserves the
message index and last-send marker and does not trigger an immediate resend. The
12-day first-send cutoff also applies to existing users; no new DB migration is needed.

The BotHelp payment branch passes `plan=retention_1m` through the normal country,
currency, payment-method, and `/payments/create` flow. The backend verifies eligibility
and uses the hidden `LAVA_OFFER_CLUB_RETENTION_1M` product. A successful Lava webhook
grants 30 days and changes `retention_offers` from `0` to `1`; later discounted
invoice creation is rejected. Active users are removed from inactive-user mailings using
`BOTHELP_STEP_REVIEW_MAILING_STOP` after activation and by periodic reconciliation.
BotHelp can call `POST /subscriptions/retention-offer` before entering the shared payment
flow; it returns whether the lifetime `retention_offers` flag is still `0` as the string
`True` or `False`. Discounted invoice creation uses the same single check, regardless of
current club status, expiry, kick time, or retention-message history.

### Google Sheets

Only successfully paid pending invoices whose exact referral matches `GSHEET_REF` are
eligible, both in the immediate webhook delivery and the retry worker. The setting defaults
to `ref_ref`, matching the production target. Migration `0021` replaces the old
source-specific index with a partial index on `(ref, gsheet_attempts, id)` for all paid,
undelivered invoices. It supports the configured filter and retry ordering without
embedding a referral in the index predicate. Historical migration `0013` stays unchanged.
BotHelp still sends `"ref": "{%reff%}"` to `/payments/create`: `reff` is only the BotHelp
subscriber field, not a new backend request field.
The production spreadsheet is `17VZh_StM_cCUesx-oEX6nVYd_xkbtIX7etkzh03aFCc`, worksheet
`Сумма выплат` (gid `0`). The service account has editor access only to this restricted
spreadsheet; do not make it public. Switching the target does not clear old delivery
markers or automatically backfill already exported payments.
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
- From this Mac, use `ssh -i ~/.ssh/id_ed25519 -o UseKeychain=yes -o BatchMode=yes
  root@82.25.61.129`. The key is passphrase-protected and macOS Keychain supplies
  the unlock credential. An empty SSH agent does not mean the server rejects the
  key; authentication was reverified with this command on 2026-11-07. Never print
  the private key or its passphrase.
- Project directory: `/opt/easy_steps`
- Compose file: `/opt/easy_steps/docker-compose.prod.yml`
- Application container is normally `easy_steps-app-1`.
- Main database container is normally `easy_steps-db-1`.
- Database/user name: `easysteps`.
- Backups: `/opt/easy_steps/backups/`.

### Diagnostic log retention

Production Compose uses the `json-file` driver with compressed rotation: app keeps
eight files of up to 25 MiB each (about 200 MiB total), Caddy and PostgreSQL each
keep five files of up to 10 MiB each (about 50 MiB total). Both stdout and stderr remain available
through `docker compose -f docker-compose.prod.yml logs --since 24h app`.
Retention is size-based, so a burst of logs can shorten the available history.
Changing Docker logging options requires recreating the affected container.

Before recreating a container, archive its existing logs with timestamps into
`/opt/easy_steps/log-archives/` (root-only, gzip). Files there expire after 30 days
through systemd-tmpfiles; they can be searched with `zgrep` during investigations.
Do not print complete archives because they can contain personal data.

Install host policy from the tracked files:

```bash
install -D -m 0644 ops/journald-easysteps.conf /etc/systemd/journald.conf.d/60-easysteps.conf
install -D -m 0644 ops/tmpfiles-easysteps-logs.conf /etc/tmpfiles.d/easysteps-logs.conf
systemd-tmpfiles --create /etc/tmpfiles.d/easysteps-logs.conf
systemctl restart systemd-journald
```

Journald retains up to 30 days within a 256 MiB persistent budget (64 MiB in
runtime storage), reserves 1 GiB free space, and keeps the existing log levels.
Capacity can shorten that history. Archive recent system logs before first
applying this lower limit.

### PostgreSQL Storage Isolation

The production cutover completed on 2026-11-07. The application was unavailable
from 11:39:02 to 11:40:23 UTC (about 81 seconds). A fresh final dump, not the old
staging snapshot, was restored in one transaction. All public table contents and
sequence values matched the source before the application was restarted; an
offline physical checksum check reported zero bad checksums. Query statistics
were rebuilt with `ANALYZE`; public indexes were valid.

Production now runs one PostgreSQL container, `easy_steps-db-1`, using the external
volume `easy_steps_pgdata_prod`. The former development PostgreSQL and temporary
staging containers were stopped and removed without deleting either named volume.
No PostgreSQL port is published on the host.

Recovery and audit materials are root-only:

- Final source dump: `/opt/easy_steps/backups/pre_postgres_cutover_final_20261107T113341Z.dump`.
- Verified new-server dump: `/opt/easy_steps/backups/post_postgres_cutover_20261107T113341Z.dump`.
- Cutover state, scripts, row fingerprints, and saved container metadata:
  `/opt/easy_steps/backups/postgres_cutover_resume_20261107T113341Z/`.
- Historical preparation state: `/opt/easy_steps/backups/postgres_cutover_20261103T121950Z/state.json`.

Saved container metadata and role dumps contain credentials; never print or commit
their contents. The nightly Telegram backup still targets `easy_steps-db-1` and
therefore uses the new server. On a new host, provision the external volume before
starting Compose:

```bash
docker volume create easy_steps_pgdata_prod
```

It is external so Compose does not own its deletion lifecycle. The production
database has no published host port. Local development uses a different volume,
`pgdata_dev`, and publishes PostgreSQL only on `127.0.0.1`. New production clusters
are initialized with data checksums enabled.

During the 2026-11-03 investigation, production `db` and a leftover development
`postgres` server both wrote to `easy_steps_pgdata`. The second server exposed
port 5432 publicly and returned a system-catalog TOAST read error. That old volume
is retained for recovery/forensics only. Do not start additional servers on it or
silently point production back at it after migration.
Recovery must restore a validated logical dump into a fresh, isolated volume. Do
not infer data consistency from container health.

### Host disk alerts

`ops/disk_monitor.py` runs on the host using the standard library, independently
of Docker. `easysteps-disk-monitor.timer` checks `/` every five minutes. The
confirmed policy is occupied disk space >= 75%, not free space < 75% and not RAM.
The percentage follows `df`, including filesystem-reserved blocks.

Telegram alerts use `@am_moshkin_admin_bot` and go to Telegram ID `905113282`.
Credentials are in root-only `/etc/easysteps-monitor.env`; the tracked template
is `ops/disk-monitor.env.example`. Never print that environment file. This bot's
token also exists in the pre-existing `/opt/backups/tg_backup.sh`.

Send immediately on crossing the threshold, repeat at most once per 24 hours
while usage remains high, and send recovery once usage falls to <= 70%.
Confirmed delivery state is stored atomically in
`/var/lib/easysteps-monitor/disk.json`. Failed sends leave state unchanged and
are retried on the next timer run. A `--test` invocation sends current readings
without changing real alert state. Exception logs must never include token URLs.

Install the two tracked systemd units into `/etc/systemd/system/`, create the
root-only environment file, run `systemctl daemon-reload`, and enable with
`systemctl enable --now easysteps-disk-monitor.timer`. Check via:

```bash
systemctl list-timers easysteps-disk-monitor.timer
systemctl show easysteps-disk-monitor.service -p Result -p ExecMainStatus
journalctl -u easysteps-disk-monitor.service --since 24h
```

This is a separate host timer and does not require restarting the bot or DB.

Names and deployment state can change. Before acting, verify with read-only commands:

`.dockerignore` excludes runtime credentials, `.env`, backups, and diagnostic archives
from the image build context. Keep secrets mounted or supplied at runtime.

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
- The BotHelp webhook independently verifies identity through the official API but does
  not have a separate shared-secret authentication layer. Do not expose additional
  entitlement/payment mutation behavior through it without authentication.
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
