# Knowledge-Points Mini Program

A working prototype of a **health-education loyalty mini program**: users watch knowledge videos, earn points for verified watch time, and redeem points for gifts that can only be picked up in person at a physical store. An operations console handles content, gifts, store inventory, pickup requests, members and audit logs.

The prototype was built from a reference screen recording to settle the pages, copy and business rules *before* a real mini program account was available. The browser build hosts the same flows so they can be clicked through and tested quickly; `miniprogram/` contains the native WeChat project (WXML/WXSS/JavaScript) that the browser build mirrors.

This is a portfolio-grade engineering sample, not a hosted product: no brand assets, no production credentials and no live endpoints are included.

## What is implemented

**User side**

- Home with knowledge banners and a two-column gift grid.
- Video categories, list, detail and playback.
- Verified watch-time accounting: a heartbeat every 5 seconds, a server-side session, and a unique slot per (user, video, 5-second position) so the same second can never be paid twice.
- Points wallet with balance, transactions and per-video progress.
- Gift catalogue, gift detail, store selection, pickup request and pickup voucher.
- Pickup records with status, invitation flow and invite records.
- Profile, activity rules and customer-service entry.

**Operations console** (`/admin`)

- Staff login with two fixed roles: administrator and store verifier.
- Video categories and full video lifecycle (create, edit, publish, unpublish, delete), cover handling and duration-derived point caps.
- Gifts, per-store inventory and redemption limits.
- Pickup requests, store leads, voucher verification and status transitions.
- Member records, watch/points reporting, invitation relationships, manual point adjustments with a reason.
- Admin operation log, CSV export and system configuration.

## Business rules that the code enforces

| Rule | Behaviour |
|---|---|
| Watch points | 1 point per full minute of verified watch time. The cap is `round(duration_seconds / 60)`, adjustable by an administrator but never above the duration-derived value. |
| Valid watch time | Paused playback, seeking, backgrounded tabs and abnormal playback rates do not count. Only server-validated heartbeats advance progress. |
| Idempotency | Every point movement carries a unique business key. Replayed requests and network retries reuse the first result instead of paying twice. |
| Pickup, not shipping | Requests are store-only. There is no address, courier, tracking number or warehouse state in the model, by design. |
| Atomic redemption | Point deduction, inventory locking and request creation happen in one database transaction; cancellation writes a reversal transaction and restores the locked quantity. Original rows are never deleted. |
| Invitations | A new user can bind exactly one inviter, self-invitation is rejected, and a reward is issued once per qualified invitee through unique business keys. |
| Auditability | Point changes, pickup transitions, manual adjustments and console writes all leave a reason, an actor and a timestamp. |

Status machines are explicit: pickup requests move `WAIT_CONTACT → WAIT_ARRIVAL → PICKED_UP` or terminate as `CANCELLED / REJECTED / EXPIRED`; store leads move `NEW → CONTACTED → ARRIVED → CLOSED`; invitations move `BOUND → QUALIFIED → REWARDED` (optionally `INVALID`, never deleted).

## Architecture

```
app/server.py        single-file HTTP service: routing, auth, points, pickup, invites,
                     admin API, CSV export, static hosting, OSS signing
app/schema.sqlite.sql   development schema
app/backup.py        SQLite online-backup helper
database/schema.mysql.sql  MySQL 8 schema for the production target
web/                 browser build of the user flows + admin console
miniprogram/         native WeChat project (pages, custom tab bar, assets)
tests/               business-flow, page, cache, backup and load tests
scripts/             start/stop, demo media generation, acceptance helpers
```

The service is deliberately dependency-light: Python's standard `ThreadingHTTPServer`, `sqlite3` in WAL mode, and `qrcode` for voucher QR codes. `oss2` is optional and only needed when object-storage uploading is enabled. All business rules live on the server; the client cannot grant itself points or change a pickup state.

## Getting started

Requirements: Python 3.11+, PowerShell (Windows).

```powershell
python -m pip install -r app/requirements.txt

# generates a demo video, then serves the app on 127.0.0.1:8765
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

- User build: <http://127.0.0.1:8765>
- Operations console: <http://127.0.0.1:8765/admin>

The development database starts empty and is created from `app/schema.sqlite.sql`. `scripts/start.ps1` sets `ENABLE_DEV_ENDPOINTS=1` so demo helpers work locally; keep that switch off anywhere else.

### Configuration

Copy `.env.example` to `.env` and fill in your own values. Never commit the result.

| Variable | Purpose |
|---|---|
| `ENVIRONMENT` | `production` disables development-only endpoints. |
| `ENABLE_DEV_ENDPOINTS` | Enables demo/reset helpers. Leave `0` outside local work. |
| `LOCAL_ADMIN_PASSWORD` | Password for the administrator account. There is no usable default: set a long random value. |
| `LOCAL_VERIFIER_PASSWORD` | Password for the store-verifier account. |
| `WX_APPID`, `WX_APPSECRET` | WeChat mini program credentials, server-side only. |
| `PUBLIC_API_BASE_URL` | Public base URL used when the mini program calls the API. |
| `OSS_*` | Optional object storage for video and image uploads (presigned upload, private bucket reads). |
| `DATABASE_URL` | MySQL connection string for the production target. |

## Tests

```powershell
python -m pip install -r requirements-dev.txt   # pyyaml, needed by validate_design.py
powershell -ExecutionPolicy Bypass -File scripts\run-tests.ps1
```

The suite runs 19 Python tests and 3 mini program JavaScript checks:

- video upload, publish, edit, unpublish, delete;
- point accrual per verified minute, capped by the duration-derived total;
- duplicate heartbeats and replayed requests not paying twice;
- gift inventory, pickup creation, cancellation refund and store verification;
- invitation binding, self-invitation rejection and reward transactions;
- role isolation between administrator and verifier;
- user endpoints not leaking console data;
- two concurrent identical point adjustments taking effect once;
- operation logging and CSV export.

Two further tools are included:

```powershell
# online backup, restore into a copy, compare row counts, PRAGMA integrity_check
python tests\database_backup_restore_test.py

# read-only concurrency test with explicit error-rate and P95 thresholds
python tests\runtime_load_test.py --base-url http://127.0.0.1:8765 --concurrency 5,20,50 --requests-per-stage 200
```

## Design notes and trade-offs

- **Why relational transactions:** deduction, inventory locking and request creation must succeed or fail together; a relational database with row-level constraints is the simplest correct tool.
- **Why slots rather than a running timer:** storing one row per five-second position makes double payment physically impossible and keeps an auditable trail, at the cost of more rows.
- **Why browser first:** the flows could be reviewed and tested before WeChat account, domain registration and ICP filing were ready. The native project mirrors the same pages.
- **Known limits at prototype scale:** a single-process threaded server, SQLite as the development database, and no CDN. The MySQL 8 schema and an object-storage path are provided for the production direction; a real deployment would also need a production WSGI/ASGI process, monitoring and restore drills.

## Privacy and data handling

Contact name and phone number are stored as the snapshot submitted at request time, so a later profile edit cannot invalidate a store verification. Only administrators can read across stores; a verifier sees their own store. Account deletion anonymises identity instead of deleting point and pickup evidence. The development database and uploaded media are excluded from version control.

## License

MIT. See `LICENSE`.
